# CodeSwarm

**多智能体并行代码生成，基于 MCP 协议编排。**

[English](README.md)

---

## CodeSwarm 是什么？

CodeSwarm 是一个 MCP 服务器，可以编排多个 AI 编码智能体并行工作。将它连接到任何兼容 MCP 的工具（Cursor、Claude Desktop、Hermes Agent 等），用自然语言描述你的开发需求，CodeSwarm 会自动将需求拆解为子任务、分配给多个智能体（Codex、Claude Code）并行执行、运行测试并返回结果。

## 工作原理

```
你（自然语言描述需求）
  │
  ▼
┌─────────────────────────────────────────┐
│             CodeSwarm MCP               │
│                                         │
│  1. 拆解需求 → 结构化子任务             │
│  2. 构建依赖 DAG                        │
│  3. 并行调度子任务                       │
│  4. 分发给多个智能体并发执行             │
│  5. 自动测试，失败自动重试               │
│  6. 返回结果                            │
│                                         │
│         ┌───────────┬───────────┐       │
│         ▼           ▼           ▼       │
│     ┌───────┐  ┌─────────┐  ┌─────┐   │
│     │ Codex │  │ Claude  │  │ ... │   │
│     │       │  │  Code   │  │     │   │
│     └───────┘  └─────────┘  └─────┘   │
└─────────────────────────────────────────┘
```

**流程：** 拆解 → 调度（DAG）→ 并行执行 → 测试 → 重试 → 完成。

---

## 快速开始

### 1. 安装

```bash
pip install codeswarm
```

### 2. 配置 MCP 客户端

#### Cursor

在 `.cursor/mcp.json` 中添加：

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

在 `claude_desktop_config.json` 中添加：

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

在 Hermes 配置中添加：

```yaml
mcp_servers:
  codeswarm:
    command: codeswarm
    args: [mcp-server]
    env:
      OPENAI_API_KEY: sk-xxx
      CODEX_API_KEY: sk-xxx
```

### 3. 开始使用

连接后，直接在对话中描述你的需求：

> "给我的 Express API 加上 JWT 用户认证，包括注册、登录和路由保护中间件。"

CodeSwarm 会自动：
1. **拆解**需求为结构化子任务
2. **调度**独立任务并行执行
3. **执行**通过 Codex / Claude Code 智能体
4. **测试**自动运行测试，失败自动重试
5. **返回**汇总结果

---

## MCP 工具

CodeSwarm 通过 MCP 协议暴露四个工具：

| 工具 | 说明 |
|------|------|
| `decompose_task` | 将自然语言需求拆解为带依赖关系的结构化子任务 |
| `execute_tasks` | 在多个智能体间并行执行子任务（遵循依赖 DAG） |
| `auto_test_and_fix` | 对生成的代码运行测试，失败时自动修复并重试 |
| `full_pipeline` | 端到端：拆解 → 执行 → 测试 → 修复，一次调用完成全部流程 |

### `full_pipeline`（推荐）

最简单的使用方式——描述需求即可获得结果：

```json
{
  "tool": "full_pipeline",
  "arguments": {
    "requirement": "为我的 Todo 应用添加 REST API，支持增删改查",
    "project_dir": "/path/to/your/project",
    "test_command": "python -m pytest"
  }
}
```

### `decompose_task`

仅拆解，不执行：

```json
{
  "tool": "decompose_task",
  "arguments": {
    "requirement": "给 Express API 添加 JWT 用户认证"
  }
}
```

### `execute_tasks`

执行之前拆解的任务：

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

运行测试并自动修复：

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

## 配置

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | 用于任务拆解的 LLM API 密钥 | — |
| `OPENAI_BASE_URL` | 自定义 API 端点（用于 OpenAI 兼容的第三方服务） | — |
| `LLM_MODEL` | 用于任务拆解的模型名称 | `gpt-4o` |
| `CODEX_API_KEY` | Codex 智能体的 API 密钥 | — |
| `CODEX_MODEL` | Codex 智能体使用的模型 | — |
| `CODESWARM_CONFIG` | 配置文件路径 | `codeswarm.yaml` |

### 配置文件

生成默认配置：

```bash
codeswarm init
```

会创建 `codeswarm.yaml`：

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

## CLI 命令

