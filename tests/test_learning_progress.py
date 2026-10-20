"""Pure tests for bounded parent-only progress."""

import io
import sys
from typing import NoReturn

import pytest

from tests import learning_progress as progress


def test_disabled_has_no_clock_or_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PGW_TEST_LEARNING_PROGRESS", raising=False)

    def fail() -> NoReturn:
        raise AssertionError("clock called")

    monkeypatch.setattr(progress, "perf_counter", fail)
    with progress.Progress().phase("fixture"):
        pass


def test_fixed_output_and_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGW_TEST_LEARNING_PROGRESS", "1")
    output = io.StringIO()
    monkeypatch.setattr(sys, "stderr", output)
    reporter = progress.Progress()
    for _ in range(50):
        with reporter.phase("probe_total", 1, 32):
            pass
    lines = output.getvalue().splitlines()
    assert len(lines) == 80
    assert all(len(line.encode("ascii")) < 128 for line in lines)
    assert lines[0].startswith(
        "learning-progress phase=probe_total event=start index=1 total=32 elapsed="
    )


def test_invalid_metadata_is_not_exported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGW_TEST_LEARNING_PROGRESS", "1")
    output = io.StringIO()
    monkeypatch.setattr(sys, "stderr", output)
    reporter = progress.Progress()
    reporter.emit("private-data", "start", 1, 1, 0)
    reporter.emit("fixture", "raw-error", 1, 1, 0)
    for elapsed in (float("nan"), float("inf"), -1, 1000000):
        reporter.emit("fixture", "start", 1, 1, elapsed)
    reporter.emit("fixture", "start", 1, 33, 0)
    assert output.getvalue() == ""


@pytest.mark.parametrize("emission_error", [OSError, KeyboardInterrupt])
def test_end_failure_preserves_original(
    monkeypatch: pytest.MonkeyPatch, emission_error: type[BaseException]
) -> None:
    monkeypatch.setenv("PGW_TEST_LEARNING_PROGRESS", "1")
    reporter = progress.Progress()

    def emit(*args: object) -> None:
        if args[1] == "end":
            raise emission_error()

    monkeypatch.setattr(reporter, "emit", emit)
    original = ValueError("synthetic")
    with pytest.raises(ValueError) as caught, reporter.phase("fixture"):
        raise original
    assert caught.value is original


def test_timed_calls_once_preserves_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGW_TEST_LEARNING_PROGRESS", "1")
    calls: list[int] = []

    @progress.timed("active_setup")
    def operation(value: int) -> int:
        calls.append(value)
        return value

    assert operation(7) == 7
    assert calls == [7]


def test_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGW_TEST_LEARNING_PROGRESS", "1")
    with pytest.raises(KeyboardInterrupt), progress.Progress().phase("fixture"):
        raise KeyboardInterrupt


@pytest.mark.parametrize("emission_error", [KeyboardInterrupt, SystemExit])
def test_end_cancellation_inside_outer_except_propagates(
    monkeypatch: pytest.MonkeyPatch, emission_error: type[BaseException]
) -> None:
    monkeypatch.setenv("PGW_TEST_LEARNING_PROGRESS", "1")
    reporter = progress.Progress()

    def emit(*args: object) -> None:
        if args[1] == "end":
            raise emission_error()

    monkeypatch.setattr(reporter, "emit", emit)
    try:
        raise ValueError("handled synthetic error")
    except ValueError:
        with pytest.raises(emission_error), reporter.phase("fixture"):
            pass
