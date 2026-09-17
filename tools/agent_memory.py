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

SCHEMA_VERSION = "1.2"
LEGACY_SCHEMA_VERSION = "1.0"
PREVIOUS_SCHEMA_VERSION = "1.1"
SUPPORTED_SCHEMA_VERSIONS = frozenset(
    {LEGACY_SCHEMA_VERSION, PREVIOUS_SCHEMA_VERSION, SCHEMA_VERSION}
)
EVIDENCE_KEYS = frozenset({"gate", "verdict", "snapshot", "durations"})
GATE_EVIDENCE_KEYS = frozenset(
    {
        "profile",
        "profile_version",
        "complete",
        "status",
        "machine_code",
        "expected_checks",
        "executed_checks",
        "checks",
    }
)
CHECK_EVIDENCE_KEYS = frozenset(
    {"name", "status", "exit_code", "duration_seconds", "metrics"}
)
SNAPSHOT_EVIDENCE_KEYS = frozenset(
    {
        "base_sha",
        "snapshot_commit",
        "tree_hash",
        "diff_sha256",
        "snapshot_method",
        "provenance_complete",
        "tree_unchanged",
    }
)
VERDICT_EVIDENCE_KEYS = frozenset(
    {"verdict", "review_basis", "escalation_reason", "blocking_findings"}
)
VERDICT_EVIDENCE_KEYS_V2 = VERDICT_EVIDENCE_KEYS | frozenset({"iteration_history"})
ITERATION_KEYS = frozenset({"iteration", "verdict", "blocking_findings"})
FINDING_KEYS = frozenset(
    {
        "severity",
        "category",
        "location",
        "summary",
        "check_id",
        "finding_fingerprint",
        "redaction",
    }
)
FINDING_TEXT_FIELD_NAMES = frozenset({"requirement", "evidence", "required_fix"})
FINDING_KEYS_V1 = FINDING_KEYS
FINDING_KEYS_V2 = FINDING_KEYS | FINDING_TEXT_FIELD_NAMES
DROP_REASONS = frozenset(
    {"missing", "disallowed_chars", "looks_like_secret", "path_like", "unparseable"}
)
LOCATION_KEYS = frozenset({"path", "line_start", "line_end"})
DURATION_KEYS = frozenset(
    {"total_seconds", "executor_seconds", "controller_seconds", "gate_seconds"}
)
REDACTION_KEYS = frozenset({"applied", "version", "summary_dropped"})
REDACTION_KEYS_V2 = REDACTION_KEYS | frozenset(
    {"dropped_fields", "drop_reasons"}
)
CANONICAL_FINDING_SEVERITIES = frozenset({"critical", "high", "medium", "low"})
LEGACY_FINDING_SEVERITIES = frozenset({"blocking", "major", "minor", "info"})
FINDING_SEVERITIES = (
    CANONICAL_FINDING_SEVERITIES
    | LEGACY_FINDING_SEVERITIES
    | frozenset({"unknown"})
)
FINDING_REDACTION_FAILED = "FINDING_REDACTION_FAILED"
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
EVIDENCE_TEXT_MAX_CHARS = 400
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TASK_CLASS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ALLOWED_VERDICTS = {"PASS", "PASS_WITH_NOTES", "FAIL_RETRY", "FAIL_ESCALATE"}
ALLOWED_USAGE_SOURCES = {"unavailable", "provider", "host"}
PROMOTION_SCHEMA_VERSION = "1.0"
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
    evidence: dict[str, Any] | None = None


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


def _parse_version(value: Any) -> tuple[int, int]:
    """Разобрать schema_version по числовым компонентам, а не как строку."""
    if not isinstance(value, str):
        raise MemoryError("INVALID_RECORD")
    parts = value.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise MemoryError("INVALID_RECORD")
    return int(parts[0]), int(parts[1])


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
    major, minor = _parse_version(value.get("schema_version"))
    current_major, current_minor = _parse_version(SCHEMA_VERSION)
    if major != current_major or minor > current_minor:
        raise MemoryError("INVALID_RECORD")
    if minor > 0:
        expected = expected | {"evidence"}
    if set(value) != expected:
        raise MemoryError("INVALID_RECORD")
    if minor > 0:
        _validate_evidence(value["evidence"])
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


