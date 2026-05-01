"""End-to-end integration tests for decomposition and scheduling."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from swarmdev.core.config import LLMConfig
from swarmdev.core.types import (
    AgentAdapter,
    AgentInfo,
    ChatMessage,
    MessageType,
    ProgressUpdate,
    Task,
    TaskResult,
    TaskStatus,
)
from swarmdev.orchestrator.decomposer import LLMDecomposer
from swarmdev.orchestrator.parallel_scheduler import ParallelScheduler


ExecuteFn = Callable[[Task, str], Awaitable[TaskResult]]


@pytest.fixture
def config() -> LLMConfig:
    return LLMConfig(
        model="gpt-4o",
        api_key="test-key",
        temperature=0.1,
    )


def _make_chat_message(text: str) -> ChatMessage:
    return ChatMessage(
        channel="telegram",
        chat_id="chat-123",
        user_id="user-456",
        text=text,
        message_type=MessageType.USER_REQUEST,
        metadata={"project_context": "Repository under test"},
    )


def _make_agent(
    name: str = "codex",
    execute_fn: ExecuteFn | None = None,
    healthy: bool = True,
) -> AgentAdapter:
    agent = MagicMock(spec=AgentAdapter)
    agent.info = AgentInfo(
        name=name,
        agent_type="codex",
        capabilities=["code_generation"],
    )
    agent.health_check = AsyncMock(return_value=healthy)

    if execute_fn is not None:
        agent.execute = execute_fn
    else:
        agent.execute = AsyncMock(
            return_value=TaskResult(task_id="", success=True, output="done")
        )

    return agent


def _make_llm_response(content: str) -> MagicMock:
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_openai_client(
    *,
    content: str | None = None,
    side_effect: Exception | list[object] | None = None,
) -> MagicMock:
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()

    if side_effect is not None:
        client.chat.completions.create = AsyncMock(side_effect=side_effect)
    else:
        assert content is not None
        client.chat.completions.create = AsyncMock(
            return_value=_make_llm_response(content)
        )

    return client


def _tasks_by_title(scheduler: ParallelScheduler) -> dict[str, Task]:
    return {task.title: task for task in scheduler._tasks}


class TestEndToEndPipeline:
    @pytest.mark.asyncio
    async def test_happy_path_pipeline_with_progress_callback(
        self,
        config: LLMConfig,
    ) -> None:
        chat_message = _make_chat_message("Add login flow")
        llm_output = json.dumps({
            "summary": "Implement login flow",
            "estimated_total_time": "2h",
            "sub_tasks": [
                {
                    "title": "Build login form",
                    "description": "Create the UI for login",
                    "files_to_modify": ["src/login.tsx"],
                    "dependencies": [],
                    "estimated_complexity": "medium",
                },
                {
                    "title": "Wire auth endpoint",
                    "description": "Connect the form to the backend",
                    "files_to_modify": ["src/auth.py"],
                    "dependencies": [0],
                    "estimated_complexity": "high",
                },
            ],
        })
        client = _make_openai_client(content=llm_output)
        progress_updates: list[ProgressUpdate] = []
        execution_order: list[str] = []

        async def execute(task: Task, workdir: str) -> TaskResult:
            execution_order.append(task.title)
            await asyncio.sleep(0.01)
            return TaskResult(
                task_id=task.id,
                success=True,
                output=f"completed:{task.title}",
                files_changed=task.files_to_modify,
                metadata={"workdir": workdir},
            )

        agent = _make_agent(execute_fn=execute)
        decomposer = LLMDecomposer(config, max_retries=0)
        scheduler = ParallelScheduler([agent])
        scheduler.set_progress_callback(progress_updates.append)

        with patch("swarmdev.orchestrator.decomposer.AsyncOpenAI", return_value=client) as mock_openai:
            decomposition = await decomposer.decompose(
                chat_message.text,
                project_context=chat_message.metadata["project_context"],
            )

        tasks = scheduler.submit_tasks(decomposition)
        results = await scheduler.run()

        assert chat_message.message_type == MessageType.USER_REQUEST
        assert decomposition.summary == "Implement login flow"
        assert [sub_task.title for sub_task in decomposition.sub_tasks] == [
            "Build login form",
            "Wire auth endpoint",
        ]
        assert [task.status for task in tasks] == [TaskStatus.COMPLETED, TaskStatus.COMPLETED]
        assert execution_order == ["Build login form", "Wire auth endpoint"]
        assert [result.output for result in results] == [
            "completed:Build login form",
            "completed:Wire auth endpoint",
        ]
        assert len(progress_updates) == 5
        assert [update.tasks_status[1]["status"] for update in progress_updates] == [
            "pending",
            "pending",
            "ready",
            "running",
            "completed",
        ]
        assert progress_updates[-1].message == "2/2 tasks finished"
        assert progress_updates[-1].overall_progress == 1.0
        assert progress_updates[-1].is_final is True
        mock_openai.assert_called_once_with(api_key="test-key")
        client.chat.completions.create.assert_awaited_once()
        create_kwargs = client.chat.completions.create.await_args.kwargs
        assert create_kwargs["model"] == "gpt-4o"
        assert chat_message.text in create_kwargs["messages"][1]["content"]

    @pytest.mark.asyncio
    async def test_multi_task_decomposition_respects_dependency_ordering(
        self,
        config: LLMConfig,
    ) -> None:
        chat_message = _make_chat_message("Implement a dashboard with shared data loading")
        llm_output = json.dumps({
            "summary": "Build dashboard pipeline",
            "estimated_total_time": "4h",
            "sub_tasks": [
                {
                    "title": "Fetch dashboard data",
                    "description": "Add the shared data loader",
                    "files_to_modify": ["src/data.py"],
                    "dependencies": [],
                    "estimated_complexity": "medium",
                },
                {
                    "title": "Render summary cards",
                    "description": "Use the loaded data in cards",
                    "files_to_modify": ["src/cards.tsx"],
                    "dependencies": [0],
                    "estimated_complexity": "low",
                },
                {
                    "title": "Render activity chart",
                    "description": "Use the loaded data in the chart",
                    "files_to_modify": ["src/chart.tsx"],
                    "dependencies": [0],
                    "estimated_complexity": "medium",
                },
                {
                    "title": "Add dashboard smoke test",
                    "description": "Validate both views render",
                    "files_to_modify": ["tests/test_dashboard.py"],
                    "dependencies": [1, 2],
                    "estimated_complexity": "medium",
                },
            ],
        })
        client = _make_openai_client(content=llm_output)
        start_times: dict[str, float] = {}
        end_times: dict[str, float] = {}
        durations = {
            "Fetch dashboard data": 0.04,
            "Render summary cards": 0.07,
            "Render activity chart": 0.07,
            "Add dashboard smoke test": 0.01,
        }

        async def execute(task: Task, workdir: str) -> TaskResult:
            start_times[task.title] = time.time()
            await asyncio.sleep(durations[task.title])
            end_times[task.title] = time.time()
            return TaskResult(task_id=task.id, success=True, output=task.title)

        agent_a = _make_agent(name="codex-a", execute_fn=execute)
        agent_b = _make_agent(name="codex-b", execute_fn=execute)
        decomposer = LLMDecomposer(config, max_retries=0)
        scheduler = ParallelScheduler([agent_a, agent_b], max_concurrent=2)

        with patch("swarmdev.orchestrator.decomposer.AsyncOpenAI", return_value=client):
            decomposition = await decomposer.decompose(chat_message.text)

        scheduler.submit_tasks(decomposition)
        results = await scheduler.run()

        assert [sub_task.dependencies for sub_task in decomposition.sub_tasks] == [
            [],
            [0],
            [0],
            [1, 2],
        ]
        assert len(results) == 4
        assert all(result.success for result in results)
        assert start_times["Render summary cards"] >= end_times["Fetch dashboard data"]
        assert start_times["Render activity chart"] >= end_times["Fetch dashboard data"]
        assert abs(start_times["Render summary cards"] - start_times["Render activity chart"]) < 0.05
        assert start_times["Add dashboard smoke test"] >= max(
            end_times["Render summary cards"],
            end_times["Render activity chart"],
        )


class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_decompose_failure_falls_back_and_still_runs(
        self,
        config: LLMConfig,
    ) -> None:
        chat_message = _make_chat_message("Refactor the parser")
        client = _make_openai_client(side_effect=RuntimeError("OpenAI unavailable"))

        async def execute(task: Task, workdir: str) -> TaskResult:
            return TaskResult(task_id=task.id, success=True, output=f"fallback:{task.title}")

        agent = _make_agent(execute_fn=execute)
        decomposer = LLMDecomposer(config, max_retries=0)
        scheduler = ParallelScheduler([agent])

        with patch("swarmdev.orchestrator.decomposer.AsyncOpenAI", return_value=client):
            decomposition = await decomposer.decompose(chat_message.text)

        scheduler.submit_tasks(decomposition)
        results = await scheduler.run()

        assert "Fallback" in decomposition.summary
        assert len(decomposition.sub_tasks) == 1
        assert decomposition.sub_tasks[0].title == "Handle user request"
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].output == "fallback:Handle user request"
        client.chat.completions.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_failure_cascades_to_dependents(
        self,
        config: LLMConfig,
    ) -> None:
        chat_message = _make_chat_message("Ship a feature behind a dependency chain")
        llm_output = json.dumps({
            "summary": "Dependency chain",
            "sub_tasks": [
                {
                    "title": "Task A",
                    "description": "Do A",
                    "files_to_modify": [],
                    "dependencies": [],
                    "estimated_complexity": "low",
                },
                {
                    "title": "Task B",
                    "description": "Do B",
                    "files_to_modify": [],
                    "dependencies": [0],
                    "estimated_complexity": "medium",
                },
                {
                    "title": "Task C",
                    "description": "Do C",
                    "files_to_modify": [],
                    "dependencies": [1],
                    "estimated_complexity": "high",
                },
            ],
        })
        client = _make_openai_client(content=llm_output)
        attempts = defaultdict(int)

        async def execute(task: Task, workdir: str) -> TaskResult:
            attempts[task.title] += 1
            return TaskResult(task_id=task.id, success=False, error=f"{task.title} failed")

        agent = _make_agent(execute_fn=execute)
        decomposer = LLMDecomposer(config, max_retries=0)
        scheduler = ParallelScheduler([agent])

        with patch("swarmdev.orchestrator.decomposer.AsyncOpenAI", return_value=client):
            decomposition = await decomposer.decompose(chat_message.text)

        scheduler.submit_tasks(decomposition)
        results = await scheduler.run()
        tasks = _tasks_by_title(scheduler)

        assert len(results) == 3
        assert tasks["Task A"].status == TaskStatus.FAILED
        assert tasks["Task B"].status == TaskStatus.CANCELLED
        assert tasks["Task C"].status == TaskStatus.CANCELLED
        assert tasks["Task A"].result is not None
        assert tasks["Task A"].result.error == "Task A failed"
        assert tasks["Task B"].result is not None
        assert "Dependency failed or was cancelled" in tasks["Task B"].result.error
        assert tasks["Task C"].result is not None
        assert "Dependency failed or was cancelled" in tasks["Task C"].result.error
        assert attempts == {"Task A": 3}

    @pytest.mark.asyncio
    async def test_partial_success_keeps_independent_results(
        self,
        config: LLMConfig,
    ) -> None:
        chat_message = _make_chat_message("Run independent work alongside a failing chain")
        llm_output = json.dumps({
            "summary": "Partial success graph",
            "sub_tasks": [
                {
                    "title": "Task A",
                    "description": "Failing root task",
                    "files_to_modify": [],
                    "dependencies": [],
                    "estimated_complexity": "low",
                },
                {
                    "title": "Task B",
                    "description": "Depends on A",
                    "files_to_modify": [],
                    "dependencies": [0],
                    "estimated_complexity": "medium",
                },
                {
                    "title": "Task C",
                    "description": "Independent task",
                    "files_to_modify": [],
                    "dependencies": [],
                    "estimated_complexity": "low",
                },
            ],
        })
        client = _make_openai_client(content=llm_output)
        attempts = defaultdict(int)

        async def execute(task: Task, workdir: str) -> TaskResult:
            attempts[task.title] += 1
            await asyncio.sleep(0.01)
            if task.title == "Task A":
                return TaskResult(task_id=task.id, success=False, error="Task A failed")
            return TaskResult(task_id=task.id, success=True, output=f"{task.title} complete")

        agent = _make_agent(execute_fn=execute)
        decomposer = LLMDecomposer(config, max_retries=0)
        scheduler = ParallelScheduler([agent], max_concurrent=2)

        with patch("swarmdev.orchestrator.decomposer.AsyncOpenAI", return_value=client):
            decomposition = await decomposer.decompose(chat_message.text)

        scheduler.submit_tasks(decomposition)
        results = await scheduler.run()
        tasks = _tasks_by_title(scheduler)

        assert len(results) == 3
        assert tasks["Task A"].status == TaskStatus.FAILED
        assert tasks["Task B"].status == TaskStatus.CANCELLED
        assert tasks["Task C"].status == TaskStatus.COMPLETED
        assert tasks["Task C"].result is not None
        assert tasks["Task C"].result.output == "Task C complete"
        assert attempts["Task A"] == 3
        assert attempts["Task C"] == 1
