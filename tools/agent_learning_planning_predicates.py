"""Fixed planning equality predicates; callers must verify original sources first."""

from __future__ import annotations

from typing import Any

from tools import agent_epic_planning as admission
from tools import agent_learning as core
from tools import agent_learning_planning_records as records
from tools.agent_continuation import digest


def facts(request: dict[str, Any], *, head_sha: str, tree_hash: str) -> dict[str, Any]:
    """Normalize approved original facts, without treating hashes as authority."""
    core.hash_value(head_sha, size=40)
    core.hash_value(tree_hash, size=40)
    identity = request["identity"]
    profile = request["task_profile"]
    criteria = admission.extract_criteria(
        request["issue_body"], profile["criteria_sections"]
    )
    if criteria != request["criteria"]:
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    grant = admission.exact(request["grant"], {"causal_scope", "allowed_paths"})
    admission.strings(grant["causal_scope"], 64)
    admission.strings(grant["allowed_paths"], 64)
    core.identifier(profile["task_class"])
    for key in ("base_sha", "default_sha"):
        core.hash_value(request["evidence"][key], size=40)
    core.hash_value(identity["policy_sha"], size=40)
    planning = identity["planning_digest"]
    if not isinstance(planning, str) or not planning.startswith("sha256:"):
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    core.hash_value(planning.removeprefix("sha256:"))
    return {
        "criteria_digest": digest(criteria),
        "criterion_count": len(criteria),
        "grant_digest": digest(grant),
        "profile_digest": planning,
        "task_class": profile["task_class"],
        "policy_sha": identity["policy_sha"],
        "initial_head": head_sha,
        "initial_tree": tree_hash,
        "initial_base": request["evidence"]["base_sha"],
        "initial_default": request["evidence"]["default_sha"],
    }


def check_identity(role: str, value: dict[str, Any]) -> str:
    if role not in records.PLANNING_ROLES:
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    return digest(
        {
            "validator": "PROPOSAL_V1" if role == "planner" else "REVIEW_V1",
            "policy_sha": value["policy_sha"],
            "criterion_count": value["criterion_count"],
            "applicability": value,
        }
    )


def current_equivalent(current: dict[str, Any], corrected: dict[str, Any]) -> bool:
    """Relevance alone excludes Git identity; proof and measurements do not."""
    keys = (
        "criteria_digest",
        "criterion_count",
        "grant_digest",
        "profile_digest",
        "task_class",
        "policy_sha",
    )
    return all(current[key] == corrected[key] for key in keys)
