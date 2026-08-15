from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

from webflow.browser.session import BrowserSession
from webflow.config import BrowserSettings, Settings, get_settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def quote_form_url() -> str:
    return (FIXTURES / "quote_form.html").as_uri()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[BrowserSession]:
    settings = BrowserSettings(headless=True, default_timeout_ms=5_000)
    browser = BrowserSession(settings)
    await browser.start()
    try:
        yield browser
    finally:
        await browser.close()


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Isolated settings: temp data dir, temp profile, no real LLM."""
    monkeypatch.setenv("WEBFLOW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WEBFLOW_PROFILE_PATH", str(tmp_path / "profile.json"))
    monkeypatch.setenv("WEBFLOW_LLM__PROVIDER", "null")
    monkeypatch.setenv("WEBFLOW_BROWSER__HEADLESS", "true")
    monkeypatch.setenv("WEBFLOW_BROWSER__DEFAULT_TIMEOUT_MS", "5000")
    monkeypatch.setenv("WEBFLOW_AGENT__SETTLE_MS", "50")
    get_settings.cache_clear()
    resolved = get_settings()
    resolved.ensure_dirs()
    try:
        yield resolved
    finally:
        get_settings.cache_clear()
        shutil.rmtree(resolved.data_path, ignore_errors=True)
