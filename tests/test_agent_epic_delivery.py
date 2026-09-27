"""Trusted epic task delivery driver tests (#252)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from tools import agent_epic_delivery as delivery


def _mandate() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "1.0",
        "repository": "OWNER/repository",
        "epic": 248,
        "roadmap_ref": "roadmap/248-autonomous-epic-runner",
        "policy_sha": "a" * 40,
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
        "approval": {"mandate_digest": "pending"},
    }
    value["approval"] = {"mandate_digest": delivery.mandate_digest(value)}
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

    assert _run(
        tmp_path,
        effect=lambda _request: (_ for _ in ()).throw(delivery.OutcomeUnknown()),
    ).machine_code == "ESCALATE_UNKNOWN_OUTCOME"
    applied = _run(
        tmp_path,
        reconcile=lambda _request: ("APPLIED", {"receipt": "found"}),
    )
    assert applied == delivery.Result("NO_OP", "ALREADY_APPLIED", {"receipt": "found"})

    other = _request("push_task")
    assert _run(
        tmp_path,
        request=other,
        assessment={},
        effect=lambda _request: (_ for _ in ()).throw(delivery.OutcomeUnknown()),
    ).machine_code == "ESCALATE_UNKNOWN_OUTCOME"
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
    assert _run(
        tmp_path, request=update, assessment=_assessment("post-merge")
    ).machine_code == "TASK_NOT_CLOSED"
    assert _run(
        tmp_path, request=close, assessment=_assessment("post-merge")
    ).status == "APPLIED"
    assert _run(
        tmp_path, request=update, assessment=_assessment("post-merge")
    ).status == "APPLIED"


def test_issue_text_cannot_expand_mandate(tmp_path: Path) -> None:
    (tmp_path / "repository").mkdir()
    mandate = copy.deepcopy(_mandate())
    operations = mandate["operations"]
    assert isinstance(operations, list)
    operations.remove("merge_task_pr")
    mandate["approval"] = {"mandate_digest": delivery.mandate_digest(mandate)}
    result = _run(tmp_path, mandate=mandate)
    assert result.machine_code == "MANDATE_AUTHORITY_MISSING"
