"""Unsupported platform refuses before creating any evidence."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tools.agent_gate_journal import GateBlocked, Journal


def test_non_posix_refuses_before_state_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "must-not-exist"
    with monkeypatch.context() as scope:
        scope.setattr(os, "name", "nt")
        with pytest.raises(GateBlocked, match="UNSUPPORTED_PLATFORM"):
            Journal.create(target, {})
    assert not target.exists()