def _fail_record() -> None:
    raise MemoryError("INVALID_RECORD")


def _assert_safe_text(value: str) -> None:
    """Отклонить абсолютные и платформенные пути в сохранённой evidence."""
    if len(value) > EVIDENCE_TEXT_MAX_CHARS:
        _fail_record()
    if value.startswith(("/", "~", "\\")):
        _fail_record()
    if WINDOWS_DRIVE_RE.match(value) or "\\" in value:
        _fail_record()
    if ".." in value:
        _fail_record()


def _assert_safe_tree(value: Any) -> None:
    """Рекурсивно проверить, что evidence не содержит небезопасных строк."""
    if isinstance(value, str):
        _assert_safe_text(value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail_record()
            _assert_safe_tree(item)
        return
    if isinstance(value, list):
        for item in value:
            _assert_safe_tree(item)
        return
    if value is None or isinstance(value, bool | int | float):
        return
    _fail_record()


def _validate_location(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != LOCATION_KEYS:
        _fail_record()
    path = value["path"]
    if path is not None and (not isinstance(path, str) or not path):
        _fail_record()
    for key in ("line_start", "line_end"):
        line = value[key]
        if line is not None and not _non_negative(line):
            _fail_record()


def _validate_finding_texts(value: Mapping[str, Any]) -> None:
    """Проверить раздельные redacted-поля finding версии 2."""
    for key in sorted(FINDING_TEXT_FIELD_NAMES):
        text = value[key]
        if text is not None and (
            not isinstance(text, str) or len(text) > EVIDENCE_TEXT_MAX_CHARS
        ):
            _fail_record()
    redaction = value["redaction"]
    dropped = redaction["dropped_fields"]
    if not isinstance(dropped, list):
        _fail_record()
    if not all(isinstance(item, str) for item in dropped):
        _fail_record()
    if dropped != sorted(set(dropped)):
        _fail_record()
    if any(item not in FINDING_TEXT_FIELD_NAMES for item in dropped):
        _fail_record()
    reasons = redaction["drop_reasons"]
    if not isinstance(reasons, dict):
        _fail_record()
    if set(reasons) != set(dropped):
        _fail_record()
    for reason in reasons.values():
        if reason not in DROP_REASONS:
            _fail_record()


def _validate_finding(value: Any) -> None:
    if not isinstance(value, dict):
        _fail_record()
    if set(value) not in (FINDING_KEYS_V1, FINDING_KEYS_V2):
        _fail_record()
    if value["severity"] not in FINDING_SEVERITIES:
        _fail_record()
    category = value["category"]
    if not isinstance(category, str) or not IDENTIFIER_RE.match(category):
        _fail_record()
    summary = value["summary"]
    if summary is not None and (
        not isinstance(summary, str) or len(summary) > EVIDENCE_TEXT_MAX_CHARS
    ):
        _fail_record()
    check_id = value["check_id"]
    if check_id is not None and (
        not isinstance(check_id, str) or not IDENTIFIER_RE.match(check_id)
    ):
        _fail_record()
    fingerprint = value["finding_fingerprint"]
    if not isinstance(fingerprint, str) or not FINGERPRINT_RE.match(fingerprint):
        _fail_record()
    redaction = value["redaction"]
    expected_redaction = (
        REDACTION_KEYS_V2 if set(value) == FINDING_KEYS_V2 else REDACTION_KEYS
    )
    if not isinstance(redaction, dict) or set(redaction) != expected_redaction:
        _fail_record()
    if redaction["applied"] is not True:
        _fail_record()
    if not isinstance(redaction["version"], str) or not redaction["version"]:
        _fail_record()
    if not isinstance(redaction["summary_dropped"], bool):
        _fail_record()
    if set(value) == FINDING_KEYS_V2:
        _validate_finding_texts(value)
    _validate_location(value["location"])


def _validate_check(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != CHECK_EVIDENCE_KEYS:
        _fail_record()
    name = value["name"]
    if not isinstance(name, str) or not name:
        _fail_record()
    status = value["status"]
    if status is not None and not isinstance(status, str):
        _fail_record()
    exit_code = value["exit_code"]
    if exit_code is not None and not isinstance(exit_code, int):
        _fail_record()
    duration = value["duration_seconds"]
    if duration is not None and not isinstance(duration, int | float):
        _fail_record()
    metrics = value["metrics"]
    if metrics is not None and not isinstance(metrics, dict):
        _fail_record()


def _validate_gate_evidence(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != GATE_EVIDENCE_KEYS:
        _fail_record()
    if value["complete"] is not None and not isinstance(value["complete"], bool):
        _fail_record()
    for key in ("profile", "profile_version", "status", "machine_code"):
        item = value[key]
        if item is not None and not isinstance(item, str):
            _fail_record()
    for key in ("expected_checks", "executed_checks"):
        names = value[key]
        if names is None:
            continue
        if not isinstance(names, list) or not all(
            isinstance(item, str) and item for item in names
        ):
            _fail_record()
    checks = value["checks"]
    if checks is None:
        return
    if not isinstance(checks, list):
        _fail_record()
    for check in checks:
        _validate_check(check)


def _validate_snapshot_evidence(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != SNAPSHOT_EVIDENCE_KEYS:
        _fail_record()
    for key in ("base_sha", "snapshot_commit", "tree_hash", "diff_sha256"):
        item = value[key]
        if item is not None and (not isinstance(item, str) or not item):
            _fail_record()
    method = value["snapshot_method"]
    if method is not None and not isinstance(method, str):
        _fail_record()
    for key in ("provenance_complete", "tree_unchanged"):
        item = value[key]
        if item is not None and not isinstance(item, bool):
            _fail_record()


def _validate_iteration_history(value: Any) -> None:
    """Проверить историю итераций repair-цикла (#204)."""
    if not isinstance(value, list) or not value:
        _fail_record()
    numbers: list[int] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != ITERATION_KEYS:
            _fail_record()
        iteration = item["iteration"]
        if not _non_negative(iteration):
            _fail_record()
        numbers.append(iteration)
        verdict = item["verdict"]
        if verdict is not None and verdict not in ALLOWED_VERDICTS:
            _fail_record()
        findings = item["blocking_findings"]
        if findings is None:
            continue
        if not isinstance(findings, list):
            _fail_record()
        for finding in findings:
            _validate_finding(finding)
    if numbers != sorted(numbers):
        _fail_record()


def _validate_verdict_evidence(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) not in (
        VERDICT_EVIDENCE_KEYS,
        VERDICT_EVIDENCE_KEYS_V2,
    ):
        _fail_record()
    verdict = value["verdict"]
    if verdict is not None and verdict not in ALLOWED_VERDICTS:
        _fail_record()
    basis = value["review_basis"]
    if basis is not None and not isinstance(basis, dict):
        _fail_record()
    reason = value["escalation_reason"]
    if reason is not None and (
        not isinstance(reason, str) or len(reason) > EVIDENCE_TEXT_MAX_CHARS
    ):
        _fail_record()
    if "iteration_history" in value:
        _validate_iteration_history(value["iteration_history"])
    findings = value["blocking_findings"]
    if findings is None:
        return
    if not isinstance(findings, list):
        _fail_record()
    for finding in findings:
        _validate_finding(finding)


def _validate_durations(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != DURATION_KEYS:
        _fail_record()
    for item in value.values():
        if item is None:
            continue
        if not isinstance(item, int | float) or isinstance(item, bool) or item < 0:
            _fail_record()


def _validate_evidence(value: Any) -> None:
    """Проверить redacted evidence приватной записи без раскрытия значений."""
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != EVIDENCE_KEYS:
        _fail_record()
    _assert_safe_tree(value)
    _validate_gate_evidence(value["gate"])
    _validate_snapshot_evidence(value["snapshot"])
    _validate_verdict_evidence(value["verdict"])
    _validate_durations(value["durations"])


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
    if (
        set(value) != expected
        or value.get("schema_version") != PROMOTION_SCHEMA_VERSION
    ):
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
