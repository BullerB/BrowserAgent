"""The single place where an :class:`Action` is turned into browser interaction.

Both the agent and the flow player go through here, which guarantees that what
was learned during exploration replays identically later.
"""

from __future__ import annotations

import time
from contextlib import suppress
from dataclasses import dataclass

from playwright.async_api import Locator, Page

from webflow.browser import locators
from webflow.domain.actions import (
    Action,
    CheckAction,
    ClickAction,
    FillAction,
    FillAndPickAction,
    GotoAction,
    PressAction,
    ScrollAction,
    SelectAction,
    UploadAction,
    WaitAction,
)
from webflow.domain.errors import ActionExecutionError, LocatorResolutionError
from webflow.domain.selectors import Selector, SelectorSet
from webflow.domain.values import ValueContext
from webflow.logging import get_logger

log = get_logger(__name__)

#: Fallbacks for autocomplete widgets that expose no accessible listbox.
SUGGESTION_FALLBACKS = (
    "[role='option']",
    "[role='listbox'] li",
    "ul[class*='suggest'] li",
    "[class*='autocomplete'] li",
    "[class*='suggestion'] li",
    "[class*='dropdown'] li",
)


@dataclass(slots=True)
class ExecutionOutcome:
    url_after: str
    duration_ms: int
    """Which selector strategy actually resolved, so the flow can promote it."""
    selector_used: Selector | None = None
    skipped: bool = False
    note: str | None = None


class ActionExecutor:
    def __init__(
        self,
        page: Page,
        values: ValueContext | None = None,
        *,
        settle_ms: int = 300,
    ) -> None:
        self.page = page
        self.values = values or ValueContext()
        self.settle_ms = settle_ms

    async def execute(self, action: Action) -> ExecutionOutcome:
        started = time.perf_counter()
        selector_used: Selector | None = None
        try:
            selector_used = await self._dispatch(action)
        except LocatorResolutionError as exc:
            if getattr(action, "optional", False):
                log.info("optional_step_skipped", action=action.type, reason=str(exc))
                return ExecutionOutcome(
                    url_after=self.page.url,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    skipped=True,
                    note=str(exc),
                )
            raise
        except ActionExecutionError:
            raise
        except Exception as exc:
            raise ActionExecutionError(action.type, str(exc), self.page.url) from exc

        wait_ms = action.wait_after_ms or self.settle_ms
        if wait_ms:
            await self.page.wait_for_timeout(wait_ms)

        return ExecutionOutcome(
            url_after=self.page.url,
            duration_ms=int((time.perf_counter() - started) * 1000),
            selector_used=selector_used,
        )

    async def _dispatch(self, action: Action) -> Selector | None:
        match action:
            case GotoAction():
                await self.page.goto(action.url, wait_until=action.wait_until)
                return None
            case ClickAction():
                resolved = await self._resolve(action.target)
                await self._click(resolved.locator)
                return resolved.selector
            case FillAction():
                resolved = await self._resolve(action.target)
                value = action.value.resolve(self.values)
                await self._fill(resolved.locator, value, action.sequential)
                return resolved.selector
            case FillAndPickAction():
                return await self._fill_and_pick(action)
            case SelectAction():
                resolved = await self._resolve(action.target)
                value = action.value.resolve(self.values)
                if action.by == "label":
                    await resolved.locator.select_option(label=value)
                elif action.by == "value":
                    await resolved.locator.select_option(value=value)
                else:
                    await resolved.locator.select_option(index=int(value))
                return resolved.selector
            case CheckAction():
                resolved = await self._resolve(action.target)
                if action.checked:
                    await resolved.locator.check(force=True)
                else:
                    await resolved.locator.uncheck(force=True)
                return resolved.selector
            case PressAction():
                if action.target is not None:
                    resolved = await self._resolve(action.target)
                    await resolved.locator.press(action.key)
                    return resolved.selector
                await self.page.keyboard.press(action.key)
                return None
            case UploadAction():
                resolved = await self._resolve(action.target)
                await resolved.locator.set_input_files(action.path.resolve(self.values))
                return resolved.selector
            case ScrollAction():
                await self._scroll(action)
                return None
            case WaitAction():
                await self._wait(action)
                return None
            case _:
                raise ActionExecutionError(
                    action.type, "not executable against a page", self.page.url
                )

    async def _resolve(self, target: SelectorSet) -> locators.ResolvedLocator:
        return await locators.resolve(self.page, target)

    async def _click(self, locator: Locator) -> None:
        with suppress(Exception):
            await locator.scroll_into_view_if_needed(timeout=3_000)
        try:
            await locator.click(timeout=8_000)
        except Exception:
            # Overlays and custom widgets frequently intercept the real click.
            await locator.click(timeout=5_000, force=True)

    async def _fill(self, locator: Locator, value: str, sequential: bool) -> None:
        await locator.scroll_into_view_if_needed(timeout=3_000)
        if sequential:
            await locator.click()
            await locator.fill("")
            await locator.type(value, delay=40)
        else:
            await locator.fill(value)

    async def _fill_and_pick(self, action: FillAndPickAction) -> Selector | None:
        """Type into an autocomplete field and commit one of its suggestions."""
        resolved = await self._resolve(action.target)
        value = action.value.resolve(self.values)
        await self._fill(resolved.locator, value, sequential=True)

        if action.suggestion is not None:
            picked = await locators.try_resolve(self.page, action.suggestion)
            if picked is not None:
                await self._click(picked.locator)
                return resolved.selector

        deadline = time.perf_counter() + action.suggestion_timeout_ms / 1000
        while time.perf_counter() < deadline:
            for css in SUGGESTION_FALLBACKS:
                candidate = self.page.locator(css)
                if await candidate.count() > action.suggestion_index:
                    option = candidate.nth(action.suggestion_index)
                    if await option.is_visible():
                        await self._click(option)
                        return resolved.selector
            await self.page.wait_for_timeout(250)

        # Some widgets accept the typed value on Enter when no list appears.
        await resolved.locator.press("Enter")
        return resolved.selector

    async def _scroll(self, action: ScrollAction) -> None:
        if action.target is not None:
            resolved = await locators.try_resolve(self.page, action.target)
            if resolved is not None:
                await resolved.locator.scroll_into_view_if_needed()
                return
        if action.direction == "top":
            await self.page.evaluate("window.scrollTo(0, 0)")
        elif action.direction == "bottom":
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            delta = action.amount_px * (1 if action.direction == "down" else -1)
            await self.page.mouse.wheel(0, delta)

    async def _wait(self, action: WaitAction) -> None:
        if action.target is None:
            await self.page.wait_for_timeout(action.timeout_ms)
            return
        resolved = await locators.try_resolve(self.page, action.target)
        if resolved is None:
            if action.state in {"hidden", "detached"}:
                return
            raise LocatorResolutionError(str(action.target), len(action.target.candidates))
        await resolved.locator.wait_for(state=action.state, timeout=action.timeout_ms)
