"""Automatic test execution and retry-based fixing for agent tasks."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

from codeswarm.core.types import AgentAdapter, Task, TaskResult


@dataclass
class TestResult:
    """Result of running the configured test command."""

    success: bool = False
    output: str = ""
    failed_count: int = 0
    passed_count: int = 0
    duration_seconds: float = 0.0


class AutoTester:
    """Run tests after an agent task and retry with fixes when needed."""

    def __init__(
        self,
        test_command: str = "python -m pytest",
        max_fix_attempts: int = 3,
        timeout: int = 120,
    ) -> None:
        self.test_command = test_command
        self.max_fix_attempts = max(0, max_fix_attempts)
        self.timeout = timeout

    async def run_tests(self, workdir: str) -> TestResult:
        """Run the configured test command and return the parsed result."""
        return await self._run_test_command(workdir, self.test_command)

    async def auto_test_and_fix(
        self,
        task: Task,
        agent: AgentAdapter,
        workdir: str,
        test_command: str,
    ) -> TaskResult:
        """Execute a task, run tests, and retry with fix prompts when needed."""
        start = time.monotonic()
        active_test_command = test_command or self.test_command

        latest_result = await agent.execute(task, workdir)
        if not latest_result.task_id:
            latest_result.task_id = task.id
        if not latest_result.success:
            return latest_result

        last_test_result = TestResult()

        for attempt in range(self.max_fix_attempts + 1):
            last_test_result = await self._run_test_command(workdir, active_test_command)
            if last_test_result.success:
                return TaskResult(
                    task_id=task.id,
                    success=True,
                    output=self._join_output(latest_result.output, last_test_result.output),
                    files_changed=list(latest_result.files_changed),
                    duration_seconds=time.monotonic() - start,
                    metadata={
                        "test_command": active_test_command,
                        "fix_attempts_used": attempt,
                        "test_passed_count": last_test_result.passed_count,
                        "test_failed_count": last_test_result.failed_count,
                        "test_duration_seconds": last_test_result.duration_seconds,
                    },
                )

            if attempt >= self.max_fix_attempts:
                break

            fix_task = Task(
                title=f"{task.title} - fix tests" if task.title else "Fix failing tests",
                description=f"以下测试失败了，请修复：{last_test_result.output}",
                files_to_modify=list(task.files_to_modify),
                metadata={
                    **task.metadata,
                    "parent_task_id": task.id,
                    "test_command": active_test_command,
                    "fix_attempt": attempt + 1,
                },
            )
            latest_result = await agent.execute(fix_task, workdir)
            if not latest_result.task_id:
                latest_result.task_id = fix_task.id
            if not latest_result.success:
                return TaskResult(
                    task_id=task.id,
                    success=False,
                    output=self._join_output(latest_result.output, last_test_result.output),
                    files_changed=list(latest_result.files_changed),
                    error=latest_result.error,
                    duration_seconds=time.monotonic() - start,
                    metadata={
                        "test_command": active_test_command,
                        "fix_attempts_used": attempt + 1,
                        "test_passed_count": last_test_result.passed_count,
                        "test_failed_count": last_test_result.failed_count,
                        "test_duration_seconds": last_test_result.duration_seconds,
                    },
                )

        return TaskResult(
            task_id=task.id,
            success=False,
            output=self._join_output(latest_result.output, last_test_result.output),
            files_changed=list(latest_result.files_changed),
            error=f"Tests failed after {self.max_fix_attempts} fix attempts",
            duration_seconds=time.monotonic() - start,
            metadata={
                "test_command": active_test_command,
                "fix_attempts_used": self.max_fix_attempts,
                "test_passed_count": last_test_result.passed_count,
                "test_failed_count": last_test_result.failed_count,
                "test_duration_seconds": last_test_result.duration_seconds,
            },
        )

    async def _run_test_command(self, workdir: str, test_command: str) -> TestResult:
        """Run a shell test command and collect stdout/stderr."""
        start = time.monotonic()
        process = await asyncio.create_subprocess_shell(
            test_command,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            duration = time.monotonic() - start
            combined_output = self._join_output(
                stdout.decode("utf-8", errors="replace").strip(),
                stderr.decode("utf-8", errors="replace").strip(),
                f"Test command timed out after {self.timeout} seconds",
            )
            passed_count, failed_count = self._parse_pytest_counts(combined_output)
            return TestResult(
                success=False,
                output=combined_output,
                failed_count=failed_count,
                passed_count=passed_count,
                duration_seconds=duration,
            )

        duration = time.monotonic() - start
        combined_output = self._join_output(
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )
        passed_count, failed_count = self._parse_pytest_counts(combined_output)
        return TestResult(
            success=process.returncode == 0,
            output=combined_output,
            failed_count=failed_count,
            passed_count=passed_count,
            duration_seconds=duration,
        )

    def _parse_pytest_counts(self, output: str) -> tuple[int, int]:
        """Extract passed and failed counts from pytest output."""
        passed_count = 0
        failed_count = 0
        summary_line = self._find_summary_line(output)

        for count_text, status in re.findall(
            r"(\d+)\s+(passed|failed|error|errors)",
            summary_line,
            flags=re.IGNORECASE,
        ):
            count = int(count_text)
            normalized = status.lower()
            if normalized == "passed":
                passed_count += count
            else:
                failed_count += count

        return passed_count, failed_count

    def _find_summary_line(self, output: str) -> str:
        """Return the pytest summary line when present."""
        lines = [line.strip(" =") for line in output.splitlines() if line.strip()]
        for line in reversed(lines):
            if " in " not in line:
                continue
            if re.search(r"\b(passed|failed|error|errors)\b", line, flags=re.IGNORECASE):
                return line
        return output

    def _join_output(self, *parts: str) -> str:
        """Join non-empty output chunks with blank lines."""
        normalized_parts = [part.strip() for part in parts if part and part.strip()]
        return "\n\n".join(normalized_parts)
