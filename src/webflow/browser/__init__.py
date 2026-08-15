"""Playwright-facing layer: sessions, page observation, locators, execution."""

from __future__ import annotations

from webflow.browser.artifacts import capture_screenshot, run_artifacts_dir
from webflow.browser.executor import ActionExecutor, ExecutionOutcome
from webflow.browser.locators import ResolvedLocator, promote, resolve, try_resolve
from webflow.browser.observer import observe, observe_stable, settle
from webflow.browser.session import BrowserSession

__all__ = [
    "ActionExecutor",
    "BrowserSession",
    "ExecutionOutcome",
    "ResolvedLocator",
    "capture_screenshot",
    "observe",
    "observe_stable",
    "promote",
    "resolve",
    "run_artifacts_dir",
    "settle",
    "try_resolve",
]
