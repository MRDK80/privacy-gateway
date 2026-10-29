"""Neutral representation cases are not successful source-provenance fixtures."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from tools.agent_continuation import digest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_origin_rows import (
    OriginRows,
    _project_origin_rows,
    read_origin_rows_owned,
    read_preaggregate_origin_rows_owned,
)


@pytest.mark.parametrize(
    "entry", [read_origin_rows_owned, read_preaggregate_origin_rows_owned]
)
@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_fixed_origin_entry_refuses_unowned_session(
    entry: Callable[[Any], OriginRows], unowned: Any
) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        entry(unowned)


def member(family: str, task: int | None, present: bool) -> dict[str, Any]:
    return {"family": family, "task": task, "present": present}


def original(
    kind: str, record: dict[str, Any], task: int | None = 301
) -> dict[str, Any]:
    return {
        "family": "WORKFLOW_STORE" if task is not None else "PLANNING_STORE",
        "task": task,
        "kind": kind,
        "record": record,
    }


def test_present_empty_origin_and_absent_internal_membership_are_distinct() -> None:
    rows = _project_origin_rows(
        [], [member("WORKFLOW_STORE", 301, False), member("PLANNING_STORE", None, True)]
    )
    expected: dict[str, Any] = {
        "family": "PLANNING_STORE",
        "task": None,
        "records": [],
        "tombstones": [],
        "evaluations": [],
        "rollback_records": [],
        "barrier": None,
    }
    assert rows.retained == ()
    assert len(rows.negative_checks) == 1
    assert rows.negative_checks[0].source_digest == digest(expected)
    with pytest.raises(FrozenInstanceError):
        setattr(rows.negative_checks[0], "source_digest", "a" * 64)
    assert not hasattr(rows, "ready") and not hasattr(rows, "records")


def test_all_rollback_evaluations_and_all_referencing_tombstones_are_committed() -> (
    None
):
    first = {"evaluation_id": "a", "comparison": {"status": "ROLLBACK_REQUIRED"}}
    second = {"evaluation_id": "b", "comparison": {"status": "ROLLBACK_REQUIRED"}}
    other = {"evaluation_id": "c", "comparison": {"status": "NO_MEASURED_COMPARISON"}}
    tombs = [
        {"record_digest": str(i), "evaluation_digest": digest(evaluation)}
        for i, evaluation in enumerate((first, first, second, other))
    ]
    source = [original("evaluation", v) for v in (first, second, other)]
    source += [original("tombstone", v) for v in tombs]
    rows = _project_origin_rows(source[::-1], [member("WORKFLOW_STORE", 301, True)])
    expected_records = sorted(
        [
            {
                "kind": "evaluation",
                "key": v["evaluation_id"],
                "record_digest": digest(v),
            }
            for v in (first, second, other)
        ]
        + [
            {"kind": "tombstone", "key": v["record_digest"], "record_digest": digest(v)}
            for v in tombs
        ],
        key=lambda row: (row["kind"], row["key"]),
    )
    expected: dict[str, Any] = {
        "family": "WORKFLOW_STORE",
        "task": 301,
        "records": expected_records,
        "evaluations": sorted(map(digest, (first, second, other))),
        "tombstones": sorted(map(digest, tombs)),
        "rollback_records": sorted(map(digest, [first, second, *tombs[:3]])),
        "barrier": None,
    }
    assert rows.negative_checks[0].source_digest == digest(expected)
    assert [(v.kind, v.key) for v in rows.retained] == [
        (v["kind"], v["key"]) for v in expected_records
    ]
    assert rows == _project_origin_rows(source, [member("WORKFLOW_STORE", 301, True)])


def test_equal_records_in_distinct_origins_preserve_multiplicity() -> None:
    record = {"evaluation_id": "a", "comparison": {"status": "NO_MEASURED_COMPARISON"}}
    rows = _project_origin_rows(
        [original("evaluation", record), original("evaluation", record, None)],
        [member("WORKFLOW_STORE", 301, True), member("PLANNING_STORE", None, True)],
    )
    assert len(rows.retained) == len(rows.negative_checks) == 2
    assert [v.family for v in rows.retained] == ["PLANNING_STORE", "WORKFLOW_STORE"]
    assert rows.retained[0].record_digest == rows.retained[1].record_digest
