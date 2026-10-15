"""Closed opt-in learning data; validation does not establish source authority."""

from __future__ import annotations

import re
from typing import Any, cast

from tools.agent_continuation import digest
from tools.agent_orchestrate import OrchestrationError
from tools.agent_verified_lessons import LessonError, _safe_text

PREDICATES = frozenset(
    {
        "FULL_GATE_PASS",
        "INDEPENDENT_CONTROLLER_PASS",
        "EXACT_PR_CI_PASS",
        "EXACT_POST_CI_PASS",
        "APPROVED_DEMO_PASS",
        "MATCHED_FAILURE_AND_CORRECTION",
        "RETAINED_REGRESSION_CHECK_PASS",
    }
)
ROLES = frozenset({"planner", "planning_review", "executor", "controller"})
OUTCOMES = frozenset(
    {"PASS", "PASS_WITH_NOTES", "FAIL_ESCALATE", "STOPPED", "EXPIRED", "UNKNOWN"}
)
SUCCESS = frozenset({"PASS", "PASS_WITH_NOTES"})
PROFILE_FIELDS = {
    "schema_version",
    "repository",
    "epic",
    "roadmap_ref",
    "policy_sha",
    "mandate_digest",
    "owner_identity",
    "catalog_digest",
    "limits",
}
LIMITS = {
    "max_hints_per_role": 3,
    "max_hint_bytes": 4096,
    "max_record_bytes": 65536,
    "max_records": 1024,
    "max_store_bytes": 8388608,
}


class LearningError(OrchestrationError):
    """Machine code only; never echo supplied data."""


def exact(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise LearningError("LEARNING_INVALID")
    return cast(dict[str, Any], value)


def integer(value: Any, maximum: int, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise LearningError("LEARNING_INVALID")
    return value


def identifier(value: Any) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value) is None
    ):
        raise LearningError("LEARNING_INVALID")
    return value


def hash_value(value: Any, *, size: int = 64, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is None
    ):
        raise LearningError("LEARNING_INVALID")


def seal(value: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in value.items() if k != "record_digest"}
    try:
        record_digest = digest(clean)
    except (TypeError, ValueError, RecursionError):
        raise LearningError("LEARNING_INVALID") from None
    return {**clean, "record_digest": record_digest}


def sealed(value: Any, fields: set[str]) -> dict[str, Any]:
    result = exact(value, fields | {"schema_version", "record_digest"})
    if result["schema_version"] != "1.0" or result != seal(result):
        raise LearningError("LEARNING_INVALID")
    return result


def text(value: Any) -> str:
    try:
        result = _safe_text(value)
    except LessonError:
        raise LearningError("LEARNING_UNSAFE_HINT") from None
    if re.search(
        r"(?:https?://|(?:\d{1,3}\.){3}\d{1,3}|(?:^|\s)/(?:home|tmp|etc)/)", result
    ):
        raise LearningError("LEARNING_UNSAFE_HINT")
    return result


def profile(raw: Any) -> dict[str, Any]:
    value = exact(raw, PROFILE_FIELDS)
    if (
        value["schema_version"] != "1.0"
        or not isinstance(value["repository"], str)
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*",
            value["repository"],
        )
        is None
    ):
        raise LearningError("LEARNING_PROFILE_INVALID")
    integer(value["epic"], 2147483647, 1)
    if (
        not isinstance(value["roadmap_ref"], str)
        or re.fullmatch(
            r"roadmap/" + str(value["epic"]) + r"-[a-z0-9-]+", value["roadmap_ref"]
        )
        is None
        or not isinstance(value["owner_identity"], str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}", value["owner_identity"])
        is None
    ):
        raise LearningError("LEARNING_PROFILE_INVALID")
    hash_value(value["policy_sha"], size=40)
    hash_value(value["catalog_digest"])
    mandate = value["mandate_digest"]
    if not isinstance(mandate, str) or not mandate.startswith("sha256:"):
        raise LearningError("LEARNING_PROFILE_INVALID")
    hash_value(mandate[7:])
    limits = exact(value["limits"], set(LIMITS))
    for key, maximum in LIMITS.items():
        integer(limits[key], maximum, 0 if key == "max_hints_per_role" else 1)
    return value


def environment(raw: Any) -> dict[str, Any]:
    value = exact(
        raw, {"os", "python_minor", "gate_profile", "backend_digest", "tool_digests"}
    )
    if (
        not isinstance(value["os"], str)
        or not isinstance(value["python_minor"], str)
        or value["os"] not in {"linux", "windows"}
        or value["python_minor"] not in {"3.11", "3.12"}
        or value["gate_profile"] != "repository-full"
    ):
        raise LearningError("LEARNING_ENVIRONMENT_INVALID")
    hash_value(value["backend_digest"], nullable=True)
    hashes = value["tool_digests"]
    if not isinstance(hashes, list) or len(hashes) > 8:
        raise LearningError("LEARNING_ENVIRONMENT_INVALID")
    for item in hashes:
        hash_value(item)
    if hashes != sorted(set(hashes)):
        raise LearningError("LEARNING_ENVIRONMENT_INVALID")
    return value


