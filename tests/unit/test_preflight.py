from __future__ import annotations

from pathlib import Path

import pytest

from webflow.config import Settings
from webflow.llm.base import ScriptedLLMClient
from webflow.preflight import Level, preflight

TARGET = "forsikringsguiden/bilforsikring"


def _levels(report: object) -> dict[str, Level]:
    return {c.name: c.level for c in report.checks}  # type: ignore[attr-defined]


async def test_missing_llm_and_missing_flow_is_a_hard_blocker(settings: Settings) -> None:
    report = await preflight([TARGET], settings=settings, probe_llm=False)
    levels = _levels(report)

    assert levels["llm"] is Level.WARN
    assert levels[f"target:{TARGET}"] is Level.FAIL, "cannot explore without an LLM"
    assert not report.ready
    assert "NOT READY" in str(report)


async def test_missing_profile_only_warns(settings: Settings) -> None:
    report = await preflight([TARGET], settings=settings, probe_llm=False)
    assert _levels(report)["profile"] is Level.WARN


async def test_unknown_target_fails_clearly(settings: Settings) -> None:
    report = await preflight(["nosuchsite/nosuchgoal"], settings=settings, probe_llm=False)
    assert _levels(report)["target:nosuchsite/nosuchgoal"] is Level.FAIL


async def test_browser_and_providers_are_reported_healthy(settings: Settings) -> None:
    report = await preflight([], settings=settings, probe_llm=False)
    levels = _levels(report)
    assert levels["browser"] is Level.OK
    assert levels["providers"] is Level.OK


async def test_probe_reports_a_working_llm(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "webflow.preflight.create_llm_client",
        lambda _: ScriptedLLMClient(['{"ok": true}']),
    )
    report = await preflight([], settings=settings, probe_llm=True)
    assert _levels(report)["llm"] is Level.OK
    assert report.ready


async def test_probe_reports_a_broken_llm(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "webflow.preflight.create_llm_client", lambda _: ScriptedLLMClient([])
    )
    report = await preflight([], settings=settings, probe_llm=True)
    assert _levels(report)["llm"] is Level.FAIL
    assert not report.ready


async def test_cached_flow_makes_a_target_runnable_without_an_llm(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from providers.registry import get_provider
    from webflow.domain.actions import GotoAction
    from webflow.domain.flow import Flow, FlowStep
    from webflow.orchestrator.services import make_flow_store

    provider = get_provider("forsikringsguiden")
    monkeypatch.setattr(type(provider), "flows_dir", property(lambda _: tmp_path / "flows"))
    make_flow_store(provider.flows_dir, settings).save(
        Flow(
            provider_id="forsikringsguiden",
            goal="bilforsikring",
            start_url="https://forsikringsguiden.dk/bilforsikring",
            steps=[FlowStep(index=0, action=GotoAction(url="https://forsikringsguiden.dk"))],
        )
    )

    report = await preflight([TARGET], settings=settings, probe_llm=False)
    assert _levels(report)[f"target:{TARGET}"] is Level.OK
