"""Closed private planning source records; shapes alone do not prove origin."""

from __future__ import annotations

from typing import Any

from tools import agent_learning as c
from tools import agent_learning_records as legacy

PLANNING_ROLES = frozenset({"planner", "planning_review"})
FAILURE_CODES = {
    "PROPOSAL_V1": frozenset(
        {"PLANNING_CRITERIA_INCOMPLETE", "PLANNING_SCOPE_NOT_MINIMAL"}
    ),
    "REVIEW_V1": frozenset({"PLANNING_REVIEW_INVALID"}),
}
CURRENT_PREDICATE = "CURRENT_APPROVED_CRITERIA_AND_GRANT"


def identity(raw: Any) -> dict[str, Any]:
    value = c.exact(
        raw,
        {
            "repository",
            "epic",
            "task",
            "planning_digest",
            "mandate_digest",
            "policy_sha",
        },
    )
    legacy.repository(value["repository"])
    c.integer(value["epic"], 2147483647, 1)
    c.integer(value["task"], 2147483647, 1)
    if value["epic"] == value["task"]:
        raise c.LearningError("LEARNING_IDENTITY_INVALID")
    c.hash_value(value["policy_sha"], size=40)
    for key in ("planning_digest", "mandate_digest"):
        digest = value[key]
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise c.LearningError("LEARNING_IDENTITY_INVALID")
        c.hash_value(digest.removeprefix("sha256:"))
    return value


def _original(value: dict[str, Any]) -> None:
    identity(value["identity"])
    if not isinstance(value["role"], str) or value["role"] not in PLANNING_ROLES:
        raise c.LearningError("LEARNING_INVALID")
    c.integer(value["ordinal"], 3, 1)


def _outcome(value: dict[str, Any]) -> None:
    expected = "PROPOSAL_V1" if value["role"] == "planner" else "REVIEW_V1"
    if not isinstance(value["machine_code"], str):
        raise c.LearningError("LEARNING_INVALID")
    if value.get("validator", expected) != expected:
        raise c.LearningError("LEARNING_INVALID")
    if value["outcome"] == "PASS":
        if value["machine_code"] != "OK":
            raise c.LearningError("LEARNING_INVALID")
    elif value["outcome"] == "FAIL":
        if value["machine_code"] not in FAILURE_CODES[expected]:
            raise c.LearningError("LEARNING_INVALID")
    else:
        raise c.LearningError("LEARNING_INVALID")


def validation(raw: Any) -> dict[str, Any]:
    value = c.sealed(
        raw,
        {
            "identity",
            "role",
            "ordinal",
            "request_digest",
            "response_digest",
            "applicability_digest",
            "validator",
            "outcome",
            "machine_code",
            "check_identity",
        },
    )
    _original(value)
    _outcome(value)
    for key in (
        "request_digest",
        "response_digest",
        "applicability_digest",
        "check_identity",
    ):
        c.hash_value(value[key])
    return value


def context(raw: Any) -> dict[str, Any]:
    value = c.sealed(
        raw,
        {
            "identity",
            "role",
            "ordinal",
            "now",
            "owner_identity",
            "started_at",
            "task_iterations",
            "follow_up_issues",
            "request_digest",
        },
    )
    _original(value)
    c.integer(value["now"], 2**53 - 1)
    c.integer(value["started_at"], value["now"])
    for key in ("task_iterations", "follow_up_issues"):
        c.integer(value[key], 2147483647)
    owner = value["owner_identity"]
    if (
        not isinstance(owner, str)
        or not 1 <= len(owner) <= 256
        or owner != owner.strip()
        or any(ord(char) < 32 for char in owner)
    ):
        raise c.LearningError("LEARNING_IDENTITY_INVALID")
    c.hash_value(value["request_digest"])
    return value


def observation(raw: Any) -> dict[str, Any]:
    value = c.sealed(
        raw,
        {
            "kind",
            "identity",
            "role",
            "ordinal",
            "outcome",
            "machine_code",
            "request_digest",
            "response_digest",
            "validation_digest",
            "applicability_digest",
        },
    )
    _original(value)
    _outcome(value)
    if value["kind"] != "PLANNING_VALIDATION":
        raise c.LearningError("LEARNING_INVALID")
    for key in (
        "request_digest",
        "response_digest",
        "validation_digest",
        "applicability_digest",
    ):
        c.hash_value(value[key])
    return value


