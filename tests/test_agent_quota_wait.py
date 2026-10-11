"""Finite quota polling retains original stage budgets across restart."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_wait as wait
from tools.agent_quota import QuotaError
from tools.agent_quota_wait import QuotaWait, bare_digest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private state")


def setup(
    tmp_path: Path, clock: list[int]
) -> tuple[wait.Reader, dict[str, Any], dict[str, Any]]:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(mode=0o700, exist_ok=True)
    owner: dict[str, Any] = {
        "schema_version": "1.0",
        "repository": "OWNER/repo",
        "epic": 294,
        "roadmap_ref": "roadmap/294-autonomy",
        "policy_sha": "a" * 40,
        "mandate_digest": "sha256:" + "b" * 64,
        "owner_identity": "owner",
        "wait_profile_digest": "sha256:" + "c" * 64,
        "limit_id": "codex",
        "source": {
            "executable": "/synthetic/codex",
            "executable_sha256": "d" * 64,
            "version_digest": "e" * 64,
            "request_timeout_seconds": 3,
        },
        "limits": {
            "poll_seconds": 10,
            "max_polls": 2,
            "max_wait_seconds": 100,
            "max_wakeups": 2,
        },
        "wakeup": {
            "backend": "systemd_user",
            "supervisor_sha256": "f" * 64,
            "host_probe_digest": None,
        },
    }
    identity = {
        k: owner[k]
        for k in (
            "repository",
            "epic",
            "policy_sha",
            "mandate_digest",
            "wait_profile_digest",
            "limit_id",
        )
    }
    identity.update(
        quota_profile_digest=bare_digest(owner),
        source_digest=bare_digest(owner["source"]),
        runtime_config_digest="sha256:" + "1" * 64,
    )
    reader = wait.Reader(
        directory=state,
        root=root,
        identity={
            "repository": "OWNER/repo",
            "epic": 294,
            "policy_sha": "a" * 40,
            "wait_digest": owner["wait_profile_digest"],
            "stage": "TASK:300:RUN_TASK",
        },
        limits={
            "max_phase_seconds": 100,
            "max_read_attempts": 2,
            "max_ci_polls": 2,
            "max_read_commands": 8,
        },
        deadline_at=1100,
        now=lambda: clock[0],
    )
    return reader, owner, identity


def test_restart_preserves_deadline_and_poll_counter(tmp_path: Path) -> None:
    clock = [1000]
    reader, owner, identity = setup(tmp_path, clock)
    current = QuotaWait(
        reader,
        owner,
        identity,
        runtime_deadline=1090,
        now=lambda: clock[0],
        guard=lambda: None,
    )
    paused = current.poll(
        lambda: {"primary": {"usedPercent": 100, "resetsAt": None}, "secondary": None}
    )
    assert paused["next_check_at"] == 1010
    clock[0] = 1010
    reopened = QuotaWait(
        reader,
        owner,
        identity,
        runtime_deadline=1090,
        now=lambda: clock[0],
        guard=lambda: None,
    )
    ready = reopened.poll(
        lambda: {"primary": {"usedPercent": 0, "resetsAt": 1200}, "secondary": None}
    )
    assert ready["status"] == "READY"
    assert ready["attempts"] == 2
    assert ready["deadline_at"] == 1090
    with pytest.raises(QuotaError, match="QUOTA_POLL_BUDGET_EXHAUSTED"):
        reopened.poll(lambda: pytest.fail("no third read"))


def test_orphan_reservation_never_retries(tmp_path: Path) -> None:
    clock = [1000]
    reader, owner, identity = setup(tmp_path, clock)
    current = QuotaWait(
        reader,
        owner,
        identity,
        runtime_deadline=1090,
        now=lambda: clock[0],
        guard=lambda: None,
    )
    with reader._locked():
        value = current._load()
        value.update(status="RESERVED", attempts=1)
        current._save(value)
    with pytest.raises(QuotaError, match="QUOTA_OUTCOME_UNKNOWN"):
        current.poll(lambda: pytest.fail("orphan must not replay"))


def test_poll_guard_cancels_before_source(tmp_path: Path) -> None:
    clock = [1000]
    reader, owner, identity = setup(tmp_path, clock)

    def stopped() -> None:
        raise QuotaError("OWNER_STOPPED")

    current = QuotaWait(
        reader,
        owner,
        identity,
        runtime_deadline=1090,
        now=lambda: clock[0],
        guard=stopped,
    )
    with pytest.raises(QuotaError, match="OWNER_STOPPED"):
        current.poll(lambda: pytest.fail("stopped source"))


@pytest.mark.parametrize("field", ["status", "interrupted", "wakeup"])
def test_malformed_state_enum_is_a_closed_error(tmp_path: Path, field: str) -> None:
    clock = [1000]
    reader, owner, identity = setup(tmp_path, clock)
    current = QuotaWait(
        reader,
        owner,
        identity,
        runtime_deadline=1090,
        now=lambda: clock[0],
        guard=lambda: None,
    )
    value = current.poll(
        lambda: {
            "primary": {"usedPercent": 100, "resetsAt": None},
            "secondary": None,
        }
    )
    if field == "status":
        value["status"] = {}
    elif field == "interrupted":
        value["interrupted"] = {
            "checkpoint_identity_digest": "1" * 64,
            "phase": "RUN_TASK",
            "operation_digest": "2" * 64,
            "outcome": {},
        }
    else:
        value["wakeup"] = {
            "candidate_digest": "1" * 64,
            "arm_id": "2" * 64,
            "attempts": 1,
            "status": {},
            "next_check_at": 1010,
            "unit_digest": "3" * 64,
        }
    with pytest.raises(QuotaError, match="QUOTA_STATE_INVALID"):
        current._validate(value)


@pytest.mark.parametrize("stage", ["version", "app-server"])
def test_persisted_short_deadline_reaps_source_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    import hashlib
    import time

    from tools import agent_quota_source as source

    clock = [1000]
    reader, owner, identity = setup(tmp_path, clock)
    marker = tmp_path / "child.pid"
    executable = tmp_path / "slow-source"
    executable.write_text(
        "#!/usr/bin/python3\nimport os,sys,time\n"
        f"if sys.argv[1] == {('--version' if stage == 'version' else stage)!r}:\n"
        f"    open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
        "    time.sleep(10)\n"
        "else:\n    print('synthetic-version', flush=True)\n"
    )
    executable.chmod(0o700)
    owner["source"].update(
        executable=str(executable),
        executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        version_digest=hashlib.sha256(b"synthetic-version").hexdigest(),
        request_timeout_seconds=10,
    )
    identity.update(
        source_digest=bare_digest(owner["source"]),
        quota_profile_digest=bare_digest(owner),
    )
    current = QuotaWait(
        reader,
        owner,
        identity,
        runtime_deadline=clock[0] + 100,
        now=lambda: clock[0],
        guard=lambda: None,
    )
    retained = current._load()
    retained["deadline_at"] = clock[0] + 1
    current._save(retained)
    original_check = current._check

    def advancing(value: dict[str, Any]) -> int:
        if marker.exists():
            clock[0] += 1
        return original_check(value)

    monkeypatch.setattr(current, "_check", advancing)
    started = time.monotonic()
    with pytest.raises(QuotaError, match="QUOTA_DEADLINE_EXCEEDED"):
        current.poll(
            lambda: source.read(
                owner["source"],
                "codex",
                guard=current.read_guard,
                timeout_seconds=current.read_timeout(),
            )
        )
    assert time.monotonic() - started < 2
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    final = current._load()
    assert final["attempts"] == 1 and final["status"] == "FAILED"
    assert final["deadline_at"] == retained["deadline_at"]
