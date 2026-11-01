"""No parser or caller object grants fixed workflow-source admission."""

from typing import Any

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_learning_binding_source import read_workflow_associations_owned


@pytest.mark.parametrize("unowned", [None, object(), {}, True])
def test_binding_reader_requires_original_final_session(unowned: Any) -> None:
    with pytest.raises(PhaseBlocked, match="SESSION_SCOPE_CHANGED"):
        read_workflow_associations_owned(unowned)


def test_exact_current_run_join_rejects_zero_hint_orphan() -> None:
    from tools.agent_continuation import digest
    from tools.agent_final_learning_binding_source import _current_invocations
    from tools.agent_learning import LearningError

    owner = {"repository": "synthetic/repo", "epic": 294, "policy_sha": "1" * 40}
    identity = {
        **owner,
        "task": 301,
        "original_run_id": "2" * 32,
        "owner_profile_digest": digest(owner),
    }
    row = {
        "identity": identity,
        "role": "executor",
        "status": "COMPLETED",
        "invocation_key": "3" * 64,
        "lesson_ids": [],
    }
    origin = {
        "family": "WORKFLOW_STORE",
        "task": 301,
        "kind": "invocation",
        "record": row,
    }
    with pytest.raises(LearningError, match="LEARNING_SOURCE_MISMATCH"):
        _current_invocations([origin], 301, "2" * 32, [], owner)


def test_current_run_join_ignores_other_run_as_existing_policy() -> None:
    from tools.agent_final_learning_binding_source import _current_invocations

    row = {"identity": {"original_run_id": "4" * 32}}
    origin = {
        "family": "WORKFLOW_STORE",
        "task": 301,
        "kind": "invocation",
        "record": row,
    }
    owner = {"repository": "synthetic/repo", "epic": 294, "policy_sha": "1" * 40}
    _current_invocations([origin], 301, "2" * 32, [], owner)
