"""Headless replay of a cached Flow, substituting values from the user's profile.

If a selector no longer matches (site changed), falls back to matching by the
recorded visible text before giving up, so small markup changes don't break
the cached flow.
"""
from __future__ import annotations

from playwright.sync_api import Page

from .models import Flow, Step, resolve_value


class FlowPlaybackError(RuntimeError):
    def __init__(self, message: str, step_index: int | None = None, url: str | None = None):
        super().__init__(message)
        self.step_index = step_index
        self.url = url


def _locate(page: Page, step: Step):
    # positional CSS paths (e.g. nth-of-type) can silently resolve to the wrong element
    # if the page layout shifts slightly, so prefer an unambiguous text match when we have one
    if step.action == "click" and step.text:
        by_text = page.get_by_text(step.text, exact=True)
        if by_text.count() == 1:
            return by_text.first

    if step.selector:
        locator = page.locator(step.selector)
        if locator.count() > 0:
            return locator.first
    if step.text:
        locator = page.get_by_text(step.text, exact=False)
        if locator.count() > 0:
            return locator.first
    return None


def _execute_step(page: Page, step: Step, profile: dict, index: int) -> None:
    if step.action == "goto":
        page.goto(step.url)
        return

    locator = _locate(page, step)
    if locator is None:
        raise FlowPlaybackError(
            f"Step {index} ({step.action}) could not locate element for {step.to_dict()}. "
            "The site may have changed; re-record this flow.",
            step_index=index,
            url=page.url,
        )

    if step.action == "fill":
        value = resolve_value(profile, step.profile_key, step.value)
        locator.fill(value or "")
        page.wait_for_timeout(600)  # let async autocomplete/validation settle before next step
    elif step.action == "fill_and_pick":
        # site's address widget only validates when a real dropdown suggestion is clicked;
        # the suggestion API is occasionally slow, so retry a couple of times before giving up
        value = resolve_value(profile, step.profile_key, step.value)
        suggestion = None
        for attempt in range(3):
            locator.click()
            locator.fill("")
            locator.press_sequentially(value or "", delay=50)
            page.wait_for_timeout(1200 + attempt * 800)
            candidate = page.get_by_text(value or "", exact=False).first
            if candidate.count() > 0:
                suggestion = candidate
                break
        if suggestion is None:
            raise FlowPlaybackError(
                f"Step {index} (fill_and_pick) no autocomplete suggestion appeared for '{value}'",
                step_index=index,
                url=page.url,
            )
        suggestion.click()
        page.wait_for_timeout(500)
    elif step.action == "click":
        locator.click()
        page.wait_for_timeout(800)
    elif step.action == "select":
        value = resolve_value(profile, step.profile_key, step.value)
        locator.select_option(value)
    elif step.action == "check":
        locator.check()
    elif step.action == "press":
        locator.press(step.key)
        page.wait_for_timeout(500)
    else:
        raise FlowPlaybackError(f"Step {index} unknown action '{step.action}'", step_index=index, url=page.url)


def play_flow(page: Page, flow: Flow, profile: dict) -> None:
    for index, step in enumerate(flow.steps):
        _execute_step(page, step, profile, index)


def _describe_step(index: int, step: Step) -> str:
    params = {
        k: v
        for k, v in (
            ("selector", step.selector),
            ("text", step.text),
            ("value", step.value),
            ("profile_key", step.profile_key),
            ("url", step.url),
            ("key", step.key),
        )
        if v is not None
    }
    return f"[{index}] {step.action} {params}"


RETRY = object()  # sentinel: re-run the original step as-is instead of repairing/skipping it


def _repair_step(page: Page, index: int, step: Step, error: "FlowPlaybackError"):
    """Let the user fix a failing step live, reusing the recorder's element picker."""
    from .recorder import _show_clickable, _show_inputs, capture_action  # avoid import cycle

    print(f"\nStep {index} failed: {error}")
    print(f"Current page: {page.url}")

    while True:
        buttons = _show_clickable(page)
        inputs = _show_inputs(page)
        choice = input(
            "\n[f<idx>] fill input, [a<idx>] fill + confirm autocomplete suggestion, "
            "[c<idx>] click button, [r] retry this step, [n] skip this step, [q] abort: "
        ).strip().lower()

        if choice == "q":
            return None
        if choice == "r":
            return RETRY
        if choice == "n":
            return step  # keep the original (still-failing) step, caller decides what to do

        try:
            kind, idx_str = choice[0], choice[1:]
            idx = int(idx_str)
            if kind not in ("f", "a", "c"):
                print("Unknown command")
                continue
            return capture_action(kind, idx, page, buttons, inputs)
        except Exception as e:
            print("Error:", e)


def play_flow_interactive(
    page: Page,
    flow: Flow,
    profile: dict,
    *,
    mode: str = "manual",
    delay_ms: int = 1000,
) -> bool:
    """Replay a Flow one step at a time with the browser visible.

    mode="manual" pauses for Enter before each step; mode="auto" auto-advances with a
    delay. If a step fails, drops into an interactive repair prompt; returns True if any
    step was patched, so the caller can offer to save the updated flow.
    """
    modified = False
    index = 0
    while index < len(flow.steps):
        step = flow.steps[index]
        print(_describe_step(index, step))
        if mode == "manual":
            command = input("Press Enter to run this step ('s' to skip, 'q' to abort): ").strip().lower()
            if command == "q":
                raise FlowPlaybackError("Aborted by user", step_index=index, url=page.url)
            if command == "s":
                index += 1
                continue

        while True:
            try:
                _execute_step(page, step, profile, index)
                break
            except FlowPlaybackError as error:
                replacement = _repair_step(page, index, step, error)
                if replacement is None:
                    raise
                if replacement is RETRY:
                    continue  # re-run the same step, then fall back into the normal auto/manual flow
                if replacement is not step:
                    flow.steps[index] = replacement
                    modified = True
                break

        if mode == "auto":
            page.wait_for_timeout(delay_ms)
        index += 1

    return modified
