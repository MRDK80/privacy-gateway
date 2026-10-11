"""Two tasks -> final, using actual checkpoint supervisor and isolated state."""

import os
import subprocess
import sys
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX inherited supervisor lock")
@pytest.mark.parametrize(
    "fault", ["malformed", "closed", "wrong_inode", "unlocked", "other_owner"]
)
def test_inherited_supervisor_lock_rejects_invalid_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    session, calls = build(tmp_path)
    directory = session.control.directory
    directory.mkdir(mode=0o700)
    path = directory / "epic-loop.lock"
    path.touch(mode=0o600)
    marker = "invalid"
    descriptor: int | None = None
    owner = loop.RunnerLock(directory) if fault == "other_owner" else None
    if owner is not None:
        owner.__enter__()
    try:
        if fault in {"wrong_inode", "unlocked", "other_owner"}:
            target = tmp_path / "other.lock" if fault == "wrong_inode" else path
            descriptor = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
            marker = str(descriptor)
        elif fault == "closed":
            descriptor = os.open(path, os.O_RDWR)
            marker = str(descriptor)
            os.close(descriptor)
            descriptor = None
        monkeypatch.setenv(loop.SUPERVISOR_LOCK_FD_ENV, marker)
        before = session.seed.path.read_bytes()
        with pytest.raises(loop.LoopError, match="LOCK_FAILED|RUNNER_LOCKED"):
            session.resume()
        assert calls == []
        assert session.seed.path.read_bytes() == before
        assert loop.SUPERVISOR_LOCK_FD_ENV not in os.environ
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass  # Rejected adoption already consumed this duplicate.
        if owner is not None:
            owner.__exit__()


@pytest.mark.skipif(os.name == "nt", reason="POSIX inherited supervisor lock")
def test_inherited_lock_remains_parent_owned_and_is_not_forwarded_to_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, calls = build(tmp_path)
    adapter = session.adapter
    with loop.RunnerLock(session.control.directory) as parent:
        duplicate = os.dup(parent.descriptor())
        os.set_inheritable(duplicate, True)
        monkeypatch.setenv(loop.SUPERVISOR_LOCK_FD_ENV, str(duplicate))

        def checked_adapter(*args: Any) -> loop.RuntimeAdapter:
            assert loop.SUPERVISOR_LOCK_FD_ENV not in os.environ
            assert not os.get_inheritable(duplicate)
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import os; assert 'PGW_SUPERVISOR_LOCK_FD' not in os.environ\n"
                    f"try: os.fstat({duplicate})\n"
                    "except OSError: pass\n"
                    "else: raise AssertionError('inherited supervisor descriptor')",
                ],
                check=False,
                capture_output=True,
                timeout=10,
            )
            assert child.returncode == 0
            return adapter(*args)

        session.adapter = checked_adapter
        assert session.resume()["status"] == "ROADMAP_DONE"
        assert calls
        with pytest.raises(loop.LoopError, match="RUNNER_LOCKED"):
            session.resume()
    assert session.resume()["machine_code"] == "ALREADY_COMPLETED"
