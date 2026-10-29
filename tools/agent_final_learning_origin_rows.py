"""Immutable neutral origins, exposed only after the fixed full source read."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.agent_continuation import digest
from tools.agent_final_learning_domain import (
    _FinalSourceReader,
    _PreAggregateFinalSourceReader,
    preaggregate_source_scope_owned,
)
from tools.agent_final_preparation_runtime import _CurrentFinalSources
from tools.agent_learning import LearningError
from tools.agent_learning_store import KEY_FIELDS


@dataclass(frozen=True, slots=True, repr=False)
class RetainedRow:
    family: str
    task: int | None
    kind: str
    key: str
    record_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class NegativeRow:
    family: str
    task: int | None
    source_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class OriginRows:
    retained: tuple[RetainedRow, ...]
    negative_checks: tuple[NegativeRow, ...]


def _project_origin_rows(
    origins: list[dict[str, Any]], membership: list[dict[str, Any]]
) -> OriginRows:
    """Pure approved representation, never validation or source authority."""
    retained: list[RetainedRow] = []
    negatives: list[NegativeRow] = []
    for origin in sorted(
        membership,
        key=lambda value: (
            value["family"],
            -1 if value["task"] is None else value["task"],
        ),
    ):
        if not origin["present"]:
            continue
        family, task = origin["family"], origin["task"]
        items = [
            item for item in origins if (item["family"], item["task"]) == (family, task)
        ]
        records = sorted(
            (
                RetainedRow(
                    family,
                    task,
                    item["kind"],
                    item["record"][KEY_FIELDS[item["kind"]]],
                    digest(item["record"]),
                )
                for item in items
            ),
            key=lambda row: (row.kind, row.key),
        )
        retained.extend(records)
        rollback_evaluations = {
            digest(item["record"])
            for item in items
            if item["kind"] == "evaluation"
            and item["record"]["comparison"]["status"] == "ROLLBACK_REQUIRED"
        }
        rollback = [
            digest(item["record"])
            for item in items
            if (
                item["kind"] == "evaluation"
                and digest(item["record"]) in rollback_evaluations
            )
            or (
                item["kind"] == "tombstone"
                and item["record"]["evaluation_digest"] in rollback_evaluations
            )
        ]
        preimage = {
            "family": family,
            "task": task,
            "records": [
                {"kind": row.kind, "key": row.key, "record_digest": row.record_digest}
                for row in records
            ],
            "tombstones": sorted(
                digest(item["record"]) for item in items if item["kind"] == "tombstone"
            ),
            "evaluations": sorted(
                digest(item["record"]) for item in items if item["kind"] == "evaluation"
            ),
            "rollback_records": sorted(rollback),
            "barrier": None,
        }
        negatives.append(NegativeRow(family, task, digest(preimage)))
    return OriginRows(tuple(retained), tuple(negatives))


def read_origin_rows_owned(session: Any) -> OriginRows:
    """Completed-owned full source proof; no caller facts or reader injection."""
    from tools.agent_final_learning_binding_source import _read_workflow_source_snapshot

    pins = _CurrentFinalSources(session)
    reader = _FinalSourceReader(session)
    try:
        _, rows = _read_workflow_source_snapshot(reader, session)
        reader._source_tasks()
        pins.check()
        return rows
    finally:
        reader.close()


def read_preaggregate_origin_rows_owned(session: Any) -> OriginRows:
    """Only the approved original-zero factory, with its final scope guard."""
    from tools.agent_final_learning_binding_source import _read_workflow_source_snapshot

    pins = _CurrentFinalSources(session)
    with preaggregate_source_scope_owned(session) as reader:
        if type(reader) is not _PreAggregateFinalSourceReader:
            raise LearningError("LEARNING_SOURCE_MISMATCH")
        assert isinstance(reader, _PreAggregateFinalSourceReader)
        _, rows = _read_workflow_source_snapshot(reader, session)
        reader._source_tasks()
        pins.check()
        return rows
