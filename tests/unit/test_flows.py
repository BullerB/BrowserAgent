from __future__ import annotations

from pathlib import Path

from webflow.config import Settings
from webflow.domain.actions import ClickAction, DoneAction, FillAction, GotoAction
from webflow.domain.checkpoint import AnswerField, CheckpointReason, CheckpointRequest
from webflow.domain.flow import Flow
from webflow.domain.run import ExecutedStep, RunState, StepStatus
from webflow.domain.selectors import Selector, SelectorKind, SelectorSet
from webflow.domain.values import ValueSource
from webflow.extraction.heuristics import normalise_price, parse_card
from webflow.flows.recorder import record_flow
from webflow.flows.repair import apply_repairs
from webflow.flows.store import FlowStore


def _target(name: str) -> SelectorSet:
    return SelectorSet(
        candidates=[Selector(kind=SelectorKind.TEST_ID, value=name)], description=name
    )


def _run_with_trajectory() -> RunState:
    run = RunState(provider_id="demo", goal="quote")
    run.record(
        ExecutedStep(index=0, action=GotoAction(url="https://demo.dk/start"), url_after="x")
    )
    run.record(ExecutedStep(index=1, action=ClickAction(target=_target("cookies"))))
    run.record(
        ExecutedStep(
            index=2,
            action=ClickAction(target=_target("dead-end")),
            status=StepStatus.FAILED,
            error="not found",
        )
    )
    run.record(
        ExecutedStep(
            index=3,
            action=FillAction(
                target=_target("email"), value=ValueSource(profile_key="person.email")
            ),
        )
    )
    run.record(ExecutedStep(index=4, action=DoneAction(summary="finished")))
    return run


def test_recorder_drops_failures_and_the_done_marker() -> None:
    flow = record_flow(_run_with_trajectory(), start_url="https://demo.dk/start")

    kinds = [s.action.type for s in flow.steps]
    assert kinds == ["goto", "click", "fill"]
    assert flow.provider_id == "demo"
    assert flow.required_profile_keys() == {"person.email"}


def test_recorder_prepends_navigation_when_the_trajectory_lacks_it() -> None:
    run = RunState(provider_id="demo", goal="quote")
    run.record(ExecutedStep(index=0, action=ClickAction(target=_target("cookies"))))
    flow = record_flow(run, start_url="https://demo.dk/start")
    assert flow.steps[0].action.type == "goto"


def test_checkpoint_steps_survive_recording_so_replay_knows_where_to_ask() -> None:
    from webflow.domain.actions import HumanCheckpointAction

    run = RunState(provider_id="demo", goal="quote")
    run.record(ExecutedStep(index=0, action=GotoAction(url="https://demo.dk/start")))
    run.record(
        ExecutedStep(
            index=1,
            action=HumanCheckpointAction(
                request=CheckpointRequest(
                    reason=CheckpointReason.MISSING_PROFILE_DATA,
                    question="Annual km?",
                    fields=[AnswerField(key="annual_km", prompt="km/year")],
                )
            ),
        )
    )
    flow = record_flow(run, start_url="https://demo.dk/start")
    assert [s.action.type for s in flow.steps] == ["goto", "human_checkpoint"]


def test_checkpoint_step_is_not_replayed_when_fast_forwarding() -> None:
    from webflow.domain.actions import HumanCheckpointAction

    run = RunState(provider_id="demo", goal="quote")
    run.record(ExecutedStep(index=0, action=GotoAction(url="https://demo.dk/start")))
    run.record(
        ExecutedStep(
            index=1,
            action=HumanCheckpointAction(
                request=CheckpointRequest(reason=CheckpointReason.LOGIN, question="Log in")
            ),
        )
    )
    assert [a.type for a in run.replayable_actions()] == ["goto"]


def test_flow_round_trips_through_json(settings: Settings) -> None:
    flow = record_flow(_run_with_trajectory(), start_url="https://demo.dk/start")
    store = FlowStore(roots=[settings.data_path / "flows"])
    path = store.save(flow)

    assert path.name == "v1.json"
    reloaded = store.latest("demo", "quote")
    assert reloaded is not None
    assert reloaded.model_dump() == flow.model_dump()


def test_store_returns_the_newest_version(settings: Settings) -> None:
    store = FlowStore(roots=[settings.data_path / "flows"])
    base = record_flow(_run_with_trajectory(), start_url="https://demo.dk/start")
    store.save(base)
    store.save(base.model_copy(update={"version": 2}))

    assert store.versions("demo", "quote") == [1, 2]
    latest = store.latest("demo", "quote")
    assert latest is not None and latest.version == 2
    assert store.next_version("demo", "quote") == 3


def test_local_root_shadows_the_provider_root(tmp_path: Path) -> None:
    local, shipped = tmp_path / "local", tmp_path / "shipped"
    flow = record_flow(_run_with_trajectory(), start_url="https://demo.dk/start")
    FlowStore(roots=[shipped], write_root=shipped).save(flow)
    FlowStore(roots=[local], write_root=local).save(
        flow.model_copy(update={"notes": ["local override"]})
    )

    combined = FlowStore(roots=[local, shipped], write_root=shipped)
    latest = combined.latest("demo", "quote")
    assert latest is not None and latest.notes == ["local override"]


def test_repairing_a_step_bumps_the_version_and_counts_the_repair() -> None:
    flow = record_flow(_run_with_trajectory(), start_url="https://demo.dk/start")
    replacement = ClickAction(target=_target("cookies-v2"))

    repaired = apply_repairs(flow, {1: replacement}, {})
    assert repaired is not None
    assert repaired.version == flow.version + 1
    assert repaired.steps[1].repair_count == 1
    assert repaired.steps[1].action.target.candidates[0].value == "cookies-v2"


def test_apply_repairs_is_a_no_op_without_changes() -> None:
    flow = record_flow(_run_with_trajectory(), start_url="https://demo.dk/start")
    assert apply_repairs(flow, {}, {}) is None


def test_flow_reports_its_weakest_selector() -> None:
    flow = Flow(
        provider_id="demo",
        goal="quote",
        start_url="https://demo.dk",
        steps=[],
    )
    assert flow.robustness == 0


# ------------------------------------------------------------------ heuristics


def test_danish_prices_are_normalised_to_digits() -> None:
    assert normalise_price("3.499") == "3499"
    assert normalise_price("12 000") == "12000"


def test_price_card_yields_company_and_period() -> None:
    record = parse_card("Alm. Brand\n3.499 kr / år\nSelvrisiko 5.000 kr")
    assert record is not None
    assert record.data["company"] == "Alm. Brand"
    assert record.data["price"] == "3499"
    assert record.data["currency"] == "DKK"
    assert record.data["period"] == "year"


def test_card_without_a_price_is_ignored() -> None:
    assert parse_card("Sammenlign forsikringer") is None
