"""Pinned planning-validator rules and separately approved composite binding."""

from __future__ import annotations

from typing import Any

from tools import agent_learning as c
from tools import agent_learning_planning_records as sources
from tools.agent_continuation import digest
from tools.agent_learning_records import code

PREDICATES = frozenset(
    {
        "FULL_GATE_PASS",
        "INDEPENDENT_CONTROLLER_PASS",
        "EXACT_PR_CI_PASS",
        "EXACT_POST_CI_PASS",
        "APPROVED_DEMO_PASS",
        "PLANNING_VALIDATION_FAIL_PASS",
    }
)
RULE_FIELDS = {
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
    "source_kind",
    "validator",
    "failure_code",
    "check_identity_kind",
    "current_predicate",
}


def catalog(raw: Any) -> dict[str, Any]:
    value = c.exact(raw, {"schema_version", "rules"})
    rules = value["rules"]
    if (
        value["schema_version"] != "1.0"
        or not isinstance(rules, list)
        or len(rules) > 128
    ):
        raise c.LearningError("LEARNING_CATALOG_INVALID")
    seen: set[str] = set()
    for raw_rule in rules:
        item = c.exact(raw_rule, RULE_FIELDS)
        for key in ("rule_id", "task_class", "problem_class"):
            c.identifier(item[key])
        if item["rule_id"] in seen:
            raise c.LearningError("LEARNING_CATALOG_INVALID")
        seen.add(item["rule_id"])
        role = item["role"]
        if not isinstance(role, str) or role not in {"planner", "controller"}:
            raise c.LearningError("LEARNING_CATALOG_INVALID")
        validator = "PROPOSAL_V1" if role == "planner" else "REVIEW_V1"
        code(item["failure_code"])
        code(item["symptom_code"])
        if (
            item["source_kind"] != "PLANNING_VALIDATION"
            or item["validator"] != validator
            or item["failure_code"] not in sources.FAILURE_CODES[validator]
            or item["symptom_code"] != item["failure_code"]
            or item["check_identity_kind"] != "PINNED_VALIDATOR_INPUT"
            or item["current_predicate"] != sources.CURRENT_PREDICATE
        ):
            raise c.LearningError("LEARNING_CATALOG_INVALID")
        if c.environment(item["environment"])["backend_digest"] is not None:
            raise c.LearningError("LEARNING_CATALOG_INVALID")
        predicates = item["predicates"]
        if (
            not isinstance(predicates, list)
            or any(not isinstance(p, str) for p in predicates)
            or len(predicates) != len(PREDICATES)
            or set(predicates) != PREDICATES
        ):
            raise c.LearningError("LEARNING_CATALOG_INVALID")
        steps = item["diagnostic_steps"]
        if not isinstance(steps, list) or len(steps) > 4:
            raise c.LearningError("LEARNING_CATALOG_INVALID")
        for text in (*steps, item["minimal_fix"], item["applicability"]):
            c.text(text)
    return value


def catalog_digest(workflow: Any, planning: Any) -> str:
    return digest(
        {
            "workflow_catalog_digest": digest(c.catalog(workflow)),
            "planning_catalog_digest": digest(catalog(planning)),
        }
    )
