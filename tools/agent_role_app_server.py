"""Fixed ephemeral App Server role transport with first-party error attribution."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


class RoleError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.machine_code = code


class QuotaInterrupted(RoleError):
    def __init__(
        self,
        *,
        role: str,
        invocation: str,
        thread: str,
        turn: str,
        code: str,
        backend_digest: str,
    ) -> None:
        super().__init__("ROLE_QUOTA_INTERRUPTED")
        self.event = {
            "schema_version": "1.0",
            "role": role,
            "invocation_digest": invocation,
            "thread_digest": hashlib.sha256(thread.encode()).hexdigest(),
            "turn_digest": hashlib.sha256(turn.encode()).hexdigest(),
            "error_code": code,
            "backend_digest": backend_digest,
            "outcome": "UNKNOWN",
        }


def run(
    argv: Sequence[str],
    *,
    role: str,
    workdir: Path,
    prompt: str,
    schema: dict[str, Any],
    model: str | None,
    timeout: int,
    output_limit: int,
    invocation: str,
    backend_digest: str,
    environment: dict[str, str],
    guard: Callable[[], None],
) -> dict[str, Any]:
    """Caller must provide the verified outer namespace; no direct fallback."""
    if (
        os.name != "posix"
        or role not in {"executor", "controller"}
        or not argv
        or "--ro-bind" not in argv
    ):
        raise RoleError("ROLE_SANDBOX_UNSUPPORTED")
    guard()
    set_blocking = getattr(os, "set_blocking", None)
    if not callable(set_blocking):
        raise RoleError("ROLE_SANDBOX_UNSUPPORTED")
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=workdir,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        raise RoleError("ROLE_BACKEND_UNAVAILABLE") from None
    assert process.stdin is not None and process.stdout is not None
    deadline = time.monotonic() + timeout
    buffer = b""
    outgoing = bytearray()
    set_blocking(process.stdin.fileno(), False)
    selector: selectors.BaseSelector
    total = 0
    thread: str | None = None
    turn: str | None = None
    terminal_error: str | None = None
    result_text: str | None = None
    expected_rpc = 1
    sandbox = "read-only" if role == "controller" else "danger-full-access"

    def send(value: dict[str, Any]) -> None:
        assert process.stdin is not None
        outgoing.extend(json.dumps(value, separators=(",", ":")).encode() + b"\n")
        if len(outgoing) > 4_000_000:
            raise RoleError("ROLE_INPUT_LIMIT")

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "pgw-role", "version": "1.0"}},
            }
        )
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                guard()
                if outgoing:
                    try:
                        written = os.write(process.stdin.fileno(), outgoing[:8192])
                    except BlockingIOError:
                        written = 0
                    del outgoing[:written]
                if not selector.select(min(0.01, max(0, deadline - time.monotonic()))):
                    continue
                chunk = os.read(process.stdout.fileno(), 8192)
                if not chunk:
                    raise RoleError("ROLE_BACKEND_INCOMPLETE")
                total += len(chunk)
                if total > 4_000_000:
                    raise RoleError("ROLE_EVENT_OUTPUT_LIMIT")
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        raise RoleError("ROLE_PROTOCOL_INVALID") from None
                    if not isinstance(event, dict):
                        raise RoleError("ROLE_PROTOCOL_INVALID")
                    if "method" in event and "id" in event:
                        # Server requests never confer permission, including approvals.
                        raise RoleError("ROLE_SERVER_REQUEST_FORBIDDEN")
                    rpc_id = event.get("id")
                    if rpc_id is not None:
                        if type(rpc_id) is not int or rpc_id != expected_rpc:
                            raise RoleError("ROLE_PROTOCOL_INVALID")
                        expected_rpc += 1
                    if rpc_id is not None and "error" in event:
                        raise RoleError("ROLE_RPC_FAILED")
                    if rpc_id == 1:
                        send({"method": "initialized", "params": {}})
                        params: dict[str, Any] = {
                            "ephemeral": True,
                            "cwd": str(workdir),
                            "approvalPolicy": "never",
                            "sandbox": sandbox,
                            "config": {
                                "project_doc_max_bytes": 0,
                                "projects": {
                                    str(workdir): {"trust_level": "untrusted"}
                                },
                            },
                        }
                        if model is not None:
                            params["model"] = model
                        send({"id": 2, "method": "thread/start", "params": params})
                    elif rpc_id == 2:
                        response = event.get("result")
                        if (
                            not isinstance(response, dict)
                            or response.get("approvalPolicy") != "never"
                            or response.get("cwd") != str(workdir)
                            or response.get("instructionSources") != []
                            or (model is not None and response.get("model") != model)
                            or not isinstance(response.get("sandbox"), dict)
                            or response["sandbox"].get("type")
                            != (
                                "readOnly"
                                if role == "controller"
                                else "dangerFullAccess"
                            )
                        ):
                            raise RoleError("ROLE_EFFECTIVE_POLICY_UNVERIFIED")
                        thread_value = response.get("thread")
                        value = (
                            thread_value.get("id")
                            if isinstance(thread_value, dict)
                            else None
                        )
                        if not isinstance(value, str) or not value or len(value) > 128:
                            raise RoleError("ROLE_PROTOCOL_INVALID")
                        thread = value
                        send(
                            {
                                "id": 3,
                                "method": "turn/start",
                                "params": {
                                    "threadId": thread,
                                    "input": [{"type": "text", "text": prompt}],
                                    "cwd": str(workdir),
                                    "approvalPolicy": "never",
                                    "outputSchema": schema,
                                    "sandboxPolicy": {"type": "readOnly"}
                                    if role == "controller"
                                    else {
                                        "type": "externalSandbox",
                                        "networkAccess": "enabled",
                                    },
                                },
                            }
                        )
                    elif rpc_id == 3:
                        response = event.get("result")
                        value = (
                            response["turn"].get("id")
                            if isinstance(response, dict)
                            and isinstance(response.get("turn"), dict)
                            else None
                        )
                        if not isinstance(value, str) or not value or len(value) > 128:
                            raise RoleError("ROLE_PROTOCOL_INVALID")
                        turn = value
                    else:
                        method, notification = event.get("method"), event.get("params")
                        if not isinstance(notification, dict):
                            continue
                        if method in {"error", "item/completed", "turn/completed"}:
                            if (
                                thread is None
                                or turn is None
                                or notification.get("threadId") != thread
                            ):
                                raise RoleError("ROLE_EVENT_BINDING_CHANGED")
                            event_turn = (
                                notification.get("turnId")
                                if method != "turn/completed"
                                else (
                                    notification["turn"].get("id")
                                    if isinstance(notification.get("turn"), dict)
                                    else None
                                )
                            )
                            if event_turn != turn:
                                raise RoleError("ROLE_EVENT_BINDING_CHANGED")
                        if method == "error":
                            error = notification.get("error")
                            code = (
                                error.get("codexErrorInfo")
                                if isinstance(error, dict)
                                else None
                            )
                            if (
                                notification.get("willRetry") is False
                                and isinstance(code, str)
                                and code
                                in {
                                    "rateLimitExceeded",
                                    "usageLimitExceeded",
                                }
                            ):
                                terminal_error = code
                            elif notification.get("willRetry") is not True:
                                raise RoleError("ROLE_NON_QUOTA_ERROR")
                        elif method == "item/completed":
                            item = notification.get("item")
                            if (
                                isinstance(item, dict)
                                and item.get("type") == "agentMessage"
                            ):
                                text = item.get("text")
                                if (
                                    not isinstance(text, str)
                                    or len(text.encode()) > output_limit
                                ):
                                    raise RoleError("ROLE_OUTPUT_LIMIT")
                                result_text = text
                        elif method == "turn/completed":
                            status = notification["turn"].get("status")
                            guard()
                            if terminal_error is not None and status == "failed":
                                assert thread is not None and turn is not None
                                raise QuotaInterrupted(
                                    role=role,
                                    invocation=invocation,
                                    thread=thread,
                                    turn=turn,
                                    code=terminal_error,
                                    backend_digest=backend_digest,
                                )
                            if (
                                status != "completed"
                                or terminal_error is not None
                                or result_text is None
                            ):
                                raise RoleError("ROLE_COMPLETION_INVALID")
                            try:
                                payload = json.loads(result_text)
                            except ValueError:
                                raise RoleError("ROLE_OUTPUT_INVALID") from None
                            if not isinstance(payload, dict):
                                raise RoleError("ROLE_OUTPUT_INVALID")
                            return payload
            raise RoleError("ROLE_BACKEND_TIMEOUT")
    except OSError:
        raise RoleError("ROLE_BACKEND_UNAVAILABLE") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
