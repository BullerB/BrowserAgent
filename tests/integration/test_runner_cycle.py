"""The full lifecycle, offline: explore -> suspend -> resume -> record -> replay.

This is the behaviour the whole design exists for, so it is tested end to end
against a local page with a scripted planner instead of a real LLM.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.support.demo_provider import DemoProvider
from webflow.config import Settings
from webflow.domain.checkpoint import CheckpointReason
from webflow.domain.run import RunMode, RunStatus
from webflow.flows.store import FlowStore
from webflow.llm.base import ScriptedLLMClient
from webflow.orchestrator.runner import GoalRunner
from webflow.orchestrator.services import Services
from webflow.profiles import Person, Profile


def decide(**action: Any) -> str:
    return json.dumps({"page_summary": "", "reasoning": "", "action": action})


EXTRACTION_REPLY = json.dumps(
    {
        "records": [
            {"company": "Alm. Brand", "price": "3499", "currency": "DKK", "period": "year"},
            {"company": "Tryg", "price": "4120", "currency": "DKK", "period": "year"},
        ]
    }
)

#: Turns 1-3: accept cookies, fill the e-mail, then discover we have no age.
EXPLORE_UNTIL_CHECKPOINT = [
    decide(kind="click", element_index=0, summary="accept cookies"),
    decide(kind="fill", element_index=0, profile_key="person.email"),
    decide(
        kind="ask_human",
        reason=CheckpointReason.MISSING_PROFILE_DATA.value,
        question="Hvor gammel er du?",
        fields=[
            {
                "key": "age",
                "prompt": "Din alder i hele år",
                "type": "number",
                "profile_key": "extra.age",
            }
        ],
    ),
]

#: Turns 4-9 after the human answered, then the extraction call.
FINISH_AFTER_ANSWER = [
    decide(kind="fill", element_index=1, answer_key="age"),
    decide(kind="fill_and_pick", element_index=2, literal_value="Isafjordsgade"),
    decide(kind="select", element_index=3, literal_value="Volkswagen"),
    decide(kind="check", element_index=4),
    decide(kind="click", element_index=5, summary="submit the form"),
    decide(kind="extract", schema_name="insurance_quote"),
    EXTRACTION_REPLY,
]


@pytest.fixture
def profile() -> Profile:
    return Profile(person=Person(email="test@example.com"))


@pytest.fixture
def provider(quote_form_url: str, tmp_path: Path) -> DemoProvider:
    return DemoProvider(quote_form_url, tmp_path / "provider_flows")


def _services(settings: Settings, replies: list[str]) -> Services:
    return Services.create(settings=settings, llm=ScriptedLLMClient(replies))


async def test_run_suspends_on_a_question_and_finishes_after_the_human_answers(
    settings: Settings, provider: DemoProvider, profile: Profile
) -> None:
    services = _services(settings, list(EXPLORE_UNTIL_CHECKPOINT))
    runner = GoalRunner(provider, "quote", services, profile=profile, headless=True)

    first = await runner.start()

    # --- suspended, with everything needed to come back later ------------
    assert first.awaiting_human
    assert first.run.status is RunStatus.AWAITING_HUMAN
    assert first.pending is not None
    assert first.pending.reason is CheckpointReason.MISSING_PROFILE_DATA
    assert [f.key for f in first.pending.fields] == ["age"]
    assert first.pending.screenshot_path is not None
    assert first.run.storage_state is not None, "session state must survive the pause"
    assert first.results is None

    queued = await services.queue.pending()
    assert [q.run_id for q in queued] == [first.run.id]

    # --- a human answers, possibly much later ---------------------------
    resumed_run = await services.queue.answer(first.run.id, {"age": "29"})
    assert resumed_run.status is RunStatus.PENDING
    assert services.profiles.load().extra["age"] == "29"

    # --- resume in a brand new browser ----------------------------------
    services.llm = ScriptedLLMClient(list(FINISH_AFTER_ANSWER))
    resumer = GoalRunner(provider, "quote", services, profile=profile, headless=True)
    second = await resumer.resume(first.run.id)

    assert second.succeeded, second.warnings
    assert second.results is not None
    assert [r.data["company"] for r in second.results.records] == ["Alm. Brand", "Tryg"]

    # --- and the successful path is now cached --------------------------
    assert second.recorded_flow
    flow = FlowStore(roots=[provider.flows_dir]).latest("demo", "quote")
    assert flow is not None
    assert [s.action.type for s in flow.steps] == [
        "goto",
        "click",
        "fill",
        "human_checkpoint",
        "fill",
        "fill_and_pick",
        "select",
        "check",
        "click",
        "extract",
    ]
    await services.aclose()


async def test_second_run_replays_the_cached_flow_without_the_planner(
    settings: Settings, provider: DemoProvider, profile: Profile
) -> None:
    services = _services(settings, list(EXPLORE_UNTIL_CHECKPOINT))
    first = await GoalRunner(provider, "quote", services, profile=profile).start()
    await services.queue.answer(first.run.id, {"age": "29"})
    services.llm = ScriptedLLMClient(list(FINISH_AFTER_ANSWER))
    await GoalRunner(provider, "quote", services, profile=profile).resume(first.run.id)

    # A planner without replies: any call would raise, so this asserts none happen.
    services.llm = ScriptedLLMClient([])
    third = await GoalRunner(provider, "quote", services, profile=profile).start()

    assert third.succeeded, third.warnings
    assert third.run.mode is RunMode.REPLAY
    assert third.run.llm_calls == 0
    assert third.recorded_flow is False
    assert third.results is not None
    # The remembered answer satisfied the checkpoint, so nobody was asked again.
    assert third.pending is None
    assert third.run.answers == {"age": "29"}
    # Extraction fell back to the price heuristic because no LLM was available.
    assert third.results.method == "heuristic"
    assert [r.data["company"] for r in third.results.records] == ["Alm. Brand", "Tryg"]
    await services.aclose()


async def test_replay_falls_back_to_the_agent_when_a_step_breaks(
    settings: Settings, provider: DemoProvider, profile: Profile, tmp_path: Path
) -> None:
    services = _services(settings, list(EXPLORE_UNTIL_CHECKPOINT))
    first = await GoalRunner(provider, "quote", services, profile=profile).start()
    await services.queue.answer(first.run.id, {"age": "29"})
    services.llm = ScriptedLLMClient(list(FINISH_AFTER_ANSWER))
    await GoalRunner(provider, "quote", services, profile=profile).resume(first.run.id)

    # Break the cached flow: point the e-mail step at an element that is gone.
    store = FlowStore(roots=[provider.flows_dir], write_root=provider.flows_dir)
    flow = store.latest("demo", "quote")
    assert flow is not None
    broken = flow.model_copy(deep=True)
    target = broken.steps[2].action.target
    target.candidates = [
        c.model_copy(update={"value": "ghost-element"}) for c in target.candidates
    ]
    store.save(broken.model_copy(update={"version": flow.version + 1}))

    # The repair handler is asked first, then exploration takes over.
    services.llm = ScriptedLLMClient(
        [
            decide(kind="fill", element_index=0, profile_key="person.email"),
            *FINISH_AFTER_ANSWER,
        ]
    )
    outcome = await GoalRunner(provider, "quote", services, profile=profile).start()

    assert outcome.succeeded, outcome.warnings
    assert outcome.results is not None
    assert len(outcome.results.records) == 2
    await services.aclose()
