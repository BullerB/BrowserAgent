"""Browser lifecycle.

A session is deliberately cheap to create and destroy: the whole point of the
checkpoint design is that we can throw the browser away while waiting for a
human and rebuild it later from ``storage_state``.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any, Self

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from webflow.config import BrowserSettings, get_settings
from webflow.domain.errors import BrowserError
from webflow.logging import get_logger

log = get_logger(__name__)


class BrowserSession:
    """Owns a Playwright instance, browser, context and the active page."""

    def __init__(
        self,
        settings: BrowserSettings | None = None,
        *,
        storage_state: dict[str, Any] | None = None,
        headless: bool | None = None,
        trace_dir: Path | None = None,
    ) -> None:
        self._settings = settings or get_settings().browser
        self._storage_state = storage_state
        self._headless = self._settings.headless if headless is None else headless
        self._trace_dir = trace_dir
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise BrowserError("Session is not started")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise BrowserError("Session is not started")
        return self._context

    async def start(self) -> Self:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
            slow_mo=self._settings.slow_mo_ms or None,
        )
        self._context = await self._browser.new_context(
            locale=self._settings.locale,
            timezone_id=self._settings.timezone,
            user_agent=self._settings.user_agent,
            viewport={
                "width": self._settings.viewport_width,
                "height": self._settings.viewport_height,
            },
            storage_state=self._storage_state,  # type: ignore[arg-type]
        )
        self._context.set_default_timeout(self._settings.default_timeout_ms)
        self._context.set_default_navigation_timeout(self._settings.navigation_timeout_ms)
        if self._settings.record_trace and self._trace_dir:
            await self._context.tracing.start(screenshots=True, snapshots=True)
        self._page = await self._context.new_page()
        log.debug("browser_started", headless=self._headless, restored=bool(self._storage_state))
        return self

    async def export_storage_state(self) -> dict[str, Any]:
        """Cookies + localStorage, so a resumed run keeps its server-side session."""
        return dict(await self.context.storage_state())

    async def close(self) -> None:
        try:
            if self._context is not None:
                if self._settings.record_trace and self._trace_dir:
                    self._trace_dir.mkdir(parents=True, exist_ok=True)
                    await self._context.tracing.stop(path=str(self._trace_dir / "trace.zip"))
                await self._context.close()
            if self._browser is not None:
                await self._browser.close()
        finally:
            if self._playwright is not None:
                await self._playwright.stop()
            self._playwright = self._browser = self._context = self._page = None
            log.debug("browser_closed")

    async def __aenter__(self) -> Self:
        return await self.start()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
