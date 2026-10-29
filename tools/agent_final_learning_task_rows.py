"""Full supported-U neutral task rows; no noW producer or complete packet."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_binding_source import (
    WorkflowCallFact,
    _read_workflow_coverage_snapshot,
)
from tools.agent_final_learning_domain import (
    _FinalPlanningReader,
    _FinalSourceReader,
    _PreAggregateFinalSourceReader,
    _PreAggregatePlanningReader,
)
from tools.agent_final_learning_no_planning import (
    NoPlanningTaskFact,
    _no_planning_partition,
    _read_planning_partition,
)
from tools.agent_final_learning_packet import _row
from tools.agent_final_learning_planning_coverage import (
    PlanningCoverage,
    _planning_coverage,
)
from tools.agent_final_learning_workflow_tasks import WorkflowTaskFact
from tools.agent_final_preparation_runtime import _CurrentFinalSources
from tools.agent_learning import LearningError


@dataclass(frozen=True, slots=True, repr=False)
class TaskRow:
    task: int
    workflow_run_id: str
    workflow_calls: int
    planning_attempts: int


def _require(condition: bool) -> None:
    if not condition:
        raise LearningError("LEARNING_SOURCE_MISMATCH")


def _count(value: int) -> None:
    # Original WORKFLOW repair bound2 and planning attempt bounds3, unchanged.
    _require(type(value) is int and 0 <= value <= 3)


def _project_task_rows(
    universe: tuple[int, ...],
    workflow: tuple[WorkflowTaskFact, ...],
    calls: tuple[WorkflowCallFact, ...],
    planning: PlanningCoverage,
    no_planning: tuple[NoPlanningTaskFact, ...],
) -> tuple[TaskRow, ...]:
    """Pure relation only; callers cannot turn neutral constructors into proof."""
    _require(
        bool(universe) and all(type(task) is int and task > 0 for task in universe)
    )
    _require(len(set(universe)) == len(universe))
    _require(all(type(fact.task) is int and fact.task > 0 for fact in workflow))
    workflows = {fact.task: fact for fact in workflow}
    _require(len(workflows) == len(workflow) and set(workflows) == set(universe))
    expected: set[tuple[int, str, int]] = set()
    for fact in workflow:
        _require(type(fact.workflow_run_id) is str)
        _count(fact.executor_calls)
        _count(fact.controller_calls)
        for role, count in (
            ("executor", fact.executor_calls),
            ("controller", fact.controller_calls),
        ):
            expected.update(
                (fact.task, role, ordinal) for ordinal in range(1, count + 1)
            )
    actual = [(fact.association.task, fact.role, fact.ordinal) for fact in calls]
    _require(len(set(actual)) == len(actual) and set(actual) == expected)
    for call in calls:
        _require(call.association.task in workflows)
        _require(call.run_id == workflows[call.association.task].workflow_run_id)
    for admission in planning.admissions:
        _require(type(admission.task) is int and admission.task > 0)
        _count(admission.planner_attempts)
        _count(admission.planning_review_attempts)
    _planning_coverage(planning.admissions, planning.calls)
    actual_tasks = tuple(fact.task for fact in planning.admissions)
    _no_planning_partition(universe, actual_tasks, no_planning)
    admissions = {fact.task: fact for fact in planning.admissions}
    for absent in no_planning:
        _require(type(absent.task) is int and absent.task in workflows)
        _require(absent.workflow_run_id == workflows[absent.task].workflow_run_id)
    rows = []
    for task in sorted(universe):
        fact = workflows[task]
        if task in admissions:
            original = admissions[task]
            attempts = original.planner_attempts + original.planning_review_attempts
        else:
            # Only the exact verified partition above establishes this branch.
            _require(task in {value.task for value in no_planning})
            attempts = 0
        row = TaskRow(
            task,
            fact.workflow_run_id,
            fact.executor_calls + fact.controller_calls,
            attempts,
        )
        _row("tasks", asdict(row))  # Closed shape only, never encode a partial packet.
        rows.append(row)
    return tuple(rows)


def _planning_identity(
    partition: tuple[PlanningCoverage, tuple[NoPlanningTaskFact, ...]],
) -> tuple[Any, ...]:
    coverage, no_planning = partition
    # HistoricalPlanningUse deliberately has object identity, not value __eq__.
    return (
        coverage.admissions,
        tuple(
            (value.facts(), value.lesson_references(), value.call_fact())
            for value in coverage.calls
        ),
        no_planning,
    )


def _read_task_rows(
    reader: _FinalPlanningReader | _PreAggregatePlanningReader, session: Any
) -> tuple[TaskRow, ...]:
    if (
        type(reader) not in {_FinalPlanningReader, _PreAggregatePlanningReader}
        or reader.session is not session
    ):
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    pins = _CurrentFinalSources(session)
    source = reader.reader
    if type(source) not in {_FinalSourceReader, _PreAggregateFinalSourceReader}:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    assert isinstance(source, (_FinalSourceReader, _PreAggregateFinalSourceReader))
    universe = source._source_tasks()
    original_domain = reader._domain_with_task_facts()
    workflow = _read_workflow_coverage_snapshot(source, session)
    planning = _read_planning_partition(reader, session)
    rows = _project_task_rows(universe, workflow[2], workflow[0], *planning)
    if _read_workflow_coverage_snapshot(source, session) != workflow:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    if _planning_identity(
        _read_planning_partition(reader, session)
    ) != _planning_identity(planning):
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    if (
        reader._domain_with_task_facts() != original_domain
        or source._source_tasks() != universe
    ):
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    reader._guard()
    pins.check()
    return rows


def read_task_rows_owned(session: Any) -> tuple[TaskRow, ...]:
    reader = _FinalPlanningReader(session)
    try:
        return _read_task_rows(reader, session)
    finally:
        reader.reader.close()


def read_preaggregate_task_rows_owned(session: Any) -> tuple[TaskRow, ...]:
    reader = _PreAggregatePlanningReader(session)
    try:
        return _read_task_rows(reader, session)
    finally:
        reader.reader.close()
