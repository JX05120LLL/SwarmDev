"""Tests for the parallel task scheduler."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from swarmdev.core.types import (
    AgentAdapter,
    AgentInfo,
    DecompositionResult,
    ProgressUpdate,
    SubTask,
    Task,
    TaskResult,
    TaskStatus,
)
from swarmdev.orchestrator.parallel_scheduler import ParallelScheduler


# ============================================================
# Helpers
# ============================================================

ExecuteFn = Callable[[Task, str], Awaitable[TaskResult]]


def _make_agent(
    name: str = "test-agent",
    execute_fn: ExecuteFn | None = None,
    healthy: bool = True,
) -> AgentAdapter:
    """Create a mock AgentAdapter."""
    agent = MagicMock(spec=AgentAdapter)
    agent.info = AgentInfo(name=name, capabilities=["code_generation"])
    agent.health_check = AsyncMock(return_value=healthy)

    if execute_fn is not None:
        agent.execute = execute_fn
    else:
        agent.execute = AsyncMock(
            return_value=TaskResult(task_id="", success=True, output="done")
        )

    return agent


def _make_decomposition(*tasks: tuple[str, list[int], str]) -> DecompositionResult:
    """Build a DecompositionResult from (title, dependencies, complexity) tuples."""
    sub_tasks = [
        SubTask(
            title=title,
            description=f"Desc: {title}",
            dependencies=deps,
            estimated_complexity=complexity,
        )
        for title, deps, complexity in tasks
    ]
    return DecompositionResult(
        original_request="test request",
        summary="Test decomposition",
        estimated_total_time="1h",
        sub_tasks=sub_tasks,
    )


def _tasks_by_title(scheduler: ParallelScheduler) -> dict[str, Task]:
    """Index scheduler tasks by title."""
    return {task.title: task for task in scheduler._tasks}


# ============================================================
# Basic parallel execution
# ============================================================

class TestBasicParallelExecution:
    """Core parallel scheduling logic."""

    @pytest.mark.asyncio
    async def test_empty_decomposition(self) -> None:
        """Empty decompositions should return no results."""
        agent = _make_agent()
        scheduler = ParallelScheduler([agent])
        decomp = DecompositionResult(
            original_request="nothing",
            summary="Empty",
            sub_tasks=[],
        )

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()

        assert results == []

    @pytest.mark.asyncio
    async def test_single_task(self) -> None:
        """A single ready task should complete successfully."""
        agent = _make_agent()
        scheduler = ParallelScheduler([agent])
        decomp = _make_decomposition(("Do thing", [], "low"))

        tasks = scheduler.submit_tasks(decomp)
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.READY

        results = await scheduler.run()

        assert len(results) == 1
        assert results[0].success is True
        assert tasks[0].status == TaskStatus.COMPLETED
        assert agent.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_parallel_independent_tasks(self) -> None:
        """Independent tasks should run concurrently."""
        start_times: dict[str, float] = {}
        end_times: dict[str, float] = {}

        async def slow_execute(task: Task, workdir: str) -> TaskResult:
            start_times[task.title] = time.time()
            await asyncio.sleep(0.10)
            end_times[task.title] = time.time()
            return TaskResult(task_id=task.id, success=True, output=task.title)

        agent_a = _make_agent(name="A", execute_fn=slow_execute)
        agent_b = _make_agent(name="B", execute_fn=slow_execute)
        scheduler = ParallelScheduler([agent_a, agent_b], max_concurrent=2)
        decomp = _make_decomposition(
            ("Task A", [], "low"),
            ("Task B", [], "low"),
        )

        scheduler.submit_tasks(decomp)
        started_at = time.time()
        results = await scheduler.run()
        elapsed = time.time() - started_at

        assert len(results) == 2
        assert all(result.success for result in results)
        assert elapsed < 0.18
        assert abs(start_times["Task A"] - start_times["Task B"]) < 0.05

    @pytest.mark.asyncio
    async def test_max_concurrent_respected(self) -> None:
        """The scheduler should never exceed max_concurrent executions."""
        current_running = 0
        max_running = 0
        lock = asyncio.Lock()

        async def tracked_execute(task: Task, workdir: str) -> TaskResult:
            nonlocal current_running, max_running
            async with lock:
                current_running += 1
                max_running = max(max_running, current_running)
            await asyncio.sleep(0.05)
            async with lock:
                current_running -= 1
            return TaskResult(task_id=task.id, success=True, output=task.title)

        agent = _make_agent(execute_fn=tracked_execute)
        scheduler = ParallelScheduler([agent], max_concurrent=2)
        decomp = _make_decomposition(
            ("T1", [], "low"),
            ("T2", [], "low"),
            ("T3", [], "low"),
            ("T4", [], "low"),
        )

        scheduler.submit_tasks(decomp)
        started_at = time.time()
        results = await scheduler.run()
        elapsed = time.time() - started_at

        assert len(results) == 4
        assert all(result.success for result in results)
        assert max_running == 2
        assert elapsed >= 0.09


# ============================================================
# Dependency handling
# ============================================================

class TestDependencyHandling:
    """Dependency-aware execution order."""

    @pytest.mark.asyncio
    async def test_sequential_dependency(self) -> None:
        """A -> B -> C should run strictly in order."""
        execution_order: list[str] = []

        async def track_execute(task: Task, workdir: str) -> TaskResult:
            execution_order.append(task.title)
            await asyncio.sleep(0.01)
            return TaskResult(task_id=task.id, success=True, output=task.title)

        agent = _make_agent(execute_fn=track_execute)
        scheduler = ParallelScheduler([agent], max_concurrent=3)
        decomp = _make_decomposition(
            ("Task A", [], "low"),
            ("Task B", [0], "medium"),
            ("Task C", [1], "medium"),
        )

        scheduler.submit_tasks(decomp)
        await scheduler.run()

        assert execution_order == ["Task A", "Task B", "Task C"]

    @pytest.mark.asyncio
    async def test_diamond_dependency(self) -> None:
        """A -> {B, C} -> D should fan out and then join."""
        start_times: dict[str, float] = {}
        end_times: dict[str, float] = {}
        durations = {
            "Task A": 0.05,
            "Task B": 0.08,
            "Task C": 0.08,
            "Task D": 0.01,
        }

        async def timed_execute(task: Task, workdir: str) -> TaskResult:
            start_times[task.title] = time.time()
            await asyncio.sleep(durations[task.title])
            end_times[task.title] = time.time()
            return TaskResult(task_id=task.id, success=True, output=task.title)

        agent = _make_agent(execute_fn=timed_execute)
        scheduler = ParallelScheduler([agent], max_concurrent=4)
        decomp = _make_decomposition(
            ("Task A", [], "low"),
            ("Task B", [0], "medium"),
            ("Task C", [0], "medium"),
            ("Task D", [1, 2], "high"),
        )

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()

        assert len(results) == 4
        assert all(result.success for result in results)
        assert start_times["Task B"] >= end_times["Task A"]
        assert start_times["Task C"] >= end_times["Task A"]
        assert abs(start_times["Task B"] - start_times["Task C"]) < 0.05
        assert start_times["Task D"] >= max(end_times["Task B"], end_times["Task C"])

    @pytest.mark.asyncio
    async def test_complex_dag(self) -> None:
        """A more complex DAG should respect all dependency edges."""
        start_times: dict[str, float] = {}
        end_times: dict[str, float] = {}
        durations = {
            "Task A": 0.05,
            "Task B": 0.05,
            "Task C": 0.04,
            "Task D": 0.04,
            "Task E": 0.04,
            "Task F": 0.01,
        }

        async def timed_execute(task: Task, workdir: str) -> TaskResult:
            start_times[task.title] = time.time()
            await asyncio.sleep(durations[task.title])
            end_times[task.title] = time.time()
            return TaskResult(task_id=task.id, success=True, output=task.title)

        agent = _make_agent(execute_fn=timed_execute)
        scheduler = ParallelScheduler([agent], max_concurrent=3)
        decomp = _make_decomposition(
            ("Task A", [], "low"),
            ("Task B", [], "low"),
            ("Task C", [0], "medium"),
            ("Task D", [0], "medium"),
            ("Task E", [1, 2], "high"),
            ("Task F", [3, 4], "high"),
        )

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()

        assert len(results) == 6
        assert all(result.success for result in results)
        assert abs(start_times["Task A"] - start_times["Task B"]) < 0.05
        assert start_times["Task C"] >= end_times["Task A"]
        assert start_times["Task D"] >= end_times["Task A"]
        assert abs(start_times["Task C"] - start_times["Task D"]) < 0.05
        assert start_times["Task E"] >= max(end_times["Task B"], end_times["Task C"])
        assert start_times["Task F"] >= max(end_times["Task D"], end_times["Task E"])


# ============================================================
# Failure and retry
# ============================================================

class TestFailureAndRetry:
    """Failure handling, retry, and cancellation."""

    @pytest.mark.asyncio
    async def test_task_retry_on_failure(self) -> None:
        """A failed task should be retried and eventually succeed."""
        call_count = 0

        async def fail_then_succeed(task: Task, workdir: str) -> TaskResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return TaskResult(task_id=task.id, success=False, error="oops")
            return TaskResult(task_id=task.id, success=True, output="fixed")

        agent = _make_agent(execute_fn=fail_then_succeed)
        scheduler = ParallelScheduler([agent])
        decomp = _make_decomposition(("Flaky task", [], "medium"))

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()
        task = scheduler._tasks[0]

        assert len(results) == 1
        assert results[0].success is True
        assert call_count == 2
        assert task.retry_count == 1
        assert task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_cascade_cancel(self) -> None:
        """A failed task should cancel all downstream dependents."""
        call_count = 0

        async def always_fail(task: Task, workdir: str) -> TaskResult:
            nonlocal call_count
            call_count += 1
            return TaskResult(task_id=task.id, success=False, error="fail")

        agent = _make_agent(execute_fn=always_fail)
        scheduler = ParallelScheduler([agent])
        decomp = _make_decomposition(
            ("Task A", [], "low"),
            ("Task B", [0], "medium"),
            ("Task C", [1], "high"),
        )

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()
        tasks = _tasks_by_title(scheduler)

        assert len(results) == 3
        assert tasks["Task A"].status == TaskStatus.FAILED
        assert tasks["Task B"].status == TaskStatus.CANCELLED
        assert tasks["Task C"].status == TaskStatus.CANCELLED
        assert tasks["Task B"].result is not None
        assert tasks["Task C"].result is not None
        assert "Dependency failed or was cancelled" in tasks["Task B"].result.error
        assert "Dependency failed or was cancelled" in tasks["Task C"].result.error
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_all_fail(self) -> None:
        """All independent tasks should fail after exhausting retries."""
        attempts: dict[str, int] = defaultdict(int)

        async def always_fail(task: Task, workdir: str) -> TaskResult:
            attempts[task.title] += 1
            await asyncio.sleep(0.01)
            return TaskResult(task_id=task.id, success=False, error=f"{task.title} failed")

        agent = _make_agent(execute_fn=always_fail)
        scheduler = ParallelScheduler([agent], max_concurrent=3)
        decomp = _make_decomposition(
            ("Task A", [], "low"),
            ("Task B", [], "low"),
            ("Task C", [], "low"),
        )

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()

        assert len(results) == 3
        assert all(result.success is False for result in results)
        assert all(task.status == TaskStatus.FAILED for task in scheduler._tasks)
        assert attempts == {"Task A": 3, "Task B": 3, "Task C": 3}


# ============================================================
# Agent management
# ============================================================

class TestAgentManagement:
    """Agent health and assignment behavior."""

    @pytest.mark.asyncio
    async def test_no_healthy_agents(self) -> None:
        """All tasks should fail immediately if no healthy agents exist."""
        agent_a = _make_agent(name="A", healthy=False)
        agent_b = _make_agent(name="B", healthy=False)
        scheduler = ParallelScheduler([agent_a, agent_b])
        decomp = _make_decomposition(
            ("Task A", [], "low"),
            ("Task B", [], "low"),
        )

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()

        assert len(results) == 2
        assert all(result.success is False for result in results)
        assert all("No healthy agents available" in (result.error or "") for result in results)
        assert agent_a.execute.call_count == 0
        assert agent_b.execute.call_count == 0

    @pytest.mark.asyncio
    async def test_agent_rotation(self) -> None:
        """Ready tasks should be assigned to agents in round-robin order."""
        assignments: dict[str, str] = {}

        async def execute_a(task: Task, workdir: str) -> TaskResult:
            assignments[task.title] = "A"
            await asyncio.sleep(0.01)
            return TaskResult(task_id=task.id, success=True)

        async def execute_b(task: Task, workdir: str) -> TaskResult:
            assignments[task.title] = "B"
            await asyncio.sleep(0.01)
            return TaskResult(task_id=task.id, success=True)

        agent_a = _make_agent(name="A", execute_fn=execute_a)
        agent_b = _make_agent(name="B", execute_fn=execute_b)
        scheduler = ParallelScheduler([agent_a, agent_b], max_concurrent=4)
        decomp = _make_decomposition(
            ("T1", [], "low"),
            ("T2", [], "low"),
            ("T3", [], "low"),
            ("T4", [], "low"),
        )

        scheduler.submit_tasks(decomp)
        await scheduler.run()

        assert assignments == {"T1": "A", "T2": "B", "T3": "A", "T4": "B"}
        assert [task.assigned_agent for task in scheduler._tasks] == ["A", "B", "A", "B"]


# ============================================================
# Progress callback
# ============================================================

class TestProgressCallback:
    """Progress callback behavior."""

    @pytest.mark.asyncio
    async def test_progress_callback_fired(self) -> None:
        """The callback should fire for each task state transition."""
        updates: list[ProgressUpdate] = []

        async def execute(task: Task, workdir: str) -> TaskResult:
            await asyncio.sleep(0.01)
            return TaskResult(task_id=task.id, success=True, output=task.title)

        agent = _make_agent(execute_fn=execute)
        scheduler = ParallelScheduler([agent])
        scheduler.set_progress_callback(updates.append)
        decomp = _make_decomposition(
            ("Task A", [], "low"),
            ("Task B", [0], "medium"),
        )

        scheduler.submit_tasks(decomp)
        await scheduler.run()

        assert len(updates) == 5
        assert [update.tasks_status[0]["status"] for update in updates] == [
            "running",
            "completed",
            "completed",
            "completed",
            "completed",
        ]
        assert [update.tasks_status[1]["status"] for update in updates] == [
            "pending",
            "pending",
            "ready",
            "running",
            "completed",
        ]
        assert updates[-1].is_final is True

    @pytest.mark.asyncio
    async def test_progress_data_accuracy(self) -> None:
        """Progress payloads should reflect current task state accurately."""
        updates: list[ProgressUpdate] = []

        async def execute(task: Task, workdir: str) -> TaskResult:
            await asyncio.sleep(0.01)
            return TaskResult(task_id=task.id, success=True, output="done")

        agent = _make_agent(name="agent-1", execute_fn=execute)
        scheduler = ParallelScheduler([agent])
        scheduler.set_progress_callback(updates.append)
        decomp = _make_decomposition(("Only task", [], "low"))

        scheduler.submit_tasks(decomp)
        await scheduler.run()

        assert len(updates) == 2

        running_update, completed_update = updates

        assert running_update.message == "0/1 tasks finished"
        assert running_update.overall_progress == 0.0
        assert running_update.is_final is False
        assert running_update.tasks_status == [
            {"title": "Only task", "status": "running", "agent": "agent-1"}
        ]

        assert completed_update.message == "1/1 tasks finished"
        assert completed_update.overall_progress == 1.0
        assert completed_update.is_final is True
        assert completed_update.tasks_status == [
            {"title": "Only task", "status": "completed", "agent": "agent-1"}
        ]
