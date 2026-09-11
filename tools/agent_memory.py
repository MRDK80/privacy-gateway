#!/usr/bin/env python3
"""Private append-only retrospectives and aggregate metrics for agent runs."""

from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = "1.0"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TASK_CLASS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ALLOWED_VERDICTS = {"PASS", "PASS_WITH_NOTES", "FAIL_RETRY", "FAIL_ESCALATE"}
ALLOWED_USAGE_SOURCES = {"unavailable", "provider", "host"}
PROMOTION_TARGETS = {"test", "validator", "policy", "role", "skill", "configuration"}


class MemoryError(Exception):
    """Controlled failure safe to expose without record contents."""

    def __init__(self, machine_code: str) -> None:
        super().__init__(machine_code)
        self.machine_code = machine_code


@dataclass(frozen=True)
class Usage:
    source: str = "unavailable"
    units: int | None = None


@dataclass(frozen=True)
class Retrospective:
    schema_version: str
    record_id: str
    recorded_at: str
    task_issue: int
    epic_issue: int
    task_class: str
    base_sha: str
    head_sha: str
    duration_seconds: int
    executor_calls: int
    controller_calls: int
    iterations: int
    repair_loops: int
    verdicts: tuple[str, ...]
    failures: tuple[str, ...]
    false_positives: int
    manual_interventions: int
    findings: int
    usage: Usage


