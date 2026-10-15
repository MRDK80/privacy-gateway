"""Reconcile terminal observations from canonical retained sources only."""

from __future__ import annotations

from typing import Any

from tools import agent_learning as c
from tools import agent_learning_records as records
from tools.agent_continuation import digest
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_store import Store


def terminal(reader: SourceReader, task: int) -> dict[str, Any]:
    fact = reader.workflow(task)
    original, state = fact.memory, fact.state
    value: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "TERMINAL_WORKFLOW",
        "identity": {
            "repository": reader.owner["repository"],
            "epic": reader.owner["epic"],
            "task": task,
            "run_id": state["run_id"],
            "policy_sha": reader.owner["policy_sha"],
            "head_sha": original["head_sha"],
            "merge_sha": None,
        },
        "outcome": {
            "status": state["status"],
            "machine_codes": sorted(set(state["failures"])),
        },
        "metrics": {
            "findings": state["findings"],
            "repair_iterations": state["repair_iterations"],
            "executor_calls": state["executor_calls"],
            "controller_calls": state["controller_calls"],
            "duration_seconds": original["duration_seconds"],
        },
        "source": {name: None for name in records.SOURCE_FIELDS},
    }
    value["source"]["workflow_digest"] = digest({"state": state, "memory": original})
    value["record_id"] = records.observation_id(value)
    value = records.observation(c.seal(value))
    with reader.ownership(fact.directory):
        with Store(
            fact.directory / "learning",
            reader.root,
            fact.directory,
            reader.owner,
            validators=records.VALIDATORS,
        ) as store:
            store.append("observation", value, "record_id")
    return value


def delivered(reader: SourceReader, task: int, runtime: Any) -> dict[str, Any]:
    """Only the original phase runtime can supply applicable demo assessment."""
    from tools import agent_epic_loop as loop
    from tools import agent_gate
    from tools.agent_epic_runtime import TaskPhaseRuntime
    from tools.agent_learning_delivery import DeliverySources

    reader.guard()
    directory = reader.task_directory(task)
    if (
        not isinstance(runtime, TaskPhaseRuntime)
        or runtime.store.directory != directory
        or runtime.store.repository_root.resolve() != reader.root
    ):
        raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
    with reader.ownership(directory):
        original = terminal(reader, task)
        controller = reader.controller(task)
        if controller[
            "status"
        ] not in c.SUCCESS or not agent_gate.gate_evidence_is_passing(
            controller["gate"]
        ):
            raise c.LearningError("LEARNING_CONTROLLER_SOURCE_INVALID")
        sources = DeliverySources(reader)
        delivery = sources.ledger(task)
        pr_ci = sources.ci(delivery["head_sha"], event="pull_request")
        post_ci = sources.ci(delivery["merge_sha"], event="push")
        demo = dict(runtime._demo(require_completion=True))
        if not loop._valid_demo_receipt(demo, delivery["merge_sha"]):
            raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
        value = {
            key: item
            for key, item in original.items()
            if key not in {"record_id", "record_digest"}
        }
        value.update(
            kind="TASK_DELIVERY",
            identity={
                **original["identity"],
                "head_sha": delivery["head_sha"],
                "merge_sha": delivery["merge_sha"],
            },
            outcome={"status": "TASK_DONE", "machine_codes": []},
            source={
                **original["source"],
                "gate_digest": digest(controller["gate"]),
                "controller_digest": controller["source_digest"],
                "delivery_digest": delivery["source_digest"],
                "pr_ci_digest": digest(pr_ci),
                "post_ci_digest": digest(post_ci),
                "demo_digest": digest(demo),
            },
        )
        value["record_id"] = records.observation_id(value)
        value = records.observation(c.seal(value))
        with Store(
            directory / "learning",
            reader.root,
            directory,
            reader.owner,
            validators=records.VALIDATORS,
        ) as store:
            store.append("observation", value, "record_id")
        return value
