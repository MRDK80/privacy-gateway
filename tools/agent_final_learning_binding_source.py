"""Fixed FINAL read joins for original workflow associations, never authority."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
from typing import Any

from tools import agent_learning as core
from tools import agent_learning_planning_promotion as planning
from tools import agent_learning_planning_records as planning_records
from tools import agent_learning_records as records
from tools.agent_continuation import digest
from tools.agent_final_learning_domain import (
    _FinalSourceReader,
    _PreAggregateFinalSourceReader,
    preaggregate_source_scope_owned,
)
from tools.agent_final_learning_measurements import _origin_snapshot, _OriginSnapshot
from tools.agent_final_learning_origin_rows import OriginRows, _project_origin_rows
from tools.agent_final_learning_settings import (
    _settings_call_source,
    _SettingsCallSource,
)
from tools.agent_final_learning_workflow_tasks import WorkflowTaskFact
from tools.agent_learning_measure import _metadata
from tools.agent_learning_store import Store
from tools.agent_learning_verify import Verifier


def _require(condition: bool) -> None:
    if not condition:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")


@dataclass(frozen=True, repr=False)
class WorkflowAssociation:
    task: int
    invocation_key: str
    native_invocation_digest: str
    binding_digest: str
    source_digest: str
    references: tuple[tuple[str, str, str, str, str, str], ...]


@dataclass(frozen=True, slots=True, repr=False)
class WorkflowCallFact:
    """Neutral same-branch projection; construction conveys no source authority."""

    association: WorkflowAssociation
    run_id: str
    role: str
    ordinal: int
    metadata_digest: str


def _origins(
    reader: _FinalSourceReader | _PreAggregateFinalSourceReader, verifier: Verifier
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fixed original domains, complete source validation even without a Store."""
    origins: list[dict[str, Any]] = []
    membership: list[dict[str, Any]] = []
    tasks: dict[int, list[dict[str, Any]]] = {}
    workflow_rows: list[dict[str, Any]] = []
    for task in reader._source_tasks():
        reader.guard()
        directory = reader.task_directory(task)
        tasks[task] = []
        path = directory / "learning"
        present = path.exists() or path.is_symlink()
        if present:
            with reader.ownership(directory):
                with Store(
                    path,
                    reader.root,
                    directory,
                    reader.owner,
                    validators=records.VALIDATORS,
                ) as store:
                    store.ensure_usable()
                    tasks[task] = store.records()
        info = path.lstat() if present else None
        membership.append(
            {
                "family": "WORKFLOW_STORE",
                "task": task,
                "present": present,
                "identity": (
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_uid,
                    info.st_nlink,
                )
                if info is not None
                else None,
                "records_digest": digest(tasks[task]),
            }
        )
        workflow_rows.extend(tasks[task])
        if len(workflow_rows) > reader.owner["limits"]["max_records"]:
            raise core.LearningError("LEARNING_STORAGE_EXHAUSTED")
        origins.extend(
            {
                "family": "WORKFLOW_STORE",
                "task": task,
                "kind": item["kind"],
                "record": item["record"],
            }
            for item in tasks[task]
        )
    # Preserve original source completeness for EVERY U root, Store present or not.
    for task, items in tasks.items():
        verifier._source_complete(task, items, workflow_rows)
    # Exactly one shared planning origin; no per-task duplication or fake receipts.
    planning_path = reader.seed / "planning-learning"
    planning_present = planning_path.exists() or planning_path.is_symlink()
    planned = planning.retained(reader)
    info = planning_path.lstat() if planning_present else None
    membership.append(
        {
            "family": "PLANNING_STORE",
            "task": None,
            "present": planning_present,
            "identity": (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_nlink,
            )
            if info is not None
            else None,
            "records_digest": digest(planned),
        }
    )
    origins.extend(
        {
            "family": "PLANNING_STORE",
            "task": None,
            "kind": item["kind"],
            "record": item["record"],
        }
        for item in planned
    )
    reader._source_tasks()
    return origins, membership


