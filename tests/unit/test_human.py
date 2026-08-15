from __future__ import annotations

from webflow.config import Settings
from webflow.domain.checkpoint import (
    AnswerField,
    CheckpointReason,
    CheckpointRequest,
    HumanAnswer,
)
from webflow.domain.run import RunState, RunStatus
from webflow.human.answer_bank import AnswerBank
from webflow.human.queue import InterventionQueue
from webflow.persistence.db import Database
from webflow.persistence.repository import AnswerRepository, CheckpointRepository, RunRepository
from webflow.profiles import ProfileStore


def _request(question: str = "How many km per year?") -> CheckpointRequest:
    return CheckpointRequest(
        reason=CheckpointReason.MISSING_PROFILE_DATA,
        question=question,
        fields=[
            AnswerField(
                key="annual_km",
                prompt="Kilometres driven per year",
                type="number",
                profile_key="vehicle.annual_km",
            )
        ],
    )


def test_fingerprint_ignores_volatile_context() -> None:
    a = _request()
    b = _request()
    b.url = "https://forsikringsguiden.dk/step/4?session=abc"
    b.screenshot_path = "/tmp/x.png"
    assert a.fingerprint == b.fingerprint
    assert a.id != b.id


def test_fingerprint_changes_with_the_question() -> None:
    assert _request().fingerprint != _request("Something else?").fingerprint


def test_reasons_that_need_a_live_browser_are_flagged() -> None:
    assert CheckpointReason.CAPTCHA.needs_live_browser
    assert CheckpointReason.MFA.needs_live_browser
    assert not CheckpointReason.MISSING_PROFILE_DATA.needs_live_browser


async def test_answer_bank_reuses_a_previous_answer(settings: Settings) -> None:
    db = Database(settings)
    bank = AnswerBank(AnswerRepository(db))
    request = _request()

    assert await bank.try_answer(request) is None

    await bank.remember(
        "forsikringsguiden",
        request,
        HumanAnswer(checkpoint_id=request.id, values={"annual_km": "15000"}),
    )
    auto = await bank.try_answer(_request())
    assert auto is not None
    assert auto.values == {"annual_km": "15000"}
    await db.dispose()


async def test_captcha_answers_are_never_auto_replayed(settings: Settings) -> None:
    db = Database(settings)
    bank = AnswerBank(AnswerRepository(db))
    request = CheckpointRequest(
        reason=CheckpointReason.CAPTCHA,
        question="Solve the captcha",
        fields=[AnswerField(key="done", prompt="ok")],
    )
    await bank.remember(
        "x", request, HumanAnswer(checkpoint_id=request.id, values={"done": "yes"})
    )
    assert await bank.try_answer(request) is None
    await db.dispose()


async def test_one_shot_answers_are_not_remembered(settings: Settings) -> None:
    db = Database(settings)
    bank = AnswerBank(AnswerRepository(db))
    request = CheckpointRequest(
        reason=CheckpointReason.MFA,
        question="SMS code?",
        fields=[AnswerField(key="code", prompt="code", reusable=False)],
    )
    await bank.remember(
        "x", request, HumanAnswer(checkpoint_id=request.id, values={"code": "1234"})
    )
    assert await AnswerRepository(db).lookup(request.fingerprint) == {}
    await db.dispose()


async def test_answering_persists_to_bank_and_profile(settings: Settings) -> None:
    db = Database(settings)
    runs, checkpoints = RunRepository(db), CheckpointRepository(db)
    profiles = ProfileStore(settings.resolve_path(settings.profile_path))
    queue = InterventionQueue(runs, checkpoints, AnswerRepository(db), profiles)

    run = RunState(provider_id="forsikringsguiden", goal="bilforsikring")
    request = _request()
    await runs.save(run)
    await queue.suspend(run, request)

    pending = await queue.pending()
    assert [p.run_id for p in pending] == [run.id]
    assert "km per year" in pending[0].describe()

    resumed = await queue.answer(run.id, {"annual_km": "15000"})
    assert resumed.status is RunStatus.PENDING
    assert resumed.answers == {"annual_km": "15000"}
    assert resumed.pending_checkpoint is None
    assert request.fingerprint in resumed.resolved_checkpoints

    assert await queue.pending() == []
    assert profiles.load().vehicle.annual_km == "15000"
    await db.dispose()


async def test_aborting_terminates_the_run(settings: Settings) -> None:
    db = Database(settings)
    runs, checkpoints = RunRepository(db), CheckpointRepository(db)
    queue = InterventionQueue(runs, checkpoints, AnswerRepository(db))

    run = RunState(provider_id="p", goal="g")
    await runs.save(run)
    await queue.suspend(run, _request())

    aborted = await queue.answer(run.id, aborted=True)
    assert aborted.status is RunStatus.ABORTED
    await db.dispose()
