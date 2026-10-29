"""Read-only planning lesson selection from replayed original proof."""

from __future__ import annotations

from typing import Any

from tools import agent_learning as core
from tools import agent_learning_hints as hints
from tools.agent_continuation import digest
from tools.agent_learning_planning_predicates import current_equivalent
from tools.agent_learning_planning_promotion import pair, retained
from tools.agent_learning_planning_sources import replay
from tools.agent_learning_sources import environment
from tools.agent_learning_verify import Verifier


def select(
    verifier: Verifier, role: str, current: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reader = verifier.reader
    reader.guard()
    if role not in {"planner", "planning_review"}:
        raise core.LearningError("LEARNING_INVOCATION_INVALID")
    # Existing unresolved-run/source-chain and negative-latch checks remain mandatory.
    workflow_items = verifier.retained()
    items = retained(reader)
    tombstones = {
        item["record"]["lesson_id"]
        for item in workflow_items + items
        if item["kind"] == "tombstone"
    }
    observations = [
        item["record"] for item in items if item["kind"] == "planning_observation"
    ]
    candidates = {
        digest(item["record"]): item["record"]
        for item in items
        if item["kind"] == "planning_candidate"
    }
    catalog_role = "controller" if role == "planning_review" else role
    matches: list[dict[str, Any]] = []
    for item in items:
        if item["kind"] != "planning_verification":
            continue
        verified = item["record"]
        if verified["lesson_id"] in tombstones:
            continue
        if verified["catalog_digest"] != reader.owner["catalog_digest"]:
            continue
        candidate = candidates.get(verified["candidate_digest"])
        if candidate is None:
            raise core.LearningError("LEARNING_SOURCE_MISSING")
        linked = (
            "source_kind",
            "validator",
            "observation_digest",
            "applicability_digest",
            "evidence_digest",
            "check_identity_digest",
        )
        if any(candidate[key] != verified[key] for key in linked):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        rules = [
            rule
            for rule in reader.planning_catalog["rules"]
            if rule["rule_id"] == candidate["rule_id"]
            and rule["role"] == catalog_role
            and rule["task_class"] == current["task_class"]
            and rule["environment"] == environment(reader, planning=True)
        ]
        if not rules:
            continue
        if len(rules) != 1:
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        rule = rules[0]
        if candidate["validator"] != rule["validator"]:
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        afters = [
            item
            for item in observations
            if digest(item) == verified["observation_digest"]
        ]
        if not afters:
            raise core.LearningError("LEARNING_SOURCE_MISSING")
        confirmed = False
        for after in afters:
            if after["role"] != role or after["outcome"] != "PASS":
                raise core.LearningError("LEARNING_SOURCE_MISMATCH")
            for before in observations:
                proof = pair(reader, verifier.session, before, after, rule)
                if proof is None:
                    continue
                proof_keys = (
                    "applicability_digest",
                    "evidence_digest",
                    "check_identity_digest",
                )
                if any(
                    proof[key] != verified[key] or proof[key] != candidate[key]
                    for key in proof_keys
                ):
                    continue
                confirmed = True
                corrected = replay(
                    reader, after["identity"]["task"], role, after["ordinal"]
                )
                if current_equivalent(current, corrected["facts"]):
                    matches.append(
                        {
                            "verified": verified,
                            "rule": rule,
                            "failure": before,
                            "corrected": after,
                        }
                    )
        if not confirmed:
            raise core.LearningError("LEARNING_VERIFICATION_STALE")
    # Ambiguous classification never selects a hint; no model prose is consulted.
    unique_rules = {item["rule"]["rule_id"] for item in matches}
    if len(unique_rules) != 1:
        matches = []
    selected: list[dict[str, Any]] = []
    proofs: list[dict[str, Any]] = []
    for match in sorted(matches, key=lambda item: item["verified"]["lesson_id"]):
        # The approved companion binds one original pair: deterministic origin.
        if selected or len(selected) >= reader.owner["limits"]["max_hints_per_role"]:
            break
        verified, rule = match["verified"], match["rule"]
        if any(item["lesson_id"] == verified["lesson_id"] for item in selected):
            continue
        selected.append(
            {
                "lesson_id": verified["lesson_id"],
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
        )
        proofs.append(match)
    envelope = hints.envelope(
        {
            "schema_version": "1.0",
            "role": role,
            "catalog_digest": reader.owner["catalog_digest"],
            "selection_digest": digest(selected),
            "hints": selected,
        },
        reader.owner,
        role=role,
    )
    return envelope, proofs


def revalidate(verifier: Verifier, proof: dict[str, Any]) -> None:
    """Reread the selected original pair without retrying a pending current call."""
    reader = verifier.reader
    reader.guard()
    rule, verified = proof["rule"], proof["verified"]
    if (
        rule not in reader.planning_catalog["rules"]
        or verified["catalog_digest"] != reader.owner["catalog_digest"]
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    items = retained(reader)
    sources = {(item["kind"], digest(item["record"])): item["record"] for item in items}
    if ("planning_verification", digest(verified)) not in sources:
        raise core.LearningError("LEARNING_SOURCE_MISSING")
    if any(
        item["kind"] == "tombstone"
        and item["record"]["lesson_id"] == verified["lesson_id"]
        for item in items
    ):
        raise core.LearningError("LEARNING_VERIFICATION_STALE")
    candidate = _original_records(proof, items)
    paired = pair(reader, verifier.session, proof["failure"], proof["corrected"], rule)
    _original_links(proof, candidate, paired)


def _original_records(
    proof: dict[str, Any], items: list[dict[str, Any]]
) -> dict[str, Any]:
    verified = proof["verified"]
    sources = {(item["kind"], digest(item["record"])): item["record"] for item in items}
    if ("planning_verification", digest(verified)) not in sources:
        raise core.LearningError("LEARNING_SOURCE_MISSING")
    for name in ("failure", "corrected"):
        if ("planning_observation", digest(proof[name])) not in sources:
            raise core.LearningError("LEARNING_SOURCE_MISSING")
    candidate = sources.get(("planning_candidate", verified["candidate_digest"]))
    if candidate is None:
        raise core.LearningError("LEARNING_SOURCE_MISSING")
    return dict(candidate)


def _original_links(
    proof: dict[str, Any], candidate: dict[str, Any], paired: dict[str, Any] | None
) -> None:
    rule, verified = proof["rule"], proof["verified"]
    if paired is None:
        raise core.LearningError("LEARNING_VERIFICATION_STALE")
    links = (
        "source_kind",
        "validator",
        "observation_digest",
        "applicability_digest",
        "evidence_digest",
        "check_identity_digest",
    )
    if (
        candidate["rule_id"] != rule["rule_id"]
        or candidate["validator"] != rule["validator"]
        or any(candidate[key] != verified[key] for key in links)
        or paired["after_digest"] != verified["observation_digest"]
        or any(
            paired[key] != verified[key]
            for key in (
                "applicability_digest",
                "evidence_digest",
                "check_identity_digest",
            )
        )
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
