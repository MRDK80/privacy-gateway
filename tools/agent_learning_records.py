"""Exact redacted record shapes; original-source verification is separate."""

from __future__ import annotations

import re
from typing import Any

from tools import agent_learning as c
from tools.agent_continuation import digest

SOURCE_FIELDS = {
    "workflow_digest",
    "gate_digest",
    "controller_digest",
    "delivery_digest",
    "pr_ci_digest",
    "post_ci_digest",
    "demo_digest",
}


def repository(value: Any) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value)
        is None
        or len(value) > 256
    ):
        raise c.LearningError("LEARNING_IDENTITY_INVALID")


def nonce(value: Any) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"(?:[0-9a-f]{24}|[0-9a-f]{32}|[0-9a-f]{64})", value) is None
    ):
        raise c.LearningError("LEARNING_IDENTITY_INVALID")


def code(value: Any) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) is None
    ):
        raise c.LearningError("LEARNING_INVALID")


def identity(value: Any, *, invocation: bool = False) -> dict[str, Any]:
    fields = (
        {
            "repository",
            "epic",
            "task",
            "policy_sha",
            "owner_profile_digest",
            "original_run_id",
        }
        if invocation
        else {
            "repository",
            "epic",
            "task",
            "run_id",
            "policy_sha",
            "head_sha",
            "merge_sha",
        }
    )
    result = c.exact(value, fields)
    repository(result["repository"])
    c.integer(result["epic"], 2147483647, 1)
    c.integer(result["task"], 2147483647, 1)
    if result["epic"] == result["task"]:
        raise c.LearningError("LEARNING_IDENTITY_INVALID")
    c.hash_value(result["policy_sha"], size=40)
    if invocation:
        c.hash_value(result["owner_profile_digest"])
        nonce(result["original_run_id"])
    else:
        nonce(result["run_id"])
        c.hash_value(result["head_sha"], size=40)
        c.hash_value(result["merge_sha"], size=40, nullable=True)
    return result


def observation_id(value: dict[str, Any]) -> str:
    source = identity(value["identity"])
    return digest(
        {
            "repository": source["repository"],
            "epic": source["epic"],
            "task": source["task"],
            "run_id": source["run_id"],
            "kind": value["kind"],
        }
    )


def observation(raw: Any) -> dict[str, Any]:
    value = c.sealed(
        raw, {"record_id", "kind", "identity", "outcome", "metrics", "source"}
    )
    who = identity(value["identity"])
    if value["kind"] not in ("TERMINAL_WORKFLOW", "TASK_DELIVERY") or value[
        "record_id"
    ] != observation_id(value):
        raise c.LearningError("LEARNING_OBSERVATION_INVALID")
    outcome = c.exact(value["outcome"], {"status", "machine_codes"})
    if not isinstance(outcome["status"], str):
        raise c.LearningError("LEARNING_OBSERVATION_INVALID")
    codes = outcome["machine_codes"]
    if not isinstance(codes, list) or len(codes) > 16:
        raise c.LearningError("LEARNING_OBSERVATION_INVALID")
    for item in codes:
        code(item)
    if len(set(codes)) != len(codes):
        raise c.LearningError("LEARNING_OBSERVATION_INVALID")
    metrics = c.exact(
        value["metrics"],
        {
            "findings",
            "repair_iterations",
            "executor_calls",
            "controller_calls",
            "duration_seconds",
        },
    )
    for name, maximum in {
        "findings": 4096,
        "repair_iterations": 2,
        "executor_calls": 3,
        "controller_calls": 3,
        "duration_seconds": 86400,
    }.items():
        c.integer(metrics[name], maximum)
    source = c.exact(value["source"], SOURCE_FIELDS)
    for name in SOURCE_FIELDS:
        c.hash_value(source[name], nullable=name != "workflow_digest")
    if value["kind"] == "TASK_DELIVERY":
        if (
            outcome["status"] != "TASK_DONE"
            or who["merge_sha"] is None
            or any(source[k] is None for k in SOURCE_FIELDS)
        ):
            raise c.LearningError("LEARNING_OBSERVATION_INVALID")
    elif outcome["status"] not in c.OUTCOMES or who["merge_sha"] is not None:
        raise c.LearningError("LEARNING_OBSERVATION_INVALID")
    return value


def invocation_id(value: dict[str, Any]) -> str:
    return digest(
        {key: value[key] for key in ("identity", "role", "ordinal", "repair_iteration")}
    )


def invocation(raw: Any) -> dict[str, Any]:
    value = c.sealed(
        raw,
        {
            "invocation_key",
            "identity",
            "role",
            "ordinal",
            "repair_iteration",
            "catalog_digest",
            "selection_digest",
            "lesson_ids",
            "input_digest",
            "prompt_digest",
            "status",
        },
    )
    identity(value["identity"], invocation=True)
    role = value["role"]
    if (
        not isinstance(role, str)
        or role not in c.ROLES
        or value["status"] not in ("RESERVED", "COMPLETED", "UNKNOWN")
    ):
        raise c.LearningError("LEARNING_INVOCATION_INVALID")
    c.integer(value["repair_iteration"], 2)
    c.integer(value["ordinal"], 3 if role in {"executor", "controller"} else 32, 1)
    if role == "executor" and value["ordinal"] != value["repair_iteration"] + 1:
        raise c.LearningError("LEARNING_INVOCATION_INVALID")
    for key in (
        "invocation_key",
        "catalog_digest",
        "selection_digest",
        "input_digest",
        "prompt_digest",
    ):
        c.hash_value(value[key])
    lessons = value["lesson_ids"]
    if not isinstance(lessons, list) or len(lessons) > 3:
        raise c.LearningError("LEARNING_INVOCATION_INVALID")
    for lesson in lessons:
        c.hash_value(lesson)
    if lessons != sorted(set(lessons)) or value["invocation_key"] != invocation_id(
        value
    ):
        raise c.LearningError("LEARNING_INVOCATION_INVALID")
    return value


