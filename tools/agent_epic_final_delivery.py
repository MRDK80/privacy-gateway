"""Separate mandate-checked, reconciliation-first final roadmap driver."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, cast

from tools import agent_epic_delivery as task_delivery
from tools import agent_epic_final as final
from tools.agent_epic_handover import MANDATE_FIELDS

OPERATIONS = {"create_roadmap_pr", "merge_roadmap_pr", "close_epic", "update_epic"}
POST_MERGE = {"close_epic", "update_epic"}


@dataclass(frozen=True)
class Request:
    operation_id: str
    operation: str
    repository: str
    epic: int
    roadmap_ref: str
    roadmap_sha: str
    policy_sha: str
    main_sha: str
    pr: int | None
    merge_sha: str | None = None
    schema_version: str = "1.0"


Authority = Callable[[], tuple[Mapping[str, Any], str, task_delivery.MandateContext]]
Gate = Callable[[Request], final.FinalGate]
Effect = Callable[[Request], Mapping[str, object]]
Reconcile = Callable[[Request], tuple[str, Mapping[str, object] | None]]


def valid_request(request: Request) -> bool:
    return bool(
        request.schema_version == "1.0"
        and request.operation in OPERATIONS
        and request.operation_id
        and request.repository.count("/") == 1
        and type(request.epic) is int
        and request.epic > 0
        and request.roadmap_ref.startswith(f"roadmap/{request.epic}-")
        and all(
            task_delivery.SHA_RE.fullmatch(value) is not None
            for value in (request.roadmap_sha, request.policy_sha, request.main_sha)
        )
        and (
            request.pr is None
            if request.operation == "create_roadmap_pr"
            else type(request.pr) is int and request.pr > 0
        )
        and ((request.operation in POST_MERGE) == (request.merge_sha is not None))
        and (
            request.merge_sha is None
            or task_delivery.SHA_RE.fullmatch(request.merge_sha) is not None
        )
    )


def authorization_code(request: Request, authority: Authority) -> str | None:
    mandate, approved, context = authority()
    if set(mandate) != MANDATE_FIELDS or mandate.get("schema_version") != "3.0":
        return "MANDATE_SCHEMA_UNSUPPORTED"
    lifecycle = task_delivery.mandate_lifecycle_code(
        mandate, context, at_task_start=False
    )
    if lifecycle is not None:
        return lifecycle
    approval = mandate.get("approval")
    if (
        task_delivery.DIGEST_RE.fullmatch(approved) is None
        or task_delivery.mandate_digest(mandate) != approved
        or not isinstance(approval, Mapping)
        or approval.get("mandate_digest") != approved
    ):
        return "MANDATE_APPROVAL_MISMATCH"
    if any(
        mandate.get(key) != expected
        for key, expected in {
            "repository": request.repository,
            "epic": request.epic,
            "roadmap_ref": request.roadmap_ref,
            "policy_sha": request.policy_sha,
        }.items()
    ):
        return "MANDATE_IDENTITY_MISMATCH"
    operations = mandate.get("operations")
    if not isinstance(operations, list) or request.operation not in operations:
        return "MANDATE_AUTHORITY_MISSING"
    return None


def _entry(
    request: Request, status: str, receipt: Mapping[str, object] | None
) -> dict[str, object]:
    return {
        "operation": request.operation,
        "request": asdict(request),
        "status": status,
        "receipt": dict(receipt) if receipt else None,
    }


def deliver(
    request: Request,
    *,
    authority: Authority,
    gate: Gate,
    ledger: task_delivery.Ledger,
    effect: Effect,
    reconcile: Reconcile,
) -> task_delivery.Result:
    """The gate provider is trusted infrastructure, never model/request data."""
    if not valid_request(request):
        return task_delivery.Result("BLOCKED", "REQUEST_INVALID", None)
    code = authorization_code(request, authority)
    if code is not None:
        return task_delivery.Result("BLOCKED", code, None)
    try:
        entries = ledger.load()
    except ValueError:
        return task_delivery.Result("BLOCKED", "LEDGER_INVALID", None)
    previous = entries.get(request.operation_id)
    if previous is not None:
        if previous.get("request") != asdict(request):
            return task_delivery.Result("BLOCKED", "OPERATION_ID_COLLISION", None)
        if previous["status"] == "APPLIED":
            return task_delivery.Result(
                "NO_OP", "ALREADY_APPLIED", cast(dict[str, object], previous["receipt"])
            )
        if previous["status"] in {"INTENT", "UNKNOWN"}:
            try:
                state, receipt = reconcile(request)
            except Exception:
                return task_delivery.Result(
                    "ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None
                )
            if state == "APPLIED" and receipt:
                entries[request.operation_id] = _entry(request, "APPLIED", receipt)
                ledger.save(entries)
                return task_delivery.Result("NO_OP", "ALREADY_APPLIED", dict(receipt))
            if state != "NOT_APPLIED":
                return task_delivery.Result(
                    "ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None
                )
    if request.operation == "update_epic":
        expected = asdict(request) | {"operation": "close_epic"}
        expected.pop("operation_id")
        closed = any(
            item.get("status") == "APPLIED"
            and isinstance(item.get("request"), Mapping)
            and {
                key: value
                for key, value in cast(Mapping[str, object], item["request"]).items()
                if key != "operation_id"
            }
            == expected
            for item in entries.values()
        )
        if not closed:
            return task_delivery.Result("BLOCKED", "EPIC_NOT_CLOSED", None)
    # Both calls obtain fresh facts; no assessment is carried in the request.
    for _ in range(2):
        checked = gate(request)
        if checked.status != "PASS" or checked.machine_code != "OK":
            return task_delivery.Result("BLOCKED", checked.machine_code, None)
        expected_stage = (
            "ROADMAP DONE"
            if request.operation in POST_MERGE
            else "ROADMAP READY FOR RELEASE"
        )
        if checked.roadmap_status != expected_stage:
            return task_delivery.Result("BLOCKED", "FINAL_STAGE_GATE_INVALID", None)
        code = authorization_code(request, authority)
        if code is not None:
            return task_delivery.Result("BLOCKED", code, None)
    entries[request.operation_id] = _entry(request, "INTENT", None)
    ledger.save(entries)
    code = authorization_code(request, authority)
    if code is not None:
        return task_delivery.Result("BLOCKED", code, None)
    try:
        receipt = dict(effect(request))
        if not receipt:
            raise task_delivery.OutcomeUnknown
    except Exception:
        entries[request.operation_id] = _entry(request, "UNKNOWN", None)
        ledger.save(entries)
        return task_delivery.Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
    entries[request.operation_id] = _entry(request, "APPLIED", receipt)
    ledger.save(entries)
    return task_delivery.Result("APPLIED", "OK", receipt)
