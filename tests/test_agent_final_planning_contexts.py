"""Fixed planning entry refusal and neutral carrier compatibility only."""

from typing import Any

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_domain import (
    read_final_planning_uses_owned,
    read_preaggregate_planning_uses_owned,
)
from tools.agent_final_preparation_runtime import HistoricalPlanningUse
from tools.agent_learning_planning_sources import PlanningCallFact


@pytest.mark.parametrize(
    "entry", [read_final_planning_uses_owned, read_preaggregate_planning_uses_owned]
)
@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_fixed_planning_entries_refuse_unowned_session(
    entry: Any, unowned: Any
) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        entry(unowned)


@pytest.mark.parametrize("hinted", [False, True])
def test_neutral_carrier_keeps_facts_ten_and_ordered_six_references(
    hinted: bool,
) -> None:
    # No successful Session/source provenance is manufactured by constructors.
    references = (("a" * 64,) * 6, ("b" * 64,) * 6) if hinted else ()
    call = PlanningCallFact(
        301, "planner", 1, "0" * 64, "1" * 64, "2" * 64, None,
        "3" * 64, "4" * 64, "5" * 64, "6" * 64, "7" * 64, "8" * 64,
    )
    lessons = tuple(row[0] for row in references)
    value = HistoricalPlanningUse(
        301, "planner", 1, "1" * 64, "2" * 64, "3" * 64, "4" * 64,
        None, lessons, (), references, call,
    )
    assert value.facts() == (
        301, "planner", 1, "1" * 64, "2" * 64, "3" * 64, "4" * 64,
        None, lessons, (),
    )
    assert value.lesson_references() == references
    assert value.call_fact() is call
    assert call.relevance_digest is None
    with pytest.raises(AttributeError):
        setattr(value, "_lesson_references", ())