def candidate(raw: Any) -> dict[str, Any]:
    value = c.sealed(
        raw,
        {
            "candidate_id",
            "rule_id",
            "role",
            "observation_digest",
            "applicability_digest",
            "evidence_digest",
        },
    )
    c.identifier(value["rule_id"])
    if value["role"] not in ("planner", "executor", "controller"):
        raise c.LearningError("LEARNING_CANDIDATE_INVALID")
    for key in (
        "candidate_id",
        "observation_digest",
        "applicability_digest",
        "evidence_digest",
    ):
        c.hash_value(value[key])
    if value["candidate_id"] != digest(
        {k: v for k, v in value.items() if k not in {"candidate_id", "record_digest"}}
    ):
        raise c.LearningError("LEARNING_CANDIDATE_INVALID")
    return value


def verification(raw: Any) -> dict[str, Any]:
    value = c.sealed(
        raw,
        {
            "lesson_id",
            "candidate_digest",
            "catalog_digest",
            "observation_digest",
            "evidence_digest",
            "applicability_digest",
        },
    )
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
        {k: v for k, v in value.items() if k not in {"lesson_id", "record_digest"}}
    ):
        raise c.LearningError("LEARNING_VERIFICATION_INVALID")
    return value


def evaluation(raw: Any) -> dict[str, Any]:
    value = c.sealed(
        raw, {"evaluation_id", "baseline_digest", "hinted_digest", "comparison"}
    )
    for key in ("evaluation_id", "hinted_digest"):
        c.hash_value(value[key])
    c.hash_value(value["baseline_digest"], nullable=True)
    comparison = c.exact(
        value["comparison"],
        {
            "status",
            "outcome_regressed",
            "iteration_delta",
            "call_delta",
            "duration_delta_seconds",
        },
    )
    status = comparison["status"]
    if status not in (
        "UNMEASURED",
        "NO_MEASURED_COMPARISON",
        "NO_MEASURED_CHANGE",
        "MEASURED_IMPROVEMENT",
        "ROLLBACK_REQUIRED",
    ):
        raise c.LearningError("LEARNING_EVALUATION_INVALID")
    fields = (
        "outcome_regressed",
        "iteration_delta",
        "call_delta",
        "duration_delta_seconds",
    )
    if status in ("UNMEASURED", "NO_MEASURED_COMPARISON"):
        if any(comparison[k] is not None for k in fields) or (
            status == "UNMEASURED"
        ) != (value["baseline_digest"] is None):
            raise c.LearningError("LEARNING_EVALUATION_INVALID")
    else:
        if (
            value["baseline_digest"] is None
            or type(comparison["outcome_regressed"]) is not bool
        ):
            raise c.LearningError("LEARNING_EVALUATION_INVALID")
        for key in fields[1:]:
            c.integer(comparison[key], 86400, -86400)
        if status != "ROLLBACK_REQUIRED" and (
            comparison["outcome_regressed"]
            or any(comparison[k] > 0 for k in fields[1:])
        ):
            raise c.LearningError("LEARNING_EVALUATION_INVALID")
    if value["evaluation_id"] != digest(
        {k: v for k, v in value.items() if k not in {"evaluation_id", "record_digest"}}
    ):
        raise c.LearningError("LEARNING_EVALUATION_INVALID")
    return value


def tombstone(raw: Any) -> dict[str, Any]:
    value = c.sealed(raw, {"lesson_id", "reason_code", "evaluation_digest"})
    c.hash_value(value["lesson_id"])
    c.hash_value(value["evaluation_digest"])
    if value["reason_code"] not in ("LEARNING_REGRESSION", "OWNER_DISABLED"):
        raise c.LearningError("LEARNING_TOMBSTONE_INVALID")
    return value


def gate(raw: Any) -> dict[str, Any]:
    value = c.sealed(
        raw,
        {
            "invocation_digest",
            "run_id",
            "policy_sha",
            "snapshot_digest",
            "environment_digest",
            "checks",
        },
    )
    for key in ("invocation_digest", "snapshot_digest", "environment_digest"):
        c.hash_value(value[key])
    c.hash_value(value["policy_sha"], size=40)
    nonce(value["run_id"])
    if not isinstance(value["checks"], list) or not 1 <= len(value["checks"]) <= 16:
        raise c.LearningError("LEARNING_GATE_INVALID")
    seen: set[str] = set()
    for raw_check in value["checks"]:
        check = c.exact(
            raw_check,
            {
                "gate_profile",
                "check_name",
                "argv_digest",
                "test_identity",
                "outcome",
                "exit_code",
                "evidence_digest",
            },
        )
        who = c.check_identity(
            {
                k: check[k]
                for k in ("gate_profile", "check_name", "argv_digest", "test_identity")
            }
        )
        c.hash_value(check["evidence_digest"])
        c.integer(check["exit_code"], 255)
        if (
            check["outcome"] not in ("PASS", "FAIL")
            or (check["outcome"] == "PASS") != (check["exit_code"] == 0)
            or digest(who) in seen
        ):
            raise c.LearningError("LEARNING_GATE_INVALID")
        seen.add(digest(who))
    return value


VALIDATORS = {
    "observation": observation,
    "candidate": candidate,
    "verification": verification,
    "invocation": invocation,
    "measurement": c.measurement,
    "evaluation": evaluation,
    "tombstone": tombstone,
    "gate": gate,
}
