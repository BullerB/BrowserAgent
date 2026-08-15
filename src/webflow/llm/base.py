"""Provider-agnostic LLM interface.

Structured output is implemented once, on top of a single ``_complete`` method,
by asking for JSON and validating it against a pydantic model. That is the
lowest common denominator across vendors and keeps new providers trivial to add.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from webflow.domain.errors import LLMError
from webflow.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(slots=True)
class LLMUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion


def extract_json(text: str) -> str:
    """Pull a JSON object out of a reply that may be fenced or prefixed with prose."""
    fenced = _JSON_BLOCK.search(text)
    if fenced:
        return fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text.strip()


class LLMClient(ABC):
    """Minimal contract every provider adapter implements."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.usage = LLMUsage()

    @abstractmethod
    async def _complete(self, system: str, user: str, *, json_mode: bool) -> str:
        """Return the raw assistant message."""

    async def generate_text(self, system: str, user: str) -> str:
        return await self._complete(system, user, json_mode=False)

    async def generate_structured(self, system: str, user: str, response_model: type[T]) -> T:
        """Ask for JSON matching ``response_model``, retrying once with the error."""
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        instruction = (
            f"{system}\n\nReply with a single JSON object and nothing else. "
            f"It must validate against this JSON Schema:\n{schema}"
        )
        raw = await self._complete(instruction, user, json_mode=True)
        try:
            return response_model.model_validate_json(extract_json(raw))
        except (ValidationError, ValueError) as first_error:
            log.debug("llm_structured_retry", model=self.model, error=str(first_error)[:400])
            repair = (
                f"{user}\n\nYour previous reply was invalid:\n{raw[:1500]}\n\n"
                f"Validation error:\n{str(first_error)[:800]}\n\nReturn corrected JSON only."
            )
            retried = await self._complete(instruction, repair, json_mode=True)
            try:
                return response_model.model_validate_json(extract_json(retried))
            except (ValidationError, ValueError) as second_error:
                raise LLMError(
                    f"{self.model} did not return valid {response_model.__name__}: {second_error}"
                ) from second_error


class NullLLMClient(LLMClient):
    """Used when no provider is configured; replay-only runs never touch it."""

    def __init__(self) -> None:
        super().__init__(model="null")

    async def _complete(self, system: str, user: str, *, json_mode: bool) -> str:
        from webflow.domain.errors import LLMNotConfiguredError

        raise LLMNotConfiguredError(
            "No LLM provider configured. Set WEBFLOW_LLM__PROVIDER and WEBFLOW_LLM__API_KEY "
            "to let the agent explore new sites (cached flows replay without one)."
        )


class ScriptedLLMClient(LLMClient):
    """Deterministic client for tests: returns queued replies in order."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__(model="scripted")
        self.replies = list(replies)
        self.prompts: list[tuple[str, str]] = []

    async def _complete(self, system: str, user: str, *, json_mode: bool) -> str:
        self.prompts.append((system, user))
        if not self.replies:
            raise LLMError("ScriptedLLMClient ran out of replies")
        self.usage.add(0, 0)
        return self.replies.pop(0)
