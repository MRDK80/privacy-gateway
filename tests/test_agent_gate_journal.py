"""Synthetic persistence/rollback failure paths for the opt-in gate journal."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tools.agent_gate_journal import GateBlocked, Journal

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX-only evidence V1")


def test_dispatch_is_durable_before_caller_can_spawn(tmp_path: Path) -> None:
    with Journal.create(tmp_path / "evidence", {"fingerprint": "a" * 64}) as store:
        store.append({"kind": "dispatch", "unit": "synthetic", "attempt": 1})
    with Journal.open(tmp_path / "evidence") as recovered:
        assert recovered.events == [
            {"kind": "dispatch", "unit": "synthetic", "attempt": 1}
        ]


def test_old_snapshot_cannot_hide_later_dispatch(tmp_path: Path) -> None:
    with Journal.create(tmp_path / "evidence", {}) as store:
        store.checkpoint({"accepted": []})
        store.append({"kind": "dispatch", "unit": "later", "attempt": 1})
    with Journal.open(tmp_path / "evidence") as recovered:
        assert recovered.events[-1]["unit"] == "later"


def test_corrupt_committed_suffix_blocks_old_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    with Journal.create(root, {}) as store:
        store.checkpoint({"accepted": []})
        store.append({"kind": "dispatch", "unit": "later", "attempt": 1})
    (root / "journal.jsonl").write_bytes(b"broken\n")
    with pytest.raises(GateBlocked):
        Journal.open(root)


def test_torn_snapshot_temp_preserves_prior_generation(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    with Journal.create(root, {}) as store:
        store.checkpoint({"accepted": []})
    (root / "snapshot.tmp").write_bytes(b"{")
    (root / "snapshot.tmp").chmod(0o600)
    with Journal.open(root) as store:
        assert store.last_snapshot is not None
        assert store.last_snapshot["state"] == {"accepted": []}


def test_missing_store_is_not_initial_zero(tmp_path: Path) -> None:
    with pytest.raises(GateBlocked):
        Journal.open(tmp_path / "missing")


def test_second_writer_is_rejected(tmp_path: Path) -> None:
    with Journal.create(tmp_path / "evidence", {}):
        with pytest.raises(GateBlocked):
            Journal.open(tmp_path / "evidence")


def test_missing_commit_is_not_silently_discarded(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    with Journal.create(root, {}) as store:
        store.append({"kind": "dispatch", "unit": "one", "attempt": 1})
    (root / "commit-00000001.json").unlink()
    with pytest.raises(GateBlocked):
        Journal.open(root)


def test_snapshot_frontier_cannot_exceed_actual_history(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    with Journal.create(root, {}) as store:
        store.checkpoint({})
    target = root / "snapshot-00000001.json"
    value = json.loads(target.read_text())
    value["sequence"] = 17
    target.write_text(json.dumps(value))
    with pytest.raises(GateBlocked):
        Journal.open(root)


def test_writer_crash_during_snapshot_keeps_valid_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "evidence"
    with Journal.create(root, {}) as store:
        store.checkpoint({"accepted": ["synthetic"]})
        original = store._immutable

        def torn(name: str, data: bytes) -> None:
            if name.startswith("snapshot"):
                (root / name).write_bytes(b"{")
                (root / name).chmod(0o600)
                raise OSError("synthetic write fault")
            original(name, data)

        monkeypatch.setattr(store, "_immutable", torn)
        with pytest.raises(OSError):
            store.checkpoint({"accepted": ["synthetic", "later"]})
    with Journal.open(root) as recovered:
        assert recovered.last_snapshot is not None
        assert recovered.last_snapshot["state"] == {"accepted": ["synthetic"]}
        recovered.checkpoint({"accepted": ["synthetic", "after-recovery"]})
    with Journal.open(root) as final:
        assert final.last_snapshot is not None
        assert final.last_snapshot["state"] == {
            "accepted": ["synthetic", "after-recovery"]
        }


@pytest.mark.parametrize("raw", [b"[]", b'{"version":1,"version":1}', b'{"x":NaN}'])
def test_malformed_genesis_is_blocked(tmp_path: Path, raw: bytes) -> None:
    root = tmp_path / "evidence"
    with Journal.create(root, {}):
        pass
    (root / "genesis.json").write_bytes(raw)
    with pytest.raises(GateBlocked):
        Journal.open(root)
