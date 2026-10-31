"""Closed private final-preparation records; valid shapes grant no authority."""

from __future__ import annotations

import math
import re
from typing import Any, NoReturn

from tools import agent_continuation as records
from tools.agent_epic_loop import PhaseBlocked

IDENTITY = {"repository", "epic", "mandate_digest", "launch_digest"}
PROFILE = {
    "repository",
    "epic",
    "roadmap_ref",
    "policy_sha",
    "mandate_digest",
    "owner_identity",
    "max_report_chars",
    "max_artifact_bytes",
    "max_final_model_calls",
}
CANDIDATE = IDENTITY | {
    "roadmap_ref",
    "policy_sha",
    "owner_identity",
    "pr",
    "main_sha",
    "roadmap_sha",
    "preparation_digest",
    "pr_ci_digest",
}
APPROVAL = IDENTITY | {
    "owner_identity",
    "approved_at",
    "pr",
    "main_sha",
    "roadmap_sha",
    "policy_sha",
    "candidate_digest",
    "pr_ci_digest",
    "final_demo_digest",
}
OWNER_PIN = IDENTITY | {
    "owner_identity",
    "candidate_digest",
    "approval_digest",
    "approved_at",
    "installation_receipt_digest",
}
OWNER_RECEIPT = (OWNER_PIN - {"installation_receipt_digest"}) | {
    "kind",
    "owner_command_digest",
}
REQUEST = IDENTITY | {
    "operation",
    "repository",
    "epic",
    "roadmap_ref",
    "main_sha",
    "roadmap_sha",
    "policy_sha",
    "preparation_digest",
}
ASSESSMENT = {
    "status",
    "machine_code",
    "snapshot_digest",
    "local_gate_digest",
    "review_digest",
    "children_digest",
    "task_delivery_digest",
    "learning_digest",
}
REPORT = {
    "repository",
    "epic",
    "candidate_digest",
    "pr",
    "main_sha",
    "roadmap_sha",
    "status",
    "counts",
    "learning_status",
    "comparison_status",
}
STATE = IDENTITY | {
    "phase",
    "candidate_digest",
    "pending_operation_id",
    "calls_consumed",
    "blocked_code",
}
ROLE_STATE = IDENTITY | {"calls_consumed", "pending_reservation_digest"}
RESERVATION = IDENTITY | {
    "ordinal",
    "preparation_identity_digest",
    "input_digest",
    "backend_digest",
    "settings_digest",
    "started_at",
    "deadline_at",
    "status",
    "output_digest",
}
BLOCK_CODES = frozenset(
    {
        "IDENTITY_INVALID",
        "ROADMAP_SHA_CHANGED",
        "MAIN_CHANGED",
        "OPEN_REQUIRED_TASKS",
        "FINAL_LOCAL_GATE_FAILED",
        "FINAL_DIFF_REVIEW_REQUIRED",
        "FINAL_ROLE_OUTCOME_UNKNOWN",
        "PROMPT_LESSON_GATE_FAILED",
        "FINAL_APPROVAL_INVALID",
        "MANDATE_EXPIRED",
        "MANDATE_REVOKED",
        "MANDATE_DURATION_LIMIT",
        "MANDATE_TASK_LIMIT",
        "MANDATE_FOLLOW_UP_LIMIT",
        "FINAL_STAGE_GATE_INVALID",
        "MAIN_POST_MERGE_CI_FAILED",
        "FINAL_RECEIPT_INVALID",
        "FINAL_TREE_MISMATCH",
        "FINAL_WORKTREE_INVALID",
        "FINAL_DIFF_TOO_LARGE",
        "ESCALATE_UNKNOWN_OUTCOME",
        "MANDATE_AUTHORITY_MISSING",
    }
)


def invalid() -> NoReturn:
    raise PhaseBlocked("FINAL_APPROVAL_INVALID")


def integer(value: Any, maximum: int = 2**53 - 1, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        invalid()
    return int(value)


def hash_value(value: Any, *, sha: bool = False, tagged: bool = False) -> str:
    pattern = (
        r"[0-9a-f]{40}"
        if sha
        else (r"sha256:[0-9a-f]{64}" if tagged else r"[0-9a-f]{64}")
    )
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        invalid()
    return str(value)


def _identity(value: dict[str, Any]) -> None:
    if (
        not isinstance(value["repository"], str)
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}",
            value["repository"],
        )
        is None
    ):
        invalid()
    integer(value["epic"], 2147483647, 1)
    for key in ("mandate_digest", "launch_digest"):
        if key in value:
            hash_value(value[key], tagged=True)
    if "owner_identity" in value:
        owner = value["owner_identity"]
        if (
            not isinstance(owner, str)
            or not 1 <= len(owner) <= 256
            or owner != owner.strip()
            or any(ord(char) < 32 for char in owner)
        ):
            invalid()
    if "roadmap_ref" in value:
        ref = value["roadmap_ref"]
        if (
            not isinstance(ref, str)
            or len(ref) > 256
            or not ref.startswith(f"roadmap/{value['epic']}-")
            or re.fullmatch(r"[A-Za-z0-9_./-]+", ref) is None
            or ".." in ref
            or "//" in ref
            or ref.endswith(("/", "."))
        ):
            invalid()
    for key in ("main_sha", "roadmap_sha", "policy_sha"):
        if key in value:
            hash_value(value[key], sha=True)
    if "pr" in value:
        integer(value["pr"], 2147483647, 1)


