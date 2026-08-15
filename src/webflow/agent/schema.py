"""The LLM-facing action schema.

Deliberately flatter than the domain :class:`Action` union: the planner refers to
elements by the index it can see in the observation, and to values by profile
key. Translation into a domain action - including building a robust
:class:`SelectorSet` - happens here, locally, so the model never handles
selectors or personal data.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from webflow.domain.actions import (
    Action,
    CheckAction,
    ClickAction,
    DoneAction,
    ExtractAction,
    FillAction,
    FillAndPickAction,
    HumanCheckpointAction,
    PressAction,
    ScrollAction,
    SelectAction,
    WaitAction,
)
from webflow.domain.checkpoint import AnswerField, CheckpointReason, CheckpointRequest
from webflow.domain.errors import AgentError
from webflow.domain.observation import PageObservation
from webflow.domain.values import ValueSource

PlannedKind = Literal[
    "click",
    "fill",
    "fill_and_pick",
    "select",
    "check",
    "uncheck",
    "press",
    "scroll",
    "wait",
    "extract",
    "ask_human",
    "done",
]

#: Kinds that must name an element from the observation.
_NEEDS_ELEMENT = frozenset(
    {"click", "fill", "fill_and_pick", "select", "check", "uncheck"}
)
#: Kinds that must supply a value.
_NEEDS_VALUE = frozenset({"fill", "fill_and_pick", "select"})


class PlannedAction(BaseModel):
    """What the planner asks for, before it becomes a domain action."""

    kind: PlannedKind
    """Index from the INTERACTIVE ELEMENTS list of the current observation."""
    element_index: int | None = None
    """Dotted profile path, preferred over a literal so the flow stays reusable."""
    profile_key: str | None = None
    """Key of a previously human-supplied answer."""
    answer_key: str | None = None
    """Only for values that are genuinely page-specific, never personal data."""
    literal_value: str | None = None
    key: str | None = None
    schema_name: str | None = None

    reason: CheckpointReason | None = None
    question: str | None = None
    fields: list[AnswerField] = Field(default_factory=list)

    success: bool = True
    summary: str | None = None


class PlannerDecision(BaseModel):
    """One planner turn."""

    """What the page is currently asking for, in one sentence."""
    page_summary: str = ""
    """Why this action moves the goal forward."""
    reasoning: str = ""
    action: PlannedAction

    @model_validator(mode="after")
    def _action_has_required_fields(self) -> Self:
        action = self.action
        if action.kind in _NEEDS_ELEMENT and action.element_index is None:
            raise ValueError(f"{action.kind!r} requires element_index")
        if action.kind in _NEEDS_VALUE and not any(
            (action.profile_key, action.answer_key, action.literal_value)
        ):
            raise ValueError(
                f"{action.kind!r} needs profile_key, answer_key or literal_value"
            )
        return self


class DemonstrationReview(BaseModel):
    """The planner's verdict on a human take-over, so it can be folded into the flow."""

    """Indexes (into the demonstrated action list) worth keeping in the learned flow."""
    keep_indexes: list[int] = Field(default_factory=list)
    """Why these actions were kept or dropped."""
    reasoning: str = ""
    """One line to store alongside the flow, e.g. 'human picked a different bank'."""
    flow_note: str = ""


def _value_source(planned: PlannedAction) -> ValueSource:
    if not any((planned.profile_key, planned.answer_key, planned.literal_value)):
        raise AgentError(f"{planned.kind!r} needs profile_key, answer_key or literal_value")
    return ValueSource(
        profile_key=planned.profile_key,
        answer_key=planned.answer_key,
        literal=planned.literal_value,
    )


def to_action(planned: PlannedAction, observation: PageObservation) -> Action:
    """Translate a planner decision into an executable, recordable action."""
    element = None
    if planned.kind in _NEEDS_ELEMENT:
        if planned.element_index is None:
            raise AgentError(f"{planned.kind!r} requires element_index")
        element = observation.by_index(planned.element_index)
        if element is None:
            raise AgentError(f"No element with index {planned.element_index} in this observation")
    if planned.kind in _NEEDS_VALUE:
        _value_source(planned)

    match planned.kind:
        case "click":
            assert element is not None
            return ClickAction(target=element.to_selector_set(), reasoning=planned.summary)
        case "fill":
            assert element is not None
            return FillAction(
                target=element.to_selector_set(),
                value=_value_source(planned),
                reasoning=planned.summary,
            )
        case "fill_and_pick":
            assert element is not None
            return FillAndPickAction(
                target=element.to_selector_set(),
                value=_value_source(planned),
                reasoning=planned.summary,
            )
        case "select":
            assert element is not None
            return SelectAction(
                target=element.to_selector_set(),
                value=_value_source(planned),
                reasoning=planned.summary,
            )
        case "check" | "uncheck":
            assert element is not None
            return CheckAction(
                target=element.to_selector_set(),
                checked=planned.kind == "check",
                reasoning=planned.summary,
            )
        case "press":
            return PressAction(key=planned.key or "Enter", reasoning=planned.summary)
        case "scroll":
            return ScrollAction(reasoning=planned.summary)
        case "wait":
            return WaitAction(timeout_ms=1_500, reasoning=planned.summary)
        case "extract":
            return ExtractAction(
                schema_name=planned.schema_name or "default", reasoning=planned.summary
            )
        case "ask_human":
            return HumanCheckpointAction(
                request=CheckpointRequest(
                    reason=planned.reason or CheckpointReason.AMBIGUOUS_QUESTION,
                    question=planned.question or "The agent needs help to continue.",
                    fields=planned.fields,
                    url=observation.url,
                    page_title=observation.title,
                    page_excerpt=observation.text[:1_500],
                ),
                reasoning=planned.summary,
            )
        case "done":
            return DoneAction(success=planned.success, summary=planned.summary or "")
        case _:
            raise AgentError(f"Unsupported planned action {planned.kind!r}")
