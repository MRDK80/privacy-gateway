"""Trusted adapter error channel, separate from model-authored role reports."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from tools import agent_orchestrate as workflow
from tools.agent_quota import QuotaError
from tools.agent_quota_process import run as bounded_run
from tools.agent_quota_wait import bare_digest


@dataclass
class _MalformedSink:
    fd: int
    identity: tuple[int, int]
    consumed: bool = False


_malformed_sink: ContextVar[_MalformedSink | None] = ContextVar(
    "_malformed_sink", default=None,
)


def _diagnostic_identity(fd: int) -> tuple[int, int]:
    if type(fd) is not int or fd < 0:
        raise ValueError("DIAGNOSTIC_SETUP_STOP")
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode) or info.st_uid != getattr(os, "getuid")()
        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
        or info.st_size != 0 or os.lseek(fd, 0, os.SEEK_CUR) != 0
    ):
        raise ValueError("DIAGNOSTIC_SETUP_STOP")
    return info.st_dev, info.st_ino


@contextmanager
def _malformed_diagnostic(fd: int) -> Iterator[None]:
    """Private synthetic-harness binding; no environment/config activation."""
    try:
        if _malformed_sink.get() is not None:
            raise ValueError("DIAGNOSTIC_SETUP_STOP")
        identity = _diagnostic_identity(fd)
        os.set_inheritable(fd, False)
    except Exception:
        raise RuntimeError("DIAGNOSTIC_SETUP_STOP") from None
    token = _malformed_sink.set(_MalformedSink(fd, identity))
    try:
        yield
    finally:
        try:
            _malformed_sink.reset(token)
        except Exception:
            # A diagnostic cleanup error cannot replace the original outcome.
            pass


def _note_malformed_output(role: str, returncode: int, stdout_bytes: int) -> None:
    sink = _malformed_sink.get()
    if sink is None or sink.consumed:
        return
    # Context copies share this latch. Failed/partial writes never refund it.
    sink.consumed = True
    try:
        if role not in {"executor", "controller"} or type(stdout_bytes) is not int:
            return
        if stdout_bytes < 0 or _diagnostic_identity(sink.fd) != sink.identity:
            return
        code: int | str = (
            returncode if type(returncode) is int and -255 <= returncode <= 255
            else "OTHER"
        )
        raw = (json.dumps({
            "schema_version": "diagnostic1", "role": role,
            "returncode": code, "stdout_bytes": stdout_bytes,
            "parse_stage": "JSON_LOAD_VALUE_ERROR",
        }, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        if len(raw) <= 512:
            os.write(sink.fd, raw)
    except Exception:
        # No raw values, exception text, retry or change to parser semantics.
        pass


EVENT_KEYS = {
    "schema_version",
    "role",
    "invocation_digest",
    "thread_digest",
    "turn_digest",
    "error_code",
    "backend_digest",
    "outcome",
}


class Interrupted(Exception):
    """A confirmed terminal quota error does not prove an executor's outcome."""

    def __init__(self, event: dict[str, Any]) -> None:
        super().__init__("ROLE_QUOTA_INTERRUPTED")
        self.event = dict(event)


def interruption(
    value: Any,
    *,
    role: str,
    payload: dict[str, Any],
    backend_digest: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"role_interruption"}:
        raise workflow.OrchestrationError("ROLE_INTERRUPTION_INVALID")
    return validate_event(
        value["role_interruption"],
        role=role,
        invocation_digest=bare_digest(payload),
        backend_digest=backend_digest,
    )


def validate_event(
    event: Any,
    *,
    role: str,
    invocation_digest: str,
    backend_digest: str,
) -> dict[str, Any]:
    if (
        not isinstance(event, dict)
        or set(event) != EVENT_KEYS
        or event["schema_version"] != "1.0"
        or event["role"] != role
        or event["invocation_digest"] != invocation_digest
        or event["backend_digest"] != backend_digest
        or event["outcome"] != "UNKNOWN"
        or not isinstance(event["error_code"], str)
        or event["error_code"] not in {"rateLimitExceeded", "usageLimitExceeded"}
    ):
        raise workflow.OrchestrationError("ROLE_INTERRUPTION_INVALID")
    for key in ("invocation_digest", "backend_digest", "thread_digest", "turn_digest"):
        if (
            not isinstance(event[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", event[key]) is None
        ):
            raise workflow.OrchestrationError("ROLE_INTERRUPTION_INVALID")
    return dict(event)


class CommandAdapter(workflow.CommandAdapter):
    """Only owner-pinned commands may instantiate this opt-in error transport."""

    quota_resume_enabled = True

    def __init__(
        self,
        *args: Any,
        backend_digest: str,
        guard: Callable[[], None],
        on_interruption: Callable[[dict[str, Any]], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.backend_digest, self.guard = backend_digest, guard
        self.on_interruption = on_interruption

    def _call(self, command: Sequence[str], payload: dict[str, Any]) -> dict[str, Any]:
        self.guard()
        safe = {
            key: value for key, value in payload.items() if key != "contract_object"
        }
        role = "executor" if tuple(command) == self.executor_command else "controller"
        if tuple(command) not in (self.executor_command, self.controller_command):
            raise workflow.OrchestrationError("UNTRUSTED_RUNTIME_COMMAND")
        try:
            completed = bounded_run(
                command,
                cwd=self.root,
                input_bytes=json.dumps(safe).encode(),
                timeout=self.timeout_seconds,
                cap=self.output_limit,
                guard=self.guard,
            )
        except QuotaError as error:
            raise workflow.OrchestrationError(error.machine_code) from None
        self.guard()
        if len(completed.stdout) > self.output_limit:
            raise workflow.OrchestrationError("OUTPUT_LIMIT")
        try:
            value = json.loads(completed.stdout)
        except ValueError:
            _note_malformed_output(role, completed.returncode, len(completed.stdout))
            raise workflow.OrchestrationError("MALFORMED_OUTPUT") from None
        if completed.returncode == 20:
            verified = interruption(
                value,
                role=role,
                payload=safe,
                backend_digest=self.backend_digest,
            )
            if self.on_interruption is not None:
                self.on_interruption(verified)
            raise Interrupted(verified)
        if completed.returncode != 0:
            raise workflow.OrchestrationError("MODEL_UNAVAILABLE")
        if not isinstance(value, dict) or "role_interruption" in value:
            raise workflow.OrchestrationError("MALFORMED_OUTPUT")
        return value
