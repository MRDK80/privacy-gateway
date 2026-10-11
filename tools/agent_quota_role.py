"""Trusted adapter error channel, separate from model-authored role reports."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from tools import agent_orchestrate as workflow
from tools.agent_quota import QuotaError
from tools.agent_quota_process import run as bounded_run
from tools.agent_quota_wait import bare_digest

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
