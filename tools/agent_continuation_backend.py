"""Closed owner backend and trusted completion receipts for config6 only."""

from __future__ import annotations

import re
import stat
from typing import Any

from tools import agent_continuation as records
from tools import agent_orchestrate as workflow


def model(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise records.ContinuationError("CONTINUATION_BACKEND_INVALID")
    return value


def document(raw: Any) -> dict[str, Any]:
    value = records.exact(
        raw,
        {
            "schema_version",
            "source",
            "model",
            "original_reasoning",
            "repeat_reasoning_levels",
        },
    )
    if (
        value["schema_version"] != "1.0"
        or value["original_reasoning"] != "low"
        or value["repeat_reasoning_levels"] != ["medium", "high"]
    ):
        raise records.ContinuationError("CONTINUATION_BACKEND_INVALID")
    model(value["model"])
    source = records.exact(
        value["source"],
        {
            "executable",
            "executable_sha256",
            "version_digest",
            "request_timeout_seconds",
        },
    )
    path = source["executable"]
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or any(c in path for c in "\r\n\x00")
    ):
        raise records.ContinuationError("CONTINUATION_BACKEND_INVALID")
    records.integer(source["request_timeout_seconds"], 120, minimum=1)
    for key in ("executable_sha256", "version_digest"):
        records.hash_value(source[key])
    return value


