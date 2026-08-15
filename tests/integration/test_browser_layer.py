"""Observer / locator / executor behaviour against a static local page."""

from __future__ import annotations

import pytest

from webflow.browser import locators, observe
from webflow.browser.executor import ActionExecutor
from webflow.browser.session import BrowserSession
from webflow.domain.actions import (
    CheckAction,
    ClickAction,
    FillAction,
    FillAndPickAction,
    GotoAction,
    SelectAction,
)
from webflow.domain.errors import LocatorResolutionError
from webflow.domain.selectors import Selector, SelectorKind, SelectorSet
from webflow.domain.values import ValueContext, ValueSource


async def _open(session: BrowserSession, url: str) -> ActionExecutor:
    executor = ActionExecutor(
        session.page,
        ValueContext(profile={"person": {"email": "test@example.com", "age": 29}}),
        settle_ms=50,
    )
    await executor.execute(GotoAction(url=url))
    return executor


async def test_observe_finds_labelled_inputs(
    session: BrowserSession, quote_form_url: str
) -> None:
    await _open(session, quote_form_url)
    obs = await observe(session.page)

    by_id = {e.element_id: e for e in obs.elements if e.element_id}
    assert "email" in by_id
    email = by_id["email"]
    assert email.role == "textbox"
    assert email.label == "E-mail"
    assert email.placeholder == "din@mail.dk"
    assert email.required is True
    assert email.group_label == "Om dig"

    brand = by_id["brand"]
    assert brand.role == "combobox"
    assert "Volkswagen" in brand.options

    assert "Bilforsikring" in obs.text
    assert obs.to_prompt().startswith("URL:")


async def test_selector_set_resolves_by_test_id_first(
    session: BrowserSession, quote_form_url: str
) -> None:
    await _open(session, quote_form_url)
    obs = await observe(session.page)
    cookie = next(e for e in obs.elements if e.element_id == "cookie-accept")

    selector_set = cookie.to_selector_set()
    assert selector_set.candidates[0].kind is SelectorKind.TEST_ID

    resolved = await locators.resolve(session.page, selector_set)
    assert resolved.unique
    assert resolved.selector.kind is SelectorKind.TEST_ID


async def test_resolution_falls_back_when_first_strategy_is_stale(
    session: BrowserSession, quote_form_url: str
) -> None:
    await _open(session, quote_form_url)
    selector_set = SelectorSet(
        candidates=[
            Selector(kind=SelectorKind.TEST_ID, value="does-not-exist"),
            Selector(kind=SelectorKind.CSS, value="#nope > .gone"),
            Selector(kind=SelectorKind.LABEL, value="E-mail", exact=True),
        ],
        description="email field",
    )
    resolved = await locators.resolve(session.page, selector_set)
    assert resolved.selector.kind is SelectorKind.LABEL


async def test_resolution_raises_when_nothing_matches(
    session: BrowserSession, quote_form_url: str
) -> None:
    await _open(session, quote_form_url)
    selector_set = SelectorSet(
        candidates=[Selector(kind=SelectorKind.TEST_ID, value="ghost")], description="ghost"
    )
    with pytest.raises(LocatorResolutionError):
        await locators.resolve(session.page, selector_set)


async def test_optional_step_is_skipped_instead_of_failing(
    session: BrowserSession, quote_form_url: str
) -> None:
    executor = await _open(session, quote_form_url)
    action = ClickAction(
        target=SelectorSet(
            candidates=[Selector(kind=SelectorKind.TEST_ID, value="ghost")], description="ghost"
        ),
        optional=True,
    )
    outcome = await executor.execute(action)
    assert outcome.skipped


async def test_fill_uses_profile_value(session: BrowserSession, quote_form_url: str) -> None:
    executor = await _open(session, quote_form_url)
    await executor.execute(
        FillAction(
            target=SelectorSet(
                candidates=[Selector(kind=SelectorKind.ELEMENT_ID, value="email")],
                description="email",
            ),
            value=ValueSource(profile_key="person.email"),
        )
    )
    assert await session.page.input_value("#email") == "test@example.com"


async def test_fill_and_pick_commits_a_suggestion(
    session: BrowserSession, quote_form_url: str
) -> None:
    executor = await _open(session, quote_form_url)
    await executor.execute(
        FillAndPickAction(
            target=SelectorSet(
                candidates=[Selector(kind=SelectorKind.NAME_ATTR, value="autoInput")],
                description="address",
            ),
            value=ValueSource.of("Isafjordsgade"),
        )
    )
    assert await session.page.input_value("#address") == "Isafjordsgade 6, 2300 København S"


async def test_select_check_and_submit_reach_results(
    session: BrowserSession, quote_form_url: str
) -> None:
    executor = await _open(session, quote_form_url)

    def by_id(element_id: str) -> SelectorSet:
        return SelectorSet(
            candidates=[Selector(kind=SelectorKind.ELEMENT_ID, value=element_id)],
            description=element_id,
        )

    await executor.execute(
        FillAction(target=by_id("email"), value=ValueSource(profile_key="person.email"))
    )
    await executor.execute(
        FillAndPickAction(target=by_id("address"), value=ValueSource.of("Isafjordsgade"))
    )
    await executor.execute(
        SelectAction(target=by_id("brand"), value=ValueSource.of("Volkswagen"))
    )
    await executor.execute(CheckAction(target=by_id("terms")))
    await executor.execute(ClickAction(target=by_id("submit")))

    obs = await observe(session.page)
    assert "Alm. Brand" in obs.text
    assert "3.499 kr" in obs.text


async def test_validation_message_is_observed(
    session: BrowserSession, quote_form_url: str
) -> None:
    executor = await _open(session, quote_form_url)
    by_id = SelectorSet(
        candidates=[Selector(kind=SelectorKind.ELEMENT_ID, value="email")], description="email"
    )
    await executor.execute(FillAction(target=by_id, value=ValueSource.of("a@b.dk")))
    await executor.execute(
        ClickAction(
            target=SelectorSet(
                candidates=[Selector(kind=SelectorKind.ELEMENT_ID, value="submit")],
                description="submit",
            )
        )
    )
    obs = await observe(session.page)
    assert any("Adresse" in m for m in obs.validation_messages)
