"""Trusted gate callback retains redacted per-check facts before attempt rollover."""

from __future__ import annotations

import sys
from dataclasses import asdict

from tools import agent_gate
from tools import agent_learning as c
from tools import agent_learning_records as records
from tools.agent_continuation import digest
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_runtime_cli import learning_private_json, secure_directory
from tools.agent_learning_evidence import SourceReader
from tools.agent_orchestrate import StateStore


class GateJournal:
    """Only pinned workflow code supplies the original consumed attempt identity."""

    def __init__(
        self,
        reader: SourceReader,
        task: int,
        run_id: str,
        executor_ordinal: int,
        repair_iteration: int,
        snapshot: agent_gate.SnapshotSession,
    ) -> None:
        reader.guard()
        records.nonce(run_id)
        c.integer(executor_ordinal, 3, 1)
        c.integer(repair_iteration, 2)
        if executor_ordinal != repair_iteration + 1:
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        self.reader, self.task, self.run_id = reader, task, run_id
        self.ordinal, self.repair = executor_ordinal, repair_iteration
        self.snapshot = snapshot
        self.directory = reader.task_directory(task)
        RunnerLock.current_owned(self.directory)
        self.target = self.directory / "learning-source/gate"
        if self.target.is_symlink() or self.target.parent.is_symlink():
            raise c.LearningError("LEARNING_STORAGE_UNSAFE")
        self.target.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.target.parent.chmod(0o700)
        secure_directory(self.target, reader.root)
        # Effective role-backend evidence is independent of account usage metrics.
        backend = reader._json(reader.seed / "continuation-profile.json")[
            "backend_digest"
        ]
        self.environment = c.environment(
            {
                "os": "linux" if sys.platform == "linux" else "windows",
                "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
                "gate_profile": agent_gate.FULL_PROFILE,
                "backend_digest": backend,
                "tool_digests": [],
            }
        )

    def __call__(self, result: agent_gate.CheckResult) -> None:
        self.reader.guard()
        RunnerLock.current_owned(self.directory)
        if (
            result.id not in agent_gate.CHECK_COMMANDS
            or result.argv != agent_gate.CHECK_COMMANDS[result.id]
        ):
            raise c.LearningError("LEARNING_GATE_SOURCE_INVALID")
        if result.exit_code is None or result.status not in {"passed", "failed"}:
            # A timeout/unavailable/parse-error is not a fabricated completed
            # regression check. The original gate still reports its failure.
            return
        c.integer(result.exit_code, 255)
        outcome = "PASS" if result.status == "passed" else "FAIL"
        source = asdict(result)
        invocation = digest(
            {
                "run_id": self.run_id,
                "executor_ordinal": self.ordinal,
                "repair_iteration": self.repair,
                "snapshot": self.snapshot.identity(),
                "check_id": result.id,
            }
        )
        value = c.seal(
            {
                "schema_version": "1.0",
                "invocation_digest": invocation,
                "run_id": self.run_id,
                "policy_sha": self.reader.owner["policy_sha"],
                "snapshot_digest": digest(self.snapshot.identity()),
                "environment_digest": digest(self.environment),
                "checks": [
                    {
                        "gate_profile": agent_gate.FULL_PROFILE,
                        "check_name": result.id,
                        "argv_digest": digest(list(result.argv)),
                        "test_identity": None,
                        "outcome": outcome,
                        "exit_code": result.exit_code,
                        "evidence_digest": digest(source),
                    }
                ],
            }
        )
        records.gate(value)
        path = self.target / (invocation + ".json")
        limits = self.reader.owner["limits"]
        if path.exists() or path.is_symlink():
            if learning_private_json(path, limits["max_record_bytes"]) != value:
                raise c.LearningError("LEARNING_STATE_CONFLICT")
            return
        paths = list(self.target.glob("*.json"))
        if len(paths) >= limits["max_records"]:
            raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
        import json

        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        if (
            len(encoded) > limits["max_record_bytes"]
            or sum(p.lstat().st_size for p in paths) + len(encoded)
            > limits["max_store_bytes"]
        ):
            raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
        StateStore(self.target).save(invocation, value)
