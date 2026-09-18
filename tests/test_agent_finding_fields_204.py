"""Регрессия #204: раздельные redacted-поля finding и причины отказа."""

from __future__ import annotations

from typing import Any

from tools import agent_memory, agent_orchestrate

SECRET_SHAPED = "_".join(("ghp", "SYNTHETIC", "EXAMPLE", "0123456789"))

CANONICAL_FINDING: dict[str, Any] = {
    "severity": "high",
    "requirement": "Synthetic requirement text for the regression fixture.",
    "location": {"file": "README.md", "line": 12},
    "evidence": "Synthetic evidence text for the regression fixture.",
    "required_fix": "Synthetic required fix text for the regression fixture.",
}


def test_all_three_text_fields_are_persisted() -> None:
    redacted = agent_orchestrate.redact_finding(CANONICAL_FINDING)
    assert redacted["requirement"].startswith("Synthetic requirement")
    assert redacted["evidence"].startswith("Synthetic evidence")
    assert redacted["required_fix"].startswith("Synthetic required fix")


def test_summary_dropped_is_false_when_text_survives() -> None:
    redacted = agent_orchestrate.redact_finding(CANONICAL_FINDING)
    assert redacted["redaction"]["summary_dropped"] is False
    assert redacted["redaction"]["dropped_fields"] == []
    assert redacted["redaction"]["drop_reasons"] == {}


def test_redaction_version_is_two() -> None:
    redacted = agent_orchestrate.redact_finding(CANONICAL_FINDING)
    assert redacted["redaction"]["version"] == "2"


def test_secret_shaped_evidence_does_not_remove_requirement() -> None:
    finding = dict(CANONICAL_FINDING, evidence=SECRET_SHAPED)
    redacted = agent_orchestrate.redact_finding(finding)
    assert redacted["evidence"] is None
    assert redacted["requirement"] is not None
    assert redacted["required_fix"] is not None
    assert redacted["redaction"]["dropped_fields"] == ["evidence"]
    assert redacted["redaction"]["drop_reasons"] == {"evidence": "looks_like_secret"}
    assert redacted["redaction"]["summary_dropped"] is False


def test_parent_directory_text_is_dropped_with_reason() -> None:
    finding = dict(CANONICAL_FINDING, required_fix="Move ../secrets.env out of tree")
    redacted = agent_orchestrate.redact_finding(finding)
    assert redacted["required_fix"] is None
    assert redacted["redaction"]["dropped_fields"] == ["required_fix"]
    assert redacted["redaction"]["drop_reasons"] == {"required_fix": "path_like"}


def test_slash_is_stripped_by_alphabet_before_path_check() -> None:
    finding = dict(CANONICAL_FINDING, required_fix="/etc/hosts must be reverted")
    redacted = agent_orchestrate.redact_finding(finding)
    assert redacted["required_fix"] == "etchosts must be reverted"
    assert redacted["redaction"]["dropped_fields"] == []
    assert redacted["redaction"]["summary_dropped"] is False


def test_missing_text_field_is_reported_as_missing() -> None:
    finding = dict(CANONICAL_FINDING)
    del finding["evidence"]
    redacted = agent_orchestrate.redact_finding(finding)
    assert redacted["evidence"] is None
    assert redacted["redaction"]["drop_reasons"] == {"evidence": "missing"}


def test_evidence_limit_is_stricter_than_requirement_limit() -> None:
    assert (
        agent_orchestrate.EVIDENCE_TEXT_MAX_CHARS
        < agent_orchestrate.SUMMARY_MAX_CHARS
    )
    long_text = "evidence " * 60
    finding = dict(CANONICAL_FINDING, evidence=long_text, requirement=long_text)
    redacted = agent_orchestrate.redact_finding(finding)
    assert len(redacted["evidence"]) < len(redacted["requirement"])


def test_new_shape_matches_private_store_contract() -> None:
    redacted = agent_orchestrate.redact_finding(CANONICAL_FINDING)
    assert set(redacted) == agent_memory.FINDING_KEYS_V2
    assert set(redacted["redaction"]) == agent_memory.REDACTION_KEYS_V2


def test_unparseable_finding_still_fails_closed() -> None:
    redacted = agent_orchestrate.redact_finding("plain controller text")
    assert redacted["category"] == agent_orchestrate.FINDING_REDACTION_FAILED
    assert redacted["redaction"]["summary_dropped"] is True
    assert redacted["redaction"]["dropped_fields"] == [
        "evidence",
        "required_fix",
        "requirement",
    ]
    assert set(redacted["redaction"]["drop_reasons"].values()) == {"unparseable"}
