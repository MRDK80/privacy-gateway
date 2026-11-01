"""Private storage safety/counters; installed source authority is tested separately."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from tools import agent_final_preparation_records as records
from tools.agent_epic_loop import PhaseBlocked, RunnerLock
from tools.agent_epic_runtime_cli import DeploymentError
from tools.agent_final_preparation_storage import Store

from tests.test_agent_final_preparation_records import original

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private storage")


def setup(tmp_path: Path) -> tuple[Path, Path]:
    root, seed = tmp_path / "repo", tmp_path / "seed"
    root.mkdir()
    seed.mkdir(mode=0o700)
    return root, seed


def test_private_roundtrip_preserves_consumed_call(tmp_path: Path) -> None:
    root, seed = setup(tmp_path)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        value = original("role_state")
        store.write("role-state", value)
        assert store.read("role-state") == value
        path = seed / "final-preparation/role-state.json"
        assert path.stat().st_mode & 0o077 == 0
        altered = records.seal(
            {**value, "calls_consumed": 0, "pending_reservation_digest": None}
        )
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            store.write("role-state", altered)
        assert store.read("role-state") == value


@pytest.mark.parametrize(
    "attack", ["symlink", "hardlink", "partial", "mode", "duplicate"]
)
def test_unsafe_existing_record_is_never_replaced(tmp_path: Path, attack: str) -> None:
    root, seed = setup(tmp_path)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        value = original("role_state")
        store.write("role-state", value)
        path = seed / "final-preparation/role-state.json"
        if attack == "symlink":
            target = seed / "external.json"
            path.rename(target)
            path.symlink_to(target)
        elif attack == "hardlink":
            os.link(path, seed / "duplicate.json")
        elif attack == "partial":
            path.write_bytes(b'{"schema_version":')
        elif attack == "mode":
            path.chmod(0o644)
        else:
            encoded = json.dumps(value)
            path.write_text(encoded[:-1] + ',"calls_consumed":0}')
        before = path.read_bytes()
        with pytest.raises((PhaseBlocked, DeploymentError)):
            store.write("role-state", value)
        assert path.read_bytes() == before


def test_invalid_key_cannot_escape_owned_storage(tmp_path: Path) -> None:
    root, seed = setup(tmp_path)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            store.write("../outside", original("role_state"))
        assert not (seed / "outside.json").exists()


def test_record_cap_does_not_replace_original(tmp_path: Path) -> None:
    root, seed = setup(tmp_path)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        value = original("role_state")
        store.write("role-state", value)
        before = (seed / "final-preparation/role-state.json").read_bytes()
        store.owner["max_artifact_bytes"] = 1
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            store.read("role-state")
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            store.write("role-state", value)
        assert (seed / "final-preparation/role-state.json").read_bytes() == before


def test_owner_namespace_cannot_be_rebound(tmp_path: Path) -> None:
    root, seed = setup(tmp_path)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        value = records.seal({**original("role_state"), "epic": 999})
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            store.write("role-state", value)
        assert store.read("role-state") is None


@pytest.mark.parametrize("operation", ["read", "write"])
def test_fifo_original_is_refused_without_blocking(
    tmp_path: Path, operation: str
) -> None:
    # Real child process/open, not a mocked file-type check. The outer timeout
    # kills and reaps only this synthetic child if the original open blocks.
    script = r"""
from pathlib import Path
import os,sys
from tests.test_agent_final_preparation_records import original
from tools.agent_epic_loop import PhaseBlocked,RunnerLock
from tools.agent_final_preparation_storage import Store
base=Path(sys.argv[1]);root=base/'repo';seed=base/'seed'
root.mkdir();seed.mkdir(mode=0o700)
with RunnerLock(seed):
    store=Store(seed,root,original('profile'),lambda:None)
    target=seed/'final-preparation/role-state.json'
    os.mkfifo(target,0o600)
    try:
        if sys.argv[2]=='read':store.read('role-state')
        else:store.write('role-state',original('role_state'))
    except PhaseBlocked as error:
        assert str(error)=='FINAL_APPROVAL_INVALID'
        print(str(error))
    else:raise AssertionError('FIFO accepted as a regular original')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path), operation],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "FINAL_APPROVAL_INVALID"
    assert stat.S_ISFIFO(
        (tmp_path / "seed/final-preparation/role-state.json").lstat().st_mode
    )
