"""Create-only preparation boundary; installed source callbacks retain authority."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from tools import agent_epic_delivery as delivery
from tools import agent_epic_final_delivery as legacy
from tools import agent_final_preparation_records as records
from tools.agent_epic_loop import PhaseBlocked


class Ledger(Protocol):
    """Canonical private typed entries; implementation owns locks/caps/file safety."""

    def load(self) -> dict[str, dict[str, Any]]: ...

    def save(self, entries: dict[str, dict[str, Any]]) -> None: ...

    def assert_owned(self) -> None: ...


Binding = Callable[[Mapping[str, Any]], None]
Assessment = Callable[[Mapping[str, Any]], dict[str, Any]]
Effect = Callable[[legacy.Request], Mapping[str, object]]
Reconcile = Callable[[legacy.Request], tuple[str, Mapping[str, object] | None]]


def transport_request(value: Mapping[str, Any]) -> legacy.Request:
    """The preparation adapter cannot construct a merge/close/update operation."""
    return legacy.Request(
        operation_id=value["operation_id"],
        operation="create_roadmap_pr",
        repository=value["repository"],
        epic=value["epic"],
        roadmap_ref=value["roadmap_ref"],
        roadmap_sha=value["roadmap_sha"],
        policy_sha=value["policy_sha"],
        main_sha=value["main_sha"],
        pr=None,
    )


def receipt(raw: Mapping[str, object], value: Mapping[str, Any]) -> dict[str, object]:
    fields = {
        "pr",
        "repository",
        "base_ref",
        "head_ref",
        "main_sha",
        "roadmap_sha",
        "operation_id",
    }
    if set(raw) != fields:
        records.invalid()
    records.integer(raw["pr"], 2147483647, 1)
    expected = {
        "repository": value["repository"],
        "base_ref": "main",
        "head_ref": value["roadmap_ref"],
        "main_sha": value["main_sha"],
        "roadmap_sha": value["roadmap_sha"],
        "operation_id": value["operation_id"],
    }
    if any(raw[key] != item for key, item in expected.items()):
        records.invalid()
    return dict(raw)


def entries(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        records.invalid()
    result = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict) or set(entry) != {
            "request",
            "status",
            "receipt",
        }:
            records.invalid()
        request = records.request(entry["request"])
        if request["operation_id"] != key:
            records.invalid()
        status = entry["status"]
        if not isinstance(status, str) or status not in {
            "INTENT",
            "UNKNOWN",
            "APPLIED",
        }:
            records.invalid()
        if status == "APPLIED":
            if not isinstance(entry["receipt"], dict):
                records.invalid()
            receipt(entry["receipt"], request)
        elif entry["receipt"] is not None:
            records.invalid()
        result[key] = dict(entry)
    return result


def deliver(
    raw: Any,
    *,
    authority: legacy.Authority,
    binding: Binding,
    assessment: Assessment,
    ledger: Ledger,
    effect: Effect,
    reconcile: Reconcile,
) -> delivery.Result:
    """Callbacks are installed trusted code, never callback paths/model JSON.

    binding verifies original immutable launch/profile/namespace without granting
    writes. assessment independently rereads original exact preparation sources.
    Only a fresh original mandate authorizes a new external create effect.
    """
    try:
        value = records.request(raw)
        binding(value)
        ledger.assert_owned()
        current = entries(ledger.load())
    except (ValueError, PhaseBlocked):
        return delivery.Result("BLOCKED", "FINAL_APPROVAL_INVALID", None)
    key = value["operation_id"]
    request = transport_request(value)
    for other, entry in current.items():
        if other != key:
            # Reuse/drift is resolved by the installed preparation source reader.
            # This create path never creates a second PR around retained history.
            return delivery.Result("BLOCKED", "FINAL_RECEIPT_INVALID", None)
        if entry["request"] != value:
            return delivery.Result("BLOCKED", "OPERATION_ID_COLLISION", None)
    previous = current.get(key)
    if previous is not None:
        try:
            state, observed = reconcile(request)
            if state == "APPLIED" and observed is not None:
                found = receipt(observed, value)
                current[key] = {"request": value, "status": "APPLIED", "receipt": found}
                ledger.save(current)
                return delivery.Result("NO_OP", "ALREADY_APPLIED", found)
            if state != "NOT_APPLIED" or previous["status"] == "APPLIED":
                return delivery.Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
        except Exception:
            return delivery.Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
    # Reconciliation above is read-only; expiry/revoke still forbid new effects.
    for _ in range(2):
        code = legacy.authorization_code(request, authority)
        if code is not None:
            return delivery.Result("BLOCKED", code, None)
        try:
            binding(value)
            checked = records.assessment(assessment(value))
        except PhaseBlocked:
            return delivery.Result("BLOCKED", "FINAL_APPROVAL_INVALID", None)
        if checked["status"] != "READY":
            return delivery.Result("BLOCKED", checked["machine_code"], None)
        if checked["record_digest"] != value["preparation_digest"]:
            return delivery.Result("BLOCKED", "FINAL_APPROVAL_INVALID", None)
    current[key] = {"request": value, "status": "INTENT", "receipt": None}
    try:
        ledger.save(current)
        binding(value)
        checked = records.assessment(assessment(value))
    except (ValueError, PhaseBlocked):
        return delivery.Result("BLOCKED", "FINAL_APPROVAL_INVALID", None)
    if checked["status"] != "READY":
        return delivery.Result("BLOCKED", checked["machine_code"], None)
    if checked["record_digest"] != value["preparation_digest"]:
        return delivery.Result("BLOCKED", "FINAL_APPROVAL_INVALID", None)
    code = legacy.authorization_code(request, authority)
    if code is not None:
        return delivery.Result("BLOCKED", code, None)
    try:
        found = receipt(effect(request), value)
    except Exception:
        current[key] = {"request": value, "status": "UNKNOWN", "receipt": None}
        ledger.save(current)
        return delivery.Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
    current[key] = {"request": value, "status": "APPLIED", "receipt": found}
    ledger.save(current)
    return delivery.Result("APPLIED", "OK", found)