def _sealed(
    raw: Any,
    fields: set[str],
    *,
    version: str = "1.0",
    self_digest: str = "record_digest",
) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != fields | {
        "schema_version",
        self_digest,
    }:
        invalid()
    value = dict(raw)
    if value["schema_version"] != version:
        invalid()
    hash_value(value[self_digest])
    if value[self_digest] != records.digest(
        {k: v for k, v in value.items() if k != self_digest}
    ):
        invalid()
    if "repository" in value:
        _identity(value)
    return value


def seal(
    value: dict[str, Any], *, self_digest: str = "record_digest"
) -> dict[str, Any]:
    original = {key: item for key, item in value.items() if key != self_digest}
    return {**original, self_digest: records.digest(original)}


def profile(raw: Any) -> dict[str, Any]:
    value = _sealed(raw, PROFILE)
    integer(value["max_report_chars"], 20000, 1)
    integer(value["max_artifact_bytes"], 262144, 1)
    integer(value["max_final_model_calls"], 1, 1)
    return value


def candidate(raw: Any) -> dict[str, Any]:
    value = _sealed(raw, CANDIDATE, self_digest="candidate_digest")
    for key in ("preparation_digest", "pr_ci_digest"):
        hash_value(value[key])
    return value


def approval(raw: Any) -> dict[str, Any]:
    value = _sealed(raw, APPROVAL, version="2.0", self_digest="approved_digest")
    integer(value["approved_at"])
    for key in ("candidate_digest", "pr_ci_digest", "final_demo_digest"):
        hash_value(value[key])
    return value


def owner_pin(raw: Any) -> dict[str, Any]:
    value = _sealed(raw, OWNER_PIN)
    integer(value["approved_at"])
    for key in ("candidate_digest", "approval_digest", "installation_receipt_digest"):
        hash_value(value[key])
    return value


def owner_receipt(raw: Any) -> dict[str, Any]:
    value = _sealed(raw, OWNER_RECEIPT)
    if value["kind"] != "OWNER_FINAL_APPROVAL_PIN":
        invalid()
    integer(value["approved_at"])
    for key in ("candidate_digest", "approval_digest", "owner_command_digest"):
        hash_value(value[key])
    return value


def request(raw: Any) -> dict[str, Any]:
    value = _sealed(raw, REQUEST, self_digest="operation_id")
    if value["operation"] != "create_roadmap_pr":
        invalid()
    hash_value(value["preparation_digest"])
    return value


def assessment(raw: Any) -> dict[str, Any]:
    value = _sealed(raw, ASSESSMENT)
    status, code = value["status"], value["machine_code"]
    if not (
        (status == "READY" and code == "OK")
        or (status == "BLOCKED" and isinstance(code, str) and code in BLOCK_CODES)
    ):
        invalid()
    for key in ASSESSMENT - {"status", "machine_code"}:
        hash_value(value[key])
    return value


def report(raw: Any) -> dict[str, Any]:
    value = _sealed(raw, REPORT)
    hash_value(value["candidate_digest"])
    for key in ("learning_status", "comparison_status"):
        if not isinstance(value[key], str):
            invalid()
    counts = value["counts"]
    if not isinstance(counts, dict) or set(counts) != {"total", "closed", "open"}:
        invalid()
    for count in counts.values():
        integer(count, 2147483647)
    if (
        counts["total"] != counts["closed"] + counts["open"]
        or counts["open"] != 0
        or value["status"] != "WAITING_OWNER_FINAL_APPROVAL"
        or value["learning_status"] not in {"NONE_APPLIED", "VERIFIED_APPLIED"}
        or value["comparison_status"] not in {"UNMEASURED", "VERIFIED_COMPARABLE"}
        or (
            value["learning_status"] == "NONE_APPLIED"
            and value["comparison_status"] != "UNMEASURED"
        )
    ):
        invalid()
    return value


