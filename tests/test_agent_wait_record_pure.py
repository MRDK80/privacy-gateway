"""Pure wait shape validation preserves original objects and failure order."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from tools.agent_epic_wait import WaitError, validate_wait_record


def originals() -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    identity = {
        "repository": "synthetic/repo",
        "epic": 294,
        "policy_sha": "1" * 40,
        "wait_digest": "sha256:" + "2" * 64,
        "stage": "FINAL:FINAL_GATE",
    }
    limits = {
        "max_phase_seconds": 60,
        "max_read_commands": 2,
        "max_read_attempts": 2,
        "max_ci_polls": 2,
    }
    raw = {
        "schema_version": "1.0",
        "identity": identity,
        "started_at": 100,
        "last_seen_at": 100.0,
        "deadline_at": 160,
        "requests": {},
        "ci": None,
    }
    return raw, identity, limits


def test_exact_original_object_order_and_numeric_representation() -> None:
    raw, identity, limits = originals()
    before = copy.deepcopy(raw)
    order = tuple(raw)
    assert validate_wait_record(raw, identity, limits) is raw
    assert raw == before and tuple(raw) == order
    assert type(raw["started_at"]) is int
    assert type(raw["last_seen_at"]) is float


@pytest.mark.parametrize(
    "key,value",
    [
        ("started_at", True),
        ("started_at", float("nan")),
        ("last_seen_at", 99),
        ("deadline_at", 161),
        ("schema_version", "2.0"),
        ("requests", {"2": {}}),
    ],
)
def test_original_closed_time_schema_and_sequence(key: str, value: Any) -> None:
    raw, identity, limits = originals()
    raw[key] = value
    with pytest.raises(WaitError, match="WAIT_STATE_INVALID"):
        validate_wait_record(raw, identity, limits)


@pytest.mark.parametrize(
    "status,next_at,category,accepted",
    [
        ("READY", None, None, True),
        ("RESERVED", None, None, True),
        ("FAILED", None, None, True),
        ("WAITING", 101, "timeout", True),
        ("WAITING", 101, None, False),
        ("READY", 101, None, False),
        ("WAITING", True, "network", False),
    ],
)
def test_original_request_status_rules(
    status: str,
    next_at: Any,
    category: Any,
    accepted: bool,
) -> None:
    raw, identity, limits = originals()
    raw["requests"] = {
        "1": {
            "command_digest": "sha256:" + "3" * 64,
            "attempts": 1,
            "status": status,
            "category": category,
            "next_check_at": next_at,
        }
    }
    if accepted:
        assert validate_wait_record(raw, identity, limits) is raw
    else:
        with pytest.raises(WaitError, match="WAIT_STATE_INVALID"):
            validate_wait_record(raw, identity, limits)


def test_original_malformed_nested_type_is_not_normalized() -> None:
    raw, identity, limits = originals()
    raw["ci"] = {
        "identity_digest": "sha256:" + "3" * 64,
        "attempts": 1,
        "status": [],
        "next_check_at": None,
    }
    with pytest.raises(TypeError):
        validate_wait_record(raw, identity, limits)


def test_record_shape_precedes_malformed_nested_item() -> None:
    raw, identity, limits = originals()
    raw["extra"] = "synthetic"
    raw["ci"] = {"status": []}
    with pytest.raises(WaitError, match="WAIT_STATE_INVALID"):
        validate_wait_record(raw, identity, limits)
