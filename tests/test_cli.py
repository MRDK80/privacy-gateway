"""Базовые smoke-тесты CLI Privacy Gateway — Этап Э1."""

from __future__ import annotations

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
    )


def test_package_importable() -> None:
    """Пакет privacy_gateway должен импортироваться."""
    assert privacy_gateway.__version__ == "0.1.0"


def test_help_exit_code() -> None:
    """python -m privacy_gateway --help должен завершаться с кодом 0."""
    result = _run("--help")
    assert result.returncode == 0, result.stderr


def test_help_contains_product_name() -> None:
    """Вывод help должен содержать название 'Privacy Gateway'."""
    result = _run("--help")
    assert "Privacy Gateway" in result.stdout


def test_prepare_subcommand_visible_in_help() -> None:
    """Команда 'prepare' должна быть видна в общем help как будущая команда."""
    result = _run("--help")
    assert "prepare" in result.stdout


def test_prepare_returns_nonzero_in_e1() -> None:
    """Вызов 'prepare' на этапе Э1 должен завершаться с ненулевым кодом."""
    result = _run("prepare")
    assert result.returncode != 0
    assert "Э1" in result.stderr


def test_restore_returns_nonzero_in_e1() -> None:
    """Вызов 'restore' на этапе Э1 должен завершаться с ненулевым кодом."""
    result = _run("restore")
    assert result.returncode != 0
    assert "Э1" in result.stderr


@pytest.mark.parametrize("subcommand", ["prepare", "restore"])
def test_subcommand_help_exits_zero(subcommand: str) -> None:
    """pgw prepare --help и pgw restore --help должны завершаться с кодом 0."""
    result = _run(subcommand, "--help")
    assert result.returncode == 0, result.stderr
