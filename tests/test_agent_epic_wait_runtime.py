"""Real finite CI reservations drive pinned assessments without effects."""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from tools import agent_epic_final_runtime as final_runtime
from tools import agent_epic_loop as loop
from tools import agent_epic_queue as queue
from tools import agent_epic_session_runtime as sessions
from tools import agent_epic_wait as waits
from tools import agent_gate
from tools.agent_epic_wait_scope import Scope

from tests.test_agent_coordinator_delivery import _green_checks
from tests.test_agent_epic_final_runtime import build as final_build
from tests.test_agent_epic_runtime import build as task_build
from tests.test_agent_epic_session_runtime import build as session_build
from tests.test_agent_epic_wait import Clock

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Private runtime is POSIX-only")


class Budget:
    def __init__(self, tmp_path: Path) -> None:
        self.directory = tmp_path / "bounded"
        self.directory.mkdir(mode=0o700)
        self.clock = Clock()
        self.readers: dict[str, waits.Reader] = {}

    @contextmanager
    def context(self, stage: str) -> Iterator[waits.Reader]:
        yield self.reader(stage)

    def recorded(self, stage: str) -> bool:
        return (
            stage in self.readers
            and (self.readers[stage].directory / "wait.json").exists()
        )

    def reader(self, stage: str) -> waits.Reader:
        if stage not in self.readers:
            self.readers[stage] = waits.Reader(
                directory=self.directory,
                root=self.directory.parent / "repo",
                identity={
                    "repository": "OWNER/repository",
                    "epic": 248,
                    "policy_sha": "a" * 40,
                    "wait_digest": "sha256:" + "b" * 64,
                    "stage": stage,
                },
                limits={
                    "max_phase_seconds": 600,
                    "max_read_attempts": 3,
                    "max_ci_polls": 3,
                    "max_read_commands": 20,
                },
                deadline_at=100600,
                now=self.clock.now,
                monotonic=lambda: self.clock.elapsed,
                sleep=self.clock.sleep,
            )
        return self.readers[stage]


def test_task_ci_pending_uses_finite_saved_polls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, github = task_build(tmp_path, monkeypatch, "RUN_TASK")
    budget = Budget(tmp_path)
    engine.wait_scope = cast(Scope, budget)
    polls: list[int] = []

    def checks(*args: Any) -> list[dict[str, str]]:
        polls.append(1)
        return (
            [value | {"state": "PENDING"} for value in _green_checks()]
            if len(polls) == 1
            else _green_checks()
        )

    monkeypatch.setattr(github, "pr_checks", checks)
    assert engine.effect("PR_CI") == {"phase": "PR_CI"}
    assert len(polls) == 2
    assert budget.clock.elapsed == 60
    saved = next(iter(budget.readers.values())).inspect()
    assert saved["ci"]["attempts"] == 2
    assert saved["ci"]["status"] == "READY"


def test_final_ci_pending_uses_same_guarded_assessment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, request = final_build(tmp_path, monkeypatch)
    budget = Budget(tmp_path)
    engine.wait_scope = cast(Scope, budget)
    polls: list[int] = []

    def checks(*args: Any) -> list[dict[str, str]]:
        polls.append(1)
        return (
            [value | {"state": "PENDING"} for value in _green_checks()]
            if len(polls) == 1
            else _green_checks()
        )

    monkeypatch.setattr(engine.github, "pr_checks", checks)
    assert engine.gate(request).status == "PASS"
    assert len(polls) == 2
    assert budget.clock.elapsed == 60
    assert budget.readers["FINAL:PR_CI"].inspect()["ci"]["attempts"] == 2


def test_final_ci_polls_reuse_cached_gate_and_controller_with_fresh_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, request = final_build(tmp_path, monkeypatch)
    evidence = engine._prepare(request)
    key = hashlib.sha256(engine.approved_digest.encode()).hexdigest()
    engine.storage.save(key, dict(evidence))
    monkeypatch.setattr(
        engine, "_prepare", final_runtime.FinalRuntime._prepare.__get__(engine)
    )
    monkeypatch.setattr(
        agent_gate,
        "trusted_snapshot_session",
        lambda **kwargs: pytest.fail("cached final gate/controller was rerun"),
    )
    budget = Budget(tmp_path)
    engine.wait_scope = cast(Scope, budget)
    facts: list[int] = []
    polls: list[int] = []

    def fresh(request: Any) -> tuple[str, str, list[int]]:
        facts.append(1)
        return request.roadmap_sha, request.main_sha, []

    def checks(*args: Any) -> list[dict[str, str]]:
        polls.append(1)
        return (
            [value | {"state": "PENDING"} for value in _green_checks()]
            if len(polls) == 1
            else _green_checks()
        )

    monkeypatch.setattr(engine, "_facts", fresh)
    monkeypatch.setattr(engine.github, "pr_checks", checks)
    assert engine.gate(request).status == "PASS"
    assert len(facts) == len(polls) == 2
    assert budget.clock.elapsed == 60


