"""Immutable bounded problem/relevance and full-gate companions."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from tools import agent_learning as core
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_runtime_cli import secure_directory
from tools.agent_learning_evidence import SourceReader


def save(
    reader: SourceReader,
    directory: Path,
    name: str,
    value: dict[str, Any],
    *,
    owner: Path,
) -> None:
    reader.guard()
    RunnerLock.current_owned(owner)
    if (
        re.fullmatch(
            r"(?:[0-9a-f]{64}\.(?:problem|relevance)|[0-9a-f]{32}-[1-3])\.json", name
        )
        is None
    ):
        raise core.LearningError("LEARNING_STATE_INVALID")
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise core.LearningError("LEARNING_STORAGE_UNSAFE")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    secure_directory(directory, reader.root)
    target = directory / name
    limits = reader.owner["limits"]
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    if len(raw) > limits["max_record_bytes"]:
        raise core.LearningError("LEARNING_STORAGE_EXHAUSTED")
    count, size = 0, 0
    for path in directory.iterdir():
        if path.name == "epic-loop.lock":
            continue
        if count >= limits["max_records"] or path.suffix != ".json":
            raise core.LearningError("LEARNING_STORAGE_EXHAUSTED")
        reader._json(path)
        count += 1
        size += path.lstat().st_size
    if target.exists() or target.is_symlink():
        if reader._json(target) != value:
            raise core.LearningError("LEARNING_STATE_CONFLICT")
        return
    if count >= limits["max_records"] or size + len(raw) > limits["max_store_bytes"]:
        raise core.LearningError("LEARNING_STORAGE_EXHAUSTED")
    try:
        descriptor = os.open(
            target,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        raise core.LearningError("LEARNING_WRITE_FAILED") from None
