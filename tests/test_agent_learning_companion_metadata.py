"""Closed companion enumeration is bounded and never invents role calls."""

import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_learning as core
from tools import agent_learning_records as records
from tools.agent_continuation import digest
from tools.agent_epic_loop import RunnerLock
from tools.agent_learning_companions import save
from tools.agent_learning_measure import _metadata

from tests.test_agent_learning_planning_journal import StorageReader
from tests.test_agent_learning_records import invocation


def fixture(tmp_path: Path) -> tuple[StorageReader, Path, dict[str, Any]]:
    root, seed = tmp_path / "repo", tmp_path / "seed"
    root.mkdir()
    seed.mkdir(mode=0o700)
    reader = StorageReader(seed, root)
    value = invocation()
    value["identity"]["owner_profile_digest"] = digest(reader.owner)
    value["status"] = "COMPLETED"
    value["invocation_key"] = records.invocation_id(value)
    value = records.invocation(core.seal(value))
    directory = seed / "learning-invocations"
    directory.mkdir(mode=0o700)
    import json

    path = directory / (value["invocation_key"] + ".json")
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return reader, directory, value


def problem(value: dict[str, Any]) -> dict[str, Any]:
    return core.seal(
        {
            "schema_version": "1.0",
            "invocation_key": value["invocation_key"],
            "role": value["role"],
            "source_kind": "NONE",
            "source_digest": None,
            "problem_class": None,
        }
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_companion_is_not_an_extra_call(tmp_path: Path) -> None:
    reader, directory, value = fixture(tmp_path)
    with RunnerLock(reader.seed):
        save(
            reader,
            directory,
            value["invocation_key"] + ".problem.json",
            problem(value),
            owner=reader.seed,
        )
    assert _metadata(reader, directory, task=302, run_id="c" * 32) == [value]


@pytest.mark.parametrize("damage", ["orphan", "role", "unknown", "record-cap"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_bad_companion_cannot_be_silently_skipped(tmp_path: Path, damage: str) -> None:
    reader, directory, value = fixture(tmp_path)
    binding = problem(value)
    if damage == "role":
        binding = core.seal({**binding, "role": "controller"})
    with RunnerLock(reader.seed):
        save(
            reader,
            directory,
            value["invocation_key"] + ".problem.json",
            binding,
            owner=reader.seed,
        )
    if damage == "orphan":
        (directory / (value["invocation_key"] + ".json")).unlink()
    elif damage == "unknown":
        (directory / (value["invocation_key"] + ".problem.json")).rename(
            directory / (value["invocation_key"] + ".unknown.json")
        )
    elif damage == "record-cap":
        reader.owner["limits"]["max_records"] = 1
    with pytest.raises(core.LearningError):
        _metadata(reader, directory, task=302, run_id="c" * 32)


@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_every_completed_invocation_requires_its_problem_companion(
    tmp_path: Path,
) -> None:
    reader, directory, value = fixture(tmp_path)
    assert value["status"] == "COMPLETED"
    with pytest.raises(core.LearningError, match="LEARNING_SOURCE_MISSING"):
        _metadata(reader, directory, task=302, run_id="c" * 32)
