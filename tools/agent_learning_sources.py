"""Retained gate facts and read-only historical phase factories for learning."""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from tools import agent_consumer_demo as demo
from tools import agent_epic_loop as loop
from tools import agent_learning as c
from tools import agent_learning_records as records
from tools.agent_continuation import digest
from tools.agent_epic_final_runtime import digest as completion_digest
from tools.agent_epic_runtime import TaskPhaseRuntime
from tools.agent_epic_runtime_cli import learning_private_json, secure_directory
from tools.agent_epic_session_runtime import SessionRuntime
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_store import Store


def environment(reader: SourceReader, *, planning: bool = False) -> dict[str, Any]:
    reader.guard()
    backend = (
        None
        if planning
        else reader._json(reader.seed / "continuation-profile.json")["backend_digest"]
    )
    return c.environment(
        {
            "os": "linux" if sys.platform == "linux" else "windows",
            "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
            "gate_profile": "repository-full",
            "backend_digest": backend,
            "tool_digests": [],
        }
    )


def gate(reader: SourceReader, task: int) -> list[dict[str, Any]]:
    fact = reader.workflow(task)
    target = fact.directory / "learning-source/gate"
    limits = reader.owner["limits"]
    with reader.ownership(fact.directory):
        if not target.exists() and not target.is_symlink():
            return []
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise c.LearningError("LEARNING_STORAGE_UNSAFE")
        secure_directory(target.parent, reader.root)
        secure_directory(target, reader.root)
        result: list[dict[str, Any]] = []
        size = 0
        for count, path in enumerate(target.iterdir(), 1):
            if count > limits["max_records"] or path.suffix != ".json":
                raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
            c.hash_value(path.stem)
            size += path.lstat().st_size
            if size > limits["max_store_bytes"]:
                raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
            value = records.gate(
                learning_private_json(path, limits["max_record_bytes"])
            )
            if path.stem != value["invocation_digest"]:
                raise c.LearningError("LEARNING_GATE_SOURCE_INVALID")
            if value["run_id"] != fact.state["run_id"]:
                continue
            if value["policy_sha"] != reader.owner["policy_sha"] or value[
                "environment_digest"
            ] != digest(environment(reader)):
                raise c.LearningError("LEARNING_GATE_SOURCE_INVALID")
            result.append(value)
        return sorted(result, key=lambda value: value["invocation_digest"])


