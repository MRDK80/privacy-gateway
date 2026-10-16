"""Source measurements, honest comparisons, and persistent negative rollback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools import agent_epic_planning as admission
from tools import agent_learning as c
from tools import agent_learning_planning_records as planning_records
from tools import agent_learning_records as records
from tools.agent_continuation import digest
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_runtime_cli import secure_directory
from tools.agent_epic_session_runtime import SessionRuntime
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_sources import environment as source_environment
from tools.agent_learning_store import Store


def _metadata(
    reader: SourceReader, directory: Path, *, task: int, run_id: str
) -> list[dict[str, Any]]:
    if not directory.exists() and not directory.is_symlink():
        return []
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise c.LearningError("LEARNING_STORAGE_UNSAFE")
    secure_directory(directory, reader.root)
    limits = reader.owner["limits"]
    values: list[dict[str, Any]] = []
    size = 0
    companions: dict[str, dict[str, Any]] = {}
    invocations: dict[str, dict[str, Any]] = {}
    with RunnerLock(directory):
        for count, path in enumerate(directory.iterdir(), 1):
            if path.name == "epic-loop.lock":
                continue
            if count > limits["max_records"] + 1 or path.suffix != ".json":
                raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
            size += path.lstat().st_size
            if size > limits["max_store_bytes"]:
                raise c.LearningError("LEARNING_STORAGE_UNSAFE")
            suffix = next(
                (
                    suffix
                    for suffix in (".problem", ".relevance")
                    if path.stem.endswith(suffix)
                ),
                None,
            )
            if suffix is not None:
                c.hash_value(path.stem.removesuffix(suffix))
                validate = (
                    planning_records.problem_binding
                    if suffix == ".problem"
                    else planning_records.relevance
                )
                companions[path.stem] = validate(reader._json(path))
                continue
            c.hash_value(path.stem)
            value = records.invocation(reader._json(path))
            invocations[path.stem] = value
            if size > limits["max_store_bytes"] or value["invocation_key"] != path.stem:
                raise c.LearningError("LEARNING_STORAGE_UNSAFE")
            identity = value["identity"]
            if identity["original_run_id"] != run_id:
                continue
            if (
                identity["task"] != task
                or identity["policy_sha"] != reader.owner["policy_sha"]
                or identity["epic"] != reader.owner["epic"]
                or identity["repository"] != reader.owner["repository"]
                or identity["owner_profile_digest"] != digest(reader.owner)
                or value["status"] != "COMPLETED"
            ):
                raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
            values.append(value)
        for key in invocations:
            if key + ".problem" not in companions:
                raise c.LearningError("LEARNING_SOURCE_MISSING")
        for stem, companion in companions.items():
            key, kind = stem.rsplit(".", 1)
            invocation = invocations.get(key)
            if invocation is None:
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            problem = companions.get(key + ".problem")
            if problem is None:
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            if (
                problem["invocation_key"] != key
                or problem["role"] != invocation["role"]
                or (problem["source_kind"] == "NONE" and invocation["lesson_ids"])
            ):
                raise c.LearningError("LEARNING_SOURCE_MISMATCH")
            relevance = companions.get(key + ".relevance")
            if problem["source_kind"] == "PLANNING_VALIDATION":
                if relevance is None:
                    raise c.LearningError("LEARNING_SOURCE_MISSING")
                if problem["source_digest"] != digest(relevance):
                    raise c.LearningError("LEARNING_SOURCE_MISMATCH")
            elif kind == "relevance":
                raise c.LearningError("LEARNING_SOURCE_MISMATCH")
    return values


def observed(
    reader: SourceReader, session: SessionRuntime, task: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reader.guard()
    if (
        not isinstance(session, SessionRuntime)
        or session.state != reader.seed
        or session.root.resolve() != reader.root
        or dict(session.config) != reader.config
    ):
        raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
    fact = reader.workflow(task)
    directory = fact.directory
    calls = {
        "planner": 0,
        "planning_review": 0,
        "executor": fact.state["executor_calls"],
        "controller": fact.state["controller_calls"],
    }
    planning = admission.Admission(reader.seed, reader.root)
    source = planning._storage(task)
    planning_path = source / "admission.json"
    planned_metadata: list[dict[str, Any]] = []
    if planning_path.exists() or planning_path.is_symlink():
        original = reader._json(planning_path)
        admission.exact(original, admission.RECORD_KEYS)
        if (
            original["task"] != task
            or original["policy_sha"] != reader.owner["policy_sha"]
            or original["mandate_digest"] != reader.owner["mandate_digest"]
            or original["repository"] != reader.owner["repository"]
            or original["epic"] != reader.owner["epic"]
            or original["pending"] is not None
        ):
            raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
        for role in ("planner", "review"):
            count = c.integer(original["attempts"][role], 3)
            calls["planner" if role == "planner" else "planning_review"] = count
        identity = {
            key: original[key]
            for key in (
                "schema_version",
                "repository",
                "epic",
                "task",
                "planning_digest",
                "mandate_digest",
                "policy_sha",
            )
        }
        planned_metadata = _metadata(
            reader, source / "learning-invocations", task=task, run_id=digest(identity)
        )
        if any(
            sum(value["role"] == logical for value in planned_metadata)
            != calls[logical]
            for logical in ("planner", "planning_review")
        ):
            raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
    elif session._approved_recipe(task) is None:
        seed = reader._json(reader.seed / "epic-loop-checkpoint.json")
        plans = reader._json(reader.seed / "approved-plans.json")
        config = reader._json(directory / "runtime-task.json")
        if (
            seed["task"] != task
            or plans["initial_handover_digest"] != config["approved_handover_digest"]
        ):
            raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
    metadata = _metadata(
        reader,
        directory / "learning-invocations",
        task=task,
        run_id=fact.state["run_id"],
    )
    metadata += planned_metadata
    actual_counts = {
        role: sum(value["role"] == role for value in metadata)
        for role in ("executor", "controller")
    }
    if any(actual_counts[role] != calls[role] for role in actual_counts):
        raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
    if any(value["status"] != "COMPLETED" for value in metadata):
        raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
    selection = sorted(
        {value["selection_digest"] for value in metadata if value["lesson_ids"]}
    )
    applicability = digest(
        {
            "policy_sha": reader.owner["policy_sha"],
            "catalog_digest": reader.owner["catalog_digest"],
            "criteria_digest": digest(list(fact.contract.acceptance_criteria)),
            "grant_digest": digest(reader.mandate["task_grants"][str(task)]),
            "initial_head": fact.memory["head_sha"],
            "initial_base": fact.contract.base_sha,
            "contract_class": fact.contract.task_class,
            "allowed_paths": list(fact.contract.allowed_paths),
            "roles": sorted(c.ROLES),
            "problems": sorted(
                {
                    rule["problem_class"]
                    for rule in reader.catalog["rules"]
                    if rule["task_class"] == fact.contract.task_class
                }
            ),
            "environment": source_environment(reader),
            "initial_tree": reader._git(
                "rev-parse", fact.memory["head_sha"] + "^{tree}"
            )
            .decode()
            .strip(),
        }
    )
    value = {
        "schema_version": "1.0",
        "source_run_digest": digest({"state": fact.state, "memory": fact.memory}),
        "applicability_digest": applicability,
        "hint_set_digest": digest(selection) if selection else None,
        "outcome": fact.state["status"],
        "iterations": calls["controller"],
        "calls": calls,
        "duration_seconds": fact.memory["duration_seconds"],
    }
    value["measurement_id"] = digest(value)
    value = c.measurement(c.seal(value))
    return value, metadata


def verified_baseline(
    reader: SourceReader,
    session: SessionRuntime,
    value: dict[str, Any],
    retained: list[dict[str, Any]],
) -> bool:
    origins = [
        item["record"]["identity"]["task"]
        for item in retained
        if item["kind"] == "observation"
        and item["record"]["kind"] == "TERMINAL_WORKFLOW"
        and item["record"]["source"]["workflow_digest"] == value["source_run_digest"]
    ]
    for task in sorted(set(origins)):
        source, _ = observed(reader, session, task)
        if source == value and source["hint_set_digest"] is None:
            return True
    return False


def comparison(
    reader: SourceReader,
    session: SessionRuntime,
    value: dict[str, Any],
    retained: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Recompute the deterministic comparison from original baseline sources."""
    baselines = [
        item["record"]
        for item in retained
        if item["kind"] == "measurement"
        and item["record"]["hint_set_digest"] is None
        and item["record"]["applicability_digest"] == value["applicability_digest"]
        and verified_baseline(reader, session, item["record"], retained)
    ]
    baselines.sort(key=lambda item: item["record_digest"])
    baseline = baselines[0] if baselines else None
    return baseline, records.evaluation(c.compare(baseline, value))


