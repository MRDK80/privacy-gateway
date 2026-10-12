"""Private partial snapshots never become worktree commits or review receipts."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from tools import agent_orchestrate as workflow
from tools.agent_continuation import ContinuationError
from tools.agent_continuation_store import Store

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX owner-only store")


def git(root: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def prepared(tmp_path: Path) -> tuple[Path, Store, workflow.TaskContract]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Synthetic")
    git(root, "config", "user.email", "synthetic@example.invalid")
    (root / "code.py").write_text("VALUE = 1\n")
    git(root, "add", "code.py")
    git(root, "commit", "-m", "synthetic base")
    base = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-c", "codex/301-test")
    (root / "code.py").write_text("VALUE = 2\n")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    contract = workflow.TaskContract(
        issue=301,
        epic=294,
        acceptance_criteria=("synthetic",),
        base_ref="roadmap/294-autonomy",
        base_sha=base,
        head_ref="codex/301-test",
        allowed_paths=("code.py",),
    )
    return root, Store(state, root), contract


def test_retained_unreviewed_snapshot_without_worktree_git_writes(
    prepared: tuple[Path, Store, workflow.TaskContract],
) -> None:
    root, store, contract = prepared
    before = git(root, "count-objects", "-v")
    refs = git(root, "show-ref")
    value = store.snapshot(contract, "a" * 64)
    mirror = store.directory / "snapshots" / ("a" * 64) / "objects.git"
    assert (
        git(root, "--git-dir", str(mirror), "show", value["snapshot_sha"] + ":code.py")
        == "VALUE = 2"
    )
    assert git(root, "count-objects", "-v") == before
    assert git(root, "show-ref") == refs
    assert git(root, "rev-parse", "HEAD") == contract.base_sha
    assert (mirror / "objects").stat().st_mode & 0o077 == 0


def test_symlink_and_repeated_snapshot_fail_closed(
    prepared: tuple[Path, Store, workflow.TaskContract], tmp_path: Path
) -> None:
    root, store, contract = prepared
    store.snapshot(contract, "b" * 64)
    with pytest.raises(ContinuationError, match="CONTINUATION_SNAPSHOT_EXISTS"):
        store.snapshot(contract, "b" * 64)
    (root / "code.py").unlink()
    (root / "code.py").symlink_to(tmp_path / "outside")
    with pytest.raises(ContinuationError, match="CONTINUATION_SNAPSHOT_UNSAFE"):
        store.snapshot(contract, "c" * 64)


def test_record_cap_covers_serialized_bytes_before_write(
    prepared: tuple[Path, Store, workflow.TaskContract],
) -> None:
    _, store, _ = prepared
    key = "handoff-" + "d" * 64
    value = {"phase": "EXECUTOR", "ordinal": 1}
    actual_size = len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
    with pytest.raises(ContinuationError, match="CONTINUATION_RECORD_TOO_LARGE"):
        store.save(key, value, cap=actual_size - 1)
    assert not (store.directory / (key + ".json")).exists()
    store.save(key, value, cap=actual_size)
    assert store.load(key, cap=actual_size) == value


def test_snapshot_verification_rereads_workspace_and_private_objects(
    prepared: tuple[Path, Store, workflow.TaskContract],
) -> None:
    root, store, contract = prepared
    invocation = "e" * 64
    value = store.snapshot(contract, invocation)
    before = git(root, "count-objects", "-v")
    refs = git(root, "show-ref")
    store.verify_snapshot(contract, invocation, value)
    assert git(root, "count-objects", "-v") == before
    assert git(root, "show-ref") == refs
    (root / "code.py").write_text("VALUE = 3\n")
    with pytest.raises(ContinuationError, match="CONTINUATION_WORKSPACE_CHANGED"):
        store.verify_snapshot(contract, invocation, value)


def test_forged_snapshot_digest_or_changed_head_cannot_supply_evidence(
    prepared: tuple[Path, Store, workflow.TaskContract],
) -> None:
    _, store, contract = prepared
    invocation = "f" * 64
    value = store.snapshot(contract, invocation)
    for key, replacement in (("snapshot_digest", "a" * 64), ("head_sha", "a" * 40)):
        with pytest.raises(ContinuationError, match="CONTINUATION_WORKSPACE_CHANGED"):
            store.verify_snapshot(contract, invocation, {**value, key: replacement})


@pytest.mark.skipif(os.name == "nt", reason="POSIX canonical lock")
def test_continuation_requires_actual_current_process_runner_ownership(
    tmp_path: Path,
) -> None:
    from tools import agent_epic_loop as loop

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    with pytest.raises(loop.LoopError, match="LOCK_FAILED"):
        loop.RunnerLock.current_owned(state)
    with loop.RunnerLock(state) as lock:
        assert loop.RunnerLock.current_owned(state) is lock
    with pytest.raises(loop.LoopError, match="LOCK_FAILED"):
        loop.RunnerLock.current_owned(state)
