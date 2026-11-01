"""Neutral projection compatibility, never fabricated successful source proof."""

from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_binding_source import (
    WorkflowAssociation,
    WorkflowCallFact,
    read_workflow_call_facts_owned,
)


@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_call_fact_entry_requires_original_completed_owned_session(
    unowned: Any,
) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        read_workflow_call_facts_owned(unowned)


@pytest.mark.parametrize("hinted", [False, True])
def test_neutral_projection_keeps_legacy_constructor_and_immutable_values(
    hinted: bool,
) -> None:
    # Synthetic representation values are deliberately NOT a verified call.
    references = (("a" * 64,) * 6,) if hinted else ()
    association = WorkflowAssociation(
        301, "b" * 64, "c" * 64, "d" * 64, "e" * 64, references
    )
    fact = WorkflowCallFact(association, "f" * 32, "executor", 1, "0" * 64)
    assert fact.association is association
    assert fact.association.references == references
    assert fact.metadata_digest != association.source_digest
    assert (fact.run_id, fact.role, fact.ordinal) == ("f" * 32, "executor", 1)
    with pytest.raises(FrozenInstanceError):
        setattr(fact, "role", "controller")
    with pytest.raises(FrozenInstanceError):
        setattr(association, "source_digest", "1" * 64)
    assert "f" * 32 not in repr(fact)