def _rollback_maximum(
    reader: SourceReader,
    session: SessionRuntime,
    task: int,
    value: dict[str, Any],
    metadata: list[dict[str, Any]],
) -> int:
    """Bound the run union by original approved calls and the per-call cap."""
    fact = reader.workflow(task)
    budgets = {
        "executor": fact.contract.max_repair_iterations + 1,
        "controller": fact.contract.max_repair_iterations + 1,
        "planner": 0,
        "planning_review": 0,
    }
    if any(value["calls"][role] for role in ("planner", "planning_review")):
        planning = session._planning_owner(reader.mandate)
        budgets["planner"] = planning["limits"]["max_planner_attempts"]
        budgets["planning_review"] = planning["limits"]["max_review_attempts"]
    cap = c.integer(reader.owner["limits"]["max_hints_per_role"], 3)
    for role, budget in budgets.items():
        c.integer(budget, 3)
        count = c.integer(value["calls"][role], budget)
        used = [item for item in metadata if item["role"] == role]
        if (
            len(used) != count
            or {item["ordinal"] for item in used} != set(range(1, count + 1))
            or any(len(item["lesson_ids"]) > cap for item in used)
        ):
            raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
    return sum(budgets.values()) * cap


def capture(reader: SourceReader, session: SessionRuntime, task: int) -> dict[str, Any]:
    directory = reader.task_directory(task)
    with reader.ownership(directory):
        return _capture(reader, session, task)


