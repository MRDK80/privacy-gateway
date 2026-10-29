"""Same-branch neutral original WORKFLOW coverage; no P/packet/default zeros."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.agent_final_learning_domain import (
    _FinalSourceReader,
    _PreAggregateFinalSourceReader,
    preaggregate_source_scope_owned,
)
from tools.agent_final_preparation_runtime import _CurrentFinalSources
from tools.agent_learning import LearningError


@dataclass(frozen=True, slots=True, repr=False)
class WorkflowTaskFact:
    task: int
    workflow_run_id: str
    executor_calls: int
    controller_calls: int


def read_workflow_task_facts_owned(session: Any) -> tuple[WorkflowTaskFact, ...]:
    """Original completed branch only; counters are not association lengths."""
    from tools.agent_final_learning_binding_source import (
        _read_workflow_coverage_snapshot,
    )

    pins = _CurrentFinalSources(session)
    reader = _FinalSourceReader(session)
    try:
        _, _, facts = _read_workflow_coverage_snapshot(reader, session)
        reader._source_tasks()
        pins.check()
        return facts
    finally:
        reader.close()


def read_preaggregate_workflow_task_facts_owned(
    session: Any,
) -> tuple[WorkflowTaskFact, ...]:
    """Fixed original-zero context, no reader injection or no-W producer."""
    from tools.agent_final_learning_binding_source import (
        _read_workflow_coverage_snapshot,
    )

    pins = _CurrentFinalSources(session)
    with preaggregate_source_scope_owned(session) as reader:
        if type(reader) is not _PreAggregateFinalSourceReader:
            raise LearningError("LEARNING_SOURCE_MISMATCH")
        assert isinstance(reader, _PreAggregateFinalSourceReader)
        _, _, facts = _read_workflow_coverage_snapshot(reader, session)
        reader._source_tasks()
        pins.check()
        return facts
