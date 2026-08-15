"""Self-healing: ask the planner to rewrite a step whose element has moved.

This is what keeps cached flows alive across site redesigns. Only the broken
step is re-planned - the rest of the flow still replays without an LLM.
"""

from __future__ import annotations

from playwright.async_api import Page

from webflow.agent.planner import Planner, PlannerContext
from webflow.browser.observer import observe_stable
from webflow.domain.actions import Action
from webflow.domain.flow import Flow, FlowStep
from webflow.logging import get_logger

log = get_logger(__name__)


def describe_step(step: FlowStep) -> str:
    action = step.action
    target = getattr(action, "target", None)
    value = getattr(action, "value", None)
    parts = [f"{action.type}"]
    if target is not None:
        parts.append(f"on {target}")
    if value is not None:
        parts.append(f"with {value.describe()}")
    if step.note:
        parts.append(f"({step.note})")
    return " ".join(parts)


class StepRepairer:
    """Bound to one playback attempt; enforces a repair budget."""

    def __init__(
        self,
        planner: Planner,
        page: Page,
        context: PlannerContext,
        *,
        max_repairs: int = 3,
    ) -> None:
        self._planner = planner
        self._page = page
        self._context = context
        self._max_repairs = max_repairs
        self.repairs_used = 0

    async def __call__(self, step: FlowStep, error: Exception) -> Action | None:
        if self.repairs_used >= self._max_repairs:
            log.warning("repair_budget_exhausted", step=step.index)
            return None
        self.repairs_used += 1

        observation = await observe_stable(self._page, settle_ms=500)
        replacement = await self._planner.repair_step(
            observation,
            self._context,
            step_index=step.index,
            step_description=describe_step(step),
            error=str(error),
        )
        if replacement.type != step.action.type:
            log.info(
                "repair_changed_action_kind",
                step=step.index,
                was=step.action.type,
                now=replacement.type,
            )
        return replacement


def apply_repairs(
    flow: Flow, repairs: dict[int, Action], promotions: dict[int, Action]
) -> Flow | None:
    """Fold playback fixes back into the flow, returning a new version.

    Returns ``None`` when nothing changed, so callers can skip writing a file.
    """
    if not repairs and not promotions:
        return None

    updated = flow
    for index, action in repairs.items():
        updated = updated.with_replaced_step(index, action, note=f"self-healed step {index}")

    if promotions:
        steps = [s.model_copy(deep=True) for s in updated.steps]
        for index, action in promotions.items():
            if index < len(steps):
                steps[index] = steps[index].model_copy(update={"action": action})
        updated = updated.model_copy(update={"steps": steps})
        if not repairs:
            updated = updated.model_copy(
                update={
                    "version": updated.version + 1,
                    "notes": [*updated.notes, "promoted selectors that resolved during replay"],
                }
            )

    return updated
