"""Rebuilding a suspended run.

Nothing about the original browser survives a suspension, so resuming means:
restore cookies/localStorage, replay the actions that already succeeded, and
hand back a page sitting where the human was asked the question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from webflow.browser.executor import ActionExecutor
from webflow.browser.session import BrowserSession
from webflow.config import BrowserSettings
from webflow.domain.errors import WebflowError
from webflow.domain.run import RunState
from webflow.domain.values import ValueContext
from webflow.logging import get_logger

log = get_logger(__name__)


class ResumeError(WebflowError):
    """The recorded prefix could not be replayed, so the run must restart."""

    def __init__(self, step_index: int, reason: str) -> None:
        super().__init__(f"Could not fast-forward past step {step_index}: {reason}")
        self.step_index = step_index


@dataclass(slots=True)
class FastForwardResult:
    replayed: int
    landed_url: str
    """True when replay ended somewhere other than where the run was suspended."""
    drifted: bool = False
    warnings: list[str] = field(default_factory=list)


def _same_page(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    pa, pb = urlparse(a), urlparse(b)
    return (pa.netloc, pa.path.rstrip("/")) == (pb.netloc, pb.path.rstrip("/"))


async def fast_forward(
    session: BrowserSession,
    run: RunState,
    values: ValueContext,
    *,
    settle_ms: int = 200,
) -> FastForwardResult:
    """Re-execute the successful prefix of a run against a fresh browser."""
    executor = ActionExecutor(session.page, values, settle_ms=settle_ms)
    actions = run.replayable_actions()

    if not actions:
        if not run.last_url:
            raise ResumeError(0, "run has neither a trajectory nor a last URL")
        await session.page.goto(run.last_url)
        return FastForwardResult(replayed=0, landed_url=session.page.url)

    warnings: list[str] = []
    for index, action in enumerate(actions):
        try:
            outcome = await executor.execute(action)
            if outcome.skipped:
                warnings.append(f"step {index} ({action.type}) skipped during fast-forward")
        except Exception as exc:
            raise ResumeError(index, f"{action.type}: {exc}") from exc

    landed = session.page.url
    drifted = not _same_page(landed, run.last_url)
    if drifted:
        warnings.append(f"expected to land on {run.last_url}, ended on {landed}")
        log.warning("fast_forward_drift", run_id=run.id, expected=run.last_url, actual=landed)

    return FastForwardResult(
        replayed=len(actions), landed_url=landed, drifted=drifted, warnings=warnings
    )


async def rehydrate(
    run: RunState,
    values: ValueContext,
    *,
    browser_settings: BrowserSettings | None = None,
    headless: bool | None = None,
) -> tuple[BrowserSession, FastForwardResult]:
    """Open a browser restored to the point where the run was suspended.

    A checkpoint the human must solve in the browser (captcha, MFA, login) forces
    a headed window; everything else can resume headless.
    """
    if run.pending_checkpoint is not None and run.pending_checkpoint.reason.needs_live_browser:
        headless = False

    session = BrowserSession(
        browser_settings, storage_state=run.storage_state, headless=headless
    )
    await session.start()
    try:
        result = await fast_forward(session, run, values)
    except Exception:
        await session.close()
        raise
    log.info("run_rehydrated", run_id=run.id, replayed=result.replayed, url=result.landed_url)
    return session, result
