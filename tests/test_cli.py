"""Базовые smoke-тесты CLI Privacy Gateway — Этапы Э1/Э6."""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys

import pytest

import privacy_gateway


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Вызвать CLI как отдельный процесс и вернуть результат."""
    return subprocess.run(
        [sys.executable, "-m", "privacy_gateway", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )


def test_package_importable() -> None:
    """Пакет privacy_gateway должен импортироваться."""
    assert privacy_gateway.__version__ == "0.5.0"


def test_version_matches_distribution_metadata() -> None:
    """__version__ должен совпадать с версией установленного дистрибутива."""
    distribution_version = importlib.metadata.version("privacy-gateway")
    assert privacy_gateway.__version__ == distribution_version


def test_help_exit_code() -> None:
    """python -m privacy_gateway --help должен завершаться с кодом 0."""
    result = _run("--help")
    assert result.returncode == 0, result.stderr


def test_help_contains_product_name() -> None:
    """Вывод help должен содержать название 'Privacy Gateway'."""
    result = _run("--help")
    assert "Privacy Gateway" in result.stdout


def test_prepare_subcommand_visible_in_help() -> None:
    """Команда 'prepare' должна быть видна в общем help."""
    result = _run("--help")
    assert "prepare" in result.stdout


def test_prepare_without_args_returns_nonzero() -> None:
    """Вызов 'prepare' без аргументов завершается с ненулевым кодом (Э6+)."""
    result = _run("prepare")
    assert result.returncode != 0
    # Э6: argparse требует ФАЙЛ — ошибка в stderr
    assert "ФАЙЛ" in result.stderr or "required" in result.stderr


def test_restore_returns_nonzero() -> None:
    """Вызов 'restore' завершается с ненулевым кодом (заглушка Э7+)."""
    result = _run("restore")
    assert result.returncode != 0
    # Э6: сообщение о недоступности restore
    assert "restore" in result.stderr.lower() or "Э7" in result.stderr


@pytest.mark.parametrize("subcommand", ["prepare", "restore"])
def test_subcommand_help_exits_zero(subcommand: str) -> None:
    """pgw prepare --help и pgw restore --help должны завершаться с кодом 0."""
    result = _run(subcommand, "--help")
    assert result.returncode == 0, result.stderr
