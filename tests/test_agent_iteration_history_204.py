"""Регрессия #204: история итераций repair-цикла и версия приватной записи."""

from __future__ import annotations

from typing import Any

from tools import agent_memory, agent_orchestrate


def _verdict(iteration: int, verdict: str, count: int) -> dict[str, Any]:
    findings = [
        {
            "severity": "high",
            "requirement": f"Synthetic requirement {iteration} {index}.",
            "location": {"file": "README.md", "line": 12 + index},
            "evidence": f"Synthetic evidence {iteration} {index}.",
            "required_fix": f"Synthetic fix {iteration} {index}.",
        }
        for index in range(count)
    ]
    return {
        "verdict": verdict,
        "review_basis": {"trust_source_kind": "base_sha"},
        "escalation_reason": "budget_exhausted" if count and iteration == 2 else None,
        "repair_iteration": iteration,
        "blocking_findings": findings,
    }


def _state_with_three_iterations() -> dict[str, Any]:
    state: dict[str, Any] = {}
    agent_orchestrate._record_verdict(state, _verdict(0, "FAIL_RETRY", 3))
    agent_orchestrate._record_verdict(state, _verdict(1, "FAIL_RETRY", 2))
    agent_orchestrate._record_verdict(state, _verdict(2, "FAIL_ESCALATE", 2))
    return state


def test_history_keeps_every_iteration() -> None:
    state = _state_with_three_iterations()
    history = state["verdict"]["iteration_history"]
    assert [item["iteration"] for item in history] == [0, 1, 2]
    assert [item["verdict"] for item in history] == [
        "FAIL_RETRY",
        "FAIL_RETRY",
        "FAIL_ESCALATE",
    ]


def test_history_sum_matches_findings_counter() -> None:
    state = _state_with_three_iterations()
    history = state["verdict"]["iteration_history"]
    total = sum(len(item["blocking_findings"]) for item in history)
    assert total == 7


def test_final_verdict_projection_is_last_iteration() -> None:
    state = _state_with_three_iterations()
    verdict = state["verdict"]
    assert verdict["verdict"] == "FAIL_ESCALATE"
    assert len(verdict["blocking_findings"]) == 2


def test_repeated_fingerprints_are_not_deduplicated() -> None:
    state: dict[str, Any] = {}
    payload = _verdict(0, "FAIL_RETRY", 1)
    agent_orchestrate._record_verdict(state, payload)
    agent_orchestrate._record_verdict(state, dict(payload, repair_iteration=1))
    history = state["verdict"]["iteration_history"]
    first = history[0]["blocking_findings"][0]["finding_fingerprint"]
    second = history[1]["blocking_findings"][0]["finding_fingerprint"]
    assert first == second
    assert len(history) == 2


def test_iteration_number_falls_back_to_position() -> None:
    state: dict[str, Any] = {}
    payload = _verdict(0, "FAIL_RETRY", 1)
    del payload["repair_iteration"]
    agent_orchestrate._record_verdict(state, payload)
    agent_orchestrate._record_verdict(state, payload)
    history = state["verdict"]["iteration_history"]
    assert [item["iteration"] for item in history] == [0, 1]


def test_history_passes_private_validation() -> None:
    state = _state_with_three_iterations()
    agent_memory._validate_verdict_evidence(state["verdict"])


def test_schema_version_is_one_two() -> None:
    assert agent_memory.SCHEMA_VERSION == "1.2"
    assert agent_memory.PROMOTION_SCHEMA_VERSION == "1.0"


def test_supported_versions_cover_legacy_records() -> None:
    assert agent_memory.SUPPORTED_SCHEMA_VERSIONS == frozenset(
        {"1.0", "1.1", "1.2"}
    )


def test_version_parsing_is_numeric() -> None:
    assert agent_memory._parse_version("1.10") == (1, 10)


def test_version_parsing_accepts_syntactic_versions() -> None:
    assert agent_memory._parse_version("2.0") == (2, 0)
    assert agent_memory._parse_version("1.10") == (1, 10)
    assert agent_memory._parse_version("1.2") == (1, 2)


def test_malformed_version_is_rejected() -> None:
    for value in ("1", "1.a", "", "one.two", "1.2.3", "-1.0", None):
        try:
            agent_memory._parse_version(value)
        except agent_memory.MemoryError:
            continue
        raise AssertionError(f"version accepted unexpectedly: {value!r}")


def test_foreign_major_is_not_supported() -> None:
    assert "2.0" not in agent_memory.SUPPORTED_SCHEMA_VERSIONS
    assert agent_memory.SCHEMA_VERSION in agent_memory.SUPPORTED_SCHEMA_VERSIONS
    assert agent_memory.LEGACY_SCHEMA_VERSION in agent_memory.SUPPORTED_SCHEMA_VERSIONS
