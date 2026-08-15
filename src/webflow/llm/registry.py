"""Pick an LLM adapter from configuration."""

from __future__ import annotations

from webflow.config import LLMSettings, get_settings
from webflow.domain.errors import ConfigurationError
from webflow.llm.base import LLMClient, NullLLMClient


def create_llm_client(settings: LLMSettings | None = None) -> LLMClient:
    settings = settings or get_settings().llm
    match settings.provider:
        case "openai":
            from webflow.llm.openai_client import OpenAIClient

            return OpenAIClient(settings)
        case "anthropic":
            from webflow.llm.anthropic_client import AnthropicClient

            return AnthropicClient(settings)
        case "null":
            return NullLLMClient()
        case _:
            raise ConfigurationError(f"Unknown LLM provider {settings.provider!r}")
