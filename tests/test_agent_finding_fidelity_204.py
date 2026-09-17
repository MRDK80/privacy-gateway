"""Регрессия #204: severity и location сохраняются при редакции findings."""

from __future__ import annotations

from typing import Any

from tools import agent_memory, agent_orchestrate

CANONICAL_FINDING: dict[str, Any] = {
    "severity": "high",
    "requirement": "Synthetic requirement text for the regression fixture.",
    "location": {"file": "README.md", "line": 12},
    "evidence": "Synthetic evidence text for the regression fixture.",
    "required_fix": "Synthetic required fix text for the regression fixture.",
}


def test_canonical_severity_survives_redaction() -> None:
    redacted = agent_orchestrate.redact_finding(CANONICAL_FINDING)
    assert redacted["severity"] == "high"


def test_every_canonical_severity_survives_redaction() -> None:
    for severity in ("critical", "high", "medium", "low"):
        finding = dict(CANONICAL_FINDING, severity=severity)
        assert agent_orchestrate.redact_finding(finding)["severity"] == severity


def test_severity_outside_enum_falls_back_to_unknown() -> None:
    finding = dict(CANONICAL_FINDING, severity="catastrophic")
    assert agent_orchestrate.redact_finding(finding)["severity"] == "unknown"


def test_canonical_location_survives_redaction() -> None:
    redacted = agent_orchestrate.redact_finding(CANONICAL_FINDING)
    assert redacted["location"]["path"] == "README.md"
    assert redacted["location"]["line_start"] == 12
    assert redacted["location"]["line_end"] is None


def test_absolute_location_is_still_rejected() -> None:
    finding = dict(CANONICAL_FINDING, location={"file": "/etc/passwd"})
    redacted = agent_orchestrate.redact_finding(finding)
    assert redacted["location"]["path"] is None


def test_private_store_accepts_canonical_severity() -> None:
    redacted = agent_orchestrate.redact_finding(CANONICAL_FINDING)
    assert redacted["severity"] in agent_memory.FINDING_SEVERITIES


def test_legacy_severity_still_accepted() -> None:
    finding = dict(CANONICAL_FINDING, severity="blocking")
    assert agent_orchestrate.redact_finding(finding)["severity"] == "blocking"
