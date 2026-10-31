"""Synthetic owner source shape contracts; no human event is certified here."""

from __future__ import annotations

from typing import Any

import pytest
from tools import agent_final_preparation_records as records
from tools import agent_owner_final_sources as sources
from tools.agent_epic_loop import PhaseBlocked

from tests.test_agent_final_preparation_records import original


def document(kind: str) -> dict[str, Any]:
    value = original("approval")
    for key in ("approved_digest", "approved_at", "final_demo_digest"):
        value.pop(key)
    value["schema_version"] = "1.0"
    if kind == "command":
        value.update(
            kind="OWNER_FINAL_CANDIDATE_APPROVAL",
            procedure="ADR303_FINAL_OWNER_APPROVAL_V1",
            approval_digest="a" * 64,
            final_demo_digest="b" * 64,
            approved_at=110,
        )
    else:
        value.update(
            kind="OWNER_REAL_FINAL_DEMO",
            procedure="ADR303_OWNER_REAL_FINAL_DEMO_V1",
            started_at=100,
            completed_at=109,
            scenarios=[
                {
                    "id": "synthetic-cli",
                    "surface": "CLI",
                    "steps": ["Use a synthetic input through the CLI."],
                    "expected": "The synthetic value is protected.",
                }
            ],
            observations=[
                {
                    "id": "synthetic-cli",
                    "observed_at": 105,
                    "outcome": "OWNER_OBSERVED_EXPECTED",
                    "observation": "Synthetic contract fixture only.",
                }
            ],
        )
    return records.seal(value)


@pytest.mark.parametrize("kind", ["command", "demo"])
def test_closed_synthetic_shape_is_not_human_authority(kind: str) -> None:
    raw = document(kind)
    assert getattr(sources, kind)(raw) == raw
    # No owner pin, installed procedure, source graph or effect is produced.
    assert "permissions" not in raw
    assert "approved" not in raw


@pytest.mark.parametrize("kind", ["command", "demo"])
@pytest.mark.parametrize("attack", ["extra", "version", "preseed", "bool-pr", "digest"])
def test_source_identity_cannot_add_authority(kind: str, attack: str) -> None:
    raw = document(kind)
    if attack == "extra":
        raw["approved"] = True
    elif attack == "version":
        raw["schema_version"] = "2.0"
    elif attack == "preseed":
        raw["kind"] = "OWNER_ORIGINAL_FINAL_COUNTER_PROVISIONING"
    elif attack == "bool-pr":
        raw["pr"] = True
    else:
        raw["candidate_digest"] = "invalid"
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        getattr(sources, kind)(records.seal(raw))


@pytest.mark.parametrize(
    "attack",
    [
        "missing-observation",
        "duplicate-id",
        "foreign-id",
        "future-observation",
        "reversed-time",
        "boolean-time",
        "model-boolean",
        "extra-observation-field",
        "empty-steps",
        "oversize-step",
        "oversize-observation",
        "too-many-scenarios",
    ],
)
def test_demo_must_cover_exact_bounded_human_scenario_shape(attack: str) -> None:
    raw = document("demo")
    scenario, observation = raw["scenarios"][0], raw["observations"][0]
    if attack == "missing-observation":
        raw["observations"] = []
    elif attack == "duplicate-id":
        raw["scenarios"].append(dict(scenario))
        raw["observations"].append(dict(observation))
    elif attack == "foreign-id":
        observation["id"] = "unreviewed-scenario"
    elif attack == "future-observation":
        observation["observed_at"] = 110
    elif attack == "reversed-time":
        raw["completed_at"] = 99
    elif attack == "boolean-time":
        observation["observed_at"] = True
    elif attack == "model-boolean":
        observation["outcome"] = True
    elif attack == "extra-observation-field":
        observation["approved"] = True
    elif attack == "empty-steps":
        scenario["steps"] = []
    elif attack == "oversize-step":
        scenario["steps"] = ["x" * 513]
    elif attack == "oversize-observation":
        observation["observation"] = "x" * 2049
    else:
        raw["scenarios"] = [dict(scenario, id=f"synthetic-{i}") for i in range(17)]
        raw["observations"] = [
            dict(observation, id=f"synthetic-{i}") for i in range(17)
        ]
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        sources.demo(records.seal(raw))
