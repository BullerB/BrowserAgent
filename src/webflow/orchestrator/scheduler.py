"""Run several provider goals at once.

Concurrency is bounded by a semaphore: browsers are expensive, and hammering a
comparison site with parallel sessions is both slow and rude.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from providers.registry import get_provider
from webflow.domain.run import RunStatus
from webflow.logging import get_logger
from webflow.orchestrator.runner import GoalRunner, RunOutcome
from webflow.orchestrator.services import Services
from webflow.profiles import Profile

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GoalRequest:
    provider_id: str
    goal: str


@dataclass
class BatchResult:
    outcomes: list[RunOutcome] = field(default_factory=list)
    """Targets that raised before a run could even be created."""
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def completed(self) -> list[RunOutcome]:
        return [o for o in self.outcomes if o.succeeded]

    @property
    def awaiting_human(self) -> list[RunOutcome]:
        return [o for o in self.outcomes if o.awaiting_human]

    @property
    def failed(self) -> list[RunOutcome]:
        return [o for o in self.outcomes if o.run.status is RunStatus.FAILED]

    @property
    def error_count(self) -> int:
        return len(self.failed) + len(self.failures)

    def summary(self) -> str:
        lines = [
            f"{len(self.completed)} completed, {len(self.awaiting_human)} awaiting a human, "
            f"{self.error_count} failed"
        ]
        for outcome in self.completed:
            records = len(outcome.results.records) if outcome.results else 0
            lines.append(
                f"  ok: {outcome.run.provider_id}/{outcome.run.goal} - {records} records"
                f" (flow v{outcome.flow_version}, {outcome.run.llm_calls} LLM calls)"
            )
        for outcome in self.awaiting_human:
            assert outcome.pending is not None
            fields = ", ".join(f.key for f in outcome.pending.fields) or "-"
            lines.append(
                f"  waiting: run {outcome.run.id} ({outcome.run.provider_id}/"
                f"{outcome.run.goal}) - {outcome.pending.question} | fields: {fields}"
            )
        for outcome in self.failed:
            lines.append(
                f"  failed: {outcome.run.provider_id}/{outcome.run.goal} - {outcome.run.error}"
            )
        for key, error in self.failures.items():
            lines.append(f"  failed: {key} - {error}")
        return "\n".join(lines)


async def run_goals(
    requests: list[GoalRequest],
    services: Services,
    *,
    profile: Profile | None = None,
    headless: bool | None = None,
    max_concurrency: int | None = None,
    interactive: bool = False,
) -> BatchResult:
    """Fan out across providers; partial success is the expected case."""
    limit = max_concurrency or services.settings.max_concurrent_providers
    semaphore = asyncio.Semaphore(limit)
    result = BatchResult()

    async def one(request: GoalRequest) -> RunOutcome | None:
        key = f"{request.provider_id}/{request.goal}"
        async with semaphore:
            try:
                runner = GoalRunner(
                    get_provider(request.provider_id),
                    request.goal,
                    services,
                    profile=profile,
                    headless=headless,
                    interactive=interactive,
                )
                return await runner.start()
            except Exception as exc:
                log.error("goal_failed", goal=key, error=str(exc))
                result.failures[key] = str(exc)
                return None

    for outcome in await asyncio.gather(*(one(r) for r in requests)):
        if outcome is not None:
            result.outcomes.append(outcome)

    log.info(
        "batch_finished",
        completed=len(result.completed),
        awaiting=len(result.awaiting_human),
        failed=result.error_count,
    )
    return result


async def resume_run(
    run_id: str,
    services: Services,
    *,
    profile: Profile | None = None,
    headless: bool | None = None,
    interactive: bool = False,
) -> RunOutcome:
    """Continue a run whose checkpoint has been answered."""
    run = await services.runs.get(run_id)
    if run is None:
        raise KeyError(f"Unknown run {run_id!r}")
    runner = GoalRunner(
        get_provider(run.provider_id),
        run.goal,
        services,
        profile=profile,
        headless=headless,
        interactive=interactive,
    )
    return await runner.resume(run_id)
