"""Representation tests; no Session, original provenance or readiness claim."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from tools.agent_continuation import canonical, digest
from tools.agent_final_learning_packet import PacketShapeError, encode_packet


def inputs() -> dict[str, Any]:
    return {
        "identity": {
            "repository": "example/repo", "epic": 294, "policy_sha": "a" * 40,
            "mandate_digest": "sha256:" + "b" * 64,
            "launch_digest": "sha256:" + "c" * 64,
            "learning_profile_digest": "sha256:" + "d" * 64,
        },
        "tasks": [{"task": 306, "workflow_run_id": None,
                   "workflow_calls": 0, "planning_attempts": 0}],
        "calls": [], "retained": [], "uses": [], "measurements": [],
        "negative_checks": [], "max_artifact_bytes": 262144,
    }


def planning(raw: dict[str, Any]) -> None:
    raw["tasks"][0]["planning_attempts"] = 1
    raw["calls"] = [{
        "task": 306, "family": "PLANNING", "role": "planning_review",
        "ordinal": 1, "invocation_key": "e" * 64,
        "native_invocation_digest": None, "binding_digest": None,
        "metadata_digest": "f" * 64, "source_digest": "0" * 64,
    }]
    raw["uses"] = [{"call_key": "e" * 64, "ordered_lessons": []}]


def test_canonical_digest_and_exact_byte_boundary() -> None:
    raw = inputs()
    packet = encode_packet(**raw)
    expected = deepcopy(packet)
    del expected["source_digest"]
    assert packet["source_digest"] == digest(expected)
    assert set(packet) == {
        "identity", "tasks", "calls", "retained", "uses", "measurements",
        "negative_checks", "source_digest",
    }
    raw["max_artifact_bytes"] = len(canonical(packet))
    assert encode_packet(**raw) == packet
    raw["max_artifact_bytes"] -= 1
    with pytest.raises(PacketShapeError):
        encode_packet(**raw)


def test_cross_store_multiplicity_and_empty_origin_preserved() -> None:
    raw = inputs()
    raw["tasks"].append({"task": 305, "workflow_run_id": None,
                         "workflow_calls": 0, "planning_attempts": 0})
    for task in (306, 305):
        raw["retained"].append({
            "family": "WORKFLOW_STORE", "task": task, "kind": "candidate",
            "key": "e" * 64, "record_digest": "f" * 64,
        })
    raw["negative_checks"] = [
        {"family": "WORKFLOW_STORE", "task": task, "source_digest": "a" * 64}
        for task in (306, 305)
    ] + [{"family": "PLANNING_STORE", "task": None, "source_digest": "b" * 64}]
    packet = encode_packet(**raw)
    assert [row["task"] for row in packet["tasks"]] == [305, 306]
    assert [row["task"] for row in packet["retained"]] == [305, 306]
    assert packet["negative_checks"][0]["task"] is None
    assert len(packet["negative_checks"]) == 3
    raw["negative_checks"].pop(0)
    with pytest.raises(PacketShapeError):
        encode_packet(**raw)


def test_original_lesson_order_and_owned_output() -> None:
    raw = inputs()
    planning(raw)
    lessons = [{field: char * 64 for field in (
        "lesson_id", "verification_digest", "candidate_digest", "before_digest",
        "after_digest", "pair_digest",
    )} for char in ("f", "a")]
    raw["uses"][0]["ordered_lessons"] = lessons
    packet = encode_packet(**raw)
    lessons[0]["lesson_id"] = "b" * 64
    assert [row["lesson_id"] for row in packet["uses"][0]["ordered_lessons"]] == [
        "f" * 64, "a" * 64,
    ]
    raw["identity"]["repository"] = "changed/repo"
    assert packet["identity"]["repository"] == "example/repo"


@pytest.mark.parametrize("fault", [
    "extra", "tag", "bool", "count", "missing_use", "extra_use", "collision",
    "lesson_duplicate", "wrong_kind", "planning_native", "measurement_family",
    "missing_run", "wrong_measurement_run", "duplicate_origin", "limit_bool",
])
def test_closed_grammar_and_consistency_refusals(fault: str) -> None:
    raw = inputs()
    planning(raw)
    if fault == "extra":
        raw["calls"][0]["payload"] = "SYNTHETIC-SECRET"
    elif fault == "tag":
        raw["identity"]["launch_digest"] = "c" * 64
    elif fault == "bool":
        raw["calls"][0]["ordinal"] = True
    elif fault == "count":
        raw["tasks"][0]["planning_attempts"] = 2
    elif fault == "missing_use":
        raw["uses"] = []
    elif fault == "extra_use":
        raw["uses"].append({"call_key": "a" * 64, "ordered_lessons": []})
    elif fault == "collision":
        other = dict(raw["calls"][0], family="WORKFLOW", role="executor",
                     native_invocation_digest="a" * 64, binding_digest="b" * 64)
        raw["calls"].append(other)
        raw["tasks"][0].update(workflow_calls=1, workflow_run_id="a" * 32)
    elif fault == "lesson_duplicate":
        lesson = {field: "a" * 64 for field in (
            "lesson_id", "verification_digest", "candidate_digest", "before_digest",
            "after_digest", "pair_digest",
        )}
        raw["uses"][0]["ordered_lessons"] = [lesson, lesson]
    elif fault == "wrong_kind":
        raw["retained"] = [{"family": "PLANNING_STORE", "task": None,
                            "kind": "invocation", "key": "a" * 64,
                            "record_digest": "b" * 64}]
    elif fault == "planning_native":
        raw["calls"][0]["native_invocation_digest"] = "a" * 64
    elif fault in ("measurement_family", "wrong_measurement_run"):
        raw["measurements"] = [{
            "family": "PLANNING" if fault == "measurement_family" else "WORKFLOW",
            "task": 306, "run_id": "a" * 32, "measurement_digest": "a" * 64,
            "baseline_digest": None, "evaluation_digest": None,
        }]
    elif fault == "missing_run":
        raw["tasks"][0]["workflow_calls"] = 1
    elif fault == "duplicate_origin":
        row = {"family": "PLANNING_STORE", "task": None, "source_digest": "a" * 64}
        raw["negative_checks"] = [row, row]
    else:
        raw["max_artifact_bytes"] = True
    with pytest.raises(PacketShapeError, match="^NEUTRAL_PACKET_INVALID$"):
        encode_packet(**raw)


def test_terminal_zero_calls_preserves_original_workflow_run_identity() -> None:
    # Representation only: the fixed source reader must prove the original.
    raw = inputs()
    raw["tasks"][0]["workflow_run_id"] = "a" * 32
    raw["measurements"] = [{
        "family": "WORKFLOW", "task": 306, "run_id": "a" * 32,
        "measurement_digest": "b" * 64,
        "baseline_digest": None, "evaluation_digest": None,
    }]
    packet = encode_packet(**raw)
    assert packet["tasks"] == raw["tasks"]
    assert packet["tasks"][0]["workflow_run_id"] == "a" * 32
    assert packet["calls"] == [] and packet["uses"] == []
    assert packet["measurements"][0]["run_id"] == "a" * 32


def test_null_zero_shape_grants_no_source_provenance() -> None:
    # Null remains representable; no-W producer/proof is outside this kernel.
    raw = inputs()
    packet = encode_packet(**raw)
    assert packet["tasks"] == raw["tasks"]
    assert set(packet) == {
        "identity", "tasks", "calls", "retained", "uses", "measurements",
        "negative_checks", "source_digest",
    }


def test_null_run_with_positive_workflow_calls_still_refuses() -> None:
    raw = inputs()
    raw["tasks"][0]["workflow_calls"] = 1
    raw["calls"] = [{
        "task": 306, "family": "WORKFLOW", "role": "executor", "ordinal": 1,
        "invocation_key": "e" * 64, "native_invocation_digest": "a" * 64,
        "binding_digest": "b" * 64, "metadata_digest": "f" * 64,
        "source_digest": "0" * 64,
    }]
    raw["uses"] = [{"call_key": "e" * 64, "ordered_lessons": []}]
    with pytest.raises(PacketShapeError, match="^NEUTRAL_PACKET_INVALID$"):
        encode_packet(**raw)


def test_zero_call_original_run_still_binds_measurement_identity() -> None:
    raw = inputs()
    raw["tasks"][0]["workflow_run_id"] = "a" * 32
    raw["measurements"] = [{
        "family": "WORKFLOW", "task": 306, "run_id": "b" * 32,
        "measurement_digest": "c" * 64,
        "baseline_digest": None, "evaluation_digest": None,
    }]
    with pytest.raises(PacketShapeError, match="^NEUTRAL_PACKET_INVALID$"):
        encode_packet(**raw)