def problem_binding(raw: Any) -> dict[str, Any]:
    value = c.sealed(
        raw, {"invocation_key", "role", "source_kind", "source_digest", "problem_class"}
    )
    c.hash_value(value["invocation_key"])
    role, kind = value["role"], value["source_kind"]
    if not isinstance(role, str) or role not in c.ROLES or not isinstance(kind, str):
        raise c.LearningError("LEARNING_INVOCATION_INVALID")
    if kind == "NONE":
        if value["source_digest"] is not None or value["problem_class"] is not None:
            raise c.LearningError("LEARNING_INVALID")
    else:
        if (
            (kind == "SAME_RUN_GATE" and role not in {"executor", "controller"})
            or (kind == "PLANNING_VALIDATION" and role not in PLANNING_ROLES)
            or kind not in {"SAME_RUN_GATE", "PLANNING_VALIDATION"}
        ):
            raise c.LearningError("LEARNING_INVALID")
        c.hash_value(value["source_digest"])
        c.identifier(value["problem_class"])
    return value


def relevance(raw: Any) -> dict[str, Any]:
    keys = {
        "current_request_digest",
        "corrected_validation_digest",
        "failure_validation_digest",
        "current_predicate",
        "criteria_digest",
        "grant_digest",
        "profile_digest",
        "environment_digest",
        "policy_sha",
    }
    value = c.sealed(raw, keys)
    if value["current_predicate"] != CURRENT_PREDICATE:
        raise c.LearningError("LEARNING_INVALID")
    c.hash_value(value["policy_sha"], size=40)
    for key in keys - {"current_predicate", "policy_sha"}:
        c.hash_value(value[key])
    return value


def artifact(raw: Any) -> dict[str, Any]:
    """Validate the original closed envelope; reader replays its real validators."""
    from tools import agent_epic_planning as admission
    from tools.agent_continuation import digest
    from tools.agent_epic_planning_roles import REQUEST_KEYS

    value = c.sealed(
        raw, {"identity", "role", "ordinal", "kind", "payload", "payload_digest"}
    )
    _original(value)
    payload = value["payload"]
    if not isinstance(payload, dict):
        raise c.LearningError("LEARNING_INVALID")
    kind = value["kind"]
    if kind == "REQUEST":
        fields = REQUEST_KEYS | (
            {"untrusted_verified_hints"}
            if "untrusted_verified_hints" in payload
            else set()
        )
    elif kind == "RESPONSE":
        fields = (
            admission.PROPOSAL_KEYS
            if value["role"] == "planner"
            else admission.REVIEW_KEYS
        )
    else:
        raise c.LearningError("LEARNING_INVALID")
    c.exact(payload, fields)
    if payload["schema_version"] != "1.0":
        raise c.LearningError("LEARNING_INVALID")
    c.hash_value(value["payload_digest"])
    if value["payload_digest"] != digest(payload):
        raise c.LearningError("LEARNING_SOURCE_MISMATCH")
    return value


def _promotion_source(value: dict[str, Any]) -> None:
    if (
        value["source_kind"] != "PLANNING_VALIDATION"
        or not isinstance(value["validator"], str)
        or value["validator"]
        not in {
            "PROPOSAL_V1",
            "REVIEW_V1",
        }
    ):
        raise c.LearningError("LEARNING_INVALID")
    c.hash_value(value["check_identity_digest"])


def candidate(raw: Any) -> dict[str, Any]:
    from tools.agent_continuation import digest

    value = c.sealed(
        raw,
        {
            "candidate_id",
            "rule_id",
            "role",
            "observation_digest",
            "applicability_digest",
            "evidence_digest",
            "source_kind",
            "validator",
            "check_identity_digest",
        },
    )
    _promotion_source(value)
    c.identifier(value["rule_id"])
    expected = {"planner": "PROPOSAL_V1", "controller": "REVIEW_V1"}
    if (
        not isinstance(value["role"], str)
        or expected.get(value["role"]) != value["validator"]
    ):
        raise c.LearningError("LEARNING_CANDIDATE_INVALID")
    for key in (
        "candidate_id",
        "observation_digest",
        "applicability_digest",
        "evidence_digest",
    ):
        c.hash_value(value[key])
    if value["candidate_id"] != digest(
        {
            key: item
            for key, item in value.items()
            if key not in {"candidate_id", "record_digest"}
        }
    ):
        raise c.LearningError("LEARNING_CANDIDATE_INVALID")
    return value


def verification(raw: Any) -> dict[str, Any]:
    from tools.agent_continuation import digest

    value = c.sealed(
        raw,
        {
            "lesson_id",
            "candidate_digest",
            "catalog_digest",
            "observation_digest",
            "evidence_digest",
            "applicability_digest",
            "source_kind",
            "validator",
            "check_identity_digest",
        },
    )
    _promotion_source(value)
    for key in (
        "lesson_id",
        "candidate_digest",
        "catalog_digest",
        "observation_digest",
        "evidence_digest",
        "applicability_digest",
    ):
        c.hash_value(value[key])
    if value["lesson_id"] != digest(
        {
            key: item
            for key, item in value.items()
            if key not in {"lesson_id", "record_digest"}
        }
    ):
        raise c.LearningError("LEARNING_VERIFICATION_INVALID")
    return value
