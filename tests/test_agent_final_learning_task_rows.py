"""Neutral task relations and unowned refusals; no fake source-success fixture."""

from dataclasses import FrozenInstanceError, fields, replace
from typing import Any, cast

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_binding_source import (
    WorkflowAssociation,
    WorkflowCallFact,
)
from tools.agent_final_learning_no_planning import NoPlanningTaskFact
from tools.agent_final_learning_planning_coverage import PlanningCoverage
from tools.agent_final_learning_task_rows import (
    _planning_identity,
    _project_task_rows,
    read_preaggregate_task_rows_owned,
    read_task_rows_owned,
)
from tools.agent_final_learning_workflow_tasks import WorkflowTaskFact
from tools.agent_final_preparation_runtime import (
    HistoricalPlanningUse,
    PlanningAdmissionTaskFact,
)
from tools.agent_learning import LearningError
from tools.agent_learning_planning_sources import PlanningCallFact


@pytest.mark.parametrize(
    "entry", [read_task_rows_owned, read_preaggregate_task_rows_owned]
)
@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_task_rows_refuse_unowned_session(entry: Any, unowned: Any) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        entry(unowned)


def workflow_call(task: int, role: str, ordinal: int) -> WorkflowCallFact:
    association = WorkflowAssociation(task, "a" * 64, "b" * 64, "c" * 64, "d" * 64, ())
    return WorkflowCallFact(association, "e" * 32, role, ordinal, "f" * 64)


def planning_call(role: str, ordinal: int) -> HistoricalPlanningUse:
    fact = PlanningCallFact(
        301,
        role,
        ordinal,
        "0" * 64,
        "1" * 64,
        "2" * 64,
        None,
        "3" * 64,
        "4" * 64,
        "5" * 64,
        "6" * 64,
        "7" * 64,
        "8" * 64,
    )
    return HistoricalPlanningUse(
        301,
        role,
        ordinal,
        "1" * 64,
        "2" * 64,
        "3" * 64,
        "4" * 64,
        None,
        (),
        (),
        (),
        fact,
    )


def values() -> tuple[Any, ...]:
    # These constructors are representation inputs, never original-source proof.
    workflow = (
        WorkflowTaskFact(301, "a" * 32, 1, 1),
        WorkflowTaskFact(302, "b" * 32, 0, 0),
        WorkflowTaskFact(303, "c" * 32, 1, 0),
    )
    calls = (
        replace(workflow_call(301, "executor", 1), run_id="a" * 32),
        replace(workflow_call(301, "controller", 1), run_id="a" * 32),
        replace(workflow_call(303, "executor", 1), run_id="c" * 32),
    )
    planning = PlanningCoverage(
        (PlanningAdmissionTaskFact(301, 2, 1),),
        (
            planning_call("planner", 1),
            planning_call("planner", 2),
            planning_call("planning_review", 1),
        ),
    )
    no_planning = (NoPlanningTaskFact(302, "b" * 32), NoPlanningTaskFact(303, "c" * 32))
    return (303, 301, 302), workflow, calls, planning, no_planning


def test_full_universe_counts_both_roles_and_preserves_real_zero_call_run() -> None:
    rows = _project_task_rows(*values())
    assert [(v.task, v.workflow_calls, v.planning_attempts) for v in rows] == [
        (301, 2, 3),
        (302, 0, 0),
        (303, 1, 0),
    ]
    assert rows[1].workflow_run_id == "b" * 32
    assert [f.name for f in fields(rows[0])] == [
        "task",
        "workflow_run_id",
        "workflow_calls",
        "planning_attempts",
    ]
    with pytest.raises(FrozenInstanceError):
        setattr(rows[0], "planning_attempts", 0)
    assert not hasattr(rows[0], "ready") and not hasattr(rows[0], "source_digest")


def test_actual_zero_admission_wins_without_no_planning_default() -> None:
    universe, workflow, calls, _, no_planning = values()
    planning = PlanningCoverage((PlanningAdmissionTaskFact(301, 0, 0),), ())
    rows = _project_task_rows(universe, workflow, calls, planning, no_planning)
    assert rows[0].task == 301 and rows[0].planning_attempts == 0
    assert all(f.task != 301 for f in no_planning)


@pytest.mark.parametrize(
    "fault",
    [
        "missing-w",
        "duplicate-w",
        "unknown-w",
        "missing-partition",
        "overlap",
        "duplicate-nop",
        "unknown-nop",
        "nop-run",
        "missing-w-call",
        "duplicate-w-call",
        "wrong-w-role",
        "missing-p-call",
        "duplicate-p-call",
        "extra-p-call",
        "null-w",
        "w-call-run",
    ],
)
def test_missing_or_conflicting_original_relations_never_default_to_zero(
    fault: str,
) -> None:
    universe, workflow, calls, planning, no_planning = values()
    if fault == "missing-w":
        workflow = workflow[:-1]
    elif fault == "duplicate-w":
        workflow += (workflow[0],)
    elif fault == "unknown-w":
        workflow += (WorkflowTaskFact(304, "d" * 32, 0, 0),)
    elif fault == "missing-partition":
        no_planning = no_planning[:-1]
    elif fault == "overlap":
        no_planning += (NoPlanningTaskFact(301, "a" * 32),)
    elif fault == "duplicate-nop":
        no_planning += (no_planning[0],)
    elif fault == "unknown-nop":
        no_planning += (NoPlanningTaskFact(304, "d" * 32),)
    elif fault == "nop-run":
        no_planning = (NoPlanningTaskFact(302, "d" * 32), no_planning[1])
    elif fault == "missing-w-call":
        calls = calls[:-1]
    elif fault == "duplicate-w-call":
        calls += (calls[0],)
    elif fault == "wrong-w-role":
        calls = (replace(calls[0], role="planner"), *calls[1:])
    elif fault == "missing-p-call":
        planning = PlanningCoverage(planning.admissions, planning.calls[:-1])
    elif fault == "duplicate-p-call":
        planning = PlanningCoverage(
            planning.admissions, (*planning.calls, planning.calls[0])
        )
    elif fault == "extra-p-call":
        planning = PlanningCoverage(
            planning.admissions, (*planning.calls, planning_call("planner", 3))
        )
    elif fault == "null-w":
        workflow = (
            workflow[0],
            WorkflowTaskFact(302, cast(str, None), 0, 0),
            workflow[2],
        )
    else:
        calls = (replace(calls[0], run_id="d" * 32), *calls[1:])
    with pytest.raises(LearningError, match="LEARNING_SOURCE_MISMATCH"):
        _project_task_rows(universe, workflow, calls, planning, no_planning)


def test_replayed_planning_contents_match_without_object_identity() -> None:
    first = values()
    second = values()
    assert first[3].calls[0] is not second[3].calls[0]
    assert _planning_identity((first[3], first[4])) == _planning_identity(
        (second[3], second[4])
    )
    missing = PlanningCoverage(second[3].admissions, second[3].calls[:-1])
    assert _planning_identity((first[3], first[4])) != _planning_identity(
        (missing, second[4])
    )
