"""Immutable workflow applicability from actual native completed/pending prefixes."""

from __future__ import annotations

from typing import Any

from tools import agent_learning as core
from tools import agent_learning_planning_records as records
from tools import agent_orchestrate as workflow
from tools.agent_continuation import digest
from tools.agent_epic_runtime_cli import secure_directory
from tools.agent_learning_companions import save
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_gate_problem import controller_problem, verify
from tools.agent_learning_verify import current_problem_class


def canonical(reader: SourceReader, task: int) -> dict[str, Any] | None:
    contract = reader.contract(task)
    path = (
        reader.task_directory(task)
        / "bounded-workflow"
        / (workflow._run_key(contract) + ".json")
    )
    if not path.exists() and not path.is_symlink():
        return None
    return reader._json(path)


def source(
    reader: SourceReader,
    task: int,
    role: str,
    state: dict[str, Any] | None,
    gate: dict[str, Any] | None,
    *,
    pending: bool = False,
) -> tuple[str | None, dict[str, Any] | None]:
    reader.guard()
    contract = reader.contract(task)
    if role == "controller":
        if state is None:
            raise core.LearningError("LEARNING_SOURCE_MISSING")
        problem, origin = controller_problem(reader, task, state)
        if origin is not None and gate != origin["corrected"]["gate"]:
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        return problem, origin
    if role != "executor":
        raise core.LearningError("LEARNING_INVOCATION_INVALID")
    if gate is None:
        return None, None
    if state is None:
        raise core.LearningError("LEARNING_SOURCE_MISSING")
    ordinal = state["executor_calls"] - int(pending)
    if ordinal < 1:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    path = (
        reader.task_directory(task)
        / "learning-source/full-gates"
        / (state["run_id"] + "-" + str(ordinal) + ".json")
    )
    origin = verify(reader, task, reader._json(path), state)
    if origin["gate"] != gate:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    problem = current_problem_class(reader, contract.task_class, role, origin["gate"])
    return problem, origin if problem is not None else None


def retain(
    reader: SourceReader,
    invocation: dict[str, Any],
    payload: dict[str, Any],
    prepared: tuple[str, str, dict[str, Any] | None, str | None, dict[str, Any] | None],
) -> None:
    role, prepared_digest, previous, problem, origin = prepared
    stripped = {
        key: item
        for key, item in payload.items()
        if key not in {"session_id", "trusted_policy"}
    }
    task = invocation["identity"]["task"]
    current = canonical(reader, task)
    if (
        current is None
        or role != invocation["role"]
        or digest(stripped) != prepared_digest
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    ordinal = 1 if previous is None else previous[role + "_calls"] + 1
    if (
        invocation["ordinal"] != ordinal
        or current[role + "_calls"] != ordinal
        or (previous is not None and current["run_id"] != previous["run_id"])
        or (
            previous is not None
            and current[("controller" if role == "executor" else "executor") + "_calls"]
            != previous[("controller" if role == "executor" else "executor") + "_calls"]
        )
        or current["invocation_digest"] != digest(payload)
        or current["role_pending"] != role
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    hints = payload["untrusted_verified_hints"]["hints"]
    ownership = reader.seed / "role-settings/ownership"
    secure_directory(ownership.parent, reader.root)
    if not ownership.exists() and not ownership.is_symlink():
        ownership.mkdir(mode=0o700)
    secure_directory(ownership, reader.root)
    with reader.current_pending_prefix(task, digest(payload)):
        if origin is not None:
            gate = (
                origin["corrected"]["gate"] if role == "controller" else origin["gate"]
            )
            actual_problem, actual_origin = source(
                reader, task, role, current, gate, pending=True
            )
            if actual_problem != problem or actual_origin != origin:
                raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        if hints and (
            origin is None
            or problem is None
            or any(hint["problem_class"] != problem for hint in hints)
        ):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        value = records.problem_binding(
            core.seal(
                {
                    "schema_version": "1.0",
                    "invocation_key": invocation["invocation_key"],
                    "role": role,
                    "source_kind": "SAME_RUN_GATE" if hints else "NONE",
                    "source_digest": digest(origin) if hints else None,
                    "problem_class": problem if hints else None,
                }
            )
        )
        directory = reader.task_directory(task) / "learning-invocations"
        save(
            reader,
            directory,
            invocation["invocation_key"] + ".problem.json",
            value,
            owner=reader.task_directory(task),
        )
        if (
            records.problem_binding(
                reader._json(
                    directory / (invocation["invocation_key"] + ".problem.json")
                )
            )
            != value
        ):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
