#!/usr/bin/env python3
"""Store and select bounded, evidence-verified private prompt lessons."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = "1.0"
MAX_HINTS = 3
MAX_STEPS = 4
MAX_TEXT = 160
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9 .,():/_+-]{1,160}$")
SECRET_RE = re.compile(
    r"(?i)(?:gh[pousr]_[A-Za-z0-9]{20,}|api[_-]?key|password|secret|token\s*[=:])"
)
INJECTION_RE = re.compile(
    r"(?i)(?:ignore|override|bypass|disregard).{0,24}(?:policy|instruction|gate|approval)|"
    r"(?:merge|push|commit).{0,12}(?:now|without|anyway)|system\s*prompt"
)
RECORD_FIELDS = {
    "schema_version",
    "lesson_id",
    "epic",
    "task",
    "task_class",
    "problem_class",
    "environment",
    "symptom_code",
    "diagnostic_steps",
    "minimal_fix",
    "applicability",
    "head_sha",
    "merge_sha",
    "pr",
    "iterations",
    "duration_seconds",
    "verification",
}
VERIFICATION_FIELDS = {"gate", "controller", "pr_checks", "post_merge_checks"}


class LessonError(Exception):
    """Safe machine-coded failure without echoing private content."""

    def __init__(self, machine_code: str) -> None:
        super().__init__(machine_code)
        self.machine_code = machine_code


@dataclass(frozen=True)
class VerifiedLesson:
    schema_version: str
    lesson_id: str
    epic: int
    task: int
    task_class: str
    problem_class: str
    environment: tuple[str, ...]
    symptom_code: str
    diagnostic_steps: tuple[str, ...]
    minimal_fix: str
    applicability: str
    head_sha: str
    merge_sha: str
    pr: int
    iterations: int
    duration_seconds: int
    verification: dict[str, Any]


@dataclass(frozen=True)
class Hint:
    lesson_id: str
    trust: str
    problem_class: str
    symptom_code: str
    diagnostic_steps: tuple[str, ...]
    minimal_fix: str
    applicability: str
    permissions: None = None


@dataclass(frozen=True)
class Measurement:
    iterations: int
    duration_seconds: int
    calls: int


@dataclass(frozen=True)
class Evaluation:
    status: str
    iteration_delta: int
    duration_delta_seconds: int
    call_delta: int


def default_directory() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured) if configured else Path.home() / ".local" / "state"
    return base / "privacy-gateway" / "agent-memory" / "verified-lessons"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _safe_text(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_TEXT
        or SAFE_TEXT_RE.fullmatch(value) is None
        or SECRET_RE.search(value)
        or INJECTION_RE.search(value)
    ):
        raise LessonError("UNSAFE_HINT")
    return value


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise LessonError("INVALID_LESSON")
    return value


def _strings(value: Any, *, maximum: int) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > maximum
        or len(set(value)) != len(value)
    ):
        raise LessonError("INVALID_LESSON")
    return tuple(_safe_text(item) for item in value)


def validate_candidate(
    value: Mapping[str, Any], *, expected_head_sha: str | None = None
) -> VerifiedLesson:
    """Validate redaction, immutable provenance and all independent gates."""
    if set(value) != RECORD_FIELDS or value.get("schema_version") != SCHEMA_VERSION:
        raise LessonError("INVALID_LESSON")
    verification = value.get("verification")
    if (
        not isinstance(verification, Mapping)
        or set(verification) != VERIFICATION_FIELDS
    ):
        raise LessonError("EVIDENCE_INCOMPLETE")
    if (
        verification.get("gate") != "PASS"
        or verification.get("controller") not in {"PASS", "PASS_WITH_NOTES"}
        or verification.get("pr_checks") != ["SUCCESS"] * 5
        or verification.get("post_merge_checks") != ["SUCCESS"] * 5
    ):
        raise LessonError("EVIDENCE_INCOMPLETE")
    head_sha, merge_sha = value.get("head_sha"), value.get("merge_sha")
    if (
        not isinstance(head_sha, str)
        or SHA_RE.fullmatch(head_sha) is None
        or not isinstance(merge_sha, str)
        or SHA_RE.fullmatch(merge_sha) is None
    ):
        raise LessonError("INVALID_LESSON")
    if expected_head_sha is not None and head_sha != expected_head_sha:
        raise LessonError("STALE_EVIDENCE")
    integers = (value.get("epic"), value.get("task"), value.get("pr"))
    metrics = (value.get("iterations"), value.get("duration_seconds"))
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1
        for item in integers
    ):
        raise LessonError("INVALID_LESSON")
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item < 0
        for item in metrics
    ):
        raise LessonError("INVALID_LESSON")
    symptom = value.get("symptom_code")
    if not isinstance(symptom, str) or CODE_RE.fullmatch(symptom) is None:
        raise LessonError("INVALID_LESSON")
    environment = _strings(value.get("environment"), maximum=8)
    steps = _strings(value.get("diagnostic_steps"), maximum=MAX_STEPS)
    return VerifiedLesson(
        SCHEMA_VERSION,
        _identifier(value.get("lesson_id")),
        cast(int, value["epic"]),
        cast(int, value["task"]),
        _identifier(value.get("task_class")),
        _identifier(value.get("problem_class")),
        environment,
        symptom,
        steps,
        _safe_text(value.get("minimal_fix")),
        _safe_text(value.get("applicability")),
        head_sha,
        merge_sha,
        cast(int, value["pr"]),
        cast(int, value["iterations"]),
        cast(int, value["duration_seconds"]),
        dict(verification),
    )


def to_hint(lesson: VerifiedLesson) -> Hint:
    return Hint(
        lesson.lesson_id,
        "UNTRUSTED_VERIFIED_HINT",
        lesson.problem_class,
        lesson.symptom_code,
        lesson.diagnostic_steps,
        lesson.minimal_fix,
        lesson.applicability,
    )


class LessonStore:
    """Append-only private lessons plus explicit disable tombstones."""

    def __init__(self, directory: Path, repository_root: Path) -> None:
        if _inside(directory, repository_root):
            raise LessonError("UNSAFE_STORAGE")
        self.directory = directory
        self.path = directory / "lessons.jsonl"

    def _append(self, value: Mapping[str, Any]) -> None:
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.directory, 0o700)
            descriptor = os.open(
                self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600
            )
            try:
                line = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
                os.write(descriptor, line.encode())
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise LessonError("LESSON_WRITE_FAILED") from error

    def records(self) -> Iterable[dict[str, Any]]:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise LessonError("INVALID_LESSON")
                yield cast(dict[str, Any], value)
        except (OSError, ValueError) as error:
            raise LessonError("INVALID_LESSON") from error

    def verify(
        self, value: Mapping[str, Any], *, expected_head_sha: str | None = None
    ) -> None:
        lesson = validate_candidate(value, expected_head_sha=expected_head_sha)
        normalized = asdict(lesson)
        normalized["environment"] = list(lesson.environment)
        normalized["diagnostic_steps"] = list(lesson.diagnostic_steps)
        matches = [
            item
            for item in self.records()
            if item.get("lesson_id") == lesson.lesson_id
            and item.get("event", "verified") == "verified"
        ]
        if matches:
            if matches[0] != normalized:
                raise LessonError("LESSON_CONFLICT")
            return
        self._append(normalized)

    def disable(self, lesson_id: str, *, reason_code: str) -> None:
        _identifier(lesson_id)
        if CODE_RE.fullmatch(reason_code) is None:
            raise LessonError("INVALID_LESSON")
        if not any(item.get("lesson_id") == lesson_id for item in self.records()):
            raise LessonError("LESSON_NOT_FOUND")
        tombstone = {
            "event": "disabled",
            "lesson_id": lesson_id,
            "reason_code": reason_code,
        }
        if tombstone not in tuple(self.records()):
            self._append(tombstone)

    def select(
        self,
        *,
        task_class: str,
        problem_class: str,
        environment: Sequence[str],
        limit: int = MAX_HINTS,
    ) -> tuple[Hint, ...]:
        if not 0 <= limit <= MAX_HINTS:
            raise LessonError("INVALID_LIMIT")
        disabled = {
            item["lesson_id"]
            for item in self.records()
            if item.get("event") == "disabled"
        }
        selected: list[Hint] = []
        for item in self.records():
            if item.get("event") == "disabled" or item.get("lesson_id") in disabled:
                continue
            lesson = validate_candidate(item)
            if (
                lesson.task_class == task_class
                and lesson.problem_class == problem_class
                and lesson.environment == tuple(environment)
            ):
                selected.append(to_hint(lesson))
        return tuple(sorted(selected, key=lambda item: item.lesson_id)[:limit])


def evaluate(*, baseline: Measurement, hinted: Measurement) -> Evaluation:
    for measurement in (baseline, hinted):
        if (
            min(measurement.iterations, measurement.duration_seconds, measurement.calls)
            < 0
        ):
            raise LessonError("INVALID_MEASUREMENT")
    deltas = (
        hinted.iterations - baseline.iterations,
        hinted.duration_seconds - baseline.duration_seconds,
        hinted.calls - baseline.calls,
    )
    if all(value <= 0 for value in deltas) and any(value < 0 for value in deltas):
        status = "MEASURED_IMPROVEMENT"
    elif any(value > 0 for value in deltas):
        status = "ROLLBACK_RECOMMENDED"
    else:
        status = "NO_MEASURED_CHANGE"
    return Evaluation(status, *deltas)
