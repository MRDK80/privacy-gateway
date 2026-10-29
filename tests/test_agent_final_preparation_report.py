"""Publication strips private evidence and blocks incomplete or oversized reports."""

from __future__ import annotations

from typing import Any

import pytest
from tools import agent_final_preparation_records as records
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_preparation_report import prepare_report

from tests.test_agent_final_preparation_records import original


def inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    profile, candidate, assessment = (
        original(kind) for kind in ("profile", "candidate", "assessment")
    )
    candidate["preparation_digest"] = assessment["record_digest"]
    return profile, records.seal(candidate, self_digest="candidate_digest"), assessment


def test_public_wait_report_has_no_private_receipts_or_release_claim() -> None:
    value = prepare_report(
        *inputs(),
        child_numbers=(295, 296),
        learning_status="NONE_APPLIED",
        comparison_status="UNMEASURED",
    )
    assert set(value) == records.REPORT | {"schema_version", "record_digest"}
    assert value["status"] == "WAITING_OWNER_FINAL_APPROVAL"
    assert value["counts"] == {"total": 2, "closed": 2, "open": 0}


@pytest.mark.parametrize(
    "mutation",
    ["blocked", "unbound", "owner", "duplicates", "fake-measurement", "chars", "bytes"],
)
def test_cannot_publish_a_ready_report_from_conflicting_inputs(mutation: str) -> None:
    profile, candidate, assessment = inputs()
    children = (295, 296)
    comparison = "UNMEASURED"
    if mutation == "blocked":
        assessment.update(status="BLOCKED", machine_code="OPEN_REQUIRED_TASKS")
        assessment = records.seal(assessment)
        candidate["preparation_digest"] = assessment["record_digest"]
    elif mutation == "unbound":
        candidate["preparation_digest"] = "f" * 64
    elif mutation == "owner":
        candidate["owner_identity"] = "other-owner"
    elif mutation == "duplicates":
        children = (295, 295)
    elif mutation == "fake-measurement":
        comparison = "VERIFIED_COMPARABLE"
    else:
        profile["max_report_chars" if mutation == "chars" else "max_artifact_bytes"] = 1
    profile = records.seal(profile)
    candidate = records.seal(candidate, self_digest="candidate_digest")
    with pytest.raises(PhaseBlocked):
        prepare_report(
            profile,
            candidate,
            assessment,
            child_numbers=children,
            learning_status="NONE_APPLIED",
            comparison_status=comparison,
        )
