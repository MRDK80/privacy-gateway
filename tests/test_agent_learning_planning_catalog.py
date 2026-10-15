"""Planning predicates cannot claim workflow gate-only evidence."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from tools import agent_learning as core
from tools import agent_learning_planning_catalog as planning
from tools.agent_continuation import digest

from tests.test_agent_learning import rule


def sample() -> dict[str, Any]:
    value = rule()
    value.pop("regression_checks")
    value.update(
        role="planner",
        source_kind="PLANNING_VALIDATION",
        validator="PROPOSAL_V1",
        failure_code="PLANNING_CRITERIA_INCOMPLETE",
        check_identity_kind="PINNED_VALIDATOR_INPUT",
        current_predicate="CURRENT_APPROVED_CRITERIA_AND_GRANT",
    )
    value["environment"]["backend_digest"] = None
    value["predicates"] = sorted(planning.PREDICATES)
    value["symptom_code"] = value["failure_code"]
    return {"schema_version": "1.0", "rules": [value]}


def test_closed_planning_catalog_and_composite_owner_binding() -> None:
    value = sample()
    assert planning.catalog(value) == value
    empty = {"schema_version": "1.0", "rules": []}
    assert planning.catalog_digest(empty, value) == digest(
        {
            "workflow_catalog_digest": digest(empty),
            "planning_catalog_digest": digest(value),
        }
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("role", "executor"),
        ("source_kind", "WORKFLOW"),
        ("validator", "REVIEW_V1"),
        ("failure_code", "MODEL_TEXT"),
        ("current_predicate", "INFER_FROM_ISSUE"),
        ("check_identity_kind", "MODEL_TEST_NAME"),
        ("regression_checks", []),
    ],
)
def test_unapproved_catalog_variants_are_rejected(key: str, value: Any) -> None:
    item = sample()
    item["rules"][0][key] = value
    with pytest.raises(core.LearningError):
        planning.catalog(item)


def test_review_class_is_scoped_to_planning_environment() -> None:
    item = sample()
    item["rules"][0].update(
        role="controller",
        validator="REVIEW_V1",
        failure_code="PLANNING_REVIEW_INVALID",
        symptom_code="PLANNING_REVIEW_INVALID",
    )
    assert planning.catalog(item) == item
    item["rules"][0]["environment"]["backend_digest"] = "a" * 64
    with pytest.raises(core.LearningError):
        planning.catalog(item)


def test_duplicate_rules_and_gate_only_predicates_are_rejected() -> None:
    item = sample()
    item["rules"].append(copy.deepcopy(item["rules"][0]))
    with pytest.raises(core.LearningError):
        planning.catalog(item)
    item = sample()
    item["rules"][0]["predicates"][0] = "RETAINED_REGRESSION_CHECK_PASS"
    with pytest.raises(core.LearningError):
        planning.catalog(item)
