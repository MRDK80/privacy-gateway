"""Same-run original full-gate failure binding for workflow role hints."""

from __future__ import annotations

import hashlib
import subprocess
from typing import Any

from tools import agent_gate
from tools import agent_learning as core
from tools import agent_learning_records as records
from tools import agent_orchestrate as workflow
from tools.agent_continuation import digest
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_sources import environment
from tools.agent_learning_verify import current_problem_class

FIELDS = {
    "run_id",
    "policy_sha",
    "contract_digest",
    "executor_ordinal",
    "repair_iteration",
    "gate",
}


def bundle(raw: Any) -> dict[str, Any]:
    value = core.sealed(raw, FIELDS)
    records.nonce(value["run_id"])
    core.hash_value(value["policy_sha"], size=40)
    core.hash_value(value["contract_digest"])
    core.integer(value["executor_ordinal"], 3, 1)
    core.integer(value["repair_iteration"], 2)
    if value["executor_ordinal"] != value["repair_iteration"] + 1:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    gate = value["gate"]
    if not isinstance(gate, dict) or not agent_gate.is_repository_full(gate):
        raise core.LearningError("LEARNING_GATE_REQUIRED")
    return value


def verify(
    reader: SourceReader, task: int, raw: Any, canonical: dict[str, Any]
) -> dict[str, Any]:
    reader.guard()
    value = bundle(raw)
    contract = reader.contract(task)
    if (
        value["run_id"] != canonical["run_id"]
        or value["policy_sha"] != reader.owner["policy_sha"]
        or value["contract_digest"] != digest(workflow._contract_fields(contract))
        or value["executor_ordinal"] > canonical["executor_calls"]
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    chain, _ = reader._completed_role_chain(task, contract, canonical)
    original = chain.get(("executor", value["executor_ordinal"]))
    if original is None:
        raise core.LearningError("LEARNING_SOURCE_MISSING")
    gate = value["gate"]
    snapshot = core.exact(
        gate["snapshot"],
        {
            "base_sha",
            "snapshot_method",
            "snapshot_commit",
            "tree_hash",
            "diff_sha256",
            "provenance_complete",
        },
    )
    for key in ("base_sha", "snapshot_commit", "tree_hash"):
        core.hash_value(snapshot[key], size=40)
    core.hash_value(snapshot["diff_sha256"])
    if (
        snapshot["base_sha"] != contract.base_sha
        or snapshot["snapshot_method"] != agent_gate.SNAPSHOT_METHOD
        or snapshot["provenance_complete"] is not True
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")

    def commit_id(message: str) -> str:
        data = (
            f"tree {snapshot['tree_hash']}\nparent {contract.base_sha}\n"
            "author privacy-gateway orchestrator "
            "<orchestrator@privacy-gateway.invalid> 0 +0000\n"
            "committer privacy-gateway orchestrator "
            "<orchestrator@privacy-gateway.invalid> 0 +0000\n\n" + message + "\n"
        ).encode()
        return hashlib.sha1(
            b"commit " + str(len(data)).encode() + b"\0" + data
        ).hexdigest()

    native_commit = commit_id("private unreviewed continuation snapshot")
    if snapshot["snapshot_commit"] != commit_id(
        agent_gate.SNAPSHOT_MESSAGE
    ) or original["reservation"]["snapshot_after"] != digest(
        {
            "tree": snapshot["tree_hash"],
            "commit": native_commit,
            "base_sha": contract.base_sha,
        }
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    invocation = original["reservation"]["invocation_digest"]
    mirror = (
        reader.task_directory(task)
        / "continuation/snapshots"
        / digest({"invocation": invocation, "stage": "after"})
        / "objects.git"
    )
    result = subprocess.run(
        [
            "git",
            "--git-dir",
            str(mirror),
            "diff",
            "--binary",
            "--no-ext-diff",
            contract.base_sha,
            snapshot["tree_hash"],
        ],
        cwd=reader.root,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if (
        result.returncode
        or len(result.stdout) > reader.owner["limits"]["max_store_bytes"]
        or hashlib.sha256(result.stdout).hexdigest() != snapshot["diff_sha256"]
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    checks = gate.get("checks")
    expected = list(agent_gate.PROFILES[agent_gate.FULL_PROFILE])
    if not isinstance(checks, list) or len(checks) != len(expected):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    for name, check in zip(expected, checks, strict=True):
        if (
            not isinstance(check, dict)
            or check.get("id") != name
            or check.get("argv") != list(agent_gate.CHECK_COMMANDS[name])
        ):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        check_key = digest(
            {
                "run_id": value["run_id"],
                "executor_ordinal": value["executor_ordinal"],
                "repair_iteration": value["repair_iteration"],
                "snapshot": snapshot,
                "check_id": name,
            }
        )
        row = records.gate(
            reader._json(
                reader.task_directory(task)
                / "learning-source/gate"
                / (check_key + ".json")
            )
        )
        original_check = {
            key: check[key]
            for key in (
                "id",
                "argv",
                "status",
                "exit_code",
                "duration_seconds",
                "summary",
                "metrics",
            )
        }
        required = {
            "gate_profile": agent_gate.FULL_PROFILE,
            "check_name": name,
            "argv_digest": digest(check["argv"]),
            "test_identity": None,
            "outcome": "PASS" if check["status"] == "passed" else "FAIL",
            "exit_code": check["exit_code"],
            "evidence_digest": digest(original_check),
        }
        if (
            row["invocation_digest"] != check_key
            or row["run_id"] != value["run_id"]
            or row["policy_sha"] != value["policy_sha"]
            or row["snapshot_digest"] != digest(snapshot)
            or row["environment_digest"] != digest(environment(reader))
            or row["checks"] != [required]
        ):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    return value


def controller_problem(
    reader: SourceReader, task: int, canonical: dict[str, Any]
) -> tuple[str | None, dict[str, Any] | None]:
    contract = reader.contract(task)
    directory = reader.task_directory(task) / "learning-source/full-gates"
    if not directory.exists() and not directory.is_symlink():
        if canonical["executor_calls"] > 0:
            raise core.LearningError("LEARNING_SOURCE_MISSING")
        return None, None
    prior: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for ordinal in range(1, canonical["executor_calls"] + 1):
        path = directory / (canonical["run_id"] + "-" + str(ordinal) + ".json")
        # The latest gate must be retained; any prior consumed complete gate
        # cannot silently vanish under the amended source contract.
        source = verify(reader, task, reader._json(path), canonical)
        if source["gate"]["status"] == "failed":
            prior.append(source)
        if ordinal == canonical["executor_calls"]:
            current = source
    if (
        current is None
        or not agent_gate.gate_evidence_is_passing(current["gate"])
        or not prior
    ):
        return None, None
    previous = prior[-1]
    problem = current_problem_class(
        reader, contract.task_class, "controller", previous["gate"]
    )
    if problem is None:
        return None, None
    return problem, {"failed": previous, "corrected": current}


def retain(
    reader: SourceReader,
    task: int,
    run_id: str,
    ordinal: int,
    repair: int,
    gate: dict[str, Any],
) -> None:
    """Pinned workflow supplies its original normalized gate immediately."""
    from tools.agent_learning_companions import save

    reader.guard()
    contract = reader.contract(task)
    value = bundle(
        core.seal(
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "policy_sha": reader.owner["policy_sha"],
                "contract_digest": digest(workflow._contract_fields(contract)),
                "executor_ordinal": ordinal,
                "repair_iteration": repair,
                "gate": gate,
            }
        )
    )
    directory = reader.task_directory(task)
    save(
        reader,
        directory / "learning-source/full-gates",
        run_id + "-" + str(ordinal) + ".json",
        value,
        owner=directory,
    )