```bash
# 生成示例配置文件
codeswarm init

# 交互式对话模式（输入需求，获取结果）
codeswarm chat

# 从命令行执行单个任务
codeswarm run "为 API 路由添加错误处理"

# 启动 Telegram 机器人服务器
codeswarm serve

# 启动 MCP 服务器（用于 MCP 客户端连接）
codeswarm mcp-server

# 检查组件状态
codeswarm status
```

### 使用示例

```bash
# 快速单次任务
codeswarm run "创建一个 Python CLI 工具，将 CSV 转换为 JSON"

# 使用自定义配置
codeswarm run -c my-config.yaml "为认证模块添加单元测试"

# 交互模式
codeswarm chat
> 给我的 Express API 添加限流中间件
> 📋 已拆解为 3 个子任务:
>   1. 创建限流工具函数 [medium]
>   2. 添加中间件集成 [low] (依赖: [0])
>   3. 添加限流测试 [medium] (依赖: [0])
> 要开始执行吗？(y/n) y
```

---

## 架构

```
┌──────────────────────┐
│     MCP 客户端        │
│  (Cursor / Claude /   │
│   Hermes Agent)       │
└──────────┬───────────┘
           │ MCP 协议
           ▼
┌──────────────────────────────────────────────┐
│                CodeSwarm                      │
│                                               │
│  ┌─────────────┐  ┌───────────────────────┐  │
│  │  LLM         │  │  编排器                │  │
│  │  拆解器      │──▶│  并行调度器            │  │
│  │  (GPT-4o)    │  │  (基于 DAG)            │  │
│  └─────────────┘  └───────────┬───────────┘  │
│                               │               │
│              ┌────────────────┼───────────┐   │
│              ▼                ▼            ▼   │
│         ┌────────┐    ┌──────────┐   ┌──────┐ │
│         │ Codex  │    │ Claude   │   │ 自定义│ │
│         │ 适配器 │    │ Code     │   │ 智能体│ │
│         └────────┘    │ 适配器   │   └──────┘ │
│                       └──────────┘            │
│                                               │
│  ┌──────────────────────────────────────────┐ │
│  │  自动测试器（运行测试 → 修复 → 重试）    │ │
│  └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

**核心组件：**

- **LLM 拆解器** — 使用 GPT-4o（或兼容模型）将需求拆解为带依赖关系的结构化子任务
- **并行调度器** — 基于 DAG 的调度器，独立任务并发执行，并行度可配置
- **智能体适配器** — 可插拔的编码智能体适配器（Codex、Claude Code 或自定义）
- **自动测试器** — 代码生成后自动运行测试，失败时自动反馈给智能体修复

---

## 项目结构

```
codeswarm/
├── __init__.py
├── cli.py                          # CLI 入口
├── core/
│   ├── __init__.py
│   ├── config.py                   # 配置管理
│   └── types.py                    # 核心类型定义与协议
├── agents/
│   ├── __init__.py
│   ├── codex_adapter.py            # Codex CLI 智能体适配器
│   └── claude_code_adapter.py      # Claude Code CLI 智能体适配器
├── orchestrator/
│   ├── __init__.py
│   ├── scheduler.py                # 基础任务调度器
│   ├── parallel_scheduler.py       # 基于 DAG 的并行调度器
│   ├── decomposer.py               # LLM 驱动的任务拆解器
│   └── auto_tester.py              # 自动测试与重试逻辑
├── channels/
│   ├── __init__.py
│   └── telegram_channel.py         # Telegram 机器人通道
codeswarm.example.yaml              # 示例配置文件
pyproject.toml                      # 项目元数据与依赖
```

---

## 测试

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 运行并查看覆盖率
pytest --cov=codeswarm

# 代码检查
ruff check .
```

---

## 路线图

- [x] LLM 驱动的任务拆解
- [x] 基于 DAG 的并行调度
- [x] Codex 智能体适配器
- [x] Claude Code 智能体适配器
- [x] 自动测试与重试
- [x] MCP 服务器集成
- [ ] 流式进度更新
- [ ] Web UI 仪表盘
- [ ] 更多智能体适配器（Aider、Continue 等）
- [ ] 任务依赖可视化
- [ ] 项目感知上下文注入
- [ ] Git 集成（每个任务自动创建分支）
- [ ] 成本追踪与预算限制

---

## 许可证

MIT

---

> **CodeSwarm** — 让智能体并行工作，更快交付。