def _capture(
    reader: SourceReader, session: SessionRuntime, task: int
) -> dict[str, Any]:
    from tools.agent_learning_verify import Verifier

    value, metadata = observed(reader, session, task)
    retained = Verifier(reader, session)._retained_for_capture(task)
    hint_ids = sorted({lesson for item in metadata for lesson in item["lesson_ids"]})
    baseline, evaluation = (
        comparison(reader, session, value, retained) if hint_ids else (None, None)
    )
    completion = None
    if hint_ids:
        # A completed private hint record cannot substitute for the original
        # provider settings/completion chain, including a failed controller.
        completion = reader.controller(task)
    rollback = (
        evaluation is not None
        and evaluation["comparison"]["status"] == "ROLLBACK_REQUIRED"
    )
    maximum = (
        _rollback_maximum(reader, session, task, value, metadata) if rollback else 0
    )
    directory = reader.task_directory(task)
    with reader.ownership(directory):
        with Store(
            directory / "learning",
            reader.root,
            directory,
            reader.owner,
            validators=records.VALIDATORS,
        ) as store:
            # Only this locked source reconciliation may finish its own
            # pending append. Selection always uses the strict retained path.
            store.records()
            if rollback:
                assert evaluation is not None
                store.block(
                    digest(evaluation), hint_ids, "LEARNING_REGRESSION", maximum=maximum
                )
            for item in metadata:
                store.append("invocation", item, "invocation_key")
            store.append("measurement", value, "measurement_id")
            if not hint_ids:
                return value
            assert evaluation is not None
            store.append("evaluation", evaluation, "evaluation_id")
            if rollback:
                reason = "LEARNING_REGRESSION"
                for lesson in hint_ids:
                    tombstone = records.tombstone(
                        c.seal(
                            {
                                "schema_version": "1.0",
                                "lesson_id": lesson,
                                "reason_code": reason,
                                "evaluation_digest": digest(evaluation),
                            }
                        )
                    )
                    store.append("tombstone", tombstone, "record_digest")
                reread, applied = observed(reader, session, task)
                if (
                    reread != value
                    or reader.controller(task) != completion
                    or sorted(digest(item) for item in applied)
                    != sorted(digest(item) for item in metadata)
                    or baseline is None
                    or not verified_baseline(reader, session, baseline, retained)
                ):
                    raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
                store.clear_block()
                store.ensure_usable()
            return value
