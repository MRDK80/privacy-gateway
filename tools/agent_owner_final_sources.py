"""Closed owner source shapes; validation does not establish human authority.

The fixed installed reader must separately prove installation, namespace, current
candidate/CI and active authority under the admitted external owner boundary.
These pure validators do not read, install or create owner artifacts.
"""

from __future__ import annotations

import re
from typing import Any

from tools import agent_final_preparation_records as records

_COMMON = records.IDENTITY | {
    "owner_identity",
    "candidate_digest",
    "pr",
    "main_sha",
    "roadmap_sha",
    "policy_sha",
    "pr_ci_digest",
    "kind",
    "procedure",
}
_COMMAND = _COMMON | {"approval_digest", "final_demo_digest", "approved_at"}
_DEMO = _COMMON | {"started_at", "completed_at", "scenarios", "observations"}


def _text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        records.invalid()
    return str(value)


def _scenario_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value) is None
    ):
        records.invalid()
    return str(value)


def _common(raw: Any, fields: set[str], kind: str, procedure: str) -> dict[str, Any]:
    value = records._sealed(raw, fields)
    if value["kind"] != kind or value["procedure"] != procedure:
        records.invalid()
    for key in ("candidate_digest", "pr_ci_digest"):
        records.hash_value(value[key])
    return value


def command(raw: Any) -> dict[str, Any]:
    """Command1 shape and self digest only; not proof of human invocation."""
    value = _common(
        raw,
        _COMMAND,
        "OWNER_FINAL_CANDIDATE_APPROVAL",
        "ADR303_FINAL_OWNER_APPROVAL_V1",
    )
    records.integer(value["approved_at"])
    for key in ("approval_digest", "final_demo_digest"):
        records.hash_value(value[key])
    return value


def demo(raw: Any) -> dict[str, Any]:
    """DemoSource1 bounded original prose, not verification of physical events."""
    value = _common(
        raw, _DEMO, "OWNER_REAL_FINAL_DEMO", "ADR303_OWNER_REAL_FINAL_DEMO_V1"
    )
    started = records.integer(value["started_at"])
    completed = records.integer(value["completed_at"])
    if started > completed:
        records.invalid()
    scenarios, observations = value["scenarios"], value["observations"]
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 16:
        records.invalid()
    if not isinstance(observations, list) or len(observations) != len(scenarios):
        records.invalid()
    ids: set[str] = set()
    for item in scenarios:
        if not isinstance(item, dict) or set(item) != {
            "id",
            "surface",
            "steps",
            "expected",
        }:
            records.invalid()
        name = _scenario_id(item["id"])
        if name in ids:
            records.invalid()
        ids.add(name)
        surface, steps = item["surface"], item["steps"]
        if not isinstance(surface, str) or surface not in {
            "CLI",
            "LIBRARY",
            "UI",
            "LOCAL_WORKFLOW",
        }:
            records.invalid()
        if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
            records.invalid()
        for step in steps:
            _text(step, 512)
        _text(item["expected"], 1024)
    observed: set[str] = set()
    for item in observations:
        if not isinstance(item, dict) or set(item) != {
            "id",
            "observed_at",
            "outcome",
            "observation",
        }:
            records.invalid()
        name = _scenario_id(item["id"])
        if name not in ids or name in observed:
            records.invalid()
        observed.add(name)
        at = records.integer(item["observed_at"])
        if (
            not started <= at <= completed
            or item["outcome"] != "OWNER_OBSERVED_EXPECTED"
        ):
            records.invalid()
        _text(item["observation"], 2048)
    if observed != ids:
        records.invalid()
    return value
