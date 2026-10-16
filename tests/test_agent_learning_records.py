"""Redacted record schemas are exact; their hashes do not grant authority."""

from __future__ import annotations

from typing import Any

import pytest
from tools import agent_learning as c
from tools import agent_learning_records as records


def observation(*, delivery: bool = False) -> dict[str, Any]:
    value = {
        "schema_version": "1.0",
        "kind": "TASK_DELIVERY" if delivery else "TERMINAL_WORKFLOW",
        "identity": {
            "repository": "OWNER/repo",
            "epic": 294,
            "task": 302,
            "run_id": "a" * 32,
            "policy_sha": "b" * 40,
            "head_sha": "c" * 40,
            "merge_sha": "d" * 40 if delivery else None,
        },
        "outcome": {
            "status": "TASK_DONE" if delivery else "FAIL_ESCALATE",
            "machine_codes": [] if delivery else ["GATE_FAILED"],
        },
        "metrics": {
            "findings": 0,
            "repair_iterations": 1,
            "executor_calls": 2,
            "controller_calls": 1,
            "duration_seconds": 10,
        },
        "source": {
            key: "e" * 64 if delivery or key == "workflow_digest" else None
            for key in records.SOURCE_FIELDS
        },
    }
    value["record_id"] = records.observation_id(value)
    return c.seal(value)


@pytest.mark.parametrize("delivery", [False, True])
def test_exact_observed_outcomes(delivery: bool) -> None:
    assert records.observation(observation(delivery=delivery))["kind"] == (
        "TASK_DELIVERY" if delivery else "TERMINAL_WORKFLOW"
    )


@pytest.mark.parametrize("change", ["raw", "success", "metric", "source", "nonce"])
def test_inconsistent_or_unredacted_observation(change: str) -> None:
    value = observation()
    if change == "raw":
        value["raw_summary"] = "synthetic"
    elif change == "success":
        value["outcome"]["status"] = "TASK_DONE"
    elif change == "metric":
        value["metrics"]["executor_calls"] = True
    elif change == "source":
        value["source"]["workflow_digest"] = None
    else:
        value["identity"]["run_id"] = "synthetic path /tmp/private"
    with pytest.raises(c.LearningError):
        records.observation(c.seal(value))


def invocation() -> dict[str, Any]:
    value = {
        "schema_version": "1.0",
        "identity": {
            "repository": "OWNER/repo",
            "epic": 294,
            "task": 302,
            "policy_sha": "a" * 40,
            "owner_profile_digest": "b" * 64,
            "original_run_id": "c" * 32,
        },
        "role": "executor",
        "ordinal": 1,
        "repair_iteration": 0,
        "catalog_digest": "d" * 64,
        "selection_digest": "e" * 64,
        "lesson_ids": [],
        "input_digest": "f" * 64,
        "prompt_digest": "0" * 64,
        "status": "RESERVED",
    }
    value["invocation_key"] = records.invocation_id(value)
    return c.seal(value)


def test_invocation_binds_original_counter_not_model_summary() -> None:
    value = invocation()
    assert records.invocation(value)["status"] == "RESERVED"
    value["ordinal"] = 2
    with pytest.raises(c.LearningError):
        records.invocation(c.seal(value))


def test_evaluation_schema_checks_derived_comparison() -> None:
    from tests.test_agent_learning import measurement

    value = c.compare(measurement(), measurement(hints=True))
    assert records.evaluation(value) == value
    value["comparison"]["call_delta"] = "faster"
    with pytest.raises(c.LearningError):
        records.evaluation(c.seal(value))
