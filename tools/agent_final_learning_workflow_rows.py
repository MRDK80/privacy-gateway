"""Fixed WORKFLOW-only neutral rows; no complete packet or readiness assertion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools import agent_learning as learning
from tools.agent_final_learning_binding_source import (
    WorkflowCallFact,
    _read_workflow_call_facts,
)
from tools.agent_final_learning_domain import (
    _FinalSourceReader,
    _PreAggregateFinalSourceReader,
    preaggregate_source_scope_owned,
)
from tools.agent_final_preparation_runtime import _CurrentFinalSources


@dataclass(frozen=True, slots=True, repr=False)
class WorkflowCallRow:
    task: int
    family: str
    role: str
    ordinal: int
    invocation_key: str
    native_invocation_digest: str
    metadata_digest: str
    binding_digest: str
    source_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class WorkflowUseRow:
    call_key: str
    ordered_lessons: tuple[tuple[str, str, str, str, str, str], ...]


@dataclass(frozen=True, slots=True, repr=False)
class WorkflowRows:
    calls: tuple[WorkflowCallRow, ...]
    uses: tuple[WorkflowUseRow, ...]


def _project_workflow_rows(facts: tuple[WorkflowCallFact, ...]) -> WorkflowRows:
    """Pure representation only; owned entries obtain their own original facts."""
    calls = []
    uses = []
    keys: set[str] = set()
    for fact in facts:
        source = fact.association
        if source.invocation_key in keys:
            raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
        keys.add(source.invocation_key)
        calls.append(
            WorkflowCallRow(
                source.task,
                "WORKFLOW",
                fact.role,
                fact.ordinal,
                source.invocation_key,
                source.native_invocation_digest,
                fact.metadata_digest,
                source.binding_digest,
                source.source_digest,
            )
        )
        uses.append(WorkflowUseRow(source.invocation_key, source.references))
    return WorkflowRows(
        tuple(
            sorted(
                calls,
                key=lambda row: (
                    row.task,
                    row.family,
                    row.role,
                    row.ordinal,
                    row.invocation_key,
                ),
            )
        ),
        tuple(sorted(uses, key=lambda row: row.call_key)),
    )


def read_workflow_rows_owned(session: Any) -> WorkflowRows:
    """Original completed source proof, projection and final guard in one scope."""
    pins = _CurrentFinalSources(session)
    reader = _FinalSourceReader(session)
    try:
        facts = _read_workflow_call_facts(reader, session)
        rows = _project_workflow_rows(facts)
        reader._source_tasks()
        pins.check()
        return rows
    finally:
        reader.close()


def read_preaggregate_workflow_rows_owned(session: Any) -> WorkflowRows:
    """Only the original-zero factory, never an injected reader or source flag."""
    pins = _CurrentFinalSources(session)
    with preaggregate_source_scope_owned(session) as reader:
        if type(reader) is not _PreAggregateFinalSourceReader:
            raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
        assert isinstance(reader, _PreAggregateFinalSourceReader)
        facts = _read_workflow_call_facts(reader, session)
        rows = _project_workflow_rows(facts)
        reader._source_tasks()
        pins.check()
        return rows
