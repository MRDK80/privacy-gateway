"""Owner wait locators never renew launch anchors or final call accounting."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from tools import agent_final_preparation_records as records
from tools.agent_epic_loop import PhaseBlocked

from tests.test_agent_final_preparation_records import original


def anchor() -> dict[str, Any]:
    return {
        "invocation_id": "a" * 32,
        **{
            key: "sha256:" + "b" * 64
            for key in (
                "frame_digest",
                "wait_identity_digest",
                "runtime_config_digest",
                "control_digest",
            )
        },
        "stage_started_at": 100,
        "deadline_at": 200.5,
    }


def state(value: Any = None, *, phase: str = "PREPARING") -> dict[str, Any]:
    return records.seal(
        {
            **original("state"),
            "schema_version": "2.0",
            "owner_wait": value,
            "phase": phase,
            "blocked_code": ("FINAL_APPROVAL_INVALID" if phase == "BLOCKED" else None),
        }
    )


def test_publication_is_not_admission_and_preserves_numeric_representation() -> None:
    before = state()
    published = state(anchor(), phase="WAITING_OWNER_FINAL_APPROVAL")
    saved = copy.deepcopy((before, published))
    assert records.state2_transition(before, published) == published
    assert (before, published) == saved
    parsed = records.state2(published)["owner_wait"]
    assert type(parsed["stage_started_at"]) is int
    assert type(parsed["deadline_at"]) is float
    # Null shape remains legal even at wait: source admission is a future boundary.
    assert records.state2(state(None, phase="WAITING_OWNER_FINAL_APPROVAL"))


@pytest.mark.parametrize(
    "phase",
    [
        "PREPARING",
        "WAITING_OWNER_FINAL_APPROVAL",
        "MERGING",
        "MAIN_CI_PENDING",
        "DONE",
        "BLOCKED",
    ],
)
def test_anchor_survives_every_phase_including_candidate_refresh(phase: str) -> None:
    before = state(anchor())
    after = state(anchor(), phase=phase)
    after["candidate_digest"] = "f" * 64
    assert (
        records.state2_transition(before, records.seal(after))["owner_wait"] == anchor()
    )


@pytest.mark.parametrize("field", sorted(records.OWNER_WAIT) + ["remove", "numeric"])
def test_resealed_anchor_cannot_be_removed_rebound_or_renormalized(field: str) -> None:
    before = state(anchor())
    changed = anchor()
    if field == "remove":
        changed_value = None
    elif field == "numeric":
        changed["stage_started_at"] = 100.0
        changed_value = changed
    else:
        changed[field] = (
            300
            if field in {"stage_started_at", "deadline_at"}
            else ("c" * 32 if field == "invocation_id" else "sha256:" + "c" * 64)
        )
        changed_value = changed
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.state2_transition(before, state(changed_value))


@pytest.mark.parametrize("field", sorted(records.IDENTITY))
def test_transition_cannot_move_anchor_to_another_launch(field: str) -> None:
    after = state(anchor())
    after[field] = {"repository": "OTHER/repo", "epic": 295}.get(
        field, "sha256:" + "c" * 64
    )
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.state2_transition(state(anchor()), records.seal(after))


def test_calls_spent_cannot_refund_but_initial_spend_remains_possible() -> None:
    fresh = state()
    fresh["calls_consumed"] = 0
    fresh = records.seal(fresh)
    assert records.state2_transition(fresh, state())["calls_consumed"] == 1
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.state2_transition(state(), fresh)


@pytest.mark.parametrize("field", ["stage_started_at", "deadline_at"])
@pytest.mark.parametrize(
    "value", [True, False, -1, "100", None, float("nan"), float("inf"), -float("inf")]
)
def test_time_shape_rejects_bool_nonfinite_and_unknown(field: str, value: Any) -> None:
    value_anchor = anchor()
    value_anchor[field] = value
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.owner_wait(value_anchor)


@pytest.mark.parametrize("attack", ["extra", "missing", "path", "digest", "version"])
def test_closed_state2_and_anchor_refuse_resealed_unknown_shapes(attack: str) -> None:
    value = state(anchor())
    if attack == "extra":
        value["owner_wait"]["approved"] = True
    elif attack == "missing":
        value["owner_wait"].pop("control_digest")
    elif attack == "path":
        value["owner_wait"]["invocation_id"] = "../frame.json"
    elif attack == "digest":
        value["owner_wait"]["frame_digest"] = "b" * 64
    else:
        value["schema_version"] = "1.0"
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.state2(records.seal(value))


def test_legacy_remains_distinct_without_migration_or_backfill() -> None:
    legacy = original("state")
    assert records.state(legacy) == legacy
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.state2(legacy)
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.state(state())
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.state2_transition(legacy, state(anchor()))
