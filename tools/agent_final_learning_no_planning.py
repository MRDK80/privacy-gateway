"""Fixed positive noP source route; absence is never sufficient proof."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools import agent_epic_planning as admission
from tools.agent_final_learning_planning_coverage import PlanningCoverage
from tools.agent_final_preparation_runtime import HistoricalPlanningReader
from tools.agent_learning import LearningError
from tools.agent_learning_measure import _observed_with_workflow_fact


@dataclass(frozen=True, slots=True, repr=False)
class NoPlanningTaskFact:
    task: int
    workflow_run_id: str


def _no_planning_partition(
    universe: tuple[int, ...],
    actual: tuple[int, ...],
    facts: tuple[NoPlanningTaskFact, ...],
) -> tuple[NoPlanningTaskFact, ...]:
    """Pure relation only; owned entry obtains original sources itself."""
    tasks = tuple(fact.task for fact in facts)
    if (
        len(set(universe)) != len(universe)
        or len(set(actual)) != len(actual)
        or len(set(tasks)) != len(tasks)
        or set(actual) & set(tasks)
        or set(actual) | set(tasks) != set(universe)
    ):
        raise LearningError("LEARNING_SOURCE_MISMATCH")
    return tuple(sorted(facts, key=lambda fact: fact.task))


def _read_planning_partition(
    reader: HistoricalPlanningReader, session: Any
) -> tuple[PlanningCoverage, tuple[NoPlanningTaskFact, ...]]:
    from tools.agent_final_learning_domain import (
        _FinalPlanningReader,
        _PreAggregatePlanningReader,
        _read_planning_coverage,
    )

    if type(reader) not in {_FinalPlanningReader, _PreAggregatePlanningReader}:
        raise LearningError("LEARNING_SOURCE_MISMATCH")
    assert isinstance(reader, (_FinalPlanningReader, _PreAggregatePlanningReader))
    if reader.session is not session:
        raise LearningError("LEARNING_SOURCE_MISMATCH")
    source = reader.reader
    universe = source._source_tasks()
    baseline, _ = reader._domain_with_task_facts()
    coverage = _read_planning_coverage(reader, session)
    actual = tuple(fact.task for fact in coverage.admissions)
    if actual != baseline.tasks:
        raise LearningError("LEARNING_SOURCE_MISMATCH")
    proofs: dict[int, Any] = {}
    facts = []
    for task in universe:
        reader._guard()
        if task in actual:
            continue
        container = admission.Admission(source.seed, source.root)._storage(task)
        if container.exists() or container.is_symlink():
            raise LearningError("LEARNING_SOURCE_MISMATCH")
        proof = _observed_with_workflow_fact(source, session, task)
        (measurement, _), workflow = proof
        if measurement["calls"]["planner"] or measurement["calls"]["planning_review"]:
            raise LearningError("LEARNING_SOURCE_MISMATCH")
        proofs[task] = proof
        facts.append(NoPlanningTaskFact(task, workflow.state["run_id"]))
        reader._guard()
    result = _no_planning_partition(universe, actual, tuple(facts))
    for task, original in proofs.items():
        reader._guard()
        container = admission.Admission(source.seed, source.root)._storage(task)
        if container.exists() or container.is_symlink():
            raise LearningError("LEARNING_SOURCE_MISMATCH")
        if _observed_with_workflow_fact(source, session, task) != original:
            raise LearningError("LEARNING_SOURCE_MISMATCH")
    final, _ = reader._domain_with_task_facts()
    if final != baseline or source._source_tasks() != universe:
        raise LearningError("LEARNING_SOURCE_MISMATCH")
    reader._guard()
    return coverage, result


def _read_no_planning_tasks(
    reader: HistoricalPlanningReader, session: Any
) -> tuple[NoPlanningTaskFact, ...]:
    """Preserve the legacy return API from the same full source branch."""
    return _read_planning_partition(reader, session)[1]


def read_no_planning_tasks_owned(session: Any) -> tuple[NoPlanningTaskFact, ...]:
    from tools.agent_final_learning_domain import _FinalPlanningReader

    reader = _FinalPlanningReader(session)
    try:
        return _read_no_planning_tasks(reader, session)
    finally:
        reader.reader.close()


def read_preaggregate_no_planning_tasks_owned(
    session: Any,
) -> tuple[NoPlanningTaskFact, ...]:
    from tools.agent_final_learning_domain import _PreAggregatePlanningReader

    reader = _PreAggregatePlanningReader(session)
    try:
        return _read_no_planning_tasks(reader, session)
    finally:
        reader.reader.close()
