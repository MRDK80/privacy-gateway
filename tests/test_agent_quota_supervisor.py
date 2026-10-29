"""Final quota polling retains its stage and never refunds a role call."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tools import agent_epic_runtime_cli as guards
from tools import agent_orchestrate as workflow
from tools import agent_quota_supervisor as supervisor
from tools.agent_quota_wait import QuotaWait, bare_digest

from tests.test_agent_epic_bootstrap import checkpoint
from tests.test_agent_quota_wait import setup
from tests.test_agent_quota_wakeup import setup as host_setup

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Private POSIX runtime")


@pytest.mark.parametrize("cancelled", [False, True])
def test_final_poll_arms_then_blocks_without_new_role_call(
    tmp_path: Path,
    cancelled: bool,
) -> None:
    clock = [1000]
    reader, owner, identity = setup(tmp_path, clock)
    reader.identity["stage"] = "FINAL:FINAL_GATE"
    host_dir = tmp_path / "host"
    host_dir.mkdir(mode=0o700)
    host, candidate, service, calls = host_setup(host_dir)
    host.install(candidate, service, approved_digest=candidate["candidate_digest"])
    approval = "sha256:" + "1" * 64
    key = hashlib.sha256(approval.encode()).hexdigest()
    event = {
        "schema_version": "1.0",
        "role": "controller",
        "invocation_digest": "2" * 64,
        "thread_digest": "3" * 64,
        "turn_digest": "4" * 64,
        "error_code": "rateLimitExceeded",
        "backend_digest": bare_digest(owner["source"]),
        "outcome": "UNKNOWN",
    }
    workflow.StateStore(reader.directory).save(
        "quota-final-pause",
        {
            "schema_version": "1.0",
            "approval_digest": approval,
            "reservation_key": key,
            "stage": "FINAL:FINAL_GATE",
            "event": event,
            "status": "PAUSED_RATE_LIMIT",
        },
    )
    reserved = {
        "schema_version": "1.0",
        "invocation_digest": event["invocation_digest"],
        "backend_digest": event["backend_digest"],
        "approval_digest": approval,
        "calls": 1,
        "status": "INTERRUPTED",
        "interruption": event,
    }
    storage = workflow.StateStore(reader.directory / "final-role-reservations")
    storage.save(key, reserved)
    observed: list[int] = []

    def read(*args: Any, **kwargs: Any) -> dict[str, Any]:
        observed.append(clock[0])
        return {
            "primary": {
                "usedPercent": 100 if clock[0] == 1000 else 0,
                "resetsAt": None,
            },
            "secondary": None,
        }

    @contextmanager
    def context(stage: str) -> Iterator[Any]:
        assert stage == "FINAL:FINAL_GATE"
        yield reader

    # Exercise the real durable quota reader and fixed host adapter; deployment
    # authority validation is independently covered by frozen readiness tests.
    engine = supervisor.Supervisor.__new__(supervisor.Supervisor)
    engine.state, engine.owner, engine.candidate = reader.directory, owner, candidate
    engine.config = {"approved_final_digest": approval}
    engine.scope = cast(Any, SimpleNamespace(context=context))
    engine.now, engine.read, engine.host = lambda: clock[0], read, host
    engine._binding = lambda: None  # type: ignore[method-assign]
    engine._identity = lambda: identity  # type: ignore[method-assign]
    assert engine.resume_final()["status"] == "PAUSED_RATE_LIMIT"
    assert len([call for call in calls if "start" in call]) == 1
    if cancelled:
        retained_wait = QuotaWait(
            reader,
            owner,
            identity,
            runtime_deadline=1100,
            now=lambda: clock[0],
            guard=lambda: None,
        )
        armed = retained_wait._load()["wakeup"]
        host.cancel(candidate, armed["arm_id"], armed["unit_digest"])
        assert engine.resume_final()["status"] == "PAUSED_RATE_LIMIT"
        new_arm = retained_wait._load()["wakeup"]
        assert new_arm["attempts"] == 2
        assert new_arm["arm_id"] != armed["arm_id"]
        assert observed == [1000]
    clock[0] = 1010
    result = engine.resume_final()
    assert result == {
        "status": "BLOCKED",
        "machine_code": "FINAL_ROLE_BUDGET_EXHAUSTED",
    }
    assert engine.resume_final() == result
    assert observed == [1000, 1010]
    assert storage.load(key) == reserved
    current = QuotaWait(
        reader,
        owner,
        identity,
        runtime_deadline=1100,
        now=lambda: clock[0],
        guard=lambda: None,
    )
    retained = current._load()
    assert retained["attempts"] == 2 and retained["started_at"] == 1000
    assert retained["deadline_at"] == 1100


def test_wake_waits_for_origin_lock_then_revalidates(tmp_path: Path) -> None:
    import subprocess
    import sys
    import time

    from tools import agent_epic_loop as loop

    directory = tmp_path / "control"
    directory.mkdir(mode=0o700)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,sys; f=open(sys.argv[1],'a+b'); "
                "fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); "
                "sys.stdin.readline()"
            ),
            str(directory / "epic-loop.lock"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdin is not None
        assert child.stdout.readline().strip() == "ready"
        calls: list[str] = []

        def guard() -> None:
            calls.append("guard")
            if len(calls) == 2:
                assert child.stdin is not None
                child.stdin.write("release\n")
                child.stdin.flush()

        with supervisor.wake_lock(
            directory, guard=guard, deadline=time.time() + 3
        ) as owned:
            owned.assert_owned(directory)
            with pytest.raises(loop.LoopError, match="RUNNER_LOCKED"):
                with loop.RunnerLock.supervisor(directory):
                    pytest.fail("duplicate runner")
        assert len(calls) >= 3
    finally:
        child.kill()
        child.wait(timeout=3)


def test_wake_lock_deadline_and_owner_stop_do_not_acquire(tmp_path: Path) -> None:
    import time

    from tools import agent_epic_loop as loop
    from tools.agent_quota import QuotaError

    directory = tmp_path / "control"
    directory.mkdir(mode=0o700)
    with pytest.raises(QuotaError, match="QUOTA_WAKE_LOCK_DEADLINE"):
        with supervisor.wake_lock(
            directory, guard=lambda: None, deadline=time.time() - 1
        ):
            pytest.fail("expired wake")
    assert not (directory / "epic-loop.lock").exists()

    def stopped() -> None:
        raise loop.LoopError("OWNER_CONTROL_CHANGED")

    with pytest.raises(loop.LoopError, match="OWNER_CONTROL_CHANGED"):
        with supervisor.wake_lock(directory, guard=stopped, deadline=time.time() + 1):
            pytest.fail("stopped wake")
    assert not (directory / "epic-loop.lock").exists()


def test_restart_lock_wait_is_clamped_to_original_quota_deadline(
    tmp_path: Path,
) -> None:
    from tools import agent_epic_loop as loop

    root, state = tmp_path / "repo", tmp_path / "state"
    root.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    saved = checkpoint(version="3.0")
    saved.update(policy_sha="a" * 40, pending_phase="RUN_TASK", status="RUNNING")
    loop.CheckpointStore(state, root).save(saved)
    directory = state / "original-wait"
    workflow.StateStore(directory).save("quota", {"deadline_at": 1090})
    scope = SimpleNamespace(
        frame={"deadline_at": 5000},
        reader=lambda stage: SimpleNamespace(directory=directory),
    )
    session = SimpleNamespace(state=state, root=root, wait_scope=scope)
    assert supervisor.wake_deadline(session) == 1090


def test_stop_cancels_owned_arms_across_task_waits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_dir = tmp_path / "host"
    host_dir.mkdir(mode=0o700)
    host, candidate, service, calls = host_setup(host_dir)
    host.install(candidate, service, approved_digest=candidate["candidate_digest"])
    for letter in ("c", "d", "e"):
        host.arm(candidate, letter * 64, 1010, deadline=1090)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(mode=0o700)
    _, owner, _ = setup(profile_dir, [1000])
    workflow.StateStore(host.state).save("epic-runtime", {"schema_version": "5.0"})
    workflow.StateStore(host.state).save(
        "mandate", {"limits": {"max_task_iterations": 2}}
    )
    workflow.StateStore(host.state).save("quota-wakeup-candidate", candidate)
    monkeypatch.setattr(guards, "quota_profile_guard", lambda *args: owner)
    monkeypatch.setattr(guards, "managed_launch_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor, "prepare", lambda *args: (candidate, service))
    monkeypatch.setattr(supervisor, "SystemdUser", lambda *args, **kwargs: host)
    result = supervisor.cancel_owned(host.state, host.root)
    assert result["status"] == "CANCELLED"
    assert len([call for call in calls if "stop" in call]) == 3


@pytest.mark.parametrize(
    "callback", ["correct", "stale", "duplicate", "invalid", "host_unknown"]
)
def test_callback_consumes_only_exact_current_arm(
    tmp_path: Path,
    callback: str,
) -> None:
    from tools import agent_epic_loop as loop
    from tools.agent_quota import QuotaError

    clock = [1000]
    reader, owner, identity = setup(tmp_path, clock)
    host_dir = tmp_path / "host"
    host_dir.mkdir(mode=0o700)
    host, candidate, service, _ = host_setup(host_dir)
    host.install(candidate, service, approved_digest=candidate["candidate_digest"])
    current = QuotaWait(
        reader,
        owner,
        identity,
        runtime_deadline=1100,
        now=lambda: clock[0],
        guard=lambda: None,
    )
    current.poll(
        lambda: {"primary": {"usedPercent": 100, "resetsAt": None}, "secondary": None}
    )
    engine = supervisor.Supervisor.__new__(supervisor.Supervisor)
    engine.owner, engine.candidate, engine.host = owner, candidate, host
    engine.now = lambda: clock[0]
    engine._arm(current)
    old = current._load()["wakeup"]
    # A later pause installs a different ordinal; an old callback cannot consume it.
    if callback == "stale":
        value = current._load()
        value["wakeup"]["status"] = "FIRED"
        current._save(value)
        engine._arm(current)
    engine.wake_arm_id = old["arm_id"] if callback != "invalid" else "INVALID"
    if callback == "duplicate":
        value = current._load()
        value["wakeup"]["status"] = "FIRED"
        current._save(value)
    if callback == "host_unknown":
        (host.state / ("quota-arm-" + old["arm_id"] + ".json")).unlink()
    clock[0] = 1010
    before = current._load()
    with loop.RunnerLock.supervisor(tmp_path / "control"):
        if callback == "correct":
            engine._consume_wake(current)
            assert current._load()["wakeup"]["status"] == "FIRED"
        else:
            with pytest.raises(QuotaError):
                engine._consume_wake(current)
            assert current._load() == before
    assert current._load()["attempts"] == 1


def test_quota_wait_passthrough_noarm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import agent_epic_entry as entry
    from tools import agent_epic_loop as loop

    from tests.test_agent_epic_session_cli import waiting_result

    root, state = tmp_path / "repo", tmp_path / "state"
    root.mkdir()
    state.mkdir(mode=0o700)
    store = loop.CheckpointStore(state, root)
    store.save(checkpoint(version="3.0") | {"policy_sha": "a" * 40})
    before = store.path.read_bytes()
    expected = waiting_result()
    calls: list[str] = []

    def resume(*, supervisor_lock: loop.RunnerLock) -> dict[str, Any]:
        supervisor_lock.assert_owned(state / "session-control")
        calls.append("resume")
        return expected

    session = SimpleNamespace(
        state=state, root=root, wait_scope=None, resume=resume,
        config={"schema_version": "8.0", "approved_quota_digest": "sha256:" + "a" * 64},
    )
    monkeypatch.setattr(entry, "_active_store", lambda *args: store)
    monkeypatch.setattr(
        supervisor, "SystemdUser", lambda *args, **kwargs: pytest.fail("arm")
    )
    monkeypatch.delenv(loop.SUPERVISOR_LOCK_FD_ENV, raising=False)
    assert supervisor.run(session, wake=False) == expected
    assert calls == ["resume"]
    assert store.path.read_bytes() == before
    assert not (state / "quota-final-pause.json").exists()
    assert not list(state.rglob("quota-arm-*.json"))
