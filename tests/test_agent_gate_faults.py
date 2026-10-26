"""Bounded synthetic I/O/IPC failure matrix; never a project genuine run."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tools import agent_gate_journal as storage
from tools import agent_gate_runner as process_runner
from tools.agent_gate_journal import GateBlocked, Journal
from tools.agent_gate_runner import Checkpoints, child

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux evidence V1")


def run_child(
    tmp_path: Path, program: str, receive: Any, tick: Any = lambda: None
) -> None:
    child(
        [sys.executable, "-c", program],
        root=tmp_path,
        environment={"PATH": os.defpath, "HOME": str(tmp_path)},
        ids=[],
        receive=receive,
        tick=tick,
        deadline=time.monotonic() + 5,
        spawn=lambda pid: None,
    )


def test_silent_actual_child_does_not_prevent_timer_snapshots(tmp_path: Path) -> None:
    now = [0.0]
    saves: list[float] = []
    timer = Checkpoints(lambda stamp, late: saves.append(stamp), clock=lambda: now[0])

    def tick() -> None:
        now[0] += 900
        timer.tick()

    run_child(tmp_path, "import time; time.sleep(0.15)", lambda event: None, tick)
    assert len(saves) >= 2
    assert all(value % 900 == 0 for value in saves)


def test_fragmented_report_is_acknowledged_once(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    program = (
        "import os,time; f=int(os.environ['PGW_GATE_EVENT_FD']);"
        "os.write(f,b'{\"kind\":'); time.sleep(.01);"
        "os.write(f,b'\"synthetic\"}\\n');"
        "assert os.read(int(os.environ['PGW_GATE_ACK_FD']),1)==b'+'"
    )
    run_child(tmp_path, program, events.append)
    assert events == [{"kind": "synthetic"}]


def test_child_exit_failure_never_becomes_terminal_success(tmp_path: Path) -> None:
    with pytest.raises(GateBlocked, match="CHILD_TERMINAL_OR_OWNERSHIP_UNKNOWN"):
        run_child(tmp_path, "raise SystemExit(7)", lambda event: None)


def test_unknown_group_prevents_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process_runner, "_group_empty", lambda pid: False)
    with pytest.raises(GateBlocked, match="CHILD_TERMINAL_OR_OWNERSHIP_UNKNOWN"):
        run_child(tmp_path, "pass", lambda event: None)


def test_append_fsync_failure_cannot_fall_back_to_old_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    with Journal.create(root, {}) as store:
        store.checkpoint({})
        with monkeypatch.context() as fault:

            def fail(fd: int) -> None:
                raise OSError("synthetic fsync failure")

            fault.setattr(os, "fsync", fail)
            with pytest.raises(OSError):
                store.append({"kind": "dispatch", "unit": 0, "attempt": 1})
            assert store.poisoned
    with pytest.raises(GateBlocked, match="STORE_COMMITTED_HISTORY_MISSING"):
        Journal.open(root)


def test_snapshot_rename_failure_preserves_recoverable_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    with Journal.create(root, {}) as store:
        store.checkpoint({"generation": 1})
        with monkeypatch.context() as fault:

            def fail(*args: Any, **kwargs: Any) -> None:
                raise OSError("synthetic rename failure")

            fault.setattr(os, "rename", fail)
            with pytest.raises(OSError):
                store.checkpoint({"generation": 2})
    with Journal.open(root) as recovered:
        assert recovered.last_snapshot is not None
        assert recovered.last_snapshot["state"] == {"generation": 1}
        recovered.checkpoint({"generation": 3})


def test_snapshot_rotation_keeps_two_generations_and_entire_journal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    with Journal.create(root, {}) as store:
        store.append({"kind": "synthetic"})
        for number in range(4):
            store.checkpoint({"number": number})
        assert len(list(root.glob("snapshot-*.json"))) == 2
    with Journal.open(root) as recovered:
        assert recovered.events == [{"kind": "synthetic"}]
        assert recovered.last_snapshot is not None
        assert recovered.last_snapshot["state"] == {"number": 3}


def test_event_quota_refuses_before_persisting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Journal.create(tmp_path / "store", {}) as store:
        monkeypatch.setattr(storage, "EVENT_LIMIT", 8)
        with pytest.raises(GateBlocked, match="STORE_EVENT_LIMIT"):
            store.append({"kind": "synthetic"})
        assert store.sequence == 0
        assert (store.root / "journal.jsonl").read_bytes() == b""


def test_nonregular_evidence_is_rejected_before_blocking_open(tmp_path: Path) -> None:
    root = tmp_path / "store"
    with Journal.create(root, {}):
        pass
    (root / "journal.jsonl").unlink()
    getattr(os, "mkfifo")(root / "journal.jsonl", 0o600)
    with pytest.raises(GateBlocked, match="STORE_ENTRY_INVALID"):
        Journal.open(root)


@pytest.mark.parametrize("scenario", ["exit7", "post_guard", "before_spawn"])
def test_controlled_failure_preserves_parent_observed_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    from tools.agent_gate_runner import Runner, boot_identity

    from tests.test_agent_gate_runner import sample

    root, value, env = sample(
        tmp_path,
        "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n",
    )
    expiry, deadline = time.time() + 30, time.monotonic() + 30
    reference: list[Any] = [None]

    def identify() -> str:
        if scenario == "post_guard" and reference[0] is not None:
            unit = reference[0].state.units.get(0, {})
            if unit.get("session") == 0:
                return "c" * 64
        return "b" * 64

    with Journal.create(
        tmp_path / "store",
        {
            "plan": value,
            "fingerprint": "b" * 64,
            "profile": "c" * 64,
            "expires_at": expiry,
            "deadline": deadline,
            "boot": boot_identity(),
            "max_collections": 4,
        },
    ) as store:
        runner = Runner(
            store, value, identity=identify, expires_at=expiry, deadline=deadline
        )
        reference[0] = runner
        runner.verify_inventory(root=root, python=Path(sys.executable), environment=env)
        actual_child = process_runner.child

        def injected(command: Any, **kwargs: Any) -> None:
            if scenario == "exit7":
                command = [sys.executable, "-c", "raise SystemExit(7)"]
            if scenario == "before_spawn":

                def refuse() -> None:
                    raise GateBlocked("RUN_EXPIRED")

                kwargs["before_spawn"] = refuse
            actual_child(command, **kwargs)

        if scenario != "post_guard":
            monkeypatch.setattr(process_runner, "child", injected)
        with pytest.raises(GateBlocked):
            runner.run_unit(0, root=root, python=Path(sys.executable), environment=env)
        observations = [e for e in store.events if e["kind"] == "failure_observation"]
        assert len(observations) == 1
        observation = observations[0]
        assert (observation["scope"], observation["index"], observation["attempt"]) == (
            "unit",
            0,
            1,
        )
        facts = observation["observed"]
        expected_exit = {"exit7": 7, "post_guard": 0, "before_spawn": None}[scenario]
        assert facts["exit_before_cleanup"] == expected_exit
        assert facts["spawned"] == (scenario != "before_spawn")
        assert "CLEAN" not in facts.values()
        assert runner.state.units[0]["terminal"] is None
        with pytest.raises(GateBlocked, match="UNIT_OUTCOME_UNKNOWN"):
            runner.state.remaining()


@pytest.mark.parametrize("scope_name", ["inventory", "quality"])
def test_failure_observation_and_owner_retry_preserve_scope_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope_name: str
) -> None:
    from tools.agent_gate_journal import digest
    from tools.agent_gate_runner import Runner, boot_identity

    from tests.test_agent_gate_runner import sample

    root, value, env = sample(
        tmp_path,
        "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n",
    )
    expiry, deadline = time.time() + 60, time.monotonic() + 60
    with Journal.create(
        tmp_path / "store",
        {
            "plan": value,
            "fingerprint": "b" * 64,
            "profile": "c" * 64,
            "expires_at": expiry,
            "deadline": deadline,
            "boot": boot_identity(),
            "max_collections": 4,
        },
    ) as store:
        runner = Runner(
            store,
            value,
            identity=lambda: "b" * 64,
            expires_at=expiry,
            deadline=deadline,
        )
        actual_child = process_runner.child
        if scope_name == "quality":
            runner.verify_inventory(
                root=root, python=Path(sys.executable), environment=env
            )
            for index in (0, 1):
                runner.run_unit(
                    index, root=root, python=Path(sys.executable), environment=env
                )

        def fail(command: Any, **kwargs: Any) -> None:
            actual_child([sys.executable, "-c", "raise SystemExit(7)"], **kwargs)

        monkeypatch.setattr(process_runner, "child", fail)
        with pytest.raises(GateBlocked):
            if scope_name == "inventory":
                runner.verify_inventory(
                    root=root, python=Path(sys.executable), environment=env
                )
            else:
                runner.run_quality(
                    "ruff", root=root, python=Path(sys.executable), environment=env
                )
        failures = [e for e in store.events if e["kind"] == "failure_observation"]
        assert len(failures) == 1
        assert failures[0]["scope"] == scope_name
        assert failures[0]["observed"]["exit_before_cleanup"] == 7
        monkeypatch.setattr(process_runner, "child", actual_child)
        permit = {
            "version": 1,
            "genesis": digest(store.genesis),
            "plan": digest(value),
            "profile": "c" * 64,
            "source": "b" * 64,
            "frontier": store.frontier,
            "sequence": store.sequence,
            "boot": boot_identity(),
            "deadline": deadline,
            "expires_at": expiry,
            "scope": scope_name,
            "index": None if scope_name == "inventory" else "ruff",
            "previous_attempt": 1,
            "next_attempt": 2,
            "decision": "retry_once",
            "reconciliation_sha256": "d" * 64,
            "owner_authorization_sha256": "e" * 64,
        }
        runner.recover(
            permit,
            digest(permit),
            root=root,
            python=Path(sys.executable),
            environment=env,
        )
        assert len(runner.state.failures) == 1
        if scope_name == "inventory":
            assert runner.state.collections == 2
            assert runner.state.remaining() == [0, 1]
        else:
            assert runner.state.quality_attempts["ruff"] == 2
            assert runner.state.quality["ruff"] is not None
            assert (
                runner.state.units[0]["attempt"]
                == runner.state.units[1]["attempt"]
                == 1
            )
