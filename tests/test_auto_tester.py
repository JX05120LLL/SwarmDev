"""Tests for the automatic test execution and retry-based fixing module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeswarm.core.types import AgentAdapter, Task, TaskResult
from codeswarm.orchestrator.auto_tester import AutoTester, TestResult


# ============================================================
# Helpers
# ============================================================

def _make_task(
    title: str = "Build feature",
    description: str = "Implement something",
    files_to_modify: list[str] | None = None,
    metadata: dict | None = None,
) -> Task:
    """Create a Task with sensible defaults."""
    return Task(
        id="task-1",
        title=title,
        description=description,
        files_to_modify=files_to_modify or [],
        metadata=metadata or {},
    )


def _make_agent(
    execute_fn: None | callable = None,
) -> AgentAdapter:
    """Create a mock AgentAdapter."""
    agent = MagicMock(spec=AgentAdapter)

    if execute_fn is not None:
        agent.execute = execute_fn
    else:
        agent.execute = AsyncMock(
            return_value=TaskResult(task_id="", success=True, output="done")
        )

    return agent


def _make_process(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> MagicMock:
    """Create a mock subprocess process."""
    process = MagicMock()
    process.returncode = returncode
    process.communicate = AsyncMock(return_value=(stdout, stderr))
    process.kill = MagicMock()
    return process


# ============================================================
# TestResult parsing — run_tests via subprocess mocking
# ============================================================


class TestRunTests:
    """Test the run_tests / _run_test_command pathway."""

    @pytest.mark.asyncio
    async def test_successful_tests(self) -> None:
        """All tests pass — success is True and counts are parsed."""
        pytest_output = (
            "============================= test session starts =============================\n"
            "collected 78 items\n"
            "\n"
            "======================== 78 passed in 1.23s ========================\n"
        )
        process = _make_process(
            returncode=0,
            stdout=pytest_output.encode(),
            stderr=b"",
        )

        tester = AutoTester()

        with patch(
            "codeswarm.orchestrator.auto_tester.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=process,
        ):
            result = await tester.run_tests("/tmp/workdir")

        assert result.success is True
        assert result.passed_count == 78
        assert result.failed_count == 0
        assert "78 passed" in result.output

    @pytest.mark.asyncio
    async def test_failed_tests(self) -> None:
        """Some tests fail — success is False, counts reflect failures."""
        pytest_output = (
            "============================= test session starts =============================\n"
            "collected 78 items\n"
            "\n"
            "FAILED test_bar.py::test_baz - AssertionError\n"
            "FAILED test_foo.py::test_qux - AssertionError\n"
            "\n"
            "======================== 2 failed, 76 passed in 2.5s ========================\n"
        )
        process = _make_process(
            returncode=1,
            stdout=pytest_output.encode(),
            stderr=b"",
        )

        tester = AutoTester()

        with patch(
            "codeswarm.orchestrator.auto_tester.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=process,
        ):
            result = await tester.run_tests("/tmp/workdir")

        assert result.success is False
        assert result.failed_count == 2
        assert result.passed_count == 76
        assert "2 failed" in result.output

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        """Process hangs longer than timeout — it gets killed."""
        process = _make_process(returncode=-9, stdout=b"", stderr=b"")

        tester = AutoTester(timeout=1)

        async def _hang(*args, **kwargs):
            await asyncio.sleep(999)

        with patch(
            "codeswarm.orchestrator.auto_tester.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=process,
        ), patch(
            "codeswarm.orchestrator.auto_tester.asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError,
        ):
            result = await tester.run_tests("/tmp/workdir")

        assert result.success is False
        assert "timed out" in result.output
        process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_output(self) -> None:
        """Empty stdout and stderr are handled gracefully."""
        process = _make_process(
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

        tester = AutoTester()

        with patch(
            "codeswarm.orchestrator.auto_tester.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=process,
        ):
            result = await tester.run_tests("/tmp/workdir")

        assert result.success is True
        assert result.passed_count == 0
        assert result.failed_count == 0
        # Output should be empty string (join_output strips empty parts)
        assert result.output == ""


# ============================================================
# Auto test-and-fix orchestration
# ============================================================


class TestAutoTestAndFix:
    """Test the auto_test_and_fix orchestration logic."""

    @pytest.mark.asyncio
    async def test_task_succeeds_first_try(self) -> None:
        """Agent succeeds and tests pass on the first try — no fix attempts."""
        agent = _make_agent()
        agent.execute = AsyncMock(
            return_value=TaskResult(task_id="task-1", success=True, output="built")
        )

        pytest_output = b"======================== 10 passed in 0.5s ========================\n"
        process = _make_process(returncode=0, stdout=pytest_output, stderr=b"")

        tester = AutoTester(max_fix_attempts=3)

        task = _make_task()

        with patch(
            "codeswarm.orchestrator.auto_tester.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=process,
        ):
            result = await tester.auto_test_and_fix(task, agent, "/tmp/workdir", "")

        assert result.success is True
        assert result.metadata["fix_attempts_used"] == 0
        assert result.metadata["test_passed_count"] == 10
        assert result.metadata["test_failed_count"] == 0
        # Agent was called exactly once (no fix attempts)
        assert agent.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_task_succeeds_after_fix(self) -> None:
        """Agent succeeds but tests fail, fix attempt succeeds."""
        state = {"call_count": 0}

        async def agent_execute(task: Task, workdir: str) -> TaskResult:
            state["call_count"] += 1
            if state["call_count"] == 1:
                # Initial execution succeeds
                return TaskResult(task_id="task-1", success=True, output="built")
            else:
                # Fix attempt
                return TaskResult(task_id="fix-task-1", success=True, output="fixed")

        agent = _make_agent(execute_fn=agent_execute)

        fail_output = b"======================== 2 failed, 8 passed in 1.0s ========================\n"
        pass_output = b"======================== 10 passed in 0.8s ========================\n"

        fail_process = _make_process(returncode=1, stdout=fail_output, stderr=b"")
        pass_process = _make_process(returncode=0, stdout=pass_output, stderr=b"")

        tester = AutoTester(max_fix_attempts=3)
        task = _make_task()

        call_idx = {"n": 0}

        async def mock_subprocess(*args, **kwargs):
            call_idx["n"] += 1
            if call_idx["n"] == 1:
                return fail_process
            return pass_process

        with patch(
            "codeswarm.orchestrator.auto_tester.asyncio.create_subprocess_shell",
            side_effect=mock_subprocess,
        ):
            result = await tester.auto_test_and_fix(task, agent, "/tmp/workdir", "")

        assert result.success is True
        assert result.metadata["fix_attempts_used"] == 1
        # Agent was called twice: initial + one fix
        assert state["call_count"] == 2

    @pytest.mark.asyncio
    async def test_task_fails_all_fixes(self) -> None:
        """Agent succeeds but tests keep failing through all fix attempts."""
        state = {"call_count": 0}

        async def agent_execute(task: Task, workdir: str) -> TaskResult:
            state["call_count"] += 1
            return TaskResult(task_id="task-1", success=True, output="built")

        agent = _make_agent(execute_fn=agent_execute)

        fail_output = b"======================== 5 failed in 1.0s ========================\n"
        fail_process = _make_process(returncode=1, stdout=fail_output, stderr=b"")

        tester = AutoTester(max_fix_attempts=2)
        task = _make_task()

        with patch(
            "codeswarm.orchestrator.auto_tester.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=fail_process,
        ):
            result = await tester.auto_test_and_fix(task, agent, "/tmp/workdir", "")

        assert result.success is False
        assert "Tests failed after 2 fix attempts" in result.error
        assert result.metadata["fix_attempts_used"] == 2
        # Initial execution + 2 fix attempts = 3 agent calls
        assert state["call_count"] == 3

    @pytest.mark.asyncio
    async def test_agent_execution_fails(self) -> None:
        """Agent returns failure — tests are never run."""
        agent = _make_agent()
        agent.execute = AsyncMock(
            return_value=TaskResult(
                task_id="task-1", success=False, output="", error="agent crashed"
            )
        )

        tester = AutoTester(max_fix_attempts=3)
        task = _make_task()

        with patch(
            "codeswarm.orchestrator.auto_tester.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
        ) as mock_subprocess:
            result = await tester.auto_test_and_fix(task, agent, "/tmp/workdir", "")

        assert result.success is False
        assert result.error == "agent crashed"
        # Subprocess was never called — tests never ran
        mock_subprocess.assert_not_called()
        # Agent was called exactly once
        assert agent.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_fix_prompt_includes_test_output(self) -> None:
        """The fix task description contains the test error output."""
        captured_tasks: list[Task] = []

        async def agent_execute(task: Task, workdir: str) -> TaskResult:
            captured_tasks.append(task)
            return TaskResult(task_id="task-1", success=True, output="built")

        agent = _make_agent(execute_fn=agent_execute)

        error_output = (
            "FAILED test_foo.py::test_bar - AssertionError: expected 42, got 0\n"
            "======================== 1 failed, 9 passed in 0.5s ========================\n"
        )
        fail_process = _make_process(
            returncode=1,
            stdout=error_output.encode(),
            stderr=b"",
        )

        tester = AutoTester(max_fix_attempts=1)
        task = _make_task(title="Build widget")

        with patch(
            "codeswarm.orchestrator.auto_tester.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=fail_process,
        ):
            await tester.auto_test_and_fix(task, agent, "/tmp/workdir", "")

        # captured_tasks[0] is the original, captured_tasks[1] is the fix task
        assert len(captured_tasks) == 2
        fix_task = captured_tasks[1]
        assert "AssertionError" in fix_task.description or "expected 42" in fix_task.description
        assert "1 failed" in fix_task.description or "test_foo" in fix_task.description
        # Verify metadata on the fix task
        assert fix_task.metadata["parent_task_id"] == "task-1"
        assert fix_task.metadata["fix_attempt"] == 1
        # test_command="" falls back to the default "python -m pytest"
        assert fix_task.metadata["test_command"] == "python -m pytest"
        # Fix title includes original title
        assert "Build widget" in fix_task.title
