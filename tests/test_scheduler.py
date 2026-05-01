"""Tests for the task scheduler."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from swarmdev.core.types import (
    AgentAdapter,
    AgentInfo,
    DecompositionResult,
    SubTask,
    Task,
    TaskResult,
    TaskStatus,
)
from swarmdev.orchestrator.scheduler import TaskScheduler


# ============================================================
# Helpers
# ============================================================

def _make_agent(
    name: str = "test-agent",
    execute_fn: None | callable = None,
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


# ============================================================
# Basic scheduling
# ============================================================

class TestBasicScheduling:
    """Core scheduling logic."""

    @pytest.mark.asyncio
    async def test_single_task_completes(self) -> None:
        agent = _make_agent()
        scheduler = TaskScheduler([agent])
        decomp = _make_decomposition(("Do thing", [], "low"))

        tasks = scheduler.submit_tasks(decomp)
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.READY

        results = await scheduler.run()

        assert len(results) == 1
        assert results[0].success is True
        assert agent.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_two_independent_tasks(self) -> None:
        agent = _make_agent()
        scheduler = TaskScheduler([agent])
        decomp = _make_decomposition(
            ("Task A", [], "low"),
            ("Task B", [], "low"),
        )

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()

        assert len(results) == 2
        assert all(r.success for r in results)
        assert agent.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_dependent_tasks_ordered(self) -> None:
        """Task B depends on Task A — A must run before B."""
        execution_order: list[str] = []

        async def track_execute(task: Task, workdir: str) -> TaskResult:
            execution_order.append(task.title)
            return TaskResult(task_id=task.id, success=True, output="ok")

        agent = _make_agent(execute_fn=track_execute)
        scheduler = TaskScheduler([agent])
        decomp = _make_decomposition(
            ("Task A", [], "low"),
            ("Task B", [0], "medium"),  # depends on A (index 0)
        )

        scheduler.submit_tasks(decomp)
        await scheduler.run()

        assert execution_order == ["Task A", "Task B"]


# ============================================================
# Failure and retry
# ============================================================

class TestFailureAndRetry:
    """Task failure handling and retry logic."""

    @pytest.mark.asyncio
    async def test_failed_task_retries(self) -> None:
        call_count = 0

        async def fail_then_succeed(task: Task, workdir: str) -> TaskResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return TaskResult(task_id=task.id, success=False, error="oops")
            return TaskResult(task_id=task.id, success=True, output="fixed")

        agent = _make_agent(execute_fn=fail_then_succeed)
        scheduler = TaskScheduler([agent])
        decomp = _make_decomposition(("Flaky task", [], "medium"))

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()

        assert len(results) == 1
        assert results[0].success is True
        assert call_count == 2  # failed once, succeeded on retry

    @pytest.mark.asyncio
    async def test_task_fails_after_max_retries(self) -> None:
        call_count = 0

        async def always_fail(task: Task, workdir: str) -> TaskResult:
            nonlocal call_count
            call_count += 1
            return TaskResult(task_id=task.id, success=False, error="permanent failure")

        agent = _make_agent(execute_fn=always_fail)
        scheduler = TaskScheduler([agent])
        decomp = _make_decomposition(("Hopeless task", [], "low"))

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()

        assert len(results) == 1
        assert results[0].success is False
        # 1 initial + 2 retries = 3 calls
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_failed_dependency_cancels_dependent(self) -> None:
        async def always_fail(task: Task, workdir: str) -> TaskResult:
            return TaskResult(task_id=task.id, success=False, error="fail")

        agent = _make_agent(execute_fn=always_fail)
        scheduler = TaskScheduler([agent])
        decomp = _make_decomposition(
            ("Prerequisite", [], "low"),
            ("Dependent", [0], "medium"),
        )

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()

        results_by_title = {r.task_id: r for r in results}
        # Find the tasks
        tasks = scheduler._tasks
        prereq = next(t for t in tasks if t.title == "Prerequisite")
        dependent = next(t for t in tasks if t.title == "Dependent")

        assert prereq.status == TaskStatus.FAILED
        assert dependent.status == TaskStatus.CANCELLED


# ============================================================
# No healthy agents
# ============================================================

class TestNoHealthyAgents:
    """When all agents are unhealthy."""

    @pytest.mark.asyncio
    async def test_all_pending_fail(self) -> None:
        agent = _make_agent(healthy=False)
        scheduler = TaskScheduler([agent])
        decomp = _make_decomposition(
            ("A", [], "low"),
            ("B", [], "low"),
        )

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()

        assert len(results) == 2
        assert all(not r.success for r in results)
        assert all("No healthy agents" in r.error for r in results)

    @pytest.mark.asyncio
    async def test_empty_decomposition(self) -> None:
        agent = _make_agent()
        scheduler = TaskScheduler([agent])
        decomp = DecompositionResult(
            original_request="nothing",
            summary="Empty",
            sub_tasks=[],
        )

        scheduler.submit_tasks(decomp)
        results = await scheduler.run()

        assert results == []


# ============================================================
# Agent rotation
# ============================================================

class TestAgentRotation:
    """Verify round-robin agent selection."""

    @pytest.mark.asyncio
    async def test_round_robin_across_agents(self) -> None:
        agents_used: list[str] = []

        async def track_agent_a(task: Task, workdir: str) -> TaskResult:
            agents_used.append("A")
            return TaskResult(task_id=task.id, success=True)

        async def track_agent_b(task: Task, workdir: str) -> TaskResult:
            agents_used.append("B")
            return TaskResult(task_id=task.id, success=True)

        agent_a = _make_agent(name="A", execute_fn=track_agent_a)
        agent_b = _make_agent(name="B", execute_fn=track_agent_b)

        scheduler = TaskScheduler([agent_a, agent_b])
        decomp = _make_decomposition(
            ("T1", [], "low"),
            ("T2", [], "low"),
            ("T3", [], "low"),
        )

        scheduler.submit_tasks(decomp)
        await scheduler.run()

        assert agents_used == ["A", "B", "A"]  # round-robin


# ============================================================
# Progress reporting
# ============================================================

class TestProgressReporting:
    """Verify ProgressUpdate correctness."""

    @pytest.mark.asyncio
    async def test_progress_during_execution(self) -> None:
        agent = _make_agent()
        scheduler = TaskScheduler([agent])
        decomp = _make_decomposition(
            ("A", [], "low"),
            ("B", [], "low"),
        )

        scheduler.submit_tasks(decomp)

        progress = scheduler.get_progress()
        assert progress.overall_progress == 0.0
        assert progress.is_final is False

        await scheduler.run()

        progress = scheduler.get_progress()
        assert progress.overall_progress == 1.0
        assert progress.is_final is True

    @pytest.mark.asyncio
    async def test_progress_with_no_tasks(self) -> None:
        scheduler = TaskScheduler([])
        progress = scheduler.get_progress()

        assert progress.overall_progress == 0.0
        assert progress.is_final is False
        assert "No tasks" in progress.message


# ============================================================
# submit_tasks edge cases
# ============================================================

class TestSubmitTasks:
    """Verify task creation from decomposition."""

    def test_submit_preserves_metadata(self) -> None:
        agent = _make_agent()
        scheduler = TaskScheduler([agent])
        decomp = _make_decomposition(
            ("A", [], "high"),
            ("B", [0], "low"),
        )

        tasks = scheduler.submit_tasks(decomp)

        assert tasks[0].metadata["estimated_complexity"] == "high"
        assert tasks[0].metadata["subtask_index"] == 0
        assert tasks[1].metadata["subtask_index"] == 1

    def test_submit_sets_dependency_task_ids(self) -> None:
        agent = _make_agent()
        scheduler = TaskScheduler([agent])
        decomp = _make_decomposition(
            ("A", [], "low"),
            ("B", [0], "medium"),
        )

        tasks = scheduler.submit_tasks(decomp)

        # B's dependencies should reference A's task ID (not index)
        assert tasks[1].dependencies == [tasks[0].id]
        assert tasks[0].status == TaskStatus.READY  # no deps
        assert tasks[1].status == TaskStatus.PENDING  # has dep

    def test_submit_invalid_dependency_index_raises(self) -> None:
        agent = _make_agent()
        scheduler = TaskScheduler([agent])
        # Create a decomposition with an invalid dependency
        decomp = DecompositionResult(
            original_request="test",
            sub_tasks=[
                SubTask(title="A", description="A", dependencies=[99]),
            ],
        )

        with pytest.raises(ValueError, match="Invalid dependency"):
            scheduler.submit_tasks(decomp)
