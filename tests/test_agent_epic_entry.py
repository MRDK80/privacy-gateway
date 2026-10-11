"""Managed launch/control tests use synthetic private state only."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_entry as entry
from tools import agent_epic_install as install
from tools import agent_epic_runtime_cli as guards

from tests.test_agent_epic_install import runtime_fixture
from tests.test_agent_epic_loop import (
    active_runtime_namespace as active_runtime_namespace,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX private runtime")


def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    root, installed, state = runtime_fixture(tmp_path, monkeypatch, epic=True)
    from tools import agent_epic_loop as loop

    loop.CheckpointStore(state, root).save(
        guards.private_json(state / "checkpoint.json")
    )
    config = guards.private_json(state / "epic-runtime.json")
    config.update(schema_version="2.0", approved_launch_digest=None)
    (state / "epic-runtime.json").write_text(json.dumps(config))
    adapter = guards.private_json(state / "runtime-adapter.json")
    adapter["epic_command"] += [
        "epic_resume",
        "--state-dir",
        str(state),
        "--repository-root",
        str(root),
    ]
    (state / "runtime-adapter.json").write_text(json.dumps(adapter))
    report = entry.prepare(root, installed, state)
    config["approved_launch_digest"] = report["candidate_digest"]
    (state / "epic-runtime.json").write_text(json.dumps(config))
    return root, installed, state


def test_candidate_is_not_self_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, installed, state = managed(tmp_path, monkeypatch)
    config = guards.private_json(state / "epic-runtime.json")
    config["approved_launch_digest"] = None
    (state / "epic-runtime.json").write_text(json.dumps(config))
    with pytest.raises(guards.DeploymentError, match="LAUNCH_APPROVAL_MISMATCH"):
        guards.managed_launch_guard(state, root, allow_initial=True)
    assert not (state / "launch-control.json").exists()


@pytest.mark.parametrize("fault", ["argv", "timeout", "owner", "extra"])
def test_launch_binding_rejects_config_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root, _, state = managed(tmp_path, monkeypatch)
    if fault in {"argv", "timeout"}:
        path = state / "runtime-adapter.json"
        value = guards.private_json(path)
        if fault == "argv":
            value["epic_command"].append("--injected")
        else:
            value["timeout_seconds"] += 1
    else:
        path = state / "launch.json"
        value = guards.private_json(path)
        value["owner_identity" if fault == "owner" else "extra"] = "UNTRUSTED"
    path.write_text(json.dumps(value))
    with pytest.raises(guards.DeploymentError):
        guards.managed_launch_guard(state, root, allow_initial=True)


def test_pause_and_terminal_stop_cover_direct_child_and_legacy_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, state = managed(tmp_path, monkeypatch)
    entry.control(root, state, "PAUSED")
    child = state / "tasks" / "253"
    child.mkdir(mode=0o700, parents=True)
    child_config = {
        "approved_mandate_digest": guards.private_json(state / "epic-runtime.json")[
            "approved_mandate_digest"
        ]
    }
    for name, value in (
        ("mandate.json", guards.private_json(state / "mandate.json")),
        ("runtime-task.json", child_config),
    ):
        path = child / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    with pytest.raises(guards.DeploymentError, match="OWNER_PAUSED"):
        guards.managed_launch_guard(child, root)
    guards.managed_launch_guard(child, root, read_only=True)
    entry.control(root, state, "STOPPED")
    with pytest.raises(guards.DeploymentError, match="OWNER_STOPPED"):
        entry.control(root, state, "RUNNING")
    config = guards.private_json(state / "epic-runtime.json")
    config.pop("approved_launch_digest")
    config["schema_version"] = "1.0"
    (state / "epic-runtime.json").write_text(json.dumps(config))
    with pytest.raises(guards.DeploymentError):
        guards.managed_launch_guard(child, root)
    assert guards.private_json(state / "launch-control.json")["state"] == "STOPPED"


class DiscoveryGitHub:
    def __init__(self, roadmap: str) -> None:
        self.roadmap = roadmap
        self.extra = False
        self.ancestry = "ahead"

    def repository(self, repository: str) -> dict[str, object]:
        return {"nameWithOwner": repository, "defaultBranchRef": {"name": "main"}}

    def branches(self, repository: str) -> dict[str, str]:
        result = {"main": "a" * 40, self.roadmap: "b" * 40}
        if self.extra:
            result[self.roadmap + "-ambiguous"] = "c" * 40
        return result

    def compare(self, repository: str, base: str, head: str) -> str:
        return self.ancestry


class DiscoveryIssues:
    def issue(self, repository: str, number: int) -> dict[str, object]:
        from tests.test_agent_epic_runner_e2e import _task

        if number == 248:
            return {
                "number": 248,
                "state": "OPEN",
                "subIssues": {"nodes": [{"number": 253}]},
            }
        return _task(number)


def test_discovery_requires_unique_structural_binding_and_never_exposes_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_agent_epic_snapshot import git

    root, _, state = managed(tmp_path, monkeypatch)
    git(root, "remote", "add", "origin", "https://github.com/OWNER/repository.git")
    registry = tmp_path / "registry.json"
    value: dict[str, Any] = {
        "schema_version": "1.0",
        "runs": [
            {
                "repository": "OWNER/repository",
                "epic": 248,
                "state_directory": str(state),
            }
        ],
    }
    registry.write_text(json.dumps(value))
    registry.chmod(0o600)
    roadmap = guards.private_json(state / "mandate.json")["roadmap_ref"]
    client = DiscoveryGitHub(roadmap)
    report = entry.discover(
        root, "OWNER/repository", 248, registry, github=client, issues=DiscoveryIssues()
    )
    assert report["selected_task"] == 253
    assert str(tmp_path) not in json.dumps(report)
    client.extra = True
    with pytest.raises(guards.DeploymentError, match="ROADMAP_AMBIGUOUS"):
        entry.discover(
            root,
            "OWNER/repository",
            248,
            registry,
            github=client,
            issues=DiscoveryIssues(),
        )
    client.extra = False
    value["runs"] *= 2
    registry.write_text(json.dumps(value))
    with pytest.raises(guards.DeploymentError, match="PRIVATE_RUN_AMBIGUOUS"):
        entry.discover(
            root,
            "OWNER/repository",
            248,
            registry,
            github=client,
            issues=DiscoveryIssues(),
        )


def test_second_runner_and_unknown_pending_are_not_restarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import agent_epic_loop as loop

    root, _, state = managed(tmp_path, monkeypatch)
    lock = state / "entry-run"
    lock.mkdir(mode=0o700)
    with loop.RunnerLock(lock), pytest.raises(loop.LoopError, match="RUNNER_LOCKED"):
        entry.run(root, state, "resume")
    assert not (state / "launch-control.json").exists()


@pytest.mark.parametrize("action", ["start", "resume"])
def test_direct_supervisor_lock_preserves_pause_before_chat_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    from tools import agent_epic_loop as loop

    root, _, state = managed(tmp_path, monkeypatch)
    entry.control(root, state, "PAUSED")
    before = (state / "launch-control.json").read_bytes()
    store = loop.CheckpointStore(state, root)
    saved = store.load()
    assert saved is not None
    saved.update(pending_phase="RUN_TASK", status="RUNNING")
    store.save(saved)
    checkpoint_before = store.path.read_bytes()
    monkeypatch.setattr(
        install,
        "readiness",
        lambda *args, **kwargs: pytest.fail("occupied supervisor readiness"),
    )
    monkeypatch.setattr(
        loop.CommandRuntimeAdapter,
        "reconcile",
        lambda *args: pytest.fail("occupied supervisor reconciliation"),
    )
    calls: list[str] = []

    def resumed(self: loop.CommandRuntimeAdapter) -> dict[str, Any]:
        calls.append("resume")
        return {"status": "BLOCKED", "machine_code": "RUNNER_LOCKED"}

    monkeypatch.setattr(loop.CommandRuntimeAdapter, "resume_epic", resumed)
    lock = state / "session-control"
    lock.mkdir(mode=0o700)
    with loop.RunnerLock(lock):
        with pytest.raises(loop.LoopError, match="RUNNER_LOCKED"):
            entry.run(root, state, action)
        assert (state / "launch-control.json").read_bytes() == before
        assert store.path.read_bytes() == checkpoint_before
        assert calls == []


def test_entry_invokes_actual_checkpoint_supervisor_with_synthetic_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import agent_epic_install as install
    from tools import agent_epic_loop as loop
    from tools import agent_epic_queue as queue
    from tools.agent_epic_session import Session

    from tests.test_agent_epic_runner_e2e import _task

    root, installed, state = managed(tmp_path, monkeypatch)
    original_readiness = install.readiness
    monkeypatch.setattr(
        install,
        "readiness",
        lambda *args, **kwargs: original_readiness(
            *args,
            **kwargs,
            namespace_probe=lambda: None,
            github_probe=lambda *args: None,
        ),
    )
    calls: list[str] = []
    closed = False
    store = loop.CheckpointStore(state, root)

    class Adapter:
        def live(self) -> dict[str, object]:
            saved = store.load()
            assert saved is not None
            return {key: saved[key] for key in loop.IDENTITY_KEYS}

        def merge_sha(self) -> str:
            return "c" * 40

        def reconcile(self, phase: str) -> tuple[str, None]:
            return "NOT_APPLIED", None

        def effect(self, phase: str) -> dict[str, object]:
            nonlocal closed
            calls.append(phase)
            if phase == "TASK_DONE":
                closed = True
            if phase == "DEMO":
                return {
                    "status": "CONSUMER_DEMO_READY",
                    "baseline_sha": "a" * 40,
                    "roadmap_sha": "c" * 40,
                    "reason": None,
                }
            return {"phase": phase}

    session = Session(
        store=store,
        adapter=lambda *args: Adapter(),
        select=lambda: queue.plan_queue(
            epic=248,
            approved_order=(253,),
            tasks=[_task(253, state="CLOSED" if closed else "OPEN")],
        ),
        prepare=lambda *args: pytest.fail("unexpected successor"),
        reconcile_plan=lambda *args: "UNKNOWN",
        final_resume=lambda count: {
            "status": "BLOCKED",
            "machine_code": "OWNER_FINAL_APPROVAL_REQUIRED",
        },
        maximum_tasks=1,
    )

    def invoke(
        adapter: loop.CommandRuntimeAdapter, command: object
    ) -> dict[str, object]:
        assert command == adapter.epic_command
        assert adapter.supervisor_lock is not None
        monkeypatch.setenv(
            loop.SUPERVISOR_LOCK_FD_ENV,
            str(os.dup(adapter.supervisor_lock.descriptor())),
        )
        result = session.resume()
        return {
            "schema_version": "1.0",
            "status": result["status"],
            "machine_code": result["machine_code"],
            "result": result,
        }

    monkeypatch.setattr(loop.CommandRuntimeAdapter, "_run", invoke)
    report = entry.run(root, state, "start")
    assert calls == list(loop.PHASES[1:])
    assert closed and report["machine_code"] == "OWNER_FINAL_APPROVAL_REQUIRED"
    assert str(tmp_path) not in json.dumps(report)


def probed_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import agent_epic_install as install

    original = install.readiness
    monkeypatch.setattr(
        install,
        "readiness",
        lambda *args, **kwargs: original(
            *args,
            **kwargs,
            namespace_probe=lambda: None,
            github_probe=lambda *args: None,
        ),
    )


@pytest.mark.parametrize("failure", ["expired", "revoked"])
def test_resume_revalidates_mandate_without_clearing_pause_or_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import time

    from tools import agent_epic_loop as loop

    root, _, state = managed(tmp_path, monkeypatch)
    entry.control(root, state, "PAUSED")
    before_control = (state / "launch-control.json").read_bytes()
    before_checkpoint = loop.CheckpointStore(state, root).path.read_bytes()
    if failure == "expired":
        monkeypatch.setattr(time, "time", lambda: 100000)
    else:
        mandate = guards.private_json(state / "mandate.json")
        mandate["revoked"] = True
        (state / "mandate.json").write_text(json.dumps(mandate))
    monkeypatch.setattr(
        loop.CommandRuntimeAdapter,
        "resume_epic",
        lambda self: pytest.fail("unauthorized restart"),
    )
    with pytest.raises(guards.DeploymentError):
        entry.run(root, state, "resume")
    assert (state / "launch-control.json").read_bytes() == before_control
    assert loop.CheckpointStore(state, root).path.read_bytes() == before_checkpoint


def test_owner_pause_during_start_validation_is_never_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import agent_epic_install as install
    from tools import agent_epic_loop as loop

    root, _, state = managed(tmp_path, monkeypatch)
    probed_readiness(monkeypatch)
    probe = install.readiness

    def pause_during_probe(*args: Any, **kwargs: Any) -> dict[str, Any]:
        entry.control(root, state, "PAUSED")
        return probe(*args, **kwargs)

    monkeypatch.setattr(install, "readiness", pause_during_probe)
    monkeypatch.setattr(
        loop.CommandRuntimeAdapter,
        "resume_epic",
        lambda self: pytest.fail("pause bypass"),
    )
    with pytest.raises(guards.DeploymentError, match="OWNER_CONTROL_CHANGED"):
        entry.run(root, state, "start")
    assert guards.private_json(state / "launch-control.json")["state"] == "PAUSED"


def test_unknown_pending_is_preserved_without_another_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import agent_epic_loop as loop

    root, _, state = managed(tmp_path, monkeypatch)
    entry.control(root, state, "PAUSED")
    probed_readiness(monkeypatch)
    store = loop.CheckpointStore(state, root)
    saved = store.load()
    assert saved is not None
    saved.update(
        phase="PLAN",
        completed_phases=[],
        pending_phase="RUN_TASK",
        status="RUNNING",
    )
    store.save(saved)
    before = store.path.read_bytes()
    monkeypatch.setattr(
        loop.CommandRuntimeAdapter, "reconcile", lambda *args: ("UNKNOWN", None)
    )
    monkeypatch.setattr(
        loop.CommandRuntimeAdapter,
        "resume_epic",
        lambda self: pytest.fail("unknown replay"),
    )
    with pytest.raises(guards.DeploymentError, match="ESCALATE_UNKNOWN_OUTCOME"):
        entry.run(root, state, "resume")
    assert store.path.read_bytes() == before
    assert guards.private_json(state / "launch-control.json")["state"] == "PAUSED"


def test_background_request_is_an_explicit_blocker() -> None:
    with pytest.raises(guards.DeploymentError, match="BACKGROUND_WAKEUP_UNVERIFIED"):
        entry.execute(
            "start",
            Path("/synthetic/repo"),
            Path("/synthetic/state"),
            repository=None,
            epic=None,
            registry=None,
            installation=None,
            background=True,
        )


def frozen_managed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    import datetime
    import time

    from tools import agent_epic_delivery as delivery
    from tools import agent_epic_install as install
    from tools import agent_epic_loop as loop
    from tools.agent_coordinator_handover import handover_digest

    from tests.test_agent_epic_snapshot import git

    root, _, state = managed(tmp_path, monkeypatch)
    source = Path(guards.__file__).parent
    for original in source.glob("*.py"):
        (root / "tools" / original.name).write_bytes(original.read_bytes())
    git(root, "add", "tools")
    git(root, "commit", "-qm", "synthetic complete frozen entry")
    sha = git(root, "rev-parse", "HEAD")
    installation = tmp_path / "complete-installation"
    install.prepare(root, sha, "OWNER/repository", installation)
    now = int(datetime.datetime.now(datetime.UTC).timestamp())
    monkeypatch.setattr(time, "time", lambda: now)
    mandate = guards.private_json(state / "mandate.json")
    mandate.update(policy_sha=sha, issued_at=now - 1, expires_at=now + 3600)
    mandate["approval"]["approved_at"] = now
    approved = delivery.mandate_digest(mandate)
    mandate["approval"]["mandate_digest"] = approved
    (state / "mandate.json").write_text(json.dumps(mandate))
    seed = guards.private_json(state / "checkpoint.json")
    seed["policy_sha"] = sha
    (state / "checkpoint.json").write_text(json.dumps(seed))
    handover = guards.private_json(state / "handover.json")
    handover["trusted_policy"]["policy_sha"] = sha
    (state / "handover.json").write_text(json.dumps(handover))
    plans = guards.private_json(state / "approved-plans.json")
    plans["initial_handover_digest"] = handover_digest(handover)
    (state / "approved-plans.json").write_text(json.dumps(plans))
    config = guards.private_json(state / "epic-runtime.json")
    config.update(
        approved_plans_digest=install.digest(plans),
        policy_root=str(installation / "policy"),
        approved_mandate_digest=approved,
        started_at=now,
        approved_launch_digest=None,
    )
    (state / "epic-runtime.json").write_text(json.dumps(config))
    script = state / "adapter-bin" / "runtime.py"
    script.write_bytes((installation / "runtime.py").read_bytes())
    (state / "launch.json").unlink()
    report = entry.prepare(root, installation, state)
    config["approved_launch_digest"] = report["candidate_digest"]
    (state / "epic-runtime.json").write_text(json.dumps(config))
    store = loop.CheckpointStore(state, root)
    saved = store.load()
    assert saved is not None
    saved["policy_sha"] = sha
    store.save(saved)
    return root, state, script


@pytest.mark.parametrize("tampered", [False, True])
def test_frozen_cli_validates_bytes_before_importing_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tampered: bool
) -> None:
    import subprocess
    import sys

    root, state, script = frozen_managed(tmp_path, monkeypatch)
    if tampered:
        target = (
            Path(guards.private_json(state / "epic-runtime.json")["policy_root"])
            / "tools"
            / "agent_epic_entry.py"
        )
        target.write_text("print('SYNTHETIC_UNTRUSTED_IMPORT')\n")
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "entry",
            "--action",
            "status",
            "--state-dir",
            str(state),
            "--repository-root",
            str(root),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0 and not completed.stderr
    assert str(tmp_path) not in completed.stdout
    assert "SYNTHETIC_UNTRUSTED_IMPORT" not in completed.stdout
    result = json.loads(completed.stdout)
    assert result["status"] == ("BLOCKED" if tampered else "STATUS")
    assert result["machine_code"] == ("UNTRUSTED_RUNTIME_COMMAND" if tampered else "OK")


@pytest.mark.usefixtures("active_runtime_namespace")
@pytest.mark.parametrize("action", ["start", "resume"])
def test_managed_supervisor_shares_canonical_ownership_through_bubblewrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    import subprocess

    from tools import agent_epic_loop as loop
    from tools.agent_epic_session import Session

    root, state, _ = frozen_managed(tmp_path, monkeypatch)
    if action == "resume":
        entry.control(root, state, "PAUSED")
    initial_generation = (
        guards.private_json(state / "launch-control.json")["generation"]
        if action == "resume"
        else -1
    )
    monkeypatch.setattr(
        loop.CommandRuntimeAdapter,
        "_bubblewrap",
        staticmethod(lambda: Path("/usr/bin/bwrap")),
    )
    probed_readiness(monkeypatch)
    store = loop.CheckpointStore(state, root)
    saved = store.load()
    assert saved is not None
    # An already completed synthetic cursor reaches the actual installed Session
    # without remote services or any effect authority exercised by the fixture.
    session = Session(
        store=store,
        adapter=lambda *args: pytest.fail("unexpected direct effect"),
        select=lambda: pytest.fail("unexpected queue read"),
        prepare=lambda *args: pytest.fail("unexpected successor"),
        reconcile_plan=lambda *args: "UNKNOWN",
        final_resume=lambda *args: pytest.fail("unexpected final effect"),
        maximum_tasks=1,
    )
    session.control.save(
        "cursor",
        {
            "schema_version": "1.0",
            **{key: saved[key] for key in ("repository", "epic", "policy_sha")},
            "seed_task": saved["task"],
            "current_task": saved["task"],
            "completed_tasks": [],
            "pending_task": None,
            "mode": "DONE",
        },
    )
    observed: list[str] = []
    readiness = install.readiness
    original_popen = subprocess.Popen

    def reject_direct(label: str) -> None:
        with pytest.raises(loop.LoopError, match="RUNNER_LOCKED"):
            session.resume()
        observed.append(label)

    def validate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        reject_direct("validation")
        report = readiness(*args, **kwargs)
        assert report["status"] == "READY", report
        return report

    def launch(*args: Any, **kwargs: Any) -> Any:
        if args[0][0] != "/usr/bin/bwrap":
            return original_popen(*args, **kwargs)
        reject_direct("child launch")
        child = original_popen(*args, **kwargs)
        reject_direct("child running")
        return child

    monkeypatch.setattr(install, "readiness", validate)
    monkeypatch.setattr(subprocess, "Popen", launch)
    before_checkpoint = store.path.read_bytes()
    report = entry.run(root, state, action)
    assert store.path.read_bytes() == before_checkpoint
    assert (
        guards.private_json(state / "launch-control.json")["generation"]
        == initial_generation + 1
    )
    assert report["status"] == "ROADMAP_DONE"
    assert report["machine_code"] == "ALREADY_COMPLETED"
    assert observed == ["validation", "child launch", "child running"]
    assert session.resume()["status"] == "ROADMAP_DONE"
