"""Synthetic closed recovery records and whole-prompt boundary tests (#301)."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from tools import agent_continuation as continuation


def owner() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "repository": "OWNER/repo",
        "epic": 294,
        "roadmap_ref": "roadmap/294-autonomy",
        "policy_sha": "a" * 40,
        "mandate_digest": "sha256:" + "b" * 64,
        "owner_identity": "owner",
        "backend_digest": "c" * 64,
        "limits": {
            "threshold_permille": 800,
            "max_handoffs": 2,
            "max_prompt_bytes": 1000,
            "max_record_bytes": 65536,
            "max_findings": 4,
        },
    }


def identity() -> dict[str, Any]:
    profile = owner()
    return {
        "repository": "OWNER/repo",
        "epic": 294,
        "task": 301,
        "run_id": "d" * 32,
        "policy_sha": "a" * 40,
        "continuation_profile_digest": continuation.digest(profile),
        "launch_config_digest": "sha256:" + "e" * 64,
    }


def record() -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "1.0",
        "identity": identity(),
        "contract_digest": "f" * 64,
        "policy_digest": "1" * 64,
        "workspace": {
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
            "snapshot_sha": "c" * 40,
            "snapshot_digest": "2" * 64,
        },
        "progress": {
            "role": "controller",
            "phase": "CONTROLLER",
            "ordinal": 1,
            "checkpoint_digest": "3" * 64,
        },
        "pending": None,
        "findings": [],
        "gates": [],
        "consumed": {
            "started_at": 1000,
            "deadline_at": 1100,
            "repair_iterations": 0,
            "executor_calls": 1,
            "controller_calls": 0,
            "handoffs": 1,
            "original_reasoning": "low",
            "repeat_reasoning": None,
            "reasoning_evidence_digest": "4" * 64,
        },
        "source": {
            "invocation_digest": "5" * 64,
            "backend_digest": "c" * 64,
            "checkpoint_digest": "3" * 64,
            "reservation_digest": "6" * 64,
        },
        "status": "PREPARED",
    }
    value["record_digest"] = continuation.digest(value)
    return value


def seal(value: dict[str, Any]) -> dict[str, Any]:
    value["record_digest"] = continuation.digest(
        {k: v for k, v in value.items() if k != "record_digest"}
    )
    return value


def test_closed_valid_record() -> None:
    value = record()
    assert continuation.handoff(value, identity(), owner(), max_repairs=2) == value


@pytest.mark.parametrize(
    "field", ["prompt", "chain_of_thought", "owner_approval", "raw_logs"]
)
def test_extra_fields_cannot_supply_authority(field: str) -> None:
    value = record()
    value[field] = "untrusted"
    with pytest.raises(continuation.ContinuationError):
        continuation.handoff(seal(value), identity(), owner(), max_repairs=2)


@pytest.mark.parametrize("count", [True, -1, 4, "0"])
def test_consumed_calls_are_not_refunded_or_unbounded(count: object) -> None:
    value = record()
    value["consumed"]["executor_calls"] = count
    with pytest.raises(continuation.ContinuationError):
        continuation.handoff(seal(value), identity(), owner(), max_repairs=2)


def test_record_digest_and_source_identity_are_not_summary_authority() -> None:
    value = record()
    expected = copy.deepcopy(identity())
    expected["policy_sha"] = "9" * 40
    with pytest.raises(continuation.ContinuationError):
        continuation.handoff(value, expected, owner(), max_repairs=2)
    value["consumed"]["executor_calls"] = 0
    with pytest.raises(continuation.ContinuationError):
        continuation.handoff(value, identity(), owner(), max_repairs=2)


def test_complete_utf8_prompt_has_proactive_stop_without_trim() -> None:
    approved = owner()
    assert continuation.preflight("x" * 799, approved, role="controller") == 799
    with pytest.raises(
        continuation.ContinuationError, match="CONTEXT_CAPACITY_STRATEGY_REQUIRED"
    ):
        continuation.preflight("x" * 800, approved, role="controller")
    with pytest.raises(
        continuation.ContinuationError, match="CONTEXT_REVIEW_TOO_LARGE"
    ):
        continuation.preflight("я" * 501, approved, role="controller")


@pytest.mark.parametrize("status", [{}, [], True, None])
def test_malformed_handoff_status_has_closed_error(status: object) -> None:
    value = record()
    value["status"] = status
    with pytest.raises(
        continuation.ContinuationError, match="CONTINUATION_HANDOFF_INVALID"
    ):
        continuation.handoff(seal(value), identity(), owner(), max_repairs=2)


def test_request_preflight_counts_full_actual_adapter_prompt() -> None:
    from tools import codex_adapter

    request = {
        "contract_object": object(),
        "trusted_policy": {"AGENTS.md": "synthetic policy " * 100},
        "diff": "synthetic diff " * 100,
        "session_id": "synthetic-session",
    }
    approved = owner()
    approved["limits"]["max_prompt_bytes"] = 262144
    safe = {key: value for key, value in request.items() if key != "contract_object"}
    actual = codex_adapter._prompt("controller", safe)
    assert continuation.preflight_request(
        request, approved, role="controller"
    ) == len(actual.encode("utf-8"))
    approved["limits"]["max_prompt_bytes"] = 1000
    with pytest.raises(
        continuation.ContinuationError, match="CONTEXT_REVIEW_TOO_LARGE"
    ):
        continuation.preflight_request(request, approved, role="controller")
    assert request["trusted_policy"] == safe["trusted_policy"]
    assert request["diff"] == safe["diff"]


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"schema_version": "4.0"}, False),
        ({"schema_version": "5.0"}, True),
        ({"schema_version": "6.0", "approved_quota_digest": None}, False),
        (
            {"schema_version": "6.0", "approved_quota_digest": "sha256:" + "a" * 64},
            True,
        ),
    ],
)
def test_quota_opt_in_composes_only_explicit_config6_reference(
    config: dict[str, Any], expected: bool
) -> None:
    from tools import agent_epic_runtime_cli as guards

    assert guards.quota_enabled(config) is expected
