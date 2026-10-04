"""Concrete SHA-bound task phases; deployment remains owner-only and explicit."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from typing import Any, cast

from tools import agent_consumer_demo as demo
from tools import agent_coordinator_delivery as assessment
from tools import agent_epic_delivery as delivery
from tools import agent_epic_loop as loop
from tools import agent_epic_policy_binding as binding
from tools import agent_epic_queue as queue
from tools import agent_github_read as reads
from tools.agent_coordinator_handover import _repository_identity
from tools.agent_epic_transport import GhTaskTransport

Authority = Callable[[], tuple[Mapping[str, object], str, delivery.MandateContext]]
Bootstrap = Callable[[], Mapping[str, object]]


class TaskPhaseRuntime:
    """Bind real read-only assessments to the existing trusted delivery ledger.

    Owner-installed code supplies fresh authority, genuine workflow artifacts
    and the reviewed demo plan. None are inferred from checkpoint status.
    """

    def __init__(
        self,
        *,
        store: loop.CheckpointStore,
        authority: Authority,
        artifacts: Callable[[], Mapping[str, Any]],
        demo_plan: Callable[[], Mapping[str, Any]],
        approved_order: tuple[int, ...],
        bootstrap: Bootstrap | None = None,
        demo_completion: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.store = store
        self.authority = authority
        self.artifacts = artifacts
        self.demo_plan = demo_plan
        self.approved_order = approved_order
        self.bootstrap = bootstrap
        self.demo_completion = demo_completion
        self.continuation_enabled = False
        self.recovery: Any = None
        self.github: assessment.GitHubClient = assessment.GhClient(
            read=self._github_read
        )
        self.issues: queue.GitHubClient = queue.GhClient(read=self._github_read)
        self.transport = GhTaskTransport(
            store.repository_root,
            reporting=artifacts,
            before_write=self._before_transport_write,
            read=self._github_read,
        )
        self._merge: str | None = None

    def _github_read(self, argv: Sequence[str]) -> str:
        # Baseline is local and immutable for this one request/retry sequence.
        saved = self._checkpoint()
        identity = {key: saved[key] for key in loop.IDENTITY_KEYS}
        policy = saved.get("policy_sha")
        initial_mandate, initial_digest, _ = self.authority()
        initial_identity = {
            key: initial_mandate.get(key)
            for key in ("repository", "epic", "roadmap_ref", "policy_sha")
        }
        from tools import agent_epic_recovery as recovery

        control = recovery.Control(self.store.directory, self.store.repository_root)
        generation = (
            control.load()["generation"]
            if recovery.present(self.store.directory)
            else None
        )

        recovery_digest = (
            self.recovery.approved() if self.recovery is not None else None
        )

        def guard() -> None:
            mandate, approved, context = self.authority()
            if (
                approved != initial_digest
                or delivery.mandate_digest(mandate) != approved
                or any(
                    mandate.get(key) != value for key, value in initial_identity.items()
                )
            ):
                raise loop.LoopError("MANDATE_APPROVAL_MISMATCH")
            code = delivery.mandate_lifecycle_code(mandate, context)
            if code is not None:
                raise loop.LoopError(code)
            current = self._checkpoint()
            if (
                any(current[key] != value for key, value in identity.items())
                or current.get("policy_sha") != policy
                or _repository_identity(self.store.repository_root)
                != saved["repository"]
            ):
                raise loop.LoopError("LIVE_IDENTITY_CHANGED")
            present = recovery.present(self.store.directory)
            if present != (generation is not None):
                raise loop.LoopError("RECOVERY_STOPPED")
            if present:
                state = control.load()
                if (
                    generation is None
                    or state["generation"] != generation
                    or (state["stopped"] and state["generation"] > 0)
                ):
                    raise loop.LoopError("RECOVERY_STOPPED")
            if self.recovery is not None:
                pinned = self.recovery.approved()
                if pinned != recovery_digest:
                    raise loop.LoopError("RECOVERY_STOPPED")
                if pinned is not None:
                    approval = recovery.private_value(
                        self.store.directory / "bootstrap-recovery-approval.json"
                    )
                    if (
                        set(approval) != recovery.APPROVAL_KEYS
                        or recovery.digest(approval) != pinned
                        or approval["generation"]
                        != (generation if generation is not None else 0)
                        or type(approval["issued_at"]) is not int
                        or type(approval["expires_at"]) is not int
                        or not approval["issued_at"]
                        <= context.now
                        < approval["expires_at"]
                    ):
                        raise loop.LoopError("RECOVERY_STOPPED")

        return reads.Reader(guard=guard).command(argv)

    def _checkpoint(self, phase: str | None = None) -> dict[str, Any]:
        saved = self.store.load()
        if saved is None:
            raise loop.LoopError("CHECKPOINT_NOT_FOUND")
        if phase is not None and saved["pending_phase"] != phase:
            raise loop.LoopError("PHASE_ORDER_INVALID")
        return saved

    def live(self) -> Mapping[str, Any]:
        try:
            return self._live()
        except (
            assessment.GitHubError,
            queue.GitHubError,
            delivery.OutcomeUnknown,
        ) as error:
            raise loop.LoopError("LIVE_FACTS_UNAVAILABLE") from error

    def _live(self) -> Mapping[str, Any]:
        saved = self._checkpoint()
        repo = _repository_identity(self.store.repository_root)
        if repo != saved["repository"]:
            raise loop.LoopError("LIVE_IDENTITY_CHANGED")
        task = self.issues.issue(repo, saved["task"])
        parent = task.get("parent")
        if task.get("number") != saved["task"] or not isinstance(parent, Mapping):
            raise loop.LoopError("LIVE_IDENTITY_CHANGED")
        parent_number = parent.get("number")
        if not isinstance(parent_number, int) or isinstance(parent_number, bool):
            raise loop.LoopError("LIVE_IDENTITY_CHANGED")
        epic = self.issues.issue(repo, parent_number)
        if epic.get("number") != saved["epic"]:
            raise loop.LoopError("LIVE_IDENTITY_CHANGED")
        if saved["pr"] is None:
            # Bootstrap may already have created its PR before checkpoint commit.
            artifact = self.artifacts()
            bound = artifact.get("delivery_identity")
            pr_number = bound.get("pr") if isinstance(bound, Mapping) else None
            if pr_number is None:
                head = self.transport.command(("git", "rev-parse", "HEAD")).strip()
                ref = self.transport.command(
                    ("git", "symbolic-ref", "--short", "HEAD")
                ).strip()
                return {
                    "repository": repo,
                    "epic": epic["number"],
                    "task": task["number"],
                    "pr": None,
                    "base_ref": saved["base_ref"],
                    "base_sha": self.github.ref_sha(repo, saved["base_ref"]),
                    "head_ref": ref,
                    "head_sha": head,
                }
        else:
            pr_number = saved["pr"]
        if (
            not isinstance(pr_number, int)
            or isinstance(pr_number, bool)
            or pr_number < 1
        ):
            raise loop.LoopError("LIVE_IDENTITY_CHANGED")
        pr = self.github.pull_request(repo, pr_number)
        if pr.get("isCrossRepository") is not False:
            raise loop.LoopError("LIVE_IDENTITY_CHANGED")
        merge = pr.get("mergeCommit")
        self._merge = merge.get("oid") if isinstance(merge, Mapping) else None
        return {
            "repository": repo,
            "epic": epic["number"],
            "task": task["number"],
            "pr": pr.get("number"),
            "base_ref": pr.get("baseRefName"),
            "base_sha": pr.get("baseRefOid"),
            "head_ref": pr.get("headRefName"),
            "head_sha": pr.get("headRefOid"),
        }

    def merge_sha(self) -> str | None:
        return self._merge

    def _assess(self, phase: str) -> dict[str, Any]:
        saved = self._checkpoint()
        artifact = self.artifacts()
        gate = artifact.get("gate")
        paths = artifact.get("allowed_paths")
        if (
            not isinstance(gate, Mapping)
            or not isinstance(paths, list)
            or not all(isinstance(path, str) for path in paths)
        ):
            raise loop.PhaseBlocked("WORKFLOW_EVIDENCE_MISSING")
        facts = self.live()
        if any(facts[key] != saved[key] for key in loop.IDENTITY_KEYS):
            raise loop.PhaseBlocked("STALE_IDENTITY")
        merge_sha = self._merge if phase == "post-merge" else None
        result = assessment.assess(
            assessment.Options(
                phase=phase,
                repository=saved["repository"],
                epic=saved["epic"],
                task=saved["task"],
                pr=saved["pr"],
                base_ref=saved["base_ref"],
                base_sha=saved["base_sha"],
                head_ref=saved["head_ref"],
                head_sha=saved["head_sha"],
                allowed_paths=tuple(paths),
                merge_sha=merge_sha,
            ),
            gate,
            self.github,
        )
        if result.status != "pass":
            raise loop.PhaseBlocked(result.machine_code)
        return asdict(result)

    def _request(self, operation: str) -> delivery.Request:
        saved = self._checkpoint()
        identity = {key: saved[key] for key in loop.IDENTITY_KEYS}
        pinned = saved["schema_version"] == loop.PINNED_SCHEMA_VERSION
        policy = (
            {"schema_version": "3.0", "policy_sha": saved["policy_sha"]}
            if pinned
            else {}
        )
        merge = (
            saved["merge_sha"] if operation in delivery.POST_MERGE_OPERATIONS else None
        )
        operation_id = hashlib.sha256(
            json.dumps(
                {**identity, **policy, "merge_sha": merge, "operation": operation},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return delivery.Request(
            operation_id=operation_id,
            operation=operation,
            **identity,
            **policy,
            merge_sha=merge,
        )

    def _write(self, operation: str) -> None:
        request = self._request(operation)
        phase = "post-merge" if operation in delivery.POST_MERGE_OPERATIONS else "pr"
        checked = self._assess(phase)
        mandate, digest, context = self.authority()
        if operation in delivery.POST_MERGE_OPERATIONS:
            self._demo(require_completion=True)
            code = delivery._authorized(request, mandate, digest, context)
            if code is not None:
                raise loop.PhaseBlocked(code)
            if request.schema_version == "3.0":

                def before_import() -> None:
                    current, approval, now = self.authority()
                    code = delivery._authorized(request, current, approval, now)
                    if approval != digest or code is not None:
                        raise binding.BindingError(code or "MANDATE_APPROVAL_MISMATCH")

                try:
                    binding.import_verified_merge(
                        self.store.repository_root,
                        request.repository,
                        request.base_sha,
                        cast(str, request.merge_sha),
                        lambda: self.github.ref_sha(
                            request.repository, request.base_ref
                        ),
                        before_import=before_import,
                    )
                except binding.BindingError as error:
                    raise loop.PhaseBlocked(str(error)) from None

        def revalidate(_: delivery.Request) -> bool:
            try:
                self._assess(phase)
                if operation in delivery.POST_MERGE_OPERATIONS:
                    self._demo(require_completion=True)
                current, approval, now = self.authority()
                return (
                    approval == digest
                    and delivery._authorized(request, current, approval, now) is None
                )
            except loop.PhaseBlocked:
                return False

        def effect(value: delivery.Request) -> Mapping[str, object]:
            current, approval, now = self.authority()
            if (
                approval != digest
                or delivery._authorized(value, current, approval, now) is not None
            ):
                raise delivery.OutcomeUnknown
            return self.transport.effect(value)

        result = delivery.deliver(
            request,
            mandate=mandate,
            approved_mandate_digest=digest,
            assessment=checked,
            ledger=delivery.Ledger(self.store.directory, self.store.repository_root),
            mandate_context=context,
            revalidate=revalidate,
            effect=effect,
            reconcile=self.transport.reconcile,
        )
        if result.status == "BLOCKED":
            raise loop.PhaseBlocked(result.machine_code)
        if result.status not in {"APPLIED", "NO_OP"}:
            raise loop.LoopError("ESCALATE_UNKNOWN_OUTCOME")

    def _before_transport_write(self, request: delivery.Request) -> None:
        if request.operation in {"push_task", "create_task_pr"}:
            facts = self.live()
            if any(facts[key] != getattr(request, key) for key in loop.IDENTITY_KEYS):
                raise delivery.OutcomeUnknown
            task = self.issues.issue(request.repository, request.task)
            if task.get("state") != "OPEN":
                raise delivery.OutcomeUnknown
            if request.operation == "create_task_pr":
                try:
                    remote_head = self.github.ref_sha(
                        request.repository, request.head_ref
                    )
                except assessment.GitHubError:
                    raise delivery.OutcomeUnknown from None
                if remote_head != request.head_sha:
                    raise delivery.OutcomeUnknown
        else:
            post = request.operation in delivery.POST_MERGE_OPERATIONS
            self._assess("post-merge" if post else "pr")
            if post:
                self._demo(require_completion=True)
        if self.recovery is not None and not self.recovery.refresh(request):
            raise delivery.OutcomeUnknown
        mandate, approved, context = self.authority()
        if delivery._authorized(request, mandate, approved, context) is not None:
            raise delivery.OutcomeUnknown

    def _demo(self, *, require_completion: bool = False) -> Mapping[str, object]:
        saved = self._checkpoint()
        try:
            plan = demo.validate_plan(self.demo_plan())
        except demo.DemoError as error:
            raise loop.PhaseBlocked("DEMO_PENDING") from error
        if any(plan[key] != saved[key] for key in ("repository", "epic", "task")):
            raise loop.PhaseBlocked("DEMO_PENDING")
        if (
            plan["roadmap_ref"] != saved["base_ref"]
            or plan["roadmap_sha"] != saved["merge_sha"]
        ):
            raise loop.PhaseBlocked("DEMO_PENDING")
        if (
            self.github.ref_sha(saved["repository"], plan["baseline_ref"])
            != plan["baseline_sha"]
            or self.github.ref_sha(saved["repository"], plan["roadmap_ref"])
            != saved["merge_sha"]
        ):
            raise loop.PhaseBlocked("DEMO_PENDING")
        result = cast(Mapping[str, object], asdict(demo.assess(plan)))
        if require_completion and plan["consumer_visible"]:
            from tools.agent_epic_final_runtime import digest

            try:
                receipt = self.demo_completion() if self.demo_completion else {}
                expected = {
                    key: plan[key]
                    for key in (
                        "repository",
                        "epic",
                        "task",
                        "baseline_sha",
                        "roadmap_sha",
                    )
                }
                _, _, context = self.authority()
                expected.update(
                    schema_version="1.0",
                    plan_digest=digest(plan),
                    owner_identity=context.owner_identity,
                    completed_by_owner=True,
                )
                if (
                    dict(receipt) != expected
                    or receipt.get("completed_by_owner") is not True
                ):
                    raise ValueError
            except Exception:
                raise loop.PhaseBlocked("DEMO_COMPLETION_REQUIRED") from None
        return result

    def effect(self, phase: str) -> Mapping[str, object]:
        from tools import agent_epic_recovery as recovery

        if recovery.present(self.store.directory):
            if self.recovery is None:
                raise loop.PhaseBlocked("RECOVERY_PROFILE_REQUIRED")
            if phase != "RUN_TASK":
                self.recovery.control.check_entry()
                raise loop.PhaseBlocked("RECOVERY_COMPLETE_REVIEW_REQUIRED")
        self._checkpoint(phase)
        if phase == "RUN_TASK":
            if self.bootstrap is None:
                raise loop.PhaseBlocked("WORKFLOW_NOT_CONFIGURED")
            return self.bootstrap()
        if phase in {"PR_CI", "POST_MERGE"}:
            self._assess("pr" if phase == "PR_CI" else "post-merge")
        elif phase == "MERGE":
            self._write("merge_task_pr")
        elif phase == "DEMO":
            return self._demo()
        elif phase == "TASK_DONE":
            self._demo(require_completion=True)
            self._write("close_task")
            self._write("update_epic")
        elif phase == "NEXT_TASK":
            if getattr(self, "continuation_enabled", False):
                from tools.agent_epic_session import structural_queue

                saved = self._checkpoint()
                structural_queue(
                    self.issues,
                    repository=saved["repository"],
                    epic=saved["epic"],
                    approved_order=self.approved_order,
                )
                return {"phase": phase}
            saved = self._checkpoint()
            epic = self.issues.issue(saved["repository"], saved["epic"])
            children = epic.get("subIssues")
            nodes = children.get("nodes") if isinstance(children, Mapping) else None
            if not isinstance(nodes, list) or not all(
                isinstance(node, Mapping) and isinstance(node.get("number"), int)
                for node in nodes
            ):
                raise loop.PhaseBlocked("QUEUE_INVALID")
            tasks = [
                self.issues.issue(saved["repository"], node["number"]) for node in nodes
            ]
            selected = queue.plan_queue(
                epic=saved["epic"], approved_order=self.approved_order, tasks=tasks
            )
            raise loop.PhaseBlocked(
                "FINAL_GATE_REQUIRED"
                if selected.state == "FINAL_GATE"
                else "NEXT_TASK_PLAN_REQUIRED"
            )
        else:
            raise loop.LoopError("PHASE_ORDER_INVALID")
        return {"phase": phase}

    def reconcile(self, phase: str) -> tuple[str, Mapping[str, object] | None]:
        self._checkpoint(phase)
        if phase == "MERGE":
            state, receipt = self.transport.reconcile(self._request("merge_task_pr"))
            return state, {"phase": phase} if state == "APPLIED" and receipt else None
        if phase == "TASK_DONE":
            try:
                self._assess("post-merge")
                self._demo(require_completion=True)
            except (loop.LoopError, loop.PhaseBlocked):
                return "UNKNOWN", None
            for operation in ("close_task", "update_epic"):
                state, _ = self.transport.reconcile(self._request(operation))
                if state != "APPLIED":
                    return "UNKNOWN", None
            return "APPLIED", {"phase": phase}
        if phase == "RUN_TASK":
            if self.recovery is not None and self.bootstrap is not None:
                return self.bootstrap.reconcile()  # type: ignore[attr-defined,no-any-return]
            bound = self.artifacts().get("delivery_identity")
            if isinstance(bound, Mapping):
                return "APPLIED", {"phase": phase, "delivery_identity": dict(bound)}
            return "UNKNOWN", None
        if phase == "NEXT_TASK":
            return "NOT_APPLIED", None
        try:
            if phase in {"PR_CI", "POST_MERGE"}:
                self._assess("pr" if phase == "PR_CI" else "post-merge")
                return "APPLIED", {"phase": phase}
            if phase == "DEMO":
                return "APPLIED", self._demo()
        except loop.PhaseBlocked:
            return "NOT_APPLIED", None
        return "UNKNOWN", None
