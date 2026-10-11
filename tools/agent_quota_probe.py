"""Closed owner-pinned host observations; synthetic evidence cannot yield READY."""

from __future__ import annotations

from typing import Any

from tools.agent_epic_runtime_cli import (
    QUOTA_PROBE_KEYS,
    DeploymentError,
    quota_probe_document,
)
from tools.agent_quota import QuotaError

KEYS = QUOTA_PROBE_KEYS


def validate(value: Any) -> dict[str, Any]:
    try:
        return quota_probe_document(value)
    except DeploymentError as error:
        raise QuotaError(str(error)) from None


def require_host(
    value: Any,
    *,
    expected_digest: str | None,
    bindings: dict[str, str],
) -> dict[str, Any]:
    if expected_digest is None:
        raise QuotaError("QUOTA_WAKE_PROBE_REQUIRED")
    checked = validate(value)
    if checked["kind"] != "host":
        raise QuotaError("QUOTA_WAKE_PROBE_SYNTHETIC")
    if checked["probe_digest"] != expected_digest or any(
        checked.get(key) != item for key, item in bindings.items()
    ):
        raise QuotaError("QUOTA_WAKE_PROBE_BINDING_CHANGED")
    return checked
