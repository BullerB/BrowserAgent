"""Live smoke test against a real site. Run manually:

    pytest tests/live/test_forsikringsguiden_live.py -m live -s

Needs internet. No LLM key required - this checks that the page observer,
provider hooks and locator layer cope with the real DOM.
"""

from __future__ import annotations

import pytest

from providers.insurance.forsikringsguiden import PROVIDER
from webflow.browser.executor import ActionExecutor
from webflow.browser.observer import observe, observe_stable
from webflow.browser.session import BrowserSession
from webflow.config import BrowserSettings
from webflow.domain.actions import ClickAction, FillAction, FillAndPickAction
from webflow.domain.values import ValueContext, ValueSource

pytestmark = pytest.mark.live


async def test_car_insurance_start_page_is_observable() -> None:
    session = BrowserSession(BrowserSettings(headless=True, navigation_timeout_ms=45_000))
    await session.start()
    try:
        goal = PROVIDER.goal("bilforsikring")
        await session.page.goto(goal.start_url, wait_until="domcontentloaded")
        await PROVIDER.prepare(session.page)

        observation = await observe(session.page, settle_ms=1_500)

        print(f"\nURL   : {observation.url}")
        print(f"TITLE : {observation.title}")
        print(f"ELEMS : {len(observation.elements)}")
        for element in observation.elements[:40]:
            print("  " + element.to_prompt_line())

        assert observation.elements, "observer found no interactive elements"
        assert any(e.is_input for e in observation.elements), "no form fields detected"
        # Every element must be addressable, or the agent cannot act on it.
        assert all(e.to_selector_set().candidates for e in observation.elements)

        names = " | ".join(e.name.lower() for e in observation.elements)
        assert "accepter alle" not in names, "cookie wall was not dismissed by prepare()"
        assert "videre" in names, "continue button not found"
        assert any(
            e.name_attr == "autoInput" or "alder" in (e.name or "").lower()
            for e in observation.elements
        ), "neither the address field nor the age field was found"
    finally:
        await session.close()


async def test_first_form_page_can_be_completed() -> None:
    """Drive page one for real: address autocomplete, age, then continue.

    Uses a public building's address - no personal data - and stops long before
    anything is submitted to an insurer.
    """
    session = BrowserSession(BrowserSettings(headless=True, navigation_timeout_ms=45_000))
    await session.start()
    try:
        goal = PROVIDER.goal("bilforsikring")
        await session.page.goto(goal.start_url, wait_until="domcontentloaded")
        await PROVIDER.prepare(session.page)

        executor = ActionExecutor(session.page, ValueContext(), settle_ms=800)
        before = await observe(session.page, settle_ms=1_000)

        address = next(e for e in before.elements if e.name_attr == "autoInput")
        await executor.execute(
            FillAndPickAction(
                target=address.to_selector_set(),
                value=ValueSource.of("Rådhuspladsen 1"),
                suggestion_timeout_ms=8_000,
            )
        )
        picked = await session.page.locator("[name='autoInput']").input_value()
        print(f"\nADDRESS FIELD AFTER PICK: {picked!r}")
        assert "Rådhuspladsen" in picked
        assert picked.strip() != "Rådhuspladsen 1", "no suggestion was committed"

        age = next(e for e in before.elements if "alder" in (e.name or "").lower())
        await executor.execute(
            FillAction(target=age.to_selector_set(), value=ValueSource.of("30"))
        )

        videre = next(e for e in before.elements if e.name.strip().lower() == "videre")
        await executor.execute(ClickAction(target=videre.to_selector_set()))

        after = await observe_stable(session.page, settle_ms=1_000, timeout_ms=15_000)
        print(f"PAGE 2 URL   : {after.url}")
        print(f"PAGE 2 ELEMS : {len(after.elements)}")
        for element in after.elements[:25]:
            print("  " + element.to_prompt_line())
        print(f"VALIDATION   : {after.validation_messages}")

        assert after.signature() != before.signature(), "the form did not advance"
        # Page two asks for the registration plate; it renders seconds after the
        # click, which is the whole reason observe_stable exists.
        assert any(
            e.name_attr == "licenseplate" or "nummerplade" in e.describe().lower()
            for e in after.elements
        ), "the registration-plate field never appeared"
    finally:
        await session.close()
