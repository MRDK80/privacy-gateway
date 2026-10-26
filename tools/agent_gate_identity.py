"""Bounded source/input identity for an explicitly reviewed local profile."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path

from tools.agent_gate_journal import GateBlocked, digest

MAX_FILES = 30_000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_INPUT_BYTES = 1024 * 1024 * 1024
EXCLUDED = frozenset(
    {
        ".git",
        ".agent-private",
        ".venv",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "__pycache__",
    }
)


def fingerprint(
    root: Path,
    inputs: Sequence[Path],
    environment: Mapping[str, str],
    bindings: Mapping[str, str],
) -> str:
    """Profile review must prove input closure; this function does not infer it.

    Excluded locations may not be hidden test inputs. Unknown input closure means
    no reuse. No raw environment or file content is persisted in the fingerprint.
    """
    files: dict[str, dict[str, str | int]] = {}
    total = 0
    visited = 0

    def visit(path: Path, name: str, source: bool, depth: int = 0) -> None:
        nonlocal total, visited
        visited += 1
        if visited > MAX_FILES or depth > 64:
            raise GateBlocked("IDENTITY_TRAVERSAL_LIMIT")
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise GateBlocked("IDENTITY_SYMLINK")
        if stat.S_ISDIR(info.st_mode):
            if len(files) >= MAX_FILES:
                raise GateBlocked("IDENTITY_TOTAL_LIMIT")
            files[name] = {"kind": "directory", "mode": stat.S_IMODE(info.st_mode)}
            with os.scandir(path) as entries:
                names: list[str] = []
                for entry in entries:
                    visited += 1
                    if visited > MAX_FILES:
                        raise GateBlocked("IDENTITY_TRAVERSAL_LIMIT")
                    if len(names) >= MAX_FILES:
                        raise GateBlocked("IDENTITY_DIRECTORY_LIMIT")
                    if entry.name == "__pycache__" or entry.name.endswith(".pyc"):
                        continue
                    if source and entry.name in EXCLUDED:
                        continue
                    names.append(entry.name)
            for child in sorted(names):
                visit(path / child, f"{name}/{child}", source, depth + 1)
            return
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise GateBlocked("IDENTITY_FILE_INVALID")
        total += info.st_size
        if total > MAX_INPUT_BYTES or len(files) >= MAX_FILES:
            raise GateBlocked("IDENTITY_TOTAL_LIMIT")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK")
        fd = os.open(path, flags)
        try:
            before = os.fstat(fd)
            if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                raise GateBlocked("IDENTITY_REPLACED")
            hashed = hashlib.sha256()
            read = 0
            while data := os.read(fd, 64 * 1024):
                read += len(data)
                if read > MAX_FILE_BYTES or read > info.st_size:
                    raise GateBlocked("IDENTITY_FILE_GREW")
                hashed.update(data)
            after = os.fstat(fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or read != info.st_size:
                raise GateBlocked("IDENTITY_FILE_CHANGED")
            files[name] = {
                "kind": "file",
                "hash": hashed.hexdigest(),
                "mode": stat.S_IMODE(after.st_mode),
            }
        finally:
            os.close(fd)

    if os.name != "posix":
        raise GateBlocked("UNSUPPORTED_PLATFORM")
    visit(root, "source", True)
    for index, path in enumerate(inputs):
        visit(path, f"input-{index}", False)
    return digest(
        {
            "root": str(root),
            "files": files,
            "environment": dict(environment),
            "bindings": dict(bindings),
        }
    )
