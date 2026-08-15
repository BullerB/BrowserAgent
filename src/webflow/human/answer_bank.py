"""The answer bank: remember what a human said, so we only ask once.

This is what turns a supervised first run into an unattended second one. Every
answer is stored under the question's fingerprint; when the same question comes
back - on a later run, or on a different day - it is satisfied automatically.
"""

from __future__ import annotations

from webflow.domain.checkpoint import CheckpointRequest, HumanAnswer
from webflow.logging import get_logger
from webflow.persistence.repository import AnswerRepository

log = get_logger(__name__)


class AnswerBank:
    def __init__(self, repository: AnswerRepository) -> None:
        self._repository = repository

    async def try_answer(self, request: CheckpointRequest) -> HumanAnswer | None:
        """Auto-answer a checkpoint from history, when it is safe to do so."""
        if request.reason.needs_live_browser:
            return None
        reusable = [f for f in request.fields if f.reusable]
        if not reusable:
            return None

        known = await self._repository.lookup(request.fingerprint)
        missing = [f.key for f in reusable if f.required and f.key not in known]
        if missing:
            return None

        log.info("checkpoint_auto_answered", question=request.question[:80])
        return HumanAnswer(
            checkpoint_id=request.id,
            values={f.key: known[f.key] for f in reusable if f.key in known},
            note="auto-answered from answer bank",
        )

    async def remember(
        self, provider_id: str, request: CheckpointRequest, answer: HumanAnswer
    ) -> None:
        reusable = {f.key: f for f in request.fields if f.reusable}
        values = {k: v for k, v in answer.values.items() if k in reusable}
        if not values:
            return
        await self._repository.remember(
            provider_id=provider_id,
            fingerprint=request.fingerprint,
            question=request.question,
            values=values,
            profile_keys={k: reusable[k].profile_key for k in values},
        )

    @staticmethod
    def profile_updates(request: CheckpointRequest, answer: HumanAnswer) -> dict[str, str]:
        """Answers that belong in the profile file rather than just the bank."""
        return {
            field.profile_key: answer.values[field.key]
            for field in request.fields
            if field.profile_key and field.reusable and field.key in answer.values
        }

    async def forget(self, request: CheckpointRequest) -> None:
        await self._repository.forget(request.fingerprint)