def completed_receipt(
    owner: dict[str, Any],
    observed: dict[str, Any],
    *,
    role: str,
    invocation: str,
    output: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    owner = document(owner)
    records.exact(observed, {"model", "reasoning", "thread_digest", "turn_digest"})
    if (
        role not in {"executor", "controller"}
        or observed["model"] != owner["model"]
        or observed["reasoning"] not in ["low", "medium", "high"]
    ):
        raise records.ContinuationError("CONTINUATION_REASONING_UNVERIFIED")
    for key in ("thread_digest", "turn_digest"):
        records.hash_value(observed[key])
    records.hash_value(invocation)
    terminal = {
        "role": role,
        "invocation_digest": invocation,
        "thread_digest": observed["thread_digest"],
        "turn_digest": observed["turn_digest"],
        "status": "completed",
        "validated_output_digest": records.digest(output),
        "adapter_exit_code": 0,
    }
    settings = {"model": observed["model"], "reasoning": observed["reasoning"]}
    value = {
        "schema_version": "1.0",
        "role": role,
        "invocation_digest": invocation,
        "backend_digest": records.digest(owner),
        "requested": dict(settings),
        "effective": dict(settings),
        "thread_digest": observed["thread_digest"],
        "turn_digest": observed["turn_digest"],
        "outcome": "COMPLETED",
        "terminal_digest": records.digest(terminal),
    }
    value["receipt_digest"] = records.digest(value)
    return value, terminal


def validate_completion(
    raw: Any,
    raw_terminal: Any,
    owner: dict[str, Any],
    *,
    role: str,
    invocation: str,
    output: dict[str, Any] | None,
    observed_exit: int | None,
    reasoning: str,
    expected_output_digest: str | None = None,
) -> dict[str, Any]:
    owner = document(owner)
    value = records.exact(
        raw,
        {
            "schema_version",
            "role",
            "invocation_digest",
            "backend_digest",
            "requested",
            "effective",
            "thread_digest",
            "turn_digest",
            "outcome",
            "terminal_digest",
            "receipt_digest",
        },
    )
    terminal = records.exact(
        raw_terminal,
        {
            "role",
            "invocation_digest",
            "thread_digest",
            "turn_digest",
            "status",
            "validated_output_digest",
            "adapter_exit_code",
        },
    )
    output_digest = (
        records.digest(output) if output is not None else expected_output_digest
    )
    records.hash_value(output_digest)
    expected_settings = {"model": owner["model"], "reasoning": reasoning}
    if (
        reasoning not in ["low", "medium", "high"]
        or type(observed_exit) is not int
        or observed_exit != 0
        or value["schema_version"] != "1.0"
        or value["role"] != role
        or value["invocation_digest"] != invocation
        or value["backend_digest"] != records.digest(owner)
        or value["requested"] != expected_settings
        or value["effective"] != expected_settings
        or value["outcome"] != "COMPLETED"
        or value["receipt_digest"]
        != records.digest({k: v for k, v in value.items() if k != "receipt_digest"})
        or value["terminal_digest"] != records.digest(terminal)
        or terminal["role"] != role
        or terminal["invocation_digest"] != invocation
        or terminal["thread_digest"] != value["thread_digest"]
        or terminal["turn_digest"] != value["turn_digest"]
        or terminal["status"] != "completed"
        or terminal["validated_output_digest"] != output_digest
        or type(terminal["adapter_exit_code"]) is not int
        or terminal["adapter_exit_code"] != observed_exit
    ):
        raise records.ContinuationError("CONTINUATION_SETTINGS_RECEIPT_INVALID")
    for key in (
        "invocation_digest",
        "backend_digest",
        "thread_digest",
        "turn_digest",
        "terminal_digest",
        "receipt_digest",
    ):
        if (
            not isinstance(value[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
        ):
            raise records.ContinuationError("CONTINUATION_SETTINGS_RECEIPT_INVALID")
    return value


class SettingsStore:
    """Capped owner-only source receipts; symlinks and hardlinks are rejected."""

    def __init__(self, directory: Any, root: Any, cap: int) -> None:
        from tools.agent_continuation_store import Store
        from tools.agent_epic_runtime_cli import secure_directory

        secure_directory(directory.parent, root)
        if not directory.exists() and not directory.is_symlink():
            directory.mkdir(mode=0o700)
        self.store = Store(directory, root)
        self.cap = cap

    def load(self, invocation: str) -> dict[str, Any] | None:
        records.hash_value(invocation)
        return self.store.load("receipt-" + invocation, cap=self.cap)

    def save(self, invocation: str, value: dict[str, Any]) -> None:
        records.hash_value(invocation)
        if self.load(invocation) is not None:
            raise records.ContinuationError("CONTINUATION_SETTINGS_CONFLICT")
        self.store.save("receipt-" + invocation, value, cap=self.cap)


class SettingsAdapter(workflow.CommandAdapter):
    """Trusted fixed child envelope is verified after its observed successful exit."""

    def __init__(
        self,
        delegate: Any,
        owner: dict[str, Any],
        profile: dict[str, Any],
        state: Any,
        guard: Any,
    ) -> None:
        self.delegate = delegate
        self.owner = document(owner)
        self.profile = records.profile(profile)
        self.state, self.guard = state, guard
        self.backend_digest = getattr(delegate, "backend_digest", None)
        self.quota_resume_enabled = getattr(delegate, "quota_resume_enabled", False)
        self.store = SettingsStore(
            state / "role-settings",
            delegate.root,
            self.profile["limits"]["max_record_bytes"],
        )
        self.executor_command = delegate.executor_command
        self.controller_command = delegate.controller_command
        self.root = delegate.root
        self.timeout_seconds = delegate.timeout_seconds
        self.output_limit = delegate.output_limit

    def _settings_call(self, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        from tools import agent_epic_loop as loop
        from tools import agent_epic_runtime_cli as guards

        directory = self.store.store.directory / "ownership"
        self.guard()
        if not directory.exists() and not directory.is_symlink():
            directory.mkdir(mode=0o700)
        guards.secure_directory(directory, self.root)
        path = directory / "epic-loop.lock"
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != directory.stat().st_uid):
                raise records.ContinuationError("CONTINUATION_STORAGE_UNSAFE")
        with loop.RunnerLock(directory):
            return self._locked_settings_call(role, payload)

    def _locked_settings_call(
        self, role: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.guard()
        safe = {k: v for k, v in payload.items() if k != "contract_object"}
        invocation = records.digest(safe)
        if self.store.load(invocation) is not None:
            raise records.ContinuationError("CONTINUATION_SETTINGS_CONFLICT")
        records.preflight_request(safe, self.profile, role=role)
        iteration = safe.get("repair_iteration")
        if type(iteration) is not int or not 0 <= iteration <= 2:
            raise records.ContinuationError("CONTINUATION_REASONING_UNVERIFIED")
        command = (
            self.executor_command if role == "executor" else self.controller_command
        )
        value = records.exact(
            self.delegate._call(command, payload),
            {"role_output", "settings_receipt", "terminal_fact"},
        )
        output = value["role_output"]
        if not isinstance(output, dict):
            raise records.ContinuationError("CONTINUATION_SETTINGS_RECEIPT_INVALID")
        receipt = validate_completion(
            value["settings_receipt"],
            value["terminal_fact"],
            self.owner,
            role=role,
            invocation=invocation,
            output=output,
            observed_exit=0,
            reasoning=["low", "medium", "high"][iteration],
        )
        self.guard()
        self.store.save(
            invocation, {"receipt": receipt, "terminal": value["terminal_fact"]}
        )
        return output

    def execute(self, request: dict[str, Any], session_id: str) -> dict[str, Any]:
        return self._settings_call("executor", {**request, "session_id": session_id})

    def review(
        self, request: dict[str, Any], trusted_policy: dict[str, str], session_id: str
    ) -> dict[str, Any]:
        return self._settings_call(
            "controller",
            {**request, "trusted_policy": trusted_policy, "session_id": session_id},
        )
