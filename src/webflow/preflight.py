"""Configuration self-check.

Answers "will a real run work?" without doing one: verifies the browser, the
profile, the providers and - optionally - that the configured LLM actually
responds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel

from providers.registry import discover, get_provider
from webflow.config import Settings, get_settings
from webflow.domain.errors import WebflowError
from webflow.llm.base import LLMClient, NullLLMClient
from webflow.llm.registry import create_llm_client
from webflow.orchestrator.services import make_flow_store
from webflow.profiles import ProfileStore


class Level(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(slots=True)
class Check:
    level: Level
    name: str
    detail: str

    def __str__(self) -> str:
        mark = {Level.OK: "[ ok ]", Level.WARN: "[warn]", Level.FAIL: "[FAIL]"}[self.level]
        return f"{mark} {self.name}: {self.detail}"


@dataclass
class PreflightReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not any(c.level is Level.FAIL for c in self.checks)

    def __str__(self) -> str:
        verdict = "READY" if self.ready else "NOT READY"
        return "\n".join([*(str(c) for c in self.checks), "", f"=> {verdict}"])


class _Ping(BaseModel):
    ok: bool


async def _check_browser(report: PreflightReport, settings: Settings) -> None:
    from webflow.browser.session import BrowserSession

    session = BrowserSession(settings.browser, headless=True)
    try:
        await session.start()
        await session.close()
        report.checks.append(Check(Level.OK, "browser", "Chromium launches"))
    except Exception as exc:
        await session.close()
        report.checks.append(
            Check(
                Level.FAIL,
                "browser",
                f"{exc}. Run: python -m playwright install chromium",
            )
        )


async def _check_llm(report: PreflightReport, settings: Settings, probe: bool) -> LLMClient:
    try:
        llm = create_llm_client(settings.llm)
    except Exception as exc:
        report.checks.append(Check(Level.FAIL, "llm", str(exc)))
        return NullLLMClient()

    if isinstance(llm, NullLLMClient):
        report.checks.append(
            Check(
                Level.WARN,
                "llm",
                "not configured - cached flows still replay, but new sites cannot be "
                "explored. Set WEBFLOW_LLM__PROVIDER and WEBFLOW_LLM__API_KEY.",
            )
        )
        return llm

    if not probe:
        report.checks.append(
            Check(Level.OK, "llm", f"{settings.llm.provider}/{llm.model} configured (not called)")
        )
        return llm

    try:
        await llm.generate_structured(
            "You are a health check.", 'Reply with {"ok": true}', _Ping
        )
        report.checks.append(
            Check(Level.OK, "llm", f"{settings.llm.provider}/{llm.model} answered a live call")
        )
    except Exception as exc:
        report.checks.append(Check(Level.FAIL, "llm", f"{llm.model} call failed: {exc}"))
    return llm


def _check_profile(report: PreflightReport, settings: Settings, targets: list[str]) -> None:
    store = ProfileStore(settings.resolve_path(settings.profile_path))
    if not store.path.is_file():
        report.checks.append(
            Check(
                Level.WARN,
                "profile",
                f"{store.path} missing - the agent will ask you for every field. "
                "Copy profiles/profile.example.json to profiles/profile.json.",
            )
        )
        return

    try:
        profile = store.load()
    except Exception as exc:
        report.checks.append(Check(Level.FAIL, "profile", f"{store.path} is invalid: {exc}"))
        return

    report.checks.append(Check(Level.OK, "profile", f"loaded {store.path}"))

    for target in targets:
        provider_id, _, goal_name = target.partition("/")
        try:
            goal = get_provider(provider_id).goal(goal_name)
        except (WebflowError, KeyError):
            continue
        missing = profile.missing(goal.required_profile_keys)
        if missing:
            report.checks.append(
                Check(
                    Level.WARN,
                    f"profile:{target}",
                    f"missing {', '.join(missing)} - expect a checkpoint asking for these",
                )
            )
        else:
            report.checks.append(
                Check(Level.OK, f"profile:{target}", "all required fields present")
            )


def _check_targets(
    report: PreflightReport,
    settings: Settings,
    targets: list[str],
    *,
    llm_available: bool,
) -> None:
    known = discover()
    report.checks.append(
        Check(
            Level.OK if known else Level.FAIL,
            "providers",
            ", ".join(sorted(known)) or "none discovered",
        )
    )

    for target in targets:
        provider_id, _, goal_name = target.partition("/")
        try:
            provider = get_provider(provider_id)
            goal = provider.goal(goal_name)
        except (WebflowError, KeyError) as exc:
            report.checks.append(Check(Level.FAIL, f"target:{target}", str(exc)))
            continue

        flow = make_flow_store(provider.flows_dir, settings).latest(provider_id, goal_name)
        if flow is None:
            # Nothing cached and nothing to think with means this target cannot run.
            report.checks.append(
                Check(
                    Level.WARN if llm_available else Level.FAIL,
                    f"target:{target}",
                    f"no cached flow yet - the first run must explore {goal.start_url} "
                    + ("using the LLM" if llm_available else "but no LLM is configured"),
                )
            )
        else:
            report.checks.append(
                Check(
                    Level.OK,
                    f"target:{target}",
                    f"cached flow v{flow.version}, {len(flow.steps)} steps "
                    "- replays without an LLM",
                )
            )


async def preflight(
    targets: list[str] | None = None,
    *,
    settings: Settings | None = None,
    probe_llm: bool = True,
) -> PreflightReport:
    """Check everything a real run depends on.

    Set ``probe_llm=False`` to skip the (paid) live model call.
    """
    settings = settings or get_settings()
    targets = targets or []
    report = PreflightReport()

    settings.ensure_dirs()
    report.checks.append(Check(Level.OK, "data dir", str(settings.data_path)))

    llm = await _check_llm(report, settings, probe_llm)
    _check_targets(report, settings, targets, llm_available=not isinstance(llm, NullLLMClient))
    _check_profile(report, settings, targets)
    await _check_browser(report, settings)

    return report
