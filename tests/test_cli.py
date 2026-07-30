"""Basic smoke tests for Privacy Gateway CLI — Stage E1."""

from __future__ import annotations

import subprocess
import sys

import pytest

import privacy_gateway


def test_package_importable() -> None:
    """privacy_gateway package must be importable."""
    assert privacy_gateway.__version__ == "0.1.0"


def test_help_exit_code() -> None:
    """python -m privacy_gateway --help must exit with code 0."""
    result = subprocess.run(
        [sys.executable, "-m", "privacy_gateway", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_help_contains_product_name() -> None:
    """Help output must mention 'Privacy Gateway'."""
    result = subprocess.run(
        [sys.executable, "-m", "privacy_gateway", "--help"],
        capture_output=True,
        text=True,
    )
    assert "Privacy Gateway" in result.stdout


def test_prepare_subcommand_visible_in_help() -> None:
    """'prepare' must appear in the top-level help as a future command."""
    result = subprocess.run(
        [sys.executable, "-m", "privacy_gateway", "--help"],
        capture_output=True,
        text=True,
    )
    assert "prepare" in result.stdout


def test_prepare_returns_nonzero_in_e1() -> None:
    """Calling 'prepare' in stage E1 must exit with nonzero code."""
    result = subprocess.run(
        [sys.executable, "-m", "privacy_gateway", "prepare"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "E1" in result.stderr


def test_restore_returns_nonzero_in_e1() -> None:
    """Calling 'restore' in stage E1 must exit with nonzero code."""
    result = subprocess.run(
        [sys.executable, "-m", "privacy_gateway", "restore"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "E1" in result.stderr


@pytest.mark.parametrize("subcommand", ["prepare", "restore"])
def test_subcommand_help_exits_zero(subcommand: str) -> None:
    """pgw prepare --help and pgw restore --help must exit 0."""
    result = subprocess.run(
        [sys.executable, "-m", "privacy_gateway", subcommand, "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
