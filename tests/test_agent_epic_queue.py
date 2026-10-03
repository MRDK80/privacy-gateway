"""Bounded read-only epic queue and follow-up planning tests (#250)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "agent_epic_queue.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_epic_queue", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


queue = _load_module()


def _task(
    number: int,
    *,
    state: str = "OPEN",
    parent: int | None = 248,
    blocked_by: tuple[int, ...] = (),
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"synthetic task {number}",
        "state": state,
        "url": f"https://example.invalid/issues/{number}",
        "parent": None if parent is None else {"number": parent},
        "blockedBy": {
            "nodes": [
                {
                    "number": blocker,
                    "state": "OPEN",
                    "url": f"https://example.invalid/issues/{blocker}",
                }
                for blocker in blocked_by
            ]
        },
    }


def test_approved_order_selects_one_ready_task() -> None:
    result = queue.plan_queue(
        epic=248,
        approved_order=(249, 250, 251),
        tasks=(_task(249, state="CLOSED"), _task(250), _task(251, blocked_by=(250,))),
    )
    assert result.state == "RUN_TASK"
    assert result.machine_code == "READY_TASK"
    assert result.selected is not None and result.selected.number == 250
    assert [item.number for item in result.remaining] == [250, 251]


def test_new_current_epic_task_is_included_after_recalculation() -> None:
    before = queue.plan_queue(epic=248, approved_order=(249,), tasks=(_task(249),))
    after = queue.plan_queue(
        epic=248,
        approved_order=(249, 260),
        tasks=(_task(249, state="CLOSED"), _task(260)),
    )
    assert before.selected is not None and before.selected.number == 249
    assert after.selected is not None and after.selected.number == 260


def test_unknown_parent_and_dependency_cycle_fail_closed() -> None:
    unknown = queue.plan_queue(
        epic=248, approved_order=(250,), tasks=(_task(250, parent=None),)
    )
    assert unknown.machine_code == "PARENT_MISMATCH"
    cycle = queue.plan_queue(
        epic=248,
        approved_order=(250, 251),
        tasks=(_task(250, blocked_by=(251,)), _task(251, blocked_by=(250,))),
    )
    assert cycle.machine_code == "DEPENDENCY_CYCLE"


def test_unapproved_or_conflicting_order_requires_decision() -> None:
    missing = queue.plan_queue(
        epic=248, approved_order=(250,), tasks=(_task(250), _task(251))
    )
    assert missing.state == "NEEDS_DECISION"
    assert missing.machine_code == "ORDER_INCOMPLETE"
    conflict = queue.plan_queue(
        epic=248,
        approved_order=(251, 250),
        tasks=(_task(250), _task(251, blocked_by=(250,))),
    )
    assert conflict.machine_code == "ORDER_CONFLICT"


def test_completion_requires_no_open_structural_children() -> None:
    result = queue.plan_queue(
        epic=248,
        approved_order=(249, 250),
        tasks=(_task(249, state="CLOSED"), _task(250, state="CLOSED")),
    )
    assert result.state == "FINAL_GATE"
    assert result.machine_code == "QUEUE_EXHAUSTED"


def test_follow_up_classification_is_bounded_and_deduplicated() -> None:
    current = queue.plan_follow_up(
        current_epic=248,
        parent=248,
        known_epics=(248, 300),
        provenance="sha256:one",
        existing_provenance=(),
        created_count=0,
        creation_limit=2,
    )
    assert current.classification == "blocking-current-epic"
    assert current.action == "PLAN_CREATE_AND_LINK"

    other = queue.plan_follow_up(
        current_epic=248,
        parent=300,
        known_epics=(248, 300),
        provenance="sha256:two",
        existing_provenance=(),
        created_count=0,
        creation_limit=2,
    )
    assert other.classification == "nonblocking-other-epic"

    unknown = queue.plan_follow_up(
        current_epic=248,
        parent=None,
        known_epics=(248, 300),
        provenance="sha256:three",
        existing_provenance=(),
        created_count=0,
        creation_limit=2,
    )
    assert unknown.classification == "needs-owner"
    assert unknown.action == "NEEDS_DECISION"

    duplicate = queue.plan_follow_up(
        current_epic=248,
        parent=248,
        known_epics=(248,),
        provenance="sha256:one",
        existing_provenance=("sha256:one",),
        created_count=0,
        creation_limit=2,
    )
    assert duplicate.action == "NO_OP_DUPLICATE"

    limited = queue.plan_follow_up(
        current_epic=248,
        parent=248,
        known_epics=(248,),
        provenance="sha256:new",
        existing_provenance=(),
        created_count=2,
        creation_limit=2,
    )
    assert limited.action == "NEEDS_DECISION_LIMIT"


def test_gh_adapter_is_read_only() -> None:
    calls: list[tuple[str, ...]] = []

    def run(command: Any, **_kwargs: Any) -> Any:
        calls.append(tuple(command))
        return type("Completed", (), {"returncode": 0, "stdout": "{}"})()

    client = queue.GhClient(run=run)
    client.issue("owner/repo", 248)
    forbidden = {"create", "edit", "close", "reopen", "merge", "delete"}
    assert not any(forbidden.intersection(command) for command in calls)
