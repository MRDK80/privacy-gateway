"""Тесты для tools/check_secrets_baseline.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "check_secrets_baseline.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_secrets_baseline", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_module()


def test_tracked_baseline_is_excluded() -> None:
    assert guard.is_excluded(".secrets.baseline") is True


def test_nested_file_with_baseline_name_is_kept() -> None:
    assert guard.is_excluded("docs/.secrets.baseline") is False


def test_cache_directories_are_excluded() -> None:
    assert guard.is_excluded(".venv/lib/x.py") is True
    assert guard.is_excluded(".mypy_cache/a.json") is True
    assert guard.is_excluded("sub/.ruff_cache/a.json") is True


def test_egg_info_is_excluded() -> None:
    assert guard.is_excluded("pgw.egg-info/PKG-INFO") is True


def test_source_files_are_kept() -> None:
    assert guard.is_excluded("src/pgw/cli.py") is False
    assert guard.is_excluded("tools/check_secrets_baseline.py") is False


def test_select_files_filters_and_sorts() -> None:
    tracked = [
        "src/b.py",
        "",
        ".secrets.baseline",
        "src/a.py",
        ".venv/x.py",
    ]
    assert guard.select_files(tracked) == ["src/a.py", "src/b.py"]


def test_batched_splits_into_chunks() -> None:
    assert list(guard.batched(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]


def test_sha256_of_known_content(tmp_path: Path) -> None:
    target = tmp_path / "payload.txt"
    target.write_bytes(b"abc")
    assert guard.sha256_of(target) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
