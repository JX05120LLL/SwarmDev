"""LLM-backed task decomposition for the orchestrator."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from codeswarm.core.config import LLMConfig
from codeswarm.core.types import DecompositionResult, SubTask, TaskDecomposer

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You decompose software work into execution-ready sub-tasks.

Return only valid JSON. Do not include markdown, explanations, or code fences.

Output schema:
{
  "summary": "short summary",
  "estimated_total_time": "rough estimate",
  "sub_tasks": [
    {
      "title": "short task title",
      "description": "specific instructions for the worker",
      "files_to_modify": ["path/to/file.py"],
      "dependencies": [0],
      "estimated_complexity": "low|medium|high"
    }
  ]
}

Rules:
- Prefer 1-6 sub_tasks.
- dependencies must reference earlier task indices when possible.
- Use empty arrays instead of null.
- Keep titles concise and descriptions actionable.
- If the request is too small to split meaningfully, return one sub-task.
"""


class LLMDecomposer(TaskDecomposer):
    """Use an LLM to break a user request into structured sub-tasks."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        max_retries: int = 1,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.config = config
        self.max_retries = max(0, max_retries)
        self._client = client

    async def decompose(
        self,
        request: str,
        project_context: str = "",
    ) -> DecompositionResult:
        """Decompose a user request into sub-tasks."""
        request = request.strip()
        if not request:
            return DecompositionResult(
                original_request="",
                summary="Empty request.",
                estimated_total_time="unknown",
                sub_tasks=[],
            )

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response_text = await self._request_decomposition(request, project_context)
                return self._parse_decomposition(request, response_text)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "Task decomposition attempt %s/%s failed: %s",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )

        return self._fallback_result(request, last_error)

    def _get_client(self) -> AsyncOpenAI:
        """Initialize the async OpenAI client lazily."""
        if self._client is None:
            client_kwargs: dict[str, Any] = {}
            if self.config.api_key:
                client_kwargs["api_key"] = self.config.api_key
            if self.config.base_url:
                client_kwargs["base_url"] = self.config.base_url
            self._client = AsyncOpenAI(**client_kwargs)
        return self._client

    async def _request_decomposition(self, request: str, project_context: str) -> str:
        """Send the decomposition prompt to the LLM and return raw text."""
        user_prompt = self._build_user_prompt(request, project_context)
        response = await self._get_client().chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )

        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise ValueError("LLM returned an empty response")
        return content

    def _build_user_prompt(self, request: str, project_context: str) -> str:
        """Build the user prompt for decomposition."""
        prompt_parts = [f"User request:\n{request}"]
        if project_context.strip():
            prompt_parts.append(f"Project context:\n{project_context.strip()}")
        prompt_parts.append(
            "Return a JSON object matching the schema exactly. "
            "All dependencies must be integer indices into sub_tasks."
        )
        return "\n\n".join(prompt_parts)

    def _parse_decomposition(self, request: str, response_text: str) -> DecompositionResult:
        """Parse raw LLM output into a DecompositionResult."""
        payload = self._load_json_payload(response_text)
        if isinstance(payload, list):
            payload = {"sub_tasks": payload}
        if not isinstance(payload, dict):
            raise ValueError("LLM JSON output must be an object or an array of sub-tasks")

        raw_sub_tasks = (
            payload.get("sub_tasks")
            or payload.get("subtasks")
            or payload.get("tasks")
            or []
        )
        if not isinstance(raw_sub_tasks, list):
            raise ValueError("sub_tasks must be a list")

        sub_tasks = [self._parse_sub_task(item) for item in raw_sub_tasks if isinstance(item, dict)]
        sub_tasks = self._normalize_dependencies(sub_tasks)

        if not sub_tasks:
            raise ValueError("No valid sub_tasks found in LLM output")

        summary = str(payload.get("summary") or "LLM-generated task decomposition.")
        estimated_total_time = str(payload.get("estimated_total_time") or "unknown")
        return DecompositionResult(
            original_request=request,
            summary=summary,
            estimated_total_time=estimated_total_time,
            sub_tasks=sub_tasks,
        )

    def _load_json_payload(self, response_text: str) -> Any:
        """Load JSON from raw LLM output with light recovery for wrapped text."""
        text = response_text.strip()
        if not text:
            raise ValueError("LLM returned blank text")

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                inner = "\n".join(lines[1:-1]).strip()
                if inner:
                    return json.loads(inner)

        start_positions = [pos for pos in (text.find("{"), text.find("[")) if pos != -1]
        if not start_positions:
            raise ValueError("No JSON object found in LLM output")
        start = min(start_positions)

        end_positions = [pos for pos in (text.rfind("}"), text.rfind("]")) if pos != -1]
        end = max(end_positions)
        if end < start:
            raise ValueError("Malformed JSON boundaries in LLM output")

        return json.loads(text[start : end + 1])

    def _parse_sub_task(self, item: dict[str, Any]) -> SubTask:
        """Convert a raw sub-task object into the shared SubTask type."""
        title = str(item.get("title") or "Untitled task").strip()
        description = str(item.get("description") or title).strip()
        files_to_modify = self._coerce_string_list(item.get("files_to_modify"))
        dependencies = self._coerce_int_list(item.get("dependencies"))
        estimated_complexity = self._normalize_complexity(item.get("estimated_complexity"))

        return SubTask(
            title=title,
            description=description,
            files_to_modify=files_to_modify,
            dependencies=dependencies,
            estimated_complexity=estimated_complexity,
        )

    def _normalize_dependencies(self, sub_tasks: list[SubTask]) -> list[SubTask]:
        """Keep dependency indices valid and deduplicated."""
        task_count = len(sub_tasks)
        normalized: list[SubTask] = []
        for index, task in enumerate(sub_tasks):
            seen: set[int] = set()
            deps: list[int] = []
            for dep in task.dependencies:
                if dep == index or dep < 0 or dep >= task_count or dep in seen:
                    continue
                seen.add(dep)
                deps.append(dep)

            normalized.append(
                SubTask(
                    title=task.title,
                    description=task.description,
                    files_to_modify=task.files_to_modify,
                    dependencies=deps,
                    estimated_complexity=task.estimated_complexity,
                )
            )
        return normalized

    def _coerce_string_list(self, value: Any) -> list[str]:
        """Convert a value into a list of non-empty strings."""
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _coerce_int_list(self, value: Any) -> list[int]:
        """Convert a value into a list of integers."""
        if not isinstance(value, list):
            return []

        items: list[int] = []
        for item in value:
            if isinstance(item, bool):
                continue
            try:
                items.append(int(item))
            except (TypeError, ValueError):
                continue
        return items

    def _normalize_complexity(self, value: Any) -> str:
        """Normalize complexity to the shared enum-like values."""
        normalized = str(value or "medium").strip().lower()
        if normalized not in {"low", "medium", "high"}:
            return "medium"
        return normalized

    def _fallback_result(
        self,
        request: str,
        error: Exception | None = None,
    ) -> DecompositionResult:
        """Fallback to a single task when LLM output cannot be used."""
        if error:
            logger.warning("Falling back to single-task decomposition: %s", error)

        return DecompositionResult(
            original_request=request,
            summary="Fallback decomposition using a single task.",
            estimated_total_time="unknown",
            sub_tasks=[
                SubTask(
                    title="Handle user request",
                    description=request,
                    estimated_complexity="medium",
                )
            ],
        )
