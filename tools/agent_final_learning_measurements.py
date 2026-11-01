"""Private retained originals and fixed terminal workflow measurements."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tools.agent_continuation import canonical, digest
from tools.agent_learning import LearningError
from tools.agent_learning_measure import _observed_with_workflow_fact, comparison
from tools.agent_learning_store import KEY_FIELDS


@dataclass(frozen=True, slots=True, repr=False)
class _OriginSource:
    family: str
    task: int | None
    kind: str
    key: str
    record_bytes: bytes


@dataclass(frozen=True, slots=True, repr=False)
class _OriginSnapshot:
    records: tuple[_OriginSource, ...]
    membership_bytes: bytes


@dataclass(frozen=True, slots=True, repr=False)
class MeasurementRow:
    family: str
    task: int
    run_id: str
    measurement_digest: str
    baseline_digest: str | None
    evaluation_digest: str | None


def _require(value: bool) -> None:
    if not value:
        raise LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")


def _origin_snapshot(
    origins: list[dict[str, Any]], membership: list[dict[str, Any]],
) -> _OriginSnapshot:
    """Same-branch representation only; caller shapes are not source proof."""
    return _OriginSnapshot(
        tuple(_OriginSource(
            item["family"], item["task"], item["kind"],
            item["record"][KEY_FIELDS[item["kind"]]], canonical(item["record"]),
        ) for item in origins),
        canonical(membership),
    )


def _object(source: _OriginSource) -> dict[str, Any]:
    value = json.loads(source.record_bytes)
    _require(isinstance(value, dict))
    return dict(value)


def _persisted(
    origins: _OriginSnapshot, task: int, kind: str, value: dict[str, Any],
) -> None:
    expected = canonical(value)
    _require(any(
        item.family == "WORKFLOW_STORE" and item.task == task
        and item.kind == kind and item.record_bytes == expected
        for item in origins.records
    ))


def _row(
    task: int, run_id: str, value: dict[str, Any],
    baseline: dict[str, Any] | None, evaluation: dict[str, Any] | None,
) -> MeasurementRow:
    """Neutral projection; persistence and comparisons proved by owned reader."""
    if value["hint_set_digest"] is None:
        _require(baseline is None and evaluation is None)
    else:
        _require(evaluation is not None)
    return MeasurementRow(
        "WORKFLOW", task, run_id, digest(value),
        digest(baseline) if baseline is not None else None,
        digest(evaluation) if evaluation is not None else None,
    )


def _project_measurements(
    reader: Any, session: Any, universe: tuple[int, ...],
    origins: _OriginSnapshot, task_facts: tuple[Any, ...],
) -> tuple[MeasurementRow, ...]:
    # Original comparison consumes WORKFLOW retained union, not PLANNING Store.
    retained = [{"kind": item.kind, "record": _object(item)}
                for item in origins.records if item.family == "WORKFLOW_STORE"]
    facts = {fact.task: fact for fact in task_facts}
    _require(len(facts) == len(task_facts) and set(facts) == set(universe))
    rows = []
    for task in universe:
        reader.guard()
        (value, metadata), workflow = _observed_with_workflow_fact(
            reader, session, task
        )
        fact = facts[task]
        _require(
            workflow.state["run_id"] == fact.workflow_run_id
            and workflow.state["executor_calls"] == fact.executor_calls
            and workflow.state["controller_calls"] == fact.controller_calls
        )
        # Current source-run records cannot hide a conflicting measurement behind
        # a matching key/digest. Historical other-run originals remain retained.
        current = [item for item in origins.records
                   if item.family == "WORKFLOW_STORE" and item.task == task
                   and item.kind == "measurement"
                   and _object(item)["source_run_digest"] == value["source_run_digest"]]
        _require(bool(current) and all(item.record_bytes == canonical(value)
                                      for item in current))
        _persisted(origins, task, "measurement", value)
        baseline: dict[str, Any] | None = None
        evaluation: dict[str, Any] | None = None
        if any(item["lesson_ids"] for item in metadata):
            reader.controller(task)
            # verified_baseline False is an original candidate filter; exceptions
            # propagate unchanged. Empty verified set gives original null baseline.
            baseline, evaluation = comparison(reader, session, value, retained)
            _persisted(origins, task, "evaluation", evaluation)
        rows.append(_row(task, workflow.state["run_id"], value, baseline, evaluation))
        reader.guard()
    return tuple(sorted(rows, key=lambda row: (row.task, row.run_id)))


def _read_measurements(historical: Any, session: Any) -> tuple[MeasurementRow, ...]:
    from tools.agent_final_learning_binding_source import (
        _read_workflow_origins_snapshot,
    )
    from tools.agent_final_learning_domain import (
        _FinalPlanningReader,
        _PreAggregatePlanningReader,
    )

    _require(type(historical) in {_FinalPlanningReader, _PreAggregatePlanningReader})
    _require(historical.session is session)
    reader = historical.reader
    universe = reader._source_tasks()
    original = _read_workflow_origins_snapshot(reader, session)
    rows = _project_measurements(reader, session, universe, original[4], original[2])
    final = _read_workflow_origins_snapshot(reader, session)
    _require(final == original)
    _require(
        _project_measurements(reader, session, universe, final[4], final[2]) == rows
    )
    _require(reader._source_tasks() == universe)
    historical._guard()
    return rows


def read_measurement_rows_owned(session: Any) -> tuple[MeasurementRow, ...]:
    from tools.agent_final_learning_domain import _FinalPlanningReader

    reader = _FinalPlanningReader(session)
    try:
        return _read_measurements(reader, session)
    finally:
        reader.reader.close()


def read_preaggregate_measurement_rows_owned(
    session: Any,
) -> tuple[MeasurementRow, ...]:
    from tools.agent_final_learning_domain import _PreAggregatePlanningReader

    reader = _PreAggregatePlanningReader(session)
    try:
        return _read_measurements(reader, session)
    finally:
        reader.reader.close()
