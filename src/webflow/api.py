"""The library's front door.

Three verbs are enough to use the whole system:

    outcomes = await gather(["forsikringsguiden/bilforsikring"])
    questions = await pending()
    outcome = await answer(run_id, {"annual_km": "15000"})

Everything else is an implementation detail of those three.
"""

from __future__ import annotations

from webflow.domain.run import RunState
from webflow.human.queue import PendingIntervention
from webflow.logging import configure_from_settings
from webflow.orchestrator.runner import RunOutcome
from webflow.orchestrator.scheduler import BatchResult, GoalRequest, resume_run, run_goals
from webflow.orchestrator.services import Services
from webflow.profiles import Profile


def _parse(target: str) -> GoalRequest:
    provider_id, _, goal = target.partition("/")
    if not goal:
        raise ValueError(f"Expected '<provider>/<goal>', got {target!r}")
    return GoalRequest(provider_id=provider_id, goal=goal)


async def gather(
    targets: list[str],
    *,
    services: Services | None = None,
    profile: Profile | None = None,
    headless: bool | None = None,
) -> BatchResult:
    """Run several ``provider/goal`` targets concurrently.

    Targets that need a human come back in ``result.awaiting_human`` rather than
    blocking the others.
    """
    owned = services is None
    services = services or Services.create()
    configure_from_settings(services.settings)
    try:
        return await run_goals(
            [_parse(t) for t in targets],
            services,
            profile=profile,
            headless=headless,
        )
    finally:
        if owned:
            await services.aclose()


async def pending(
    provider_id: str | None = None, *, services: Services | None = None
) -> list[PendingIntervention]:
    """Questions currently waiting for a human, across all suspended runs."""
    owned = services is None
    services = services or Services.create()
    try:
        return await services.queue.pending(provider_id)
    finally:
        if owned:
            await services.aclose()


async def answer(
    run_id: str,
    values: dict[str, str] | None = None,
    *,
    services: Services | None = None,
    profile: Profile | None = None,
    solved_in_browser: bool = False,
    aborted: bool = False,
    resume: bool = True,
    headless: bool | None = None,
) -> RunOutcome:
    """Answer the question blocking a run and, by default, continue it.

    Reusable answers are also written back to the profile, so the same question
    is never asked again.
    """
    owned = services is None
    services = services or Services.create()
    configure_from_settings(services.settings)
    try:
        run: RunState = await services.queue.answer(
            run_id,
            values,
            solved_in_browser=solved_in_browser,
            aborted=aborted,
        )
        if aborted or not resume:
            return RunOutcome(run=run)
        return await resume_run(run_id, services, profile=profile, headless=headless)
    finally:
        if owned:
            await services.aclose()
