"""Opt-in parent test location markers; never workflow evidence."""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from time import perf_counter
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")
PHASES = frozenset({"fixture", "active_setup", "probe_total"})


class Progress:
    def __init__(self) -> None:
        self.enabled = os.environ.get("PGW_TEST_LEARNING_PROGRESS") == "1"
        self.lines = 0

    def emit(
        self, phase: str, event: str, index: int, total: int, elapsed: float
    ) -> None:
        if not self.enabled:
            return
        if (
            phase not in PHASES
            or event not in {"start", "end"}
            or not 1 <= index <= total <= 32
            or not math.isfinite(elapsed)
            or not 0 <= elapsed <= 999999
            or self.lines >= 80
        ):
            return
        line = (
            f"learning-progress phase={phase} event={event} "
            f"index={index} total={total} elapsed={elapsed:.3f}\n"
        )
        if len(line.encode("ascii")) > 128:
            return
        self.lines += 1
        sys.stderr.write(line)

    @contextmanager
    def phase(self, phase: str, index: int = 1, total: int = 1) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        started: float | None = None
        try:
            started = perf_counter()
            self.emit(phase, "start", index, total, 0.0)
        except Exception:
            pass
        body_failed = False
        try:
            yield
        except BaseException:
            body_failed = True
            raise
        finally:
            if started is not None:
                try:
                    self.emit(phase, "end", index, total, perf_counter() - started)
                except BaseException as error:
                    if not body_failed and not isinstance(error, Exception):
                        raise


def timed(phase: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            with Progress().phase(phase):
                return function(*args, **kwargs)

        return wrapped

    return decorate
