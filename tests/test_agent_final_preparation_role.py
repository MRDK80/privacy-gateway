"""Synthetic original-launch counter failures across restart and partial writes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_final_preparation_records as records
from tools.agent_epic_loop import PhaseBlocked, RunnerLock
from tools.agent_final_preparation_role import RoleCounter
from tools.agent_final_preparation_storage import Store

from tests.test_agent_final_preparation_records import IDENTITY, original
from tests.test_agent_final_preparation_storage import setup as empty_setup

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private storage")


def setup(tmp_path: Path) -> tuple[Path, Path]:
    root, seed = empty_setup(tmp_path)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        # Original-start source fixture only; runtime initialization is separate.
        store.write(
            "role-state",
            records.seal(
                {
                    "schema_version": "1.0",
                    **IDENTITY,
                    "calls_consumed": 0,
                    "pending_reservation_digest": None,
                }
            ),
        )
    return root, seed


def reserve(counter: RoleCounter, identity: str = "c" * 64) -> dict[str, Any]:
    return counter.reserve(
        preparation_identity_digest=identity,
        input_digest="d" * 64,
        backend_digest="e" * 64,
        started_at=100,
        deadline_at=200,
    )


def test_consumed_before_transport_and_restart(tmp_path: Path) -> None:
    root, seed = setup(tmp_path)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        counter = RoleCounter(store, str(IDENTITY["launch_digest"]))
        reservation = reserve(counter)
        state, retained = counter.read()
        assert state is not None and state["calls_consumed"] == 1
        assert retained == reservation
    with RunnerLock(seed):
        restarted = RoleCounter(
            Store(seed, root, original("profile"), lambda: None),
            str(IDENTITY["launch_digest"]),
        )
        with pytest.raises(PhaseBlocked, match="FINAL_ROLE_CALL_SPENT"):
            reserve(restarted, "f" * 64)


@pytest.mark.parametrize(
    "failure",
    [
        "missing-state",
        "missing-reservation",
        "partial-state",
        "partial-reservation",
        "retained-source",
    ],
)
def test_partial_or_missing_original_never_resets(tmp_path: Path, failure: str) -> None:
    root, seed = setup(tmp_path)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        counter = RoleCounter(store, str(IDENTITY["launch_digest"]))
        if failure == "retained-source":
            (store.directory / "role-state.json").unlink()
            store.write("final-state", original("state"))
        else:
            reserve(counter)
            path = store.directory / (
                "role-state.json"
                if failure.endswith("state")
                else counter.key + ".json"
            )
            if failure.startswith("missing"):
                path.unlink()
            else:
                path.write_bytes(b'{"schema_version":')
        with pytest.raises(PhaseBlocked):
            reserve(counter)


def test_unknown_stays_consumed_and_cannot_be_repeated(tmp_path: Path) -> None:
    root, seed = setup(tmp_path)
    with RunnerLock(seed):
        counter = RoleCounter(
            Store(seed, root, original("profile"), lambda: None),
            str(IDENTITY["launch_digest"]),
        )
        reserve(counter)
        counter.unknown()
        state, retained = counter.read()
        assert state is not None and state["calls_consumed"] == 1
        assert retained is not None and retained["status"] == "UNKNOWN"
        with pytest.raises(PhaseBlocked, match="FINAL_ROLE_CALL_SPENT"):
            reserve(counter)


def test_partial_first_write_remains_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed = setup(tmp_path)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        counter = RoleCounter(store, str(IDENTITY["launch_digest"]))
        write = store.write

        def fail_second(key: str, value: dict[str, Any]) -> None:
            if key.startswith("reservation-"):
                raise OSError("synthetic disk failure")
            write(key, value)

        monkeypatch.setattr(store, "write", fail_second)
        with pytest.raises(OSError):
            reserve(counter)
        monkeypatch.setattr(store, "write", write)
        with pytest.raises(PhaseBlocked, match="FINAL_ROLE_CALL_SPENT"):
            reserve(counter)


def test_changed_launch_does_not_reset(tmp_path: Path) -> None:
    root, seed = setup(tmp_path)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        reserve(RoleCounter(store, str(IDENTITY["launch_digest"])))
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            reserve(RoleCounter(store, "sha256:" + "f" * 64))
