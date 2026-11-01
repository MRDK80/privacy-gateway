"""Neutral Settings relations and refused ownership; no successful fake sources."""
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_settings import (
    _expected_settings,
    _match_source,
    _settings_call_source,
    _SettingsCallSource,
    read_preaggregate_settings_commitment_owned,
    read_settings_commitment_owned,
)
from tools.agent_learning import LearningError


def source(task: int = 301, native: str = "a" * 64) -> _SettingsCallSource:
    return _settings_call_source(
        task, "b" * 32, "executor", 1, native,
        {"receipt": {"synthetic": 1}, "terminal": {"synthetic": 2}},
    )


def test_private_source_is_canonical_immutable_and_not_in_repr() -> None:
    value = source()
    _match_source(value, {"terminal": {"synthetic": 2}, "receipt": {"synthetic": 1}})
    assert "synthetic" not in repr(value)
    assert "receipt" not in repr(value)
    with pytest.raises(FrozenInstanceError):
        setattr(value, "task", 302)


def test_full_object_difference_refuses_despite_same_native() -> None:
    with pytest.raises(LearningError):
        _match_source(source(), {"receipt": {"synthetic": 9},
                                 "terminal": {"synthetic": 2}})


def test_zero_call_lifecycle_is_unsupported() -> None:
    with pytest.raises(LearningError):
        _expected_settings(())


@pytest.mark.parametrize("other_task", [301, 302])
def test_duplicate_native_is_never_deduplicated(other_task: int) -> None:
    with pytest.raises(LearningError):
        _expected_settings((source(), source(other_task)))


def test_duplicate_call_position_with_different_native_refuses() -> None:
    with pytest.raises(LearningError):
        _expected_settings((source(), source(native="c" * 64)))


def test_both_roles_and_zero_hint_sources_remain_in_expected_map() -> None:
    first = source()
    second = _settings_call_source(
        301, "b" * 32, "controller", 1, "c" * 64,
        {"receipt": {"synthetic": 3}, "terminal": {"synthetic": 4}},
    )
    expected = _expected_settings((first, second))
    assert set(expected) == {"receipt-" + "a" * 64 + ".json",
                             "receipt-" + "c" * 64 + ".json"}
    assert tuple(expected.values()) == (first, second)


@pytest.mark.parametrize("entry", [read_settings_commitment_owned,
                                   read_preaggregate_settings_commitment_owned])
@pytest.mark.parametrize("unowned", [None, object(), {}, 1])
def test_unowned_entry_refuses(entry: Any, unowned: Any) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        entry(unowned)
