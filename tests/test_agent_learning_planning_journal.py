"""Immutable private journal writes; source-origin replay is tested separately."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_learning as core
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_runtime_cli import DeploymentError
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_planning_journal import Journal

from tests.test_agent_learning import profile

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private journal")


class StorageReader(SourceReader):
    """Only the writer's file safety boundary; no fabricated origin proof."""

    def __init__(self, seed: Path, root: Path) -> None:
        self.seed, self.root = seed, root
        self.owner = profile()

    def guard(self) -> None:
        pass


def writer(tmp_path: Path) -> Journal:
    root, seed = tmp_path / "repo", tmp_path / "seed"
    root.mkdir()
    seed.mkdir(mode=0o700)
    journal = object.__new__(Journal)
    journal.reader = StorageReader(seed, root)
    journal.directory = seed / "learning-validation"
    journal.directory.mkdir(mode=0o700)
    journal.prefix = "planner-1"
    return journal


def test_immutable_write_never_replaces_conflicting_source(tmp_path: Path) -> None:
    journal = writer(tmp_path)
    with RunnerLock(journal.reader.seed):
        journal._save("request", {"synthetic": "first"})
        path = journal.directory / "planner-1-request.json"
        original = path.read_bytes()
        journal._save("request", {"synthetic": "first"})
        with pytest.raises(core.LearningError, match="LEARNING_STATE_CONFLICT"):
            journal._save("request", {"synthetic": "changed"})
        assert path.read_bytes() == original
        assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "partial", "mode"])
def test_unsafe_original_artifact_blocks_next_write(tmp_path: Path, kind: str) -> None:
    journal = writer(tmp_path)
    with RunnerLock(journal.reader.seed):
        journal._save("request", {"synthetic": True})
        path = journal.directory / "planner-1-request.json"
        if kind == "symlink":
            destination = journal.reader.seed / "outside.json"
            path.rename(destination)
            path.symlink_to(destination)
        elif kind == "hardlink":
            os.link(path, journal.reader.seed / "duplicate.json")
        elif kind == "partial":
            path.write_bytes(b'{"synthetic":')
        else:
            path.chmod(0o644)
        with pytest.raises(
            (core.LearningError, DeploymentError),
            match="LEARNING_STORAGE_UNSAFE|LEARNING_STATE_INVALID",
        ):
            journal._save("context", {"synthetic": True})
        assert not (journal.directory / "planner-1-context.json").exists()


def test_record_cap_preserves_previous_original(tmp_path: Path) -> None:
    journal = writer(tmp_path)
    journal.reader.owner["limits"]["max_records"] = 1
    first: dict[str, Any] = {"synthetic": True}
    with RunnerLock(journal.reader.seed):
        journal._save("request", first)
        original = (journal.directory / "planner-1-request.json").read_bytes()
        with pytest.raises(core.LearningError, match="LEARNING_STORAGE_EXHAUSTED"):
            journal._save("context", {"synthetic": False})
        assert (journal.directory / "planner-1-request.json").read_bytes() == original
        assert not (journal.directory / "planner-1-context.json").exists()
