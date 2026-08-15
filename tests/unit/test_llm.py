"""The LLM adapters, exercised without an API key.

A fake stands in for the vendor SDK, so the parts that are actually ours - the
request shape, JSON extraction, schema validation and the repair retry - are
verified. Only the network call itself is unproven until a key is configured.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from webflow.config import LLMSettings
from webflow.domain.errors import ConfigurationError, LLMError, LLMNotConfiguredError
from webflow.llm.base import LLMClient, NullLLMClient, ScriptedLLMClient, extract_json
from webflow.llm.registry import create_llm_client


class Answer(BaseModel):
    city: str
    population: int


class FakeCompletions:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        content = self.replies.pop(0)

        class Usage:
            prompt_tokens, completion_tokens = 11, 7

        class Message:
            def __init__(self, text: str) -> None:
                self.content = text

        class Choice:
            def __init__(self, text: str) -> None:
                self.message = Message(text)

        class Response:
            def __init__(self, text: str) -> None:
                self.choices = [Choice(text)]
                self.usage = Usage()

        return Response(content)


def _openai_client(monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> Any:
    from webflow.llm import openai_client as module

    fake = FakeCompletions(replies)

    class FakeAsyncOpenAI:
        def __init__(self, **_: Any) -> None:
            self.chat = type("Chat", (), {"completions": fake})()

    monkeypatch.setattr(module, "AsyncOpenAI", FakeAsyncOpenAI, raising=False)
    monkeypatch.setitem(
        __import__("sys").modules, "openai", type("openai", (), {"AsyncOpenAI": FakeAsyncOpenAI})
    )
    client = module.OpenAIClient(
        LLMSettings(provider="openai", model="gpt-4o-mini", api_key=SecretStr("sk-test"))
    )
    return client, fake


# ------------------------------------------------------------------- adapters


async def test_openai_adapter_sends_system_and_user_and_requests_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, fake = _openai_client(monkeypatch, ['{"city": "København", "population": 660000}'])

    answer = await client.generate_structured("be precise", "Largest DK city?", Answer)

    assert answer.city == "København"
    call = fake.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert call["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in call["messages"]] == ["system", "user"]
    assert "JSON Schema" in call["messages"][0]["content"], "schema must be sent to the model"
    assert client.usage.calls == 1


async def test_openai_adapter_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from webflow.llm import openai_client as module

    monkeypatch.setattr(module, "AsyncOpenAI", object, raising=False)
    with pytest.raises(LLMNotConfiguredError):
        module.OpenAIClient(LLMSettings(provider="openai", api_key=None))


# ------------------------------------------------------- structured behaviour


async def test_fenced_and_chatty_replies_are_still_parsed() -> None:
    client = ScriptedLLMClient(['Sure!\n```json\n{"city": "Aarhus", "population": 285000}\n```'])
    answer = await client.generate_structured("s", "u", Answer)
    assert answer.city == "Aarhus"


async def test_invalid_json_is_retried_with_the_validation_error() -> None:
    client = ScriptedLLMClient(
        ['{"city": "Odense"}', '{"city": "Odense", "population": 180000}']
    )
    answer = await client.generate_structured("s", "u", Answer)

    assert answer.population == 180000
    assert len(client.prompts) == 2
    assert "was invalid" in client.prompts[1][1]


async def test_two_bad_replies_raise_rather_than_loop() -> None:
    client = ScriptedLLMClient(['{"nope": 1}', "still not json"])
    with pytest.raises(LLMError):
        await client.generate_structured("s", "u", Answer)


def test_json_is_extracted_from_prose_and_fences() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Here you go: {"a": 1} - hope that helps') == '{"a": 1}'
    assert extract_json('{"a": 1}') == '{"a": 1}'


# -------------------------------------------------------------------- registry


def test_registry_returns_a_null_client_when_unconfigured() -> None:
    assert isinstance(create_llm_client(LLMSettings(provider="null")), NullLLMClient)


async def test_null_client_explains_what_to_configure() -> None:
    client: LLMClient = NullLLMClient()
    with pytest.raises(LLMNotConfiguredError, match="WEBFLOW_LLM__PROVIDER"):
        await client.generate_text("s", "u")


def test_unknown_provider_is_rejected() -> None:
    settings = LLMSettings.model_construct(provider="gemini")
    with pytest.raises(ConfigurationError):
        create_llm_client(settings)
