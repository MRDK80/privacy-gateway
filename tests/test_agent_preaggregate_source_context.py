"""Preaggregate factory refusals and pure closed request syntax, not READY proof."""

from typing import Any

import pytest
from tools import agent_epic_runtime_cli as guards
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_domain import preaggregate_source_scope_owned


def request() -> dict[str, Any]:
    return dict.fromkeys(
        ("issue", "acceptance_criteria", "base_sha", "head_sha", "repair_iteration",
         "gate_evidence", "reviewed_state", "diff", "contract"),
        None,
    )


@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_preaggregate_entry_refuses_unowned_session(unowned: Any) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        with preaggregate_source_scope_owned(unowned):
            raise AssertionError("unowned source factory admitted")


@pytest.mark.parametrize("extended", [False, True])
def test_request_syntax_keeps_legacy_or_exact_commitment(extended: bool) -> None:
    raw = request()
    if extended:
        raw["learning_source_digest"] = "a" * 64
    parsed, commitment = guards._final_preseed_request_variant(raw)
    assert parsed == raw
    assert commitment == ("a" * 64 if extended else None)
    # This pure grammar check deliberately does not admit native/source facts.
    assert parsed["issue"] is None


@pytest.mark.parametrize("bad", [None, True, "a" * 63, "A" * 64, "sha256:" + "a" * 64])
def test_request_commitment_rejects_wrong_raw_hash(bad: Any) -> None:
    with pytest.raises(guards.DeploymentError):
        guards._final_preseed_request_variant(
            {**request(), "learning_source_digest": bad}
        )


@pytest.mark.parametrize("extended", [False, True])
def test_request_variant_refuses_unknown_field(extended: bool) -> None:
    raw = request()
    if extended:
        raw["learning_source_digest"] = "a" * 64
    raw["source_complete"] = True
    with pytest.raises(guards.DeploymentError):
        guards._final_preseed_request_variant(raw)
