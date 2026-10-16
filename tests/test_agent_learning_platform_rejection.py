"""Unsupported hosts reject private learning before storage effects."""

import os
from pathlib import Path

import pytest
from tools import agent_learning as core
from tools.agent_epic_runtime_cli import DeploymentError, secure_directory
from tools.agent_learning_store import Store

from tests.test_agent_learning import profile


@pytest.mark.skipif(os.name == "posix", reason="Actual unsupported platform boundary")
def test_learning_store_rejects_unsupported_platform_before_storage(
    tmp_path: Path,
) -> None:
    root, task = tmp_path / "repo", tmp_path / "state"
    directory = task / "learning"
    with pytest.raises(core.LearningError, match="^LEARNING_PLATFORM_UNSUPPORTED$"):
        Store(directory, root, task, profile(), validators={})
    assert not task.exists()
    assert not directory.exists()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows runtime boundary")
def test_windows_private_runtime_rejects_before_storage(tmp_path: Path) -> None:
    root, state = tmp_path / "repo", tmp_path / "state"
    with pytest.raises(DeploymentError, match="^RUNTIME_ADAPTER_UNSUPPORTED$"):
        secure_directory(state, root)
    assert not state.exists()
