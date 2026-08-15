"""Build a :class:`PageObservation` from a live page.

Runs the snapshot script in the main frame and in every same-origin iframe, then
assigns each element a stable index for this observation only.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from playwright.async_api import Frame, Page

from webflow.browser.snapshot_script import INTERACTIVE_SELECTOR, SNAPSHOT_JS
from webflow.domain.observation import InteractiveElement, PageObservation
from webflow.logging import get_logger

log = get_logger(__name__)


async def settle(page: Page, settle_ms: int = 700) -> None:
    """Give async validation / price recalculation a chance to finish."""
    with suppress(Exception):
        await page.wait_for_load_state("domcontentloaded")
    with suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=settle_ms * 3)
    await page.wait_for_timeout(settle_ms)


async def _snapshot_frame(
    frame: Page | Frame, *, max_elements: int, max_text: int
) -> dict[str, Any] | None:
    try:
        return await frame.evaluate(  # type: ignore[no-any-return]
            SNAPSHOT_JS,
            {
                "selector": INTERACTIVE_SELECTOR,
                "maxElements": max_elements,
                "maxText": max_text,
            },
        )
    except Exception as exc:  # detached / cross-origin frames are expected
        log.debug("snapshot_frame_failed", url=getattr(frame, "url", "?"), error=str(exc))
        return None


async def observe(
    page: Page,
    *,
    max_elements: int = 120,
    max_text: int = 6_000,
    include_frames: bool = True,
    settle_ms: int = 0,
) -> PageObservation:
    if settle_ms:
        await settle(page, settle_ms)

    main = await _snapshot_frame(page, max_elements=max_elements, max_text=max_text)
    if main is None:
        return PageObservation(url=page.url, title=await page.title())

    elements: list[InteractiveElement] = []
    validation: list[str] = list(main.get("validation_messages", []))

    def add(raw_elements: list[dict[str, Any]], frame_url: str | None) -> None:
        for raw in raw_elements:
            elements.append(
                InteractiveElement(index=len(elements), frame_url=frame_url, **raw)
            )

    add(main.get("elements", []), None)

    if include_frames:
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            remaining = max_elements - len(elements)
            if remaining <= 0:
                break
            sub = await _snapshot_frame(frame, max_elements=remaining, max_text=1_000)
            if sub is None:
                continue
            add(sub.get("elements", []), frame.url)
            validation.extend(m for m in sub.get("validation_messages", []) if m not in validation)

    return PageObservation(
        url=main.get("url", page.url),
        title=main.get("title", ""),
        text=main.get("text", ""),
        elements=elements,
        validation_messages=validation,
    )


async def observe_stable(
    page: Page,
    *,
    max_elements: int = 120,
    max_text: int = 6_000,
    settle_ms: int = 0,
    timeout_ms: int = 8_000,
    poll_ms: int = 600,
) -> PageObservation:
    """Snapshot only once the page stops changing.

    Single-page forms render the next question a second or more after a click.
    Snapshotting too early hands the planner a half-built page and makes it act
    on the wrong element, so poll until two consecutive snapshots agree.
    """
    previous: str | None = None
    observation = await observe(
        page, max_elements=max_elements, max_text=max_text, settle_ms=settle_ms
    )
    elapsed = 0

    while elapsed < timeout_ms:
        signature = observation.signature()
        if previous == signature and observation.elements:
            return observation
        previous = signature
        await page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
        observation = await observe(page, max_elements=max_elements, max_text=max_text)

    log.debug("observation_did_not_settle", url=observation.url, elements=len(observation.elements))
    return observation
