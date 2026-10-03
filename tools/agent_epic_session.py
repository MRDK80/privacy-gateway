"""Outer epic continuation: separate checkpoints, cursor and process lock."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from tools import agent_epic_loop as loop
from tools import agent_epic_queue as queue
from tools import agent_orchestrate as workflow


def structural_queue(
    issues: queue.GitHubClient,
    *,
    repository: str,
    epic: int,
    approved_order: Sequence[int],
) -> queue.QueuePlan:
    value = issues.issue(repository, epic)
    children = value.get("subIssues")
    nodes = children.get("nodes") if isinstance(children, Mapping) else None
    if value.get("number") != epic or not isinstance(nodes, list):
        raise loop.PhaseBlocked("ISSUE_MISMATCH")
    tasks = []
    seen = set()
    for node in nodes:
        number = node.get("number") if isinstance(node, Mapping) else None
        if type(number) is not int or number <= 0 or number in seen:
            raise loop.PhaseBlocked("ISSUE_MISMATCH")
        seen.add(number)
        child = issues.issue(repository, number)
        if child.get("number") != number:
            raise loop.PhaseBlocked("ISSUE_MISMATCH")
        tasks.append(child)
    result = queue.plan_queue(epic=epic, approved_order=approved_order, tasks=tasks)
    if result.state not in {"RUN_TASK", "FINAL_GATE"}:
        raise loop.PhaseBlocked(result.machine_code)
    return result


class Session:
    """Callbacks belong to installed trusted code, not runtime config strings."""

    def __init__(
        self,
        *,
        store: loop.CheckpointStore,
        adapter: Callable[[loop.CheckpointStore, int], loop.RuntimeAdapter],
        select: Callable[[], queue.QueuePlan],
        prepare: Callable[[Mapping[str, Any], int, loop.CheckpointStore, int], None],
        reconcile_plan: Callable[[Mapping[str, Any], int, loop.CheckpointStore], str],
        final_resume: Callable[[int], Mapping[str, Any]],
        maximum_tasks: int,
        initial_iterations: int = 0,
    ) -> None:
        self.seed = store
        self.adapter = adapter
        self.select = select
        self.prepare = prepare
        self.reconcile_plan = reconcile_plan
        self.final_resume = final_resume
        self.maximum_tasks = maximum_tasks
        self.initial_iterations = initial_iterations
        self.control = workflow.StateStore(store.directory / "session-control")

    def task_store(self, task: int, seed_task: int) -> loop.CheckpointStore:
        directory = (
            self.seed.directory
            if task == seed_task
            else self.seed.directory / "tasks" / str(task)
        )
        if directory != self.seed.directory and (
            directory.is_symlink() or directory.parent.is_symlink()
        ):
            raise loop.LoopError("UNSAFE_STORAGE")
        return loop.CheckpointStore(directory, self.seed.repository_root)

    def _validate_cursor(
        self, value: Mapping[str, Any], seed: Mapping[str, Any]
    ) -> None:
        if set(value) != {
            "schema_version",
            "repository",
            "epic",
            "policy_sha",
            "seed_task",
            "current_task",
            "completed_tasks",
            "pending_task",
            "mode",
        }:
            raise loop.LoopError("SESSION_CURSOR_INVALID")
        if (
            value["schema_version"] != "1.0"
            or any(
                value[key] != seed[key] for key in ("repository", "epic", "policy_sha")
            )
            or value["seed_task"] != seed["task"]
            or type(value["current_task"]) is not int
            or value["current_task"] <= 0
            or not isinstance(value["completed_tasks"], list)
            or any(
                type(number) is not int or number <= 0
                for number in value["completed_tasks"]
            )
            or len(set(value["completed_tasks"])) != len(value["completed_tasks"])
            or value["mode"] not in {"TASK", "FINAL", "DONE"}
            or (
                value["pending_task"] is not None
                and (
                    type(value["pending_task"]) is not int
                    or value["pending_task"] <= 0
                    or value["pending_task"] in value["completed_tasks"]
                )
            )
        ):
            raise loop.LoopError("SESSION_CURSOR_INVALID")

    def resume(self) -> dict[str, Any]:
        self.control.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with loop.RunnerLock(self.control.directory):
            seed = self.seed.load()
            if seed is None or seed["schema_version"] != "3.0":
                raise loop.PhaseBlocked("PINNED_CHECKPOINT_REQUIRED")
            cursor = self.control.load("cursor") or {
                "schema_version": "1.0",
                "repository": seed["repository"],
                "epic": seed["epic"],
                "policy_sha": seed["policy_sha"],
                "seed_task": seed["task"],
                "current_task": seed["task"],
                "completed_tasks": [],
                "pending_task": None,
                "mode": "TASK",
            }
            self._validate_cursor(cursor, seed)
            for _ in range(self.maximum_tasks + 2):
                iterations = self.initial_iterations + len(cursor["completed_tasks"])
                if cursor["mode"] == "DONE":
                    return {
                        "status": "ROADMAP_DONE",
                        "machine_code": "ALREADY_COMPLETED",
                    }
                if cursor["mode"] == "FINAL":
                    result = dict(self.final_resume(iterations))
                    if result.get("status") == "ROADMAP_DONE":
                        cursor["mode"] = "DONE"
                        self.control.save("cursor", cursor)
                    return result
                current_store = self.task_store(
                    cursor["current_task"], cursor["seed_task"]
                )
                current = current_store.load()
                if current is None:
                    raise loop.LoopError("CHECKPOINT_NOT_FOUND")
                if any(
                    current[key] != seed[key]
                    for key in ("repository", "epic", "base_ref", "policy_sha")
                ):
                    raise loop.LoopError("SESSION_SCOPE_CHANGED")
                if current["phase"] != "NEXT_TASK":
                    outcome = loop.resume(
                        store=current_store,
                        adapter=self.adapter(current_store, iterations),
                    )
                    if outcome.status != "TASK_DONE":
                        return asdict(outcome)
                    current = current_store.load()
                    if current is None:
                        raise loop.LoopError("CHECKPOINT_NOT_FOUND")
                if (
                    current["status"] != "TASK_DONE"
                    or current["pending_phase"] is not None
                ):
                    raise loop.LoopError("SESSION_TASK_NOT_DONE")
                selected = self.select()
                if cursor["current_task"] not in cursor["completed_tasks"]:
                    cursor["completed_tasks"].append(cursor["current_task"])
                    self.control.save("cursor", cursor)
                iterations = self.initial_iterations + len(cursor["completed_tasks"])
                if selected.state == "FINAL_GATE":
                    if cursor["pending_task"] is not None:
                        raise loop.LoopError("ESCALATE_UNKNOWN_OUTCOME")
                    cursor["mode"] = "FINAL"
                    self.control.save("cursor", cursor)
                    continue
                if selected.state != "RUN_TASK" or selected.selected is None:
                    raise loop.PhaseBlocked(selected.machine_code)
                task = selected.selected.number
                if task in cursor["completed_tasks"]:
                    raise loop.PhaseBlocked("CLOSED_TASK_REOPENED")
                next_store = self.task_store(task, cursor["seed_task"])
                if cursor["pending_task"] is not None:
                    if cursor["pending_task"] != task:
                        raise loop.LoopError("ESCALATE_UNKNOWN_OUTCOME")
                    state = self.reconcile_plan(current, task, next_store)
                    if state not in {"APPLIED", "NOT_APPLIED"}:
                        raise loop.LoopError("ESCALATE_UNKNOWN_OUTCOME")
                else:
                    state = "NOT_APPLIED"
                    cursor["pending_task"] = task
                    self.control.save("cursor", cursor)
                if state == "NOT_APPLIED":
                    try:
                        self.prepare(current, task, next_store, iterations)
                    except loop.PhaseBlocked:
                        cursor["pending_task"] = None
                        self.control.save("cursor", cursor)
                        raise
                successor = next_store.load()
                if (
                    successor is None
                    or successor["schema_version"] != "3.0"
                    or successor["task"] != task
                    or successor["phase"] != "PLAN"
                    or successor["base_sha"] != current["merge_sha"]
                    or any(
                        successor[key] != current[key]
                        for key in ("repository", "epic", "base_ref", "policy_sha")
                    )
                ):
                    raise loop.LoopError("SUCCESSOR_IDENTITY_MISMATCH")
                cursor["current_task"] = task
                cursor["pending_task"] = None
                self.control.save("cursor", cursor)
            raise loop.PhaseBlocked("MANDATE_TASK_LIMIT")
