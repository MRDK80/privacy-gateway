"""Fixed owned neutral packet composition; no report or aggregate authority."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from tools import agent_learning as core
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_binding_source import (
    WorkflowCallFact,
    _read_workflow_origins_snapshot,
)
from tools.agent_final_learning_domain import (
    _FinalPlanningReader,
    _FinalSourceReader,
    _PreAggregateFinalSourceReader,
    _PreAggregatePlanningReader,
)
from tools.agent_final_learning_measurements import (
    MeasurementRow,
    _OriginSnapshot,
    _project_measurements,
)
from tools.agent_final_learning_no_planning import (
    NoPlanningTaskFact,
    _read_planning_partition,
)
from tools.agent_final_learning_origin_rows import NegativeRow, OriginRows, RetainedRow
from tools.agent_final_learning_packet import LESSON_FIELDS, encode_packet
from tools.agent_final_learning_planning_coverage import PlanningCoverage
from tools.agent_final_learning_settings import _namespace, _SettingsCallSource
from tools.agent_final_learning_task_rows import (
    TaskRow,
    _planning_identity,
    _project_task_rows,
)
from tools.agent_final_learning_workflow_rows import (
    WorkflowCallRow,
    WorkflowUseRow,
    _project_workflow_rows,
)
from tools.agent_final_learning_workflow_tasks import WorkflowTaskFact
from tools.agent_final_preparation_runtime import (
    HistoricalPlanningUse,
    _CurrentFinalSources,
)

_WorkflowSnapshot = tuple[
    tuple[WorkflowCallFact, ...], OriginRows, tuple[WorkflowTaskFact, ...],
    tuple[_SettingsCallSource, ...], _OriginSnapshot,
]
_PlanningPartition = tuple[PlanningCoverage, tuple[NoPlanningTaskFact, ...]]


@dataclass(frozen=True, slots=True, repr=False)
class _PlanningCallRow:
    task: int
    family: str
    role: str
    ordinal: int
    invocation_key: str
    native_invocation_digest: None
    metadata_digest: str
    binding_digest: None
    source_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class _PlanningRows:
    calls: tuple[_PlanningCallRow, ...]
    uses: tuple[WorkflowUseRow, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _Families:
    tasks: tuple[TaskRow, ...]
    calls: tuple[WorkflowCallRow | _PlanningCallRow, ...]
    uses: tuple[WorkflowUseRow, ...]
    retained: tuple[RetainedRow, ...]
    negative_checks: tuple[NegativeRow, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _Identity:
    repository: str
    epic: int
    policy_sha: str
    mandate_digest: str
    launch_digest: str
    learning_profile_digest: str


def _require(value: bool) -> None:
    if not value:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")


def _planning_rows(values: tuple[HistoricalPlanningUse, ...]) -> _PlanningRows:
    """Pure P13/ref6 projection, never source admission or reader selection."""
    calls = []
    uses = []
    keys: set[str] = set()
    for value in values:
        fact = value.call_fact()
        refs = value.lesson_references()
        _require(value.facts()[:3] == (fact.task, fact.role, fact.ordinal))
        _require(tuple(row[0] for row in refs) == value.facts()[8])
        _require(fact.invocation_key not in keys)
        keys.add(fact.invocation_key)
        calls.append(_PlanningCallRow(
            fact.task, "PLANNING", fact.role, fact.ordinal, fact.invocation_key,
            None, fact.metadata_digest, None, fact.source_digest,
        ))
        uses.append(WorkflowUseRow(fact.invocation_key, refs))
    return _PlanningRows(
        tuple(sorted(calls, key=lambda row: (
            row.task, row.family, row.role, row.ordinal, row.invocation_key,
        ))),
        tuple(sorted(uses, key=lambda row: row.call_key)),
    )


def _reference_membership(
    calls: Sequence[WorkflowCallRow | _PlanningCallRow],
    uses: tuple[WorkflowUseRow, ...], retained: tuple[RetainedRow, ...],
) -> None:
    """Supplement original pair/source proof; preserve valid cross-origin copies."""
    keys = [call.invocation_key for call in calls]
    use_keys = [use.call_key for use in uses]
    _require(len(set(keys)) == len(keys))
    _require(len(set(use_keys)) == len(use_keys) and set(keys) == set(use_keys))
    indexed = {call.invocation_key: call for call in calls}
    for use in uses:
        family = indexed[use.call_key].family
        _require(family in {"WORKFLOW", "PLANNING"})
        store = "WORKFLOW_STORE" if family == "WORKFLOW" else "PLANNING_STORE"
        prefix = "" if family == "WORKFLOW" else "planning_"
        for ref in use.ordered_lessons:
            _require(len(ref) == len(LESSON_FIELDS))
            for position, kind in ((1, "verification"), (2, "candidate"),
                                   (3, "observation"), (4, "observation")):
                _require(any(
                    row.family == store and row.kind == prefix + kind
                    and row.record_digest == ref[position]
                    and (kind != "verification" or row.key == ref[0])
                    for row in retained
                ))
            # ref[5] is the original derived pair, not a persisted pair row.


def _families(
    universe: tuple[int, ...], snapshot: _WorkflowSnapshot,
    partition: _PlanningPartition,
) -> _Families:
    coverage, no_planning = partition
    tasks = _project_task_rows(universe, snapshot[2], snapshot[0],
                               coverage, no_planning)
    workflow = _project_workflow_rows(snapshot[0])
    planning = _planning_rows(coverage.calls)
    calls: tuple[WorkflowCallRow | _PlanningCallRow, ...] = (
        *workflow.calls, *planning.calls,
    )
    uses = tuple(sorted((*workflow.uses, *planning.uses), key=lambda row: row.call_key))
    origins = snapshot[1]
    _reference_membership(calls, uses, origins.retained)
    return _Families(tasks, calls, uses, origins.retained, origins.negative_checks)


def _identity_cap(
    source: _FinalSourceReader | _PreAggregateFinalSourceReader,
) -> tuple[_Identity, int]:
    source.guard()
    if type(source) is _FinalSourceReader:
        assert isinstance(source, _FinalSourceReader)
        profile = source._completed.profile
    elif type(source) is _PreAggregateFinalSourceReader:
        assert isinstance(source, _PreAggregateFinalSourceReader)
        profile = source._context.original.profile
    else:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    config, owner = source.config, source.owner
    _require(config["approved_mandate_digest"] == owner["mandate_digest"])
    return _Identity(
        owner["repository"], owner["epic"], owner["policy_sha"],
        config["approved_mandate_digest"], config["approved_launch_digest"],
        config["approved_learning_digest"],
    ), core.integer(profile["max_artifact_bytes"], 262144, 1)


def _global_settings(
    source: _FinalSourceReader | _PreAggregateFinalSourceReader,
    sources: tuple[_SettingsCallSource, ...],
) -> None:
    # Exact existing namespace normalization, not source-error-as-empty handling.
    try:
        _namespace(source, sources)
    except OSError:
        raise core.LearningError("LEARNING_STORAGE_UNSAFE") from None


def _encode(
    identity: _Identity, cap: int, families: _Families,
    measurements: tuple[MeasurementRow, ...],
) -> dict[str, Any]:
    uses = [{"call_key": row.call_key, "ordered_lessons": [
        dict(zip(LESSON_FIELDS, ref, strict=True)) for ref in row.ordered_lessons
    ]} for row in families.uses]
    return encode_packet(
        identity=asdict(identity), tasks=[asdict(row) for row in families.tasks],
        calls=[asdict(row) for row in families.calls],
        retained=[asdict(row) for row in families.retained], uses=uses,
        measurements=[asdict(row) for row in measurements],
        negative_checks=[asdict(row) for row in families.negative_checks],
        max_artifact_bytes=cap,
    )


def _collect(
    historical: _FinalPlanningReader | _PreAggregatePlanningReader, session: Any,
) -> dict[str, Any]:
    if (type(historical) not in {_FinalPlanningReader, _PreAggregatePlanningReader}
        or historical.session is not session):
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    source = historical.reader
    if type(source) not in {_FinalSourceReader, _PreAggregateFinalSourceReader}:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    assert isinstance(source, (_FinalSourceReader, _PreAggregateFinalSourceReader))
    pins = _CurrentFinalSources(session)
    original_identity = _identity_cap(source)
    universe = source._source_tasks()
    domain = historical._domain_with_task_facts()
    original = _read_workflow_origins_snapshot(source, session)
    partition = _read_planning_partition(historical, session)
    _global_settings(source, original[3])  # Shared scope exits before task replay.
    measurements = _project_measurements(
        source, session, universe, original[4], original[2],
    )
    families = _families(universe, original, partition)
    final = _read_workflow_origins_snapshot(source, session)
    final_partition = _read_planning_partition(historical, session)
    _require(final == original)
    _require(_planning_identity(final_partition) == _planning_identity(partition))
    _require(_families(universe, final, final_partition) == families)
    _require(_project_measurements(source, session, universe,
                                   final[4], final[2]) == measurements)
    _global_settings(source, final[3])  # Unwind before final original domain replay.
    _require(historical._domain_with_task_facts() == domain)
    _require(source._source_tasks() == universe)
    historical._guard()
    pins.check()
    _require(_identity_cap(source) == original_identity)
    # First packet/digest construction is after every required family proof.
    packet = _encode(*original_identity, families, measurements)
    historical._guard()
    source._source_tasks()
    pins.check()
    return packet


def read_learning_packet_owned(session: Any) -> dict[str, Any]:
    historical = _FinalPlanningReader(session)
    try:
        with historical.reader._packet_policy_proof(session):
            return _collect(historical, session)
    finally:
        historical.reader.close()


def read_preaggregate_learning_packet_owned(session: Any) -> dict[str, Any]:
    historical = _PreAggregatePlanningReader(session)
    try:
        return _collect(historical, session)
    finally:
        historical.reader.close()
