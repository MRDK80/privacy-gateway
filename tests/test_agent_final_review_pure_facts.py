"""Pure factoring tests; synthetic facts do not admit standalone sources."""

from types import SimpleNamespace
from typing import Any

import pytest
from tools import agent_final_preparation_records as records
from tools import agent_final_preparation_review as review
from tools import agent_orchestrate as workflow
from tools.agent_continuation import digest
from tools.agent_epic_loop import PhaseBlocked


class BoundaryReached(Exception):
    pass


def fail(*args: Any, **kwargs: Any) -> Any:
    raise BoundaryReached


def source() -> dict[str, Any]:
    return {
        "identity": {"roadmap_sha": "a" * 40},
        "request": {"gate_evidence": {}},
        "policy": {},
        "session_id": "synthetic",
        "output": {},
        "settings_source": {"receipt": {}, "terminal": {"adapter_exit_code": 0}},
    }


def wrapper(raw: dict[str, Any]) -> Any:
    return SimpleNamespace(
        _request=lambda *args: (raw["request"], raw["policy"], None),
        _settings_original=lambda invocation: raw["settings_source"],
        _backend_original=lambda: {},
        counter=SimpleNamespace(read=fail),
        _environment=fail,
    )


def test_shape_and_identity_refuse_before_request_reconstruction() -> None:
    raw = source()
    obj = wrapper(raw)
    obj._request = fail
    with pytest.raises(Exception) as malformed:
        review.AggregateReview._validate(obj, {}, raw["identity"])
    assert not isinstance(malformed.value, BoundaryReached)
    with pytest.raises(PhaseBlocked, match="FINAL_REVIEW_SOURCE_DRIFT"):
        review.AggregateReview._validate(obj, raw, {"roadmap_sha": "b" * 40})
    with pytest.raises(BoundaryReached):
        review.AggregateReview._validate(obj, raw, raw["identity"])


def test_review_fact_preserves_original_object() -> None:
    raw = source()
    assert review.review_record_fact(raw, raw["identity"]) is raw


def test_invalid_completion_precedes_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = source()
    obj = wrapper(raw)
    events = []

    def completion(*args: Any, **kwargs: Any) -> Any:
        events.append("completion")
        raise PhaseBlocked("SYNTHETIC_COMPLETION_INVALID")

    monkeypatch.setattr(review, "validate_completion", completion)
    with pytest.raises(PhaseBlocked, match="SYNTHETIC_COMPLETION_INVALID"):
        review.AggregateReview._validate(obj, raw, raw["identity"])
    assert events == ["completion"]


@pytest.mark.parametrize("changed", ["input_digest", "preparation_identity_digest"])
def test_reservation_drift_short_circuits_second_backend(
    monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    raw = source()
    obj = wrapper(raw)
    invocation = digest(
        {**raw["request"], "trusted_policy": {}, "session_id": "synthetic"}
    )
    reservation = {
        "status": "RESERVED",
        "input_digest": invocation,
        "preparation_identity_digest": digest(raw["identity"]),
        "backend_digest": digest({}),
    }
    reservation[changed] = "f" * 64
    obj.counter.read = lambda: ({}, reservation)
    calls = []

    def backend() -> dict[str, Any]:
        calls.append("backend")
        if len(calls) > 1:
            raise BoundaryReached
        return {}

    obj._backend_original = backend
    monkeypatch.setattr(review, "validate_completion", lambda *args, **kwargs: {})
    with pytest.raises(PhaseBlocked, match="FINAL_REVIEW_SOURCE_DRIFT"):
        review.AggregateReview._validate(obj, raw, raw["identity"])
    assert calls == ["backend"]


def test_completed_seal_uses_exact_validated_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = source()
    contract: Any = None
    reservation = {"status": "RESERVED", "input_digest": "c" * 64}
    receipt = {"synthetic": "receipt"}
    monkeypatch.setattr(
        workflow, "_validate_verdict", lambda *args: "PASS_WITH_NOTES"
    )
    result = review.completed_reservation_fact(
        raw, raw["identity"], contract, reservation, receipt
    )
    assert result == records.seal(
        {
            **reservation,
            "status": "COMPLETED",
            "settings_digest": digest(receipt),
            "output_digest": digest(raw["output"]),
        }
    )
    assert reservation["status"] == "RESERVED"
    monkeypatch.setattr(workflow, "_validate_verdict", lambda *args: "FAIL")
    with pytest.raises(PhaseBlocked, match="FINAL_DIFF_REVIEW_REQUIRED"):
        review.completed_reservation_fact(
            raw, raw["identity"], contract, reservation, receipt
        )


def test_environment_parse_precedes_counter() -> None:
    obj: Any = SimpleNamespace(
        counter=SimpleNamespace(
            identity={},
            read=fail,
            store=SimpleNamespace(
                assert_owned=lambda: None, owner={"max_artifact_bytes": 100}
            ),
        ),
        _store=lambda: SimpleNamespace(load=lambda *args, **kwargs: {}),
    )
    with pytest.raises(Exception) as malformed:
        review.AggregateReview._environment(obj, {}, {})
    assert not isinstance(malformed.value, BoundaryReached)


def test_environment_links_preserve_value_and_reject_null_or_drift() -> None:
    identity = {"roadmap_sha": "a" * 40}
    original = source()
    reservation = {"input_digest": "b" * 64}
    value = {
        "repository": "synthetic/repository",
        "preparation_identity_digest": digest(identity),
        "input_digest": reservation["input_digest"],
        "review_source_digest": digest(original),
    }
    counter_identity = {"repository": value["repository"]}
    assert (
        review.environment_links_fact(
            value, counter_identity, reservation, original, identity
        )
        is value
    )
    with pytest.raises(PhaseBlocked, match="FINAL_REVIEW_SOURCE_DRIFT"):
        review.environment_links_fact(value, counter_identity, None, original, identity)
    with pytest.raises(PhaseBlocked, match="FINAL_REVIEW_SOURCE_DRIFT"):
        review.environment_links_fact(
            {**value, "input_digest": "c" * 64},
            counter_identity,
            reservation,
            original,
            identity,
        )
