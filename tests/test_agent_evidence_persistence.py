"""Персистентность redacted gate evidence и вердикта (#200)."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from tools import agent_memory, agent_orchestrate

SHAPED_VALUE = "".join(
    ("ghp7", "SYNTHETIC", "EXAMPLE", "VALUE", "0123456789")
)
SYNTHETIC_ABSOLUTE = "/home/synthetic/privacy-gateway/README.md"


def _legacy_record() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "record_id": "0" * 32,
        "recorded_at": "2026-01-01T00:00:00+00:00",
        "task_issue": 200,
        "epic_issue": 179,
        "task_class": "docs",
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "duration_seconds": 108,
        "executor_calls": 1,
        "controller_calls": 1,
        "iterations": 1,
        "repair_loops": 0,
        "verdicts": ["FAIL_ESCALATE"],
        "failures": ["REVIEW_ESCALATED"],
        "false_positives": 0,
        "manual_interventions": 0,
        "findings": 2,
        "usage": {"source": "unavailable", "units": None},
    }


def _gate_result() -> dict[str, Any]:
    return {
        "profile": "repository-full",
        "profile_version": "1",
        "complete": True,
        "status": "passed",
        "machine_code": "OK",
        "expected_checks": ["pytest", "ruff", "mypy"],
        "executed_checks": ["pytest", "ruff", "mypy"],
        "checks": [
            {
                "name": "pytest",
                "status": "passed",
                "exit_code": 0,
                "duration_seconds": 10.3,
                "metrics": {"passed": 776, "skipped": 7},
            }
        ],
        "snapshot": {
            "base_sha": "a" * 40,
            "snapshot_method": "commit-tree",
            "snapshot_commit": "c" * 40,
            "tree_hash": "d" * 40,
            "diff_sha256": "e" * 64,
            "provenance_complete": True,
        },
    }


def _verdict_payload() -> dict[str, Any]:
    return {
        "verdict": "FAIL_ESCALATE",
        "review_basis": {
            "trust_source_kind": "base_sha",
            "head_policy_applied": False,
            "executor_self_assessment_treated_as_evidence_only": True,
        },
        "escalation_reason": "blocking findings remain after review",
        "blocking_findings": [
            {
                "severity": "blocking",
                "category": "secret",
                "path": "README.md",
                "line": 12,
                "summary": f"value {SHAPED_VALUE} added to documentation",
            },
            {
                "severity": "blocking",
                "category": "scope",
                "path": SYNTHETIC_ABSOLUTE,
                "summary": "unrelated file changed",
            },
        ],
    }


def _state() -> dict[str, Any]:
    state = agent_orchestrate._new_evidence_state()
    state["tree_unchanged"] = True
    agent_orchestrate._record_gate_evidence(state, _gate_result())
    agent_orchestrate._record_verdict(state, _verdict_payload())
    state["total_seconds"] = 108.786
    state["executor_seconds"] = 60.1
    state["controller_seconds"] = 30.2
    state["gate_seconds"] = 10.3
    return state


def test_legacy_record_still_validates() -> None:
    agent_memory.validate_record(_legacy_record())


def test_current_record_requires_evidence() -> None:
    record = _legacy_record()
    record["schema_version"] = agent_memory.SCHEMA_VERSION
    with pytest.raises(agent_memory.MemoryError):
        agent_memory.validate_record(record)


def test_private_evidence_passes_record_validation() -> None:
    record = _legacy_record()
    record["schema_version"] = agent_memory.SCHEMA_VERSION
    record["evidence"] = agent_orchestrate.private_evidence(_state())
    agent_memory.validate_record(record)


def test_finding_excerpt_keeps_structure_without_raw_text() -> None:
    findings = agent_orchestrate.private_evidence(_state())["verdict"][
        "blocking_findings"
    ]
    assert len(findings) == 2
    first = findings[0]
    assert first["severity"] == "blocking"
    assert first["category"] == "secret"
    assert first["location"] == {
        "path": "README.md",
        "line_start": 12,
        "line_end": None,
    }
    assert first["finding_fingerprint"].startswith("sha256:")
    assert first["redaction"]["applied"] is True


def test_secret_shaped_summary_is_dropped() -> None:
    findings = agent_orchestrate.private_evidence(_state())["verdict"][
        "blocking_findings"
    ]
    assert findings[0]["summary"] is None
    assert findings[0]["redaction"]["summary_dropped"] is True


def test_absolute_location_is_not_persisted() -> None:
    findings = agent_orchestrate.private_evidence(_state())["verdict"][
        "blocking_findings"
    ]
    assert findings[1]["location"]["path"] is None


def test_unusable_finding_fails_closed() -> None:
    redacted = agent_orchestrate.redact_finding("plain controller text")
    assert redacted["category"] == agent_orchestrate.FINDING_REDACTION_FAILED
    assert redacted["summary"] is None
    assert redacted["finding_fingerprint"].startswith("sha256:")


def test_public_projection_is_whitelisted() -> None:
    public = agent_orchestrate.public_evidence(_state())
    assert set(public) == {"schema_version", "gate", "snapshot", "duration_seconds"}
    assert set(public["gate"]) == {
        *agent_orchestrate.PUBLIC_GATE_FIELDS,
        "checks",
    }
    assert set(public["snapshot"]) == set(agent_orchestrate.PUBLIC_SNAPSHOT_FIELDS)
    assert set(public["gate"]["checks"][0]) == set(
        agent_orchestrate.PUBLIC_CHECK_FIELDS
    )


def test_public_projection_excludes_judgement_and_metrics() -> None:
    serialized = json.dumps(agent_orchestrate.public_evidence(_state()))
    for token in ("verdict", "review_basis", "escalation_reason", "metrics"):
        assert token not in serialized


def test_public_projection_proves_full_gate_directly() -> None:
    public = agent_orchestrate.public_evidence(_state())
    assert public["gate"]["complete"] is True
    assert public["gate"]["profile"] == "repository-full"
    assert public["gate"]["expected_checks"] == public["gate"]["executed_checks"]
    assert public["snapshot"]["tree_unchanged"] is True
    assert public["snapshot"]["tree_hash"] == "d" * 40


def test_neither_layer_persists_secrets_or_absolute_paths() -> None:
    state = _state()
    serialized = json.dumps(
        [
            agent_orchestrate.private_evidence(state),
            agent_orchestrate.public_evidence(state),
        ],
        ensure_ascii=False,
    )
    assert SHAPED_VALUE not in serialized
    assert SYNTHETIC_ABSOLUTE not in serialized
    assert "/home/" not in serialized


def test_evidence_is_persisted_for_incomplete_gate() -> None:
    state = agent_orchestrate._new_evidence_state()
    agent_orchestrate._record_gate_evidence(
        state,
        {
            "profile": "repository-full",
            "complete": False,
            "status": "incomplete",
            "machine_code": "GATE_EVIDENCE_INCOMPLETE",
        },
    )
    private = agent_orchestrate.private_evidence(state)
    assert private["gate"]["machine_code"] == "GATE_EVIDENCE_INCOMPLETE"
    assert private["gate"]["complete"] is False
    assert private["verdict"] is None
    assert private["snapshot"]["tree_unchanged"] is None


def test_run_result_contract_is_unchanged() -> None:
    names = {
        item.name for item in dataclasses.fields(agent_orchestrate.RunResult)
    }
    assert names == {"status", "machine_code", "repair_iterations", "run_id"}
def test_record_without_evidence_remains_valid() -> None:
    record = _legacy_record()
    record["schema_version"] = agent_memory.SCHEMA_VERSION
    record["evidence"] = None
    agent_memory.validate_record(record)


def test_promotion_contract_version_is_independent() -> None:
    assert agent_memory.PROMOTION_SCHEMA_VERSION == "1.0"
    assert agent_memory.SCHEMA_VERSION != agent_memory.PROMOTION_SCHEMA_VERSION


def test_promotion_schema_version_is_unchanged() -> None:
    candidate = {
        "schema_version": agent_memory.PROMOTION_SCHEMA_VERSION,
        "candidate_id": "c" * 32,
        "target": "validator",
        "evidence_count": 2,
        "classified": True,
        "sanitized": True,
        "secret_privacy_scan_passed": True,
        "safe_for_full_disclosure": True,
        "independent_review_passed": True,
        "trust_source": "base_sha",
        "head_instructions_applied": False,
        "separate_change": True,
        "human_approval": False,
    }
    agent_memory.validate_promotion(candidate)
def _pass_with_notes_payload() -> dict[str, Any]:
    return {
        "verdict": "PASS_WITH_NOTES",
        "review_basis": {
            "trust_source_kind": "base_sha",
            "head_policy_applied": False,
            "executor_self_assessment_treated_as_evidence_only": True,
        },
        "escalation_reason": "",
        "blocking_findings": [],
    }


def _failed_gate_result() -> dict[str, Any]:
    return {
        "profile": "repository-full",
        "profile_version": "1",
        "complete": True,
        "status": "failed",
        "machine_code": "GATE_PROFILE_MISMATCH",
        "expected_checks": ["pytest", "ruff", "mypy"],
        "executed_checks": ["pytest", "ruff", "mypy"],
        "checks": [
            {
                "name": "pytest",
                "status": "failed",
                "exit_code": 1,
                "duration_seconds": 9.7,
                "metrics": {"passed": 770, "failed": 6},
            },
            {
                "name": "ruff",
                "status": "passed",
                "exit_code": 0,
                "duration_seconds": 0.4,
                "metrics": {},
            },
        ],
        "snapshot": {
            "base_sha": "a" * 40,
            "snapshot_method": "commit-tree",
            "snapshot_commit": "c" * 40,
            "tree_hash": "d" * 40,
            "diff_sha256": "e" * 64,
            "provenance_complete": True,
        },
    }


def test_pass_with_notes_verdict_is_persisted() -> None:
    state = agent_orchestrate._new_evidence_state()
    state["tree_unchanged"] = True
    agent_orchestrate._record_gate_evidence(state, _gate_result())
    agent_orchestrate._record_verdict(state, _pass_with_notes_payload())
    state["total_seconds"] = 41.5
    private = agent_orchestrate.private_evidence(state)
    assert private["verdict"]["verdict"] == "PASS_WITH_NOTES"
    assert private["verdict"]["blocking_findings"] == []
    basis = private["verdict"]["review_basis"]
    assert basis["trust_source_kind"] == "base_sha"
    assert basis["head_policy_applied"] is False
    assert basis["executor_self_assessment_treated_as_evidence_only"] is True
    assert private["gate"]["complete"] is True
    assert private["snapshot"]["tree_unchanged"] is True


def test_pass_with_notes_record_passes_validation() -> None:
    state = agent_orchestrate._new_evidence_state()
    state["tree_unchanged"] = True
    agent_orchestrate._record_gate_evidence(state, _gate_result())
    agent_orchestrate._record_verdict(state, _pass_with_notes_payload())
    state["total_seconds"] = 41.5
    record = _legacy_record()
    record["schema_version"] = agent_memory.SCHEMA_VERSION
    record["verdicts"] = ["PASS_WITH_NOTES"]
    record["failures"] = []
    record["findings"] = 0
    record["evidence"] = agent_orchestrate.private_evidence(state)
    agent_memory.validate_record(record)


def test_evidence_is_persisted_for_failed_gate() -> None:
    state = agent_orchestrate._new_evidence_state()
    agent_orchestrate._record_gate_evidence(state, _failed_gate_result())
    state["total_seconds"] = 12.25
    state["gate_seconds"] = 10.1
    private = agent_orchestrate.private_evidence(state)
    assert private["gate"]["status"] == "failed"
    assert private["gate"]["machine_code"] == "GATE_PROFILE_MISMATCH"
    assert private["gate"]["expected_checks"] == private["gate"]["executed_checks"]
    assert [check["name"] for check in private["gate"]["checks"]] == [
        "pytest",
        "ruff",
    ]
    assert private["gate"]["checks"][0]["exit_code"] == 1
    assert private["verdict"] is None
    assert private["snapshot"]["tree_unchanged"] is None
    assert private["snapshot"]["tree_hash"] == "d" * 40
    assert private["durations"]["total_seconds"] == 12.25


def test_failed_gate_record_passes_validation() -> None:
    state = agent_orchestrate._new_evidence_state()
    agent_orchestrate._record_gate_evidence(state, _failed_gate_result())
    state["total_seconds"] = 12.25
    record = _legacy_record()
    record["schema_version"] = agent_memory.SCHEMA_VERSION
    record["controller_calls"] = 0
    record["iterations"] = 0
    record["findings"] = 0
    record["failures"] = ["GATE_PROFILE_MISMATCH"]
    record["evidence"] = agent_orchestrate.private_evidence(state)
    agent_memory.validate_record(record)


def test_public_projection_for_failed_gate_has_summary_without_verdict() -> None:
    state = agent_orchestrate._new_evidence_state()
    agent_orchestrate._record_gate_evidence(state, _failed_gate_result())
    state["total_seconds"] = 12.25
    public = agent_orchestrate.public_evidence(state)
    assert public["gate"]["status"] == "failed"
    assert public["gate"]["machine_code"] == "GATE_PROFILE_MISMATCH"
    assert public["snapshot"]["tree_unchanged"] is None
    assert set(public) == {"schema_version", "gate", "snapshot", "duration_seconds"}
    serialized = json.dumps(public)
    for token in ("verdict", "review_basis", "escalation_reason", "metrics"):
        assert token not in serialized
