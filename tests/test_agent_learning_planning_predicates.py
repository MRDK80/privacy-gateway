"""The fixed current relevance predicate cannot relax historical proof equality."""

from __future__ import annotations

from typing import Any

import pytest
from tools.agent_learning_planning_predicates import check_identity, current_equivalent


def facts() -> dict[str, Any]:
    return {
        "criteria_digest": "a" * 64,
        "criterion_count": 2,
        "grant_digest": "b" * 64,
        "profile_digest": "sha256:" + "c" * 64,
        "task_class": "synthetic",
        "policy_sha": "d" * 40,
        "initial_head": "e" * 40,
        "initial_tree": "f" * 40,
        "initial_base": "1" * 40,
        "initial_default": "2" * 40,
    }


def test_roadmap_advancement_changes_proof_but_preserves_current_relevance() -> None:
    original = facts()
    current = {**original, "initial_head": "3" * 40, "initial_base": "4" * 40}
    assert current_equivalent(current, original)
    assert check_identity("planner", current) != check_identity("planner", original)
    assert check_identity("planning_review", original) != check_identity(
        "planner", original
    )


@pytest.mark.parametrize(
    "key",
    [
        "criteria_digest",
        "criterion_count",
        "grant_digest",
        "profile_digest",
        "task_class",
        "policy_sha",
    ],
)
def test_approved_input_drift_never_matches(key: str) -> None:
    original = facts()
    changed = {**original, key: 3 if key == "criterion_count" else "changed"}
    assert not current_equivalent(changed, original)
