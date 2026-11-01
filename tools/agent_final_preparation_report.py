"""Bounded public wait report derived from closed preparation records."""

from __future__ import annotations

import json
from typing import Any

from tools import agent_final_preparation_records as records


def prepare_report(
    profile: dict[str, Any],
    candidate: dict[str, Any],
    assessment: dict[str, Any],
    *,
    child_numbers: tuple[int, ...],
    learning_status: str,
    comparison_status: str,
) -> dict[str, Any]:
    """Format verified inputs; this formatter cannot verify their source receipts."""
    owner = records.profile(profile)
    proposed = records.candidate(candidate)
    checked = records.assessment(assessment)
    if (
        checked["status"] != "READY"
        or proposed["preparation_digest"] != checked["record_digest"]
    ):
        records.invalid()
    for key in records.PROFILE & records.CANDIDATE:
        if proposed[key] != owner[key]:
            records.invalid()
    if not isinstance(child_numbers, tuple) or len(set(child_numbers)) != len(
        child_numbers
    ):
        records.invalid()
    for number in child_numbers:
        records.integer(number, 2147483647, 1)
    value = records.report(
        records.seal(
            {
                "schema_version": "1.0",
                "repository": proposed["repository"],
                "epic": proposed["epic"],
                "candidate_digest": proposed["candidate_digest"],
                "pr": proposed["pr"],
                "main_sha": proposed["main_sha"],
                "roadmap_sha": proposed["roadmap_sha"],
                "status": "WAITING_OWNER_FINAL_APPROVAL",
                "counts": {
                    "total": len(child_numbers),
                    "closed": len(child_numbers),
                    "open": 0,
                },
                "learning_status": learning_status,
                "comparison_status": comparison_status,
            }
        )
    )
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if (
        len(encoded) > owner["max_report_chars"]
        or len(encoded.encode("utf-8")) > owner["max_artifact_bytes"]
    ):
        records.invalid()
    return value
