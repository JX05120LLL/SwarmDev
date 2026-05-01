"""CodeSwarm CLI - command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from codeswarm.core.config import CodeSwarmConfig
from codeswarm.core.types import (
    ChatMessage,
    MessageType,
    ProgressUpdate,
    TaskStatus,
)

logger = logging.getLogger("codeswarm")


async def _run_server(config: CodeSwarmConfig) -> None:
    """Run the CodeSwarm server: Telegram → decompose → schedule → reply."""
    from codeswarm.agents.codex_adapter import CodexAgentAdapter
    from codeswarm.channels.telegram_channel import TelegramChannel
    from codeswarm.orchestrator.decomposer import LLMDecomposer
    from codeswarm.orchestrator.parallel_scheduler import ParallelScheduler

    # Initialize components
    decomposer = LLMDecomposer(config.llm)
    agents = [CodexAgentAdapter(ac.name) for ac in config.agents if ac.agent_type == "codex" and ac.enabled]
    if not agents:
        agents = [CodexAgentAdapter("codex")]
    scheduler = ParallelScheduler(agents, max_concurrent=config.max_concurrent_agents)

    # Track active chat for progress updates
    active_chat: dict[str, str] = {}  # user_id -> chat_id
    channel_ref: TelegramChannel | None = None

    async def on_message(msg: ChatMessage) -> None:
        """Handle incoming user message: decompose → schedule → reply."""
        nonlocal channel_ref
        chat_id = msg.chat_id
        active_chat[msg.user_id] = chat_id

        logger.info("Received from %s: %s", msg.user_id, msg.text[:80])

        # Send acknowledgment
        if channel_ref:
            await channel_ref.send_message(chat_id, "🔄 收到！正在拆解任务...")

        # Step 1: Decompose
        try:
            result = await decomposer.decompose(msg.text)
        except Exception as exc:
            logger.error("Decomposition failed: %s", exc)
            if channel_ref:
                await channel_ref.send_message(chat_id, f"❌ 任务拆解失败: {exc}")
            return

        if not result.sub_tasks:
            if channel_ref:
                await channel_ref.send_message(chat_id, "⚠️ 没有拆解出子任务，请重新描述需求。")
            return

        # Notify user about sub-tasks
        task_list = "\n".join(
            f"  {i+1}. {st.title}" for i, st in enumerate(result.sub_tasks)
        )
        if channel_ref:
            await channel_ref.send_message(
                chat_id,
                f"📋 已拆解为 {len(result.sub_tasks)} 个子任务:\n{task_list}\n\n⏳ 开始执行...",
            )

        # Step 2: Submit and run
        scheduler.submit_tasks(result)

        # Send progress updates periodically
        async def progress_reporter() -> None:
            while not scheduler._is_finished():
                await asyncio.sleep(15)
                prog = scheduler.get_progress()
                if channel_ref and prog.tasks_status:
                    lines = []
                    for ts in prog.tasks_status:
                        icon = {"completed": "✅", "running": "🔄", "failed": "❌", "pending": "⏳"}.get(
                            ts.get("status", ""), "❓"
                        )
                        lines.append(f"  {icon} {ts.get('title', '?')}")
                    await channel_ref.send_message(
                        chat_id, f"📊 进度 ({prog.overall_progress:.0%}):\n" + "\n".join(lines)
                    )

        reporter_task = asyncio.create_task(progress_reporter())

        try:
            results = await scheduler.run()
        finally:
            reporter_task.cancel()
            try:
                await reporter_task
            except asyncio.CancelledError:
                pass

        # Step 3: Report results
        succeeded = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        total = len(results)

        summary_parts = [f"✅ 完成 {succeeded}/{total} 个任务"]
        if failed:
            summary_parts.append(f"❌ 失败 {failed} 个")
            for r in results:
                if not r.success:
                    summary_parts.append(f"  - {r.error or 'unknown error'}")

        if channel_ref:
            await channel_ref.send_message(chat_id, "\n".join(summary_parts))

        logger.info("Task batch done: %d/%d succeeded", succeeded, total)

    # Start Telegram channel
    channel = TelegramChannel(config.telegram.bot_token, on_message)
    channel_ref = channel

    logger.info("CodeSwarm server starting...")
    logger.info("Telegram bot: %s", "configured" if config.telegram.bot_token else "NOT configured")
    logger.info("LLM: %s/%s", config.llm.provider, config.llm.model)
    logger.info("Agents: %s", ", ".join(a.name for a in agents))

    # Handle graceful shutdown
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Run
    await channel.start()
    logger.info("Server is running. Send a message to your Telegram bot to get started!")

    try:
        await stop_event.wait()
    finally:
        await channel.stop()
        logger.info("Server stopped.")


async def _run_single_task(config: CodeSwarmConfig, request: str) -> None:
    """Run a single task from the command line (no Telegram needed)."""
    from codeswarm.agents.codex_adapter import CodexAgentAdapter
    from codeswarm.orchestrator.decomposer import LLMDecomposer
    from codeswarm.orchestrator.parallel_scheduler import ParallelScheduler

    decomposer = LLMDecomposer(config.llm)
    agents = [CodexAgentAdapter(ac.name) for ac in config.agents if ac.agent_type == "codex" and ac.enabled]
    if not agents:
        agents = [CodexAgentAdapter("codex")]
    scheduler = ParallelScheduler(agents, max_concurrent=config.max_concurrent_agents)

    print(f"🔄 正在拆解任务: {request}")

    # Step 1: Decompose
    try:
        result = await decomposer.decompose(request)
    except Exception as exc:
        print(f"❌ 任务拆解失败: {exc}")
        return

    if not result.sub_tasks:
        print("⚠️  没有拆解出子任务，请重新描述需求。")
        return

    print(f"\n📋 已拆解为 {len(result.sub_tasks)} 个子任务:")
    for i, st in enumerate(result.sub_tasks):
        deps = f" (依赖: {st.dependencies})" if st.dependencies else ""
        print(f"  {i+1}. {st.title} [{st.estimated_complexity}]{deps}")

    # Step 2: Submit
    scheduler.submit_tasks(result)

    # Set up progress callback
    def on_progress(update: ProgressUpdate) -> None:
        for ts in update.tasks_status:
            status = ts.get("status", "")
            icon = {"completed": "✅", "running": "🔄", "failed": "❌", "cancelled": "🚫", "pending": "⏳", "ready": "⏳"}.get(status, "❓")
            print(f"  {icon} {ts.get('title', '?')}")

    scheduler.set_progress_callback(on_progress)

    print(f"\n⏳ 开始执行 (最多 {config.max_concurrent_agents} 个并行)...\n")

    # Step 3: Run
    results = await scheduler.run()

    # Step 4: Report
    succeeded = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    total = len(results)

    print(f"\n{'='*40}")
    print(f"✅ 完成 {succeeded}/{total} 个任务")
    if failed:
        print(f"❌ 失败 {failed} 个")
        for r in results:
            if not r.success:
                print(f"  - {r.error or 'unknown error'}")

    print(f"\n📊 详情:")
    for task in scheduler._tasks:
        icon = {"completed": "✅", "failed": "❌", "cancelled": "🚫"}.get(task.status.value, "❓")
        agent = f" [{task.assigned_agent}]" if task.assigned_agent else ""
        print(f"  {icon} {task.title}{agent}")


def cmd_run(args: argparse.Namespace) -> None:
    """Run a single task from the command line."""
    config_path = Path(args.config)
    config = CodeSwarmConfig.load(config_path)

    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    asyncio.run(_run_single_task(config, args.request))


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the CodeSwarm server."""
    config_path = Path(args.config)
    config = CodeSwarmConfig.load(config_path)

    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    asyncio.run(_run_server(config))


