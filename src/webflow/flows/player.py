"""Deterministic replay of a cached flow.

Replay is the fast path: no LLM, no page reasoning, just the recorded actions.
Two hooks make it survivable - ``on_control`` decides what happens at a recorded
human checkpoint, and ``repair`` gets a chance to rewrite a step whose element
has moved.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from webflow.browser import locators
from webflow.browser.executor import ActionExecutor
from webflow.domain.actions import Action
from webflow.domain.errors import FlowPlaybackError
from webflow.domain.flow import Flow, FlowStep
from webflow.domain.run import ExecutedStep, RunMode, StepStatus
from webflow.logging import get_logger

log = get_logger(__name__)

#: Return a replacement action, or None to give up on the step.
RepairHandler = Callable[[FlowStep, Exception], Awaitable[Action | None]]
#: Called for recorded control steps; may raise HumanInterventionRequired.
ControlHandler = Callable[[FlowStep], Awaitable[None]]


@dataclass
class PlaybackReport:
    executed: list[ExecutedStep] = field(default_factory=list)
    """Steps rewritten by the repair handler, keyed by step index."""
    repairs: dict[int, Action] = field(default_factory=dict)
    """Steps whose selector order changed because a later strategy won."""
    promotions: dict[int, Action] = field(default_factory=dict)
    extract_schema: str | None = None
    completed: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.repairs or self.promotions)


class FlowPlayer:
    def __init__(
        self,
        executor: ActionExecutor,
        *,
        repair: RepairHandler | None = None,
        on_control: ControlHandler | None = None,
        promote_selectors: bool = True,
    ) -> None:
        self._executor = executor
        self._repair = repair
        self._on_control = on_control
        self._promote = promote_selectors

    async def play(self, flow: Flow) -> PlaybackReport:
        report = PlaybackReport()

        for step in flow.steps:
            if step.action.type in {"human_checkpoint", "extract", "done"}:
                await self._handle_control(step, report)
                if step.action.type == "done":
                    break
                continue

            executed = await self._run_step(step, report)
            report.executed.append(executed)

        report.completed = True
        return report

    async def _handle_control(self, step: FlowStep, report: PlaybackReport) -> None:
        if step.action.type == "extract":
            report.extract_schema = step.action.schema_name
        if self._on_control is not None:
            await self._on_control(step)
        report.executed.append(
            ExecutedStep(
                index=step.index,
                action=step.action,
                status=StepStatus.OK,
                mode=RunMode.REPLAY,
                url_after=self._executor.page.url,
            )
        )

    async def _run_step(self, step: FlowStep, report: PlaybackReport) -> ExecutedStep:
        url_before = self._executor.page.url
        try:
            outcome = await self._executor.execute(step.action)
        except Exception as exc:
            return await self._attempt_repair(step, exc, report, url_before)

        action = step.action
        if self._promote and outcome.selector_used is not None:
            target = getattr(action, "target", None)
            if target is not None and target.candidates[0] != outcome.selector_used:
                action = action.model_copy(
                    update={"target": locators.promote(target, outcome.selector_used)}
                )
                report.promotions[step.index] = action

        return ExecutedStep(
            index=step.index,
            action=action,
            status=StepStatus.SKIPPED if outcome.skipped else StepStatus.OK,
            mode=RunMode.REPLAY,
            url_before=url_before,
            url_after=outcome.url_after,
            duration_ms=outcome.duration_ms,
            error=outcome.note if outcome.skipped else None,
        )

    async def _attempt_repair(
        self,
        step: FlowStep,
        error: Exception,
        report: PlaybackReport,
        url_before: str,
    ) -> ExecutedStep:
        log.warning(
            "replay_step_failed", step=step.index, action=step.action.type, error=str(error)
        )
        if self._repair is None:
            raise FlowPlaybackError(str(error), step.index, url_before) from error

        replacement = await self._repair(step, error)
        if replacement is None:
            raise FlowPlaybackError(str(error), step.index, url_before) from error

        try:
            outcome = await self._executor.execute(replacement)
        except Exception as exc:
            raise FlowPlaybackError(
                f"repair also failed: {exc}", step.index, url_before
            ) from exc

        report.repairs[step.index] = replacement
        log.info("replay_step_repaired", step=step.index, action=replacement.type)
        return ExecutedStep(
            index=step.index,
            action=replacement,
            status=StepStatus.REPAIRED,
            mode=RunMode.REPAIR,
            url_before=url_before,
            url_after=outcome.url_after,
            duration_ms=outcome.duration_ms,
        )
