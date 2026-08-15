"""The pending-intervention queue.

A suspended run is just a row in the database: nothing is held in memory, no
browser stays open, no socket times out. Answering is a separate, later call.
"""

from __future__ import annotations

from dataclasses import dataclass

from webflow.domain.checkpoint import CheckpointRequest, HumanAnswer
from webflow.domain.errors import WebflowError
from webflow.domain.run import RunState, RunStatus
from webflow.human.answer_bank import AnswerBank
from webflow.logging import get_logger
from webflow.persistence.repository import (
    AnswerRepository,
    CheckpointRepository,
    RunRepository,
)
from webflow.profiles import ProfileStore

log = get_logger(__name__)


@dataclass(slots=True)
class PendingIntervention:
    """A question waiting for a human, with the run it belongs to."""

    run_id: str
    provider_id: str
    goal: str
    request: CheckpointRequest

    def describe(self) -> str:
        fields = ", ".join(f.key for f in self.request.fields) or "-"
        return (
            f"[{self.run_id}] {self.provider_id}/{self.goal} "
            f"({self.request.reason.value}) {self.request.question} | fields: {fields}"
        )


class InterventionQueue:
    def __init__(
        self,
        runs: RunRepository,
        checkpoints: CheckpointRepository,
        answers: AnswerRepository,
        profiles: ProfileStore | None = None,
    ) -> None:
        self._runs = runs
        self._checkpoints = checkpoints
        self._bank = AnswerBank(answers)
        self._profiles = profiles

    @property
    def bank(self) -> AnswerBank:
        return self._bank

    async def suspend(self, run: RunState, request: CheckpointRequest) -> None:
        """Persist the run and the question, then let the caller close the browser."""
        run.suspend(request)
        await self._runs.save(run)
        await self._checkpoints.open(run, request)
        log.info(
            "run_suspended",
            run_id=run.id,
            reason=request.reason.value,
            question=request.question[:100],
        )

    async def pending(self, provider_id: str | None = None) -> list[PendingIntervention]:
        items: list[PendingIntervention] = []
        for run in await self._runs.list_awaiting_human(provider_id):
            if run.pending_checkpoint is not None:
                items.append(
                    PendingIntervention(
                        run_id=run.id,
                        provider_id=run.provider_id,
                        goal=run.goal,
                        request=run.pending_checkpoint,
                    )
                )
        return items

    async def answer(
        self,
        run_id: str,
        values: dict[str, str] | None = None,
        *,
        solved_in_browser: bool = False,
        aborted: bool = False,
        note: str | None = None,
    ) -> RunState:
        """Record a human's reply and return the run, ready to be resumed."""
        run = await self._runs.get(run_id)
        if run is None:
            raise WebflowError(f"Unknown run {run_id!r}")
        if run.pending_checkpoint is None:
            raise WebflowError(f"Run {run_id!r} is not waiting for an answer")

        request = run.pending_checkpoint
        answer = HumanAnswer(
            checkpoint_id=request.id,
            values=values or {},
            solved_in_browser=solved_in_browser,
            aborted=aborted,
            note=note,
        )
        await self._checkpoints.resolve(answer)
        await self._bank.remember(run.provider_id, request, answer)
        if self._profiles is not None:
            self._profiles.apply_updates(AnswerBank.profile_updates(request, answer))

        if aborted:
            run.finish(RunStatus.ABORTED, "aborted by human")
        else:
            apply_answer(run, request, answer)
        await self._runs.save(run)
        log.info("checkpoint_answered", run_id=run_id, aborted=aborted)
        return run


def apply_answer(run: RunState, request: CheckpointRequest, answer: HumanAnswer) -> None:
    """Fold an answer into the run so the next attempt has what it needs."""
    run.answers.update(answer.values)
    run.resolved_checkpoints.append(request.fingerprint)
    run.pending_checkpoint = None
    run.status = RunStatus.PENDING
