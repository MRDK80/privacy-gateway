"""Neutral persisted relations/refusals; no successful simulated source reader."""
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_measurements import (
    _origin_snapshot,
    _persisted,
    _row,
    read_measurement_rows_owned,
    read_preaggregate_measurement_rows_owned,
)
from tools.agent_learning import LearningError


def snapshot(task: int | None = 301, family: str = "WORKFLOW_STORE") -> Any:
    return _origin_snapshot(
        [{"family": family, "task": task, "kind": "measurement", "record": {
            "measurement_id": "a" * 64, "source_run_digest": "b" * 64,
            "record_digest": "c" * 64, "synthetic": 1,
        }}], [{"family": family, "task": task, "present": True}],
    )


def test_full_origin_snapshot_is_private_immutable_and_keeps_multiplicity() -> None:
    first = snapshot()
    assert "synthetic" not in repr(first)
    assert "synthetic" not in repr(first.records[0])
    with pytest.raises(FrozenInstanceError):
        setattr(first.records[0], "task", 302)
    second = snapshot(302)
    assert first.records[0].record_bytes == second.records[0].record_bytes
    assert first.records[0] != second.records[0]


def test_exact_persisted_relation_uses_full_original_object() -> None:
    value = {"measurement_id": "a" * 64, "source_run_digest": "b" * 64,
             "record_digest": "c" * 64, "synthetic": 1}
    _persisted(snapshot(), 301, "measurement", value)
    value["synthetic"] = 9
    with pytest.raises(LearningError):
        _persisted(snapshot(), 301, "measurement", value)


@pytest.mark.parametrize("task,family", [(302, "WORKFLOW_STORE"),
                                         (None, "PLANNING_STORE")])
def test_foreign_origin_cannot_supply_current_workflow_measurement(
    task: int | None, family: str,
) -> None:
    with pytest.raises(LearningError):
        _persisted(snapshot(task, family), 301, "measurement", {})


def test_missing_current_persistence_cannot_be_computed_into_existence() -> None:
    with pytest.raises(LearningError):
        _persisted(_origin_snapshot([], []), 301, "measurement", {})


def test_zero_use_row_has_no_comparison_and_retains_actual_run() -> None:
    value = {"hint_set_digest": None}
    row = _row(301, "a" * 32, value, None, None)
    assert row.family == "WORKFLOW" and row.run_id == "a" * 32
    assert row.baseline_digest is None and row.evaluation_digest is None
    assert not hasattr(row, "ready")


def test_applied_row_requires_evaluation_and_allows_original_null_baseline() -> None:
    value = {"hint_set_digest": "b" * 64}
    with pytest.raises(LearningError):
        _row(301, "a" * 32, value, None, None)
    row = _row(301, "a" * 32, value, None, {"original": "UNMEASURED"})
    assert row.baseline_digest is None and row.evaluation_digest is not None


@pytest.mark.parametrize("entry", [read_measurement_rows_owned,
                                   read_preaggregate_measurement_rows_owned])
@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_owned_entries_refuse_unowned_session(entry: Any, unowned: Any) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        entry(unowned)