def test_final_terminal_failure_does_not_enter_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, request = final_build(tmp_path, monkeypatch)
    budget = Budget(tmp_path)
    engine.wait_scope = cast(Scope, budget)
    values = _green_checks()
    values[0]["state"] = "FAILURE"
    values[1]["state"] = "PENDING"
    monkeypatch.setattr(engine.github, "pr_checks", lambda *args: values)
    assert engine.gate(request).machine_code == "ROADMAP_PR_CI_FAILED"
    assert budget.clock.elapsed == 0
    assert budget.readers["FINAL:PR_CI"].inspect()["ci"]["status"] == "FAILED"


def test_successor_stage_is_scoped_even_when_preparation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, previous, target = session_build(tmp_path, monkeypatch)
    engine.config = {**engine.config, "schema_version": "4.0"}
    previous["task"] = 250
    before = engine._read_stage
    monkeypatch.setattr(
        engine,
        "_approved_recipe",
        lambda _: (_ for _ in ()).throw(loop.PhaseBlocked("SYNTHETIC")),
    )
    with pytest.raises(loop.PhaseBlocked, match="SYNTHETIC"):
        engine.prepare(previous, 251, target, 1)
    assert engine._read_stage == before


def test_queue_stage_uses_saved_current_task_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, _ = task_build(tmp_path, monkeypatch, "TASK_DONE")
    engine = sessions.SessionRuntime.__new__(sessions.SessionRuntime)
    engine.state, engine.root, engine.store = (
        task.store.directory,
        task.store.repository_root,
        task.store,
    )
    engine.config = {"schema_version": "4.0", "approved_order": []}
    engine.identity = {"repository": "OWNER/repository", "epic": 248}
    engine._read_stage = "SUCCESSOR:251:274"
    budget = Budget(tmp_path)
    engine.wait_scope = cast(Scope, budget)
    engine.issues = queue.GhClient(read=engine._github_read)
    argv = (
        "gh",
        "issue",
        "view",
        "248",
        "--repo",
        "OWNER/repository",
        "--json",
        "number",
    )

    def selected(*args: Any, **kwargs: Any) -> Any:
        target = budget.reader("TASK:274:NEXT_TASK")
        target.run = lambda *a, **k: subprocess.CompletedProcess(argv, 0, "{}", "")
        engine._github_read(argv)
        return queue.QueuePlan("FINAL_GATE", "OK", 248, None, ())

    monkeypatch.setattr(sessions, "structural_queue", selected)
    assert engine.select().state == "FINAL_GATE"
    assert set(budget.readers) == {"TASK:274:NEXT_TASK"}


def test_final_reconciliation_phase_is_restored_from_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = final_build(tmp_path, monkeypatch)
    budget = Budget(tmp_path)
    engine.wait_scope = cast(Scope, budget)
    argv = (
        "gh",
        "issue",
        "view",
        "248",
        "--repo",
        "OWNER/repository",
        "--json",
        "number",
    )
    reader = budget.reader("RECONCILE:FINAL:POST_MERGE")
    reader.run = lambda *a, **k: subprocess.CompletedProcess(argv, 0, "{}", "")
    monkeypatch.setattr(
        engine.transport, "reconcile", lambda _: (engine._github_read(argv), None)
    )
    before = engine._wait_phase
    engine._reconcile_transport(engine._request("close_epic", pr=278, merge="c" * 40))
    assert set(budget.readers) == {"RECONCILE:FINAL:POST_MERGE"}
    assert engine._wait_phase == before
    assert engine._reconciling is False


def test_read_only_ci_reconcile_retains_original_task_wait_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = task_build(tmp_path, monkeypatch, "RUN_TASK")
    budget = Budget(tmp_path)
    engine.wait_scope = cast(Scope, budget)
    original = budget.reader("TASK:274:PR_CI")
    budget.clock.interrupt = True
    argv = (
        "gh",
        "pr",
        "checks",
        "278",
        "--repo",
        "OWNER/repository",
        "--json",
        "name,state",
    )
    original.run = lambda *a, **k: subprocess.CompletedProcess(
        argv, 1, "", 'Get "https://api.github.com": net/http: TLS handshake timeout'
    )

    def assess(phase: str) -> dict[str, str]:
        engine._github_read(argv)
        return {"status": "pass", "machine_code": "OK"}

    monkeypatch.setattr(engine, "_assess_once", assess)
    with pytest.raises(KeyboardInterrupt):
        engine.effect("PR_CI")
    assert original.inspect()["ci"]["status"] == "WAITING"
    budget.clock.interrupt = False
    original.run = lambda *a, **k: subprocess.CompletedProcess(argv, 0, "[]", "")
    separate = budget.reader("RECONCILE:274:PR_CI")
    separate.run = original.run
    assert engine.reconcile("PR_CI") == ("APPLIED", {"phase": "PR_CI"})
    assert original.inspect()["ci"]["attempts"] == 2
    assert original.inspect()["requests"]["1"]["attempts"] == 2
    assert separate.inspect()["ci"] is None
