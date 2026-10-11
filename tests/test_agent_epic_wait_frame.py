"""Synthetic invocation descriptors cannot grant effects or extend deadlines."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_wait_frame as frame
from tools.agent_epic_wait import digest

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="Private descriptors are POSIX-only"
)


def create(tmp_path: Path, **options: Any) -> tuple[dict[str, Any], int]:
    state = tmp_path / "state"
    state.mkdir(mode=0o700, exist_ok=True)
    return frame.create(
        state=state,
        root=tmp_path / "repo",
        repository="owner/repo",
        epic=248,
        policy_sha="a" * 40,
        runtime_config_digest="sha256:" + "b" * 64,
        command=("python", "runtime.py", "epic_resume"),
        deadline_at=200,
        now=100,
        **options,
    )


def test_private_read_only_descriptor_exact_invocation(tmp_path: Path) -> None:
    value, descriptor = create(tmp_path)
    try:
        environment = frame.environment(value, descriptor)
        result = frame.consume(
            state=tmp_path / "state",
            root=tmp_path / "repo",
            environment=environment,
            repository="owner/repo",
            epic=248,
            policy_sha="a" * 40,
            runtime_config_digest="sha256:" + "b" * 64,
            command=("python", "runtime.py", "epic_resume"),
            now=101,
        )
        assert result == value
        assert not environment
        assert not os.get_inheritable(descriptor)
        with pytest.raises(OSError):
            os.write(descriptor, b"bad")
    finally:
        os.close(descriptor)


def test_nested_frame_clamps_and_does_not_reuse_nonce(tmp_path: Path) -> None:
    parent, descriptor = create(tmp_path)
    child, child_descriptor = create(tmp_path, parent=parent)
    try:
        assert child["deadline_at"] <= parent["deadline_at"]
        assert child["parent_frame_digest"] == digest(parent)
        assert child["invocation_id"] != parent["invocation_id"]
    finally:
        os.close(descriptor)
        os.close(child_descriptor)


@pytest.mark.parametrize("change", ["command", "config", "nonce", "expired"])
def test_descriptor_rejects_wrong_current_binding(tmp_path: Path, change: str) -> None:
    value, descriptor = create(tmp_path)
    environment = frame.environment(value, descriptor)
    if change == "nonce":
        environment[frame.NONCE_ENV] = "f" * 32
    try:
        with pytest.raises(frame.FrameError):
            frame.consume(
                state=tmp_path / "state",
                root=tmp_path / "repo",
                environment=environment,
                repository="owner/repo",
                epic=248,
                policy_sha="a" * 40,
                runtime_config_digest="sha256:"
                + ("c" if change == "config" else "b") * 64,
                command=("other",)
                if change == "command"
                else ("python", "runtime.py", "epic_resume"),
                now=201 if change == "expired" else 101,
            )
        assert not environment
    finally:
        os.close(descriptor)