class HistoricalDemoRuntime:
    """Historical adapter; upstream original delivery/CI proof remains required."""

    def __init__(
        self, reader: SourceReader, runtime: TaskPhaseRuntime, task: int
    ) -> None:
        self.reader, self.runtime, self.task = reader, runtime, task
        self.store = runtime.store
        self.github = runtime.github
        self.authority = runtime.authority
        self._stage = runtime._stage

    def _demo(self, *, require_completion: bool = False) -> Mapping[str, object]:
        if require_completion is not True:
            raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
        reader, runtime = self.reader, self.runtime
        reader.guard()
        saved = loop.validate_checkpoint(reader._json(self.store.path))
        plan = demo.validate_plan(runtime.demo_plan())
        if (
            saved["repository"] != reader.owner["repository"]
            or saved["epic"] != reader.owner["epic"]
            or saved["task"] != self.task
            or "DEMO" not in saved["completed_phases"]
            or saved["merge_sha"] is None
            or any(plan[key] != saved[key] for key in ("repository", "epic", "task"))
            or plan["roadmap_ref"] != saved["base_ref"]
            or plan["roadmap_sha"] != saved["merge_sha"]
            or saved["policy_sha"] != reader.owner["policy_sha"]
        ):
            raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
        contract = reader.contract(self.task)
        if (
            contract.issue != saved["task"]
            or contract.epic != saved["epic"]
            or contract.base_ref != plan["roadmap_ref"]
        ):
            raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
        repo = saved["repository"]
        baseline = runtime.github.ref_sha(repo, plan["baseline_ref"])
        current = runtime.github.ref_sha(repo, plan["roadmap_ref"])
        if baseline != plan["baseline_sha"]:
            raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
        if current == saved["merge_sha"]:
            # Preserve the live observer's exact contract and bootstrap ordering.
            return runtime._demo(require_completion=True)
        c.hash_value(current, size=40)
        for sha in (baseline, saved["merge_sha"]):
            c.hash_value(sha, size=40)
            if reader._git("cat-file", "-t", sha) != b"commit\n":
                raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
        reader._git("merge-base", "--is-ancestor", baseline, saved["merge_sha"])
        raw = runtime._github_read(
            (
                "gh",
                "api",
                f"repos/{repo}/compare/{saved['merge_sha']}...{current}",
            )
        )
        try:
            comparison = json.loads(raw)
        except (ValueError, TypeError):
            raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID") from None
        if (
            not isinstance(comparison, dict)
            or comparison.get("status") != "ahead"
            or not isinstance(comparison.get("base_commit"), dict)
            or comparison["base_commit"].get("sha") != saved["merge_sha"]
            or not isinstance(comparison.get("merge_base_commit"), dict)
            or comparison["merge_base_commit"].get("sha") != saved["merge_sha"]
        ):
            raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
        value = dict(asdict(demo.assess(plan)))
        if plan["consumer_visible"]:
            if runtime.demo_completion is None:
                raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
            _, _, context = runtime.authority()
            expected = {
                key: plan[key]
                for key in ("repository", "epic", "task", "baseline_sha", "roadmap_sha")
            }
            expected.update(
                schema_version="1.0",
                plan_digest=completion_digest(plan),
                owner_identity=context.owner_identity,
                completed_by_owner=True,
            )
            completion = dict(runtime.demo_completion())
            if (
                completion != expected
                or completion.get("completed_by_owner") is not True
            ):
                raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
        directory = self.store.directory / "learning"
        if not directory.exists() or directory.is_symlink():
            raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
        secure_directory(directory, reader.root)
        with Store(
            directory,
            reader.root,
            self.store.directory,
            reader.owner,
            validators=records.VALIDATORS,
        ) as store:
            anchors = [
                item["record"]
                for item in store.records()
                if item["kind"] == "observation"
                and item["record"]["kind"] == "TASK_DELIVERY"
                and all(
                    item["record"]["identity"][key] == saved[key]
                    for key in (
                        "repository",
                        "epic",
                        "task",
                        "policy_sha",
                        "head_sha",
                        "merge_sha",
                    )
                )
            ]
        if (
            len(anchors) != 1
            or anchors[0]["outcome"] != {"status": "TASK_DONE", "machine_codes": []}
            or anchors[0]["source"]["demo_digest"] != digest(value)
        ):
            raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
        reader.guard()
        if (
            runtime.github.ref_sha(repo, plan["baseline_ref"]) != baseline
            or runtime.github.ref_sha(repo, plan["roadmap_ref"]) != current
            or reader._json(self.store.path) != saved
        ):
            raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
        return value


def historical_runtime(
    reader: SourceReader, session: SessionRuntime, task: int
) -> HistoricalDemoRuntime:
    reader.guard()
    if (
        not isinstance(session, SessionRuntime)
        or session.state != reader.seed
        or session.root.resolve() != reader.root
        or dict(session.config) != reader.config
    ):
        raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
    directory = reader.task_directory(task)
    store = loop.CheckpointStore(directory, reader.root)
    saved = loop.validate_checkpoint(reader._json(store.path))
    if saved["merge_sha"] is None:
        raise c.LearningError("LEARNING_DELIVERY_SOURCE_MISSING")

    def plan() -> dict[str, Any]:
        value = copy.deepcopy(dict(session._recipe(task)["demo_plan"]))
        value["roadmap_sha"] = saved["merge_sha"]
        return value

    runtime = TaskPhaseRuntime(
        store=store,
        authority=lambda: session._fresh_authority(session.config["task_iterations"]),
        artifacts=lambda: {},
        demo_plan=plan,
        demo_completion=lambda: reader._json(directory / "demo-completion.json"),
        approved_order=tuple(reader.config["approved_order"]),
        wait_scope=session.wait_scope,
    )
    runtime.continuation_enabled = True
    return HistoricalDemoRuntime(reader, runtime, task)
