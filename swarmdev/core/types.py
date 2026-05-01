"""Core type definitions - the contract between all modules.

ALL modules must use these types. Do not create parallel type hierarchies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


# ============================================================
# Enums
# ============================================================

class TaskStatus(str, Enum):
    """Lifecycle status of a task."""
    PENDING = "pending"          # Waiting for dependencies
    READY = "ready"              # All deps met, can be dispatched
    RUNNING = "running"          # Currently executing
    COMPLETED = "completed"      # Finished successfully
    FAILED = "failed"            # Finished with error
    CANCELLED = "cancelled"      # Manually cancelled


class AgentStatus(str, Enum):
    """Lifecycle status of an agent."""
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


class MessageType(str, Enum):
    """Types of messages flowing through the system."""
    USER_REQUEST = "user_request"        # From chat channel
    TASK_UPDATE = "task_update"          # Progress update
    TASK_RESULT = "task_result"          # Final result
    ERROR = "error"                      # Error notification
    INFO = "info"                        # General info


# ============================================================
# Data classes
# ============================================================

@dataclass
class Task:
    """A unit of work to be executed by an agent."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    description: str = ""
    dependencies: list[str] = field(default_factory=list)  # task IDs
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str | None = None
    result: TaskResult | None = None
    files_to_modify: list[str] = field(default_factory=list)
    max_retries: int = 2
    retry_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """Result of executing a task."""
    task_id: str = ""
    success: bool = False
    output: str = ""
    files_changed: list[str] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubTask:
    """A decomposed sub-task from user's requirement."""
    title: str
    description: str
    files_to_modify: list[str] = field(default_factory=list)
    dependencies: list[int] = field(default_factory=list)  # indices into the list
    estimated_complexity: str = "medium"  # low / medium / high


@dataclass
class DecompositionResult:
    """Result of decomposing a user requirement into sub-tasks."""
    original_request: str = ""
    sub_tasks: list[SubTask] = field(default_factory=list)
    summary: str = ""
    estimated_total_time: str = ""


@dataclass
class AgentInfo:
    """Information about a registered agent."""
    name: str = ""
    agent_type: str = ""          # codex / claude_code / openclaw / custom
    capabilities: list[str] = field(default_factory=list)
    status: AgentStatus = AgentStatus.IDLE
    current_task_id: str | None = None
    supported_languages: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProgressUpdate:
    """A progress update from the orchestrator to the user."""
    message: str = ""
    tasks_status: list[dict[str, str]] = field(default_factory=list)  # [{title, status, agent}]
    overall_progress: float = 0.0   # 0.0 ~ 1.0
    is_final: bool = False


@dataclass
class ChatMessage:
    """A message from/to a chat channel."""
    channel: str = ""              # telegram / weixin / feishu
    chat_id: str = ""              # platform-specific chat identifier
    user_id: str = ""
    text: str = ""
    message_type: MessageType = MessageType.USER_REQUEST
    metadata: dict[str, Any] = field(default_factory=dict)


# ============================================================
# Protocols (interfaces)
# ============================================================

@runtime_checkable
class ChannelAdapter(Protocol):
    """Interface for chat channel adapters (Telegram, WeChat, etc.)."""

    @property
    def name(self) -> str:
        """Channel name (e.g. 'telegram', 'weixin')."""
        ...

    async def start(self) -> None:
        """Start listening for messages."""
        ...

    async def stop(self) -> None:
        """Stop the channel."""
        ...

    async def send_message(self, chat_id: str, text: str) -> bool:
        """Send a text message to a chat. Returns True on success."""
        ...

    async def send_progress(self, chat_id: str, update: ProgressUpdate) -> bool:
        """Send a progress update to a chat."""
        ...


@runtime_checkable
class AgentAdapter(Protocol):
    """Interface for AI agent adapters (Codex, Claude Code, etc.)."""

    @property
    def info(self) -> AgentInfo:
        """Agent information."""
        ...

    async def execute(self, task: Task, workdir: str) -> TaskResult:
        """Execute a task and return the result."""
        ...

    async def health_check(self) -> bool:
        """Check if the agent is available."""
        ...


@runtime_checkable
class TaskDecomposer(Protocol):
    """Interface for task decomposition (breaking requirements into sub-tasks)."""

    async def decompose(self, request: str, project_context: str = "") -> DecompositionResult:
        """Decompose a user request into sub-tasks."""
        ...


# ============================================================
# Constants
# ============================================================

DEFAULT_MAX_CONCURRENT_AGENTS = 3
DEFAULT_TASK_TIMEOUT_SECONDS = 600  # 10 minutes
DEFAULT_MAX_RETRIES = 2
