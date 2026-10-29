"""Owned-entry refusals and neutral row consistency, not fake source success."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_binding_source import (
    WorkflowAssociation,
    WorkflowCallFact,
)
from tools.agent_final_learning_workflow_rows import (
    WorkflowRows,
    _project_workflow_rows,
    read_preaggregate_workflow_rows_owned,
    read_workflow_rows_owned,
)
from tools.agent_learning import LearningError


@pytest.mark.parametrize(
    "entry", [read_workflow_rows_owned, read_preaggregate_workflow_rows_owned]
)
@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_fixed_row_entry_refuses_unowned_session(
    entry: Callable[[Any], WorkflowRows],
    unowned: Any,
) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        entry(unowned)


def fact(task: int, key: str, references: tuple[Any, ...] = ()) -> WorkflowCallFact:
    # Pure neutral representation only: this constructor proves no provenance.
    association = WorkflowAssociation(
        task, key, "c" * 64, "d" * 64, "e" * 64, references
    )
    return WorkflowCallFact(association, "f" * 32, "executor", 1, "0" * 64)


@pytest.mark.parametrize("hinted", [False, True])
def test_rows_preserve_zero_or_original_lesson_order_and_bijection(
    hinted: bool,
) -> None:
    references = (("a" * 64,) * 6, ("1" * 64,) * 6) if hinted else ()
    rows = _project_workflow_rows(
        (fact(303, "b" * 64, references), fact(301, "a" * 64))
    )
    assert [row.task for row in rows.calls] == [301, 303]
    assert [row.call_key for row in rows.uses] == [
        row.invocation_key for row in rows.calls
    ]
    assert rows.uses[1].ordered_lessons == references
    assert (
        rows.calls[1].family == "WORKFLOW" and rows.calls[1].metadata_digest == "0" * 64
    )
    with pytest.raises(FrozenInstanceError):
        setattr(rows.calls[0], "ordinal", 2)
    assert not hasattr(rows, "source_digest") and not hasattr(rows, "ready")


def test_duplicate_call_key_refuses_instead_of_deduplicating() -> None:
    with pytest.raises(LearningError, match="LEARNING_SOURCE_MISMATCH"):
        _project_workflow_rows((fact(301, "a" * 64), fact(303, "a" * 64)))
