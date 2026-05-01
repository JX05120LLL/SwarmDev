"""Telegram channel adapter."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, ApplicationBuilder, ContextTypes, MessageHandler, filters

from codeswarm.core.types import ChatMessage, ProgressUpdate

logger = logging.getLogger(__name__)

OnMessageCallback = Callable[[ChatMessage], Awaitable[None] | None]


class TelegramChannel:
    """Telegram implementation of the channel adapter protocol."""

    _MAX_MESSAGE_LENGTH = 4000

    def __init__(self, bot_token: str, on_message: OnMessageCallback) -> None:
        self._bot_token = bot_token
        self._on_message = on_message
        self._application: Application | None = None
        self._running = False

    @property
    def name(self) -> str:
        return "telegram"

    async def start(self) -> None:
        """Start Telegram long polling."""
        if self._running:
            return

        if not self._bot_token:
            raise ValueError("Telegram bot token is required")

        if self._application is None:
            self._application = (
                ApplicationBuilder()
                .token(self._bot_token)
                .build()
            )
            self._application.add_handler(
                MessageHandler(filters.TEXT & ~filters.UpdateType.EDITED_MESSAGE, self._handle_message)
            )

        await self._application.initialize()
        await self._application.start()

        if self._application.updater is None:
            raise RuntimeError("Telegram application updater is not available")

        await self._application.updater.start_polling()
        self._running = True
        logger.info("Telegram channel started")

    async def stop(self) -> None:
        """Stop Telegram polling and release resources."""
        if self._application is None:
            return

        try:
            if self._running and self._application.updater is not None:
                await self._application.updater.stop()

            if self._running:
                await self._application.stop()
        finally:
            await self._application.shutdown()
            self._running = False
            logger.info("Telegram channel stopped")

    async def send_message(self, chat_id: str, text: str) -> bool:
        """Send a text message to a Telegram chat."""
        if self._application is None:
            raise RuntimeError("Telegram channel is not started")

        try:
            chunks = self._split_message(text)
            for chunk in chunks:
                await self._application.bot.send_message(chat_id=chat_id, text=chunk)
            return True
        except TelegramError:
            logger.exception("Failed to send Telegram message to chat %s", chat_id)
            return False

    async def send_progress(self, chat_id: str, update: ProgressUpdate) -> bool:
        """Format and send a progress update."""
        return await self.send_message(chat_id, self._format_progress(update))

    async def _handle_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Convert Telegram updates into the shared ChatMessage type."""
        del context

        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user

        if message is None or chat is None or user is None or not message.text:
            return

        chat_message = ChatMessage(
            channel=self.name,
            chat_id=str(chat.id),
            user_id=str(user.id),
            text=message.text,
            metadata={
                "message_id": message.message_id,
                "chat_type": chat.type,
                "username": user.username,
                "full_name": user.full_name,
            },
        )

        try:
            result = self._on_message(chat_message)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Unhandled exception in Telegram on_message callback")

    def _format_progress(self, update: ProgressUpdate) -> str:
        """Render a progress update as plain text for Telegram."""
        lines: list[str] = []

        if update.is_final:
            lines.append("Final Update")

        if update.message:
            lines.append(update.message)

        progress_percent = max(0.0, min(update.overall_progress, 1.0)) * 100
        lines.append(f"Progress: {progress_percent:.0f}%")

        if update.tasks_status:
            lines.append("")
            lines.append("Tasks:")
            for task in update.tasks_status:
                title = task.get("title", "Untitled task")
                status = task.get("status", "unknown")
                agent = task.get("agent")
                if agent:
                    lines.append(f"- [{status}] {title} ({agent})")
                else:
                    lines.append(f"- [{status}] {title}")

        return "\n".join(lines).strip()

    def _split_message(self, text: str) -> list[str]:
        """Split long messages to stay under Telegram's message limit."""
        content = text.strip() or "(empty message)"
        if len(content) <= self._MAX_MESSAGE_LENGTH:
            return [content]

        chunks: list[str] = []
        remaining = content

        while len(remaining) > self._MAX_MESSAGE_LENGTH:
            split_at = remaining.rfind("\n", 0, self._MAX_MESSAGE_LENGTH)
            if split_at <= 0:
                split_at = self._MAX_MESSAGE_LENGTH

            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()

        if remaining:
            chunks.append(remaining)

        return chunks
