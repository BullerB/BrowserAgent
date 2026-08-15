from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from webflow.agent.guards import Guards, detect_captcha, registrable_domain
from webflow.agent.planner import Planner, PlannerContext
from webflow.agent.policies import RunPolicy
from webflow.agent.schema import PlannedAction, PlannerDecision, to_action
from webflow.config import AgentSettings
from webflow.domain.actions import ClickAction, FillAndPickAction, GotoAction, HumanCheckpointAction
from webflow.domain.checkpoint import CheckpointReason
from webflow.domain.errors import (
    AgentError,
    AgentStuckError,
    BudgetExceededError,
    GuardViolationError,
)
from webflow.domain.observation import InteractiveElement, PageObservation
from webflow.domain.selectors import Selector, SelectorKind, SelectorSet
from webflow.llm.base import ScriptedLLMClient


def _observation(*elements: InteractiveElement) -> PageObservation:
    return PageObservation(
        url="https://forsikringsguiden.dk/bilforsikring", elements=list(elements)
    )


def _button(index: int, name: str) -> InteractiveElement:
    return InteractiveElement(index=index, tag="button", role="button", name=name)


# --------------------------------------------------------------------- guards


def test_registrable_domain_ignores_subdomains() -> None:
    assert registrable_domain("www.forsikringsguiden.dk") == "forsikringsguiden.dk"


def test_guards_block_navigation_off_site() -> None:
    guards = Guards.for_site("https://forsikringsguiden.dk")
    with pytest.raises(GuardViolationError):
        guards.check(GotoAction(url="https://evil.example.com"), _observation())


def test_guards_allow_subdomains_of_the_site() -> None:
    guards = Guards.for_site("https://forsikringsguiden.dk")
    action = GotoAction(url="https://www.forsikringsguiden.dk/bilforsikring")
    assert guards.check(action, _observation()) is action


def test_irreversible_click_becomes_an_approval_checkpoint() -> None:
    guards = Guards.for_site("https://forsikringsguiden.dk")
    action = ClickAction(
        target=SelectorSet(
            candidates=[Selector(kind=SelectorKind.TEXT, value="Køb nu")],
            description="button:Køb nu",
        )
    )
    checked = guards.check(action, _observation())
    assert isinstance(checked, HumanCheckpointAction)
    assert checked.request.reason is CheckpointReason.APPROVAL


def test_ordinary_click_passes_through() -> None:
    guards = Guards.for_site("https://forsikringsguiden.dk")
    action = ClickAction(
        target=SelectorSet(
            candidates=[Selector(kind=SelectorKind.TEXT, value="Videre")],
            description="button:Videre",
        )
    )
    assert guards.check(action, _observation()) is action


def test_captcha_is_detected_from_page_text() -> None:
    observation = PageObservation(url="https://x.dk", text="Bekræft at du er et menneske")
    assert detect_captcha(observation) is not None


# ------------------------------------------------------------------- policies


def test_step_budget_is_enforced() -> None:
    policy = RunPolicy(AgentSettings(max_steps=1))
    policy.before_step()
    policy.steps = 1
    with pytest.raises(BudgetExceededError):
        policy.before_step()


def test_repeated_failures_declare_the_agent_stuck() -> None:
    policy = RunPolicy(AgentSettings(max_consecutive_failures=2))
    policy.record_failure("boom")
    with pytest.raises(AgentStuckError):
        policy.record_failure("boom")


def test_same_action_on_unchanged_page_is_a_loop() -> None:
    policy = RunPolicy(AgentSettings(loop_window=3))
    observation = _observation(_button(0, "Videre"))
    action = ClickAction(target=observation.elements[0].to_selector_set())
    policy.record_success(observation, action)
    policy.record_success(observation, action)
    with pytest.raises(AgentStuckError):
        policy.record_success(observation, action)


# --------------------------------------------------------------- action schema


def test_planned_click_resolves_to_the_indexed_element() -> None:
    observation = _observation(_button(0, "Videre"))
    action = to_action(PlannedAction(kind="click", element_index=0), observation)
    assert isinstance(action, ClickAction)
    assert action.target.candidates[0].name == "Videre"


def test_planned_action_with_unknown_index_is_rejected() -> None:
    with pytest.raises(AgentError):
        to_action(PlannedAction(kind="click", element_index=7), _observation())


def test_fill_without_a_value_is_rejected() -> None:
    observation = _observation(
        InteractiveElement(index=0, tag="input", role="textbox", name="E-mail")
    )
    with pytest.raises(AgentError):
        to_action(PlannedAction(kind="fill", element_index=0), observation)


def test_planner_decision_rejects_fill_and_pick_without_a_value() -> None:
    with pytest.raises(ValidationError, match="needs profile_key, answer_key or literal_value"):
        PlannerDecision(action=PlannedAction(kind="fill_and_pick", element_index=5))


def test_planner_decision_rejects_click_without_an_element() -> None:
    with pytest.raises(ValidationError, match="requires element_index"):
        PlannerDecision(action=PlannedAction(kind="click"))


async def test_planner_retries_fill_and_pick_without_a_value() -> None:
    observation = _observation(
        InteractiveElement(index=5, tag="input", role="textbox", name="Adresse")
    )
    malformed = {
        "page_summary": "Address is required",
        "reasoning": "Use the address autocomplete",
        "action": {"kind": "fill_and_pick", "element_index": 5},
    }
    corrected = {
        **malformed,
        "action": {
            "kind": "fill_and_pick",
            "element_index": 5,
            "profile_key": "person.address",
        },
    }
    llm = ScriptedLLMClient([json.dumps(malformed), json.dumps(corrected)])
    planner = Planner(llm)

    action, decision = await planner.next_action(
        observation,
        PlannerContext(
            goal="bilforsikring",
            goal_description="Get car insurance quotes",
            provider_name="Forsikringsguiden",
        ),
        [],
    )

    assert isinstance(action, FillAndPickAction)
    assert action.value.profile_key == "person.address"
    assert decision.action.profile_key == "person.address"
    assert len(llm.prompts) == 2
    assert "needs profile_key, answer_key or literal_value" in llm.prompts[1][1]


def test_ask_human_carries_page_context() -> None:
    observation = PageObservation(url="https://x.dk", title="T", text="hello", elements=[])
    action = to_action(
        PlannedAction(
            kind="ask_human",
            reason=CheckpointReason.MISSING_PROFILE_DATA,
            question="How many km per year?",
        ),
        observation,
    )
    assert isinstance(action, HumanCheckpointAction)
    assert action.request.url == "https://x.dk"
    assert action.request.page_excerpt == "hello"
