"""Synthetic quota source selection and strict window regressions (#300)."""

from __future__ import annotations

import pytest
from tools import agent_quota as quota


def window(used: int, reset: int | None = 2000) -> dict[str, object]:
    return {"usedPercent": used, "resetsAt": reset, "windowDurationMins": 300}


def bucket(used: int = 0) -> dict[str, object]:
    return {"limitId": "codex", "primary": window(used), "secondary": None}


def test_selected_bucket_and_both_windows() -> None:
    value = bucket(100)
    value["secondary"] = window(100, 9000)
    snapshot = quota.select({"rateLimitsByLimitId": {"codex": value}}, "codex")
    assert quota.exhausted(snapshot) == (["primary", "secondary"], 9000)


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"rateLimits": {}},
        {"rateLimitsByLimitId": {}},
        {"rateLimitsByLimitId": {"other": bucket()}},
        {"rateLimitsByLimitId": {"codex": {"primary": None}}},
    ],
)
def test_unknown_selected_quota_is_blocked(raw: dict[str, object]) -> None:
    with pytest.raises(quota.QuotaError):
        quota.select(raw, "codex")


def test_unknown_reset_does_not_prove_recovery() -> None:
    value = bucket(100)
    value["primary"] = window(100, None)
    snapshot = quota.select({"rateLimits": value}, "codex")
    assert quota.exhausted(snapshot) == (["primary"], None)


@pytest.mark.parametrize("used", [True, -1, 101, "0", None])
def test_invalid_used_percent(used: object) -> None:
    value = bucket()
    value["primary"] = {"usedPercent": used, "resetsAt": 2000}
    with pytest.raises(quota.QuotaError):
        quota.select({"rateLimits": value}, "codex")
