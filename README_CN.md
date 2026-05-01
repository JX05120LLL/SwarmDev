# SwarmDev

[English](README.md) | 中文

聊天驱动的多 Agent 协作开发平台。

通过 Telegram、微信、飞书等即时通讯工具下达开发需求，SwarmDev 自动将需求拆解为子任务，分配给多个 AI 编程 Agent（Codex、Claude Code 等）并行执行，并实时汇报进度。

## 架构

```
┌─────────────────┐     ┌──────────────┐     ┌───────────────┐
│   Telegram /    │────▶│  Task        │────▶│   Scheduler   │
│   WeChat / ...  │     │  Decomposer  │     │  (Serial →    │
└─────────────────┘     │  (LLM)       │     │   Parallel)   │
                        └──────────────┘     └───────┬───────┘
                                                     │
                                             ┌───────▼───────┐
                                             │  Agent Pool   │
                                             │  ┌────┬────┐  │
                                             │  │Codex│Claude│  │
                                             │  └────┴────┘  │
                                             └───────────────┘
```

### 核心模块

| 模块 | 文件 | 说明 |
|------|------|------|
| **Telegram 通道** | `swarmdev/channels/telegram_channel.py` | Telegram Bot 适配器，接收消息、发送进度更新 |
| **任务拆解器** | `swarmdev/orchestrator/decomposer.py` | 基于 LLM 的需求拆解引擎，将自然语言需求分解为结构化子任务 |
| **任务调度器** | `swarmdev/orchestrator/scheduler.py` | 串行调度器（支持依赖解析、失败重试、级联取消） |
| **Codex 适配器** | `swarmdev/agents/codex_adapter.py` | 通过 Codex CLI 执行编程任务的适配器 |
| **核心类型** | `swarmdev/core/types.py` | 全模块共享的类型系统：Task、SubTask、TaskResult 等 |
| **配置管理** | `swarmdev/core/config.py` | YAML 配置加载、环境变量读取 |

### 核心类型

所有模块共享 `swarmdev/core/types.py` 中定义的类型系统：

- `Task` / `SubTask` / `TaskResult` — 任务单元及其结果
- `DecompositionResult` — 任务拆解器的输出
- `ProgressUpdate` — 发送给用户的状态更新
- `ChatMessage` — 聊天通道的消息
- `ChannelAdapter` / `AgentAdapter` / `TaskDecomposer` — 协议接口

## 快速开始

### 安装

```bash
# 克隆仓库
git clone https://github.com/JX05120LLL/SwarmDev.git
cd SwarmDev

# 安装（开发模式）
pip install -e ".[dev]"
```

### 配置

```bash
cp swarmdev.example.yaml swarmdev.yaml
# 编辑 swarmdev.yaml，填入你的 Token
```

```yaml
telegram:
  bot_token: "your-bot-token"        # 或设置环境变量 TELEGRAM_BOT_TOKEN
  allowed_users: []                   # 空列表 = 允许所有人

llm:
  provider: "openai"
  model: "gpt-4o"
  api_key: "sk-..."                  # 或设置环境变量 OPENAI_API_KEY
  base_url: ""                       # 自定义 API 端点（可选）
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

### 运行

```bash
# 启动 Bot
swarmdev

# 或者用 Python 模块方式运行
python -m swarmdev.cli
```

然后在 Telegram 中给你的 Bot 发消息：

> 加一个登录页面，支持邮箱密码登录，带 JWT token 刷新接口

任务拆解器会自动将需求分解为子任务，调度器分配给可用的 Agent 执行，你会在聊天中收到实时进度更新。

## 测试

```bash
pytest tests/ -v
```

59 个测试用例，覆盖：

- **任务拆解器** — 正常路径、边界情况（空消息/乱码/带围栏的 JSON）、依赖归一化、重试/降级、字段强制转换
- **调度器** — 基础调度、依赖排序、失败/重试、依赖级联取消、Agent 轮转、进度上报
- **Telegram** — 消息分割、进度格式化、消息处理、错误处理
- **Codex 适配器** — 健康检查、执行成功/失败、超时、CLI 未找到

## 项目状态

**v0.1.0** — 核心管线已跑通。

### 当前限制

- **仅串行执行** — 调度器一次处理一个任务，并行执行已规划但尚未实现
- **单一 Agent 类型** — 目前仅实现 Codex 适配器，Claude Code 和 OpenClaw 适配器规划中
- **无鉴权** — Telegram `allowed_users` 配置项已存在但尚未强制执行

## 路线图

- [ ] 并行调度器（基于依赖图的并行执行）
- [ ] Claude Code 适配器
- [ ] OpenClaw 适配器
- [ ] 微信通道
- [ ] 飞书通道
- [ ] Web Dashboard
- [ ] 用户鉴权
- [ ] 任务持久化（SQLite）

## 开发背景

这个项目的灵感来自实际使用多个 AI 编程工具的经验：

- **Hermes Agent** 擅长任务协调和工具调用
- **OpenClaw (Zero)** 擅长架构设计和技术决策
- **Codex CLI** 擅长并行编写高质量代码
- **Claude Code** 擅长深度代码理解和重构

SwarmDev 的目标是让这些 Agent 真正协作起来，而不是各自为战。通过聊天界面下达需求，由专门的协调 Agent 拆解任务、分配执行、汇总结果，实现 **1+1>2** 的效果。

## License

MIT
