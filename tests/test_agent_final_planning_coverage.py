"""Actual-admission neutral coverage/refusals, never a successful source mock."""

from dataclasses import FrozenInstanceError, fields
from typing import Any

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_planning_coverage import (
    _planning_coverage,
    read_planning_coverage_owned,
    read_preaggregate_planning_coverage_owned,
)
from tools.agent_final_preparation_runtime import (
    HistoricalPlanningUse,
    PlanningAdmissionTaskFact,
    _PlanningAdmissionSnapshot,
)
from tools.agent_learning import LearningError
from tools.agent_learning_planning_sources import PlanningCallFact


@pytest.mark.parametrize(
    "entry", [read_planning_coverage_owned, read_preaggregate_planning_coverage_owned]
)
@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_actual_admission_entry_refuses_unowned_session(
    entry: Any, unowned: Any
) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        entry(unowned)


def call(role: str, ordinal: int) -> HistoricalPlanningUse:
    # No original successful Session/source is fabricated by these neutral values.
    fact = PlanningCallFact(
        301, role, ordinal, str(ordinal) * 64, "1" * 64, "2" * 64, None,
        "3" * 64, "4" * 64, "5" * 64, "6" * 64, "7" * 64, "8" * 64,
    )
    return HistoricalPlanningUse(
        301, role, ordinal, "1" * 64, "2" * 64, "3" * 64, "4" * 64,
        None, (), (), (), fact,
    )


def test_existing_zero_admission_is_distinct_from_absent_admission_list() -> None:
    fact = PlanningAdmissionTaskFact(301, 0, 0)
    coverage = _planning_coverage((fact,), ())
    assert coverage.admissions == (fact,) and coverage.calls == ()
    assert _planning_coverage((), ()).admissions == ()
    assert [f.name for f in fields(fact)] == [
        "task", "planner_attempts", "planning_review_attempts"
    ]
    with pytest.raises(FrozenInstanceError):
        setattr(fact, "planner_attempts", 1)
    assert not hasattr(coverage, "ready") and not hasattr(coverage, "tasks")


def test_all_consumed_ordinals_remain_even_without_applied_lessons_or_pair() -> None:
    uses = (call("planner", 1), call("planner", 2), call("planning_review", 1))
    coverage = _planning_coverage((PlanningAdmissionTaskFact(301, 2, 1),), uses)
    assert coverage.calls == uses
    assert all(value.lesson_references() == () for value in coverage.calls)
    assert all(len(value.facts()) == 10 for value in coverage.calls)
    assert all(value.call_fact().relevance_digest is None for value in coverage.calls)


@pytest.mark.parametrize("bad", ["missing", "duplicate", "extra", "wrong-role"])
def test_exact_original_counter_ordinal_obligation_refuses_mismatch(bad: str) -> None:
    uses: tuple[HistoricalPlanningUse, ...] = (call("planner", 1),)
    if bad == "missing":
        uses = ()
    elif bad == "duplicate":
        uses = (call("planner", 1), call("planner", 1))
    elif bad == "extra":
        uses = (call("planner", 1), call("planner", 2))
    else:
        uses = (call("planning_review", 1),)
    with pytest.raises(LearningError, match="LEARNING_SOURCE_MISMATCH"):
        _planning_coverage((PlanningAdmissionTaskFact(301, 1, 0),), uses)


def test_original_snapshot_four_fields_and_equality_unchanged() -> None:
    value = _PlanningAdmissionSnapshot("a" * 64, (), (301,), ())
    assert [f.name for f in fields(value)] == [
        "fingerprint", "domain", "tasks", "pending"
    ]
    assert value == _PlanningAdmissionSnapshot("a" * 64, (), (301,), ())
    assert value != _PlanningAdmissionSnapshot("b" * 64, (), (301,), ())
