"""Scalar carrier contract only; no original-source authority is fabricated."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, fields
from typing import Any

import pytest
from tools.agent_continuation import digest
from tools.agent_final_preparation_runtime import HistoricalPlanningUse
from tools.agent_learning_planning_sources import PlanningCallFact


def sample() -> PlanningCallFact:
    full_request = {"schema_version": "1.0", "payload": {"synthetic": 1}}
    preimage: dict[str, Any] = {
        "family": "PLANNING",
        "task": 301,
        "role": "planner",
        "ordinal": 1,
        "metadata_digest": "1" * 64,
        "problem_digest": "2" * 64,
        "relevance_digest": None,
        "request_digest": digest(full_request),
        "response_digest": "3" * 64,
        "context_digest": "4" * 64,
        "workspace_digest": "5" * 64,
        "validation_digest": "6" * 64,
    }
    values = {key: value for key, value in preimage.items() if key != "family"}
    return PlanningCallFact(
        **values, invocation_key="7" * 64, source_digest=digest(preimage)
    )


def test_exact_thirteen_scalar_fields_and_full_wrapper_preimage() -> None:
    fact = sample()
    assert [item.name for item in fields(fact)] == [
        "task",
        "role",
        "ordinal",
        "invocation_key",
        "metadata_digest",
        "problem_digest",
        "relevance_digest",
        "request_digest",
        "response_digest",
        "context_digest",
        "workspace_digest",
        "validation_digest",
        "source_digest",
    ]
    values = asdict(fact)
    assert all(type(value) in {str, int, type(None)} for value in values.values())
    preimage = {
        key: value
        for key, value in values.items()
        if key not in {"invocation_key", "source_digest"}
    }
    assert fact.source_digest == digest({"family": "PLANNING", **preimage})
    assert fact.request_digest != digest({"synthetic": 1})
    assert not hasattr(fact, "__dict__")
    assert "301" not in repr(fact) and "planner" not in repr(fact)
    with pytest.raises(FrozenInstanceError):
        setattr(fact, "task", 302)


def test_historical_ten_facts_and_references_remain_separate() -> None:
    fact = sample()
    value = HistoricalPlanningUse(
        301,
        "planner",
        1,
        "1" * 64,
        "2" * 64,
        "3" * 64,
        "4" * 64,
        None,
        (),
        (),
        (),
        fact,
    )
    assert len(value.facts()) == 10
    assert value.facts() == (
        301,
        "planner",
        1,
        "1" * 64,
        "2" * 64,
        "3" * 64,
        "4" * 64,
        None,
        (),
        (),
    )
    assert value.lesson_references() == ()
    assert value.call_fact() is fact
    with pytest.raises(AttributeError):
        setattr(value, "_call_fact", sample())
