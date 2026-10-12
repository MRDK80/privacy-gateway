"""Closed redacted recovery records; summaries never grant role or effect authority."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, cast

from tools.agent_orchestrate import OrchestrationError

PROFILE_KEYS = {
    "schema_version",
    "repository",
    "epic",
    "roadmap_ref",
    "policy_sha",
    "mandate_digest",
    "owner_identity",
    "backend_digest",
    "limits",
}
MAXIMA = {
    "threshold_permille": 950,
    "max_handoffs": 32,
    "max_prompt_bytes": 262144,
    "max_record_bytes": 65536,
    "max_findings": 128,
}
IDENTITY_KEYS = {
    "repository",
    "epic",
    "task",
    "run_id",
    "policy_sha",
    "continuation_profile_digest",
    "launch_config_digest",
}
HANDOFF_KEYS = {
    "schema_version",
    "identity",
    "contract_digest",
    "policy_digest",
    "workspace",
    "progress",
    "pending",
    "findings",
    "gates",
    "consumed",
    "source",
    "status",
    "record_digest",
}
RESERVATION_KEYS = {
    "schema_version",
    "identity",
    "contract_digest",
    "role",
    "ordinal",
    "invocation_digest",
    "checkpoint_digest",
    "started_at",
    "deadline_at",
    "consumed_digest",
    "snapshot_before",
    "snapshot_after",
    "status",
    "outcome_digest",
}
REASONING = {"low", "medium", "high", "xhigh", "max", "ultra"}


class ContinuationError(OrchestrationError):
    """Only fixed redacted machine codes."""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContinuationError("CONTINUATION_STATE_INVALID")
    return cast(dict[str, Any], value)


def integer(value: Any, maximum: int = 253402300799, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ContinuationError("CONTINUATION_STATE_INVALID")
    return value


def hash_value(value: Any, *, sha: bool = False, prefixed: bool = False) -> str:
    pattern = (
        r"[0-9a-f]{40}"
        if sha
        else (r"sha256:[0-9a-f]{64}" if prefixed else r"[0-9a-f]{64}")
    )
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ContinuationError("CONTINUATION_STATE_INVALID")
    return value


def identity(raw: Any) -> dict[str, Any]:
    value = exact(raw, IDENTITY_KEYS)
    if (
        not isinstance(value["repository"], str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]) is None
        or not isinstance(value["run_id"], str)
        or re.fullmatch(r"[0-9a-f]{32}", value["run_id"]) is None
    ):
        raise ContinuationError("CONTINUATION_STATE_INVALID")
    integer(value["epic"], minimum=1)
    integer(value["task"], minimum=1)
    hash_value(value["policy_sha"], sha=True)
    hash_value(value["continuation_profile_digest"])
    hash_value(value["launch_config_digest"], prefixed=True)
    return value


def profile(raw: Any) -> dict[str, Any]:
    value = exact(raw, PROFILE_KEYS)
    if value["schema_version"] != "1.0":
        raise ContinuationError("CONTINUATION_PROFILE_INVALID")
    if (
        not isinstance(value["repository"], str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]) is None
        or not isinstance(value["roadmap_ref"], str)
        or re.fullmatch(r"roadmap/[1-9][0-9]*-[A-Za-z0-9_.-]+", value["roadmap_ref"])
        is None
        or not value["roadmap_ref"].startswith(f"roadmap/{value['epic']}-")
        or not isinstance(value["owner_identity"], str)
        or not value["owner_identity"]
    ):
        raise ContinuationError("CONTINUATION_PROFILE_INVALID")
    integer(value["epic"], minimum=1)
    hash_value(value["policy_sha"], sha=True)
    hash_value(value["mandate_digest"], prefixed=True)
    hash_value(value["backend_digest"])
    limits = exact(value["limits"], set(MAXIMA))
    for key, bound in MAXIMA.items():
        integer(limits[key], bound, minimum=1)
    return value


def reservation(
    raw: Any, expected: dict[str, Any], *, max_calls: int
) -> dict[str, Any]:
    value = exact(raw, RESERVATION_KEYS)
    if (
        value["schema_version"] != "1.0"
        or identity(value["identity"]) != expected
        or (
            not isinstance(value["role"], str)
            or value["role"] not in {"executor", "controller"}
        )
        or (
            not isinstance(value["status"], str)
            or value["status"] not in {"RESERVED", "COMPLETED", "FAILED", "UNKNOWN"}
        )
    ):
        raise ContinuationError("CONTINUATION_RESERVATION_INVALID")
    integer(value["ordinal"], max_calls, minimum=1)
    for key in (
        "contract_digest",
        "invocation_digest",
        "checkpoint_digest",
        "consumed_digest",
        "snapshot_before",
    ):
        hash_value(value[key])
    for key in ("snapshot_after", "outcome_digest"):
        if value[key] is not None:
            hash_value(value[key])
    if value["status"] == "COMPLETED" and any(
        value[k] is None for k in ("snapshot_after", "outcome_digest")
    ):
        raise ContinuationError("CONTINUATION_RESERVATION_INVALID")
    if integer(value["started_at"]) >= integer(value["deadline_at"]):
        raise ContinuationError("CONTINUATION_RESERVATION_INVALID")
    return value


def handoff(
    raw: Any, expected: dict[str, Any], owner: dict[str, Any], *, max_repairs: int
) -> dict[str, Any]:
    owner = profile(owner)
    value = exact(raw, HANDOFF_KEYS)
    if any(
        expected.get(k) != owner[k] for k in ("repository", "epic", "policy_sha")
    ) or expected.get("continuation_profile_digest") != digest(owner):
        raise ContinuationError("CONTINUATION_PROFILE_CHANGED")
    if (
        len(canonical(value)) > owner["limits"]["max_record_bytes"]
        or value["schema_version"] != "1.0"
        or identity(value["identity"]) != expected
        or not isinstance(value["status"], str)
        or value["status"] not in {"PREPARED", "CONSUMED", "FAILED"}
        or value["record_digest"]
        != digest({k: v for k, v in value.items() if k != "record_digest"})
    ):
        raise ContinuationError("CONTINUATION_HANDOFF_INVALID")
    for key in ("contract_digest", "policy_digest", "record_digest"):
        hash_value(value[key])
    workspace = exact(
        value["workspace"], {"base_sha", "head_sha", "snapshot_sha", "snapshot_digest"}
    )
    for key in ("base_sha", "head_sha", "snapshot_sha"):
        hash_value(workspace[key], sha=True)
    hash_value(workspace["snapshot_digest"])
    progress = exact(
        value["progress"], {"role", "phase", "ordinal", "checkpoint_digest"}
    )
    if (
        not isinstance(progress["role"], str)
        or progress["role"] not in {"executor", "controller"}
    ) or (
        not isinstance(progress["phase"], str)
        or progress["phase"]
        not in {
            "EXECUTOR",
            "GATE",
            "CONTROLLER",
        }
    ):
        raise ContinuationError("CONTINUATION_HANDOFF_INVALID")
    integer(progress["ordinal"], max_repairs + 1, minimum=1)
    hash_value(progress["checkpoint_digest"])
    pending = value["pending"]
    if pending is not None:
        pending = exact(
            pending,
            {
                "kind",
                "operation_digest",
                "reservation_digest",
                "outcome",
                "snapshot_digest",
            },
        )
        if (
            not isinstance(pending["kind"], str)
            or pending["kind"] not in {"role", "delivery"}
        ) or (
            not isinstance(pending["outcome"], str)
            or pending["outcome"]
            not in {
                "PENDING",
                "APPLIED",
                "NOT_APPLIED",
                "UNKNOWN",
            }
        ):
            raise ContinuationError("CONTINUATION_HANDOFF_INVALID")
        for key in ("operation_digest", "reservation_digest", "snapshot_digest"):
            hash_value(pending[key])
    if (
        not isinstance(value["findings"], list)
        or len(value["findings"]) > owner["limits"]["max_findings"]
    ):
        raise ContinuationError("CONTINUATION_HANDOFF_INVALID")
    for raw_finding in value["findings"]:
        finding = exact(
            raw_finding,
            {"finding_digest", "category", "status", "source_review_digest"},
        )
        if (
            not isinstance(finding["category"], str)
            or finding["category"]
            not in {
                "security",
                "contract",
                "scope",
                "correctness",
                "quality",
                "context",
                "unknown",
            }
        ) or (
            not isinstance(finding["status"], str)
            or finding["status"] not in {"open", "fixed"}
        ):
            raise ContinuationError("CONTINUATION_HANDOFF_INVALID")
        hash_value(finding["finding_digest"])
        hash_value(finding["source_review_digest"])
    if not isinstance(value["gates"], list) or len(value["gates"]) > 128:
        raise ContinuationError("CONTINUATION_HANDOFF_INVALID")
    for raw_gate in value["gates"]:
        gate = exact(raw_gate, {"kind", "sha", "evidence_digest", "outcome"})
        if (
            not isinstance(gate["kind"], str)
            or gate["kind"]
            not in {
                "local",
                "controller",
                "pr_ci",
                "post_ci",
                "demo",
            }
        ) or (
            not isinstance(gate["outcome"], str)
            or gate["outcome"] not in {"PASS", "FAIL", "PENDING"}
        ):
            raise ContinuationError("CONTINUATION_HANDOFF_INVALID")
        hash_value(gate["sha"], sha=True)
        hash_value(gate["evidence_digest"])
    consumed = exact(
        value["consumed"],
        {
            "started_at",
            "deadline_at",
            "repair_iterations",
            "executor_calls",
            "controller_calls",
            "handoffs",
            "original_reasoning",
            "repeat_reasoning",
            "reasoning_evidence_digest",
        },
    )
    if integer(consumed["started_at"]) >= integer(consumed["deadline_at"]):
        raise ContinuationError("CONTINUATION_HANDOFF_INVALID")
    integer(consumed["repair_iterations"], max_repairs)
    for key in ("executor_calls", "controller_calls"):
        integer(consumed[key], max_repairs + 1)
    integer(consumed["handoffs"], owner["limits"]["max_handoffs"])
    if (
        not isinstance(consumed["original_reasoning"], str)
        or consumed["original_reasoning"] not in REASONING
    ) or (
        consumed["repeat_reasoning"] is not None
        and (
            not isinstance(consumed["repeat_reasoning"], str)
            or consumed["repeat_reasoning"] not in REASONING
        )
    ):
        raise ContinuationError("CONTINUATION_REASONING_UNVERIFIED")
    hash_value(consumed["reasoning_evidence_digest"])
    source = exact(
        value["source"],
        {
            "invocation_digest",
            "backend_digest",
            "checkpoint_digest",
            "reservation_digest",
        },
    )
    for item in source.values():
        hash_value(item)
    if source["backend_digest"] != owner["backend_digest"]:
        raise ContinuationError("CONTINUATION_BACKEND_CHANGED")
    return value


def preflight(prompt: str, owner: dict[str, Any], *, role: str) -> int:
    """Whole prompt UTF-8 bytes; never truncate policy or independent review."""
    limits = profile(owner)["limits"]
    size = len(prompt.encode("utf-8"))
    if size > limits["max_prompt_bytes"]:
        raise ContinuationError(
            "CONTEXT_REVIEW_TOO_LARGE"
            if role == "controller"
            else "CONTEXT_PROMPT_TOO_LARGE"
        )
    if size * 1000 >= limits["max_prompt_bytes"] * limits["threshold_permille"]:
        raise ContinuationError("CONTEXT_CAPACITY_STRATEGY_REQUIRED")
    return size


def preflight_request(
    request: dict[str, Any], owner: dict[str, Any], *, role: str
) -> int:
    """Measure exactly the fixed adapter prompt without starting a provider."""
    from tools import codex_adapter

    if role not in {"executor", "controller"}:
        raise ContinuationError("CONTINUATION_STATE_INVALID")
    safe = {key: value for key, value in request.items() if key != "contract_object"}
    return preflight(codex_adapter._prompt(role, safe), owner, role=role)