def state(raw: Any) -> dict[str, Any]:
    value = _sealed(raw, STATE)
    phases = {
        "PREPARING",
        "WAITING_OWNER_FINAL_APPROVAL",
        "MERGING",
        "MAIN_CI_PENDING",
        "DONE",
        "BLOCKED",
    }
    if not isinstance(value["phase"], str) or value["phase"] not in phases:
        invalid()
    integer(value["calls_consumed"], 1)
    for key in ("candidate_digest", "pending_operation_id"):
        if value[key] is not None:
            hash_value(value[key])
    if value["phase"] in {
        "WAITING_OWNER_FINAL_APPROVAL",
        "MERGING",
        "MAIN_CI_PENDING",
        "DONE",
    }:
        hash_value(value["candidate_digest"])
    if value["phase"] == "BLOCKED":
        if (
            not isinstance(value["blocked_code"], str)
            or value["blocked_code"] not in BLOCK_CODES
        ):
            invalid()
    elif value["blocked_code"] is not None:
        invalid()
    return value


def role_state(raw: Any) -> dict[str, Any]:
    value = _sealed(raw, ROLE_STATE)
    integer(value["calls_consumed"], 1)
    if value["pending_reservation_digest"] is not None:
        hash_value(value["pending_reservation_digest"])
        if value["calls_consumed"] != 1:
            invalid()
    return value


def reservation(raw: Any) -> dict[str, Any]:
    value = _sealed(raw, RESERVATION)
    integer(value["ordinal"], 1, 1)
    start = integer(value["started_at"])
    integer(value["deadline_at"], minimum=start + 1)
    for key in ("preparation_identity_digest", "input_digest", "backend_digest"):
        hash_value(value[key])
    for key in ("settings_digest", "output_digest"):
        if value[key] is not None:
            hash_value(value[key])
    if not isinstance(value["status"], str):
        invalid()
    if value["status"] == "COMPLETED":
        hash_value(value["settings_digest"])
        hash_value(value["output_digest"])
    elif value["status"] not in {"RESERVED", "UNKNOWN"}:
        invalid()
    return value


def aggregate_environment(raw: Any) -> dict[str, Any]:
    value = _sealed(
        raw,
        IDENTITY
        | {
            "kind",
            "preparation_identity_digest",
            "input_digest",
            "review_source_digest",
            "launcher_python_version",
            "launcher_executable_sha256",
        },
    )
    if value["kind"] != "FINAL_AGGREGATE_ENVIRONMENT":
        invalid()
    for key in (
        "preparation_identity_digest",
        "input_digest",
        "review_source_digest",
        "launcher_executable_sha256",
    ):
        hash_value(value[key])
    launcher_version(value["launcher_python_version"])
    return value


def launcher_version(raw: Any) -> str:
    if not isinstance(raw, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", raw) is None:
        invalid()
    return str(raw)


OWNER_WAIT = {
    "invocation_id",
    "frame_digest",
    "wait_identity_digest",
    "runtime_config_digest",
    "control_digest",
    "stage_started_at",
    "deadline_at",
}


def owner_wait(raw: Any) -> dict[str, Any]:
    """Closed locator shape only; not source, time or owner admission."""
    if not isinstance(raw, dict) or set(raw) != OWNER_WAIT:
        invalid()
    value = dict(raw)
    if (
        not isinstance(value["invocation_id"], str)
        or re.fullmatch(r"[0-9a-f]{32}", value["invocation_id"]) is None
    ):
        invalid()
    for key in (
        "frame_digest",
        "wait_identity_digest",
        "runtime_config_digest",
        "control_digest",
    ):
        hash_value(value[key], tagged=True)
    for key in ("stage_started_at", "deadline_at"):
        timestamp = value[key]
        if type(timestamp) not in (int, float) or timestamp < 0:
            invalid()
        # All ints are finite; avoid converting arbitrarily large ints to float.
        if type(timestamp) is float and not math.isfinite(timestamp):
            invalid()
    return value


def state2(raw: Any) -> dict[str, Any]:
    """Validate state2 without upgrading, writing or admitting any state."""
    value = _sealed(raw, STATE | {"owner_wait"}, version="2.0")
    # Pure legacy-shape projection; never persisted or claimed as an original.
    state(seal({**{key: value[key] for key in STATE}, "schema_version": "1.0"}))
    if value["owner_wait"] is not None:
        value["owner_wait"] = owner_wait(value["owner_wait"])
    return value


def state2_transition(previous: Any, following: Any) -> dict[str, Any]:
    """Check launch-bound immutability; future writers must enforce this."""
    before, after = state2(previous), state2(following)
    if any(before[key] != after[key] for key in IDENTITY):
        invalid()
    if after["calls_consumed"] < before["calls_consumed"]:
        invalid()
    if before["owner_wait"] is not None:
        if after["owner_wait"] is None or records.digest(
            before["owner_wait"]
        ) != records.digest(after["owner_wait"]):
            invalid()
    return after
