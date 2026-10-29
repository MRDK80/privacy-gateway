"""Neutral W counters/run identity, never successful original-source authority."""

from dataclasses import FrozenInstanceError, fields
from typing import Any

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_workflow_tasks import (
    WorkflowTaskFact,
    read_preaggregate_workflow_task_facts_owned,
    read_workflow_task_facts_owned,
)


@pytest.mark.parametrize(
    "entry",
    [read_workflow_task_facts_owned, read_preaggregate_workflow_task_facts_owned],
)
@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_task_fact_entry_refuses_unowned_session(entry: Any, unowned: Any) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        entry(unowned)


@pytest.mark.parametrize("counts", [(0, 0), (2, 1)])
def test_neutral_fact_preserves_actual_run_and_each_counter(
    counts: tuple[int, int],
) -> None:
    # Scalars are synthetic representation values, not a successful Session.
    run_id = "a" * 32
    value = WorkflowTaskFact(301, run_id, *counts)
    assert [field.name for field in fields(value)] == [
        "task", "workflow_run_id", "executor_calls", "controller_calls"
    ]
    assert value.task == 301 and value.workflow_run_id == run_id
    assert (value.executor_calls, value.controller_calls) == counts
    assert value.executor_calls + value.controller_calls == sum(counts)
    with pytest.raises(FrozenInstanceError):
        setattr(value, "workflow_run_id", None)
    assert run_id not in repr(value)
    assert not hasattr(value, "planning_attempts") and not hasattr(value, "ready")
