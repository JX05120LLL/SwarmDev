"""Tests for the Codex agent adapter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from swarmdev.agents.codex_adapter import CodexAgentAdapter
from swarmdev.core.types import Task, TaskResult


# ============================================================
# Helpers
# ============================================================

def _make_task(title: str = "Test task", description: str = "") -> Task:
    return Task(
        title=title,
        description=description or title,
        metadata={"workdir": "/tmp/test"},
    )


# ============================================================
# Health check
# ============================================================

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_finds_codex(self) -> None:
        adapter = CodexAgentAdapter()
        with patch("shutil.which", return_value="/usr/bin/codex"):
            assert await adapter.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_codex_not_found(self) -> None:
        adapter = CodexAgentAdapter()
        with patch("shutil.which", return_value=None):
            assert await adapter.health_check() is False


# ============================================================
# Execute
# ============================================================

class TestExecute:
    @pytest.mark.asyncio
    async def test_successful_execution(self) -> None:
        adapter = CodexAgentAdapter()
        task = _make_task("Add login page", "Create a login form")

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(
            return_value=(b"Created login.tsx", b"")
        )
        mock_process.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            result = await adapter.execute(task, workdir="/tmp/project")

        assert result.success is True
        assert result.output == "Created login.tsx"
        assert result.error is None
        assert result.task_id == task.id
        assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_failed_execution(self) -> None:
        adapter = CodexAgentAdapter()
        task = _make_task("Bad task")

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(
            return_value=(b"", b"Error: invalid syntax")
        )
        mock_process.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            result = await adapter.execute(task, workdir="/tmp/project")

        assert result.success is False
        assert result.error == "Error: invalid syntax"
        assert result.metadata["returncode"] == 1

    @pytest.mark.asyncio
    async def test_codex_not_found(self) -> None:
        adapter = CodexAgentAdapter()
        task = _make_task("Test")

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("codex not found"),
        ):
            result = await adapter.execute(task, workdir="/tmp")

        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_execution_timeout(self) -> None:
        adapter = CodexAgentAdapter()
        task = _make_task("Long task")

        mock_process = AsyncMock()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        async def slow_communicate():
            await asyncio.sleep(999)
            return (b"", b"")

        mock_process.communicate = slow_communicate

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            patch("swarmdev.agents.codex_adapter.DEFAULT_TASK_TIMEOUT_SECONDS", 0.01),
        ):
            result = await adapter.execute(task, workdir="/tmp")

        assert result.success is False
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_uses_task_description_fallback(self) -> None:
        """When description is empty, falls back to title."""
        adapter = CodexAgentAdapter()
        task = _make_task(title="Build API", description="")

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"done", b""))
        mock_process.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await adapter.execute(task, workdir="/tmp")

        # Should use title since description is empty
        call_args = mock_exec.call_args
        assert "Build API" in call_args[0]


# ============================================================
# Info
# ============================================================

class TestInfo:
    def test_agent_info(self) -> None:
        adapter = CodexAgentAdapter()
        info = adapter.info

        assert info.name == "codex"
        assert "code_generation" in info.capabilities


# Need MagicMock for test_execution_timeout
from unittest.mock import MagicMock
