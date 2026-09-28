"""Codex quota pause and resume tests (#254)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tools import agent_epic_loop as loop
from tools import agent_rate_limit as rate_limit


def _checkpoint() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "repository": "OWNER/repository",
        "epic": 248,
        "task": 254,
        "pr": 264,
        "base_ref": "roadmap/248-autonomous-epic-runner",
        "base_sha": "a" * 40,
        "head_ref": "feat/254-rate-limit-pause",
        "head_sha": "b" * 40,
        "phase": "RUN_TASK",
        "completed_phases": ["RUN_TASK"],
        "pending_phase": None,
        "merge_sha": None,
        "status": "RUNNING",
        "rate_limit_pause": None,
    }


def _live(value: dict[str, object]) -> dict[str, object]:
    return {key: value[key] for key in loop.IDENTITY_KEYS}


def _store(tmp_path: Path) -> loop.CheckpointStore:
    root = tmp_path / "repo"
    root.mkdir()
    return loop.CheckpointStore(tmp_path / "private", root)


@pytest.mark.parametrize("duration", [300, 10080])
def test_pauses_for_five_hour_and_weekly_quota(tmp_path: Path, duration: int) -> None:
    value = _checkpoint()
    store = _store(tmp_path)
    result = rate_limit.pause(
        value,
        live=_live(value),
        store=store,
        snapshot={
            "primary": {
                "usedPercent": 100,
                "windowDurationMins": duration,
                "resetsAt": 2000,
            }
        },
        error_code="rateLimitExceeded",
        now=1000,
    )
    assert result.status == "PAUSED_RATE_LIMIT"
    assert store.load()["rate_limit_pause"]["next_check_at"] == 1300  # type: ignore[index]


def test_both_quotas_use_latest_reset(tmp_path: Path) -> None:
    value = _checkpoint()
    store = _store(tmp_path)
    rate_limit.pause(
        value,
        live=_live(value),
        store=store,
        snapshot={
            "primary": {"usedPercent": 100, "resetsAt": 1500},
            "secondary": {"usedPercent": 100, "resetsAt": 9000},
        },
        error_code="usageLimitExceeded",
        now=1000,
    )
    assert store.load()["rate_limit_pause"]["resets_at"] == 9000  # type: ignore[index]


def test_unknown_reset_is_not_invented_and_survives_restart(tmp_path: Path) -> None:
    value = _checkpoint()
    store = _store(tmp_path)
    rate_limit.pause(
        value,
        live=_live(value),
        store=store,
        snapshot={"primary": {"usedPercent": 100, "resetsAt": None}},
        error_code="rateLimitExceeded",
        now=1000,
    )
    reopened = loop.CheckpointStore(store.directory, tmp_path / "repo")
    pause = reopened.load()["rate_limit_pause"]  # type: ignore[index]
    assert pause == {
        "exhausted_windows": ["primary"],
        "resets_at": None,
        "next_check_at": None,
    }


def test_resume_rechecks_quota_and_live_sha(tmp_path: Path) -> None:
    value = _checkpoint()
    store = _store(tmp_path)
    rate_limit.pause(
        value,
        live=_live(value),
        store=store,
        snapshot={"primary": {"usedPercent": 100, "resetsAt": 1100}},
        error_code="rateLimitExceeded",
        now=1000,
    )
    expected = store.load()
    assert expected is not None
    changed = _live(value)
    changed["head_sha"] = "c" * 40
    blocked = rate_limit.resume(
        expected,
        live=changed,
        store=store,
        snapshot={"primary": {"usedPercent": 0, "resetsAt": 2000}},
        now=1200,
    )
    resumed = rate_limit.resume(
        expected,
        live=_live(value),
        store=store,
        snapshot={"primary": {"usedPercent": 0, "resetsAt": 2000}},
        now=1200,
    )
    assert blocked.machine_code == "LIVE_IDENTITY_CHANGED"
    assert resumed.machine_code == "RATE_LIMIT_RESUMED"
    assert store.load()["phase"] == "RUN_TASK"  # type: ignore[index]


@pytest.mark.parametrize(
    "error", ["networkError", "authenticationError", "serverOverloaded"]
)
def test_non_quota_errors_never_pause(tmp_path: Path, error: str) -> None:
    value = _checkpoint()
    store = _store(tmp_path)
    result = rate_limit.pause(
        value,
        live=_live(value),
        store=store,
        snapshot={"primary": {"usedPercent": 100, "resetsAt": 2000}},
        error_code=error,
        now=1000,
    )
    assert result.machine_code == "NOT_RATE_LIMIT_ERROR"
    assert store.load() is None
