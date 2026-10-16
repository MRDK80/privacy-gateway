"""Closed private source shapes for the owner-approved role-source addendum."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from tools import agent_learning as core
from tools import agent_learning_planning_records as records


def identity() -> dict[str, Any]:
    return {
        "repository": "OWNER/repo",
        "epic": 294,
        "task": 301,
        "planning_digest": "sha256:" + "a" * 64,
        "mandate_digest": "sha256:" + "b" * 64,
        "policy_sha": "c" * 40,
    }


def validation() -> dict[str, Any]:
    return core.seal(
        {
            "schema_version": "1.0",
            "identity": identity(),
            "role": "planner",
            "ordinal": 1,
            "request_digest": "d" * 64,
            "response_digest": "e" * 64,
            "applicability_digest": "f" * 64,
            "validator": "PROPOSAL_V1",
            "outcome": "FAIL",
            "machine_code": "PLANNING_CRITERIA_INCOMPLETE",
            "check_identity": "1" * 64,
        }
    )


def context() -> dict[str, Any]:
    return core.seal(
        {
            "schema_version": "1.0",
            "identity": identity(),
            "role": "planner",
            "ordinal": 1,
            "now": 11,
            "started_at": 10,
            "owner_identity": "OWNER",
            "task_iterations": 0,
            "follow_up_issues": 0,
            "request_digest": "d" * 64,
        }
    )


def test_closed_original_validation_and_context_shapes() -> None:
    assert records.validation(validation()) == validation()
    assert records.context(context()) == context()


@pytest.mark.parametrize(
    "key,value",
    [
        ("ordinal", True),
        ("ordinal", 4),
        ("validator", "MODEL_FINDING"),
        ("role", "executor"),
        ("outcome", "UNKNOWN"),
        ("machine_code", "MODEL_PROPOSED_SYMPTOM"),
        ("extra", "approval"),
    ],
)
def test_validation_rejects_unrepresentable_or_forged_shapes(
    key: str,
    value: Any,
) -> None:
    item = copy.deepcopy(validation())
    item[key] = value
    with pytest.raises(core.LearningError):
        records.validation(core.seal(item))


@pytest.mark.parametrize(
    "key,value",
    [
        ("now", True),
        ("started_at", 12),
        ("task_iterations", -1),
        ("follow_up_issues", True),
        ("extra", "owner approval"),
    ],
)
def test_context_rejects_guessed_or_invalid_shape(key: str, value: Any) -> None:
    item = copy.deepcopy(context())
    item[key] = value
    with pytest.raises(core.LearningError):
        records.context(core.seal(item))


def test_pass_cannot_preserve_failure_code() -> None:
    item = validation()
    item["outcome"] = "PASS"
    with pytest.raises(core.LearningError):
        records.validation(core.seal(item))


def test_problem_companion_binds_source_without_changing_hint_bytes() -> None:
    item = core.seal(
        {
            "schema_version": "1.0",
            "invocation_key": "a" * 64,
            "role": "planning_review",
            "source_kind": "PLANNING_VALIDATION",
            "source_digest": "b" * 64,
            "problem_class": "criteria",
        }
    )
    assert records.problem_binding(item) == item
    item["source_kind"] = "NONE"
    with pytest.raises(core.LearningError):
        records.problem_binding(core.seal(item))


def test_unknown_problem_source_is_an_exact_null_variant() -> None:
    item = core.seal(
        {
            "schema_version": "1.0",
            "invocation_key": "a" * 64,
            "role": "controller",
            "source_kind": "NONE",
            "source_digest": None,
            "problem_class": None,
        }
    )
    assert records.problem_binding(item) == item
    item["role"] = "planner"
    item["source_kind"] = "SAME_RUN_GATE"
    item["source_digest"] = "b" * 64
    item["problem_class"] = "typing"
    with pytest.raises(core.LearningError):
        records.problem_binding(core.seal(item))


@pytest.mark.parametrize("value", [None, True, [], {}, 1])
def test_untrusted_enum_types_are_rejected(value: Any) -> None:
    item = validation()
    item["machine_code"] = value
    with pytest.raises(core.LearningError):
        records.validation(core.seal(item))
    item = core.seal(
        {
            "schema_version": "1.0",
            "invocation_key": "a" * 64,
            "role": "controller",
            "source_kind": value,
            "source_digest": None,
            "problem_class": None,
        }
    )
    with pytest.raises(core.LearningError):
        records.problem_binding(item)


def test_original_response_artifact_closed_keys_and_digest() -> None:
    from tools.agent_continuation import digest

    payload = {
        "schema_version": "1.0",
        "plan_digest": "sha256:" + "d" * 64,
        "verdict": "PASS",
        "findings": [],
    }
    item = core.seal(
        {
            "schema_version": "1.0",
            "identity": identity(),
            "role": "planning_review",
            "ordinal": 1,
            "kind": "RESPONSE",
            "payload": payload,
            "payload_digest": digest(payload),
        }
    )
    assert records.artifact(item) == item
    item["payload"]["owner_approval"] = True
    item["payload_digest"] = digest(item["payload"])
    with pytest.raises(core.LearningError):
        records.artifact(core.seal(item))


def test_artifact_hash_cannot_substitute_for_original_payload() -> None:
    payload = {
        "schema_version": "1.0",
        "plan_digest": "sha256:" + "d" * 64,
        "verdict": "PASS",
        "findings": [],
    }
    item = core.seal(
        {
            "schema_version": "1.0",
            "identity": identity(),
            "role": "planning_review",
            "ordinal": 1,
            "kind": "RESPONSE",
            "payload": payload,
            "payload_digest": "f" * 64,
        }
    )
    with pytest.raises(core.LearningError, match="LEARNING_SOURCE_MISMATCH"):
        records.artifact(item)


def promotion(*, verified: bool = False) -> dict[str, Any]:
    from tools.agent_continuation import digest

    value: dict[str, Any] = {
        "schema_version": "1.0",
        "observation_digest": "a" * 64,
        "applicability_digest": "b" * 64,
        "evidence_digest": "c" * 64,
        "source_kind": "PLANNING_VALIDATION",
        "validator": "PROPOSAL_V1",
        "check_identity_digest": "d" * 64,
    }
    if verified:
        value.update(candidate_digest="e" * 64, catalog_digest="f" * 64)
        key = "lesson_id"
    else:
        value.update(rule_id="synthetic", role="planner")
        key = "candidate_id"
    value[key] = digest(value)
    return core.seal(value)


@pytest.mark.parametrize("verified", [False, True])
def test_planning_promotion_cannot_be_a_workflow_record(verified: bool) -> None:
    from tools import agent_learning_records as workflow_records

    value = promotion(verified=verified)
    check = records.verification if verified else records.candidate
    legacy = workflow_records.verification if verified else workflow_records.candidate
    assert check(value) == value
    with pytest.raises(core.LearningError):
        legacy(value)
    changed = core.seal({**value, "source_kind": "TERMINAL_WORKFLOW"})
    with pytest.raises(core.LearningError):
        check(changed)
