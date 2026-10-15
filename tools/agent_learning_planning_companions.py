"""Original single-source relevance companions for a consumed planning invocation."""

from __future__ import annotations

from typing import Any

from tools import agent_learning as core
from tools import agent_learning_planning_records as records
from tools.agent_continuation import digest
from tools.agent_learning_companions import save
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_sources import environment


def values(
    reader: SourceReader,
    invocation: dict[str, Any],
    request: dict[str, Any],
    current: dict[str, Any],
    proofs: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Shapes bind original proofs; caller must replay proofs before transport."""
    reader.guard()
    hints = request["untrusted_verified_hints"]["hints"]
    key = invocation["invocation_key"]
    role = invocation["role"]
    if role not in records.PLANNING_ROLES:
        raise core.LearningError("LEARNING_INVOCATION_INVALID")
    if invocation["selection_digest"] != digest(hints) or invocation[
        "lesson_ids"
    ] != sorted(item["lesson_id"] for item in hints):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    relevance = None
    if hints:
        if len(hints) != 1 or len(proofs) != 1:
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        selected = proofs[0]
        rule, verified = selected["rule"], selected["verified"]
        if (
            verified["lesson_id"] != hints[0]["lesson_id"]
            or rule["problem_class"] != hints[0]["problem_class"]
        ):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        relevance = records.relevance(
            core.seal(
                {
                    "schema_version": "1.0",
                    "current_request_digest": digest(request),
                    "corrected_validation_digest": selected["corrected"][
                        "validation_digest"
                    ],
                    "failure_validation_digest": selected["failure"][
                        "validation_digest"
                    ],
                    "current_predicate": records.CURRENT_PREDICATE,
                    "criteria_digest": current["criteria_digest"],
                    "grant_digest": current["grant_digest"],
                    "profile_digest": current["profile_digest"].removeprefix("sha256:"),
                    "environment_digest": digest(environment(reader, planning=True)),
                    "policy_sha": reader.owner["policy_sha"],
                }
            )
        )
    elif proofs:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    problem = records.problem_binding(
        core.seal(
            {
                "schema_version": "1.0",
                "invocation_key": key,
                "role": role,
                "source_kind": "NONE" if relevance is None else "PLANNING_VALIDATION",
                "source_digest": None if relevance is None else digest(relevance),
                "problem_class": None
                if relevance is None
                else hints[0]["problem_class"],
            }
        )
    )
    return problem, relevance


def retain(
    reader: SourceReader,
    invocation: dict[str, Any],
    request: dict[str, Any],
    current: dict[str, Any],
    proofs: list[dict[str, Any]],
) -> None:
    """Create immutable companions only at the already consumed managed prefix."""
    from tools.agent_epic_loop import RunnerLock
    from tools.agent_epic_planning import Admission

    reader.guard()
    RunnerLock.current_owned(reader.seed)
    task = request["identity"]["task"]
    source = Admission(reader.seed, reader.root)._storage(task)
    directory = source / "learning-invocations"
    existing = reader._json(directory / (invocation["invocation_key"] + ".json"))
    if existing != invocation or invocation["status"] != "RESERVED":
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    problem, relevance = values(reader, invocation, request, current, proofs)
    with RunnerLock(directory):
        if relevance is not None:
            save(
                reader,
                directory,
                invocation["invocation_key"] + ".relevance.json",
                relevance,
                owner=reader.seed,
            )
        save(
            reader,
            directory,
            invocation["invocation_key"] + ".problem.json",
            problem,
            owner=reader.seed,
        )
        # Partial writes remain blocked; neither canonical pending nor counters change.
        if (
            reader._json(directory / (invocation["invocation_key"] + ".problem.json"))
            != problem
        ):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        if (
            relevance is not None
            and reader._json(
                directory / (invocation["invocation_key"] + ".relevance.json")
            )
            != relevance
        ):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