def cmd_init(args: argparse.Namespace) -> None:
    """Generate a sample config file."""
    config_path = Path(args.output)
    if config_path.exists() and not args.force:
        print(f"❌ {config_path} already exists. Use --force to overwrite.")
        sys.exit(1)

    config = CodeSwarmConfig()
    config.save(config_path)
    print(f"✅ Created {config_path}")
    print("   Edit it with your Telegram bot token, LLM API key, and agent settings.")
    print("   Then run: codeswarm serve")


def cmd_status(args: argparse.Namespace) -> None:
    """Check component status."""
    import shutil

    config_path = Path(args.config)
    config = CodeSwarmConfig.load(config_path)

    print("CodeSwarm Status")
    print("=" * 40)

    # Telegram
    tg_ok = bool(config.telegram.bot_token)
    print(f"Telegram Bot:  {'✅ configured' if tg_ok else '❌ not configured'}")

    # LLM
    llm_ok = bool(config.llm.api_key)
    print(f"LLM ({config.llm.model}): {'✅ configured' if llm_ok else '❌ not configured'}")

    # Agents
    codex_ok = shutil.which("codex") is not None
    print(f"Codex CLI:     {'✅ found' if codex_ok else '❌ not found'}")

    for ac in config.agents:
        print(f"  Agent '{ac.name}' ({ac.agent_type}): {'✅ enabled' if ac.enabled else '⏸ disabled'}")

    print()
    if tg_ok and llm_ok and codex_ok:
        print("🚀 All components ready! Run: codeswarm serve")
    else:
        print("⚠️  Some components are missing. Fix the issues above.")


