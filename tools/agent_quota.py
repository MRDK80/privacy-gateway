"""Private read-only quota selection; snapshots never grant execution authority."""

from __future__ import annotations

import re
from typing import Any

from tools import agent_epic_loop as loop


class QuotaError(loop.PhaseBlocked):
    """Fixed redacted quota failure."""


def _window(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QuotaError("QUOTA_UNKNOWN")
    used, reset = value.get("usedPercent"), value.get("resetsAt")
    if (
        type(used) is not int
        or not 0 <= used <= 100
        or "resetsAt" not in value
        or (
            reset is not None
            and (type(reset) is not int or not 0 <= reset <= 253402300799)
        )
    ):
        raise QuotaError("QUOTA_UNKNOWN")
    duration = value.get("windowDurationMins")
    if duration is not None and (
        type(duration) is not int or not 1 <= duration <= 52560000
    ):
        raise QuotaError("QUOTA_UNKNOWN")
    return {"usedPercent": used, "resetsAt": reset}


def select(response: Any, limit_id: str) -> dict[str, Any]:
    """Select exactly the approved bucket; redact account/credit metadata."""
    if (
        not isinstance(response, dict)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", limit_id) is None
    ):
        raise QuotaError("QUOTA_UNKNOWN")
    if (
        "rateLimitsByLimitId" in response
        and response["rateLimitsByLimitId"] is not None
    ):
        buckets = response["rateLimitsByLimitId"]
        if not isinstance(buckets, dict) or limit_id not in buckets:
            raise QuotaError("QUOTA_UNKNOWN")
        value = buckets[limit_id]
    else:
        value = response.get("rateLimits")
        if not isinstance(value, dict) or value.get("limitId") != limit_id:
            raise QuotaError("QUOTA_UNKNOWN")
    if not isinstance(value, dict) or value.get("limitId", limit_id) != limit_id:
        raise QuotaError("QUOTA_UNKNOWN")
    primary = _window(value.get("primary"))
    if "secondary" not in value:
        raise QuotaError("QUOTA_UNKNOWN")
    secondary = None if value["secondary"] is None else _window(value["secondary"])
    return {"primary": primary, "secondary": secondary}


def exhausted(snapshot: dict[str, Any]) -> tuple[list[str], int | None]:
    names: list[str] = []
    resets: list[int] = []
    unknown = False
    for name in ("primary", "secondary"):
        value = snapshot[name]
        if value is not None and value["usedPercent"] >= 100:
            names.append(name)
            if value["resetsAt"] is None:
                unknown = True
            else:
                resets.append(value["resetsAt"])
    return names, max(resets) if resets and not unknown else None


def profile(value: Any) -> dict[str, Any]:
    """Share the stdlib pre-import policy validator with frozen readiness."""
    from tools.agent_epic_runtime_cli import DeploymentError, quota_profile_document

    try:
        return quota_profile_document(value)
    except DeploymentError as error:
        raise QuotaError(str(error)) from None
