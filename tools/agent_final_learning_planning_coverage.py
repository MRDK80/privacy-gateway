"""Actual admission counts and all consumed calls; no all-U/noP proof."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.agent_final_preparation_runtime import (
    HistoricalPlanningUse,
    PlanningAdmissionTaskFact,
)
from tools.agent_learning import LearningError


@dataclass(frozen=True, slots=True, repr=False)
class PlanningCoverage:
    admissions: tuple[PlanningAdmissionTaskFact, ...]
    calls: tuple[HistoricalPlanningUse, ...]


def _planning_coverage(
    admissions: tuple[PlanningAdmissionTaskFact, ...],
    calls: tuple[HistoricalPlanningUse, ...],
) -> PlanningCoverage:
    """Pure ordinal consistency; construction grants no source authority."""
    tasks = [fact.task for fact in admissions]
    expected = {
        (fact.task, role, ordinal)
        for fact in admissions
        for role, count in (
            ("planner", fact.planner_attempts),
            ("planning_review", fact.planning_review_attempts),
        )
        for ordinal in range(1, count + 1)
    }
    actual = [value.facts()[:3] for value in calls]
    if (
        len(set(tasks)) != len(tasks)
        or len(set(actual)) != len(actual)
        or set(actual) != expected
    ):
        raise LearningError("LEARNING_SOURCE_MISMATCH")
    return PlanningCoverage(admissions, calls)


def read_planning_coverage_owned(session: Any) -> PlanningCoverage:
    from tools.agent_final_learning_domain import (
        _FinalPlanningReader,
        _read_planning_coverage,
    )

    reader = _FinalPlanningReader(session)
    try:
        return _read_planning_coverage(reader, session)
    finally:
        reader.reader.close()


def read_preaggregate_planning_coverage_owned(session: Any) -> PlanningCoverage:
    from tools.agent_final_learning_domain import (
        _PreAggregatePlanningReader,
        _read_planning_coverage,
    )

    reader = _PreAggregatePlanningReader(session)
    try:
        return _read_planning_coverage(reader, session)
    finally:
        reader.reader.close()
