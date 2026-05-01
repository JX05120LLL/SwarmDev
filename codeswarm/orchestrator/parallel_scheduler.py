"""Parallel task scheduling for agent execution."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from codeswarm.core.types import AgentAdapter, ProgressUpdate, Task, TaskResult, TaskStatus
from codeswarm.orchestrator.scheduler import TaskScheduler


class ParallelScheduler(TaskScheduler):
    """Parallel task scheduler with dependency handling and retries."""

    def __init__(self, agents: list[AgentAdapter], max_concurrent: int = 3) -> None:
        super().__init__(agents)
        self._max_concurrent = max_concurrent
        self._progress_callback: Callable[[ProgressUpdate], None] | None = None

    def set_progress_callback(self, callback: Callable[[ProgressUpdate], None]) -> None:
        """Register a callback for task progress updates."""
        self._progress_callback = callback

    async def run(self) -> list[TaskResult]:
        if not self._tasks:
            return []

        healthy_agents = [agent for agent in self._agents if await agent.health_check()]
        if not healthy_agents:
            self._fail_all_pending("No healthy agents available")
            return self._collect_results()

        semaphore = asyncio.Semaphore(self._max_concurrent)
        in_flight: dict[asyncio.Task[TaskResult], str] = {}

        while not self._is_finished():
            self._promote_ready_tasks()
            self._cancel_tasks_with_failed_dependencies()

            active_task_ids = set(in_flight.values())
            ready_tasks = [
                task
                for task in self._tasks
                if task.status == TaskStatus.READY and task.id not in active_task_ids
            ]
            for task in ready_tasks:
                agent = self._pick_agent(healthy_agents)
                runner = asyncio.create_task(self._run_single_task(agent, task, semaphore))
                in_flight[runner] = task.id

            if not in_flight:
                if self._has_unresolved_pending_tasks():
                    self._fail_unresolvable_pending_tasks()
                    continue
                break

            done, _ = await asyncio.wait(in_flight.keys(), return_when=asyncio.FIRST_COMPLETED)
            for runner in done:
                in_flight.pop(runner, None)
                await runner

        return self._collect_results()

    async def _run_single_task(
        self,
        agent: AgentAdapter,
        task: Task,
        semaphore: asyncio.Semaphore,
    ) -> TaskResult:
        while True:
            async with semaphore:
                task.status = TaskStatus.RUNNING
                task.assigned_agent = agent.info.name
                self._emit_progress()

                try:
                    result = await agent.execute(task, workdir=task.metadata.get("workdir", "."))
                except Exception as exc:
                    result = TaskResult(task_id=task.id, success=False, error=str(exc))

            if not result.task_id:
                result.task_id = task.id

            if result.success:
                task.result = result
                task.status = TaskStatus.COMPLETED
                self._emit_progress()
                return result

            if task.retry_count < task.max_retries:
                task.retry_count += 1
                task.status = TaskStatus.READY
                self._emit_progress()
                continue

            task.result = result
            task.status = TaskStatus.FAILED
            self._emit_progress()
            return result

    def _promote_ready_tasks(self) -> None:
        for task in self._tasks:
            if task.status != TaskStatus.PENDING:
                continue
            if all(self._task_by_id(dep_id).status == TaskStatus.COMPLETED for dep_id in task.dependencies):
                task.status = TaskStatus.READY
                self._emit_progress()

    def _cancel_tasks_with_failed_dependencies(self) -> None:
        for task in self._tasks:
            if task.status != TaskStatus.PENDING:
                continue
            dependency_statuses = [self._task_by_id(dep_id).status for dep_id in task.dependencies]
            if any(status in {TaskStatus.FAILED, TaskStatus.CANCELLED} for status in dependency_statuses):
                task.status = TaskStatus.CANCELLED
                task.result = TaskResult(
                    task_id=task.id,
                    success=False,
                    error="Dependency failed or was cancelled",
                )
                self._emit_progress()

    def _fail_unresolvable_pending_tasks(self) -> None:
        for task in self._tasks:
            if task.status != TaskStatus.PENDING:
                continue
            task.status = TaskStatus.FAILED
            task.result = TaskResult(
                task_id=task.id,
                success=False,
                error="Task dependencies could not be resolved",
            )
            self._emit_progress()

    def _fail_all_pending(self, error: str) -> None:
        for task in self._tasks:
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                continue
            task.status = TaskStatus.FAILED
            task.result = TaskResult(task_id=task.id, success=False, error=error)
            self._emit_progress()

    def _has_unresolved_pending_tasks(self) -> bool:
        return any(task.status == TaskStatus.PENDING for task in self._tasks)

    def _emit_progress(self) -> None:
        if self._progress_callback is None:
            return
        self._progress_callback(self.get_progress())
