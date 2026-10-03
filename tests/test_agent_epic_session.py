"""Two tasks -> final, using actual checkpoint supervisor and isolated state."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_loop as loop
from tools import agent_epic_queue as queue
from tools.agent_epic_session import Session

from tests.test_agent_epic_bootstrap import checkpoint
from tests.test_agent_epic_runner_e2e import _task


def build(tmp_path: Path, *, uncertain: bool = False) -> tuple[Session, list[str]]:
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    seed = checkpoint(version="3.0", pr=278) | {"policy_sha": "a" * 40}
    store.save(seed)
    closed: set[int] = set()
    calls: list[str] = []

    class Adapter:
        def __init__(self, active: loop.CheckpointStore) -> None:
            self.store = active
            self.bound: dict[str, Any] | None = None

        def live(self) -> dict[str, Any]:
            saved = self.store.load()
            assert saved is not None
            return self.bound or {key: saved[key] for key in loop.IDENTITY_KEYS}

        def merge_sha(self) -> str:
            saved = self.store.load()
            assert saved is not None
            return "c" * 40 if saved["task"] == 274 else "d" * 40

        def reconcile(self, phase: str) -> tuple[str, None]:
            return "NOT_APPLIED", None

        def effect(self, phase: str) -> dict[str, Any]:
            saved = self.store.load()
            assert saved is not None
            calls.append(f"{saved['task']}:{phase}")
            if phase == "RUN_TASK" and saved["pr"] is None:
                self.bound = self.live() | {"pr": 279, "head_sha": "e" * 40}
                return {"phase": phase, "delivery_identity": self.bound}
            if phase == "DEMO":
                return {
                    "status": "CONSUMER_DEMO_READY",
                    "baseline_sha": "a" * 40,
                    "roadmap_sha": self.merge_sha(),
                    "reason": None,
                }
            if phase == "TASK_DONE":
                closed.add(saved["task"])
            return {"phase": phase}

    def select() -> queue.QueuePlan:
        return queue.plan_queue(
            epic=248,
            approved_order=(274, 275),
            tasks=[
                _task(task, state="CLOSED" if task in closed else "OPEN")
                for task in (274, 275)
            ],
        )

    def prepare(
        previous: Mapping[str, Any], task: int, target: loop.CheckpointStore, count: int
    ) -> None:
        calls.append("prepare")
        assert count == 1 and previous["status"] == "TASK_DONE"
        if uncertain:
            raise loop.LoopError("SYNTHETIC_CRASH")
        target.save(
            checkpoint(version="3.0")
            | {
                "policy_sha": previous["policy_sha"],
                "task": task,
                "base_sha": previous["merge_sha"],
                "head_sha": previous["merge_sha"],
                "head_ref": "codex/275-task",
            }
        )

    def finish(count: int) -> dict[str, Any]:
        assert count == 2 and closed == {274, 275}
        calls.append("final")
        return {"status": "ROADMAP_DONE", "machine_code": "OK"}

    session = Session(
        store=store,
        adapter=lambda active, count: Adapter(active),
        select=select,
        prepare=prepare,
        reconcile_plan=lambda *args: "UNKNOWN",
        final_resume=finish,
        maximum_tasks=3,
    )
    return session, calls


def test_one_resume_advances_two_distinct_checkpoints_then_final(
    tmp_path: Path,
) -> None:
    session, calls = build(tmp_path)
    assert session.resume()["status"] == "ROADMAP_DONE"
    first = session.seed.load()
    second = session.task_store(275, 274).load()
    assert first is not None and first["task"] == 274 and first["status"] == "TASK_DONE"
    assert (
        second is not None and second["task"] == 275 and second["status"] == "TASK_DONE"
    )
    assert second["base_sha"] == first["merge_sha"]
    assert (
        calls.index("274:NEXT_TASK")
        < calls.index("prepare")
        < calls.index("275:RUN_TASK")
    )
    before = list(calls)
    assert session.resume()["machine_code"] == "ALREADY_COMPLETED"
    assert calls == before


def test_uncertain_successor_is_not_blindly_prepared_again(tmp_path: Path) -> None:
    session, calls = build(tmp_path, uncertain=True)
    with pytest.raises(loop.LoopError, match="SYNTHETIC_CRASH"):
        session.resume()
    with pytest.raises(loop.LoopError, match="ESCALATE_UNKNOWN_OUTCOME"):
        session.resume()
    assert calls.count("prepare") == 1


def test_session_lock_excludes_second_supervisor(tmp_path: Path) -> None:
    session, calls = build(tmp_path)
    with loop.RunnerLock(session.control.directory):
        with pytest.raises(loop.LoopError, match="RUNNER_LOCKED"):
            session.resume()
    assert calls == []
