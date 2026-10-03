"""Trusted epic task delivery driver tests (#252)."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from tools import agent_epic_delivery as delivery


@pytest.mark.parametrize("operation", ["commit_task", "push_task", "create_task_pr"])
def test_versioned_bootstrap_request_does_not_require_fictitious_pr(
    operation: str,
) -> None:
    request = replace(_request(operation), pr=None, schema_version="2.0")
    assert delivery._valid_request(request)
    assert not delivery._valid_request(replace(request, schema_version="1.0"))
    assert not delivery._valid_request(replace(request, pr=0))
    assert not delivery._valid_request(replace(request, pr=True))


@pytest.mark.parametrize("operation", ["merge_task_pr", "close_task", "update_epic"])
def test_delivery_still_requires_real_pr(operation: str) -> None:
    assert not delivery._valid_request(
        replace(_request(operation), pr=None, schema_version="2.0")
    )


def test_bootstrap_delivery_keeps_mandate_and_idempotency_boundary(
    tmp_path: Path,
) -> None:
    request = replace(_request("create_task_pr"), pr=None, schema_version="2.0")
    calls: list[str] = []

    def effect(value: delivery.Request) -> dict[str, object]:
        calls.append(value.operation)
        return {"pr": 278}

    assert _run(tmp_path, request=request, effect=effect).status == "APPLIED"
    assert _run(tmp_path, request=request, effect=effect).status == "NO_OP"
    assert calls == ["create_task_pr"]
    entries = delivery.Ledger(tmp_path / "private", tmp_path / "repository").load()
    stored = entries[request.operation_id]["request"]
    assert isinstance(stored, dict) and stored["schema_version"] == "2.0"
    denied = _run(
        tmp_path,
        request=request,
        effect=lambda _: pytest.fail("expired mandate"),
        mandate_context=delivery.MandateContext(1001, "OWNER", 900, 0, 0),
    )
    assert denied.status == "BLOCKED"


def test_legacy_ledger_request_fields_do_not_change() -> None:
    from dataclasses import asdict

    request = _request()
    expected = asdict(request)
    del expected["schema_version"]
    del expected["policy_sha"]
    assert delivery._request_fields(request) == expected


def test_policy_version_cannot_silently_upgrade_legacy_authority() -> None:
    mandate = _mandate()
    request = replace(_request(), schema_version="3.0", policy_sha="a" * 40)
    assert delivery._valid_request(request)
    assert (
        delivery._authorized(
            request,
            mandate,
            delivery.mandate_digest(mandate),
            delivery.MandateContext(200, "OWNER", 150, 0, 0),
        )
        == "MANDATE_IDENTITY_MISMATCH"
    )


def test_new_policy_authority_is_not_a_legacy_base_equality_bypass() -> None:
    mandate = _mandate()
    mandate["schema_version"] = "3.0"
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    digest = delivery.mandate_digest(mandate)
    approval["mandate_digest"] = digest
    context = delivery.MandateContext(200, "OWNER", 150, 0, 0)
    request = replace(
        _request(), schema_version="3.0", policy_sha="a" * 40, base_sha="c" * 40
    )
    assert delivery._authorized(request, mandate, digest, context) is None
    assert (
        delivery._authorized(
            replace(request, policy_sha="c" * 40), mandate, digest, context
        )
        == "MANDATE_IDENTITY_MISMATCH"
    )
    assert (
        delivery._authorized(
            replace(request, schema_version="1.0", policy_sha=None),
            mandate,
            digest,
            context,
        )
        == "MANDATE_IDENTITY_MISMATCH"
    )


def test_legacy_delivery_cannot_add_pinned_policy_field() -> None:
    assert not delivery._valid_request(replace(_request(), policy_sha="a" * 40))


def _mandate() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "2.0",
        "repository": "OWNER/repository",
        "epic": 248,
        "roadmap_ref": "roadmap/248-autonomous-epic-runner",
        "policy_sha": "a" * 40,
        "issued_at": 100,
        "expires_at": 1000,
        "owner_identity": "OWNER",
        "limits": {
            "max_duration_seconds": 600,
            "max_task_iterations": 10,
            "max_follow_up_issues": 3,
        },
        "revoked": False,
        "operations": [
            "commit_task",
            "push_task",
            "create_task_pr",
            "merge_task_pr",
            "close_task",
            "update_epic",
        ],
        "task_grants": {
            "252": {
                "causal_scope": ["trusted task delivery"],
                "allowed_paths": ["tools/agent_epic_delivery.py"],
            }
        },
        "approval": {
            "mandate_digest": "pending",
            "approved_by": "OWNER",
            "approved_at": 100,
        },
    }
    approval = value["approval"]
    assert isinstance(approval, dict)
    approval["mandate_digest"] = delivery.mandate_digest(value)
    return value


def _request(operation: str = "merge_task_pr") -> delivery.Request:
    return delivery.Request(
        operation_id=f"OWNER/repository:248:252:262:{operation}:" + "b" * 40,
        operation=operation,
        repository="OWNER/repository",
        epic=248,
        task=252,
        pr=262,
        base_ref="roadmap/248-autonomous-epic-runner",
        base_sha="a" * 40,
        head_ref="feat/252-epic-task-delivery",
        head_sha="b" * 40,
        merge_sha="c" * 40 if operation in {"close_task", "update_epic"} else None,
    )


def _assessment(phase: str = "pr", *, state: str = "SUCCESS") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": "pass" if state == "SUCCESS" else "blocked",
        "machine_code": "OK" if state == "SUCCESS" else "CI_FAILED",
        "phase": phase,
        "task_status": "TASK READY FOR REVIEW" if phase == "pr" else "TASK DONE",
        "repository": "OWNER/repository",
        "epic": 248,
        "task": 252,
        "pr": 262,
        "base_ref": "roadmap/248-autonomous-epic-runner",
        "base_sha": "a" * 40,
        "head_ref": "feat/252-epic-task-delivery",
        "head_sha": "b" * 40,
        "merge_sha": "c" * 40 if phase == "post-merge" else None,
        "github_checks": [
            {"name": name, "state": state, "link": "https://example.invalid/check"}
            for name in sorted(delivery.REQUIRED_CHECKS)
        ],
    }


def _run(
    tmp_path: Path,
    *,
    request: delivery.Request | None = None,
    assessment: dict[str, object] | None = None,
    effect: delivery.Effect | None = None,
    reconcile: delivery.Reconcile | None = None,
    mandate: dict[str, object] | None = None,
    mandate_context: delivery.MandateContext | None = None,
) -> delivery.Result:
    approved = mandate or _mandate()
    return delivery.deliver(
        request or _request(),
        mandate=approved,
        approved_mandate_digest=delivery.mandate_digest(approved),
        assessment=assessment or _assessment(),
        ledger=delivery.Ledger(tmp_path / "private", tmp_path / "repository"),
        revalidate=lambda _request: True,
        effect=effect or (lambda _request: {"receipt": "merged"}),
        mandate_context=mandate_context
        or delivery.MandateContext(200, "OWNER", 150, 0, 0),
        reconcile=reconcile,
    )


def test_merge_records_intent_and_verified_receipt_once(tmp_path: Path) -> None:
    (tmp_path / "repository").mkdir()
    calls: list[str] = []

    def effect(request: delivery.Request) -> dict[str, object]:
        calls.append(request.operation)
        return {"receipt": "merged", "merge_sha": "c" * 40}

    first = _run(tmp_path, effect=effect)
    second = _run(tmp_path, effect=effect)

    assert first == delivery.Result(
        "APPLIED", "OK", {"merge_sha": "c" * 40, "receipt": "merged"}
    )
    assert second == delivery.Result("NO_OP", "ALREADY_APPLIED", first.receipt)
    assert calls == ["merge_task_pr"]


@pytest.mark.parametrize("state", ["PENDING", "NEUTRAL", "SKIPPED", "FAILURE"])
def test_non_success_or_missing_exact_checks_block_merge(
    tmp_path: Path, state: str
) -> None:
    (tmp_path / "repository").mkdir()
    result = _run(tmp_path, assessment=_assessment(state=state))
    assert result == delivery.Result("BLOCKED", "GATE_INVALID", None)

    missing = _assessment()
    checks = missing["github_checks"]
    assert isinstance(checks, list)
    checks.pop()
    assert _run(tmp_path, assessment=missing).machine_code == "GATE_INVALID"


def test_stale_identity_blocks_before_side_effect(tmp_path: Path) -> None:
    (tmp_path / "repository").mkdir()
    stale = _assessment()
    stale["head_sha"] = "d" * 40
    result = _run(
        tmp_path,
        assessment=stale,
        effect=lambda _request: pytest.fail("stale side effect must not run"),
    )
    assert result.machine_code == "STALE_IDENTITY"


def test_permission_failure_is_blocked_not_broad_token_fallback(tmp_path: Path) -> None:
    (tmp_path / "repository").mkdir()

    def denied(_request: delivery.Request) -> dict[str, object]:
        raise delivery.PermissionDenied

    result = _run(tmp_path, effect=denied)
    assert result == delivery.Result("BLOCKED", "GITHUB_PERMISSION_DENIED", None)


def test_unknown_merge_outcome_is_not_repeated_without_reconciliation(
    tmp_path: Path,
) -> None:
    (tmp_path / "repository").mkdir()
    calls = 0

    def unknown(_request: delivery.Request) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise delivery.OutcomeUnknown

    first = _run(tmp_path, effect=unknown)
    second = _run(tmp_path, effect=unknown)

    assert first.machine_code == "ESCALATE_UNKNOWN_OUTCOME"
    assert second.machine_code == "ESCALATE_UNKNOWN_OUTCOME"
    assert calls == 1


def test_reconciliation_can_prove_applied_or_not_applied(tmp_path: Path) -> None:
    (tmp_path / "repository").mkdir()

    assert (
        _run(
            tmp_path,
            effect=lambda _request: (_ for _ in ()).throw(delivery.OutcomeUnknown()),
        ).machine_code
        == "ESCALATE_UNKNOWN_OUTCOME"
    )
    applied = _run(
        tmp_path,
        reconcile=lambda _request: ("APPLIED", {"receipt": "found"}),
    )
    assert applied == delivery.Result("NO_OP", "ALREADY_APPLIED", {"receipt": "found"})

    other = _request("push_task")
    assert (
        _run(
            tmp_path,
            request=other,
            assessment={},
            effect=lambda _request: (_ for _ in ()).throw(delivery.OutcomeUnknown()),
        ).machine_code
        == "ESCALATE_UNKNOWN_OUTCOME"
    )
    retried = _run(
        tmp_path,
        request=other,
        assessment={},
        reconcile=lambda _request: ("NOT_APPLIED", None),
        effect=lambda _request: {"receipt": "pushed"},
    )
    assert retried.status == "APPLIED"


def test_post_merge_gate_required_before_close_and_epic_update(tmp_path: Path) -> None:
    (tmp_path / "repository").mkdir()
    close = _request("close_task")
    update = _request("update_epic")
    assert _run(tmp_path, request=close).machine_code == "GATE_INVALID"
    assert _run(tmp_path, request=update).machine_code == "GATE_INVALID"
    assert (
        _run(
            tmp_path, request=update, assessment=_assessment("post-merge")
        ).machine_code
        == "TASK_NOT_CLOSED"
    )
    assert (
        _run(tmp_path, request=close, assessment=_assessment("post-merge")).status
        == "APPLIED"
    )
    assert (
        _run(tmp_path, request=update, assessment=_assessment("post-merge")).status
        == "APPLIED"
    )


def test_issue_text_cannot_expand_mandate(tmp_path: Path) -> None:
    (tmp_path / "repository").mkdir()
    mandate = copy.deepcopy(_mandate())
    operations = mandate["operations"]
    assert isinstance(operations, list)
    operations.remove("merge_task_pr")
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approval["mandate_digest"] = delivery.mandate_digest(mandate)
    result = _run(tmp_path, mandate=mandate)
    assert result.machine_code == "MANDATE_AUTHORITY_MISSING"


@pytest.mark.parametrize(
    ("change", "context", "code"),
    [
        ({"schema_version": "1.0"}, {}, "MANDATE_SCHEMA_UNSUPPORTED"),
        ({"revoked": 0}, {}, "MANDATE_INVALID"),
        ({"revoked": 1}, {}, "MANDATE_INVALID"),
        ({"revoked": True}, {}, "MANDATE_REVOKED"),
        ({}, {"now": 1000}, "MANDATE_EXPIRED"),
        ({}, {"owner_identity": "OTHER"}, "MANDATE_OWNER_MISMATCH"),
        ({}, {"now": 800}, "MANDATE_DURATION_LIMIT"),
        ({}, {"task_iterations": 10}, "MANDATE_TASK_LIMIT"),
        ({}, {"follow_up_issues": 4}, "MANDATE_FOLLOW_UP_LIMIT"),
    ],
)
def test_invalid_lifecycle_blocks_before_side_effect(
    tmp_path: Path,
    change: dict[str, object],
    context: dict[str, object],
    code: str,
) -> None:
    (tmp_path / "repository").mkdir()
    mandate = _mandate()
    mandate.update(change)
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approval["mandate_digest"] = delivery.mandate_digest(mandate)
    values: dict[str, object] = {
        "now": 200,
        "owner_identity": "OWNER",
        "started_at": 150,
        "task_iterations": 0,
        "follow_up_issues": 0,
    }
    values.update(context)

    result = _run(
        tmp_path,
        mandate=mandate,
        mandate_context=delivery.MandateContext(
            now=cast(int, values["now"]),
            owner_identity=cast(str, values["owner_identity"]),
            started_at=cast(int, values["started_at"]),
            task_iterations=cast(int, values["task_iterations"]),
            follow_up_issues=cast(int, values["follow_up_issues"]),
        ),
        effect=lambda _request: pytest.fail("invalid mandate must not write"),
    )

    assert result == delivery.Result("BLOCKED", code, None)


def test_approval_identity_is_digest_bound_and_checked(tmp_path: Path) -> None:
    (tmp_path / "repository").mkdir()
    mandate = _mandate()
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approved_digest = delivery.mandate_digest(mandate)
    approval["approved_by"] = "OTHER"

    stale = delivery.deliver(
        _request(),
        mandate=mandate,
        approved_mandate_digest=approved_digest,
        assessment=_assessment(),
        ledger=delivery.Ledger(tmp_path / "private", tmp_path / "repository"),
        revalidate=lambda _request: True,
        effect=lambda _request: pytest.fail("changed approval must not write"),
        mandate_context=delivery.MandateContext(200, "OWNER", 150, 0, 0),
    )
    assert stale.machine_code == "MANDATE_APPROVAL_IDENTITY_MISMATCH"
