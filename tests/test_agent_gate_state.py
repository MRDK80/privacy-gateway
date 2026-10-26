"""Phase/session completion is stricter than a printed pytest dot."""

from __future__ import annotations

from typing import Any

import pytest
from tools.agent_gate_journal import GateBlocked
from tools.agent_gate_state import State, validate_plan


def plan() -> dict[str, Any]:
    return {
        "version": 1,
        "review": "a" * 64,
        "inventory": ["test_synthetic.py::test_one", "test_synthetic.py::test_two"],
        "units": [["test_synthetic.py::test_one"], ["test_synthetic.py::test_two"]],
        "allowed_skips": [],
    }


def reports(state: State, unit: int, *, teardown: str = "passed") -> None:
    node = state.plan["units"][unit][0]
    state.apply({"kind": "dispatch", "unit": unit, "attempt": 1})
    state.apply({"kind": "collected", "nodeid": node})
    state.apply({"kind": "started", "nodeid": node})
    for when in ("setup", "call", "teardown"):
        state.apply(
            {
                "kind": "report",
                "nodeid": node,
                "when": when,
                "outcome": teardown if when == "teardown" else "passed",
                "duration": 0.1,
                "xfail": False,
            }
        )


def terminal(state: State) -> None:
    state.apply({"kind": "session", "exit_code": 0})
    state.apply(
        {
            "kind": "terminal",
            "exit_code": 0,
            "ownership": "CLEAN",
            "fingerprint": "b" * 64,
        }
    )


def test_completed_unit_is_reused_but_active_unit_is_unknown() -> None:
    state = State(plan())
    reports(state, 0)
    terminal(state)
    assert state.remaining() == [1]
    reports(state, 1)
    with pytest.raises(GateBlocked, match="UNIT_OUTCOME_UNKNOWN"):
        state.remaining()


def test_call_pass_does_not_hide_teardown_failure() -> None:
    state = State(plan())
    reports(state, 0, teardown="failed")
    with pytest.raises(GateBlocked, match="TEARDOWN_FAILURE"):
        terminal(state)


def test_session_error_cannot_be_reused() -> None:
    state = State(plan())
    reports(state, 0)
    state.apply({"kind": "session", "exit_code": 3})
    with pytest.raises(GateBlocked, match="INCOMPLETE_SESSION"):
        state.apply(
            {
                "kind": "terminal",
                "exit_code": 0,
                "ownership": "CLEAN",
                "fingerprint": "b" * 64,
            }
        )


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "order", "unsafe"])
def test_invalid_inventory_is_rejected(mutation: str) -> None:
    value = plan()
    if mutation == "missing":
        value["units"].pop()
    elif mutation == "duplicate":
        value["inventory"].append(value["inventory"][0])
    elif mutation == "order":
        value["units"].reverse()
    else:
        value["inventory"][0] = "unsafe\nlabel"
    with pytest.raises(GateBlocked):
        validate_plan(value)


def test_dispatch_is_not_refunded_after_restart() -> None:
    state = State(plan())
    state.apply({"kind": "dispatch", "unit": 0, "attempt": 1})
    with pytest.raises(GateBlocked, match="REPEATED"):
        state.apply({"kind": "dispatch", "unit": 0, "attempt": 1})


def test_hanging_setup_snapshot_contains_exact_active_node() -> None:
    state = State(plan())
    node = state.plan["units"][0][0]
    state.apply({"kind": "dispatch", "unit": 0, "attempt": 1})
    state.apply({"kind": "collected", "nodeid": node})
    state.apply({"kind": "started", "nodeid": node})
    snapshot = state.snapshot()
    assert snapshot["units"]["0"]["current"] == node
    assert snapshot["units"]["0"]["terminal"] is None


def test_singleton_event_capacity_counterexample_is_rejected() -> None:
    from tools.agent_gate_state import capacity_preflight

    nodes = [f"test_synthetic.py::test_case_{i}" for i in range(11240)]
    value = {
        "version": 1,
        "review": "a" * 64,
        "inventory": nodes,
        "units": [[node] for node in nodes],
        "allowed_skips": [],
    }
    with pytest.raises(GateBlocked, match="PLAN_TOTAL_EVIDENCE_CAPACITY"):
        capacity_preflight(value, 2)


def test_recovery_preflight_counts_existing_journal_and_snapshot() -> None:
    from tools.agent_gate_journal import JOURNAL_LIMIT, SNAPSHOT_LIMIT
    from tools.agent_gate_state import capacity_preflight

    with pytest.raises(GateBlocked, match="PLAN_TOTAL_EVIDENCE_CAPACITY"):
        capacity_preflight(plan(), 2, used_journal_bytes=JOURNAL_LIMIT - 1)
    with pytest.raises(GateBlocked, match="PLAN_TOTAL_EVIDENCE_CAPACITY"):
        capacity_preflight(plan(), 2, used_snapshot_bytes=SNAPSHOT_LIMIT)
