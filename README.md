# CodeSwarm

**Multi-agent parallel code generation, orchestrated via MCP.**

[中文文档](README_CN.md)

---

## What is CodeSwarm?

CodeSwarm is an MCP Server that orchestrates multiple AI coding agents to work on your project in parallel. Connect it to any MCP-compatible tool (Cursor, Claude Desktop, Hermes Agent, etc.), describe what you need in plain language, and CodeSwarm will decompose the work into sub-tasks, dispatch them to agents (Codex, Claude Code), run tests, and return results — all automatically.

## How It Works

```
You (natural language)
  │
  ▼
┌─────────────────────────────────────────┐
│              CodeSwarm MCP              │
│                                         │
│  1. Decompose requirement → sub-tasks   │
│  2. Build dependency DAG                │
│  3. Schedule tasks in parallel          │
│  4. Dispatch to agents concurrently     │
│  5. Auto-test and retry on failure      │
│  6. Return results                      │
│                                         │
│         ┌───────────┬───────────┐       │
│         ▼           ▼           ▼       │
│     ┌───────┐  ┌─────────┐  ┌─────┐   │
│     │ Codex │  │ Claude  │  │ ... │   │
│     │       │  │  Code   │  │     │   │
│     └───────┘  └─────────┘  └─────┘   │
└─────────────────────────────────────────┘
```

**Pipeline:** Decompose → Schedule (DAG) → Execute (parallel) → Test → Retry → Done.

---

## Quick Start

### 1. Install

```bash
pip install codeswarm
```

### 2. Configure your MCP client

#### Cursor

Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "codeswarm": {
      "command": "codeswarm",
      "args": ["mcp-server"],
      "env": {
        "OPENAI_API_KEY": "sk-xxx",
        "CODEX_API_KEY": "sk-xxx"
      }
    }
  }
}
```

#### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "codeswarm": {
      "command": "codeswarm",
      "args": ["mcp-server"],
      "env": {
        "OPENAI_API_KEY": "sk-xxx",
        "CODEX_API_KEY": "sk-xxx"
      }
    }
  }
}
```

#### Hermes Agent

Add to your Hermes config:

```yaml
mcp_servers:
  codeswarm:
    command: codeswarm
    args: [mcp-server]
    env:
      OPENAI_API_KEY: sk-xxx
      CODEX_API_KEY: sk-xxx
```

### 3. Start using

Once connected, just describe what you need in the chat:

> "Add user authentication with JWT to my Express API, including signup, login, and middleware for protected routes."

CodeSwarm will:
1. **Decompose** the request into structured sub-tasks
2. **Schedule** independent tasks to run in parallel
3. **Execute** via Codex / Claude Code agents
4. **Test** automatically and retry on failure
5. **Return** a summary with all results

---

## MCP Tools

CodeSwarm exposes four tools via the MCP protocol:

| Tool | Description |
|------|-------------|
| `decompose_task` | Break a natural language requirement into structured sub-tasks with dependencies |
| `execute_tasks` | Run sub-tasks across multiple agents in parallel (respects dependency DAG) |
| `auto_test_and_fix` | Run tests on generated code, auto-fix failures with retry |
| `full_pipeline` | End-to-end: decompose → execute → test → fix. One call does everything |

### `full_pipeline` (recommended)

The simplest way to use CodeSwarm — describe your requirement and get results:

```json
{
  "tool": "full_pipeline",
  "arguments": {
    "requirement": "Add a REST API for managing todo items with CRUD operations",
    "project_dir": "/path/to/your/project",
    "test_command": "python -m pytest"
  }
}
```

### `decompose_task`

Just decompose, don't execute:

```json
{
  "tool": "decompose_task",
  "arguments": {
    "requirement": "Add user auth with JWT to my Express API"
  }
}
```

### `execute_tasks`

Execute previously decomposed tasks:

```json
{
  "tool": "execute_tasks",
  "arguments": {
    "tasks": [...],
    "project_dir": "/path/to/project"
  }
}
```

### `auto_test_and_fix`

Run tests and auto-fix:

```json
{
  "tool": "auto_test_and_fix",
  "arguments": {
    "project_dir": "/path/to/project",
    "test_command": "python -m pytest",
    "max_fix_attempts": 3
  }
}
```

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | API key for the LLM used in task decomposition | — |
| `OPENAI_BASE_URL` | Custom API endpoint (for OpenAI-compatible providers) | — |
| `LLM_MODEL` | Model name for task decomposition | `gpt-4o` |
| `CODEX_API_KEY` | API key for the Codex agent | — |
| `CODEX_MODEL` | Model for the Codex agent | — |
| `CODESWARM_CONFIG` | Path to config file | `codeswarm.yaml` |

### Config File

