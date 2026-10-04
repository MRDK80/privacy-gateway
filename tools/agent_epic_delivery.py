#!/usr/bin/env python3
"""Execute one mandate-authorized task delivery side effect fail-closed."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from tools.agent_epic_recovery import BootstrapRecovery

from tools import agent_coordinator_branch as branch
from tools import agent_epic_policy_binding as binding
from tools.agent_coordinator_delivery import REQUIRED_CHECKS as REQUIRED_CHECKS
from tools.agent_epic_handover import MANDATE_FIELDS
from tools.agent_epic_handover import MandateContext as MandateContext
from tools.agent_epic_handover import mandate_digest as mandate_digest
from tools.agent_epic_handover import mandate_lifecycle_code as mandate_lifecycle_code

SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
OPERATIONS = frozenset(
    {
        "commit_task",
        "push_task",
        "create_task_pr",
        "merge_task_pr",
        "close_task",
        "update_epic",
    }
)
POST_MERGE_OPERATIONS = frozenset({"close_task", "update_epic"})


class PermissionDenied(Exception):
    """The narrow GitHub credential cannot perform the requested write."""


class OutcomeUnknown(Exception):
    """A write may have happened, so blind retry is forbidden."""


@dataclass(frozen=True)
class Request:
    operation_id: str
    operation: str
    repository: str
    epic: int
    task: int
    pr: int | None
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    merge_sha: str | None = None
    schema_version: str = "1.0"
    policy_sha: str | None = None


@dataclass(frozen=True)
class Result:
    status: str
    machine_code: str
    receipt: dict[str, object] | None


Effect = Callable[[Request], Mapping[str, object]]
Revalidate = Callable[[Request], bool]
Reconcile = Callable[[Request], tuple[str, Mapping[str, object] | None]]


class Ledger:
    """Private atomic intent/receipt ledger, forbidden inside the repository."""

    def __init__(
        self,
        directory: Path,
        repository_root: Path,
        recovery: BootstrapRecovery | None = None,
        *,
        read: Callable[[Sequence[str]], str] | None = None,
    ) -> None:
        self.recovery = recovery
        self.read = read
        self.directory = directory.resolve()
        root = repository_root.resolve()
        self.repository_root = root
        try:
            self.directory.relative_to(root)
        except ValueError:
            pass
        else:
            raise ValueError("UNSAFE_LEDGER_STORAGE")
        self.path = self.directory / "delivery-ledger.json"

    def load(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("LEDGER_READ_FAILED") from error
        if not isinstance(value, dict):
            raise ValueError("LEDGER_INVALID")
        result: dict[str, dict[str, object]] = {}
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not isinstance(item, dict)
                or set(item) != {"operation", "request", "status", "receipt"}
                or item["status"] not in {"INTENT", "UNKNOWN", "APPLIED", "FAILED"}
            ):
                raise ValueError("LEDGER_INVALID")
            result[key] = cast(dict[str, object], item)
        return result

    def save(self, value: Mapping[str, object]) -> None:
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.directory, 0o700)
            descriptor, temporary = tempfile.mkstemp(
                dir=self.directory, prefix=".delivery-", suffix=".tmp"
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(value, stream, sort_keys=True, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except OSError as error:
            raise ValueError("LEDGER_WRITE_FAILED") from error


def _blocked(code: str) -> Result:
    return Result("BLOCKED", code, None)


def _valid_request(request: Request) -> bool:
    return (
        request.schema_version in {"1.0", "2.0", "3.0"}
        and (
            (
                request.schema_version == "3.0"
                and isinstance(request.policy_sha, str)
                and SHA_RE.fullmatch(request.policy_sha) is not None
            )
            or (request.schema_version != "3.0" and request.policy_sha is None)
        )
        and request.operation in OPERATIONS
        and bool(request.operation_id)
        and request.repository.count("/") == 1
        and request.epic > 0
        and request.task > 0
        and (
            (
                isinstance(request.pr, int)
                and not isinstance(request.pr, bool)
                and request.pr > 0
            )
            or (
                request.schema_version in {"2.0", "3.0"}
                and request.pr is None
                and request.operation in {"commit_task", "push_task", "create_task_pr"}
            )
        )
        and request.base_ref.startswith(f"roadmap/{request.epic}-")
        and not request.head_ref.startswith("roadmap/")
        and SHA_RE.fullmatch(request.base_sha) is not None
        and SHA_RE.fullmatch(request.head_sha) is not None
        and (
            request.merge_sha is None or SHA_RE.fullmatch(request.merge_sha) is not None
        )
        and (
            (request.operation in POST_MERGE_OPERATIONS)
            == (request.merge_sha is not None)
        )
    )


def _authorized(
    request: Request,
    mandate: Mapping[str, object],
    approved_mandate_digest: str,
    mandate_context: MandateContext,
) -> str | None:
    if mandate.get("schema_version") == "3.0" and set(mandate) != MANDATE_FIELDS:
        return "MANDATE_INVALID"
    if DIGEST_RE.fullmatch(approved_mandate_digest) is None:
        return "MANDATE_APPROVAL_MISMATCH"
    lifecycle = mandate_lifecycle_code(mandate, mandate_context)
    if lifecycle is not None:
        return lifecycle
    approval = mandate.get("approval")
    operations = mandate.get("operations")
    grants = mandate.get("task_grants")
    if (
        mandate_digest(mandate) != approved_mandate_digest
        or not isinstance(approval, Mapping)
        or approval.get("mandate_digest") != approved_mandate_digest
    ):
        return "MANDATE_APPROVAL_MISMATCH"
    if (
        mandate.get("schema_version")
        != ("3.0" if request.schema_version == "3.0" else "2.0")
        or mandate.get("repository") != request.repository
        or mandate.get("epic") != request.epic
        or mandate.get("roadmap_ref") != request.base_ref
        or mandate.get("policy_sha")
        != (request.policy_sha if request.schema_version == "3.0" else request.base_sha)
        or not isinstance(grants, Mapping)
        or str(request.task) not in grants
    ):
        return "MANDATE_IDENTITY_MISMATCH"
    if not isinstance(operations, list) or request.operation not in operations:
        return "MANDATE_AUTHORITY_MISSING"
    return None


def _assessment_code(request: Request, assessment: Mapping[str, object]) -> str | None:
    if request.operation not in {"merge_task_pr", *POST_MERGE_OPERATIONS}:
        return None
    expected_phase = (
        "post-merge" if request.operation in POST_MERGE_OPERATIONS else "pr"
    )
    if (
        assessment.get("schema_version") != "1.0"
        or assessment.get("phase") != expected_phase
        or assessment.get("status") != "pass"
        or assessment.get("machine_code") != "OK"
    ):
        return "GATE_INVALID"
    identity = {
        "repository": request.repository,
        "epic": request.epic,
        "task": request.task,
        "pr": request.pr,
        "base_ref": request.base_ref,
        "base_sha": request.base_sha,
        "head_ref": request.head_ref,
        "head_sha": request.head_sha,
        "merge_sha": request.merge_sha,
    }
    if any(assessment.get(key) != value for key, value in identity.items()):
        return "STALE_IDENTITY"
    raw_checks = assessment.get("github_checks")
    if not isinstance(raw_checks, list):
        return "GATE_INVALID"
    states: dict[str, object] = {}
    for item in raw_checks:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            return "GATE_INVALID"
        name = item["name"]
        if name in states:
            return "GATE_INVALID"
        states[name] = item.get("state")
    if set(states) != set(REQUIRED_CHECKS) or set(states.values()) != {"SUCCESS"}:
        return "GATE_INVALID"
    return None


def _entry(
    request: Request, status: str, receipt: Mapping[str, object] | None
) -> dict[str, object]:
    return {
        "operation": request.operation,
        "request": _request_fields(request),
        "status": status,
        "receipt": dict(receipt) if receipt is not None else None,
    }


def _request_fields(request: Request) -> dict[str, object]:
    """Keep persisted legacy request identity unchanged for reconciliation."""
    value = asdict(request)
    if request.schema_version != "3.0":
        del value["policy_sha"]
    if request.schema_version == "1.0":
        del value["schema_version"]
    return value


def _has_applied(
    entries: Mapping[str, Mapping[str, object]], request: Request, operation: str
) -> bool:
    for item in entries.values():
        stored = item.get("request")
        if (
            item.get("status") == "APPLIED"
            and item.get("operation") == operation
            and isinstance(stored, Mapping)
            and stored.get("repository") == request.repository
            and stored.get("epic") == request.epic
            and stored.get("task") == request.task
            and stored.get("pr") == request.pr
            and stored.get("merge_sha") == request.merge_sha
            and (
                request.schema_version != "3.0"
                or all(
                    stored.get(key) == _request_fields(request).get(key)
                    for key in (
                        "schema_version",
                        "policy_sha",
                        "base_ref",
                        "base_sha",
                        "head_ref",
                        "head_sha",
                    )
                )
            )
        ):
            return True
    return False


def _binding_code(request: Request, ledger: Ledger) -> str | None:
    if request.schema_version != "3.0":
        return None
    try:
        binding.validate_live_binding(
            {
                "schema_version": binding.SCHEMA_VERSION,
                "repository": request.repository,
                "epic": request.epic,
                "roadmap_ref": request.base_ref,
                "policy_sha": request.policy_sha,
                "base_sha": request.base_sha,
            },
            repository_root=ledger.repository_root,
            repository=request.repository,
            epic=request.epic,
            roadmap_ref=request.base_ref,
            policy_sha=cast(str, request.policy_sha),
            branches=branch.GitHubCLI(read=ledger.read).branches,
            merge_sha=request.merge_sha,
        )
    except binding.BindingError as error:
        return str(error)
    return None


def deliver(
    request: Request,
    *,
    mandate: Mapping[str, object],
    approved_mandate_digest: str,
    assessment: Mapping[str, object],
    ledger: Ledger,
    revalidate: Revalidate,
    effect: Effect,
    mandate_context: MandateContext,
    reconcile: Reconcile | None = None,
) -> Result:
    """Apply one exact side effect after authority and live-state revalidation."""
    from tools import agent_epic_recovery as recovery

    guard = ledger.recovery
    if recovery.present(ledger.directory) and guard is None:
        return _blocked("RECOVERY_PROFILE_REQUIRED")
    if not _valid_request(request):
        return _blocked("REQUEST_INVALID")
    authority = _authorized(request, mandate, approved_mandate_digest, mandate_context)
    if authority is not None:
        return _blocked(authority)
    gate = _assessment_code(request, assessment)
    if gate is not None:
        return _blocked(gate)
    proof = _binding_code(request, ledger)
    if proof is not None:
        return _blocked(proof)
    try:
        entries = ledger.load()
    except ValueError:
        return _blocked("LEDGER_INVALID")
    if request.operation == "update_epic" and not _has_applied(
        entries, request, "close_task"
    ):
        return _blocked("TASK_NOT_CLOSED")
    previous = entries.get(request.operation_id)
    if previous is not None:
        if previous.get("request") != _request_fields(request):
            return _blocked("OPERATION_ID_COLLISION")
        receipt = previous.get("receipt")
        if previous.get("status") == "APPLIED":
            if guard is not None and not guard.verify_receipt(
                request, cast(dict[str, object] | None, receipt)
            ):
                guard.stop()
                return _blocked("RECOVERY_RECEIPT_CONFLICT")
            return Result("NO_OP", "ALREADY_APPLIED", cast(dict[str, object], receipt))
        if previous.get("status") in {"INTENT", "UNKNOWN"}:
            if reconcile is None:
                return Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
            state, reconciled_receipt = (
                guard.reconcile(request) if guard is not None else reconcile(request)
            )
            if state == "APPLIED" and reconciled_receipt is not None:
                entries[request.operation_id] = _entry(
                    request, "APPLIED", reconciled_receipt
                )
                ledger.save(entries)
                return Result("NO_OP", "ALREADY_APPLIED", dict(reconciled_receipt))
            if state != "NOT_APPLIED":
                return Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
    if not revalidate(request):
        return _blocked("STALE_IDENTITY")
    proof = _binding_code(request, ledger)
    if proof is not None:
        return _blocked(proof)
    authority = _authorized(request, mandate, approved_mandate_digest, mandate_context)
    if authority is not None:
        return _blocked(authority)
    entries[request.operation_id] = _entry(request, "INTENT", None)
    try:
        if guard is not None:
            guard.before_effect(request)
        ledger.save(entries)
        receipt = dict(effect(request))
    except PermissionDenied:
        if guard is not None:
            guard.stop("permission")
        entries[request.operation_id] = _entry(request, "FAILED", None)
        ledger.save(entries)
        return _blocked("GITHUB_PERMISSION_DENIED")
    except OutcomeUnknown as error:
        if guard is not None:
            guard.stop(recovery.diagnostic(error))
        entries[request.operation_id] = _entry(request, "UNKNOWN", None)
        ledger.save(entries)
        return Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
    except Exception:
        if guard is not None:
            guard.stop()
        entries[request.operation_id] = _entry(request, "UNKNOWN", None)
        ledger.save(entries)
        return Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
    if not receipt or (
        guard is not None and not guard.verify_receipt(request, receipt)
    ):
        if guard is not None:
            guard.stop("invalid_adapter_response")
        entries[request.operation_id] = _entry(request, "UNKNOWN", None)
        ledger.save(entries)
        return Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
    entries[request.operation_id] = _entry(request, "APPLIED", receipt)
    ledger.save(entries)
    if guard is not None:
        guard.complete(request)
    return Result("APPLIED", "OK", receipt)
