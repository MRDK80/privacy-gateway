"""Pure neutral representation only; successful encoding proves no source authority."""

from __future__ import annotations

import re
from typing import Any

from tools.agent_continuation import canonical, digest
from tools.agent_learning_planning_promotion import VALIDATORS as PLANNING_KINDS
from tools.agent_learning_records import VALIDATORS as WORKFLOW_KINDS
from tools.agent_learning_store import KEY_FIELDS

LESSON_FIELDS = (
    "lesson_id", "verification_digest", "candidate_digest", "before_digest",
    "after_digest", "pair_digest",
)
FIELDS = {
    "tasks": "task workflow_run_id workflow_calls planning_attempts",
    "calls": (
        "task family role ordinal invocation_key native_invocation_digest "
        "metadata_digest binding_digest source_digest"
    ),
    "retained": "family task kind key record_digest",
    "uses": "call_key ordered_lessons",
    "measurements": (
        "family task run_id measurement_digest baseline_digest evaluation_digest"
    ),
    "negative_checks": "family task source_digest",
}


class PacketShapeError(ValueError):
    """A bounded shape refusal, without echoing supplied values."""


def _require(condition: bool) -> None:
    if not condition:
        raise PacketShapeError("NEUTRAL_PACKET_INVALID")


def _object(raw: Any, fields: str) -> dict[str, Any]:
    _require(type(raw) is dict and set(raw) == set(fields.split()))
    return {key: raw[key] for key in fields.split()}


def _integer(raw: Any, minimum: int = 0) -> None:
    _require(type(raw) is int and raw >= minimum)


def _hex(raw: Any, size: int = 64, *, nullable: bool = False) -> None:
    if nullable and raw is None:
        return
    _require(type(raw) is str and re.fullmatch(f"[0-9a-f]{{{size}}}", raw) is not None)


def _origin(row: dict[str, Any]) -> tuple[str, int]:
    family, task = row["family"], row["task"]
    _require(family in ("WORKFLOW_STORE", "PLANNING_STORE"))
    if family == "WORKFLOW_STORE":
        _integer(task, 1)
    else:
        _require(task is None)
    return family, -1 if task is None else task


def _row(name: str, raw: Any) -> dict[str, Any]:
    row = _object(raw, FIELDS[name])
    if name == "tasks":
        _integer(row["task"], 1)
        _integer(row["workflow_calls"])
        _integer(row["planning_attempts"])
        _hex(row["workflow_run_id"], 32, nullable=True)
        _require(row["workflow_run_id"] is not None or row["workflow_calls"] == 0)
    elif name == "calls":
        _integer(row["task"], 1)
        _integer(row["ordinal"], 1)
        family = row["family"]
        _require(family in ("WORKFLOW", "PLANNING"))
        roles = ("executor", "controller") if family == "WORKFLOW" else (
            "planner", "planning_review"
        )
        _require(row["role"] in roles)
        for field in ("invocation_key", "metadata_digest", "source_digest"):
            _hex(row[field])
        for field in ("native_invocation_digest", "binding_digest"):
            if family == "PLANNING":
                _require(row[field] is None)
            else:
                _hex(row[field])
    elif name in ("retained", "negative_checks"):
        family, _ = _origin(row)
        if name == "retained":
            kinds = WORKFLOW_KINDS if family == "WORKFLOW_STORE" else PLANNING_KINDS
            _require(type(row["kind"]) is str and row["kind"] in kinds)
            _require(row["kind"] in KEY_FIELDS)
            _hex(row["key"])
            _hex(row["record_digest"])
        else:
            _hex(row["source_digest"])
    elif name == "uses":
        _hex(row["call_key"])
        _require(type(row["ordered_lessons"]) is list)
        lessons: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_lesson in row["ordered_lessons"]:
            lesson = _object(raw_lesson, " ".join(LESSON_FIELDS))
            for value in lesson.values():
                _hex(value)
            _require(lesson["lesson_id"] not in seen)
            seen.add(lesson["lesson_id"])
            lessons.append(lesson)
        row["ordered_lessons"] = lessons
    else:
        _require(row["family"] == "WORKFLOW")
        _integer(row["task"], 1)
        _hex(row["run_id"], 32)
        _hex(row["measurement_digest"])
        _hex(row["baseline_digest"], nullable=True)
        _hex(row["evaluation_digest"], nullable=True)
        _require(row["baseline_digest"] is None or row["evaluation_digest"] is not None)
    return row


