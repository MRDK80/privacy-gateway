"""Closed prospective association records do not confer source authority."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from tools import agent_learning as core
from tools import agent_learning_binding_records as bindings
from tools import agent_learning_records as records
from tools.agent_continuation import digest

from tests.test_agent_learning_records import invocation


def original(*, completed: bool = False) -> dict[str, Any]:
    metadata = invocation()
    metadata["identity"]["original_run_id"] = "a" * 32
    metadata["invocation_key"] = records.invocation_id(metadata)
    metadata = records.invocation(core.seal(metadata))
    return core.seal(
        {
            "schema_version": "1.0",
            "identity": metadata["identity"],
            "role": metadata["role"],
            "ordinal": metadata["ordinal"],
            "repair_iteration": metadata["repair_iteration"],
            "invocation_key": metadata["invocation_key"],
            "stable_metadata_digest": bindings.stable_metadata(metadata),
            "native_invocation_digest": "1" * 64,
            "native_reservation_digest": "2" * 64,
            "checkpoint_digest": "3" * 64,
            "ordered_lessons": [],
            "selection_digest": digest([]),
            "input_digest": metadata["input_digest"],
            "prompt_digest": metadata["prompt_digest"],
            "status": "COMPLETED" if completed else "RESERVED",
            "completion": {key: "4" * 64 for key in bindings.COMPLETION}
            if completed
            else None,
        }
    )


@pytest.mark.parametrize("completed", [False, True])
def test_closed_record_and_duplicate_key_safe_decode(completed: bool) -> None:
    value = original(completed=completed)
    saved = copy.deepcopy(value)
    assert bindings.binding(value, max_repairs=2, max_hints=3) == saved
    raw = json.dumps(value).encode()
    assert bindings.decode(raw, max_bytes=65536, max_repairs=2, max_hints=3) == saved
    assert value == saved
    duplicate = raw[:-1] + b',"status":"RESERVED"}'
    with pytest.raises(core.LearningError):
        bindings.decode(duplicate, max_bytes=65536, max_repairs=2, max_hints=3)


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", []),
        ("role", "planner"),
        ("ordinal", True),
        ("ordinal", 1.0),
        ("repair_iteration", 3),
        ("native_invocation_digest", "sha256:" + "1" * 64),
        ("status", "UNKNOWN"),
        ("completion", {}),
        ("invocation_key", "f" * 64),
        ("ordered_lessons", [{"lesson_id": "1" * 64}]),
    ],
)
def test_resealed_unknown_or_invalid_fields_refuse(field: str, value: Any) -> None:
    item = original()
    item[field] = value
    with pytest.raises(core.LearningError):
        bindings.binding(core.seal(item), max_repairs=2, max_hints=3)


@pytest.mark.parametrize(
    "attack", ["extra", "missing", "nonce", "float-bound", "byte-cap"]
)
def test_original_identity_closed_shape_and_caps(attack: str) -> None:
    item = original()
    if attack == "extra":
        item["ready"] = True
    elif attack == "missing":
        item.pop("checkpoint_digest")
    elif attack == "nonce":
        item["identity"]["original_run_id"] = "a" * 24
        item["invocation_key"] = records.invocation_id(item)
    if attack == "float-bound":
        with pytest.raises(core.LearningError):
            bindings.binding(item, max_repairs=True, max_hints=3)
    elif attack == "byte-cap":
        with pytest.raises(core.LearningError):
            bindings.decode(
                json.dumps(item).encode(), max_bytes=1, max_repairs=2, max_hints=3
            )
    else:
        with pytest.raises(core.LearningError):
            bindings.binding(core.seal(item), max_repairs=2, max_hints=3)


def test_only_status_and_sealing_change_stable_metadata() -> None:
    value = invocation()
    reserved = core.seal({**value, "status": "RESERVED"})
    complete = core.seal({**value, "status": "COMPLETED"})
    assert bindings.stable_metadata(reserved) == bindings.stable_metadata(complete)
    assert complete["input_digest"] == "f" * 64
    changed = core.seal({**complete, "input_digest": "e" * 64})
    assert changed["input_digest"] != complete["input_digest"]
    assert bindings.stable_metadata(changed) != bindings.stable_metadata(complete)


@pytest.mark.parametrize("count,cap", [(1, 0), (4, 3)])
def test_ordered_references_cannot_exceed_original_hint_cap(
    count: int, cap: int
) -> None:
    value = original()
    value["ordered_lessons"] = [
        {key: str(index + 1) * 64 for key in bindings.REFERENCE}
        for index in range(count)
    ]
    with pytest.raises(core.LearningError):
        bindings.binding(core.seal(value), max_repairs=2, max_hints=cap)


@pytest.mark.parametrize("cap", [0, 3])
def test_same_pass_reference_projection_keeps_pair_order_and_zero_branch(
    monkeypatch: pytest.MonkeyPatch,
    cap: int,
) -> None:
    from tests.test_agent_learning_verify import paired_verifier

    value, before, after, pinned = paired_verifier(monkeypatch)
    proof = value.pair(before, after, pinned)
    assert proof is not None
    candidate = {"rule_id": pinned["rule_id"], "record_digest": "6" * 64}
    verified = {
        "lesson_id": "4" * 64,
        "catalog_digest": value.reader.owner["catalog_digest"],
        "candidate_digest": digest(candidate),
        "observation_digest": digest(after),
        "record_digest": "5" * 64,
        "evidence_digest": proof["evidence_digest"],
        "applicability_digest": proof["applicability_digest"],
    }
    retained = [{"kind": "observation", "record": item} for item in (before, after)]
    retained.extend(
        [
            {"kind": "candidate", "record": candidate},
            {"kind": "verification", "record": verified},
        ]
    )
    calls = []
    original_pair = value.pair

    def pair(first: dict[str, Any], last: dict[str, Any], rule: dict[str, Any]) -> Any:
        calls.append((digest(first), digest(last)))
        return original_pair(first, last, rule)

    monkeypatch.setattr(value, "pair", pair)
    value.reader.owner["limits"]["max_hints_per_role"] = cap
    # Pure projection unit; this synthetic reader is never a binding authority.
    envelope, references = value._selection_original(
        "runtime", "executor", "typing", retained
    )
    assert calls == [(digest(before), digest(after))]
    assert envelope["selection_digest"] == digest(envelope["hints"])
    if cap == 0:
        assert envelope["hints"] == references == []
    else:
        assert references == [
            {
                "lesson_id": verified["lesson_id"],
                "verification_digest": digest(verified),
                "candidate_digest": digest(candidate),
                "before_digest": digest(before),
                "after_digest": digest(after),
                "pair_digest": digest(proof),
            }
        ]
        assert [item["lesson_id"] for item in envelope["hints"]] == [
            item["lesson_id"] for item in references
        ]
