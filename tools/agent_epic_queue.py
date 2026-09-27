#!/usr/bin/env python3
"""Build a read-only, structurally verified queue plan for one epic."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Protocol, cast


class GitHubError(Exception):
    """Controlled GitHub transport or response error."""


class GitHubClient(Protocol):
    def issue(self, repository: str, number: int) -> dict[str, Any]: ...


class GhClient:
    """Minimal read-only adapter for structural issue facts."""

    def __init__(self, *, run: Any = subprocess.run) -> None:
        self._run = run

    def issue(self, repository: str, number: int) -> dict[str, Any]:
        try:
            completed = self._run(
                [
                    "gh",
                    "issue",
                    "view",
                    str(number),
                    "--repo",
                    repository,
                    "--json",
                    "number,title,state,url,parent,subIssues,blockedBy",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise GitHubError from error
        if completed.returncode != 0:
            raise GitHubError
        try:
            value = json.loads(completed.stdout)
        except (TypeError, ValueError) as error:
            raise GitHubError from error
        if not isinstance(value, dict):
            raise GitHubError
        return cast(dict[str, Any], value)


@dataclass(frozen=True)
class Task:
    number: int
    title: str
    state: str
    url: str
    blocked_by: tuple[int, ...]


@dataclass(frozen=True)
class QueuePlan:
    state: str
    machine_code: str
    epic: int
    selected: Task | None
    remaining: tuple[Task, ...]


@dataclass(frozen=True)
class FollowUpPlan:
    classification: str
    action: str
    parent: int | None
    provenance: str


def _number(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError
    return value


def _task(value: dict[str, Any], epic: int) -> Task:
    parent = value.get("parent")
    if not isinstance(parent, dict) or parent.get("number") != epic:
        raise ValueError("parent")
    number = _number(value.get("number"))
    title, state, url = value.get("title"), value.get("state"), value.get("url")
    blocked_by = value.get("blockedBy")
    if (
        not isinstance(title, str)
        or not isinstance(state, str)
        or not isinstance(url, str)
        or not isinstance(blocked_by, dict)
        or not isinstance(blocked_by.get("nodes"), list)
    ):
        raise ValueError("shape")
    blockers: list[int] = []
    for blocker in blocked_by["nodes"]:
        if not isinstance(blocker, dict):
            raise ValueError("blocker")
        if str(blocker.get("state", "")).upper() == "OPEN":
            blockers.append(_number(blocker.get("number")))
    return Task(number, title[:500], state.upper(), url[:1000], tuple(blockers))


def _has_cycle(tasks: dict[int, Task]) -> bool:
    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(number: int) -> bool:
        if number in visiting:
            return True
        if number in visited:
            return False
        visiting.add(number)
        for blocker in tasks[number].blocked_by:
            if blocker in tasks and tasks[blocker].state == "OPEN" and visit(blocker):
                return True
        visiting.remove(number)
        visited.add(number)
        return False

    return any(visit(number) for number in tasks)


def _result(code: str, epic: int, tasks: Iterable[Task]) -> QueuePlan:
    remaining = tuple(tasks)
    state = "BLOCKED"
    if code in {"ORDER_INCOMPLETE", "ORDER_CONFLICT"}:
        state = "NEEDS_DECISION"
    if code == "QUEUE_EXHAUSTED":
        state = "FINAL_GATE"
    return QueuePlan(state, code, epic, None, remaining)


def plan_queue(
    *, epic: int, approved_order: Sequence[int], tasks: Sequence[dict[str, Any]]
) -> QueuePlan:
    """Recalculate a queue from a fresh structural snapshot without side effects."""
    try:
        epic = _number(epic)
        parsed = tuple(_task(value, epic) for value in tasks)
    except ValueError:
        return _result("PARENT_MISMATCH", epic, ())
    by_number = {task.number: task for task in parsed}
    if len(by_number) != len(parsed):
        return _result("DUPLICATE_TASK", epic, parsed)
    if len(set(approved_order)) != len(approved_order):
        return _result("ORDER_CONFLICT", epic, parsed)
    open_numbers = {task.number for task in parsed if task.state == "OPEN"}
    if not open_numbers:
        return _result("QUEUE_EXHAUSTED", epic, ())
    if not open_numbers.issubset(set(approved_order)):
        return _result("ORDER_INCOMPLETE", epic, parsed)
    if _has_cycle(by_number):
        return _result("DEPENDENCY_CYCLE", epic, parsed)
    positions = {number: index for index, number in enumerate(approved_order)}
    for task in parsed:
        for blocker in task.blocked_by:
            blocker_is_later = positions.get(blocker, -1) > positions[task.number]
            if blocker in open_numbers and blocker_is_later:
                return _result("ORDER_CONFLICT", epic, parsed)
    remaining = tuple(
        by_number[number]
        for number in approved_order
        if number in open_numbers
    )
    ready = tuple(task for task in remaining if not set(task.blocked_by) & open_numbers)
    if not ready:
        return _result("NO_READY_TASK", epic, remaining)
    return QueuePlan("RUN_TASK", "READY_TASK", epic, ready[0], remaining)


def plan_follow_up(
    *,
    current_epic: int,
    parent: int | None,
    known_epics: Sequence[int],
    provenance: str,
    existing_provenance: Sequence[str],
    created_count: int,
    creation_limit: int,
) -> FollowUpPlan:
    """Classify one proposed follow-up; never create or link an issue."""
    if not provenance or provenance in existing_provenance:
        return FollowUpPlan("duplicate", "NO_OP_DUPLICATE", parent, provenance)
    if parent is None or parent not in set(known_epics):
        return FollowUpPlan("needs-owner", "NEEDS_DECISION", parent, provenance)
    classification = (
        "blocking-current-epic"
        if parent == current_epic
        else "nonblocking-other-epic"
    )
    if creation_limit < 0 or created_count >= creation_limit:
        return FollowUpPlan(classification, "NEEDS_DECISION_LIMIT", parent, provenance)
    return FollowUpPlan(classification, "PLAN_CREATE_AND_LINK", parent, provenance)


def render_json(value: QueuePlan | FollowUpPlan) -> str:
    return json.dumps(asdict(value), ensure_ascii=False, sort_keys=True) + "\n"
