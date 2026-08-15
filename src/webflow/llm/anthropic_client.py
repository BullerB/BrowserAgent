"""Anthropic adapter."""

from __future__ import annotations

from webflow.config import LLMSettings
from webflow.domain.errors import LLMError, LLMNotConfiguredError
from webflow.llm.base import LLMClient


class AnthropicClient(LLMClient):
    def __init__(self, settings: LLMSettings) -> None:
        super().__init__(model=settings.model)
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise LLMNotConfiguredError("pip install 'webflow[anthropic]'") from exc
        if settings.api_key is None:
            raise LLMNotConfiguredError("WEBFLOW_LLM__API_KEY is not set")
        self._settings = settings
        self._client = AsyncAnthropic(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url,
            timeout=settings.request_timeout_s,
        )

    async def _complete(self, system: str, user: str, *, json_mode: bool) -> str:
        message = await self._client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=self._settings.temperature,
            max_tokens=self._settings.max_output_tokens,
        )
        self.usage.add(message.usage.input_tokens, message.usage.output_tokens)
        parts = [block.text for block in message.content if block.type == "text"]
        if not parts:
            raise LLMError("Anthropic returned no text content")
        return "".join(parts)
