"""Synthetic owner-only append storage and independent rollback barrier."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_learning as learning
from tools.agent_epic_loop import LoopError, RunnerLock
from tools.agent_learning_store import Store

from tests.test_agent_learning import measurement, profile

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX private learning ownership"
)


def setup(tmp_path: Path, *, records: int = 10) -> tuple[Store, Path]:
    root, task = tmp_path / "repo", tmp_path / "task"
    root.mkdir()
    task.mkdir(mode=0o700)
    owner = profile()
    owner["limits"]["max_records"] = records
    return Store(
        task / "learning",
        root,
        task,
        owner,
        validators={"measurement": learning.measurement},
    ), task


def test_append_requires_both_owner_locks_and_conflicts_do_not_rewrite(
    tmp_path: Path,
) -> None:
    store, task = setup(tmp_path)
    with pytest.raises(LoopError, match="LOCK_FAILED"):
        with store:
            pass
    with RunnerLock(task), store:
        first = measurement()
        store.append("measurement", first, "measurement_id")
        before = store.path.read_bytes()
        store.append("measurement", first, "measurement_id")
        assert store.path.read_bytes() == before
        changed = learning.seal({**first, "duration_seconds": 11})
        with pytest.raises(learning.LearningError):
            store.append("measurement", changed, "measurement_id")
        assert store.path.read_bytes() == before
    assert store.path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "change", ["symlink", "hardlink", "mode", "partial", "oversized"]
)
def test_unsafe_storage_never_reads_or_overwrites(tmp_path: Path, change: str) -> None:
    store, task = setup(tmp_path)
    with RunnerLock(task), store:
        store.append("measurement", measurement(), "measurement_id")
        if change == "symlink":
            outside = task / "synthetic-outside"
            store.path.rename(outside)
            store.path.symlink_to(outside)
        elif change == "hardlink":
            os.link(store.path, task / "synthetic-hardlink")
        elif change == "mode":
            store.path.chmod(0o644)
        elif change == "partial":
            with store.path.open("ab") as stream:
                stream.write(b'{"synthetic":')
        else:
            store.path.write_bytes(
                b" " * (store.owner["limits"]["max_store_bytes"] + 1)
            )
        with pytest.raises(learning.LearningError):
            store.records()


def test_store_cap_preserves_old_records(tmp_path: Path) -> None:
    store, task = setup(tmp_path, records=1)
    with RunnerLock(task), store:
        store.append("measurement", measurement(), "measurement_id")
        before = store.path.read_bytes()
        with pytest.raises(learning.LearningError):
            store.append("measurement", measurement(hints=True), "measurement_id")
        assert store.path.read_bytes() == before


def test_negative_latch_has_independent_cap_and_blocks_selection(
    tmp_path: Path,
) -> None:
    store, task = setup(tmp_path, records=1)
    with RunnerLock(task), store:
        store.append("measurement", measurement(), "measurement_id")
        before = store.path.read_bytes()
        store.block("a" * 64, ["b" * 64], "LEARNING_REGRESSION")
        assert store.path.read_bytes() == before
        with pytest.raises(learning.LearningError, match="LEARNING_ROLLBACK_PENDING"):
            store.ensure_usable()
        with pytest.raises(learning.LearningError):
            store.clear_block()
        assert store.block_path.exists()


def test_short_append_is_fail_closed_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, task = setup(tmp_path)
    original = os.write

    def short(fd: int, data: Any) -> int:
        return original(fd, data[:10])

    with RunnerLock(task), store:
        monkeypatch.setattr(os, "write", short)
        with pytest.raises(learning.LearningError):
            store.append("measurement", measurement(), "measurement_id")
        monkeypatch.setattr(os, "write", original)
        with pytest.raises(learning.LearningError):
            store.records()


@pytest.mark.parametrize("existing", [False, True])
def test_store_rejects_symlink_ancestor_before_creation(
    tmp_path: Path, existing: bool
) -> None:
    root, private = tmp_path / "repo", tmp_path / "private"
    root.mkdir()
    private.mkdir(mode=0o700)
    task = private / "task"
    task.mkdir(mode=0o700)
    if existing:
        (task / "learning").mkdir(mode=0o700)
    alias = tmp_path / "synthetic-alias"
    alias.symlink_to(private, target_is_directory=True)
    with pytest.raises(learning.LearningError, match="LEARNING_STORAGE_UNSAFE"):
        Store(
            alias / "task/learning",
            root,
            alias / "task",
            profile(),
            validators={"measurement": learning.measurement},
        )
    assert (task / "learning").exists() is existing


@pytest.mark.parametrize("change", ["mode", "owner", "symlink"])
def test_store_rechecks_private_parent_drift_before_read_or_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    store, task = setup(tmp_path)
    with RunnerLock(task), store:
        store.append("measurement", measurement(), "measurement_id")
        before = store.path.read_bytes()
        if change == "mode":
            task.chmod(0o755)
        elif change == "owner":
            original = Path.lstat

            def changed_owner(path: Path) -> os.stat_result:
                info = original(path)
                if path == task:
                    fields = list(info)
                    fields[4] = info.st_uid + 1
                    return os.stat_result(fields)
                return info

            monkeypatch.setattr(Path, "lstat", changed_owner)
        else:
            outside = tmp_path / "synthetic-outside"
            task.rename(outside)
            task.symlink_to(outside, target_is_directory=True)
        with pytest.raises(learning.LearningError, match="LEARNING_STORAGE_UNSAFE"):
            store.records()
        with pytest.raises(learning.LearningError, match="LEARNING_STORAGE_UNSAFE"):
            store.append("measurement", measurement(hints=True), "measurement_id")
        assert store.path.read_bytes() == before


@pytest.mark.parametrize("role", ["executor", "planning_review"])
def test_partial_tombstones_never_clear_failed_evaluation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    from tools import agent_learning_records as schemas
    from tools.agent_continuation import digest

    from tests.test_agent_learning_records import invocation, observation

    store, task = setup(tmp_path)
    hinted = measurement(hints=True, outcome="FAIL_ESCALATE")
    evaluation = learning.compare(measurement(), hinted)
    observed = observation()
    observed["source"]["workflow_digest"] = hinted["source_run_digest"]
    observed["identity"]["run_id"] = "c" * 32
    observed["identity"]["policy_sha"] = "a" * 40
    observed["record_id"] = schemas.observation_id(observed)
    observed = learning.seal(observed)
    used = invocation()
    used["role"] = role
    if role == "planning_review":
        used["identity"]["original_run_id"] = "d" * 64
    used["invocation_key"] = schemas.invocation_id(used)
    used["lesson_ids"] = ["a" * 64, "b" * 64]
    used = learning.seal(used)
    tombstone = learning.seal(
        {
            "schema_version": "1.0",
            "lesson_id": "a" * 64,
            "reason_code": "LEARNING_REGRESSION",
            "evaluation_digest": digest(evaluation),
        }
    )
    retained = [
        {"kind": kind, "record": value}
        for kind, value in (
            ("measurement", hinted),
            ("evaluation", evaluation),
            ("observation", observed),
            ("invocation", used),
            ("tombstone", tombstone),
        )
    ]
    monkeypatch.setattr(store, "records", lambda: retained)
    with RunnerLock(task), store:
        with pytest.raises(learning.LearningError, match="LEARNING_ROLLBACK_PENDING"):
            store.ensure_usable()
        retained.append(
            {
                "kind": "tombstone",
                "record": learning.seal(
                    {
                        **tombstone,
                        "lesson_id": "b" * 64,
                    }
                ),
            }
        )
        if role == "planning_review":
            # No original managed Admission exists in this isolated store.
            # Even all tombstones cannot invent its planning source binding.
            with pytest.raises(
                learning.LearningError, match="LEARNING_ROLLBACK_PENDING"
            ):
                store.ensure_usable()
        else:
            store.ensure_usable()


def test_run_rollback_union_is_bounded_independently_of_each_call(
    tmp_path: Path,
) -> None:
    store, task = setup(tmp_path, records=1)
    lessons = [format(index, "064x") for index in range(36)]
    with RunnerLock(task), store:
        store.append("measurement", measurement(), "measurement_id")
        before = store.path.read_bytes()
        with pytest.raises(learning.LearningError):
            store.block("a" * 64, lessons, "LEARNING_REGRESSION", maximum=35)
        assert not store.block_path.exists()
        store.block("a" * 64, lessons, "LEARNING_REGRESSION", maximum=36)
        assert store.block_path.stat().st_size <= 4096
        assert store.path.read_bytes() == before
        with pytest.raises(learning.LearningError, match="LEARNING_ROLLBACK_PENDING"):
            store.ensure_usable()


@pytest.mark.parametrize("failure", ["write", "fsync"])
def test_latch_write_failure_leaves_persistent_negative_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    store, task = setup(tmp_path)

    def fail(*args: Any) -> Any:
        raise OSError("synthetic latch write failure")

    with RunnerLock(task), store:
        with monkeypatch.context() as patch:
            patch.setattr(os, failure, fail)
            with pytest.raises(learning.LearningError, match="LEARNING_WRITE_FAILED"):
                store.block("a" * 64, ["b" * 64], "LEARNING_REGRESSION")
        assert store.block_path.exists()
        with pytest.raises(learning.LearningError, match="LEARNING_ROLLBACK_PENDING"):
            store.ensure_usable()
    restarted = Store(
        store.directory,
        store.root,
        task,
        store.owner,
        validators={"measurement": learning.measurement},
    )
    with RunnerLock(task), restarted:
        with pytest.raises(learning.LearningError, match="LEARNING_ROLLBACK_PENDING"):
            restarted.ensure_usable()
