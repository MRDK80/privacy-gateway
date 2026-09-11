"""Контракт воспроизводимой установки developer-инструментов (#160)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
REQUIRED_GATE_TOOLS = frozenset({"pytest", "ruff", "mypy", "pre-commit"})


def _distribution_name(requirement: str) -> str:
    """Извлечь нормализованное имя distribution из простого requirement."""
    match = re.match(r"[A-Za-z0-9_.-]+", requirement)
    assert match is not None, requirement
    return re.sub(r"[-_.]+", "-", match.group().lower())


def test_dev_extra_installs_every_required_quality_gate_tool() -> None:
    """Одна установка ``.[dev]`` предоставляет весь обязательный gate."""
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    dev_dependencies = {
        _distribution_name(requirement)
        for requirement in project["optional-dependencies"]["dev"]
    }

    assert REQUIRED_GATE_TOOLS <= dev_dependencies
    assert "pre-commit" not in {
        _distribution_name(requirement)
        for requirement in project["dependencies"]
    }
