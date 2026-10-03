"""One trusted bootstrap: reviewed artifact -> commit -> push -> task PR."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from tools import agent_epic_delivery as delivery
from tools import agent_epic_loop as loop
from tools.agent_coordinator_handover import _repository_identity
from tools.agent_epic_runtime import Authority, TaskPhaseRuntime
from tools.agent_epic_snapshot import commit_reviewed_snapshot
from tools.agent_epic_workflow import ReviewedWorkflow


class BootstrapTask:
    """All writes go through the existing ledger and scoped mandate."""

    def __init__(
        self,
        runtime: TaskPhaseRuntime,
        reviewed: ReviewedWorkflow,
        authority: Authority,
    ) -> None:
        self.runtime = runtime
        self.reviewed = reviewed
        self.authority = authority

    def __call__(self) -> Mapping[str, object]:
        saved = self.runtime._checkpoint("RUN_TASK")
        if saved["schema_version"] not in {"2.0", "3.0"} or saved["pr"] is not None:
            raise loop.PhaseBlocked("BOOTSTRAP_IDENTITY_REQUIRED")
        for key in ("repository", "epic", "task", "base_ref", "base_sha", "head_ref"):
            if self.reviewed.value.get(key) != saved[key]:
                raise loop.PhaseBlocked("HANDOVER_IDENTITY_MISMATCH")
        if (
            _repository_identity(self.runtime.store.repository_root)
            != saved["repository"]
        ):
            raise loop.PhaseBlocked("LIVE_IDENTITY_CHANGED")
        mandate, approved, context = self.authority()
        pinned = saved["schema_version"] == "3.0"
        policy = (
            {"schema_version": "3.0", "policy_sha": saved["policy_sha"]}
            if pinned
            else {"schema_version": "2.0"}
        )
        if pinned and (
            self.reviewed.value.get("schema_version") != "2.0"
            or self.reviewed.value.get("trusted_policy")
            != {"source": "pinned_policy_sha", "policy_sha": saved["policy_sha"]}
        ):
            raise loop.PhaseBlocked("HANDOVER_IDENTITY_MISMATCH")
        if pinned:
            grants = mandate.get("task_grants")
            grant = (
                grants.get(str(saved["task"])) if isinstance(grants, Mapping) else None
            )
            scope = self.reviewed.value.get("causal_scope")
            paths = self.reviewed.value.get("allowed_paths")
            if (
                not isinstance(grant, Mapping)
                or not isinstance(scope, list)
                or not isinstance(paths, list)
                or not scope
                or not paths
                or not all(isinstance(item, str) for item in (*scope, *paths))
                or not isinstance(grant.get("causal_scope"), list)
                or not isinstance(grant.get("allowed_paths"), list)
                or not set(scope).issubset(grant["causal_scope"])
                or not set(paths).issubset(grant["allowed_paths"])
                or any(
                    path
                    in {
                        "AGENTS.md",
                        "CONTRIBUTING.md",
                        "SECURITY.md",
                        "docs/SECURITY.md",
                    }
                    or path.startswith("docs/ADR-")
                    for path in paths
                )
            ):
                raise loop.PhaseBlocked("PLAN_SCOPE_EXPANSION")
        for operation in ("commit_task", "push_task", "create_task_pr"):
            probe = delivery.Request(
                operation_id="bootstrap-preflight",
                operation=operation,
                **{key: saved[key] for key in loop.IDENTITY_KEYS},
                **policy,
            )
            code = delivery._authorized(probe, mandate, approved, context)
            if code is not None:
                raise loop.PhaseBlocked(code)
            if pinned:
                proof = delivery._binding_code(
                    probe,
                    delivery.Ledger(
                        self.runtime.store.directory, self.runtime.store.repository_root
                    ),
                )
                if proof is not None:
                    raise loop.PhaseBlocked(proof)
        artifact = dict(self.reviewed.run())
        gate = artifact.get("gate")
        snapshot = gate.get("snapshot") if isinstance(gate, Mapping) else None
        if not isinstance(snapshot, Mapping) or not isinstance(gate, Mapping):
            raise loop.PhaseBlocked("WORKFLOW_EVIDENCE_MISSING")
        head_sha = snapshot.get("snapshot_commit")
        if not isinstance(head_sha, str) or loop.SHA_RE.fullmatch(head_sha) is None:
            raise loop.PhaseBlocked("WORKFLOW_EVIDENCE_MISSING")
        identity = {key: saved[key] for key in loop.IDENTITY_KEYS}
        ledger = delivery.Ledger(
            self.runtime.store.directory, self.runtime.store.repository_root
        )

        def request(operation: str) -> delivery.Request:
            fields = identity | {
                "head_sha": saved["head_sha"]
                if operation == "commit_task"
                else head_sha
            }
            operation_id = hashlib.sha256(
                json.dumps(
                    {**fields, **policy, "operation": operation}, sort_keys=True
                ).encode()
            ).hexdigest()
            return delivery.Request(
                operation_id=operation_id,
                operation=operation,
                **fields,
                **policy,
            )

        def live_revalidate(value: delivery.Request) -> bool:
            try:
                transport = self.runtime.transport
                if (
                    transport.command(
                        ("git", "symbolic-ref", "--short", "HEAD")
                    ).strip()
                    != value.head_ref
                ):
                    return False
                if (
                    transport.command(("git", "rev-parse", "HEAD")).strip()
                    != value.head_sha
                ):
                    return False
                if (
                    self.runtime.github.ref_sha(value.repository, value.base_ref)
                    != value.base_sha
                ):
                    return False
                task = self.runtime.issues.issue(value.repository, value.task)
                parent = task.get("parent")
                if (
                    task.get("state") != "OPEN"
                    or not isinstance(parent, Mapping)
                    or parent.get("number") != value.epic
                ):
                    return False
                current, approval, now = self.authority()
                return (
                    approval == digest
                    and delivery._authorized(value, current, approval, now) is None
                )
            except Exception:
                return False

        # Authority is checked before starting the bounded model workflow too;
        # every actual write then reloads it, including revocation and time.
        for operation in ("commit_task", "push_task", "create_task_pr"):
            value = request(operation)
            mandate, digest, context = self.authority()
            if operation == "commit_task":
                review = artifact.get("review_receipt")
                if not isinstance(review, Mapping):
                    raise loop.PhaseBlocked("WORKFLOW_REVIEW_NOT_VERIFIED")
                result = commit_reviewed_snapshot(
                    value,
                    root=self.runtime.store.repository_root,
                    evidence=gate,
                    review_receipt=review,
                    mandate=mandate,
                    approved_mandate_digest=digest,
                    mandate_context=context,
                    ledger=ledger,
                    live_revalidate=live_revalidate,
                )
            else:

                def effect(value: delivery.Request) -> Mapping[str, object]:
                    current, approval, now = self.authority()
                    if (
                        approval != digest
                        or delivery._authorized(value, current, approval, now)
                        is not None
                    ):
                        raise delivery.OutcomeUnknown
                    if not live_revalidate(value):
                        raise delivery.OutcomeUnknown
                    return self.runtime.transport.effect(value)

                result = delivery.deliver(
                    value,
                    mandate=mandate,
                    approved_mandate_digest=digest,
                    assessment={},
                    ledger=ledger,
                    mandate_context=context,
                    revalidate=live_revalidate,
                    effect=effect,
                    reconcile=self.runtime.transport.reconcile,
                )
            if result.status == "BLOCKED":
                # Prior writes may already have occurred; preserve phase intent.
                raise loop.LoopError("BOOTSTRAP_DELIVERY_BLOCKED")
            if result.status not in {"APPLIED", "NO_OP"}:
                raise loop.LoopError("ESCALATE_UNKNOWN_OUTCOME")
            if operation == "create_task_pr":
                receipt = result.receipt
                pr = receipt.get("pr") if isinstance(receipt, Mapping) else None
                if not isinstance(pr, int) or isinstance(pr, bool) or pr < 1:
                    raise loop.LoopError("RECEIPT_INVALID")
                bound: dict[str, Any] = identity | {"head_sha": head_sha, "pr": pr}
                artifact["delivery_identity"] = bound
                self.reviewed.artifact_store.save(self.reviewed.key, artifact)
                return {"phase": "RUN_TASK", "delivery_identity": bound}
        raise loop.LoopError("ESCALATE_UNKNOWN_OUTCOME")
