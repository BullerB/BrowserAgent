"""The action vocabulary shared by the planner, the recorder and the player.

Anything the agent can do, a recorded flow can replay, and vice versa - there is
exactly one execution path (``webflow.browser.executor``) for both.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from webflow.domain.checkpoint import CheckpointRequest
from webflow.domain.selectors import SelectorSet
from webflow.domain.values import ValueSource


class BaseAction(BaseModel):
    """Fields every action shares."""

    """Why the planner chose this; kept in recorded flows as documentation."""
    reasoning: str | None = None
    """Extra settle time after the action, for sites that recalculate async."""
    wait_after_ms: int = 0
    """When true, replay logs and continues instead of failing if the target is gone."""
    optional: bool = False


class GotoAction(BaseAction):
    type: Literal["goto"] = "goto"
    url: str
    wait_until: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded"


class ClickAction(BaseAction):
    type: Literal["click"] = "click"
    target: SelectorSet


class FillAction(BaseAction):
    type: Literal["fill"] = "fill"
    target: SelectorSet
    value: ValueSource
    """Type character by character; needed by fields with keystroke listeners."""
    sequential: bool = False


class FillAndPickAction(BaseAction):
    """Type into an autocomplete field, then choose from the suggestion list.

    Needed by address widgets (forsikringsguiden's ``autoInput``), where typing
    the full address is not enough - a suggestion must actually be selected.
    """

    type: Literal["fill_and_pick"] = "fill_and_pick"
    target: SelectorSet
    value: ValueSource
    suggestion: SelectorSet | None = None
    suggestion_index: int = 0
    suggestion_timeout_ms: int = 5_000


class SelectAction(BaseAction):
    type: Literal["select"] = "select"
    target: SelectorSet
    value: ValueSource
    by: Literal["label", "value", "index"] = "label"


class CheckAction(BaseAction):
    type: Literal["check"] = "check"
    target: SelectorSet
    checked: bool = True


class PressAction(BaseAction):
    type: Literal["press"] = "press"
    key: str
    target: SelectorSet | None = None


class UploadAction(BaseAction):
    type: Literal["upload"] = "upload"
    target: SelectorSet
    path: ValueSource


class ScrollAction(BaseAction):
    type: Literal["scroll"] = "scroll"
    target: SelectorSet | None = None
    direction: Literal["down", "up", "top", "bottom"] = "down"
    amount_px: int = 600


class WaitAction(BaseAction):
    type: Literal["wait"] = "wait"
    target: SelectorSet | None = None
    state: Literal["visible", "hidden", "attached", "detached"] = "visible"
    timeout_ms: int = 10_000


class ExtractAction(BaseAction):
    """Signals that the goal page has been reached and results can be harvested."""

    type: Literal["extract"] = "extract"
    schema_name: str


class HumanCheckpointAction(BaseAction):
    """Suspend the run and ask a human.

    On replay this is satisfied automatically from the answer bank when the same
    question has been answered before.
    """

    type: Literal["human_checkpoint"] = "human_checkpoint"
    request: CheckpointRequest


class DoneAction(BaseAction):
    type: Literal["done"] = "done"
    success: bool = True
    summary: str = ""


Action = Annotated[
    GotoAction
    | ClickAction
    | FillAction
    | FillAndPickAction
    | SelectAction
    | CheckAction
    | PressAction
    | UploadAction
    | ScrollAction
    | WaitAction
    | ExtractAction
    | HumanCheckpointAction
    | DoneAction,
    Field(discriminator="type"),
]

#: Actions that address a specific element and therefore need locator resolution.
TARGETED_ACTION_TYPES = frozenset(
    {"click", "fill", "fill_and_pick", "select", "check", "upload"}
)

#: Actions that only affect the agent loop and are never replayed against a page.
CONTROL_ACTION_TYPES = frozenset({"extract", "human_checkpoint", "done"})


def action_target(action: BaseAction) -> SelectorSet | None:
    return getattr(action, "target", None)
