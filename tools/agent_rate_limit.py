"""Fail-closed Codex quota pause adapter for the private epic-loop checkpoint."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tools import agent_epic_loop as loop

QUOTA_ERROR_CODES = {"rateLimitExceeded", "usageLimitExceeded"}
WINDOWS = ("primary", "secondary")


def _exhausted(snapshot: Mapping[str, Any]) -> tuple[list[str], int | None, bool]:
    exhausted: list[str] = []
    resets: list[int] = []
    unknown_reset = False
    for name in WINDOWS:
        window = snapshot.get(name)
        if window is None:
            continue
        if not isinstance(window, Mapping):
            raise loop.LoopError("RATE_LIMIT_SNAPSHOT_INVALID")
        used = window.get("usedPercent")
        reset = window.get("resetsAt")
        if not isinstance(used, int) or isinstance(used, bool):
            raise loop.LoopError("RATE_LIMIT_SNAPSHOT_INVALID")
        if reset is not None and (
            not isinstance(reset, int) or isinstance(reset, bool)
        ):
            raise loop.LoopError("RATE_LIMIT_SNAPSHOT_INVALID")
        if used >= 100:
            exhausted.append(name)
            if reset is None:
                unknown_reset = True
            else:
                resets.append(reset)
    return (
        exhausted,
        (max(resets) if resets and not unknown_reset else None),
        unknown_reset,
    )


def pause(
    expected: Mapping[str, Any],
    *,
    live: Mapping[str, Any],
    store: loop.CheckpointStore,
    snapshot: Mapping[str, Any],
    error_code: str,
    now: int,
    max_poll_seconds: int = 300,
) -> loop.AdvanceResult:
    """Persist a quota pause without advancing or repeating the current phase."""
    try:
        if error_code not in QUOTA_ERROR_CODES:
            raise loop.LoopError("NOT_RATE_LIMIT_ERROR")
        wanted = loop.validate_checkpoint(expected)
        if any(live.get(key) != wanted[key] for key in loop.IDENTITY_KEYS):
            raise loop.LoopError("LIVE_IDENTITY_CHANGED")
        exhausted, resets_at, _unknown = _exhausted(snapshot)
        if not exhausted:
            raise loop.LoopError("RATE_LIMIT_NOT_EXHAUSTED")
        with loop.RunnerLock(store.directory):
            saved = store.load() or wanted
            if any(saved[key] != wanted[key] for key in loop.IDENTITY_KEYS):
                raise loop.LoopError("RESUME_IDENTITY_CHANGED")
            updated = dict(saved)
            updated["status"] = "PAUSED_RATE_LIMIT"
            updated["rate_limit_pause"] = {
                "exhausted_windows": exhausted,
                "resets_at": resets_at,
                "next_check_at": min(resets_at, now + max_poll_seconds)
                if resets_at is not None
                else None,
            }
            store.save(updated)
            return loop._result(
                "PAUSED_RATE_LIMIT", "RATE_LIMIT_PAUSED", updated, store
            )
    except loop.LoopError as error:
        fallback = store.load() or dict(expected)
        return loop._result("ESCALATE", error.machine_code, fallback, store)


def resume(
    expected: Mapping[str, Any],
    *,
    live: Mapping[str, Any],
    store: loop.CheckpointStore,
    snapshot: Mapping[str, Any],
    now: int,
    max_poll_seconds: int = 300,
) -> loop.AdvanceResult:
    """Recheck live identity and current quota before clearing a persisted pause."""
    try:
        wanted = loop.validate_checkpoint(expected)
        if any(live.get(key) != wanted[key] for key in loop.IDENTITY_KEYS):
            raise loop.LoopError("LIVE_IDENTITY_CHANGED")
        with loop.RunnerLock(store.directory):
            saved = store.load()
            if saved is None or saved["status"] != "PAUSED_RATE_LIMIT":
                raise loop.LoopError("RATE_LIMIT_PAUSE_NOT_FOUND")
            if any(saved[key] != wanted[key] for key in loop.IDENTITY_KEYS):
                raise loop.LoopError("RESUME_IDENTITY_CHANGED")
            exhausted, resets_at, _unknown = _exhausted(snapshot)
            if exhausted:
                updated = dict(saved)
                updated["rate_limit_pause"] = {
                    "exhausted_windows": exhausted,
                    "resets_at": resets_at,
                    "next_check_at": min(resets_at, now + max_poll_seconds)
                    if resets_at is not None
                    else None,
                }
                store.save(updated)
                return loop._result(
                    "PAUSED_RATE_LIMIT", "RATE_LIMIT_STILL_PAUSED", updated, store
                )
            updated = dict(saved)
            updated["status"] = "READY"
            updated["rate_limit_pause"] = None
            store.save(updated)
            return loop._result("CONTINUE", "RATE_LIMIT_RESUMED", updated, store)
    except loop.LoopError as error:
        fallback = store.load() or dict(expected)
        return loop._result("ESCALATE", error.machine_code, fallback, store)
