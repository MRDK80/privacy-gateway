"""Bounded owner-only append records; stored records alone convey no authority."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tools import agent_learning as contract
from tools.agent_continuation import digest
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_runtime_cli import DeploymentError, secure_directory

KEY_FIELDS = {
    "observation": "record_id",
    "planning_observation": "record_digest",
    "planning_candidate": "candidate_id",
    "planning_verification": "lesson_id",
    "candidate": "candidate_id",
    "verification": "lesson_id",
    "invocation": "invocation_key",
    "measurement": "measurement_id",
    "evaluation": "evaluation_id",
    "tombstone": "record_digest",
    "gate": "invocation_digest",
}


class Store:
    """Caller holds canonical task lock; this adds narrow learning ownership."""

    def __init__(
        self,
        directory: Path,
        root: Path,
        task: Path,
        owner: dict[str, Any],
        *,
        validators: Mapping[str, Callable[[Any], dict[str, Any]]],
    ) -> None:
        if os.name != "posix":
            raise contract.LearningError("LEARNING_PLATFORM_UNSUPPORTED")
        self.root, self.task = root, task
        self.owner = contract.profile(owner)
        self.validators = dict(validators)
        if not set(self.validators).issubset(KEY_FIELDS):
            raise contract.LearningError("LEARNING_STATE_INVALID")
        try:
            self._parents(directory)
            if not directory.exists():
                directory.mkdir(mode=0o700)
            self.directory = secure_directory(directory, root)
        except (DeploymentError, OSError):
            raise contract.LearningError("LEARNING_STORAGE_UNSAFE") from None
        self.path = self.directory / "records.jsonl"
        self.block_path = self.directory / "learning-blocked.json"
        self.lock = RunnerLock(self.directory)

    def _parents(self, directory: Path) -> None:
        for path in (self.task, directory):
            if any(parent.is_symlink() for parent in (path, *path.parents)):
                raise contract.LearningError("LEARNING_STORAGE_UNSAFE")
        secure_directory(self.task, self.root)
        secure_directory(directory.parent, self.root)

    def _safe(self) -> None:
        try:
            self._parents(self.directory)
            secure_directory(self.directory, self.root)
        except (DeploymentError, OSError):
            raise contract.LearningError("LEARNING_STORAGE_UNSAFE") from None

    def __enter__(self) -> Store:
        self._safe()
        RunnerLock.current_owned(self.task)
        self.lock.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        self.lock.__exit__(*args)

    def _owned(self) -> None:
        self._safe()
        RunnerLock.current_owned(self.task)
        RunnerLock.current_owned(self.directory)

    def _read(self, path: Path, cap: int) -> bytes | None:
        self._owned()
        if not path.exists() and not path.is_symlink():
            return None
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW"))
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != self.directory.stat().st_uid
                    or info.st_mode & 0o077
                    or info.st_size > cap
                ):
                    raise contract.LearningError("LEARNING_STORAGE_UNSAFE")
                data = stream.read(cap + 1)
                if len(data) > cap:
                    raise contract.LearningError("LEARNING_STORAGE_UNSAFE")
                return data
        except OSError:
            raise contract.LearningError("LEARNING_STORAGE_UNSAFE") from None

    @staticmethod
    def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = dict(pairs)
        if len(value) != len(pairs):
            raise contract.LearningError("LEARNING_STATE_INVALID")
        return value

    def _decode(self, raw: bytes) -> dict[str, Any]:
        try:
            value = json.loads(raw, object_pairs_hook=self._object)
            if not isinstance(value, dict):
                raise contract.LearningError("LEARNING_STATE_INVALID")
            return value
        except (ValueError, UnicodeError):
            raise contract.LearningError("LEARNING_STATE_INVALID") from None

    def records(self) -> list[dict[str, Any]]:
        raw = self._read(self.path, self.owner["limits"]["max_store_bytes"])
        if raw is None:
            return []
        lines = raw.splitlines()
        if not raw.endswith(b"\n") or len(lines) > self.owner["limits"]["max_records"]:
            raise contract.LearningError("LEARNING_STATE_INVALID")
        result: list[dict[str, Any]] = []
        identities: set[tuple[str, str]] = set()
        for line in lines:
            if len(line) > self.owner["limits"]["max_record_bytes"]:
                raise contract.LearningError("LEARNING_STATE_INVALID")
            item = contract.exact(self._decode(line), {"kind", "record"})
            kind = item["kind"]
            if not isinstance(kind, str) or kind not in self.validators:
                raise contract.LearningError("LEARNING_STATE_INVALID")
            record = self.validators[kind](item["record"])
            key = record.get(KEY_FIELDS[kind])
            contract.hash_value(key)
            identity = (kind, str(key))
            if identity in identities:
                raise contract.LearningError("LEARNING_STATE_CONFLICT")
            identities.add(identity)
            result.append({"kind": kind, "record": record})
        return result

    def append(self, kind: str, record: dict[str, Any], key_field: str) -> None:
        self._owned()
        if kind not in self.validators or KEY_FIELDS.get(kind) != key_field:
            raise contract.LearningError("LEARNING_STATE_INVALID")
        record = self.validators[kind](record)
        contract.hash_value(record.get(key_field))
        existing = self.records()
        item = {"kind": kind, "record": record}
        for previous in existing:
            if (
                previous["kind"] == kind
                and previous["record"][key_field] == record[key_field]
            ):
                if previous != item:
                    raise contract.LearningError("LEARNING_STATE_CONFLICT")
                return
        data = (
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode()
        current = self._read(self.path, self.owner["limits"]["max_store_bytes"])
        limits = self.owner["limits"]
        if (
            len(existing) >= limits["max_records"]
            or len(data) > limits["max_record_bytes"]
            or len(current or b"") + len(data) > limits["max_store_bytes"]
        ):
            raise contract.LearningError("LEARNING_STORAGE_EXHAUSTED")
        try:
            fd = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW"),
                0o600,
            )
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != self.directory.stat().st_uid
                    or info.st_mode & 0o077
                ):
                    raise contract.LearningError("LEARNING_STORAGE_UNSAFE")
                if os.write(fd, data) != len(data):
                    raise contract.LearningError("LEARNING_WRITE_FAILED")
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            raise contract.LearningError("LEARNING_WRITE_FAILED") from None

    def block(
        self, evaluation: str, lessons: list[str], reason: str, *, maximum: int = 3
    ) -> None:
        self._owned()
        # Four original roles, each at most three calls and three hints. The
        # caller verifies the actual original approved budgets before widening
        # this run-union bound; the independent byte cap remains unchanged.
        contract.integer(maximum, 36, 1)
        contract.hash_value(evaluation)
        if (
            not isinstance(lessons, list)
            or not 1 <= len(lessons) <= maximum
            or lessons != sorted(set(lessons))
            or reason != "LEARNING_REGRESSION"
        ):
            raise contract.LearningError("LEARNING_STATE_INVALID")
        for lesson in lessons:
            contract.hash_value(lesson)
        value = contract.seal(
            {
                "schema_version": "1.0",
                "evaluation_digest": evaluation,
                "lesson_ids": lessons,
                "reason_code": reason,
            }
        )
        previous = self._read(self.block_path, 4096)
        if previous is not None:
            if self._decode(previous) != value:
                raise contract.LearningError("LEARNING_STATE_CONFLICT")
            return
        data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        if len(data) > 4096:
            raise contract.LearningError("LEARNING_STATE_INVALID")
        try:
            fd = os.open(
                self.block_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW"),
                0o600,
            )
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != self.directory.stat().st_uid
                    or info.st_mode & 0o077
                ):
                    raise contract.LearningError("LEARNING_STORAGE_UNSAFE")
                # Creation establishes the negative barrier first. A failed or
                # short write leaves it present, so future selection blocks
                # even when the closed latch record cannot yet be decoded.
                if os.write(fd, data) != len(data):
                    raise contract.LearningError("LEARNING_WRITE_FAILED")
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            raise contract.LearningError("LEARNING_WRITE_FAILED") from None

    def _planning_source_matches(self, record: dict[str, Any]) -> bool:
        """Only exact original managed Admission and private metadata qualify."""
        if record["role"] not in {"planner", "planning_review"}:
            return False
        from tools import agent_epic_planning as admission
        from tools.agent_epic_runtime_cli import managed_state
        from tools.agent_learning_evidence import SourceReader

        try:
            reader = SourceReader(managed_state(self.task), self.root)
            if reader.owner != self.owner:
                return False
            task = record["identity"]["task"]
            directory = admission.Admission(reader.seed, reader.root)._storage(task)
            original = reader._json(directory / "admission.json")
            admission.exact(original, admission.RECORD_KEYS)
            identity = {
                key: original[key]
                for key in (
                    "schema_version",
                    "repository",
                    "epic",
                    "task",
                    "planning_digest",
                    "mandate_digest",
                    "policy_sha",
                )
            }
            role = "planner" if record["role"] == "planner" else "review"
            if (
                record["identity"]["original_run_id"] != digest(identity)
                or record["identity"]["owner_profile_digest"] != digest(self.owner)
                or original["mandate_digest"] != self.owner["mandate_digest"]
                or original["policy_sha"] != self.owner["policy_sha"]
                or original["pending"] is not None
                or not 1 <= record["ordinal"] <= original["attempts"][role]
                or record["status"] != "COMPLETED"
            ):
                return False
            source = reader._json(
                directory
                / "learning-invocations"
                / (record["invocation_key"] + ".json")
            )
            return source == record
        except (DeploymentError, contract.LearningError, OSError, KeyError):
            return False

    def ensure_usable(self) -> None:
        self._owned()
        if self._read(self.block_path, 4096) is not None:
            raise contract.LearningError("LEARNING_ROLLBACK_PENDING")
        records = self.records()
        for item in records:
            if (
                item["kind"] == "evaluation"
                and item["record"]["comparison"]["status"] == "ROLLBACK_REQUIRED"
            ):
                evaluation = digest(item["record"])
                tombstones = [
                    r
                    for r in records
                    if r["kind"] == "tombstone"
                    and r["record"]["evaluation_digest"] == evaluation
                ]
                hinted = [
                    r["record"]
                    for r in records
                    if r["kind"] == "measurement"
                    and digest(r["record"]) == item["record"]["hinted_digest"]
                ]
                if len(hinted) != 1:
                    raise contract.LearningError("LEARNING_ROLLBACK_PENDING")
                origins = [
                    r["record"]["identity"]
                    for r in records
                    if r["kind"] == "observation"
                    and r["record"]["source"]["workflow_digest"]
                    == hinted[0]["source_run_digest"]
                ]
                if not origins:
                    raise contract.LearningError("LEARNING_ROLLBACK_PENDING")
                lessons = {
                    lesson
                    for r in records
                    if r["kind"] == "invocation"
                    and any(
                        all(
                            r["record"]["identity"][key] == origin[key]
                            for key in ("repository", "epic", "task", "policy_sha")
                        )
                        and (
                            r["record"]["identity"]["original_run_id"]
                            == origin["run_id"]
                            or self._planning_source_matches(r["record"])
                        )
                        for origin in origins
                    )
                    for lesson in r["record"]["lesson_ids"]
                }
                removed = {r["record"]["lesson_id"] for r in tombstones}
                if not lessons or not lessons.issubset(removed):
                    raise contract.LearningError("LEARNING_ROLLBACK_PENDING")

    def clear_block(self) -> None:
        raw = self._read(self.block_path, 4096)
        if raw is None:
            return
        value = contract.sealed(
            self._decode(raw), {"evaluation_digest", "lesson_ids", "reason_code"}
        )
        contract.hash_value(value["evaluation_digest"])
        lessons = value["lesson_ids"]
        if (
            not isinstance(lessons, list)
            or not 1 <= len(lessons) <= 36
            or lessons != sorted(set(lessons))
            or value["reason_code"] != "LEARNING_REGRESSION"
        ):
            raise contract.LearningError("LEARNING_STATE_INVALID")
        for lesson in lessons:
            contract.hash_value(lesson)
        records = self.records()
        if not all(
            any(
                r["kind"] == "tombstone"
                and r["record"]["lesson_id"] == lesson
                and r["record"]["evaluation_digest"] == value["evaluation_digest"]
                for r in records
            )
            for lesson in value["lesson_ids"]
        ):
            raise contract.LearningError("LEARNING_ROLLBACK_PENDING")
        try:
            self.block_path.unlink()
        except OSError:
            raise contract.LearningError("LEARNING_ROLLBACK_PENDING") from None
