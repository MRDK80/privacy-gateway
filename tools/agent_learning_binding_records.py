"""Prospective workflow binding shapes; originals and ownership remain mandatory."""

from __future__ import annotations

import json
from typing import Any

from tools import agent_learning as core
from tools import agent_learning_records as records
from tools.agent_continuation import digest

FIELDS = {
    "identity",
    "role",
    "ordinal",
    "repair_iteration",
    "invocation_key",
    "stable_metadata_digest",
    "native_invocation_digest",
    "native_reservation_digest",
    "checkpoint_digest",
    "ordered_lessons",
    "selection_digest",
    "input_digest",
    "prompt_digest",
    "status",
    "completion",
}
REFERENCE = {
    "lesson_id",
    "verification_digest",
    "candidate_digest",
    "before_digest",
    "after_digest",
    "pair_digest",
}
COMPLETION = {
    "native_reservation_digest",
    "settings_source_digest",
    "settings_receipt_digest",
    "terminal_digest",
    "completed_metadata_digest",
}


def stable_metadata(value: dict[str, Any]) -> str:
    value = records.invocation(value)
    return digest(
        {
            key: item
            for key, item in value.items()
            if key not in {"schema_version", "record_digest", "status"}
        }
    )


def binding(raw: Any, *, max_repairs: int, max_hints: int) -> dict[str, Any]:
    core.integer(max_repairs, 2)
    core.integer(max_hints, 3)
    value = core.sealed(raw, FIELDS)
    identity = records.identity(value["identity"], invocation=True)
    core.hash_value(identity["original_run_id"], size=32)
    if not isinstance(value["role"], str) or value["role"] not in {
        "executor",
        "controller",
    }:
        raise core.LearningError("LEARNING_INVOCATION_INVALID")
    core.integer(value["ordinal"], max_repairs + 1, 1)
    core.integer(value["repair_iteration"], max_repairs)
    if (
        value["role"] == "executor"
        and value["ordinal"] != value["repair_iteration"] + 1
    ):
        raise core.LearningError("LEARNING_INVOCATION_INVALID")
    if value["invocation_key"] != records.invocation_id(value):
        raise core.LearningError("LEARNING_INVOCATION_INVALID")
    for key in FIELDS - {
        "identity",
        "role",
        "ordinal",
        "repair_iteration",
        "ordered_lessons",
        "status",
        "completion",
    }:
        core.hash_value(value[key])
    rows = value["ordered_lessons"]
    if not isinstance(rows, list) or len(rows) > max_hints:
        raise core.LearningError("LEARNING_INVOCATION_INVALID")
    lessons: set[str] = set()
    for row in rows:
        core.exact(row, REFERENCE)
        for item in row.values():
            core.hash_value(item)
        if row["lesson_id"] in lessons:
            raise core.LearningError("LEARNING_INVOCATION_INVALID")
        lessons.add(row["lesson_id"])
    if value["status"] == "RESERVED":
        if value["completion"] is not None:
            raise core.LearningError("LEARNING_INVOCATION_INVALID")
    elif value["status"] == "COMPLETED":
        completion = core.exact(value["completion"], COMPLETION)
        for item in completion.values():
            core.hash_value(item)
    else:
        raise core.LearningError("LEARNING_INVOCATION_INVALID")
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise core.LearningError("LEARNING_INVOCATION_INVALID")
    return value


def decode(
    raw: bytes, *, max_bytes: int, max_repairs: int, max_hints: int
) -> dict[str, Any]:
    core.integer(max_bytes, core.LIMITS["max_record_bytes"], 1)
    if len(raw) > max_bytes:
        raise core.LearningError("LEARNING_STORAGE_EXHAUSTED")
    try:
        value = json.loads(raw, object_pairs_hook=_object)
    except (ValueError, TypeError, RecursionError):
        raise core.LearningError("LEARNING_INVOCATION_INVALID") from None
    return binding(value, max_repairs=max_repairs, max_hints=max_hints)
