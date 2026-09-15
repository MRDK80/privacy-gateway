"""Trust boundary validation of ``review_basis`` in the orchestrator (#194)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tools.agent_orchestrate import (
    OrchestrationError,
    TaskContract,
    _canonical_trust_source_kinds,
    _validate_report,
    _validate_verdict,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "docs" / "schemas" / "controller-verdict.schema.json"
BASE_SHA = "b" * 40
HEAD_SHA = "h" * 40
ISSUE = 118


def _contract() -> TaskContract:
    return cast(
        TaskContract,
        SimpleNamespace(issue=ISSUE, base_sha=BASE_SHA, max_report_chars=200_000),
    )


def _basis(**overrides: Any) -> dict[str, Any]:
    basis: dict[str, Any] = {
        "trust_source_kind": "local_read_only_bundle",
        "head_policy_applied": False,
        "executor_self_assessment_treated_as_evidence_only": True,
    }
    basis.update(overrides)
    return basis


def _verdict(basis: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "role": "controller",
        "task_issue": ISSUE,
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "review_basis": basis,
        "verdict": "PASS_WITH_NOTES",
        "repair_iteration": 0,
        "escalation_reason": None,
        "blocking_findings": [],
        "notes": [],
    }


def _report(**overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "role": "executor",
        "task_issue": ISSUE,
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "status": "blocked",
        "acceptance_criteria": [],
        "changed_files": [],
        "checks": [],
        "residual_risks": [],
        "stop_reason": "scope_complete",
    }
    report.update(overrides)
    return report


def test_canonical_enum_comes_from_the_canonical_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    expected = schema["properties"]["review_basis"]["properties"]["trust_source_kind"]
    assert _canonical_trust_source_kinds() == frozenset(expected["enum"])
    assert "local_read_only_bundle" in _canonical_trust_source_kinds()
    assert "base_sha" in _canonical_trust_source_kinds()


def test_local_read_only_bundle_is_accepted() -> None:
    verdict = _validate_verdict(_verdict(_basis()), _contract(), HEAD_SHA)
    assert verdict == "PASS_WITH_NOTES"


def test_base_sha_is_still_accepted() -> None:
    payload = _verdict(_basis(trust_source_kind="base_sha"))
    assert _validate_verdict(payload, _contract(), HEAD_SHA) == "PASS_WITH_NOTES"


def test_head_policy_applied_is_rejected() -> None:
    payload = _verdict(_basis(head_policy_applied=True))
    with pytest.raises(OrchestrationError, match="MALFORMED_OUTPUT"):
        _validate_verdict(payload, _contract(), HEAD_SHA)


def test_executor_self_assessment_as_evidence_is_rejected() -> None:
    payload = _verdict(
        _basis(executor_self_assessment_treated_as_evidence_only=False)
    )
    with pytest.raises(OrchestrationError, match="MALFORMED_OUTPUT"):
        _validate_verdict(payload, _contract(), HEAD_SHA)


def test_unknown_trust_source_kind_fails_closed() -> None:
    payload = _verdict(_basis(trust_source_kind="task_head_worktree"))
    with pytest.raises(OrchestrationError, match="MALFORMED_OUTPUT"):
        _validate_verdict(payload, _contract(), HEAD_SHA)


def test_non_boolean_stand_ins_are_rejected() -> None:
    payload = _verdict(_basis(head_policy_applied=0))
    with pytest.raises(OrchestrationError, match="MALFORMED_OUTPUT"):
        _validate_verdict(payload, _contract(), HEAD_SHA)


@pytest.mark.parametrize(
    "missing",
    [
        "trust_source_kind",
        "head_policy_applied",
        "executor_self_assessment_treated_as_evidence_only",
    ],
)
def test_missing_review_basis_field_is_rejected(missing: str) -> None:
    basis = _basis()
    del basis[missing]
    with pytest.raises(OrchestrationError, match="MALFORMED_OUTPUT"):
        _validate_verdict(_verdict(basis), _contract(), HEAD_SHA)


def test_extra_review_basis_field_is_rejected() -> None:
    payload = _verdict(_basis(reviewer_note="synthetic"))
    with pytest.raises(OrchestrationError, match="MALFORMED_OUTPUT"):
        _validate_verdict(payload, _contract(), HEAD_SHA)


def test_review_basis_must_be_a_mapping() -> None:
    payload = _verdict(cast(dict[str, Any], "local_read_only_bundle"))
    with pytest.raises(OrchestrationError, match="MALFORMED_OUTPUT"):
        _validate_verdict(payload, _contract(), HEAD_SHA)


def test_executor_report_validation_ignores_review_basis() -> None:
    _validate_report(_report(), _contract(), HEAD_SHA)
    with pytest.raises(OrchestrationError, match="MALFORMED_OUTPUT"):
        _validate_report(
            _report(review_basis=_basis()), _contract(), HEAD_SHA
        )