def _key(name: str, row: dict[str, Any]) -> tuple[Any, ...]:
    if name == "tasks":
        return (row["task"],)
    if name == "calls":
        return tuple(row[field] for field in (
            "task", "family", "role", "ordinal", "invocation_key"
        ))
    if name == "uses":
        return (row["call_key"],)
    if name == "measurements":
        return row["task"], row["run_id"]
    origin = _origin(row)
    return (*origin, row["kind"], row["key"]) if name == "retained" else origin


def _check_joins(packet: dict[str, Any]) -> None:
    tasks = {row["task"]: row for row in packet["tasks"]}
    calls = {row["invocation_key"]: row for row in packet["calls"]}
    _require(len(calls) == len(packet["calls"]))
    _require(set(calls) == {row["call_key"] for row in packet["uses"]})
    for call in calls.values():
        _require(call["task"] in tasks)
    for task, row in tasks.items():
        for family, count in (("WORKFLOW", "workflow_calls"),
                              ("PLANNING", "planning_attempts")):
            _require(row[count] == sum(
                call["task"] == task and call["family"] == family
                for call in calls.values()
            ))
    negative = {_origin(row) for row in packet["negative_checks"]}
    for row in packet["retained"]:
        _require(_origin(row) in negative)
    for name in ("retained", "negative_checks"):
        for row in packet[name]:
            _require(row["task"] is None or row["task"] in tasks)
    for row in packet["measurements"]:
        _require(row["task"] in tasks)
        _require(row["run_id"] == tasks[row["task"]]["workflow_run_id"])


def encode_packet(
    *, identity: Any, tasks: Any, calls: Any, retained: Any, uses: Any,
    measurements: Any, negative_checks: Any, max_artifact_bytes: int,
) -> dict[str, Any]:
    """Build closed rows only. The fixed future collector must prove all sources.

    Limit must come from its originally validated preparation profile. No default,
    provenance claim, disposition, source reader or authority decision is made here.
    Original input/ordinal/hint/store bounds remain the collector's responsibility.
    """
    _integer(max_artifact_bytes, 1)
    _require(max_artifact_bytes <= 262144)
    owner = _object(identity, (
        "repository epic policy_sha mandate_digest launch_digest "
        "learning_profile_digest"
    ))
    _require(type(owner["repository"]) is str and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*",
        owner["repository"],
    ) is not None)
    _integer(owner["epic"], 1)
    _require(owner["epic"] <= 2147483647)
    _hex(owner["policy_sha"], 40)
    for field in ("mandate_digest", "launch_digest", "learning_profile_digest"):
        value = owner[field]
        _require(type(value) is str and value.startswith("sha256:"))
        _hex(value[7:])
    packet: dict[str, Any] = {"identity": owner, **{name: [] for name in FIELDS}}
    packet["source_digest"] = "0" * 64

    def cap() -> None:
        _require(len(canonical(packet)) <= max_artifact_bytes)

    cap()
    supplied = {
        "tasks": tasks, "calls": calls, "retained": retained, "uses": uses,
        "measurements": measurements, "negative_checks": negative_checks,
    }
    for name, rows in supplied.items():
        _require(type(rows) is list)
        keys: set[tuple[Any, ...]] = set()
        for raw in rows:
            row = _row(name, raw)
            key = _key(name, row)
            _require(key not in keys)
            keys.add(key)
            packet[name].append(row)
            cap()
        packet[name].sort(key=lambda row: _key(name, row))
    _check_joins(packet)
    del packet["source_digest"]
    packet["source_digest"] = digest(packet)
    cap()
    return packet
