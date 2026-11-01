"""Pure neutral projection/refusals, never successful simulated FINAL sources."""
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_collector import (
    _planning_rows,
    _reference_membership,
    read_learning_packet_owned,
    read_preaggregate_learning_packet_owned,
)
from tools.agent_final_learning_origin_rows import RetainedRow
from tools.agent_final_learning_planning_coverage import PlanningCoverage
from tools.agent_final_learning_task_rows import _planning_identity
from tools.agent_final_learning_workflow_rows import WorkflowCallRow, WorkflowUseRow
from tools.agent_final_preparation_runtime import (
    HistoricalPlanningUse,
    PlanningAdmissionTaskFact,
)
from tools.agent_learning import LearningError
from tools.agent_learning_planning_sources import PlanningCallFact


def planning_use(
    hinted: bool = False, fact_digest: str = "f" * 64,
) -> HistoricalPlanningUse:
    refs = (("1" * 64, "2" * 64, "3" * 64, "4" * 64, "5" * 64, "6" * 64),)
    fact = PlanningCallFact(303, "planner", 1, "7" * 64, "8" * 64,
                            "9" * 64, "0" * 64 if hinted else None, "a" * 64, "b" * 64,
                            "c" * 64, "d" * 64, "e" * 64, fact_digest)
    return HistoricalPlanningUse(
        303, "planner", 1, "0" * 64, "a" * 64, "e" * 64, "8" * 64,
        "6" * 64 if hinted else None, ("1" * 64,) if hinted else (), (),
        refs if hinted else (), fact,
    )


def test_planning_mapping_uses_full_thirteen_field_fact_and_keeps_empty_use() -> None:
    rows = _planning_rows((planning_use(),))
    assert rows.calls[0].source_digest == "f" * 64
    assert rows.calls[0].invocation_key == "7" * 64
    assert rows.calls[0].native_invocation_digest is None
    assert rows.calls[0].binding_digest is None
    assert rows.uses[0].ordered_lessons == ()
    with pytest.raises(FrozenInstanceError):
        setattr(rows.calls[0], "source_digest", "0" * 64)


def test_planning_mapping_preserves_six_reference_order() -> None:
    rows = _planning_rows((planning_use(True),))
    assert rows.uses[0].ordered_lessons == planning_use(True).lesson_references()
    assert tuple(rows.uses[0].ordered_lessons[0]) == tuple(str(i) * 64
                                                        for i in range(1, 7))


def test_planning_duplicate_call_key_refuses() -> None:
    with pytest.raises(LearningError):
        _planning_rows((planning_use(), planning_use()))


def workflow_call() -> WorkflowCallRow:
    return WorkflowCallRow(303, "WORKFLOW", "executor", 1, "7" * 64,
                           "a" * 64, "b" * 64, "c" * 64, "d" * 64)


def workflow_retained(task: int = 301) -> tuple[RetainedRow, ...]:
    return tuple(RetainedRow("WORKFLOW_STORE", task, kind, key, value)
                 for kind, key, value in (
                     ("verification", "1" * 64, "2" * 64),
                     ("candidate", "9" * 64, "3" * 64),
                     ("observation", "a" * 64, "4" * 64),
                     ("observation", "b" * 64, "5" * 64)))


def test_reference_membership_preserves_cross_task_origin_and_multiplicity() -> None:
    refs = planning_use(True).lesson_references()
    retained = workflow_retained(301) + workflow_retained(302)
    _reference_membership((workflow_call(),),
                          (WorkflowUseRow("7" * 64, refs),), retained)
    assert len(retained) == 8


@pytest.mark.parametrize("fault", ["missing", "wrong-family", "wrong-lesson"])
def test_reference_membership_refuses_unproved_originals(fault: str) -> None:
    rows = list(workflow_retained())
    if fault == "missing":
        rows.pop()
    elif fault == "wrong-family":
        rows[0] = RetainedRow("PLANNING_STORE", None, "planning_verification",
                              "1" * 64, "2" * 64)
    else:
        rows[0] = RetainedRow("WORKFLOW_STORE", 301, "verification",
                              "0" * 64, "2" * 64)
    with pytest.raises(LearningError):
        _reference_membership((workflow_call(),),
                              (WorkflowUseRow("7" * 64,
                                              planning_use(True).lesson_references()),),
                              tuple(rows))


@pytest.mark.parametrize("fault", ["missing-use", "extra-use", "duplicate-call",
                                   "duplicate-use", "cross-family"])
def test_global_call_key_bijection_refuses(fault: str) -> None:
    calls: tuple[Any, ...] = (workflow_call(),)
    uses: tuple[WorkflowUseRow, ...] = (WorkflowUseRow("7" * 64, ()),)
    if fault == "missing-use":
        uses = ()
    elif fault == "extra-use":
        uses += (WorkflowUseRow("8" * 64, ()),)
    elif fault == "duplicate-call":
        calls += calls
    elif fault == "duplicate-use":
        uses += uses
    else:
        calls += _planning_rows((planning_use(),)).calls
    with pytest.raises(LearningError):
        _reference_membership(calls, uses, ())


@pytest.mark.parametrize("entry", [read_learning_packet_owned,
                                   read_preaggregate_learning_packet_owned])
@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_fixed_owned_entries_refuse(entry: Any, unowned: Any) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        entry(unowned)


def test_planning_reference_family_uses_shared_original_origin() -> None:
    planning = _planning_rows((planning_use(True),))
    retained = tuple(RetainedRow(
        "PLANNING_STORE", None, "planning_" + row.kind, row.key, row.record_digest,
    ) for row in workflow_retained())
    _reference_membership(planning.calls, planning.uses, retained)
    assert all(row.task is None for row in retained)


def test_planning_content_identity_detects_same_counter_different_full_fact() -> None:
    facts = (PlanningAdmissionTaskFact(303, 1, 0),)
    first = (PlanningCoverage(facts, (planning_use(),)), ())
    equal = (PlanningCoverage(facts, (planning_use(),)), ())
    changed = (PlanningCoverage(facts, (planning_use(fact_digest="e" * 64),)), ())
    assert _planning_identity(first) == _planning_identity(equal)
    assert _planning_identity(first) != _planning_identity(changed)
