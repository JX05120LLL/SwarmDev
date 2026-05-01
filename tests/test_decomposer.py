"""Tests for the LLM task decomposer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from swarmdev.core.config import LLMConfig
from swarmdev.core.types import DecompositionResult, SubTask
from swarmdev.orchestrator.decomposer import LLMDecomposer


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def config() -> LLMConfig:
    return LLMConfig(
        model="gpt-4o",
        api_key="test-key",
        temperature=0.1,
    )


@pytest.fixture
def decomposer(config: LLMConfig) -> LLMDecomposer:
    return LLMDecomposer(config, max_retries=0)


def _make_llm_response(content: str) -> MagicMock:
    """Build a mock OpenAI chat completion response."""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


# ============================================================
# Happy path
# ============================================================

class TestDecomposeHappyPath:
    """Standard decomposition scenarios."""

    @pytest.mark.asyncio
    async def test_simple_request(self, decomposer: LLMDecomposer) -> None:
        llm_output = json.dumps({
            "summary": "Add login page",
            "estimated_total_time": "2h",
            "sub_tasks": [
                {
                    "title": "Create login form",
                    "description": "Build a login form with email and password fields",
                    "files_to_modify": ["src/pages/login.tsx"],
                    "dependencies": [],
                    "estimated_complexity": "medium",
                },
                {
                    "title": "Add auth API",
                    "description": "Implement POST /api/auth/login endpoint",
                    "files_to_modify": ["src/api/auth.py"],
                    "dependencies": [0],
                    "estimated_complexity": "high",
                },
            ],
        })
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(llm_output))
        decomposer._client = client

        result = await decomposer.decompose("Add a login page")

        assert isinstance(result, DecompositionResult)
        assert len(result.sub_tasks) == 2
        assert result.summary == "Add login page"
        assert result.sub_tasks[1].dependencies == [0]  # depends on first task (index 0)

    @pytest.mark.asyncio
    async def test_single_task_request(self, decomposer: LLMDecomposer) -> None:
        llm_output = json.dumps({
            "summary": "Fix typo",
            "sub_tasks": [
                {
                    "title": "Fix typo in README",
                    "description": "Fix the typo",
                    "files_to_modify": ["README.md"],
                    "dependencies": [],
                    "estimated_complexity": "low",
                },
            ],
        })
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(llm_output))
        decomposer._client = client

        result = await decomposer.decompose("Fix the typo in README")

        assert len(result.sub_tasks) == 1
        assert result.sub_tasks[0].estimated_complexity == "low"


# ============================================================
# Empty / edge cases
# ============================================================

class TestDecomposeEdgeCases:
    """Edge cases: empty input, malformed LLM output."""

    @pytest.mark.asyncio
    async def test_empty_request_returns_empty(self, decomposer: LLMDecomposer) -> None:
        result = await decomposer.decompose("")
        assert result.sub_tasks == []
        assert "Empty" in result.summary

    @pytest.mark.asyncio
    async def test_whitespace_only_request_returns_empty(self, decomposer: LLMDecomposer) -> None:
        result = await decomposer.decompose("   \n\t  ")
        assert result.sub_tasks == []

    @pytest.mark.asyncio
    async def test_llm_returns_array_instead_of_object(self, decomposer: LLMDecomposer) -> None:
        """LLM sometimes returns a bare array of tasks instead of a wrapper object."""
        llm_output = json.dumps([
            {
                "title": "Task A",
                "description": "Do A",
                "files_to_modify": [],
                "dependencies": [],
                "estimated_complexity": "low",
            },
        ])
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(llm_output))
        decomposer._client = client

        result = await decomposer.decompose("Do something")

        assert len(result.sub_tasks) == 1
        assert result.sub_tasks[0].title == "Task A"

    @pytest.mark.asyncio
    async def test_llm_returns_json_in_code_fence(self, decomposer: LLMDecomposer) -> None:
        payload = json.dumps({
            "summary": "Fenced",
            "sub_tasks": [
                {
                    "title": "Task",
                    "description": "Desc",
                    "files_to_modify": [],
                    "dependencies": [],
                    "estimated_complexity": "medium",
                },
            ],
        })
        llm_output = f"```json\n{payload}\n```"

        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(llm_output))
        decomposer._client = client

        result = await decomposer.decompose("Something")

        assert len(result.sub_tasks) == 1

    @pytest.mark.asyncio
    async def test_llm_returns_json_with_prefix_text(self, decomposer: LLMDecomposer) -> None:
        """LLM sometimes adds explanation text before the JSON."""
        payload = json.dumps({
            "summary": "Done",
            "sub_tasks": [
                {
                    "title": "T",
                    "description": "D",
                    "files_to_modify": [],
                    "dependencies": [],
                    "estimated_complexity": "low",
                },
            ],
        })
        llm_output = f"Here is the decomposition:\n{payload}"

        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(llm_output))
        decomposer._client = client

        result = await decomposer.decompose("Something")

        assert len(result.sub_tasks) == 1

    @pytest.mark.asyncio
    async def test_llm_returns_empty_string_falls_back(self, decomposer: LLMDecomposer) -> None:
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(""))
        decomposer._client = client

        result = await decomposer.decompose("Build a website")

        # Fallback: single task
        assert len(result.sub_tasks) == 1
        assert "Fallback" in result.summary

    @pytest.mark.asyncio
    async def test_llm_returns_garbage_falls_back(self, decomposer: LLMDecomposer) -> None:
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response("not json at all"))
        decomposer._client = client

        result = await decomposer.decompose("Build something")

        assert len(result.sub_tasks) == 1
        assert "Fallback" in result.summary


# ============================================================
# Dependency normalization
# ============================================================

class TestDependencyNormalization:
    """Verify that invalid dependencies are stripped."""

    @pytest.mark.asyncio
    async def test_self_dependency_removed(self, decomposer: LLMDecomposer) -> None:
        llm_output = json.dumps({
            "summary": "Test",
            "sub_tasks": [
                {
                    "title": "A",
                    "description": "A",
                    "files_to_modify": [],
                    "dependencies": [0],  # self-dep
                    "estimated_complexity": "low",
                },
            ],
        })
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(llm_output))
        decomposer._client = client

        result = await decomposer.decompose("Test")

        assert result.sub_tasks[0].dependencies == []

    @pytest.mark.asyncio
    async def test_out_of_range_dependency_removed(self, decomposer: LLMDecomposer) -> None:
        llm_output = json.dumps({
            "summary": "Test",
            "sub_tasks": [
                {
                    "title": "A",
                    "description": "A",
                    "files_to_modify": [],
                    "dependencies": [5, -1, 0],
                    "estimated_complexity": "low",
                },
                {
                    "title": "B",
                    "description": "B",
                    "files_to_modify": [],
                    "dependencies": [],
                    "estimated_complexity": "low",
                },
            ],
        })
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(llm_output))
        decomposer._client = client

        result = await decomposer.decompose("Test")

        # Only valid cross-task dep should remain (index 0 → B depends on A)
        # Wait, task 0 has deps [5, -1, 0]. After normalization:
        # - 5 is out of range → removed
        # - -1 is negative → removed
        # - 0 is self (index 0) → removed
        # So task 0 deps = []
        assert result.sub_tasks[0].dependencies == []

    @pytest.mark.asyncio
    async def test_duplicate_dependencies_deduped(self, decomposer: LLMDecomposer) -> None:
        llm_output = json.dumps({
            "summary": "Test",
            "sub_tasks": [
                {
                    "title": "A",
                    "description": "A",
                    "files_to_modify": [],
                    "dependencies": [],
                    "estimated_complexity": "low",
                },
                {
                    "title": "B",
                    "description": "B",
                    "files_to_modify": [],
                    "dependencies": [0, 0, 0],
                    "estimated_complexity": "low",
                },
            ],
        })
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(llm_output))
        decomposer._client = client

        result = await decomposer.decompose("Test")

        assert result.sub_tasks[1].dependencies == [0]


# ============================================================
# Retry behavior
# ============================================================

class TestRetryBehavior:
    """Verify retry and fallback logic."""

    @pytest.mark.asyncio
    async def test_retry_on_failure_then_fallback(self) -> None:
        config = LLMConfig(model="gpt-4o", api_key="key")
        decomposer = LLMDecomposer(config, max_retries=2)

        client = AsyncMock()
        client.chat.completions.create = AsyncMock(side_effect=RuntimeError("API down"))
        decomposer._client = client

        result = await decomposer.decompose("Build X")

        # Should fall back after 3 attempts (1 initial + 2 retries)
        assert len(result.sub_tasks) == 1
        assert result.sub_tasks[0].title == "Handle user request"
        assert "Fallback" in result.summary
        assert client.chat.completions.create.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self) -> None:
        config = LLMConfig(model="gpt-4o", api_key="key")
        decomposer = LLMDecomposer(config, max_retries=1)

        good_response = _make_llm_response(json.dumps({
            "summary": "OK",
            "sub_tasks": [
                {
                    "title": "T",
                    "description": "D",
                    "files_to_modify": [],
                    "dependencies": [],
                    "estimated_complexity": "low",
                },
            ],
        }))
        bad_response = RuntimeError("transient")

        client = AsyncMock()
        client.chat.completions.create = AsyncMock(side_effect=[bad_response, good_response])
        decomposer._client = client

        result = await decomposer.decompose("Build Y")

        assert len(result.sub_tasks) == 1
        assert result.summary == "OK"


# ============================================================
# Sub-task field coercion
# ============================================================

class TestFieldCoercion:
    """Verify that weird LLM outputs are handled gracefully."""

    @pytest.mark.asyncio
    async def test_missing_optional_fields(self, decomposer: LLMDecomposer) -> None:
        llm_output = json.dumps({
            "sub_tasks": [
                {
                    "title": "Minimal",
                    "description": "Just title and desc",
                },
            ],
        })
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(llm_output))
        decomposer._client = client

        result = await decomposer.decompose("Test")

        task = result.sub_tasks[0]
        assert task.title == "Minimal"
        assert task.files_to_modify == []
        assert task.dependencies == []
        assert task.estimated_complexity == "medium"  # default

    @pytest.mark.asyncio
    async def test_unknown_complexity_defaults_to_medium(self, decomposer: LLMDecomposer) -> None:
        llm_output = json.dumps({
            "sub_tasks": [
                {
                    "title": "T",
                    "description": "D",
                    "files_to_modify": [],
                    "dependencies": [],
                    "estimated_complexity": "SUPER_HARD",
                },
            ],
        })
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(llm_output))
        decomposer._client = client

        result = await decomposer.decompose("Test")

        assert result.sub_tasks[0].estimated_complexity == "medium"

    @pytest.mark.asyncio
    async def test_non_list_fields_coerced(self, decomposer: LLMDecomposer) -> None:
        """files_to_modify and dependencies might not be lists from LLM."""
        llm_output = json.dumps({
            "sub_tasks": [
                {
                    "title": "T",
                    "description": "D",
                    "files_to_modify": "single_file.py",  # string, not list
                    "dependencies": "invalid",  # not a list
                    "estimated_complexity": "low",
                },
            ],
        })
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_make_llm_response(llm_output))
        decomposer._client = client

        result = await decomposer.decompose("Test")

        assert result.sub_tasks[0].files_to_modify == []
        assert result.sub_tasks[0].dependencies == []