def default_directory() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "privacy-gateway" / "agent-memory"


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _non_negative(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_record(value: Mapping[str, Any]) -> None:
    """Validate the complete private record contract without echoing its values."""
    expected = {
        "schema_version",
        "record_id",
        "recorded_at",
        "task_issue",
        "epic_issue",
        "task_class",
        "base_sha",
        "head_sha",
        "duration_seconds",
        "executor_calls",
        "controller_calls",
        "iterations",
        "repair_loops",
        "verdicts",
        "failures",
        "false_positives",
        "manual_interventions",
        "findings",
        "usage",
    }
    if set(value) != expected or value.get("schema_version") != SCHEMA_VERSION:
        raise MemoryError("INVALID_RECORD")
    if not all(
        _non_negative(value[key])
        for key in (
            "duration_seconds",
            "executor_calls",
            "controller_calls",
            "iterations",
            "repair_loops",
            "false_positives",
            "manual_interventions",
            "findings",
        )
    ):
        raise MemoryError("INVALID_RECORD")
    if not all(
        isinstance(value.get(key), int) and value[key] >= 1
        for key in (
            "task_issue",
            "epic_issue",
        )
    ):
        raise MemoryError("INVALID_RECORD")
    if value["iterations"] != value["controller_calls"]:
        raise MemoryError("INVALID_RECORD")
    if value["repair_loops"] > 2:
        raise MemoryError("INVALID_RECORD")
    if not isinstance(value.get("record_id"), str) or not re.fullmatch(
        r"[0-9a-f]{32}", value["record_id"]
    ):
        raise MemoryError("INVALID_RECORD")
    try:
        parsed = datetime.fromisoformat(cast(str, value["recorded_at"]))
    except (TypeError, ValueError) as error:
        raise MemoryError("INVALID_RECORD") from error
    if parsed.tzinfo is None:
        raise MemoryError("INVALID_RECORD")
    if not isinstance(value.get("task_class"), str) or not TASK_CLASS_RE.fullmatch(
        value["task_class"]
    ):
        raise MemoryError("INVALID_RECORD")
    if not all(
        isinstance(value.get(key), str) and SHA_RE.fullmatch(value[key])
        for key in (
            "base_sha",
            "head_sha",
        )
    ):
        raise MemoryError("INVALID_RECORD")
    verdicts = value.get("verdicts")
    failures = value.get("failures")
    if (
        not isinstance(verdicts, list)
        or not verdicts
        or not all(
            isinstance(item, str) and item in ALLOWED_VERDICTS for item in verdicts
        )
    ):
        raise MemoryError("INVALID_RECORD")
    if not isinstance(failures, list) or not all(
        isinstance(item, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", item)
        for item in failures
    ):
        raise MemoryError("INVALID_RECORD")
    usage = value.get("usage")
    if not isinstance(usage, dict) or set(usage) != {"source", "units"}:
        raise MemoryError("INVALID_RECORD")
    if usage["source"] not in ALLOWED_USAGE_SOURCES:
        raise MemoryError("INVALID_RECORD")
    units = usage["units"]
    if usage["source"] == "unavailable":
        if units is not None:
            raise MemoryError("INVALID_RECORD")
    elif not _non_negative(units):
        raise MemoryError("INVALID_RECORD")


class RetrospectiveStore:
    """Owner-only JSONL store whose history is append-only by API contract."""

    def __init__(self, directory: Path, repository_root: Path) -> None:
        if _is_inside(directory, repository_root):
            raise MemoryError("UNSAFE_STORAGE")
        self.directory = directory
        self.path = directory / "retrospectives.jsonl"

    def append(self, record: Retrospective) -> None:
        value = asdict(record)
        value["verdicts"] = list(record.verdicts)
        value["failures"] = list(record.failures)
        validate_record(value)
        for existing in self.records():
            if existing["record_id"] == record.record_id:
                if existing != value:
                    raise MemoryError("RECORD_ID_CONFLICT")
                return
        line = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.directory, 0o700)
            descriptor = os.open(
                self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600
            )
            try:
                os.write(descriptor, f"{line}\n".encode())
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise MemoryError("RECORD_WRITE_FAILED") from error

    def records(self) -> Iterable[dict[str, Any]]:
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise MemoryError("INVALID_RECORD")
                    validate_record(value)
                    yield cast(dict[str, Any], value)
        except (OSError, ValueError) as error:
            raise MemoryError("INVALID_RECORD") from error


def new_record(**values: Any) -> Retrospective:
    """Create a record from observable run outcomes only."""
    return Retrospective(
        schema_version=SCHEMA_VERSION,
        record_id=values.pop("record_id", uuid.uuid4().hex),
        recorded_at=datetime.now(UTC).isoformat(),
        usage=values.pop("usage", Usage()),
        **values,
    )


def aggregate(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return compact trends by task class; raw records are not returned."""
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        validate_record(record)
        buckets[cast(str, record["task_class"])].append(record)
    result: list[dict[str, Any]] = []
    for task_class, items in sorted(buckets.items()):
        count = len(items)
        usage_values = [
            item["usage"]["units"]
            for item in items
            if item["usage"]["source"] != "unavailable"
        ]
        result.append(
            {
                "task_class": task_class,
                "tasks": count,
                "average_duration_seconds": sum(
                    item["duration_seconds"] for item in items
                )
                / count,
                "average_iterations": sum(item["iterations"] for item in items) / count,
                "average_findings": sum(item["findings"] for item in items) / count,
                "failures": sum(len(item["failures"]) for item in items),
                "usage_observations": len(usage_values),
                "average_usage_units": (
                    sum(usage_values) / len(usage_values) if usage_values else None
                ),
            }
        )
    return result


def validate_promotion(value: Mapping[str, Any]) -> None:
    """Fail closed unless a candidate passed every publication boundary."""
    expected = {
        "schema_version",
        "candidate_id",
        "target",
        "evidence_count",
        "classified",
        "sanitized",
        "secret_privacy_scan_passed",
        "safe_for_full_disclosure",
        "independent_review_passed",
        "trust_source",
        "head_instructions_applied",
        "separate_change",
        "human_approval",
    }
    if set(value) != expected or value.get("schema_version") != SCHEMA_VERSION:
        raise MemoryError("PUBLICATION_DENIED")
    target = value.get("target")
    if target not in PROMOTION_TARGETS or not isinstance(
        value.get("candidate_id"), str
    ):
        raise MemoryError("PUBLICATION_DENIED")
    if not re.fullmatch(r"[0-9a-f]{32}", cast(str, value["candidate_id"])):
        raise MemoryError("PUBLICATION_DENIED")
    if not isinstance(value.get("evidence_count"), int) or value["evidence_count"] < 2:
        raise MemoryError("PUBLICATION_DENIED")
    required_true = (
        "classified",
        "sanitized",
        "secret_privacy_scan_passed",
        "safe_for_full_disclosure",
        "independent_review_passed",
        "separate_change",
    )
    if any(value.get(key) is not True for key in required_true):
        raise MemoryError("PUBLICATION_DENIED")
    if value.get("trust_source") not in {"base_sha", "local_read_only_bundle"}:
        raise MemoryError("PUBLICATION_DENIED")
    if value.get("head_instructions_applied") is not False:
        raise MemoryError("PUBLICATION_DENIED")
    requires_human = target in {"policy", "role", "skill", "configuration"}
    if requires_human and value.get("human_approval") is not True:
        raise MemoryError("PUBLICATION_DENIED")
    if not requires_human and value.get("human_approval") not in {True, False}:
        raise MemoryError("PUBLICATION_DENIED")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("report",))
    parser.add_argument("--storage", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        store = RetrospectiveStore(args.storage or default_directory(), args.root)
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "trends": aggregate(store.records()),
                },
                sort_keys=True,
            )
        )
    except MemoryError as error:
        print(
            json.dumps(
                {"status": "failed", "machine_code": error.machine_code}, sort_keys=True
            )
        )
        return 20
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
