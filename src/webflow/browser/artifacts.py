"""Screenshots and other per-run debugging artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import Page

from webflow.config import get_settings
from webflow.logging import get_logger

log = get_logger(__name__)


def run_artifacts_dir(run_id: str) -> Path:
    path = get_settings().artifacts_path / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


async def capture_screenshot(page: Page, run_id: str, label: str) -> str | None:
    """Best-effort screenshot; never let a failed capture break a run."""
    try:
        stamp = datetime.now(UTC).strftime("%H%M%S%f")[:-3]
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
        path = run_artifacts_dir(run_id) / f"{stamp}_{safe}.png"
        await page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception as exc:
        log.debug("screenshot_failed", error=str(exc))
        return None


async def dump_html(page: Page, run_id: str, label: str) -> str | None:
    try:
        path = run_artifacts_dir(run_id) / f"{label}.html"
        path.write_text(await page.content(), encoding="utf-8")
        return str(path)
    except Exception as exc:
        log.debug("html_dump_failed", error=str(exc))
        return None
