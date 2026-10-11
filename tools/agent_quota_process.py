"""Bounded POSIX child output for fixed trusted quota/host commands."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from tools.agent_quota import QuotaError


def run(
    argv: Sequence[str],
    *,
    timeout: float,
    cap: int,
    guard: Callable[[], None],
    input_bytes: bytes | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if os.name != "posix":
        raise QuotaError("QUOTA_SOURCE_UNSUPPORTED")
    guard()
    set_blocking = getattr(os, "set_blocking", None)
    if not callable(set_blocking):
        raise QuotaError("QUOTA_SOURCE_UNSUPPORTED")
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            stdin=subprocess.DEVNULL if input_bytes is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        raise QuotaError("QUOTA_PROCESS_UNAVAILABLE") from None
    assert process.stdout is not None
    pending = memoryview(input_bytes or b"")
    if process.stdin is not None:
        set_blocking(process.stdin.fileno(), False)
    data = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                guard()
                if process.stdin is not None and not process.stdin.closed:
                    if pending:
                        try:
                            written = os.write(process.stdin.fileno(), pending[:8192])
                        except BlockingIOError:
                            written = 0
                        pending = pending[written:]
                    else:
                        process.stdin.close()
                if not selector.select(min(0.01, max(0, deadline - time.monotonic()))):
                    continue
                chunk = os.read(process.stdout.fileno(), min(8192, cap + 1 - len(data)))
                if not chunk:
                    remaining = max(0.001, deadline - time.monotonic())
                    return subprocess.CompletedProcess(
                        list(argv), process.wait(timeout=remaining), bytes(data)
                    )
                data.extend(chunk)
                if len(data) > cap:
                    raise QuotaError("QUOTA_PROCESS_OUTPUT_LIMIT")
            raise QuotaError("QUOTA_PROCESS_TIMEOUT")
    except (OSError, subprocess.TimeoutExpired):
        raise QuotaError("QUOTA_PROCESS_UNAVAILABLE") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()
        if process.stdin is not None:
            process.stdin.close()
