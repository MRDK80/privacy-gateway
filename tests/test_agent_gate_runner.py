"""Actual synthetic pytest sessions; no project genuine, provider or network."""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tools.agent_gate_journal import GateBlocked, Journal
from tools.agent_gate_runner import Runner, boot_identity

REPO = Path(__file__).resolve().parents[1]


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux-only runner V1")


def sample(tmp_path: Path, text: str) -> tuple[Path, dict[str, Any], dict[str, str]]:
    root = tmp_path / "synthetic"
    root.mkdir()
    (root / "test_synthetic.py").write_text(text)
    plan = {
        "version": 1,
        "review": "a" * 64,
        "inventory": ["test_synthetic.py::test_one", "test_synthetic.py::test_two"],
        "units": [["test_synthetic.py::test_one"], ["test_synthetic.py::test_two"]],
        "allowed_skips": [],
    }
    env = {
        "PATH": os.defpath,
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(REPO),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    return root, plan, env


def test_actual_pytest_pause_resume_reuses_only_completed_unit(tmp_path: Path) -> None:
    root, plan, env = sample(
        tmp_path,
        "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n",
    )
    identity = hashlib.sha256((root / "test_synthetic.py").read_bytes()).hexdigest()
    deadline = time.monotonic() + 30
    expiry = time.time() + 30
    launch = {
        "plan": plan,
        "fingerprint": identity,
        "expires_at": expiry,
        "deadline": deadline,
        "boot": boot_identity(),
        "max_collections": 4,
    }
    evidence = tmp_path / "evidence"
    with Journal.create(evidence, launch) as store:
        runner = Runner(
            store, plan, identity=lambda: identity, expires_at=expiry, deadline=deadline
        )
        runner.verify_inventory(root=root, python=Path(sys.executable), environment=env)
        runner.run_unit(0, root=root, python=Path(sys.executable), environment=env)
        runner.pause()
    with Journal.open(evidence) as store:
        runner = Runner(
            store, plan, identity=lambda: identity, expires_at=expiry, deadline=deadline
        )
        assert runner.state.remaining() == [1]
        runner.verify_inventory(root=root, python=Path(sys.executable), environment=env)
        runner.run_unit(1, root=root, python=Path(sys.executable), environment=env)
        assert runner.state.remaining() == []
        dispatches = [e["unit"] for e in store.events if e["kind"] == "dispatch"]
        assert dispatches == [0, 1]


def test_resume_cannot_extend_original_clock(tmp_path: Path) -> None:
    root, plan, _ = sample(tmp_path, "")
    deadline = time.monotonic() + 30
    expiry = time.time() + 30
    with Journal.create(
        tmp_path / "evidence",
        {
            "plan": plan,
            "fingerprint": "a" * 64,
            "expires_at": expiry,
            "deadline": deadline,
            "boot": boot_identity(),
            "max_collections": 1,
        },
    ) as store:
        with pytest.raises(GateBlocked, match="ORIGINAL_CLOCK_BINDING_INVALID"):
            Runner(
                store,
                plan,
                identity=lambda: "a" * 64,
                expires_at=expiry,
                deadline=deadline + 1,
            )


def test_fingerprint_drift_blocks_before_dispatch(tmp_path: Path) -> None:
    root, plan, _ = sample(tmp_path, "")
    deadline = time.monotonic() + 30
    expiry = time.time() + 30
    with Journal.create(
        tmp_path / "evidence",
        {
            "plan": plan,
            "fingerprint": "a" * 64,
            "expires_at": expiry,
            "deadline": deadline,
            "boot": boot_identity(),
            "max_collections": 1,
        },
    ) as store:
        with pytest.raises(GateBlocked, match="FINGERPRINT_CHANGED"):
            Runner(
                store,
                plan,
                identity=lambda: "b" * 64,
                expires_at=expiry,
                deadline=deadline,
            )
        assert store.events == []


def test_reporter_cannot_forge_parent_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from tools import agent_gate_runner as process_runner

    root, value, env = sample(
        tmp_path,
        "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n",
    )
    expiry, deadline = time.time() + 30, time.monotonic() + 30
    evidence = tmp_path / "store"
    with Journal.create(
        evidence,
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
        runner.verify_inventory(root=root, python=Path(sys.executable), environment=env)
        actual_child = process_runner.child
        node = value["inventory"][0]
        events = [
            {"kind": "collected", "nodeid": node},
            {"kind": "started", "nodeid": node},
        ]
        events += [
            {
                "kind": "report",
                "nodeid": node,
                "when": phase,
                "outcome": "passed",
                "duration": 0.01,
                "xfail": False,
            }
            for phase in ("setup", "call", "teardown")
        ]
        events += [
            {"kind": "session", "exit_code": 0},
            {
                "kind": "terminal",
                "exit_code": 0,
                "ownership": "CLEAN",
                "fingerprint": "b" * 64,
            },
        ]
        program = (
            "import os,json\nevents=json.loads(" + repr(json.dumps(events)) + ")\n"
            "f=int(os.environ['PGW_GATE_EVENT_FD']);a=int(os.environ['PGW_GATE_ACK_FD'])\n"
            "for event in events:\n"
            " os.write(f,json.dumps(event).encode()+b'\\n');assert os.read(a,1)==b'+'\n"
            "raise SystemExit(7)\n"
        )

        def injected(command: Any, **kwargs: Any) -> None:
            actual_child([sys.executable, "-c", program], **kwargs)

        monkeypatch.setattr(process_runner, "child", injected)
        with pytest.raises(GateBlocked):
            runner.run_unit(0, root=root, python=Path(sys.executable), environment=env)
    with Journal.open(evidence) as store:
        with pytest.raises(GateBlocked, match="UNIT_OUTCOME_UNKNOWN"):
            Runner(
                store,
                value,
                identity=lambda: "b" * 64,
                expires_at=expiry,
                deadline=deadline,
            )
