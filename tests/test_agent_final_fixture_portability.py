"""Portable discovery has no authority to bootstrap or repair a hook cache."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests import test_agent_final_learning_lifecycle_genuine as fixture

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX cache admission")


def _source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "private-cache"
    root.mkdir(mode=0o700)
    names = ("repo-renamed-first", "repo-renamed-second")
    rows = []
    for (repo, rev), name in zip((
        ("https://github.com/Yelp/detect-secrets", "v1.5.0"),
        ("https://github.com/pre-commit/pre-commit-hooks", "v6.0.0"),
    ), names, strict=True):
        path = root / name
        path.mkdir(mode=0o700)
        (path / "original").write_text("synthetic cache source\n")
        rows.append((repo, rev, str(path)))
    with sqlite3.connect(root / "db.db") as db:
        db.execute("CREATE TABLE repos(repo TEXT, ref TEXT, path TEXT)")
        db.executemany("INSERT INTO repos VALUES (?,?,?)", rows)
    monkeypatch.setenv("PRE_COMMIT_HOME", str(root))
    return root


def test_portable_active_interpreter() -> None:
    assert fixture._canonical_bin() == Path(sys.executable).parent


def test_portable_identity_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _source(tmp_path, monkeypatch)
    admitted, selected = fixture._cache_sources()
    assert admitted == root
    assert set(selected.values()) == {"repo-renamed-first", "repo-renamed-second"}
    assert len(selected) == 2
    root.chmod(0o755)
    with pytest.raises(AssertionError):
        fixture._cache_sources()
    root.chmod(0o700)
    first = root / "repo-renamed-first"
    first.rename(root / "retained")
    first.symlink_to(root / "retained", target_is_directory=True)
    with pytest.raises(AssertionError):
        fixture._cache_sources()


def test_portable_offline_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _source(tmp_path, monkeypatch)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("discovery must not install or invoke transport")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert fixture._cache_sources()[0] == root
    monkeypatch.delenv("PRE_COMMIT_HOME")
    with pytest.raises(AssertionError):
        fixture._cache_sources()


def test_portable_source_conservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _source(tmp_path, monkeypatch)
    before = fixture._tree_map(root)
    db_before = hashlib.sha256((root / "db.db").read_bytes()).hexdigest()
    fixture._cache_sources()
    assert fixture._tree_map(root) == before
    assert hashlib.sha256((root / "db.db").read_bytes()).hexdigest() == db_before
    with sqlite3.connect(root / "db.db") as db:
        db.execute("UPDATE repos SET ref='unknown' WHERE ref='v1.5.0'")
    changed = fixture._tree_map(root)
    with pytest.raises(AssertionError):
        fixture._cache_sources()
    assert fixture._tree_map(root) == changed
