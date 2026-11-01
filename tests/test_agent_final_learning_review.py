"""Pure request representation and refusal; no original-source success proof."""

from __future__ import annotations

import pytest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_epic_runtime_cli import DeploymentError
from tools.agent_final_preparation_review import learning_request_digest_fact
from tools.agent_final_preparation_runtime import (
    read_completed_learning_review_owned,
    review_learning_owned,
)


def original_request() -> dict[str, object]:
    return dict.fromkeys((
        "issue", "acceptance_criteria", "base_sha", "head_sha",
        "repair_iteration", "gate_evidence", "reviewed_state", "diff", "contract",
    ))


def test_original_nine_field_request_cannot_authorize_learning() -> None:
    with pytest.raises(PhaseBlocked):
        learning_request_digest_fact(original_request())


def test_closed_raw_digest_is_representation_only() -> None:
    request = {**original_request(), "learning_source_digest": "a" * 64}
    assert learning_request_digest_fact(request) == "a" * 64
    assert len(request) == 10


@pytest.mark.parametrize("value", [True, None, 3, "A" * 64, "a" * 63,
                                   "sha256:" + "a" * 64])
def test_invalid_digest_refuses(value: object) -> None:
    with pytest.raises(DeploymentError):
        learning_request_digest_fact({**original_request(),
                                      "learning_source_digest": value})


def test_unknown_field_refuses() -> None:
    with pytest.raises(DeploymentError):
        learning_request_digest_fact({**original_request(),
                                      "learning_source_digest": "a" * 64,
                                      "approved": True})


def test_missing_original_field_refuses() -> None:
    request = {**original_request(), "learning_source_digest": "a" * 64}
    del request["contract"]
    with pytest.raises(DeploymentError):
        learning_request_digest_fact(request)


def test_initial_unowned_input_refuses_before_collection() -> None:
    with pytest.raises(PhaseBlocked):
        review_learning_owned(object(), started_at=1, deadline_at=2)


def test_completed_unowned_input_refuses_before_collection() -> None:
    with pytest.raises(PhaseBlocked):
        read_completed_learning_review_owned(object())