def check_identity(raw: Any) -> dict[str, Any]:
    value = exact(raw, {"gate_profile", "check_name", "argv_digest", "test_identity"})
    if (
        value["gate_profile"] != "repository-full"
        or not isinstance(value["check_name"], str)
        or value["check_name"] not in {"pytest", "ruff", "mypy", "pre-commit"}
    ):
        raise LearningError("LEARNING_CHECK_INVALID")
    hash_value(value["argv_digest"])
    node = value["test_identity"]
    if node is not None and (
        not isinstance(node, str)
        or len(node) > 256
        or re.fullmatch(
            r"tests/test_[A-Za-z0-9_]+\.py::test_[A-Za-z0-9_]+(?:\[[A-Za-z0-9_-]+\])?",
            node,
        )
        is None
    ):
        raise LearningError("LEARNING_CHECK_INVALID")
    return value


def catalog(raw: Any) -> dict[str, Any]:
    value = exact(raw, {"schema_version", "rules"})
    if (
        value["schema_version"] != "1.0"
        or not isinstance(value["rules"], list)
        or len(value["rules"]) > 128
    ):
        raise LearningError("LEARNING_CATALOG_INVALID")
    seen: set[str] = set()
    for raw_rule in value["rules"]:
        item = exact(
            raw_rule,
            {
                "rule_id",
                "role",
                "task_class",
                "problem_class",
                "environment",
                "symptom_code",
                "diagnostic_steps",
                "minimal_fix",
                "applicability",
                "predicates",
                "regression_checks",
            },
        )
        for key in ("rule_id", "task_class", "problem_class"):
            identifier(item[key])
        if (
            item["rule_id"] in seen
            or not isinstance(item["role"], str)
            or item["role"] not in {"planner", "executor", "controller"}
        ):
            raise LearningError("LEARNING_CATALOG_INVALID")
        seen.add(item["rule_id"])
        environment(item["environment"])
        code = item["symptom_code"]
        if (
            not isinstance(code, str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) is None
        ):
            raise LearningError("LEARNING_CATALOG_INVALID")
        steps = item["diagnostic_steps"]
        if not isinstance(steps, list) or len(steps) > 4:
            raise LearningError("LEARNING_CATALOG_INVALID")
        for step in steps:
            text(step)
        for key in ("minimal_fix", "applicability"):
            text(item[key])
        predicates = item["predicates"]
        if (
            not isinstance(predicates, list)
            or any(not isinstance(p, str) for p in predicates)
            or set(predicates) != PREDICATES
            or len(predicates) != len(PREDICATES)
        ):
            raise LearningError("LEARNING_CATALOG_INVALID")
        checks = item["regression_checks"]
        if not isinstance(checks, list) or not 1 <= len(checks) <= 4:
            raise LearningError("LEARNING_CATALOG_INVALID")
        for check in checks:
            check_identity(check)
        if len({digest(c) for c in checks}) != len(checks):
            raise LearningError("LEARNING_CATALOG_INVALID")
    return value


def measurement(raw: Any) -> dict[str, Any]:
    value = sealed(
        raw,
        {
            "measurement_id",
            "source_run_digest",
            "applicability_digest",
            "hint_set_digest",
            "outcome",
            "iterations",
            "calls",
            "duration_seconds",
        },
    )
    for key in ("measurement_id", "source_run_digest", "applicability_digest"):
        hash_value(value[key])
    hash_value(value["hint_set_digest"], nullable=True)
    if not isinstance(value["outcome"], str) or value["outcome"] not in OUTCOMES:
        raise LearningError("LEARNING_MEASUREMENT_INVALID")
    integer(value["iterations"], 3)
    calls = exact(value["calls"], set(ROLES))
    for role in ROLES:
        integer(calls[role], 3 if role in {"executor", "controller"} else 32)
    if value["iterations"] != calls["controller"]:
        raise LearningError("LEARNING_MEASUREMENT_INVALID")
    integer(value["duration_seconds"], 86400)
    return value


def compare(baseline: Any, hinted: Any) -> dict[str, Any]:
    hinted = measurement(hinted)
    baseline = measurement(baseline) if baseline is not None else None
    comparison: dict[str, Any] = {
        "status": "UNMEASURED",
        "outcome_regressed": None,
        "iteration_delta": None,
        "call_delta": None,
        "duration_delta_seconds": None,
    }
    if baseline is not None:
        if baseline["hint_set_digest"] is not None or hinted["hint_set_digest"] is None:
            raise LearningError("LEARNING_MEASUREMENT_INVALID")
        if baseline["applicability_digest"] != hinted["applicability_digest"]:
            comparison["status"] = "NO_MEASURED_COMPARISON"
        else:
            regressed = (
                baseline["outcome"] in SUCCESS and hinted["outcome"] not in SUCCESS
            )
            deltas = (
                hinted["iterations"] - baseline["iterations"],
                sum(hinted["calls"].values()) - sum(baseline["calls"].values()),
                hinted["duration_seconds"] - baseline["duration_seconds"],
            )
            role_increased = any(
                hinted["calls"][r] > baseline["calls"][r] for r in ROLES
            )
            if regressed or role_increased or any(d > 0 for d in deltas):
                status = "ROLLBACK_REQUIRED"
            elif (
                hinted["outcome"] in SUCCESS
                and all(d <= 0 for d in deltas)
                and any(d < 0 for d in deltas)
            ):
                status = "MEASURED_IMPROVEMENT"
            else:
                status = "NO_MEASURED_CHANGE"
            comparison = {
                "status": status,
                "outcome_regressed": regressed,
                "iteration_delta": deltas[0],
                "call_delta": deltas[1],
                "duration_delta_seconds": deltas[2],
            }
    value = {
        "schema_version": "1.0",
        "baseline_digest": digest(baseline) if baseline else None,
        "hinted_digest": digest(hinted),
        "comparison": comparison,
    }
    value["evaluation_id"] = digest(value)
    return seal(value)
