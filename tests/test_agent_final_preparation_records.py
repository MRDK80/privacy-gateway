"""Untrusted final records cannot add operations, calls or owner authority."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from tools import agent_final_preparation_records as records
from tools.agent_epic_loop import PhaseBlocked

IDENTITY = {
    "repository": "OWNER/repo",
    "epic": 294,
    "mandate_digest": "sha256:" + "a" * 64,
    "launch_digest": "sha256:" + "b" * 64,
}


def original(kind: str) -> dict[str, Any]:
    common = {"schema_version": "1.0", **IDENTITY}
    if kind == "profile":
        common.pop("launch_digest")
        return records.seal(
            {
                **common,
                "roadmap_ref": "roadmap/294-test",
                "policy_sha": "a" * 40,
                "owner_identity": "synthetic-owner",
                "max_report_chars": 20000,
                "max_artifact_bytes": 262144,
                "max_final_model_calls": 1,
            }
        )
    if kind == "candidate":
        return records.seal(
            {
                **common,
                "roadmap_ref": "roadmap/294-test",
                "policy_sha": "a" * 40,
                "owner_identity": "synthetic-owner",
                "pr": 50,
                "main_sha": "b" * 40,
                "roadmap_sha": "c" * 40,
                "preparation_digest": "c" * 64,
                "pr_ci_digest": "d" * 64,
            },
            self_digest="candidate_digest",
        )
    if kind == "approval":
        return records.seal(
            {
                **common,
                "schema_version": "2.0",
                "owner_identity": "synthetic-owner",
                "approved_at": 100,
                "pr": 50,
                "main_sha": "b" * 40,
                "roadmap_sha": "c" * 40,
                "policy_sha": "a" * 40,
                "candidate_digest": "e" * 64,
                "pr_ci_digest": "d" * 64,
                "final_demo_digest": "f" * 64,
            },
            self_digest="approved_digest",
        )
    if kind in {"owner_pin", "owner_receipt"}:
        common.update(
            owner_identity="synthetic-owner",
            candidate_digest="e" * 64,
            approval_digest="f" * 64,
            approved_at=100,
        )
        if kind == "owner_pin":
            common["installation_receipt_digest"] = "c" * 64
        else:
            common.update(
                kind="OWNER_FINAL_APPROVAL_PIN", owner_command_digest="d" * 64
            )
        return records.seal(common)
    if kind == "request":
        return records.seal(
            {
                **common,
                "operation": "create_roadmap_pr",
                "roadmap_ref": "roadmap/294-test",
                "main_sha": "b" * 40,
                "roadmap_sha": "c" * 40,
                "policy_sha": "a" * 40,
                "preparation_digest": "e" * 64,
            },
            self_digest="operation_id",
        )
    if kind == "assessment":
        return records.seal(
            {
                "schema_version": "1.0",
                "status": "READY",
                "machine_code": "OK",
                **{
                    key: "c" * 64
                    for key in records.ASSESSMENT - {"status", "machine_code"}
                },
            }
        )
    if kind == "report":
        return records.seal(
            {
                "schema_version": "1.0",
                "repository": "OWNER/repo",
                "epic": 294,
                "candidate_digest": "e" * 64,
                "pr": 50,
                "main_sha": "b" * 40,
                "roadmap_sha": "c" * 40,
                "status": "WAITING_OWNER_FINAL_APPROVAL",
                "counts": {"total": 2, "closed": 2, "open": 0},
                "learning_status": "NONE_APPLIED",
                "comparison_status": "UNMEASURED",
            }
        )
    if kind == "state":
        return records.seal(
            {
                **common,
                "phase": "WAITING_OWNER_FINAL_APPROVAL",
                "candidate_digest": "e" * 64,
                "pending_operation_id": None,
                "calls_consumed": 1,
                "blocked_code": None,
            }
        )
    if kind == "role_state":
        return records.seal(
            {**common, "calls_consumed": 1, "pending_reservation_digest": "d" * 64}
        )
    assert kind == "reservation"
    return records.seal(
        {
            **common,
            "ordinal": 1,
            "preparation_identity_digest": "c" * 64,
            "input_digest": "d" * 64,
            "backend_digest": "e" * 64,
            "settings_digest": "f" * 64,
            "started_at": 100,
            "deadline_at": 200,
            "status": "COMPLETED",
            "output_digest": "a" * 64,
        }
    )


KINDS = (
    "profile",
    "candidate",
    "approval",
    "owner_pin",
    "owner_receipt",
    "request",
    "assessment",
    "report",
    "state",
    "role_state",
    "reservation",
)
SELF_DIGEST = {
    "candidate": "candidate_digest",
    "approval": "approved_digest",
    "request": "operation_id",
}


@pytest.mark.parametrize("kind", KINDS)
def test_exact_private_contract_roundtrip(kind: str) -> None:
    value = original(kind)
    validate: Callable[[Any], dict[str, Any]] = getattr(records, kind)
    assert validate(value) == value


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("attack", ["extra", "missing", "digest", "version"])
def test_recomputed_shape_cannot_hide_unknown_missing_or_legacy_fields(
    kind: str,
    attack: str,
) -> None:
    value = original(kind)
    field = SELF_DIGEST.get(kind, "record_digest")
    if attack == "extra":
        value["runtime_owner_approved"] = True
    elif attack == "missing":
        value.pop(next(key for key in value if key not in {"schema_version", field}))
    elif attack == "version":
        value["schema_version"] = "9.0"
    else:
        value[field] = "0" * 64
    if attack != "digest":
        value = records.seal(value, self_digest=field)
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        getattr(records, kind)(value)


@pytest.mark.parametrize("operation", ["merge_roadmap_pr", "close_epic", "update_epic"])
def test_preparation_request_can_never_be_a_main_write(operation: str) -> None:
    value = original("request")
    value["operation"] = operation
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.request(records.seal(value, self_digest="operation_id"))


@pytest.mark.parametrize("value", [0, 2, True, 1.0])
def test_final_call_budget_remains_one(value: Any) -> None:
    owner = original("profile")
    owner["max_final_model_calls"] = value
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.profile(records.seal(owner))


@pytest.mark.parametrize(
    "mutation",
    [
        {"calls_consumed": 0},
        {"calls_consumed": 2},
        {"calls_consumed": True},
    ],
)
def test_pending_reservation_cannot_claim_fresh_budget(
    mutation: dict[str, Any],
) -> None:
    value = {**original("role_state"), **mutation}
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.role_state(records.seal(value))


@pytest.mark.parametrize("field", ["settings_digest", "output_digest"])
def test_completed_review_requires_both_original_completion_references(
    field: str,
) -> None:
    value = original("reservation")
    value[field] = None
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.reservation(records.seal(value))


@pytest.mark.parametrize(
    "field,value",
    [
        ("learning_status", []),
        ("comparison_status", {}),
        ("comparison_status", "VERIFIED_COMPARABLE"),
        ("status", "ROADMAP READY FOR RELEASE"),
        ("counts", {"total": 2, "closed": 1, "open": 1}),
        ("counts", {"total": 2, "closed": True, "open": 0}),
    ],
)
def test_report_never_invents_release_or_improvement(field: str, value: Any) -> None:
    report = copy.deepcopy(original("report"))
    report[field] = value
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        records.report(records.seal(report))
