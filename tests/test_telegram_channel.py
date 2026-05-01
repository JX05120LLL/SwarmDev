"""Tests for the Telegram channel adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from swarmdev.channels.telegram_channel import TelegramChannel
from swarmdev.core.types import ChatMessage, ProgressUpdate


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def channel() -> TelegramChannel:
    received: list[ChatMessage] = []

    def on_message(msg: ChatMessage) -> None:
        received.append(msg)

    ch = TelegramChannel(bot_token="test-token-123", on_message=on_message)
    ch._received = received  # type: ignore[attr-defined]
    return ch


@pytest.fixture
def async_channel() -> TelegramChannel:
    received: list[ChatMessage] = []

    async def on_message(msg: ChatMessage) -> None:
        received.append(msg)

    ch = TelegramChannel(bot_token="test-token-123", on_message=on_message)
    ch._received = received  # type: ignore[attr-defined]
    return ch


# ============================================================
# Properties
# ============================================================

class TestProperties:
    def test_name(self, channel: TelegramChannel) -> None:
        assert channel.name == "telegram"

    def test_initial_state(self, channel: TelegramChannel) -> None:
        assert channel._running is False
        assert channel._application is None


# ============================================================
# Message splitting
# ============================================================

class TestMessageSplitting:
    def test_short_message_not_split(self, channel: TelegramChannel) -> None:
        chunks = channel._split_message("Hello world")
        assert chunks == ["Hello world"]

    def test_empty_message_placeholder(self, channel: TelegramChannel) -> None:
        chunks = channel._split_message("")
        assert chunks == ["(empty message)"]

    def test_long_message_split_on_newline(self, channel: TelegramChannel) -> None:
        # Create a message just over 4000 chars with newlines
        lines = ["x" * 100 for _ in range(50)]  # 5000 chars
        text = "\n".join(lines)

        chunks = channel._split_message(text)

        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= channel._MAX_MESSAGE_LENGTH

    def test_long_message_split_hard_break(self, channel: TelegramChannel) -> None:
        # No newlines — forces hard break at limit
        text = "a" * 5000

        chunks = channel._split_message(text)

        assert len(chunks) == 2
        assert chunks[0] == "a" * channel._MAX_MESSAGE_LENGTH
        assert chunks[1] == "a" * (5000 - channel._MAX_MESSAGE_LENGTH)

    def test_exact_limit_not_split(self, channel: TelegramChannel) -> None:
        text = "b" * channel._MAX_MESSAGE_LENGTH
        chunks = channel._split_message(text)
        assert len(chunks) == 1


# ============================================================
# Progress formatting
# ============================================================

class TestProgressFormatting:
    def test_basic_progress(self, channel: TelegramChannel) -> None:
        update = ProgressUpdate(
            message="Working...",
            overall_progress=0.5,
            is_final=False,
        )
        text = channel._format_progress(update)

        assert "Working..." in text
        assert "50%" in text

    def test_final_progress(self, channel: TelegramChannel) -> None:
        update = ProgressUpdate(
            message="All done",
            overall_progress=1.0,
            is_final=True,
        )
        text = channel._format_progress(update)

        assert "Final Update" in text
        assert "100%" in text

    def test_progress_with_tasks(self, channel: TelegramChannel) -> None:
        update = ProgressUpdate(
            message="Status",
            overall_progress=0.33,
            tasks_status=[
                {"title": "Task A", "status": "completed", "agent": "codex"},
                {"title": "Task B", "status": "running", "agent": ""},
                {"title": "Task C", "status": "pending"},
            ],
        )
        text = channel._format_progress(update)

        assert "[completed] Task A (codex)" in text
        assert "[running] Task B" in text
        assert "[pending] Task C" in text

    def test_progress_clamped(self, channel: TelegramChannel) -> None:
        update = ProgressUpdate(overall_progress=2.0)
        text = channel._format_progress(update)
        assert "100%" in text

        update = ProgressUpdate(overall_progress=-0.5)
        text = channel._format_progress(update)
        assert "0%" in text


# ============================================================
# Send message
# ============================================================

class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_when_not_started_raises(self, channel: TelegramChannel) -> None:
        with pytest.raises(RuntimeError, match="not started"):
            await channel.send_message("123", "hello")

    @pytest.mark.asyncio
    async def test_send_calls_bot(self, channel: TelegramChannel) -> None:
        mock_bot = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        channel._application = mock_app

        result = await channel.send_message("chat-123", "Hello!")

        assert result is True
        mock_bot.send_message.assert_called_once_with(chat_id="chat-123", text="Hello!")

    @pytest.mark.asyncio
    async def test_send_long_message_splits(self, channel: TelegramChannel) -> None:
        mock_bot = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        channel._application = mock_app

        text = "line\n" * 1000  # > 4000 chars
        result = await channel.send_message("chat-123", text)

        assert result is True
        assert mock_bot.send_message.call_count > 1

    @pytest.mark.asyncio
    async def test_send_telegram_error_returns_false(self, channel: TelegramChannel) -> None:
        from telegram.error import TelegramError

        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(side_effect=TelegramError("rate limited"))
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        channel._application = mock_app

        result = await channel.send_message("chat-123", "hi")

        assert result is False


# ============================================================
# Message handling
# ============================================================

class TestMessageHandling:
    @pytest.mark.asyncio
    async def test_handle_message_creates_chat_message(self, channel: TelegramChannel) -> None:
        """Verify Telegram update is converted to ChatMessage."""
        update = MagicMock()
        update.effective_message = MagicMock(text="Hello bot")
        update.effective_message.message_id = 42
        update.effective_chat = MagicMock()
        update.effective_chat.id = -100123
        update.effective_chat.type = "group"
        update.effective_user = MagicMock()
        update.effective_user.id = 99
        update.effective_user.username = "testuser"
        update.effective_user.full_name = "Test User"

        context = MagicMock()

        await channel._handle_message(update, context)

        assert len(channel._received) == 1  # type: ignore[attr-defined]
        msg = channel._received[0]  # type: ignore[attr-defined]
        assert msg.channel == "telegram"
        assert msg.chat_id == "-100123"
        assert msg.user_id == "99"
        assert msg.text == "Hello bot"
        assert msg.metadata["username"] == "testuser"

    @pytest.mark.asyncio
    async def test_handle_message_skips_empty(self, channel: TelegramChannel) -> None:
        update = MagicMock()
        update.effective_message = MagicMock(text=None)
        update.effective_chat = MagicMock()
        update.effective_user = MagicMock()

        await channel._handle_message(update, MagicMock())

        assert len(channel._received) == 0  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_handle_message_with_async_callback(self, async_channel: TelegramChannel) -> None:
        update = MagicMock()
        update.effective_message = MagicMock(text="async test")
        update.effective_message.message_id = 1
        update.effective_chat = MagicMock()
        update.effective_chat.id = 1
        update.effective_chat.type = "private"
        update.effective_user = MagicMock()
        update.effective_user.id = 1
        update.effective_user.username = None
        update.effective_user.full_name = "User"

        await async_channel._handle_message(update, MagicMock())

        assert len(async_channel._received) == 1  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_handle_message_callback_exception_logged(self, channel: TelegramChannel) -> None:
        """If the on_message callback throws, it should be caught and logged."""
        def bad_callback(msg: ChatMessage) -> None:
            raise ValueError("callback exploded")

        channel._on_message = bad_callback

        update = MagicMock()
        update.effective_message = MagicMock(text="trigger")
        update.effective_message.message_id = 1
        update.effective_chat = MagicMock()
        update.effective_chat.id = 1
        update.effective_chat.type = "private"
        update.effective_user = MagicMock()
        update.effective_user.id = 1
        update.effective_user.username = None
        update.effective_user.full_name = "User"

        # Should not raise
        await channel._handle_message(update, MagicMock())


# ============================================================
# Send progress
# ============================================================

class TestSendProgress:
    @pytest.mark.asyncio
    async def test_send_progress_delegates_to_send_message(self, channel: TelegramChannel) -> None:
        channel.send_message = AsyncMock(return_value=True)  # type: ignore[method-assign]

        update = ProgressUpdate(message="50% done", overall_progress=0.5)
        result = await channel.send_progress("chat-1", update)

        assert result is True
        channel.send_message.assert_called_once()  # type: ignore[attr-defined]
        call_args = channel.send_message.call_args  # type: ignore[attr-defined]
        assert "50%" in call_args[0][1]
