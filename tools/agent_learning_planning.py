"""Learning metadata for original bounded planner and planning-review calls."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools import agent_epic_planning as admission
from tools import agent_learning as c
from tools import agent_learning_records as records
from tools import agent_orchestrate as workflow
from tools import codex_adapter
from tools.agent_continuation import digest
from tools.agent_epic_handover import MandateContext
from tools.agent_epic_loop import PhaseBlocked, RunnerLock
from tools.agent_epic_planning_roles import INSTRUCTIONS
from tools.agent_epic_runtime_cli import canonical_digest, secure_directory
from tools.agent_learning_invocation import Runtime
from tools.agent_learning_planning_journal import Journal


def prompt(root: Path, policy_sha: str, request: dict[str, Any]) -> str:
    trusted = {
        path: workflow._git(root, "show", f"{policy_sha}:{path}")
        for path in workflow.POLICY_FILES
    }
    return (
        INSTRUCTIONS
        + codex_adapter._block(
            "trusted-policy", json.dumps(trusted, ensure_ascii=False, sort_keys=True)
        )
        + codex_adapter._block(
            "untrusted-planning-data",
            json.dumps(request, ensure_ascii=False, sort_keys=True),
        )
    )


class Planning(Runtime):
    def current_context(
        self, context: MandateContext, guard: Callable[[], None]
    ) -> None:
        """Called by original Admission with its actual context and fresh guard."""
        guard()
        self._current_context, self._current_guard = context, guard

    def prepare(self, role: str, request: dict[str, Any]) -> dict[str, Any]:
        logical = "planner" if role == "planner" else "planning_review"
        original = {
            key: value
            for key, value in request.items()
            if key != "untrusted_verified_hints"
        }
        from tools.agent_learning_planning_barrier import planning_barrier
        from tools.agent_learning_planning_current import current_facts
        from tools.agent_learning_planning_selection import select

        self._current_guard()
        current = current_facts(
            self.reader,
            logical,
            original,
            self._current_context,
            guard=self._current_guard,
        )
        planning_barrier(self.verifier, original["identity"]["task"])
        envelope, proofs = select(self.verifier, logical, current)
        result = {**original, "untrusted_verified_hints": envelope}
        full = prompt(self.reader.root, self.reader.owner["policy_sha"], result)
        if len(full) > request["limits"]["max_input_chars"]:
            raise c.LearningError("LEARNING_PLANNING_CAPACITY_EXHAUSTED")
        self._prepared_source = (digest(result), current, proofs)
        self._current_guard()
        return result

    def _planning_directory(self, task: int) -> tuple[Path, Path]:
        reader = self.reader
        reader.guard()
        RunnerLock.current_owned(reader.seed)
        source = admission.Admission(reader.seed, reader.root)._storage(task)
        directory = source / "learning-invocations"
        if any(path.is_symlink() for path in (directory, *directory.parents)):
            raise c.LearningError("LEARNING_STORAGE_UNSAFE")
        if not directory.exists():
            directory.mkdir(mode=0o700)
        return source, secure_directory(directory, reader.root)

    def _record(
        self, role: str, request: dict[str, Any], source: Path
    ) -> dict[str, Any]:
        reader = self.reader
        saved = reader._json(source / "admission.json")
        admission.exact(saved, admission.RECORD_KEYS)
        task = request["identity"]["task"]
        if any(saved[key] != value for key, value in request["identity"].items()):
            raise c.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
        ordinal = saved["attempts"][role]
        logical = "planner" if role == "planner" else "planning_review"
        identity = {
            "repository": reader.owner["repository"],
            "epic": reader.owner["epic"],
            "task": task,
            "policy_sha": reader.owner["policy_sha"],
            "owner_profile_digest": digest(reader.owner),
            "original_run_id": digest(request["identity"]),
        }
        envelope = request["untrusted_verified_hints"]
        value = {
            "schema_version": "1.0",
            "identity": identity,
            "role": logical,
            "ordinal": ordinal,
            "repair_iteration": 0,
            "catalog_digest": reader.owner["catalog_digest"],
            "selection_digest": envelope["selection_digest"],
            "lesson_ids": sorted(hint["lesson_id"] for hint in envelope["hints"]),
            "input_digest": digest(
                {
                    key: item
                    for key, item in request.items()
                    if key != "untrusted_verified_hints"
                }
            ),
            "prompt_digest": hashlib.sha256(
                prompt(reader.root, reader.owner["policy_sha"], request).encode()
            ).hexdigest(),
            "status": "RESERVED",
        }
        value["invocation_key"] = records.invocation_id(value)
        return records.invocation(c.seal(value))

    def reserve_planning(
        self, role: str, request: dict[str, Any], *, context: MandateContext
    ) -> None:
        source, directory = self._planning_directory(request["identity"]["task"])
        saved = self.reader._json(source / "admission.json")
        if saved["pending"] != {
            "role": role,
            "attempt": saved["attempts"][role],
            "request_digest": canonical_digest(request),
        }:
            raise c.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
        value = self._record(role, request, source)
        with RunnerLock(directory):
            path = directory / (value["invocation_key"] + ".json")
            if path.exists() or path.is_symlink():
                raise c.LearningError("LEARNING_INVOCATION_UNKNOWN")
            self._save(directory, value)
        journal = Journal(self.reader, role, request, context)
        journal.start()
        self._planning_journal = journal
        from tools.agent_learning_planning_companions import retain
        from tools.agent_learning_planning_selection import revalidate

        prepared_digest, current, proofs = self._prepared_source
        if prepared_digest != digest(request) or context != self._current_context:
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        self._current_guard()
        for proof in proofs:
            revalidate(self.verifier, proof)
        retain(self.reader, value, request, current, proofs)
        self._current_guard()

    def validate_planning(
        self,
        role: str,
        request: dict[str, Any],
        response: dict[str, Any],
        *,
        validate: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        journal = self._planning_journal
        if (
            journal.role != ("planner" if role == "planner" else "planning_review")
            or journal.request != request
        ):
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        from tools.agent_learning_planning_promotion import observe

        try:
            return journal.validate(response, validate)
        except PhaseBlocked as error:
            validator = "PROPOSAL_V1" if role == "planner" else "REVIEW_V1"
            from tools.agent_learning_planning_records import FAILURE_CODES

            if error.machine_code in FAILURE_CODES[validator]:
                observe(
                    self.reader,
                    request["identity"]["task"],
                    journal.role,
                    journal.ordinal,
                )
            raise

    def complete_planning(
        self, role: str, request: dict[str, Any], response: dict[str, Any]
    ) -> None:
        source, directory = self._planning_directory(request["identity"]["task"])
        saved = self.reader._json(source / "admission.json")
        output = saved["proposal" if role == "planner" else "review"]
        if saved["pending"] is not None or output != {
            "digest": canonical_digest(response),
            "response": response,
        }:
            raise c.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
        value = self._record(role, request, source)
        with RunnerLock(directory):
            original = records.invocation(
                self.reader._json(directory / (value["invocation_key"] + ".json"))
            )
            if original != value:
                raise c.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
            self._save(
                directory, records.invocation(c.seal({**value, "status": "COMPLETED"}))
            )
        from tools.agent_learning_planning_promotion import observe

        observe(
            self.reader,
            request["identity"]["task"],
            "planner" if role == "planner" else "planning_review",
            value["ordinal"],
        )