def _current_invocations(
    origins: list[dict[str, Any]],
    task: int,
    run_id: str,
    metadata: list[dict[str, Any]],
    owner: dict[str, Any],
) -> None:
    expected_identity = {
        "repository": owner["repository"],
        "epic": owner["epic"],
        "task": task,
        "policy_sha": owner["policy_sha"],
        "owner_profile_digest": digest(owner),
        "original_run_id": run_id,
    }
    stored = [
        item["record"]
        for item in origins
        if item["family"] == "WORKFLOW_STORE"
        and item["task"] == task
        and item["kind"] == "invocation"
        and item["record"]["identity"]["original_run_id"] == run_id
    ]
    expected = {item["invocation_key"]: item for item in metadata}
    _require(len(expected) == len(metadata))
    actual: dict[str, dict[str, Any]] = {}
    for row in stored:
        _require(row["identity"] == expected_identity)
        _require(
            row["role"] in {"executor", "controller"} and row["status"] == "COMPLETED"
        )
        key = row["invocation_key"]
        _require(key not in actual)
        actual[key] = row
    _require(set(actual) == set(expected))
    _require(all(digest(actual[key]) == digest(expected[key]) for key in expected))


def _read_workflow_origins_snapshot(
    reader: _FinalSourceReader | _PreAggregateFinalSourceReader, session: Any
) -> tuple[
    tuple[WorkflowCallFact, ...], OriginRows, tuple[WorkflowTaskFact, ...],
    tuple[_SettingsCallSource, ...], _OriginSnapshot,
]:
    """Shared original core, only internally constructed exact owned readers."""
    _require(type(reader) in {_FinalSourceReader, _PreAggregateFinalSourceReader})
    if isinstance(reader, _FinalSourceReader):
        _require(reader._session is session)
    else:
        _require(reader._context.pins.session is session)
    verifier = Verifier(reader, session)
    origins, membership = _origins(reader, verifier)
    result: list[WorkflowCallFact] = []
    task_facts: list[WorkflowTaskFact] = []
    settings: list[_SettingsCallSource] = []
    for task in reader._source_tasks():
        reader.guard()
        directory = reader.task_directory(task)
        # Existing original terminal source validator; unsupported absence blocks.
        fact = reader.workflow(task)
        task_facts.append(
            WorkflowTaskFact(
                task,
                fact.state["run_id"],
                fact.state["executor_calls"],
                fact.state["controller_calls"],
            )
        )
        chain, _ = reader._completed_role_chain(task, fact.contract, fact.state)
        settings.extend(
            _settings_call_source(
                task, fact.state["run_id"], role, ordinal,
                original["reservation"]["invocation_digest"], original["source"],
            )
            for (role, ordinal), original in sorted(chain.items())
        )
        metadata = _metadata(
            reader,
            directory / "learning-invocations",
            task=task,
            run_id=fact.state["run_id"],
        )
        indexed = {(row["role"], row["ordinal"]): row for row in metadata}
        _require(len(indexed) == len(metadata) and set(indexed) == set(chain))
        _current_invocations(
            origins, task, fact.state["run_id"], metadata, reader.owner
        )
        expected = {row["invocation_key"] + ".json" for row in metadata}
        path = directory / "workflow-learning-bindings"
        from tools.agent_epic_runtime_cli import secure_directory

        secure_directory(path, reader.root)
        entries = list(islice(path.iterdir(), len(expected) + 1))
        _require(
            {entry.name for entry in entries} == expected
            and len(entries) == len(expected)
        )
        # Parser is only syntax. All acceptance below comes from original joins.
        from tools import agent_learning_binding_records as binding_records

        total_bytes = 0
        for position in sorted(indexed):
            reader.guard()
            row, original = indexed[position], chain[position]
            native, checkpoint = original["reservation"], original["checkpoint"]
            source = original["source"]
            key = row["invocation_key"]
            value = binding_records.binding(
                reader._json(path / (key + ".json")),
                max_repairs=fact.contract.max_repair_iterations,
                max_hints=reader.owner["limits"]["max_hints_per_role"],
            )
            total_bytes += (path / (key + ".json")).lstat().st_size
            _require(total_bytes <= reader.owner["limits"]["max_store_bytes"])
            _require(value["status"] == row["status"] == "COMPLETED")
            _require(value["identity"] == row["identity"])
            _require(row["identity"]["original_run_id"] == fact.state["run_id"])
            for field in (
                "role",
                "ordinal",
                "repair_iteration",
                "invocation_key",
                "selection_digest",
                "input_digest",
                "prompt_digest",
            ):
                _require(value[field] == row[field])
            invocation = native["invocation_digest"]
            _require(value["native_invocation_digest"] == invocation)
            _require(value["checkpoint_digest"] == digest(checkpoint))
            _require(native["checkpoint_digest"] == digest(checkpoint))
            _require(
                value["stable_metadata_digest"] == binding_records.stable_metadata(row)
            )
            reserved = {
                **native,
                "status": "RESERVED",
                "snapshot_after": None,
                "outcome_digest": None,
            }
            _require(value["native_reservation_digest"] == digest(reserved))
            completion = value["completion"]
            _require(
                completion
                == {
                    "native_reservation_digest": digest(native),
                    "settings_source_digest": digest(source),
                    "settings_receipt_digest": digest(source["receipt"]),
                    "terminal_digest": digest(source["terminal"]),
                    "completed_metadata_digest": digest(row),
                }
            )
            _require(native["outcome_digest"] == source["receipt"]["receipt_digest"])
            problem = planning_records.problem_binding(
                reader._json(
                    directory / "learning-invocations" / (key + ".problem.json")
                )
            )
            _require(problem["invocation_key"] == key)
            # Existing _metadata/source-complete validators prove problem origin.
            calls = [
                item["record"]
                for item in origins
                if item["family"] == "WORKFLOW_STORE"
                and item["task"] == task
                and item["kind"] == "invocation"
                and item["record"]["invocation_key"] == key
            ]
            _require(bool(calls) and all(item == row for item in calls))
            references = []
            for reference in value["ordered_lessons"]:

                def original_record(kind: str, expected_digest: str) -> dict[str, Any]:
                    found = [
                        item["record"]
                        for item in origins
                        if item["family"] == "WORKFLOW_STORE"
                        and item["kind"] == kind
                        and digest(item["record"]) == expected_digest
                    ]
                    _require(bool(found) and all(item == found[0] for item in found))
                    return dict(found[0])

                verified = original_record(
                    "verification", reference["verification_digest"]
                )
                candidate = original_record("candidate", reference["candidate_digest"])
                before = original_record("observation", reference["before_digest"])
                after = original_record("observation", reference["after_digest"])
                _require(verified["lesson_id"] == reference["lesson_id"])
                _require(verified["candidate_digest"] == digest(candidate))
                _require(verified["observation_digest"] == digest(after))
                _require(verified["catalog_digest"] == reader.owner["catalog_digest"])
                rules = [
                    rule
                    for rule in reader.catalog["rules"]
                    if rule["rule_id"] == candidate["rule_id"]
                ]
                _require(len(rules) == 1)
                pair = verifier.pair(before, after, rules[0])
                _require(pair is not None and digest(pair) == reference["pair_digest"])
                assert pair is not None
                _require(
                    all(
                        pair[field] == verified[field]
                        for field in ("evidence_digest", "applicability_digest")
                    )
                )
                references.append(
                    tuple(
                        reference[field]
                        for field in (
                            "lesson_id",
                            "verification_digest",
                            "candidate_digest",
                            "before_digest",
                            "after_digest",
                            "pair_digest",
                        )
                    )
                )
            _require(
                sorted(reference[0] for reference in references) == row["lesson_ids"]
            )
            if not references:
                _require(row["selection_digest"] == digest([]))
            review = (
                reader._json(
                    directory / "continuation" / ("review-" + invocation + ".json")
                )
                if row["role"] == "controller"
                else None
            )
            proof = {
                "family": "WORKFLOW",
                "task": task,
                "run_id": fact.state["run_id"],
                "role": row["role"],
                "ordinal": row["ordinal"],
                "reservation_digest": digest(native),
                "checkpoint_digest": digest(checkpoint),
                "settings_source_digest": digest(source),
                "controller_review_digest": digest(review) if review else None,
                "metadata_digest": digest(row),
                "problem_digest": digest(problem),
                "binding_digest": digest(value),
            }
            result.append(
                WorkflowCallFact(
                    WorkflowAssociation(
                        task,
                        key,
                        invocation,
                        digest(value),
                        digest(proof),
                        tuple(references),
                    ),
                    proof["run_id"],
                    proof["role"],
                    proof["ordinal"],
                    proof["metadata_digest"],
                )
            )
            reader._source_tasks()
    _require(_origins(reader, verifier) == (origins, membership))
    reader._source_tasks()
    return (
        tuple(result), _project_origin_rows(origins, membership), tuple(task_facts),
        tuple(settings), _origin_snapshot(origins, membership),
    )


