"""Turning a successful exploration into a replayable flow.

The recorder is intentionally lossy: failed attempts, dead ends and skipped
optional steps are dropped, so the cached flow is the *clean* path the agent
eventually found rather than a transcript of everything it tried.
"""

from __future__ import annotations

from webflow.domain.actions import Action, GotoAction
from webflow.domain.flow import Flow, FlowStep
from webflow.domain.run import ExecutedStep, RunState, StepStatus
from webflow.logging import get_logger

log = get_logger(__name__)

#: Control actions worth keeping: they tell replay when to ask a human and when
#: the results page has been reached.
_KEPT_CONTROL_TYPES = frozenset({"human_checkpoint", "extract"})


def _is_worth_keeping(step: ExecutedStep) -> bool:
    if step.status in {StepStatus.FAILED, StepStatus.SKIPPED}:
        return False
    return step.action.type != "done"


def _dedupe_navigation(actions: list[Action]) -> list[Action]:
    """Collapse repeated navigations to the same URL."""
    cleaned: list[Action] = []
    for action in actions:
        if (
            isinstance(action, GotoAction)
            and cleaned
            and isinstance(cleaned[-1], GotoAction)
            and cleaned[-1].url == action.url
        ):
            continue
        cleaned.append(action)
    return cleaned


def record_flow(
    run: RunState,
    *,
    start_url: str,
    version: int = 1,
    result_schema: str | None = None,
    note: str | None = None,
) -> Flow:
    """Build a flow from the trajectory of a run that reached its goal."""
    kept = [s for s in run.trajectory if _is_worth_keeping(s)]
    actions = _dedupe_navigation([s.action for s in kept])

    if not actions or not isinstance(actions[0], GotoAction):
        actions.insert(0, GotoAction(url=start_url))

    flow = Flow(
        provider_id=run.provider_id,
        goal=run.goal,
        version=version,
        start_url=start_url,
        result_schema=result_schema,
        steps=[
            FlowStep(index=i, action=a, note=getattr(a, "reasoning", None))
            for i, a in enumerate(actions)
        ],
        notes=[note or f"recorded from run {run.id}"],
        metadata={"run_id": run.id, "llm_calls": run.llm_calls},
    )
    log.info(
        "flow_recorded",
        flow=flow.slug,
        steps=len(flow.steps),
        dropped=len(run.trajectory) - len(kept),
        robustness=flow.robustness,
    )
    return flow


def summarise_control_steps(flow: Flow) -> list[str]:
    """Human-readable list of the points where this flow may stop for a human."""
    return [
        f"step {step.index}: {step.action.request.reason.value} - {step.action.request.question}"
        for step in flow.steps
        if step.action.type in _KEPT_CONTROL_TYPES and step.action.type == "human_checkpoint"
    ]
