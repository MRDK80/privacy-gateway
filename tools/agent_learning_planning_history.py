"""Exact original hinted planning provenance, including independent delivery proof."""

from __future__ import annotations

from typing import Any

from tools import agent_learning as core
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_planning_predicates import current_equivalent


def verify(
    reader: SourceReader,
    role: str,
    envelope: dict[str, Any],
    relevance: dict[str, Any],
    current: dict[str, Any],
) -> None:
    from tools.agent_learning_planning_promotion import retained
    from tools.agent_learning_planning_selection import revalidate
    from tools.agent_learning_planning_sources import replay
    from tools.agent_learning_verify import Verifier

    reader.guard()
    verifier = reader.planning_verifier
    if not isinstance(verifier, Verifier) or verifier.reader is not reader:
        raise core.LearningError("LEARNING_SOURCE_INVALID")
    workflow_items = verifier.retained()
    items = retained(reader)
    hint = envelope["hints"][0]
    if any(
        item["kind"] == "tombstone" and item["record"]["lesson_id"] == hint["lesson_id"]
        for item in workflow_items + items
    ):
        raise core.LearningError("LEARNING_VERIFICATION_STALE")
    proof = _original_proof(reader, role, envelope, relevance, items)
    revalidate(verifier, proof)
    corrected = replay(
        reader,
        proof["corrected"]["identity"]["task"],
        role,
        proof["corrected"]["ordinal"],
    )
    if not current_equivalent(current, corrected["facts"]):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    reader.guard()


def _original_proof(
    reader: SourceReader,
    role: str,
    envelope: dict[str, Any],
    relevance: dict[str, Any],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Original links only; callers retain their distinct selection authority."""
    hint = envelope["hints"][0]
    verified = [
        item["record"]
        for item in items
        if item["kind"] == "planning_verification"
        and item["record"]["lesson_id"] == hint["lesson_id"]
    ]
    observations = [
        item["record"] for item in items if item["kind"] == "planning_observation"
    ]
    before = [
        item
        for item in observations
        if item["validation_digest"] == relevance["failure_validation_digest"]
    ]
    after = [
        item
        for item in observations
        if item["validation_digest"] == relevance["corrected_validation_digest"]
    ]
    if len(verified) != 1 or len(before) != 1 or len(after) != 1:
        raise core.LearningError("LEARNING_SOURCE_MISSING")
    # Store digests include the sealed record, rather than its internal digest.
    from tools.agent_continuation import digest

    sources = [
        item["record"]
        for item in items
        if item["kind"] == "planning_candidate"
        and digest(item["record"]) == verified[0]["candidate_digest"]
    ]
    if len(sources) != 1:
        raise core.LearningError("LEARNING_SOURCE_MISSING")
    rules = [
        rule
        for rule in reader.planning_catalog["rules"]
        if rule["rule_id"] == sources[0]["rule_id"]
    ]
    if len(rules) != 1 or before[0]["role"] != role or after[0]["role"] != role:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    rule = rules[0]
    expected = {
        "lesson_id": verified[0]["lesson_id"],
        "trust": "UNTRUSTED_VERIFIED_HINT",
        **{
            key: rule[key]
            for key in (
                "problem_class",
                "symptom_code",
                "diagnostic_steps",
                "minimal_fix",
                "applicability",
            )
        },
        "permissions": None,
    }
    if hint != expected:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    return {
        "verified": verified[0],
        "rule": rule,
        "failure": before[0],
        "corrected": after[0],
    }