def _read_workflow_settings_snapshot(
    reader: _FinalSourceReader | _PreAggregateFinalSourceReader, session: Any
) -> tuple[
    tuple[WorkflowCallFact, ...], OriginRows, tuple[WorkflowTaskFact, ...],
    tuple[_SettingsCallSource, ...],
]:
    calls, origins, tasks, settings, _ = _read_workflow_origins_snapshot(
        reader, session
    )
    return calls, origins, tasks, settings


def _read_workflow_coverage_snapshot(
    reader: _FinalSourceReader | _PreAggregateFinalSourceReader, session: Any
) -> tuple[tuple[WorkflowCallFact, ...], OriginRows, tuple[WorkflowTaskFact, ...]]:
    calls, origins, tasks, _ = _read_workflow_settings_snapshot(reader, session)
    return calls, origins, tasks


def _read_workflow_source_snapshot(
    reader: _FinalSourceReader | _PreAggregateFinalSourceReader, session: Any
) -> tuple[tuple[WorkflowCallFact, ...], OriginRows]:
    """Preserve the original private two-tuple API from the same full read."""
    facts, origins, _ = _read_workflow_coverage_snapshot(reader, session)
    return facts, origins


def _read_workflow_call_facts(
    reader: _FinalSourceReader | _PreAggregateFinalSourceReader, session: Any
) -> tuple[WorkflowCallFact, ...]:
    """Preserve legacy return shape from the same full guarded source read."""
    facts, _ = _read_workflow_source_snapshot(reader, session)
    return facts


def read_workflow_call_facts_owned(session: Any) -> tuple[WorkflowCallFact, ...]:
    """Keep the original completed-owned entry and lifetime unchanged."""
    reader = _FinalSourceReader(session)
    try:
        return _read_workflow_call_facts(reader, session)
    finally:
        reader.close()


def read_preaggregate_workflow_call_facts_owned(
    session: Any,
) -> tuple[WorkflowCallFact, ...]:
    """Fixed admitted-zero entry; scope final guard runs before facts escape."""
    with preaggregate_source_scope_owned(session) as reader:
        _require(type(reader) is _PreAggregateFinalSourceReader)
        assert isinstance(reader, _PreAggregateFinalSourceReader)
        return _read_workflow_call_facts(reader, session)


def read_workflow_associations_owned(session: Any) -> tuple[WorkflowAssociation, ...]:
    """Legacy association projection from the same fixed completed-owned read."""
    return tuple(fact.association for fact in read_workflow_call_facts_owned(session))
