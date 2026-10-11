"""A stage factory binds durable budgets to fresh private local authority."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from tools import agent_epic_entry as entry
from tools import agent_epic_loop as loop
from tools import agent_epic_runtime_cli as guards
from tools import agent_epic_wait_scope as scope

from tests.test_agent_epic_entry import frozen_managed, managed

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Private runtime is POSIX-only")


def configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    frozen: bool = False,
    timeout_seconds: int | None = None,
) -> tuple[Path, Path]:
    if frozen:
        root, state, _ = frozen_managed(tmp_path, monkeypatch)
        installed = Path(
            guards.private_json(state / "epic-runtime.json")["policy_root"]
        ).parent
    else:
        root, installed, state = managed(tmp_path, monkeypatch)
    config = guards.private_json(state / "epic-runtime.json")
    mandate = guards.private_json(state / "mandate.json")
    profile = {
        "schema_version": "1.0",
        **{
            key: mandate[key]
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        },
        "mandate_digest": config["approved_mandate_digest"],
        "limits": {
            "max_phase_seconds": 600,
            "max_read_attempts": 11,
            "max_ci_polls": 11,
            "max_read_commands": 512,
        },
    }
    path = state / "wait-profile.json"
    path.write_text(json.dumps(profile))
    path.chmod(0o600)
    config.update(
        schema_version="4.0",
        approved_wait_digest=guards.canonical_digest(profile),
        approved_planning_digest=None,
        approved_launch_digest=None,
    )
    (state / "epic-runtime.json").write_text(json.dumps(config))
    if timeout_seconds is not None:
        adapter = guards.private_json(state / "runtime-adapter.json")
        adapter["timeout_seconds"] = timeout_seconds
        (state / "runtime-adapter.json").write_text(json.dumps(adapter))
    # Build a fresh synthetic launch candidate for the new owner profile.
    (state / "launch.json").unlink()
    candidate = entry.prepare(root, installed, state)
    config["approved_launch_digest"] = candidate["candidate_digest"]
    (state / "epic-runtime.json").write_text(json.dumps(config))
    return root, state


def test_root_scope_clamps_and_reuses_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state = configured(tmp_path, monkeypatch)
    value = scope.Scope.for_root(state, root)
    assert value is not None
    reader = value.reader("DISCOVERY")
    assert reader is value.reader("DISCOVERY")
    assert value.frame["deadline_at"] <= time.time() + 600
    value.guard()
    entry.control(root, state, "PAUSED")
    with pytest.raises(guards.DeploymentError):
        value.guard()


def test_root_scope_legacy_has_no_new_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, state = managed(tmp_path, monkeypatch)
    assert scope.Scope.for_root(state, root) is None
    assert not (state / "invocations").exists()


@pytest.mark.parametrize("mode", ["PAUSED", "STOPPED"])
def test_config4_discovery_preserves_control_latch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from tests.test_agent_epic_entry import DiscoveryGitHub, DiscoveryIssues
    from tests.test_agent_epic_snapshot import git

    root, state = configured(tmp_path, monkeypatch)
    git(root, "remote", "add", "origin", "https://github.com/OWNER/repository.git")
    entry.control(root, state, mode)
    before = (state / "launch-control.json").read_bytes()
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "runs": [
                    {
                        "repository": "OWNER/repository",
                        "epic": 248,
                        "state_directory": str(state),
                    }
                ],
            }
        )
    )
    registry.chmod(0o600)
    roadmap = guards.private_json(state / "mandate.json")["roadmap_ref"]
    report = entry.discover(
        root,
        "OWNER/repository",
        248,
        registry,
        github=DiscoveryGitHub(roadmap),
        issues=DiscoveryIssues(),
    )
    assert report["status"] == "DISCOVERED"
    assert (state / "launch-control.json").read_bytes() == before


@pytest.mark.parametrize("drift", ["identity", "policy", "phase", "recovery"])
def test_task_wait_local_drift_cancels_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    import subprocess

    from tools import agent_epic_recovery as recovery

    root, state = configured(tmp_path, monkeypatch)
    store = loop.CheckpointStore(state, root)
    saved = store.load()
    assert saved is not None
    if drift == "recovery":
        recovery.Control(state, root).save(
            {
                "schema_version": "1.0",
                "generation": 0,
                "stopped": True,
                "category": "unknown",
                "authorization_digest": None,
                "attempts": {},
                "pending": None,
                "binding_digest": None,
            }
        )
    value = scope.Scope.for_root(state, root)
    assert value is not None
    reader = value.reader(f"TASK:{saved['task']}:{saved['phase']}")
    reader.now = time.time
    calls: list[int] = []
    argv = (
        "gh",
        "issue",
        "view",
        str(saved["task"]),
        "--repo",
        saved["repository"],
        "--json",
        "number,state",
    )

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    reader.run = run
    assert reader.command(argv) == "{}"
    if drift == "recovery":
        recovery.Control(state, root).stop()
    else:
        saved[
            {"identity": "head_sha", "policy": "policy_sha", "phase": "phase"}[drift]
        ] = "RUN_TASK" if drift == "phase" else "f" * 40
        if drift == "phase":
            saved["completed_phases"] = ["RUN_TASK"]
            saved["pr"] = 278
            saved["pending_phase"] = None
        store.save(saved)
    with pytest.raises((loop.LoopError, loop.PhaseBlocked, guards.DeploymentError)):
        reader.command(argv)
    assert calls == [1]


@pytest.mark.parametrize("mode", ["PAUSED", "STOPPED"])
def test_fresh_read_only_scope_never_grants_effect_or_clears_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    root, state = configured(tmp_path, monkeypatch)
    entry.control(root, state, mode)
    before = (state / "launch-control.json").read_bytes()
    observed = scope.Scope.for_root(state, root, read_only=True)
    assert observed is not None
    observed.guard()
    with pytest.raises(guards.DeploymentError, match="OWNER_" + mode):
        scope.Scope.for_root(state, root)
    assert (state / "launch-control.json").read_bytes() == before
    control = guards.private_json(state / "launch-control.json")
    control["generation"] += 1
    (state / "launch-control.json").write_text(json.dumps(control))
    with pytest.raises(guards.DeploymentError, match="OWNER_CONTROL_CHANGED"):
        observed.guard()


@pytest.mark.parametrize("drift", ["checkpoint", "recovery", "approval", "expiry"])
def test_task_local_drift_during_wait_never_starts_another_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    import subprocess

    from tools import agent_epic_recovery as recovery

    from tests.test_agent_epic_wait import Clock

    root, state = configured(tmp_path, monkeypatch)
    clock = Clock()
    clock.origin = 200
    monkeypatch.setattr(time, "time", clock.now)
    store = loop.CheckpointStore(state, root)
    saved = store.load()
    assert saved is not None
    if drift != "checkpoint":
        control = recovery.Control(state, root)
        control.save(control.load())
        approval = {
            "schema_version": "1.0",
            "checkpoint_digest": recovery.checkpoint_digest(saved),
            "artifact_digest": "sha256:" + "a" * 64,
            "mandate_digest": guards.private_json(state / "epic-runtime.json")[
                "approved_mandate_digest"
            ],
            "owner_identity": "OWNER",
            "issued_at": 100,
            "expires_at": 202 if drift == "expiry" else 300,
            "generation": 0,
            "budgets": {},
            **{key: saved[key] for key in ("repository", "epic", "task", "policy_sha")},
        }
        path = state / "bootstrap-recovery-approval.json"
        path.write_text(json.dumps(approval))
        path.chmod(0o600)
        config = {
            "schema_version": "2.0",
            "approved_recovery_digest": recovery.digest(approval),
        }
        (state / "runtime-task.json").write_text(json.dumps(config))
        (state / "runtime-task.json").chmod(0o600)
    value = scope.Scope.for_root(state, root)
    assert value is not None
    reader = value.reader(f"TASK:{saved['task']}:{saved['phase']}")
    reader.now, reader.monotonic = clock.now, lambda: clock.elapsed
    calls: list[int] = []

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return subprocess.CompletedProcess(
            [], 1, "", 'Get "https://api.github.com": net/http: TLS handshake timeout'
        )

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.elapsed == 2:
            if drift == "checkpoint":
                saved["head_sha"] = "f" * 40
                store.save(saved)
            elif drift == "recovery":
                recovery.Control(state, root).stop()
            elif drift == "approval":
                approval["expires_at"] += 1
                path.write_text(json.dumps(approval))

    reader.run, reader.sleep = run, sleep
    argv = (
        "gh",
        "issue",
        "view",
        str(saved["task"]),
        "--repo",
        saved["repository"],
        "--json",
        "number",
    )
    with pytest.raises((loop.LoopError, loop.PhaseBlocked, guards.DeploymentError)):
        reader.command(argv)
    assert calls == [1]
    assert clock.elapsed == 2
    assert reader.inspect()["requests"]["1"]["attempts"] == 1


@pytest.mark.parametrize("control_state", ["PAUSED", "STOPPED"])
def test_frozen_config4_parent_frame_reaches_read_only_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, control_state: str
) -> None:
    import subprocess
    import sys

    from tools import agent_epic_wait_frame as frames
    from tools.agent_coordinator_handover import handover_digest

    root, state = configured(tmp_path, monkeypatch, frozen=True)
    config = guards.private_json(state / "epic-runtime.json")
    task_config = {key: config[key] for key in guards.CONFIG_KEYS if key in config}
    task_config.update(
        schema_version="1.0",
        approved_handover_digest=handover_digest(
            guards.private_json(state / "handover.json")
        ),
    )
    (state / "runtime-task.json").write_text(json.dumps(task_config))
    (state / "runtime-task.json").chmod(0o600)
    store = loop.CheckpointStore(state, root)
    saved = store.load()
    assert saved is not None
    saved.update(pending_phase="RUN_TASK", status="ESCALATE")
    store.save(saved)
    entry.control(root, state, control_state)
    before = (state / "launch-control.json").read_bytes()
    command = (
        sys.executable,
        str(state / "adapter-bin" / "runtime.py"),
        "reconcile",
        "--phase",
        "RUN_TASK",
        "--state-dir",
        str(state),
        "--repository-root",
        str(root),
    )
    parent = scope.Scope.for_root(state, root, read_only=True)
    assert parent is not None
    value, descriptor = parent.child(command, 30)
    environment = dict(os.environ)
    frames.clear(environment)
    environment.update(frames.environment(value, descriptor))
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            pass_fds=(descriptor,),
            env=environment,
            cwd=root,
        )
    finally:
        os.close(descriptor)
    assert completed.returncode == 0 and completed.stderr == ""
    result = json.loads(completed.stdout)
    assert result == {"state": "UNKNOWN", "receipt": None}
    assert (state / "launch-control.json").read_bytes() == before
    records = list((state / "invocations").glob("*.json"))
    assert len(records) == 2
    from tools.agent_epic_wait import digest

    assert value["parent_frame_digest"] == digest(parent.frame)
    assert value["deadline_at"] <= parent.frame["deadline_at"]


def test_task_completed_phase_reuses_budget_after_supervisor_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from tools.agent_epic_runtime import TaskPhaseRuntime

    root, state = configured(tmp_path, monkeypatch)
    store = loop.CheckpointStore(state, root)
    saved = store.load()
    assert saved is not None
    saved.update(pending_phase="RUN_TASK", status="RUNNING")
    store.save(saved)
    budget = scope.Scope.for_root(state, root)
    assert budget is not None
    engine = TaskPhaseRuntime(
        store=store,
        authority=lambda: pytest.fail("network authority in wait guard"),
        artifacts=lambda: {},
        demo_plan=lambda: {},
        approved_order=(),
        wait_scope=budget,
    )
    argv = (
        "gh",
        "issue",
        "view",
        str(saved["task"]),
        "--repo",
        saved["repository"],
        "--json",
        "number",
    )
    reader = budget.reader(f"TASK:{saved['task']}:RUN_TASK")
    reader.now = time.time
    reader.run = lambda *a, **k: subprocess.CompletedProcess(argv, 0, "{}", "")

    def effect(phase: str) -> dict[str, object]:
        engine._github_read(argv)
        return {"phase": phase}

    monkeypatch.setattr(engine, "_effect", effect)
    assert engine.effect("RUN_TASK") == {"phase": "RUN_TASK"}
    saved.update(
        phase="RUN_TASK", pending_phase=None, completed_phases=["RUN_TASK"], pr=278
    )
    store.save(saved)
    monkeypatch.setattr(engine, "_live", lambda: {"output": engine._github_read(argv)})
    assert engine.live() == {"output": "{}"}
    assert len(reader.inspect()["requests"]) == 2


def test_nested_context_cannot_rebind_checkpoint_during_active_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    root, state = configured(tmp_path, monkeypatch)
    store = loop.CheckpointStore(state, root)
    saved = store.load()
    assert saved is not None
    budget = scope.Scope.for_root(state, root)
    assert budget is not None
    stage = f"TASK:{saved['task']}:{saved['phase']}"
    argv = (
        "gh",
        "issue",
        "view",
        str(saved["task"]),
        "--repo",
        saved["repository"],
        "--json",
        "number",
    )
    with budget.context(stage) as reader:
        reader.now = time.time
        reader.run = lambda *a, **k: subprocess.CompletedProcess(argv, 0, "{}", "")
        reader.command(argv)
        saved["head_sha"] = "f" * 40
        store.save(saved)
        with pytest.raises(loop.LoopError, match="LIVE_IDENTITY_CHANGED"):
            with budget.context(stage):
                pytest.fail("nested guard was rebound")
    assert len(reader.inspect()["requests"]) == 1


@pytest.mark.parametrize("terminal", ["FAILED", "EXPIRED"])
def test_ci_reconciliation_cannot_bypass_original_terminal_task_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal: str
) -> None:
    from tools.agent_epic_runtime import TaskPhaseRuntime

    root, state = configured(tmp_path, monkeypatch)
    store = loop.CheckpointStore(state, root)
    saved = store.load()
    assert saved is not None
    saved.update(
        phase="RUN_TASK", pending_phase="PR_CI", completed_phases=["RUN_TASK"], pr=278
    )
    store.save(saved)
    budget = scope.Scope.for_root(state, root)
    assert budget is not None
    stage = f"TASK:{saved['task']}:PR_CI"
    reader = budget.reader(stage)
    reader.now = time.time
    reader.poll_ci(
        {
            "phase": "pr",
            **{key: saved[key] for key in loop.IDENTITY_KEYS},
            "merge_sha": None,
        },
        lambda: {"machine_code": "CI_FAILED", "status": "blocked"},
    )
    if terminal == "EXPIRED":
        monkeypatch.setattr(time, "time", lambda: 231)
        budget = scope.Scope.for_root(state, root)
        assert budget is not None
        budget.reader(stage).now = time.time
    engine = TaskPhaseRuntime(
        store=store,
        authority=lambda: pytest.fail("wait guard authority"),
        artifacts=lambda: {},
        demo_plan=lambda: {},
        approved_order=(),
        wait_scope=budget,
    )
    monkeypatch.setattr(
        engine, "_assess_once", lambda _: pytest.fail("terminal assessor rerun")
    )
    assert engine.reconcile("PR_CI") == ("NOT_APPLIED", None)
    assert not budget.recorded(f"RECONCILE:{saved['task']}:PR_CI")
    assert budget.reader(stage).inspect()["ci"]["attempts"] == 1


def test_frozen_config4_two_task_supervision_retains_ci_wait_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    from typing import Any

    from tools import agent_epic_queue as queue
    from tools import agent_epic_session_runtime as sessions
    from tools import agent_epic_wait as waits
    from tools.agent_epic_runtime import TaskPhaseRuntime

    from tests.test_agent_epic_runner_e2e import _task
    from tests.test_agent_epic_wait import Clock

    root, state = configured(tmp_path, monkeypatch, frozen=True, timeout_seconds=300)
    entry.control(root, state, "RUNNING")
    clock = Clock()
    clock.origin = int(time.time())
    monkeypatch.setattr(time, "time", clock.now)
    config = guards.private_json(state / "epic-runtime.json")
    seed = loop.CheckpointStore(state, root).load()
    assert seed is not None
    first, second = seed["task"], seed["task"] + 1
    closed: set[int] = set()
    effects: list[str] = []
    failed = False
    original_reader = scope.Scope.reader

    def read(self: scope.Scope, stage: str) -> waits.Reader:
        reader = original_reader(self, stage)
        reader.now, reader.monotonic, reader.sleep = (
            clock.now,
            lambda: clock.elapsed,
            clock.sleep,
        )

        def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal failed
            if (
                argv[1:3] == ["pr", "checks"]
                and int(argv[3]) == seed["pr"] + 1
                and not failed
            ):
                failed = True
                clock.interrupt = True
                return subprocess.CompletedProcess(
                    argv,
                    1,
                    "",
                    'Get "https://api.github.com": net/http: TLS handshake timeout',
                )
            return subprocess.CompletedProcess(argv, 0, "{}", "")

        reader.run = run
        return reader

    monkeypatch.setattr(scope.Scope, "reader", read)

    def command(number: int) -> tuple[str, ...]:
        return (
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            seed["repository"],
            "--json",
            "number,state",
        )

    def session() -> sessions.SessionRuntime:
        budget = scope.Scope.for_root(state, root)
        assert budget is not None
        engine = sessions.SessionRuntime(state, root, config, wait_scope=budget)

        def adapter(store: loop.CheckpointStore, iterations: int) -> TaskPhaseRuntime:
            task = TaskPhaseRuntime(
                store=store,
                authority=lambda: pytest.fail("wait guard called derived authority"),
                artifacts=lambda: {},
                demo_plan=lambda: {},
                approved_order=(first, second),
                wait_scope=budget,
            )

            def live() -> dict[str, Any]:
                current = store.load()
                assert current is not None
                task._github_read(command(current["task"]))
                return {key: current[key] for key in loop.IDENTITY_KEYS}

            def assess(phase: str) -> dict[str, str]:
                current = store.load()
                assert current is not None
                task._github_read(
                    (
                        "gh",
                        "pr",
                        "checks",
                        str(current["pr"]),
                        "--repo",
                        current["repository"],
                        "--json",
                        "name,state",
                    )
                )
                return {"status": "pass", "machine_code": "OK"}

            def effect(phase: str) -> dict[str, Any]:
                current = store.load()
                assert current is not None
                effects.append(f"{current['task']}:{phase}")
                if phase in {"PR_CI", "POST_MERGE"}:
                    task._assess("pr" if phase == "PR_CI" else "post-merge")
                if phase == "TASK_DONE":
                    closed.add(current["task"])
                if phase == "DEMO":
                    return {
                        "status": "CONSUMER_DEMO_READY",
                        "baseline_sha": current["base_sha"],
                        "roadmap_sha": merge(),
                        "reason": None,
                    }
                return {"phase": phase}

            def merge() -> str:
                current = store.load()
                assert current is not None
                return ("d" if current["task"] == first else "e") * 40

            monkeypatch.setattr(task, "_live", live)
            monkeypatch.setattr(task, "_effect", effect)
            monkeypatch.setattr(task, "_assess_once", assess)
            monkeypatch.setattr(task, "merge_sha", merge)
            return task

        def select() -> queue.QueuePlan:
            engine._github_read(command(seed["epic"]))
            return queue.plan_queue(
                epic=seed["epic"],
                approved_order=(first, second),
                tasks=[
                    _task(number, state="CLOSED" if number in closed else "OPEN")
                    for number in (first, second)
                ],
            )

        def prepare(
            previous: Any, number: int, store: loop.CheckpointStore, iterations: int
        ) -> None:
            engine._github_read(command(number))
            store.save(
                {
                    **seed,
                    "task": number,
                    "pr": seed["pr"] + 1,
                    "base_sha": previous["merge_sha"],
                    "head_sha": previous["merge_sha"],
                    "head_ref": f"codex/{number}-synthetic",
                    "phase": "PLAN",
                    "completed_phases": [],
                    "pending_phase": None,
                    "merge_sha": None,
                    "status": "READY",
                }
            )

        def final(iterations: int) -> dict[str, str]:
            assert iterations == 2
            engine._github_read(command(seed["epic"]))
            return {"status": "ROADMAP_DONE", "machine_code": "OK"}

        monkeypatch.setattr(engine, "task_adapter", adapter)
        monkeypatch.setattr(engine, "_select", select)
        monkeypatch.setattr(engine, "_prepare", prepare)
        monkeypatch.setattr(engine, "_final_resume", final)
        return engine

    active = session()
    with pytest.raises(KeyboardInterrupt):
        active.resume()
    assert active.wait_scope is not None
    original = active.wait_scope.reader(f"TASK:{second}:PR_CI").inspect()
    assert original["ci"]["status"] == "WAITING"
    assert original["ci"]["attempts"] == 1
    assert closed == {first}
    interrupted = loop.CheckpointStore(state / "tasks" / str(second), root).load()
    assert interrupted is not None and interrupted["pending_phase"] == "PR_CI"
    clock.interrupt = False
    restarted = session()
    assert restarted.resume()["status"] == "ROADMAP_DONE"
    assert restarted.wait_scope is not None
    final = restarted.wait_scope.reader(f"TASK:{second}:PR_CI").inspect()
    assert final["ci"]["attempts"] == 2
    assert final["deadline_at"] == original["deadline_at"]
    assert final["started_at"] == original["started_at"]
    assert (
        len([item for item in final["requests"].values() if item["attempts"] == 2]) == 1
    )
    assert closed == {first, second}
    assert effects.count(f"{first}:TASK_DONE") == 1
    assert effects.count(f"{second}:PR_CI") == 1
    assert clock.elapsed == 60
    assert not restarted.wait_scope.recorded(f"RECONCILE:{second}:PR_CI")
    stages = {
        guards.private_json(path)["identity"]["stage"]
        for path in (state / "read-waits").glob("*/wait.json")
    }
    assert f"SUCCESSOR:{first}:{second}" in stages
    assert f"TASK:{first}:NEXT_TASK" in stages
    assert f"TASK:{second}:NEXT_TASK" in stages
    assert "FINAL:FINAL_GATE" in stages
    assert "DISCOVERY" not in stages


def test_active_context_survives_nested_entry_and_clears_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, state = configured(tmp_path, monkeypatch)
    budget = scope.Scope.for_root(state, root)
    assert budget is not None
    assert budget.active_stage is None
    with pytest.raises(RuntimeError, match="synthetic"):
        with budget.context("DISCOVERY"):
            assert budget.active_stage == "DISCOVERY"
            with budget.context("DISCOVERY"):
                assert budget.active_stage == "DISCOVERY"
            assert budget.active_stage == "DISCOVERY"
            raise RuntimeError("synthetic")
    assert budget.active_stage is None
