"""Narrow local commit transport for the mandate-checked delivery driver.

Evidence and the review receipt must come from the trusted workflow, not model
output. This transport grants no authority; use it only through ``deliver``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tools import agent_epic_delivery as delivery
from tools import agent_gate as gate
from tools.agent_epic_delivery import OutcomeUnknown, Request


class SnapshotCommit:
    """Publish the already reviewed deterministic artifact without new SHA."""

    def __init__(
        self,
        root: Path,
        allowed_paths: Sequence[str],
        evidence: Mapping[str, Any],
        review_receipt: Mapping[str, Any],
        authorize: Callable[[Request], bool] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.allowed_paths = tuple(allowed_paths)
        self.evidence = evidence
        self.review_receipt = review_receipt
        self.authorize = authorize

    def _git(self, *arguments: str) -> str:
        try:
            result = subprocess.run(
                ["git", *arguments],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise OutcomeUnknown from error
        if result.returncode != 0:
            raise OutcomeUnknown
        return result.stdout.strip()

    def _validate(self, request: Request) -> Mapping[str, Any]:
        if (
            request.schema_version not in {"2.0", "3.0"}
            or request.operation != "commit_task"
            or request.pr is not None
            or request.head_sha != request.base_sha
            or not request.base_ref.startswith(f"roadmap/{request.epic}-")
            or request.head_ref.startswith("roadmap/")
            or request.head_ref == "main"
            or not self.allowed_paths
            or not gate.gate_evidence_is_passing(self.evidence)
        ):
            raise OutcomeUnknown
        snapshot = self.evidence.get("snapshot")
        reviewed = self.review_receipt.get("reviewed_state")
        if (
            not isinstance(snapshot, Mapping)
            or snapshot.get("base_sha") != request.base_sha
            or snapshot.get("snapshot_method") != gate.SNAPSHOT_METHOD
            or snapshot.get("provenance_complete") is not True
            or self.review_receipt.get("schema_version")
            != ("2.0" if request.schema_version == "3.0" else "1.0")
            or (
                request.schema_version == "3.0"
                and (
                    request.policy_sha is None
                    or self.review_receipt.get("policy_sha") != request.policy_sha
                )
            )
            or self.review_receipt.get("task") != request.task
            or self.review_receipt.get("base_sha") != request.base_sha
            or self.review_receipt.get("head_ref") != request.head_ref
            or self.review_receipt.get("verdict") not in {"PASS", "PASS_WITH_NOTES"}
            or reviewed != {"snapshot_commit": snapshot.get("snapshot_commit")}
        ):
            raise OutcomeUnknown
        if self._git("symbolic-ref", "--short", "HEAD") != request.head_ref:
            raise OutcomeUnknown
        if (
            self._git("rev-parse", "--verify", f"refs/heads/{request.base_ref}")
            != request.base_sha
        ):
            raise OutcomeUnknown
        return snapshot

    @staticmethod
    def _matches(snapshot: Mapping[str, Any], session: gate.SnapshotSession) -> bool:
        return all(
            snapshot.get(key) == session.identity().get(key)
            for key in (
                "base_sha",
                "snapshot_method",
                "snapshot_commit",
                "tree_hash",
                "diff_sha256",
                "provenance_complete",
                "allowed_paths",
            )
        )

    def revalidate(self, request: Request) -> bool:
        """Read-only local checks, in addition to driver live revalidation."""
        try:
            snapshot = self._validate(request)
            if self._git("rev-parse", "HEAD") != request.head_sha:
                return False
            # No pre-existing staged state is silently replaced by this adapter.
            if self._git("diff", "--cached", "--name-only"):
                return False
            with gate.trusted_snapshot_session(
                root=self.root,
                base_sha=request.base_sha,
                allowed_paths=self.allowed_paths,
            ) as session:
                return self._matches(snapshot, session)
        except (OutcomeUnknown, gate.GateError, OSError):
            return False

    def effect(self, request: Request) -> Mapping[str, object]:
        """Import local objects, sync index, then CAS only the task branch."""
        if not self.revalidate(request):
            raise OutcomeUnknown
        snapshot = self._validate(request)
        with gate.trusted_snapshot_session(
            root=self.root,
            base_sha=request.base_sha,
            allowed_paths=self.allowed_paths,
        ) as session:
            if not self._matches(snapshot, session):
                raise OutcomeUnknown
            if self.authorize is not None and not self.authorize(request):
                raise OutcomeUnknown
            # This is a disposable LOCAL source, never a GitHub fetch or push.
            self._git(
                "fetch",
                "--no-write-fetch-head",
                "--no-tags",
                "--",
                str(session.checkout),
                session.snapshot_commit,
            )
            # Recheck the working tree after importing objects and before writes.
            if not self.revalidate(request):
                raise OutcomeUnknown
            if self.authorize is not None and not self.authorize(request):
                raise OutcomeUnknown
            self._git("read-tree", session.tree_hash)
            if self.authorize is not None and not self.authorize(request):
                raise OutcomeUnknown
            self._git(
                "update-ref",
                f"refs/heads/{request.head_ref}",
                session.snapshot_commit,
                request.head_sha,
            )
        state, receipt = self.reconcile(request)
        if state != "APPLIED" or receipt is None:
            raise OutcomeUnknown
        return receipt

    def reconcile(self, request: Request) -> tuple[str, Mapping[str, object] | None]:
        """Never repeat an uncertain ref/index write on insufficient evidence."""
        try:
            snapshot = self._validate(request)
            head = self._git("rev-parse", "HEAD")
            if head == request.head_sha:
                if self.revalidate(request):
                    return "NOT_APPLIED", None
                return "UNKNOWN", None
            if head != snapshot["snapshot_commit"]:
                return "UNKNOWN", None
            if self._git("status", "--porcelain"):
                return "UNKNOWN", None
            if self._git("rev-parse", "HEAD^{tree}") != snapshot["tree_hash"]:
                return "UNKNOWN", None
            with gate.trusted_snapshot_session(
                root=self.root,
                base_sha=request.base_sha,
                allowed_paths=self.allowed_paths,
            ) as session:
                if not self._matches(snapshot, session):
                    return "UNKNOWN", None
            return "APPLIED", {"head_sha": head, "tree_hash": snapshot["tree_hash"]}
        except (OutcomeUnknown, gate.GateError, OSError):
            return "UNKNOWN", None


def commit_reviewed_snapshot(
    request: Request,
    *,
    root: Path,
    evidence: Mapping[str, Any],
    review_receipt: Mapping[str, Any],
    mandate: Mapping[str, object],
    approved_mandate_digest: str,
    mandate_context: delivery.MandateContext,
    ledger: delivery.Ledger,
    live_revalidate: delivery.Revalidate,
) -> delivery.Result:
    """Mandate-checked entry point; task path scope comes only from the grant.

    ``review_receipt`` is emitted by a trusted supervisor after validating the
    independent controller response against its actual review request. It is
    not a model-supplied extra field or a claim inferred from checkpoint state.
    """
    grants = mandate.get("task_grants")
    grant = grants.get(str(request.task)) if isinstance(grants, Mapping) else None
    paths = grant.get("allowed_paths") if isinstance(grant, Mapping) else None
    if (
        request.operation != "commit_task"
        or not isinstance(paths, list)
        or not paths
        or not all(isinstance(path, str) for path in paths)
    ):
        return delivery.Result("BLOCKED", "MANDATE_AUTHORITY_MISSING", None)
    transport = SnapshotCommit(
        root, paths, evidence, review_receipt, authorize=live_revalidate
    )
    return delivery.deliver(
        request,
        mandate=mandate,
        approved_mandate_digest=approved_mandate_digest,
        assessment={},
        ledger=ledger,
        mandate_context=mandate_context,
        revalidate=lambda value: live_revalidate(value) and transport.revalidate(value),
        effect=transport.effect,
        reconcile=transport.reconcile,
    )
