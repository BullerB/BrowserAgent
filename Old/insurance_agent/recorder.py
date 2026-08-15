"""Interactive 'learn once' recorder.

Walks a product flow manually (like the original test_playwright.py), but
instead of just clicking around, every fill/click you make is captured as a
Step and saved to flows/<product>.json. Next time, player.py can replay it
headlessly using values from profile.json instead of asking you again.
"""
from __future__ import annotations

from playwright.sync_api import Page

from .models import Flow, Step

PRODUCT_URLS = {
    "bilforsikring": "https://forsikringsguiden.dk/bilforsikring",
    "husforsikring": "https://forsikringsguiden.dk/husforsikring",
    "indboforsikring": "https://forsikringsguiden.dk/indboforsikring",
    "ulykkesforsikring": "https://forsikringsguiden.dk/ulykkesforsikring",
    "fritidshusforsikring": "https://forsikringsguiden.dk/fritidshusforsikring",
    "rejseforsikring": "https://forsikringsguiden.dk/rejseforsikring",
    "hundeforsikring": "https://forsikringsguiden.dk/hundeforsikring",
}


def _css_for(element) -> str | None:
    """Build a reasonably stable CSS selector for a locator's underlying element."""
    return element.evaluate(
        """el => {
            if (el.id) return '#' + CSS.escape(el.id);
            if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
            let path = [];
            while (el && el.nodeType === 1 && el.tagName.toLowerCase() !== 'body') {
                let sel = el.tagName.toLowerCase();
                if (el.parentElement) {
                    const siblings = Array.from(el.parentElement.children).filter(c => c.tagName === el.tagName);
                    if (siblings.length > 1) sel += ':nth-of-type(' + (siblings.indexOf(el) + 1) + ')';
                }
                path.unshift(sel);
                el = el.parentElement;
            }
            return path.join(' > ');
        }"""
    )


def _show_clickable(page: Page):
    elements = page.locator("button, a, [role='button'], label").all()
    print("\n=== CLICKABLE ELEMENTS ===")
    visible = []
    for element in elements:
        try:
            if element.is_visible():
                text = element.inner_text().strip()
                if text:
                    visible.append(element)
                    print(f"{len(visible) - 1}: {text}")
        except Exception:
            pass
    return visible


def _show_inputs(page: Page):
    inputs = page.locator("input").all()
    visible = []
    print("\n=== FORM FIELDS ===")
    for field in inputs:
        try:
            if field.is_visible():
                visible.append(field)
                print(
                    f"{len(visible) - 1}: type={field.get_attribute('type')} "
                    f"name={field.get_attribute('name')} "
                    f"placeholder={field.get_attribute('placeholder')}"
                )
        except Exception:
            pass
    return visible


def capture_action(kind: str, idx: int, page: Page, buttons: list, inputs: list) -> Step:
    """Perform a chosen f/a/c action live on the page and return the Step it corresponds to.

    Shared by record_flow's manual walkthrough and player.py's interactive repair prompt.
    """
    if kind in ("f", "a"):
        field = inputs[idx]
        value = input("Value to fill: ")
        key = input(
            "Profile key this maps to (e.g. person.email), blank = literal only: "
        ).strip() or None
        selector = _css_for(field)

        if kind == "a":
            suggestion = None
            for attempt in range(3):
                field.click()
                field.fill("")
                field.press_sequentially(value, delay=50)
                page.wait_for_timeout(1200 + attempt * 800)
                candidate = page.get_by_text(value, exact=False).first
                if candidate.count() > 0:
                    suggestion = candidate
                    break
            if suggestion is None:
                print("No suggestion appeared; falling back to plain fill.")
                field.fill(value)
                return Step(action="fill", selector=selector, profile_key=key, value=value)
            suggestion.click()
            page.wait_for_timeout(500)
            return Step(action="fill_and_pick", selector=selector, profile_key=key, value=value)

        field.fill(value)
        return Step(action="fill", selector=selector, profile_key=key, value=value)

    if kind == "c":
        button = buttons[idx]
        text = button.inner_text().strip()
        selector = _css_for(button)
        button.click()
        page.wait_for_timeout(800)
        return Step(action="click", selector=selector, text=text)

    raise ValueError(f"Unknown command kind '{kind}'")


def record_flow(product: str, headless: bool = False) -> Flow:
    """Manually walk the site once; every action you take is saved as a Step."""
    from playwright.sync_api import sync_playwright

    url = PRODUCT_URLS.get(product)
    if not url:
        raise ValueError(f"Unknown product '{product}'. Known: {list(PRODUCT_URLS)}")

    flow = Flow(product=product)
    flow.steps.append(Step(action="goto", url=url))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(url)

        while True:
            buttons = _show_clickable(page)
            inputs = _show_inputs(page)

            choice = input(
                "\n[f<idx>] fill input, [a<idx>] fill + confirm autocomplete suggestion, "
                "[c<idx>] click button, [q] quit & save, e.g. 'f0', 'a4' or 'c2': "
            ).strip().lower()

            if choice == "q":
                break

            try:
                kind, idx_str = choice[0], choice[1:]
                idx = int(idx_str)
                if kind not in ("f", "a", "c"):
                    print("Unknown command")
                    continue
                flow.steps.append(capture_action(kind, idx, page, buttons, inputs))
            except Exception as e:
                print("Error:", e)

        browser.close()

    flow.save()
    print(f"\nSaved flow with {len(flow.steps)} steps to {flow.path}")
    return flow
