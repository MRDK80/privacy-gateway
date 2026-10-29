"""Untrusted direct input shape projections, not a human source receipt."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from tools import agent_final_preparation_records as records
from tools import agent_owner_final_sources as shapes
from tools.agent_owner_final_input import OwnerInputError, parse_owner_input


def explicit_input() -> dict[str, Any]:
    identity = {
        "repository": "synthetic/repo",
        "epic": 294,
        "owner_identity": "synthetic-owner",
        "mandate_digest": "sha256:" + "a" * 64,
        "launch_digest": "sha256:" + "b" * 64,
        "candidate_digest": "c" * 64,
        "pr": 9,
        "main_sha": "d" * 40,
        "roadmap_sha": "e" * 40,
        "policy_sha": "f" * 40,
        "pr_ci_digest": "1" * 64,
    }
    demo = records.seal(
        {
            "schema_version": "1.0",
            **identity,
            "kind": "OWNER_REAL_FINAL_DEMO",
            "procedure": "ADR303_OWNER_REAL_FINAL_DEMO_V1",
            "started_at": 100,
            "completed_at": 109,
            "scenarios": [
                {
                    "id": "synthetic-cli",
                    "surface": "CLI",
                    "steps": ["Use synthetic input."],
                    "expected": "Synthetic result.",
                }
            ],
            "observations": [
                {
                    "id": "synthetic-cli",
                    "observed_at": 105,
                    "outcome": "OWNER_OBSERVED_EXPECTED",
                    "observation": "Fixture prose, not a real demo.",
                }
            ],
        }
    )
    approval = records.seal(
        {
            "schema_version": "2.0",
            **identity,
            "final_demo_digest": demo["record_digest"],
            "approved_at": 110,
        },
        self_digest="approved_digest",
    )
    return {"approval": approval, "demo": demo}


def parse(value: dict[str, Any], **options: Any) -> Any:
    return parse_owner_input(
        json.dumps(value).encode(),
        value["approval"]["approved_digest"],
        issued_at=90,
        expires_at=120,
        max_bytes=262144,
        **options,
    )


def test_explicit_graph_projects_five_closed_unverified_shapes() -> None:
    original = explicit_input()
    result = parse(original)
    assert result.approval == original["approval"]
    assert result.demo == original["demo"]
    assert shapes.command(result.command) == result.command
    assert records.owner_receipt(result.receipt) == result.receipt
    assert records.owner_pin(result.pin) == result.pin
    assert result.command["approval_digest"] == result.approval["approved_digest"]
    assert result.command["final_demo_digest"] == result.demo["record_digest"]
    assert result.receipt["owner_command_digest"] == result.command["record_digest"]
    assert result.pin["installation_receipt_digest"] == result.receipt["record_digest"]
    assert result.command["approved_at"] == result.approval["approved_at"]
    assert result.pin["approved_at"] == result.receipt["approved_at"] == 110
    assert "Fixture prose" not in repr(result)
    assert "synthetic-owner" not in repr(result)
    assert not hasattr(result, "ready") and not hasattr(result, "verified")
    assert not hasattr(result, "source_authenticated")
    with pytest.raises(FrozenInstanceError):
        setattr(result, "approval", {})


@pytest.mark.parametrize(
    "key",
    [
        "repository",
        "epic",
        "owner_identity",
        "mandate_digest",
        "launch_digest",
        "candidate_digest",
        "pr",
        "main_sha",
        "roadmap_sha",
        "policy_sha",
        "pr_ci_digest",
    ],
)
def test_resealed_cross_input_identity_mismatch_is_not_accepted(key: str) -> None:
    value = explicit_input()
    previous = value["demo"][key]
    value["demo"][key] = (
        previous + 1
        if type(previous) is int
        else previous[:-1] + ("8" if previous[-1] != "8" else "9")
    )
    value["demo"] = records.seal(value["demo"])
    value["approval"]["final_demo_digest"] = value["demo"]["record_digest"]
    value["approval"] = records.seal(value["approval"], self_digest="approved_digest")
    with pytest.raises(OwnerInputError, match="^FINAL_APPROVAL_INVALID$"):
        parse(value)


@pytest.mark.parametrize(
    "fault",
    [
        "extra-top",
        "missing-top",
        "path",
        "wrong-version",
        "wrong-demo-digest",
        "preseed-command",
        "bool-pr",
        "bool-time",
        "float-time",
        "missing-observation",
        "model-outcome",
        "future-observation",
        "oversize-prose",
    ],
)
def test_closed_input_and_existing_demo_contracts(fault: str) -> None:
    value = explicit_input()
    if fault == "extra-top":
        value["approved"] = True
    elif fault == "missing-top":
        del value["demo"]
    elif fault == "path":
        value["demo_path"] = "/arbitrary/artifact"
    elif fault == "wrong-version":
        value["approval"]["schema_version"] = "1.0"
    elif fault == "wrong-demo-digest":
        value["approval"]["final_demo_digest"] = "9" * 64
    elif fault == "preseed-command":
        value["demo"]["kind"] = "OWNER_ORIGINAL_FINAL_COUNTER_PROVISIONING"
    elif fault == "bool-pr":
        value["demo"]["pr"] = True
    elif fault == "bool-time":
        value["demo"]["started_at"] = True
    elif fault == "float-time":
        value["demo"]["completed_at"] = 109.0
    elif fault == "missing-observation":
        value["demo"]["observations"] = []
    elif fault == "model-outcome":
        value["demo"]["observations"][0]["outcome"] = "MODEL_VERIFIED"
    elif fault == "future-observation":
        value["demo"]["observations"][0]["observed_at"] = 111
    elif fault == "oversize-prose":
        value["demo"]["observations"][0]["observation"] = "x" * 2049
    if "demo" in value:
        value["demo"] = records.seal(value["demo"])
    value["approval"] = records.seal(value["approval"], self_digest="approved_digest")
    with pytest.raises(OwnerInputError):
        parse(value)


@pytest.mark.parametrize("field,number", [("started_at", 89), ("completed_at", 111)])
def test_demo_must_fit_explicit_mandate_and_approval_window(
    field: str, number: int
) -> None:
    value = explicit_input()
    value["demo"][field] = number
    value["demo"] = records.seal(value["demo"])
    value["approval"]["final_demo_digest"] = value["demo"]["record_digest"]
    value["approval"] = records.seal(value["approval"], self_digest="approved_digest")
    with pytest.raises(OwnerInputError):
        parse(value)


def test_separate_approved_digest_is_mandatory_and_exact() -> None:
    value = explicit_input()
    with pytest.raises(OwnerInputError):
        parse_owner_input(
            json.dumps(value).encode(),
            "9" * 64,
            issued_at=90,
            expires_at=120,
            max_bytes=262144,
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"approval":{},"approval":{},"demo":{}}',
        b'{"approval":{},"demo":{"x":1,"x":2}}',
        b"\xff",
        b"[]",
        b'{"approval":NaN,"demo":{}}',
    ],
)
def test_duplicate_utf8_and_json_failures_are_closed(raw: bytes) -> None:
    with pytest.raises(OwnerInputError, match="^FINAL_APPROVAL_INVALID$") as found:
        parse_owner_input(raw, "1" * 64, issued_at=90, expires_at=120, max_bytes=262144)
    assert found.value.__cause__ is None and found.value.__context__ is None


def test_caps_before_decode_and_no_prose_in_error() -> None:
    marker = "SYNTHETIC-OWNER-SECRET-DO-NOT-RETAIN"
    value = explicit_input()
    value["demo"]["observations"][0]["observation"] = marker
    with pytest.raises(OwnerInputError) as found:
        parse_owner_input(
            json.dumps(value).encode(),
            "9" * 64,
            issued_at=90,
            expires_at=120,
            max_bytes=10,
        )
    assert marker not in str(found.value) and marker not in repr(found.value)
    assert found.value.__cause__ is None and found.value.__context__ is None


@pytest.mark.parametrize(
    "window",
    [
        {"issued_at": True, "expires_at": 120, "max_bytes": 262144},
        {"issued_at": 90, "expires_at": 120.0, "max_bytes": 262144},
        {"issued_at": 90, "expires_at": 120, "max_bytes": True},
        {"issued_at": 90, "expires_at": 110, "max_bytes": 262145},
        {"issued_at": 90, "expires_at": 109, "max_bytes": 262144},
        {"issued_at": 120, "expires_at": 90, "max_bytes": 262144},
    ],
)
def test_explicit_window_and_cap_are_strict_comparisons(window: dict[str, Any]) -> None:
    value = explicit_input()
    with pytest.raises(OwnerInputError):
        parse_owner_input(
            json.dumps(value).encode(), value["approval"]["approved_digest"], **window
        )


def test_owner_times_are_copied_not_generated_and_expiry_is_inclusive() -> None:
    value = explicit_input()
    result = parse_owner_input(
        json.dumps(value).encode(),
        value["approval"]["approved_digest"],
        issued_at=100,
        expires_at=110,
        max_bytes=262144,
    )
    assert result.demo["started_at"] == 100
    assert result.demo["completed_at"] == 109
    assert result.approval["approved_at"] == result.command["approved_at"] == 110
    assert result.receipt["approved_at"] == result.pin["approved_at"] == 110
