"""Task scheduling for agent execution."""

from __future__ import annotations

from swarmdev.core.types import (
    AgentAdapter,
    DecompositionResult,
    ProgressUpdate,
    Task,
    TaskResult,
    TaskStatus,
)


class TaskScheduler:
    """Serial task scheduler with dependency handling and retries."""

    def __init__(self, agents: list[AgentAdapter]) -> None:
        self._agents = agents
        self._tasks: list[Task] = []
        self._next_agent_index = 0

    def submit_tasks(self, decomposition: DecompositionResult) -> list[Task]:
        tasks = [
            Task(
                title=sub_task.title,
                description=sub_task.description,
                files_to_modify=list(sub_task.files_to_modify),
                max_retries=2,
                metadata={
                    "estimated_complexity": sub_task.estimated_complexity,
                    "subtask_index": index,
                },
            )
            for index, sub_task in enumerate(decomposition.sub_tasks)
        ]

        for index, (task, sub_task) in enumerate(zip(tasks, decomposition.sub_tasks, strict=True)):
            try:
                task.dependencies = [tasks[dep_index].id for dep_index in sub_task.dependencies]
            except IndexError as exc:
                raise ValueError(f"Invalid dependency index in sub-task {index}") from exc
            task.status = TaskStatus.READY if not task.dependencies else TaskStatus.PENDING
            task.metadata["subtask_index"] = index

        self._tasks = tasks
        self._next_agent_index = 0
        return tasks

    async def run(self) -> list[TaskResult]:
        if not self._tasks:
            return []

        healthy_agents = [agent for agent in self._agents if await agent.health_check()]
        if not healthy_agents:
            self._fail_all_pending("No healthy agents available")
            return self._collect_results()

        while not self._is_finished():
            self._promote_ready_tasks()
            self._cancel_tasks_with_failed_dependencies()

            ready_tasks = [task for task in self._tasks if task.status == TaskStatus.READY]
            if ready_tasks:
                task = ready_tasks[0]
                agent = self._pick_agent(healthy_agents)
                await self._run_task(agent, task)
                continue

            if self._has_unresolved_pending_tasks():
                self._fail_unresolvable_pending_tasks()
                break

            break

        return self._collect_results()

    def get_progress(self) -> ProgressUpdate:
        total = len(self._tasks)
        terminal = sum(
            1
            for task in self._tasks
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        )
        overall_progress = (terminal / total) if total else 0.0

        return ProgressUpdate(
            message=f"{terminal}/{total} tasks finished" if total else "No tasks submitted",
            tasks_status=[
                {
                    "title": task.title,
                    "status": task.status.value,
                    "agent": task.assigned_agent or "",
                }
                for task in self._tasks
            ],
            overall_progress=overall_progress,
            is_final=bool(total) and terminal == total,
        )

    async def _run_task(self, agent: AgentAdapter, task: Task) -> None:
        task.status = TaskStatus.RUNNING
        task.assigned_agent = agent.info.name
        result = await agent.execute(task, workdir=task.metadata.get("workdir", "."))

        if result.success:
            task.result = result
            task.status = TaskStatus.COMPLETED
            return

        if task.retry_count < task.max_retries:
            task.retry_count += 1
            task.status = TaskStatus.READY
            return

        task.result = result
        task.status = TaskStatus.FAILED

    def _pick_agent(self, agents: list[AgentAdapter]) -> AgentAdapter:
        agent = agents[self._next_agent_index % len(agents)]
        self._next_agent_index = (self._next_agent_index + 1) % len(agents)
        return agent

    def _promote_ready_tasks(self) -> None:
        for task in self._tasks:
            if task.status != TaskStatus.PENDING:
                continue
            if all(self._task_by_id(dep_id).status == TaskStatus.COMPLETED for dep_id in task.dependencies):
                task.status = TaskStatus.READY

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

    def _has_unresolved_pending_tasks(self) -> bool:
        return any(task.status == TaskStatus.PENDING for task in self._tasks)

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

    def _fail_all_pending(self, error: str) -> None:
        for task in self._tasks:
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                continue
            task.status = TaskStatus.FAILED
            task.result = TaskResult(task_id=task.id, success=False, error=error)

    def _is_finished(self) -> bool:
        return all(
            task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            for task in self._tasks
        )

    def _collect_results(self) -> list[TaskResult]:
        return [task.result for task in self._tasks if task.result is not None]

    def _task_by_id(self, task_id: str) -> Task:
        for task in self._tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"Unknown task id: {task_id}")