async def _run_chat(config: CodeSwarmConfig) -> None:
    """Interactive chat mode: type requirements, get results."""
    from codeswarm.agents.codex_adapter import CodexAgentAdapter
    from codeswarm.orchestrator.decomposer import LLMDecomposer
    from codeswarm.orchestrator.parallel_scheduler import ParallelScheduler

    decomposer = LLMDecomposer(config.llm)
    agents = [CodexAgentAdapter(ac.name) for ac in config.agents if ac.agent_type == "codex" and ac.enabled]
    if not agents:
        agents = [CodexAgentAdapter("codex")]

    print("🤖 CodeSwarm 已启动，输入需求开始对话 (输入 quit 退出)")
    print(f"   LLM: {config.llm.provider}/{config.llm.model}")
    print(f"   Agent: {', '.join(a.info.name for a in agents)}")
    print(f"   最大并行: {config.max_concurrent_agents}")
    print()

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("👋 再见！")
            break

        # Step 1: Decompose
        print("\n🔄 正在拆解任务...")
        try:
            result = await decomposer.decompose(user_input)
        except Exception as exc:
            print(f"❌ 任务拆解失败: {exc}\n")
            continue

        if not result.sub_tasks:
            print("⚠️  没有拆解出子任务，请重新描述需求。\n")
            continue

        # Show sub-tasks
        print(f"\n📋 已拆解为 {len(result.sub_tasks)} 个子任务:")
        for i, st in enumerate(result.sub_tasks):
            deps = f" (依赖: {st.dependencies})" if st.dependencies else ""
            print(f"  {i+1}. {st.title} [{st.estimated_complexity}]{deps}")

        # Ask for confirmation
        try:
            confirm = input("\n要开始执行吗？(y/n) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if confirm not in ("y", "yes", ""):
            print("⏭️  跳过。\n")
            continue

        # Step 2: Submit and run
        scheduler = ParallelScheduler(agents, max_concurrent=config.max_concurrent_agents)
        scheduler.submit_tasks(result)

        progress_lines: list[str] = []

        def on_progress(update: ProgressUpdate) -> None:
            # Clear and redraw progress
            if progress_lines:
                print(f"\033[{len(progress_lines)}A", end="")  # move cursor up
            progress_lines.clear()
            for ts in update.tasks_status:
                status = ts.get("status", "")
                icon = {"completed": "✅", "running": "🔄", "failed": "❌", "cancelled": "🚫", "pending": "⏳", "ready": "⏳"}.get(status, "❓")
                line = f"  {icon} {ts.get('title', '?')}"
                progress_lines.append(line)
                print(f"\033[2K{line}")  # clear line and print

        scheduler.set_progress_callback(on_progress)

        print(f"\n⏳ 执行中 (最多 {config.max_concurrent_agents} 个并行)...\n")
        # Print initial progress lines
        for _ in scheduler._tasks:
            print("  ⏳ 等待中...")

        results = await scheduler.run()

        # Step 3: Report
        succeeded = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        total = len(results)

        print(f"\n{'='*40}")
        if failed:
            print(f"✅ 完成 {succeeded}/{total}，❌ 失败 {failed}")
            for r in results:
                if not r.success:
                    print(f"  - {r.error or 'unknown error'}")
        else:
            print(f"✅ 全部完成！{succeeded}/{total} 个任务")
        print()


def cmd_mcp_server(args: argparse.Namespace) -> None:
    """Start the MCP server (stdio transport)."""
    from codeswarm.mcp_server import main
    main()


def cmd_chat(args: argparse.Namespace) -> None:
    """Start interactive chat mode."""
    config_path = Path(args.config)
    config = CodeSwarmConfig.load(config_path)

    log_level = getattr(logging, config.log_level.upper(), logging.WARNING)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    asyncio.run(_run_chat(config))


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="codeswarm",
        description="CodeSwarm — Chat-driven multi-agent collaboration development platform",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run
    p_run = subparsers.add_parser("run", help="Run a single task from the command line")
    p_run.add_argument("request", help="Task description in natural language")
    p_run.add_argument("-c", "--config", default="codeswarm.yaml", help="Config file path")
    p_run.set_defaults(func=cmd_run)

    # chat
    p_chat = subparsers.add_parser("chat", help="Start interactive chat mode")
    p_chat.add_argument("-c", "--config", default="codeswarm.yaml", help="Config file path")
    p_chat.set_defaults(func=cmd_chat)

    # serve
    p_serve = subparsers.add_parser("serve", help="Start the CodeSwarm server")
    p_serve.add_argument("-c", "--config", default="codeswarm.yaml", help="Config file path")
    p_serve.set_defaults(func=cmd_serve)

    # init
    p_init = subparsers.add_parser("init", help="Generate a sample config file")
    p_init.add_argument("-o", "--output", default="codeswarm.yaml", help="Output file path")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing file")
    p_init.set_defaults(func=cmd_init)

    # status
    p_status = subparsers.add_parser("status", help="Check component status")
    p_status.add_argument("-c", "--config", default="codeswarm.yaml", help="Config file path")
    p_status.set_defaults(func=cmd_status)

    # mcp-server
    p_mcp = subparsers.add_parser(
        "mcp-server", help="Start the MCP server (stdio transport)"
    )
    p_mcp.set_defaults(func=cmd_mcp_server)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
