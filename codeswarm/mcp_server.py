"""MCP Server for CodeSwarm — exposes decomposition, execution, and testing as MCP tools."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Any

from mcp.server.fastmcp import FastMCP

from codeswarm.core.config import LLMConfig

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# MCP Server
# -------------------------------------------------------------------

mcp = FastMCP("CodeSwarm")


def _build_llm_config() -> LLMConfig:
    """Build LLMConfig from environment variables."""
    return LLMConfig(
        provider=os.getenv("LLM_PROVIDER", "openai"),
        model=os.getenv("LLM_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o")),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=os.getenv("OPENAI_BASE_URL", ""),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
    )


def _decomposition_to_json(result: Any) -> str:
    """Convert a DecompositionResult to a formatted JSON string."""
    data = {
        "original_request": result.original_request,
        "summary": result.summary,
        "estimated_total_time": result.estimated_total_time,
        "sub_tasks": [
            {
                "title": st.title,
                "description": st.description,
                "files_to_modify": st.files_to_modify,
                "dependencies": st.dependencies,
                "complexity": st.estimated_complexity,
            }
            for st in result.sub_tasks
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def _task_result_to_dict(result: Any) -> dict[str, Any]:
    """Convert a TaskResult to a serializable dict."""
    return {
        "task_id": result.task_id,
        "success": result.success,
        "output": (result.output[:2000] + "...") if len(result.output) > 2000 else result.output,
        "error": result.error,
        "files_changed": result.files_changed,
        "duration_seconds": round(result.duration_seconds, 2),
    }


def _test_result_to_dict(result: Any) -> dict[str, Any]:
    """Convert a TestResult to a serializable dict."""
    return {
        "success": result.success,
        "output": (result.output[:3000] + "...") if len(result.output) > 3000 else result.output,
        "passed_count": result.passed_count,
        "failed_count": result.failed_count,
        "duration_seconds": round(result.duration_seconds, 2),
    }


def _build_agents(workdir: str) -> list[Any]:
    """Build available agent adapters based on CLI availability."""
    agents: list[Any] = []

    # Always try Codex
    if shutil.which("codex"):
        from codeswarm.agents.codex_adapter import CodexAgentAdapter
        agents.append(CodexAgentAdapter())

    # Try Claude Code
    if shutil.which("claude"):
        from codeswarm.agents.claude_code_adapter import ClaudeCodeAgentAdapter
        agents.append(ClaudeCodeAgentAdapter())

    # Fallback: if no agents found, still try codex (will fail gracefully)
    if not agents:
        from codeswarm.agents.codex_adapter import CodexAgentAdapter
        agents.append(CodexAgentAdapter())

    return agents


# -------------------------------------------------------------------
# MCP Tools
# -------------------------------------------------------------------


@mcp.tool()
def decompose_task(request: str, project_context: str = "") -> str:
    """Decompose a natural language request into structured sub-tasks.

    Uses an LLM to break down a high-level software requirement into
    actionable sub-tasks with dependencies and complexity estimates.

    Args:
        request: The natural language task description.
        project_context: Optional project context (e.g. file structure, tech stack).

    Returns:
        JSON string with the decomposition result including sub_tasks.
    """
    return asyncio.run(_decompose_task_async(request, project_context))


async def _decompose_task_async(request: str, project_context: str) -> str:
    from codeswarm.orchestrator.decomposer import LLMDecomposer

    config = _build_llm_config()
    decomposer = LLMDecomposer(config)

    try:
        result = await decomposer.decompose(request, project_context)
        return _decomposition_to_json(result)
    except Exception as exc:
        logger.error("decompose_task failed: %s", exc, exc_info=True)
        return json.dumps({"error": f"Decomposition failed: {exc}"}, ensure_ascii=False)


@mcp.tool()
def execute_tasks(tasks_json: str, workdir: str = ".", max_concurrent: int = 3) -> str:
    """Execute a set of decomposed tasks using AI coding agents.

    Takes the JSON output from decompose_task and runs each sub-task
    with available coding agents (Codex, Claude Code).

    Args:
        tasks_json: JSON string from decompose_task containing sub_tasks.
        workdir: Working directory for task execution (default: current dir).
        max_concurrent: Maximum concurrent agent tasks (default: 3).

    Returns:
        JSON string with execution results for each task.
    """
    return asyncio.run(_execute_tasks_async(tasks_json, workdir, max_concurrent))


async def _execute_tasks_async(tasks_json: str, workdir: str, max_concurrent: int) -> str:
    from codeswarm.core.types import DecompositionResult, SubTask
    from codeswarm.orchestrator.parallel_scheduler import ParallelScheduler

    # Parse the input JSON
    try:
        data = json.loads(tasks_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid tasks_json: {exc}"}, ensure_ascii=False)

    # Handle error from decompose_task
    if isinstance(data, dict) and "error" in data:
        return json.dumps(data, ensure_ascii=False)

    # Build DecompositionResult from JSON
    raw_tasks = data.get("sub_tasks", [])
    if not raw_tasks:
        return json.dumps({"error": "No sub_tasks found in input JSON"}, ensure_ascii=False)

    sub_tasks = []
    for item in raw_tasks:
        if isinstance(item, dict):
            sub_tasks.append(SubTask(
                title=item.get("title", "Untitled"),
                description=item.get("description", item.get("title", "")),
                files_to_modify=item.get("files_to_modify", []),
                dependencies=item.get("dependencies", []),
                estimated_complexity=item.get("complexity", item.get("estimated_complexity", "medium")),
            ))

    decomposition = DecompositionResult(
        original_request=data.get("original_request", ""),
        summary=data.get("summary", ""),
        estimated_total_time=data.get("estimated_total_time", "unknown"),
        sub_tasks=sub_tasks,
    )

    # Build agents and scheduler
    agents = _build_agents(workdir)
    scheduler = ParallelScheduler(agents, max_concurrent=max_concurrent)
    scheduler.submit_tasks(decomposition)

    # Set workdir in task metadata
    for task in scheduler._tasks:
        task.metadata["workdir"] = workdir

    try:
        results = await scheduler.run()
    except Exception as exc:
        logger.error("execute_tasks failed: %s", exc, exc_info=True)
        return json.dumps({"error": f"Execution failed: {exc}"}, ensure_ascii=False)

    # Build result JSON
    output = {
        "total": len(results),
        "succeeded": sum(1 for r in results if r.success),
        "failed": sum(1 for r in results if not r.success),
        "tasks": [],
    }

    for task, result in zip(scheduler._tasks, results, strict=False):
        task_info = {
            "title": task.title,
            "status": task.status.value,
            "agent": task.assigned_agent or "unknown",
        }
        task_info.update(_task_result_to_dict(result))
        output["tasks"].append(task_info)

    return json.dumps(output, indent=2, ensure_ascii=False)


@mcp.tool()
def auto_test_and_fix(
    workdir: str = ".",
    test_command: str = "python -m pytest",
    max_fix_attempts: int = 3,
) -> str:
    """Run tests in the working directory and return results.

    Executes the test command and returns parsed test results including
    pass/fail counts and output.

    Args:
        workdir: Directory to run tests in (default: current dir).
        test_command: Test command to execute (default: python -m pytest).
        max_fix_attempts: Maximum fix attempts (used when combined with agent fixing).

    Returns:
        JSON string with test results.
    """
    return asyncio.run(_auto_test_and_fix_async(workdir, test_command, max_fix_attempts))


async def _auto_test_and_fix_async(
    workdir: str, test_command: str, max_fix_attempts: int
) -> str:
    from codeswarm.orchestrator.auto_tester import AutoTester

    tester = AutoTester(
        test_command=test_command,
        max_fix_attempts=max_fix_attempts,
    )

    try:
        result = await tester.run_tests(workdir)
        return json.dumps(_test_result_to_dict(result), indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("auto_test_and_fix failed: %s", exc, exc_info=True)
        return json.dumps({"error": f"Test execution failed: {exc}"}, ensure_ascii=False)


@mcp.tool()
def full_pipeline(
    request: str,
    workdir: str = ".",
    max_concurrent: int = 3,
    test_command: str = "python -m pytest",
) -> str:
    """Run the complete CodeSwarm pipeline: decompose → execute → test.

    This is the main entry point for end-to-end task processing. It:
    1. Decomposes the natural language request into sub-tasks
    2. Executes all sub-tasks with available coding agents
    3. Runs tests to verify the results

    Args:
        request: Natural language task description.
        workdir: Working directory for execution (default: current dir).
        max_concurrent: Maximum concurrent agent tasks (default: 3).
        test_command: Test command to verify results (default: python -m pytest).

    Returns:
        JSON string with comprehensive pipeline results.
    """
    return asyncio.run(
        _full_pipeline_async(request, workdir, max_concurrent, test_command)
    )


async def _full_pipeline_async(
    request: str, workdir: str, max_concurrent: int, test_command: str
) -> str:
    from codeswarm.orchestrator.auto_tester import AutoTester
    from codeswarm.orchestrator.decomposer import LLMDecomposer
    from codeswarm.orchestrator.parallel_scheduler import ParallelScheduler

    pipeline_result: dict[str, Any] = {
        "request": request,
        "workdir": workdir,
        "stages": {},
    }

    # ── Stage 1: Decompose ──────────────────────────────────────────
    config = _build_llm_config()
    decomposer = LLMDecomposer(config)

    try:
        decomposition = await decomposer.decompose(request)
        pipeline_result["stages"]["decomposition"] = {
            "success": True,
            "summary": decomposition.summary,
            "estimated_total_time": decomposition.estimated_total_time,
            "sub_tasks_count": len(decomposition.sub_tasks),
            "sub_tasks": [
                {
                    "title": st.title,
                    "description": st.description,
                    "files_to_modify": st.files_to_modify,
                    "dependencies": st.dependencies,
                    "complexity": st.estimated_complexity,
                }
                for st in decomposition.sub_tasks
            ],
        }
    except Exception as exc:
        logger.error("Pipeline decomposition failed: %s", exc, exc_info=True)
        pipeline_result["stages"]["decomposition"] = {
            "success": False,
            "error": str(exc),
        }
        return json.dumps(pipeline_result, indent=2, ensure_ascii=False)

    # ── Stage 2: Execute ────────────────────────────────────────────
    agents = _build_agents(workdir)
    scheduler = ParallelScheduler(agents, max_concurrent=max_concurrent)
    scheduler.submit_tasks(decomposition)

    # Set workdir in task metadata
    for task in scheduler._tasks:
        task.metadata["workdir"] = workdir

    try:
        results = await scheduler.run()
        succeeded = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)

        pipeline_result["stages"]["execution"] = {
            "success": failed == 0,
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "tasks": [],
        }

        for task, result in zip(scheduler._tasks, results, strict=False):
            task_info = {
                "title": task.title,
                "status": task.status.value,
                "agent": task.assigned_agent or "unknown",
            }
            task_info.update(_task_result_to_dict(result))
            pipeline_result["stages"]["execution"]["tasks"].append(task_info)

    except Exception as exc:
        logger.error("Pipeline execution failed: %s", exc, exc_info=True)
        pipeline_result["stages"]["execution"] = {
            "success": False,
            "error": str(exc),
        }
        return json.dumps(pipeline_result, indent=2, ensure_ascii=False)

    # ── Stage 3: Test ───────────────────────────────────────────────
    tester = AutoTester(test_command=test_command)

    try:
        test_result = await tester.run_tests(workdir)
        pipeline_result["stages"]["testing"] = _test_result_to_dict(test_result)
        pipeline_result["stages"]["testing"]["success"] = test_result.success
    except Exception as exc:
        logger.error("Pipeline testing failed: %s", exc, exc_info=True)
        pipeline_result["stages"]["testing"] = {
            "success": False,
            "error": str(exc),
        }

    # ── Overall summary ─────────────────────────────────────────────
    decomp_ok = pipeline_result["stages"].get("decomposition", {}).get("success", False)
    exec_ok = pipeline_result["stages"].get("execution", {}).get("success", False)
    test_ok = pipeline_result["stages"].get("testing", {}).get("success", False)

    pipeline_result["overall_success"] = decomp_ok and exec_ok and test_ok
    pipeline_result["summary"] = (
        f"Decomposition: {'✅' if decomp_ok else '❌'} | "
        f"Execution: {'✅' if exec_ok else '❌'} | "
        f"Testing: {'✅' if test_ok else '❌'}"
    )

    return json.dumps(pipeline_result, indent=2, ensure_ascii=False)


# -------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------


def main() -> None:
    """Start the CodeSwarm MCP server (stdio transport)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.info("Starting CodeSwarm MCP server...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
