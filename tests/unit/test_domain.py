from __future__ import annotations

import pytest

from webflow.domain.errors import ValueResolutionError
from webflow.domain.selectors import Selector, SelectorKind, SelectorSet
from webflow.domain.values import ValueContext, ValueSource, describe_profile


def test_selector_set_keeps_stored_order_and_reports_robustness() -> None:
    css = Selector(kind=SelectorKind.CSS, value="div > button")
    role = Selector(kind=SelectorKind.ROLE, value="button", name="Videre")
    selector_set = SelectorSet(candidates=[css, role], description="continue")

    assert [c.kind for c in selector_set.ordered()] == [SelectorKind.CSS, SelectorKind.ROLE]
    assert [c.kind for c in selector_set.by_priority().ordered()] == [
        SelectorKind.ROLE,
        SelectorKind.CSS,
    ]
    assert selector_set.robustness == role.priority


def test_selector_sets_merge_without_duplicates() -> None:
    a = SelectorSet(candidates=[Selector(kind=SelectorKind.CSS, value="#x")])
    b = SelectorSet(
        candidates=[
            Selector(kind=SelectorKind.CSS, value="#x"),
            Selector(kind=SelectorKind.TEST_ID, value="x"),
        ]
    )
    merged = a.with_fallback(b)
    assert len(merged.candidates) == 2


def test_value_source_prefers_secret_then_answer_then_profile() -> None:
    context = ValueContext(
        profile={"person": {"email": "a@b.dk"}},
        answers={"annual_km": "15000"},
        secrets={"pw": "hunter2"},
    )
    assert ValueSource(profile_key="person.email").resolve(context) == "a@b.dk"
    assert ValueSource(answer_key="annual_km").resolve(context) == "15000"
    assert ValueSource(secret_key="pw").resolve(context) == "hunter2"
    assert ValueSource(profile_key="person.missing", literal="fallback").resolve(context) == (
        "fallback"
    )

def test_profile_key_falls_back_to_an_equivalent_checkpoint_answer() -> None:
    context = ValueContext(answers={"person.address": "Example Street 1"})

    assert ValueSource(profile_key="person.address").resolve(context) == "Example Street 1"


def test_value_source_requires_a_source() -> None:
    with pytest.raises(ValueError):
        ValueSource()


def test_unresolvable_value_names_the_missing_key() -> None:
    with pytest.raises(ValueResolutionError) as exc:
        ValueSource(profile_key="person.email").resolve(ValueContext())
    assert exc.value.key == "person.email"


def test_secret_values_are_never_described() -> None:
    assert ValueSource(secret_key="pw").describe() == "secret:pw"
    assert "hunter2" not in ValueSource(secret_key="pw").describe()


def test_profile_description_redacts_personal_data_but_not_car_details() -> None:
    infos = {
        i.key: i.preview
        for i in describe_profile(
            {
                "person": {"email": "a@b.dk", "address": "Vej 1", "country": "DK"},
                "vehicle": {"annual_km": "15000", "registration_number": "AB12345"},
            }
        )
    }
    assert infos["person.email"] == "***"
    assert infos["person.address"] == "***"
    assert infos["vehicle.registration_number"] == "***"
    assert infos["vehicle.annual_km"] == "15000"
    assert infos["person.country"] == "DK"
