import os
from pathlib import Path

import pytest
from tools import agent_learning as core
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_runtime_cli import DeploymentError
from tools.agent_learning_companions import save

from tests.test_agent_learning_planning_journal import StorageReader

NAME = "a" * 64 + ".problem.json"
OTHER = "b" * 64 + ".relevance.json"


def setup(tmp_path: Path) -> tuple[StorageReader, Path]:
    root = tmp_path / "repo"
    seed = tmp_path / "seed"
    root.mkdir()
    seed.mkdir(mode=0o700)
    reader = StorageReader(seed, root)
    return reader, seed / "learning-invocations"


@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_immutable_idempotent_companion_conflict(tmp_path: Path) -> None:
    reader, directory = setup(tmp_path)
    with RunnerLock(reader.seed):
        save(reader, directory, NAME, {"synthetic": True}, owner=reader.seed)
        original = (directory / NAME).read_bytes()
        save(reader, directory, NAME, {"synthetic": True}, owner=reader.seed)
        with pytest.raises(core.LearningError, match="LEARNING_STATE_CONFLICT"):
            save(reader, directory, NAME, {"synthetic": False}, owner=reader.seed)
        assert (directory / NAME).read_bytes() == original
        assert (directory / NAME).stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("fault", ["symlink", "hardlink", "partial", "mode"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_bad_retained_companion_blocks_next_write(tmp_path: Path, fault: str) -> None:
    reader, directory = setup(tmp_path)
    with RunnerLock(reader.seed):
        save(reader, directory, NAME, {"synthetic": True}, owner=reader.seed)
        path = directory / NAME
        if fault == "symlink":
            target = reader.seed / "retained.json"
            path.rename(target)
            path.symlink_to(target)
        elif fault == "hardlink":
            os.link(path, reader.seed / "duplicate.json")
        elif fault == "partial":
            path.write_bytes(b'{"synthetic":')
        else:
            path.chmod(0o644)
        with pytest.raises((core.LearningError, DeploymentError)):
            save(reader, directory, OTHER, {"synthetic": False}, owner=reader.seed)
        assert not (directory / OTHER).exists()


@pytest.mark.parametrize(
    "limit", ["max_records", "max_store_bytes", "max_record_bytes"]
)
@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_existing_caps_do_not_replace_companion(tmp_path: Path, limit: str) -> None:
    reader, directory = setup(tmp_path)
    with RunnerLock(reader.seed):
        save(reader, directory, NAME, {"synthetic": True}, owner=reader.seed)
        original = (directory / NAME).read_bytes()
        reader.owner["limits"][limit] = 1
        with pytest.raises(core.LearningError, match="LEARNING_STORAGE_EXHAUSTED"):
            save(reader, directory, OTHER, {"synthetic": False}, owner=reader.seed)
        assert (directory / NAME).read_bytes() == original
        assert not (directory / OTHER).exists()
