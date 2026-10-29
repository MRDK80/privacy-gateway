"""Neutral noP rule/partition/refusals, never a successful source mock."""

from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_no_planning import (
    NoPlanningTaskFact,
    _no_planning_partition,
    read_no_planning_tasks_owned,
    read_preaggregate_no_planning_tasks_owned,
)
from tools.agent_learning import LearningError
from tools.agent_learning_measure import _seed_planning_precedent


@pytest.mark.parametrize(
    "entry", [read_no_planning_tasks_owned, read_preaggregate_no_planning_tasks_owned]
)
@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_noP_entry_refuses_unowned_session(entry: Any, unowned: Any) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        entry(unowned)


def test_exact_root_seed_and_initial_handover_rule() -> None:
    assert _seed_planning_precedent(
        {"task": 301}, {"initial_handover_digest": "a"},
        {"approved_handover_digest": "a"}, 301,
    )


@pytest.mark.parametrize("bad", ["wrong-seed", "wrong-handover"])
def test_root_seed_rule_refuses_nonmatching_originals(bad: str) -> None:
    seed = {"task": 302 if bad == "wrong-seed" else 301}
    config = {"approved_handover_digest": "b" if bad == "wrong-handover" else "a"}
    assert not _seed_planning_precedent(
        seed, {"initial_handover_digest": "a"}, config, 301,
    )


def test_config_task_cannot_substitute_for_original_root_seed_task() -> None:
    assert not _seed_planning_precedent(
        {"task": 302}, {"initial_handover_digest": "a"},
        {"task": 301, "approved_handover_digest": "a"}, 301,
    )


def test_neutral_partition_does_not_override_actual_admission() -> None:
    fact = NoPlanningTaskFact(302, "a" * 32)
    assert _no_planning_partition((301, 302), (301,), (fact,)) == (fact,)
    with pytest.raises(FrozenInstanceError):
        setattr(fact, "workflow_run_id", None)
    assert "a" * 32 not in repr(fact) and not hasattr(fact, "ready")


@pytest.mark.parametrize("bad", ["missing", "overlap", "duplicate", "unknown"])
def test_neutral_partition_refuses_missing_or_overlapping_members(bad: str) -> None:
    facts: tuple[NoPlanningTaskFact, ...] = (NoPlanningTaskFact(302, "a" * 32),)
    if bad == "missing":
        facts = ()
    elif bad == "overlap":
        facts = (NoPlanningTaskFact(301, "a" * 32),)
    elif bad == "duplicate":
        facts = (facts[0], facts[0])
    else:
        facts = (NoPlanningTaskFact(303, "a" * 32),)
    with pytest.raises(LearningError, match="LEARNING_SOURCE_MISMATCH"):
        _no_planning_partition((301, 302), (301,), facts)
