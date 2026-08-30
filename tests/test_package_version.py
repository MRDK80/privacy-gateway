"""Инварианты единственного источника версии пакета — ADR-81, #81."""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import tomllib
from pathlib import Path

import pytest

import privacy_gateway

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"
_INIT_PATH = _REPO_ROOT / "src" / "privacy_gateway" / "__init__.py"

_DISTRIBUTION_NAME = "privacy-gateway"
_VERSION_ATTR = "privacy_gateway.__version__"


def _version_literal_nodes() -> list[ast.expr]:
    """Собрать все top-level присваивания __version__ из AST пакета."""
    tree = ast.parse(_INIT_PATH.read_text(encoding="utf-8"))
    nodes: list[ast.expr] = []
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        for target in statement.targets:
            if isinstance(target, ast.Name) and target.id == "__version__":
                nodes.append(statement.value)
    return nodes


def test_version_is_single_string_literal() -> None:
    """__version__ должен быть одним литералом под ast.literal_eval."""
    nodes = _version_literal_nodes()
    assert len(nodes) == 1, "__version__ присваивается не ровно один раз"
    literal = ast.literal_eval(nodes[0])
    assert isinstance(literal, str)
    assert literal == privacy_gateway.__version__


def test_init_does_not_read_distribution_metadata() -> None:
    """Import-путь не должен читать metadata и содержать fallback."""
    source = _INIT_PATH.read_text(encoding="utf-8")
    assert "importlib.metadata" not in source
    assert "importlib_metadata" not in source
    assert "PackageNotFoundError" not in source


def test_pyproject_declares_version_dynamic() -> None:
    """Версия в pyproject.toml должна быть dynamic из атрибута пакета."""
    data = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))
    project = data["project"]
    assert "version" in project.get("dynamic", [])
    assert "version" not in project, "статическое project.version осталось"
    dynamic_table = data["tool"]["setuptools"]["dynamic"]
    assert dynamic_table["version"] == {"attr": _VERSION_ATTR}


def test_version_readable_without_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """__version__ должен читаться, когда metadata недоступна."""

    def _missing(distribution_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(distribution_name)

    monkeypatch.setattr(importlib.metadata, "version", _missing)
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.version(_DISTRIBUTION_NAME)
    reloaded = importlib.reload(privacy_gateway)
    assert isinstance(reloaded.__version__, str)
    assert reloaded.__version__
    assert "__version__" in reloaded.__all__
