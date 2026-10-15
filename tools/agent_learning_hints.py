"""Closed untrusted hint envelopes never carry permissions or source authority."""

from __future__ import annotations

import json
from typing import Any

from tools import agent_learning as c
from tools.agent_continuation import digest


def envelope(raw: Any, owner: dict[str, Any], *, role: str) -> dict[str, Any]:
    owner = c.profile(owner)
    value = c.exact(
        raw, {"schema_version", "role", "catalog_digest", "selection_digest", "hints"}
    )
    if (
        value["schema_version"] != "1.0"
        or value["role"] != role
        or role not in c.ROLES
        or value["catalog_digest"] != owner["catalog_digest"]
    ):
        raise c.LearningError("LEARNING_HINT_INVALID")
    hints = value["hints"]
    if (
        not isinstance(hints, list)
        or len(hints) > owner["limits"]["max_hints_per_role"]
    ):
        raise c.LearningError("LEARNING_HINT_INVALID")
    lessons = set()
    for raw_hint in hints:
        hint = c.exact(
            raw_hint,
            {
                "lesson_id",
                "trust",
                "problem_class",
                "symptom_code",
                "diagnostic_steps",
                "minimal_fix",
                "applicability",
                "permissions",
            },
        )
        c.hash_value(hint["lesson_id"])
        c.identifier(hint["problem_class"])
        if (
            hint["trust"] != "UNTRUSTED_VERIFIED_HINT"
            or hint["permissions"] is not None
            or hint["lesson_id"] in lessons
        ):
            raise c.LearningError("LEARNING_HINT_INVALID")
        lessons.add(hint["lesson_id"])
        from tools.agent_learning_records import code

        code(hint["symptom_code"])
        steps = hint["diagnostic_steps"]
        if not isinstance(steps, list) or len(steps) > 4:
            raise c.LearningError("LEARNING_HINT_INVALID")
        for text in (*steps, hint["minimal_fix"], hint["applicability"]):
            c.text(text)
    if value["selection_digest"] != digest(hints):
        raise c.LearningError("LEARNING_HINT_INVALID")
    if (
        len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
        > owner["limits"]["max_hint_bytes"]
    ):
        raise c.LearningError("LEARNING_HINT_CAPACITY_EXHAUSTED")
    return value
