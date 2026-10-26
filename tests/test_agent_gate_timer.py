"""Timer saves during silence and never promotes provisional PASS."""

from __future__ import annotations

import pytest
from tools.agent_gate_journal import GateBlocked
from tools.agent_gate_runner import Checkpoints


def test_long_silent_test_gets_15_minute_saves() -> None:
    now = [0.0]
    saves: list[tuple[float, float]] = []
    timer = Checkpoints(
        lambda stamp, late: saves.append((stamp, late)), clock=lambda: now[0]
    )
    now[0] = 899
    timer.tick()
    assert saves == [(0, 0)]
    now[0] = 900
    timer.tick()
    now[0] = 1800
    timer.tick()
    timer.finish()
    assert [stamp for stamp, _ in saves] == [0, 900, 1800, 1800]


def test_scheduler_stall_is_saved_and_blocks_further_dispatch() -> None:
    now = [0.0]
    saves: list[tuple[float, float]] = []
    timer = Checkpoints(
        lambda stamp, late: saves.append((stamp, late)), clock=lambda: now[0]
    )
    now[0] = 950
    with pytest.raises(GateBlocked, match="CHECKPOINT_OVERDUE"):
        timer.tick()
    assert saves[-1] == (950, 50)


def test_save_failure_is_not_ignored() -> None:
    def fail(stamp: float, late: float) -> None:
        raise OSError("synthetic disk error")

    with pytest.raises(OSError):
        Checkpoints(fail)
