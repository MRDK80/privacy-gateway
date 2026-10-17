"""Private original planning artifacts retained under the canonical seed lock."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from tools import agent_epic_handover as handover
from tools import agent_epic_planning as admission
from tools import agent_learning as c
from tools import agent_learning_planning_predicates as predicates
from tools import agent_learning_planning_records as records
from tools.agent_continuation import digest
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_runtime_cli import canonical_digest, secure_directory
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_planning_sources import capture_workspace


class Journal:
    def __init__(
        self,
        reader: SourceReader,
        role: str,
        request: dict[str, Any],
        context: handover.MandateContext,
    ) -> None:
        reader.guard()
        RunnerLock.current_owned(reader.seed)
        if role not in {"planner", "review"}:
            raise c.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
        identity = request.get("identity")
        if not isinstance(identity, dict) or set(identity) != {
            "schema_version",
            "repository",
            "epic",
            "task",
            "planning_digest",
            "mandate_digest",
            "policy_sha",
        }:
            raise c.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
        owner = admission.profile(
            reader._json(reader.seed / "planning-profile.json"),
            approved=reader.config["approved_planning_digest"],
            mandate=reader.mandate,
        )
        if identity["planning_digest"] != canonical_digest(owner):
            raise c.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
        task = c.integer(identity.get("task"), 2147483647, 1)
        if task not in reader.config["approved_order"]:
            raise c.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
        source = admission.Admission(reader.seed, reader.root)._storage(task)
        saved = reader._json(source / "admission.json")
        admission.exact(saved, admission.RECORD_KEYS)
        ordinal = c.integer(saved["attempts"][role], 3, 1)
        expected = {key: saved[key] for key in identity}
        if identity != expected or saved["pending"] != {
            "role": role,
            "attempt": ordinal,
            "request_digest": canonical_digest(request),
        }:
            raise c.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
        self.reader, self.request = reader, copy.deepcopy(request)
        self.identity = records.identity(
            {
                key: identity[key]
                for key in (
                    "repository",
                    "epic",
                    "task",
                    "planning_digest",
                    "mandate_digest",
                    "policy_sha",
                )
            }
        )
        if (
            self.identity["policy_sha"] != reader.owner["policy_sha"]
            or self.identity["mandate_digest"] != reader.owner["mandate_digest"]
            or self.identity["repository"] != reader.owner["repository"]
            or self.identity["epic"] != reader.owner["epic"]
            or context.owner_identity != reader.owner["owner_identity"]
        ):
            raise c.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
        self.role = "planner" if role == "planner" else "planning_review"
        self.ordinal, self.source = ordinal, source
        self.directory = source / "learning-validation"
        if any(path.is_symlink() for path in (self.directory, *self.directory.parents)):
            raise c.LearningError("LEARNING_STORAGE_UNSAFE")
        self.directory.mkdir(mode=0o700, exist_ok=True)
        secure_directory(self.directory, reader.root)
        self.prefix = f"{self.role}-{ordinal}"
        self.context = records.context(
            c.seal(
                {
                    "schema_version": "1.0",
                    "identity": self.identity,
                    "role": self.role,
                    "ordinal": ordinal,
                    **asdict(context),
                    "request_digest": digest(request),
                }
            )
        )

        self.workspace = capture_workspace(reader, self.identity, self.role, ordinal)
        self.facts = predicates.facts(
            self.request,
            head_sha=self.workspace["head_sha"],
            tree_hash=self.workspace["tree_hash"],
        )

    def _save(self, suffix: str, value: dict[str, Any]) -> None:
        reader = self.reader
        reader.guard()
        RunnerLock.current_owned(reader.seed)
        secure_directory(self.directory, reader.root)
        limits = reader.owner["limits"]
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        if len(raw) > limits["max_record_bytes"]:
            raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
        target = self.directory / (self.prefix + "-" + suffix + ".json")
        count, size = 0, 0
        for path in self.directory.iterdir():
            if count >= limits["max_records"]:
                raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
            if path.is_symlink() or path.suffix != ".json":
                raise c.LearningError("LEARNING_STORAGE_UNSAFE")
            reader._json(path)
            count += 1
            size += path.lstat().st_size
        if target.exists() or target.is_symlink():
            if reader._json(target) != value:
                raise c.LearningError("LEARNING_STATE_CONFLICT")
            return
        if (
            count >= limits["max_records"]
            or size + len(raw) > limits["max_store_bytes"]
        ):
            raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(target, flags, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # A partial immutable artifact is not a completed validation source.
            raise c.LearningError("LEARNING_WRITE_FAILED") from None

    def _artifact(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return records.artifact(
            c.seal(
                {
                    "schema_version": "1.0",
                    "identity": self.identity,
                    "role": self.role,
                    "ordinal": self.ordinal,
                    "kind": kind,
                    "payload": payload,
                    "payload_digest": digest(payload),
                }
            )
        )

    def start(self) -> None:
        self._save("request", self._artifact("REQUEST", self.request))
        self._save("context", self.context)
        self._save("workspace", self.workspace)

    def finish(
        self,
        response: dict[str, Any],
        *,
        code: str,
        applicability: str,
        check_identity: str,
    ) -> dict[str, Any]:
        validator = "PROPOSAL_V1" if self.role == "planner" else "REVIEW_V1"
        value = records.validation(
            c.seal(
                {
                    "schema_version": "1.0",
                    "identity": self.identity,
                    "role": self.role,
                    "ordinal": self.ordinal,
                    "request_digest": digest(self.request),
                    "response_digest": digest(response),
                    "applicability_digest": applicability,
                    "validator": validator,
                    "outcome": "PASS" if code == "OK" else "FAIL",
                    "machine_code": code,
                    "check_identity": check_identity,
                }
            )
        )
        self._save("response", self._artifact("RESPONSE", response))
        self._save("validation", value)
        return value

    def validate(
        self, response: dict[str, Any], validate: Callable[[], dict[str, Any]]
    ) -> dict[str, Any]:
        from tools.agent_epic_loop import PhaseBlocked

        current = capture_workspace(self.reader, self.identity, self.role, self.ordinal)
        if current != self.workspace:
            raise c.LearningError("LEARNING_SOURCE_CHANGED")
        try:
            result = validate()
        except PhaseBlocked as error:
            validator = "PROPOSAL_V1" if self.role == "planner" else "REVIEW_V1"
            if error.machine_code in records.FAILURE_CODES[validator]:
                self.finish(
                    response,
                    code=error.machine_code,
                    applicability=digest(self.facts),
                    check_identity=predicates.check_identity(self.role, self.facts),
                )
            raise
        self.finish(
            response,
            code="OK",
            applicability=digest(self.facts),
            check_identity=predicates.check_identity(self.role, self.facts),
        )
        return result
