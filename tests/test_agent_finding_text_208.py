"""Регрессии редакции приватных findings (#208)."""

from __future__ import annotations

import pytest
from tools import agent_gate, agent_memory, agent_orchestrate


def _finding(**fields: str) -> dict[str, str]:
    return {
        "requirement": "Inspect docs/agent-contracts.md",
        "evidence": "Compare roadmap/base/head refs",
        "required_fix": "Verify docs/agent-contracts.md",
        **fields,
    }


def test_relative_paths_survive_in_each_field() -> None:
    redacted = agent_orchestrate.redact_finding(_finding())
    assert redacted["requirement"] == "Inspect docs/agent-contracts.md"
    assert redacted["evidence"] == "Compare roadmap/base/head refs"
    assert redacted["required_fix"] == "Verify docs/agent-contracts.md"
    assert redacted["redaction"]["truncated_fields"] == []
    assert redacted["redaction"]["version"] == "3"


@pytest.mark.parametrize(
    "unsafe",
    [
        "/etc/hosts",
        "~/notes.txt",
        "/tmp/notes.txt",
        "../notes.txt",
        "docs/../notes.txt",
        "C:/Users/example/notes.txt",
        r"C:\Users\example\notes.txt",
        r"\\server\share\notes.txt",
    ],
)
def test_unsafe_paths_are_dropped_before_filtering(unsafe: str) -> None:
    redacted = agent_orchestrate.redact_finding(
        _finding(evidence=f"Inspect {unsafe} now")
    )
    assert redacted["evidence"] is None
    assert redacted["redaction"]["drop_reasons"]["evidence"] == "path_like"


def test_secret_like_fragment_is_dropped() -> None:
    secret = "_".join(("ghp", "SYNTHETIC", "EXAMPLE", "0123456789"))
    redacted = agent_orchestrate.redact_finding(_finding(evidence=f"Found {secret}"))
    assert redacted["evidence"] is None
    assert redacted["redaction"]["drop_reasons"]["evidence"] == "looks_like_secret"


def test_evidence_truncates_on_word_boundary_and_marks_it() -> None:
    evidence = "word " * 25
    redacted = agent_orchestrate.redact_finding(_finding(evidence=evidence))
    assert redacted["evidence"] == ("word " * 20).strip()
    assert redacted["redaction"]["truncated_fields"] == ["evidence"]


def test_snapshot_sha_is_not_silently_cut() -> None:
    sha = "a1" * 20
    evidence = ("word " * 12) + f"snapshot commit {sha} remains"
    redacted = agent_orchestrate.redact_finding(_finding(evidence=evidence))
    assert redacted["evidence"] is not None
    assert (
        sha in redacted["evidence"]
        or "evidence" in redacted["redaction"]["truncated_fields"]
    )


def test_gate_summary_keeps_its_stricter_alphabet() -> None:
    summary = agent_gate._summary("pytest", "passed", {"note": "/tmp/synthetic"})
    assert "/tmp/synthetic" not in summary


def test_fingerprint_is_independent_of_redaction_version() -> None:
    raw = _finding()
    redacted = agent_orchestrate.redact_finding(raw)
    assert redacted["finding_fingerprint"] == agent_orchestrate._finding_fingerprint(
        raw
    )
    agent_memory._validate_finding(redacted)


def test_unparseable_finding_keeps_fail_closed_marker() -> None:
    redacted = agent_orchestrate.redact_finding("unparseable")
    assert redacted["category"] == agent_orchestrate.FINDING_REDACTION_FAILED
    assert redacted["redaction"]["truncated_fields"] == []
    agent_memory._validate_finding(redacted)
