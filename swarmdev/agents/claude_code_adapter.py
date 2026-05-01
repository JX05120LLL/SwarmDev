"""Claude Code agent adapter implementation."""

from __future__ import annotations

import asyncio
import shutil
import time

from swarmdev.core.types import AgentAdapter, AgentInfo, Task, TaskResult
from swarmdev.core.types import DEFAULT_TASK_TIMEOUT_SECONDS


class ClaudeCodeAgentAdapter(AgentAdapter):
    """Adapter for executing tasks with the Claude Code CLI."""

    def __init__(self, model: str | None = None) -> None:
        self._info = AgentInfo(
            name="claude_code",
            agent_type="claude_code",
            capabilities=["code_generation", "code_editing", "shell_commands"],
        )
        self._model = model

    @property
    def info(self) -> AgentInfo:
        return self._info

    async def execute(self, task: Task, workdir: str) -> TaskResult:
        start = time.monotonic()
        description = task.description or task.title

        cmd = [
            "claude",
            "-p", description,
            "--allowedTools", "Edit", "Write", "Bash",
        ]
        if self._model:
            cmd.extend(["--model", self._model])

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            duration = time.monotonic() - start
            return TaskResult(
                task_id=task.id,
                success=False,
                error="claude command not found",
                duration_seconds=duration,
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=DEFAULT_TASK_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            duration = time.monotonic() - start
            return TaskResult(
                task_id=task.id,
                success=False,
                error=f"Task timed out after {DEFAULT_TASK_TIMEOUT_SECONDS} seconds",
                duration_seconds=duration,
            )

        duration = time.monotonic() - start
        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip() or None

        return TaskResult(
            task_id=task.id,
            success=process.returncode == 0,
            output=output,
            error=error if process.returncode != 0 else None,
            duration_seconds=duration,
            metadata={"returncode": process.returncode},
        )

    async def health_check(self) -> bool:
        return shutil.which("claude") is not None