Generate a default config:

```bash
codeswarm init
```

This creates `codeswarm.yaml`:

```yaml
telegram:
  bot_token: ""
  allowed_users: []

llm:
  provider: "openai"
  model: "gpt-4o"
  api_key: ""
  base_url: ""
  temperature: 0.3

agents:
  - name: "codex"
    type: "codex"
    enabled: true

project:
  name: "my-project"
  root_dir: "."
  git_repo: ""

max_concurrent_agents: 3
task_timeout: 600
log_level: "INFO"
```

---

## CLI Commands

```bash
# Generate a sample config file
codeswarm init

# Interactive chat mode (type requirements, get results)
codeswarm chat

# Run a single task from the command line
codeswarm run "Add error handling to the API routes"

# Start the Telegram bot server
codeswarm serve

# Start the MCP server (for use with MCP clients)
codeswarm mcp-server

# Check component status
codeswarm status
```

### Examples

```bash
# Quick one-shot task
codeswarm run "Create a Python CLI tool that converts CSV to JSON"

# With a custom config
codeswarm run -c my-config.yaml "Add unit tests for the auth module"

# Interactive mode
codeswarm chat
> Add a rate limiter middleware to my Express API
> 📋 Decomposed into 3 sub-tasks:
>   1. Create rate limiter utility [medium]
>   2. Add middleware integration [low] (deps: [0])
>   3. Add tests for rate limiter [medium] (deps: [0])
> Execute? (y/n) y
```

---

## Architecture

```
┌──────────────────────┐
│    MCP Client         │
│  (Cursor / Claude /   │
│   Hermes Agent)       │
└──────────┬───────────┘
           │ MCP Protocol
           ▼
┌──────────────────────────────────────────────┐
│                CodeSwarm                      │
│                                               │
│  ┌─────────────┐  ┌───────────────────────┐  │
│  │  LLM         │  │  Orchestrator          │  │
│  │  Decomposer  │──▶│  Parallel Scheduler    │  │
│  │  (GPT-4o)    │  │  (DAG-based)           │  │
│  └─────────────┘  └───────────┬───────────┘  │
│                               │               │
│              ┌────────────────┼───────────┐   │
│              ▼                ▼            ▼   │
│         ┌────────┐    ┌──────────┐   ┌──────┐ │
│         │ Codex  │    │ Claude   │   │ Custom│ │
│         │ Adapter│    │ Code     │   │ Agent │ │
│         └────────┘    │ Adapter  │   └──────┘ │
│                       └──────────┘            │
│                                               │
│  ┌──────────────────────────────────────────┐ │
│  │  AutoTester (run tests → fix → retry)    │ │
│  └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

**Key components:**

- **LLM Decomposer** — Uses GPT-4o (or compatible) to break requirements into structured sub-tasks with dependency relationships
- **Parallel Scheduler** — DAG-based scheduler that runs independent tasks concurrently with configurable parallelism
- **Agent Adapters** — Pluggable adapters for coding agents (Codex, Claude Code, or custom)
- **AutoTester** — Runs tests after code generation, automatically feeds failures back to agents for fixing

---

## Project Structure

```
codeswarm/
├── __init__.py
├── cli.py                          # CLI entry point
├── core/
│   ├── __init__.py
│   ├── config.py                   # Configuration management
│   └── types.py                    # Core type definitions & protocols
├── agents/
│   ├── __init__.py
│   ├── codex_adapter.py            # Codex CLI agent adapter
│   └── claude_code_adapter.py      # Claude Code CLI agent adapter
├── orchestrator/
│   ├── __init__.py
│   ├── scheduler.py                # Base task scheduler
│   ├── parallel_scheduler.py       # DAG-based parallel scheduler
│   ├── decomposer.py               # LLM-powered task decomposer
│   └── auto_tester.py              # Auto test & retry logic
├── channels/
│   ├── __init__.py
│   └── telegram_channel.py         # Telegram bot channel
codeswarm.example.yaml              # Example config file
pyproject.toml                      # Project metadata & dependencies
```

---

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=codeswarm

# Lint
ruff check .
```

---

## Roadmap

- [x] LLM-powered task decomposition
- [x] DAG-based parallel scheduling
- [x] Codex agent adapter
- [x] Claude Code agent adapter
- [x] Auto-test with retry
- [x] MCP Server integration
- [ ] Streaming progress updates
- [ ] Web UI dashboard
- [ ] More agent adapters (Aider, Continue, etc.)
- [ ] Task dependency visualization
- [ ] Project-aware context injection
- [ ] Git integration (auto-branch per task)
- [ ] Cost tracking and budget limits

---

## License

MIT

---

> **CodeSwarm** — Let agents work in parallel. Ship faster.
