"""OpenAI (and OpenAI-compatible gateway) adapter."""

from __future__ import annotations

from typing import Any

from webflow.config import LLMSettings
from webflow.domain.errors import LLMError, LLMNotConfiguredError
from webflow.llm.base import LLMClient


class OpenAIClient(LLMClient):
    def __init__(self, settings: LLMSettings) -> None:
        super().__init__(model=settings.model)
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMNotConfiguredError("pip install 'webflow[openai]'") from exc
        if settings.api_key is None:
            raise LLMNotConfiguredError("WEBFLOW_LLM__API_KEY is not set")
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url,
            timeout=settings.request_timeout_s,
        )

    async def _complete(self, system: str, user: str, *, json_mode: bool) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._settings.temperature,
            "max_tokens": self._settings.max_output_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self._client.chat.completions.create(**kwargs)
        usage = response.usage
        self.usage.add(
            usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0
        )
        content: str | None = response.choices[0].message.content
        if content is None:
            raise LLMError("OpenAI returned an empty message")
        return content
