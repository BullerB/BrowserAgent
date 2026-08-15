"""Turning a :class:`SelectorSet` back into a live Playwright locator.

Strategies are tried best-first. A candidate only wins if it resolves to exactly
one usable element; a strategy matching several elements is remembered as a
fallback and used (via ``.first``) only when nothing unique was found.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from playwright.async_api import Frame, Locator, Page

from webflow.domain.errors import LocatorResolutionError
from webflow.domain.selectors import Selector, SelectorKind, SelectorSet
from webflow.logging import get_logger

log = get_logger(__name__)

Root = Page | Frame


@dataclass(slots=True)
class ResolvedLocator:
    """A live locator plus which strategy produced it."""

    locator: Locator
    selector: Selector
    unique: bool


def build_locator(root: Root, selector: Selector) -> Locator:
    kind = selector.kind
    value = selector.value
    if kind is SelectorKind.TEST_ID:
        locator = root.get_by_test_id(value)
    elif kind is SelectorKind.ROLE:
        locator = root.get_by_role(
            cast(Any, value),
            name=selector.name if selector.name else None,
            exact=selector.exact if selector.name else False,
        )
    elif kind is SelectorKind.LABEL:
        locator = root.get_by_label(value, exact=selector.exact)
    elif kind is SelectorKind.PLACEHOLDER:
        locator = root.get_by_placeholder(value, exact=selector.exact)
    elif kind is SelectorKind.ALT_TEXT:
        locator = root.get_by_alt_text(value, exact=selector.exact)
    elif kind is SelectorKind.TITLE:
        locator = root.get_by_title(value, exact=selector.exact)
    elif kind is SelectorKind.TEXT:
        locator = root.get_by_text(value, exact=selector.exact)
    elif kind is SelectorKind.ELEMENT_ID:
        locator = root.locator(f'[id="{value}"]')
    elif kind is SelectorKind.NAME_ATTR:
        locator = root.locator(f'[name="{value}"]')
    elif kind is SelectorKind.XPATH:
        locator = root.locator(f"xpath={value}")
    else:
        locator = root.locator(value)

    if selector.nth is not None:
        locator = locator.nth(selector.nth)
    return locator


def resolve_root(page: Page, frame_url: str | None) -> Root:
    """Pick the frame an element lives in, falling back to the main frame."""
    if not frame_url:
        return page
    for frame in page.frames:
        if frame.url == frame_url:
            return frame
    log.debug("frame_not_found", frame_url=frame_url)
    return page


async def _usable_count(locator: Locator, require_visible: bool) -> int:
    count = await locator.count()
    if count == 0 or not require_visible:
        return count
    visible = 0
    for i in range(min(count, 10)):
        if await locator.nth(i).is_visible():
            visible += 1
    return visible


async def resolve(
    page: Page,
    selector_set: SelectorSet,
    *,
    require_visible: bool = True,
    timeout_ms: int = 5_000,
    poll_ms: int = 250,
) -> ResolvedLocator:
    """Find the element described by ``selector_set``.

    Single-page apps routinely render the next form a second or two after a
    click, so every strategy is retried until ``timeout_ms`` before giving up.

    Raises :class:`LocatorResolutionError` when no strategy matches.
    """
    candidates = selector_set.ordered()
    ambiguous: ResolvedLocator | None = None
    elapsed = 0

    while True:
        root = resolve_root(page, selector_set.frame_url)
        for selector in candidates:
            try:
                locator = build_locator(root, selector)
                count = await _usable_count(locator, require_visible)
            except Exception as exc:  # a malformed recorded selector must not abort the run
                log.debug("selector_error", selector=str(selector), error=str(exc))
                continue

            if count == 1:
                return ResolvedLocator(locator=locator.first, selector=selector, unique=True)
            if count > 1 and ambiguous is None:
                ambiguous = ResolvedLocator(locator=locator.first, selector=selector, unique=False)

        if ambiguous is not None or elapsed >= timeout_ms:
            break
        await page.wait_for_timeout(poll_ms)
        elapsed += poll_ms

    if ambiguous is not None:
        log.debug("selector_ambiguous", selector=str(ambiguous.selector), set=str(selector_set))
        return ambiguous

    raise LocatorResolutionError(str(selector_set), len(candidates), page.url)


async def try_resolve(
    page: Page, selector_set: SelectorSet, *, timeout_ms: int = 0
) -> ResolvedLocator | None:
    try:
        return await resolve(page, selector_set, timeout_ms=timeout_ms)
    except LocatorResolutionError:
        return None


def promote(selector_set: SelectorSet, winner: Selector) -> SelectorSet:
    """Move the strategy that actually worked to the front.

    Recorded flows self-tune this way: whatever resolved during the last
    successful run is tried first next time.
    """
    rest = [c for c in selector_set.candidates if c != winner]
    return selector_set.model_copy(update={"candidates": [winner, *rest]})
