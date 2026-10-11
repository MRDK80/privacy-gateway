"""Pinned bounded stdio App Server reads; no inference or account mutation RPC."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools.agent_quota import QuotaError, select
from tools.agent_quota_process import run as bounded_run

CAP = 262144


def _pinned(source: dict[str, Any]) -> Path:
    path = Path(source["executable"])
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or not os.access(path, os.X_OK)
            or any(p.is_symlink() for p in path.parents)
        ):
            raise QuotaError("QUOTA_SOURCE_UNSAFE")
        if hashlib.sha256(path.read_bytes()).hexdigest() != source["executable_sha256"]:
            raise QuotaError("QUOTA_SOURCE_CHANGED")
    except OSError:
        raise QuotaError("QUOTA_SOURCE_UNAVAILABLE") from None
    return path


def read(
    source: dict[str, Any],
    limit_id: str,
    *,
    guard: Callable[[], None],
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Request exactly one quota snapshot and reap the ephemeral source child."""
    if os.name != "posix":
        raise QuotaError("QUOTA_SOURCE_UNSUPPORTED")
    path = _pinned(source)
    guard()
    timeout = (
        min(source["request_timeout_seconds"], timeout_seconds)
        if timeout_seconds is not None
        else source["request_timeout_seconds"]
    )
    if timeout <= 0:
        raise QuotaError("QUOTA_DEADLINE_EXCEEDED")
    deadline = time.monotonic() + timeout
    try:
        version = bounded_run(
            [str(path), "--version"],
            timeout=min(5, timeout),
            cap=4096,
            guard=guard,
        )
        if version.returncode or len(version.stdout) > 4096:
            raise QuotaError("QUOTA_SOURCE_VERSION_INVALID")
        # Canonical capped version bytes, never persisted as raw diagnostics.
        if (
            hashlib.sha256(version.stdout.strip()).hexdigest()
            != source["version_digest"]
        ):
            raise QuotaError("QUOTA_SOURCE_CHANGED")
        guard()
        _pinned(source)
        process = subprocess.Popen(
            [str(path), "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise QuotaError("QUOTA_SOURCE_UNAVAILABLE") from None
    assert process.stdin is not None and process.stdout is not None
    buffer = b""
    total = 0
    initialized = False
    expected_rpc = 1
    try:

        def send(value: dict[str, Any]) -> None:
            assert process.stdin is not None
            process.stdin.write(
                json.dumps(value, separators=(",", ":")).encode() + b"\n"
            )
            process.stdin.flush()

        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "pgw-quota-read", "version": "1.0"}},
            }
        )
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                guard()
                ready = selector.select(min(0.1, max(0, deadline - time.monotonic())))
                if not ready:
                    continue
                chunk = os.read(process.stdout.fileno(), 8192)
                if not chunk:
                    raise QuotaError("QUOTA_SOURCE_INCOMPLETE")
                total += len(chunk)
                if total > CAP:
                    raise QuotaError("QUOTA_SOURCE_OUTPUT_LIMIT")
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        raise QuotaError("QUOTA_SOURCE_PROTOCOL_INVALID") from None
                    if not isinstance(event, dict):
                        raise QuotaError("QUOTA_SOURCE_PROTOCOL_INVALID")
                    # Unexpected server requests are never answered/authorized.
                    if "method" in event and "id" in event:
                        raise QuotaError("QUOTA_SOURCE_REQUEST_FORBIDDEN")
                    rpc_id = event.get("id")
                    if rpc_id is not None:
                        if type(rpc_id) is not int or rpc_id != expected_rpc:
                            raise QuotaError("QUOTA_SOURCE_PROTOCOL_INVALID")
                        expected_rpc += 1
                    if rpc_id == 1 and not initialized:
                        if "error" in event or "result" not in event:
                            raise QuotaError("QUOTA_SOURCE_INITIALIZE_FAILED")
                        initialized = True
                        send({"method": "initialized", "params": {}})
                        send(
                            {"id": 2, "method": "account/rateLimits/read", "params": {}}
                        )
                    elif rpc_id == 2:
                        if not initialized or "error" in event or "result" not in event:
                            raise QuotaError("QUOTA_UNKNOWN")
                        guard()
                        _pinned(source)
                        return select(event["result"], limit_id)
            raise QuotaError("QUOTA_SOURCE_TIMEOUT")
    except (OSError, BrokenPipeError):
        raise QuotaError("QUOTA_SOURCE_UNAVAILABLE") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
