#!/usr/bin/env python3
"""Assess the SHA-bound final gate for one delegated epic."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tools.agent_coordinator_delivery import REQUIRED_CHECKS as REQUIRED_CHECKS

SHA_RE = re.compile(r"[0-9a-f]{40}")
LOCAL_GATE = frozenset(
    {"pytest -q", "ruff check .", "mypy .", "pre-commit run --all-files"}
)


@dataclass(frozen=True)
class FinalGate:
    status: str
    machine_code: str
    roadmap_status: str


def _checks_success(checks: Sequence[Mapping[str, object]]) -> bool:
    states: dict[str, object] = {}
    for item in checks:
        name = item.get("name")
        if not isinstance(name, str) or name in states:
            return False
        states[name] = item.get("state")
    return set(states) == set(REQUIRED_CHECKS) and set(states.values()) == {"SUCCESS"}


def assess_final_gate(
    *,
    epic: int,
    roadmap_ref: str,
    roadmap_sha: str,
    live_roadmap_sha: str,
    trusted_main_sha: str,
    live_main_sha: str,
    open_children: Sequence[int],
    local_gate: Mapping[str, bool],
    diff_reviewed: bool,
    prompt_lesson_verified: bool,
    prompt_injection_tests_passed: bool,
    demo_confirmed_by_owner: bool,
) -> FinalGate:
    """Return release readiness only for one current, completely verified SHA."""
    if (
        epic <= 0
        or not roadmap_ref.startswith(f"roadmap/{epic}-")
        or any(
            SHA_RE.fullmatch(value) is None
            for value in (
                roadmap_sha,
                live_roadmap_sha,
                trusted_main_sha,
                live_main_sha,
            )
        )
    ):
        return FinalGate("BLOCKED", "IDENTITY_INVALID", "BLOCKED")
    if roadmap_sha != live_roadmap_sha:
        return FinalGate("BLOCKED", "ROADMAP_SHA_CHANGED", "BLOCKED")
    if trusted_main_sha != live_main_sha:
        return FinalGate("BLOCKED", "MAIN_CHANGED", "BLOCKED")
    if open_children:
        return FinalGate("BLOCKED", "OPEN_REQUIRED_TASKS", "BLOCKED")
    if set(local_gate) != set(LOCAL_GATE) or not all(local_gate.values()):
        return FinalGate("BLOCKED", "LOCAL_GATE_FAILED", "BLOCKED")
    if not diff_reviewed:
        return FinalGate("BLOCKED", "DIFF_REVIEW_REQUIRED", "BLOCKED")
    if not prompt_lesson_verified or not prompt_injection_tests_passed:
        return FinalGate("BLOCKED", "PROMPT_LESSON_GATE_FAILED", "BLOCKED")
    if not demo_confirmed_by_owner:
        return FinalGate("BLOCKED", "FINAL_DEMO_UNCONFIRMED", "BLOCKED")
    return FinalGate("PASS", "OK", "ROADMAP READY FOR RELEASE")


def assess_roadmap_pr(
    gate: FinalGate,
    *,
    expected_roadmap_sha: str,
    pr_head_sha: str,
    base_ref: str,
    state: str,
    checks: Sequence[Mapping[str, object]],
) -> FinalGate:
    """Require exact PR identity and five successful checks before merge."""
    if gate.status != "PASS":
        return gate
    if (
        expected_roadmap_sha != pr_head_sha
        or base_ref != "main"
        or state != "OPEN"
    ):
        return FinalGate("BLOCKED", "ROADMAP_PR_IDENTITY_CHANGED", "BLOCKED")
    if not _checks_success(checks):
        return FinalGate("BLOCKED", "ROADMAP_PR_CI_FAILED", "BLOCKED")
    return gate


def assess_main_post_merge(
    *,
    merge_sha: str,
    live_main_sha: str,
    pr_state: str,
    checks: Sequence[Mapping[str, object]],
) -> FinalGate:
    """Declare ROADMAP DONE only from the new main SHA's own CI evidence."""
    if (
        SHA_RE.fullmatch(merge_sha) is None
        or merge_sha != live_main_sha
        or pr_state != "MERGED"
    ):
        return FinalGate("BLOCKED", "MAIN_MERGE_IDENTITY_CHANGED", "BLOCKED")
    if not _checks_success(checks):
        return FinalGate("BLOCKED", "MAIN_POST_MERGE_CI_FAILED", "BLOCKED")
    return FinalGate("PASS", "OK", "ROADMAP DONE")
