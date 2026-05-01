"""Configuration management for SwarmDev."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""
    bot_token: str = ""
    allowed_users: list[int] = field(default_factory=list)  # empty = allow all


@dataclass
class LLMConfig:
    """LLM configuration for task decomposition."""
    provider: str = "openai"        # openai / anthropic / custom
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.3


@dataclass
class AgentConfig:
    """Configuration for a single agent."""
    name: str = ""
    agent_type: str = ""            # codex / claude_code / openclaw
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectConfig:
    """Project-level configuration."""
    name: str = "swarmdev-project"
    root_dir: str = "."
    git_repo: str = ""              # git remote URL, empty = local only


@dataclass
class SwarmDevConfig:
    """Top-level configuration."""
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    agents: list[AgentConfig] = field(default_factory=list)
    project: ProjectConfig = field(default_factory=ProjectConfig)
    max_concurrent_agents: int = 3
    task_timeout: int = 600
    log_level: str = "INFO"

    @classmethod
    def load(cls, path: str | Path = "swarmdev.yaml") -> SwarmDevConfig:
        """Load config from a YAML file."""
        path = Path(path)
        if not path.exists():
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        config = cls()

        # Telegram
        if tg := data.get("telegram"):
            config.telegram = TelegramConfig(
                bot_token=tg.get("bot_token", os.getenv("TELEGRAM_BOT_TOKEN", "")),
                allowed_users=tg.get("allowed_users", []),
            )

        # LLM
        if llm := data.get("llm"):
            config.llm = LLMConfig(
                provider=llm.get("provider", "openai"),
                model=llm.get("model", "gpt-4o"),
                api_key=llm.get("api_key", os.getenv("OPENAI_API_KEY", "")),
                base_url=llm.get("base_url", ""),
                temperature=llm.get("temperature", 0.3),
            )

        # Agents
        for agent_data in data.get("agents", []):
            config.agents.append(AgentConfig(
                name=agent_data.get("name", ""),
                agent_type=agent_data.get("type", ""),
                enabled=agent_data.get("enabled", True),
                config=agent_data.get("config", {}),
            ))

        # Project
        if proj := data.get("project"):
            config.project = ProjectConfig(
                name=proj.get("name", "swarmdev-project"),
                root_dir=proj.get("root_dir", "."),
                git_repo=proj.get("git_repo", ""),
            )

        config.max_concurrent_agents = data.get("max_concurrent_agents", 3)
        config.task_timeout = data.get("task_timeout", 600)
        config.log_level = data.get("log_level", "INFO")

        return config

    def save(self, path: str | Path = "swarmdev.yaml") -> None:
        """Save config to a YAML file."""
        data = {
            "telegram": {
                "bot_token": self.telegram.bot_token,
                "allowed_users": self.telegram.allowed_users,
            },
            "llm": {
                "provider": self.llm.provider,
                "model": self.llm.model,
                "base_url": self.llm.base_url,
                "temperature": self.llm.temperature,
            },
            "agents": [
                {
                    "name": a.name,
                    "type": a.agent_type,
                    "enabled": a.enabled,
                    "config": a.config,
                }
                for a in self.agents
            ],
            "project": {
                "name": self.project.name,
                "root_dir": self.project.root_dir,
                "git_repo": self.project.git_repo,
            },
            "max_concurrent_agents": self.max_concurrent_agents,
            "task_timeout": self.task_timeout,
            "log_level": self.log_level,
        }

        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
