# SwarmDev

Chat-driven multi-agent collaboration development platform.

SwarmDev lets you describe software tasks in natural language via chat (Telegram, etc.), automatically decomposes them into sub-tasks, schedules them across AI coding agents (Codex, Claude Code, etc.), and reports progress back to you.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐
│   Telegram   │────▶│  Decomposer  │────▶│   Scheduler   │
│   Channel    │     │   (LLM)      │     │  (Serial)     │
└─────────────┘     └──────────────┘     └───────┬───────┘
                                                  │
                                          ┌───────▼───────┐
                                          │  Agent Pool   │
                                          │  ┌─────────┐  │
                                          │  │  Codex   │  │
                                          │  └─────────┘  │
                                          └───────────────┘
```

### Modules

| Module | File | Lines | Description |
|--------|------|-------|-------------|
| **Telegram Channel** | `swarmdev/channels/telegram_channel.py` | 180 | Telegram bot adapter — receives messages, sends progress updates |
| **Task Decomposer** | `swarmdev/orchestrator/decomposer.py` | 279 | LLM-backed engine that breaks requirements into structured sub-tasks |
| **Task Scheduler** | `swarmdev/orchestrator/scheduler.py` | 178 | Serial scheduler with dependency resolution, retries, and failure propagation |
| **Codex Adapter** | `swarmdev/agents/codex_adapter.py` | 76 | Adapter for executing tasks via the Codex CLI |

### Core Types

All modules share a common type system defined in `swarmdev/core/types.py`:

- `Task` / `SubTask` / `TaskResult` — work units and their outcomes
- `DecompositionResult` — output of the decomposer
- `ProgressUpdate` — status updates sent to the user
- `ChatMessage` — messages from/to chat channels
- `ChannelAdapter` / `AgentAdapter` / `TaskDecomposer` — Protocol interfaces

## Setup

```bash
# Install
pip install -e ".[dev]"

# Configure
cp swarmdev.example.yaml swarmdev.yaml
# Edit swarmdev.yaml with your tokens
```

### Configuration

```yaml
telegram:
  bot_token: "your-bot-token"      # or set TELEGRAM_BOT_TOKEN env var
  allowed_users: []                 # empty = allow all

llm:
  provider: "openai"
  model: "gpt-4o"
  api_key: "sk-..."                # or set OPENAI_API_KEY env var
  base_url: ""                     # custom endpoint (optional)
  temperature: 0.3

agents:
  - name: "codex"
    type: "codex"
    enabled: true

project:
  name: "my-project"
  root_dir: "."

max_concurrent_agents: 3
task_timeout: 600
log_level: "INFO"
```

## Usage

```bash
# Start the bot
swarmdev

# Or run programmatically
python -m swarmdev.cli
```

Then message your Telegram bot:

> Add a login page with email/password auth and a JWT token refresh endpoint

The decomposer will break this into sub-tasks, the scheduler will dispatch them to available agents, and you'll get progress updates in the chat.

## Testing

```bash
pytest tests/ -v
```

59 tests covering:
- **Decomposer** — happy path, edge cases (empty/garbage/fenced JSON), dependency normalization, retry/fallback, field coercion
- **Scheduler** — basic scheduling, dependency ordering, failure/retry, dependency cancellation, agent rotation, progress reporting
- **Telegram** — message splitting, progress formatting, message handling, error handling
- **Codex adapter** — health check, execution success/failure, timeout, CLI not found

## Project Status

**v0.1.0** — Core pipeline working.

### Known Limitations

- **Serial execution only** — The scheduler processes one task at a time. Concurrent execution is planned but not yet implemented.
- **Single agent type** — Only Codex adapter is implemented. Claude Code and OpenClaw adapters are planned.
- **No authentication** — Telegram `allowed_users` config exists but is not enforced yet.

## License

MIT
