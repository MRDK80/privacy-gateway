"""Explicit owner disposition retains prior PASS and consumes one attempt."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tools.agent_gate_journal import GateBlocked, Journal, digest
from tools.agent_gate_recovery import disposition_event
from tools.agent_gate_runner import Runner, boot_identity
from tools.agent_gate_state import State

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux recovery V1")


def fixture_plan() -> dict[str, Any]:
    nodes = ["test_synthetic.py::test_one", "test_synthetic.py::test_two"]
    return {
        "version": 1,
        "review": "a" * 64,
        "inventory": nodes,
        "units": [[nodes[0]], [nodes[1]]],
        "allowed_skips": [],
    }


def test_owner_disposition_is_frontier_bound_and_one_shot(tmp_path: Path) -> None:
    value = fixture_plan()
    expiry = time.time() + 60
    deadline = time.monotonic() + 60
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
        state = State(value)
        event = {"kind": "dispatch", "unit": 0, "attempt": 1}
        state.apply(event)
        store.append(event)
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
            "scope": "unit",
            "index": 0,
            "previous_attempt": 1,
            "next_attempt": 2,
            "decision": "retry_once",
            "reconciliation_sha256": "d" * 64,
            "owner_authorization_sha256": "e" * 64,
        }
        transition = disposition_event(
            store, state, permit, digest(permit), time.time()
        )
        state.apply(transition)
        store.append(transition)
        assert state.units[0]["attempt"] == 2
        assert len(state.history) == 1
        with pytest.raises(GateBlocked):
            disposition_event(store, state, permit, digest(permit), time.time())
    with Journal.open(tmp_path / "store") as recovered:
        with pytest.raises(GateBlocked, match="UNIT_OUTCOME_UNKNOWN"):
            Runner(
                recovered,
                value,
                identity=lambda: "b" * 64,
                expires_at=expiry,
                deadline=deadline,
            )


def test_actual_recovery_keeps_completed_unit_and_consumes_retry(
    tmp_path: Path,
) -> None:
    import os
    import sys

    root = tmp_path / "synthetic"
    root.mkdir()
    (root / "test_synthetic.py").write_text(
        "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n"
    )
    value = fixture_plan()
    expiry, deadline = time.time() + 60, time.monotonic() + 60
    env = {
        "PATH": os.defpath,
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
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
        runner.run_unit(0, root=root, python=Path(sys.executable), environment=env)
        # Synthetic crash prefix: dispatch recorded, child deliberately not spawned.
        interrupted = {"kind": "dispatch", "unit": 1, "attempt": 1}
        runner.state.apply(interrupted)
        store.append(interrupted)
    with Journal.open(evidence) as store:
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
            "scope": "unit",
            "index": 1,
            "previous_attempt": 1,
            "next_attempt": 2,
            "decision": "retry_once",
            "reconciliation_sha256": "d" * 64,
            "owner_authorization_sha256": "e" * 64,
        }
        runner = Runner(
            store,
            value,
            identity=lambda: "b" * 64,
            expires_at=expiry,
            deadline=deadline,
            allow_recovery=True,
        )
        runner.recover(
            permit,
            digest(permit),
            root=root,
            python=Path(sys.executable),
            environment=env,
        )
        assert runner.state.remaining() == []
        assert runner.state.units[0]["attempt"] == 1
        assert runner.state.units[1]["attempt"] == 2
        calls = [
            e["nodeid"]
            for e in store.events
            if e["kind"] == "report" and e["when"] == "call"
        ]
        assert calls == value["inventory"]

    with Journal.open(evidence) as store:
        runner = Runner(
            store,
            value,
            identity=lambda: "b" * 64,
            expires_at=expiry,
            deadline=deadline,
        )
        assert runner.state.remaining() == []
        assert len(runner.state.history) == 1
        assert runner.state.units[1]["attempt"] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("frontier", "f" * 64),
        ("sequence", 0),
        ("boot", "f" * 64),
        ("scope", "quality"),
        ("previous_attempt", True),
        ("next_attempt", 3),
        ("expires_at", 0),
        ("source", "f" * 64),
        ("owner_authorization_sha256", ""),
    ],
)
def test_wrong_stale_or_unbound_disposition_cannot_consume(
    tmp_path: Path, field: str, value: Any
) -> None:
    value_plan = fixture_plan()
    expiry, deadline = time.time() + 60, time.monotonic() + 60
    with Journal.create(
        tmp_path / "store",
        {
            "plan": value_plan,
            "fingerprint": "b" * 64,
            "profile": "c" * 64,
            "expires_at": expiry,
            "deadline": deadline,
            "boot": boot_identity(),
            "max_collections": 4,
        },
    ) as store:
        state = State(value_plan)
        event = {"kind": "dispatch", "unit": 0, "attempt": 1}
        state.apply(event)
        store.append(event)
        permit = {
            "version": 1,
            "genesis": digest(store.genesis),
            "plan": digest(value_plan),
            "profile": "c" * 64,
            "source": "b" * 64,
            "frontier": store.frontier,
            "sequence": store.sequence,
            "boot": boot_identity(),
            "deadline": deadline,
            "expires_at": expiry,
            "scope": "unit",
            "index": 0,
            "previous_attempt": 1,
            "next_attempt": 2,
            "decision": "retry_once",
            "reconciliation_sha256": "d" * 64,
            "owner_authorization_sha256": "e" * 64,
        }
        permit[field] = value
        before = store.frontier
        with pytest.raises(GateBlocked):
            disposition_event(store, state, permit, digest(permit), time.time())
        assert store.frontier == before
        assert state.units[0]["attempt"] == 1


def test_recovery_expiry_bounds_inventory_spawn_and_identity_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = fixture_plan()
    now = [time.time()]
    expiry, deadline = now[0] + 60, time.monotonic() + 60
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
        slow_identity = [False]

        def identify() -> str:
            if slow_identity[0]:
                now[0] += 2
            return "b" * 64

        runner = Runner(
            store, value, identity=identify, expires_at=expiry, deadline=deadline
        )
        runner._receive({"kind": "dispatch", "unit": 0, "attempt": 1})
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
            "expires_at": now[0] + 1,
            "scope": "unit",
            "index": 0,
            "previous_attempt": 1,
            "next_attempt": 2,
            "decision": "retry_once",
            "reconciliation_sha256": "d" * 64,
            "owner_authorization_sha256": "e" * 64,
        }
        runner._receive(
            disposition_event(store, runner.state, permit, digest(permit), now[0])
        )
        # Reserve prerequisite collection; it must keep the parent's narrower lease.
        runner._receive({"kind": "recovery_inventory_dispatch", "attempt": 1})
        assert runner.state.reservation_expires_at == now[0] + 1
        inventory_permit = dict(permit)
        inventory_permit.update(
            frontier=store.frontier,
            sequence=store.sequence,
            scope="inventory",
            index=None,
            expires_at=now[0] + 30,
        )
        runner._receive(
            disposition_event(
                store, runner.state, inventory_permit, digest(inventory_permit), now[0]
            )
        )
        assert runner.state.collection_expires_at == now[0] + 30
        assert runner.state.reservation_expires_at == now[0] + 1
        monkeypatch.setattr(time, "time", lambda: now[0])
        slow_identity[0] = True
        with pytest.raises(GateBlocked, match="RUN_EXPIRED"):
            runner.guard()
        before = store.frontier
        with pytest.raises(GateBlocked, match="RUN_EXPIRED"):
            runner.tick()
        with pytest.raises(GateBlocked, match="RUN_EXPIRED"):
            runner._spawn(123)
        assert store.frontier == before


def test_new_explicit_owner_attempt_can_replace_expired_retry_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    root = tmp_path / "synthetic"
    root.mkdir()
    (root / "test_synthetic.py").write_text(
        "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n"
    )
    value = fixture_plan()
    now = [time.time()]
    expiry, deadline = now[0] + 60, time.monotonic() + 60
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
        runner._receive({"kind": "dispatch", "unit": 0, "attempt": 1})
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
            "expires_at": now[0] + 1,
            "scope": "unit",
            "index": 0,
            "previous_attempt": 1,
            "next_attempt": 2,
            "decision": "retry_once",
            "reconciliation_sha256": "d" * 64,
            "owner_authorization_sha256": "e" * 64,
        }
        runner._receive(
            disposition_event(store, runner.state, permit, digest(permit), now[0])
        )
    now[0] += 2
    monkeypatch.setattr(time, "time", lambda: now[0])
    with Journal.open(evidence) as store:
        with pytest.raises(GateBlocked):
            Runner(
                store,
                value,
                identity=lambda: "b" * 64,
                expires_at=expiry,
                deadline=deadline,
            )
    with Journal.open(evidence) as store:
        runner = Runner(
            store,
            value,
            identity=lambda: "b" * 64,
            expires_at=expiry,
            deadline=deadline,
            allow_recovery=True,
        )
        permit.update(
            frontier=store.frontier,
            sequence=store.sequence,
            previous_attempt=2,
            next_attempt=3,
            expires_at=now[0] + 30,
        )
        env = {
            "PATH": os.defpath,
            "HOME": str(tmp_path),
            "LANG": "C.UTF-8",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        runner.recover(
            permit,
            digest(permit),
            root=root,
            python=Path(sys.executable),
            environment=env,
        )
        assert runner.state.units[0]["attempt"] == 3
        assert runner.state.units[0]["terminal"] is not None
        assert runner.deadline == deadline
        assert len(runner.state.history) == 2
