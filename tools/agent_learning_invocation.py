"""Private hint selection reservations follow canonical consumed role counters."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from tools import agent_learning as c
from tools import agent_learning_records as records
from tools import agent_orchestrate as workflow
from tools import codex_adapter
from tools.agent_continuation import digest
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_runtime_cli import learning_private_json, secure_directory
from tools.agent_epic_session_runtime import SessionRuntime
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_store import Store
from tools.agent_learning_verify import Verifier


class Runtime:
    def __init__(self, reader: SourceReader, session: SessionRuntime) -> None:
        self.reader, self.verifier = reader, Verifier(reader, session)
        self._workflow_binding: Any = None

    def writer_context(self, context: Any, neutral: dict[str, Any]) -> None:
        if self.reader.config["schema_version"] != "8.0":
            return
        from tools.agent_learning_workflow_binding import Binding

        if self._workflow_binding is None:
            self._workflow_binding = Binding(self)
        self._workflow_binding.context_from_orchestrator(context, neutral)

    def writer_begin(self) -> None:
        if self.reader.config["schema_version"] == "8.0":
            if self._workflow_binding is None:
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            self._workflow_binding.begin()

    def writer_invalidate(self) -> None:
        if self._workflow_binding is not None:
            self._workflow_binding.invalidate()

    def augment(
        self,
        role: str,
        payload: dict[str, Any],
        *,
        current_gate: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self._augment_original(role, payload, current_gate=current_gate)
        except BaseException:
            self.writer_invalidate()
            raise

    def _augment_original(
        self,
        role: str,
        payload: dict[str, Any],
        *,
        current_gate: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        contract = payload.get("contract")
        if not isinstance(contract, dict) or "untrusted_verified_hints" in payload:
            raise c.LearningError("LEARNING_INVOCATION_INVALID")
        from tools.agent_learning_workflow_companions import canonical, source

        task = c.integer(contract.get("issue"), 2147483647, 1)
        previous = canonical(self.reader, task)
        problem, origin = source(self.reader, task, role, previous, current_gate)
        references: list[dict[str, Any]] = []
        if self.reader.config["schema_version"] == "8.0":
            envelope, references = self.verifier.selection_proof(
                contract["task_class"], role, problem
            )
        else:
            envelope = self.verifier.selection(contract["task_class"], role, problem)
        augmented = {**payload, "untrusted_verified_hints": envelope}
        original = {
            key: value for key, value in augmented.items() if key != "contract_object"
        }
        self._workflow_prepared = (role, digest(original), previous, problem, origin)
        if self.reader.config["schema_version"] == "8.0":
            if self._workflow_binding is None:
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            self._workflow_binding.capture(role, augmented, previous, references)
        return augmented

    def _directory(self, task: int) -> Path:
        reader = self.reader
        reader.guard()
        parent = reader.task_directory(task)
        RunnerLock.current_owned(parent)
        directory = parent / "learning-invocations"
        if any(path.is_symlink() for path in (directory, *directory.parents)):
            raise c.LearningError("LEARNING_STORAGE_UNSAFE")
        secure_directory(parent, reader.root)
        if not directory.exists():
            directory.mkdir(mode=0o700)
        return secure_directory(directory, reader.root)

    def _save(self, directory: Path, value: dict[str, Any]) -> None:
        value = records.invocation(value)
        limits = self.reader.owner["limits"]
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        if len(encoded) > limits["max_record_bytes"]:
            raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
        paths: list[Path] = []
        size = 0
        for path in directory.iterdir():
            if path.name == "epic-loop.lock":
                continue
            if len(paths) >= limits["max_records"] or path.suffix != ".json":
                raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
            learning_private_json(path, limits["max_record_bytes"])
            paths.append(path)
            size += path.lstat().st_size
        target = directory / (value["invocation_key"] + ".json")
        if not target.exists() and len(paths) >= limits["max_records"]:
            raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
        previous = target.lstat().st_size if target.exists() else 0
        if size - previous + len(encoded) > limits["max_store_bytes"]:
            raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(dir=directory, prefix=".invocation-")
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            raise c.LearningError("LEARNING_WRITE_FAILED") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def reserve(self, role: str, payload: dict[str, Any]) -> None:
        try:
            self._reserve_original(role, payload)
        except BaseException:
            self.writer_invalidate()
            raise

    def _reserve_original(self, role: str, payload: dict[str, Any]) -> None:
        reader = self.reader
        safe = {
            key: value for key, value in payload.items() if key != "contract_object"
        }
        task = safe["contract"]["issue"]
        directory = self._directory(task)
        contract = reader.contract(task)
        canonical = reader._json(
            reader.task_directory(task)
            / "bounded-workflow"
            / (workflow._run_key(contract) + ".json")
        )
        if (
            canonical.get("terminal") is not False
            or canonical.get("role_pending") != role
            or canonical.get("invocation_digest") != digest(safe)
        ):
            raise c.LearningError("LEARNING_INVOCATION_SOURCE_INVALID")
        ordinal = canonical[role + "_calls"]
        envelope = safe.get("untrusted_verified_hints")
        if not isinstance(envelope, dict) or envelope.get("role") != role:
            raise c.LearningError("LEARNING_INVOCATION_INVALID")
        original = {
            key: value
            for key, value in safe.items()
            if key != "untrusted_verified_hints"
        }
        value = {
            "schema_version": "1.0",
            "identity": {
                "repository": reader.owner["repository"],
                "epic": reader.owner["epic"],
                "task": task,
                "policy_sha": reader.owner["policy_sha"],
                "owner_profile_digest": digest(reader.owner),
                "original_run_id": canonical["run_id"],
            },
            "role": role,
            "ordinal": ordinal,
            "repair_iteration": canonical["repair_iterations"],
            "catalog_digest": reader.owner["catalog_digest"],
            "selection_digest": envelope["selection_digest"],
            "lesson_ids": sorted(hint["lesson_id"] for hint in envelope["hints"]),
            "input_digest": digest(original),
            "prompt_digest": hashlib.sha256(
                codex_adapter._prompt(role, safe).encode()
            ).hexdigest(),
            "status": "RESERVED",
        }
        value["invocation_key"] = records.invocation_id(value)
        value = records.invocation(c.seal(value))
        with RunnerLock(directory):
            path = directory / (value["invocation_key"] + ".json")
            if path.exists() or path.is_symlink():
                raise c.LearningError("LEARNING_INVOCATION_UNKNOWN")
            self._save(directory, value)
            from tools.agent_learning_workflow_companions import retain

            retain(reader, value, safe, self._workflow_prepared)
        if self.reader.config["schema_version"] == "8.0":
            if self._workflow_binding is None:
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            self._workflow_binding.reserve(role, safe)

    def complete(self, role: str, payload: dict[str, Any]) -> None:
        try:
            self._complete_original(role, payload)
        except BaseException:
            self.writer_invalidate()
            raise

    def _complete_original(self, role: str, payload: dict[str, Any]) -> None:
        reader = self.reader
        safe = {
            key: value for key, value in payload.items() if key != "contract_object"
        }
        task = safe["contract"]["issue"]
        directory = self._directory(task)
        canonical = reader._json(
            reader.task_directory(task)
            / "bounded-workflow"
            / (workflow._run_key(reader.contract(task)) + ".json")
        )
        identity = {
            "repository": reader.owner["repository"],
            "epic": reader.owner["epic"],
            "task": task,
            "policy_sha": reader.owner["policy_sha"],
            "owner_profile_digest": digest(reader.owner),
            "original_run_id": canonical["run_id"],
        }
        key = records.invocation_id(
            {
                "identity": identity,
                "role": role,
                "ordinal": canonical[role + "_calls"],
                "repair_iteration": canonical["repair_iterations"],
            }
        )
        with RunnerLock(directory):
            value = records.invocation(reader._json(directory / (key + ".json")))
            invocation = digest(safe)
            source = reader._json(
                reader.task_directory(task)
                / "continuation"
                / ("reservation-" + invocation + ".json")
            )
            settings = reader._json(
                reader.seed / "role-settings" / ("receipt-" + invocation + ".json")
            )
            if (
                source.get("status") != "COMPLETED"
                or source.get("outcome_digest") != settings["receipt"]["receipt_digest"]
                or value["prompt_digest"]
                != hashlib.sha256(
                    codex_adapter._prompt(role, safe).encode()
                ).hexdigest()
            ):
                raise c.LearningError("LEARNING_INVOCATION_SOURCE_INVALID")
            value = records.invocation(c.seal({**value, "status": "COMPLETED"}))
            self._save(directory, value)
        task_directory = reader.task_directory(task)
        with Store(
            task_directory / "learning",
            reader.root,
            task_directory,
            reader.owner,
            validators=records.VALIDATORS,
        ) as store:
            store.append("invocation", value, "invocation_key")
        if self.reader.config["schema_version"] == "8.0":
            if self._workflow_binding is None:
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            self._workflow_binding.complete(role, safe)
