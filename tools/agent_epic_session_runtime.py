"""Owner-installed concrete planner/task/final factories for an epic session."""

from __future__ import annotations

import copy
import time
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tools import agent_coordinator_branch as branch
from tools import agent_coordinator_handover as handover
from tools import agent_epic_delivery as delivery
from tools import agent_epic_handover as planner
from tools import agent_epic_loop as loop
from tools import agent_epic_queue as queue
from tools import agent_orchestrate as workflow
from tools.agent_epic_bootstrap import BootstrapTask
from tools.agent_epic_final_runtime import FinalRuntime, digest
from tools.agent_epic_runtime import TaskPhaseRuntime
from tools.agent_epic_runtime_cli import private_json
from tools.agent_epic_session import Session, structural_queue
from tools.agent_epic_workflow import ReviewedWorkflow, codex_role_adapter


class SessionRuntime:
    def __init__(self, state: Path, root: Path, config: Mapping[str, Any]) -> None:
        self.state = state
        self.root = root
        self.config = config
        self.store = loop.CheckpointStore(state, root)
        self.plans = private_json(state / "approved-plans.json")
        if (
            set(self.plans) != {"schema_version", "initial_handover_digest", "tasks"}
            or self.plans["schema_version"] != "1.0"
            or digest(self.plans) != config["approved_plans_digest"]
            or not isinstance(self.plans["tasks"], dict)
        ):
            raise loop.PhaseBlocked("APPROVED_PLANS_INVALID")
        mandate, _, _ = self.authority(config["task_iterations"])
        self.identity = {
            key: mandate[key]
            for key in ("repository", "epic", "roadmap_ref", "policy_sha")
        }
        self.maximum = mandate["limits"]["max_task_iterations"]
        self.issues = queue.GhClient()
        self.github: branch.GitHubClient = branch.GitHubCLI()

    def authority(
        self, iterations: int
    ) -> tuple[dict[str, Any], str, delivery.MandateContext]:
        mandate = private_json(self.state / "mandate.json")
        approved = self.config["approved_mandate_digest"]
        if (
            mandate.get("schema_version") != "3.0"
            or delivery.mandate_digest(mandate) != approved
        ):
            raise loop.PhaseBlocked("MANDATE_APPROVAL_MISMATCH")
        if hasattr(self, "identity") and any(
            mandate[key] != value for key, value in self.identity.items()
        ):
            raise loop.PhaseBlocked("MANDATE_IDENTITY_MISMATCH")
        context = delivery.MandateContext(
            int(time.time()),
            self.config["owner_identity"],
            self.config["started_at"],
            iterations,
            self.config["follow_up_issues"],
        )
        return mandate, approved, context

    def _adapter(self) -> workflow.CommandAdapter:
        return codex_role_adapter(
            policy_root=Path(self.config["policy_root"]),
            root=self.root,
            policy_sha=self.identity["policy_sha"],
        )

    def _fresh_authority(
        self, iterations: int, *, at_task_start: bool = True
    ) -> tuple[dict[str, Any], str, delivery.MandateContext]:
        mandate, approved, context = self.authority(iterations)
        approval = mandate.get("approval")
        if (
            set(mandate) != planner.MANDATE_FIELDS
            or not isinstance(approval, Mapping)
            or approval.get("mandate_digest") != approved
        ):
            raise loop.PhaseBlocked("MANDATE_APPROVAL_MISMATCH")
        code = delivery.mandate_lifecycle_code(
            mandate, context, at_task_start=at_task_start
        )
        if code is not None:
            raise loop.PhaseBlocked(code)
        return mandate, approved, context

    def _guard_mutation(self, iterations: int, *, final: bool = False) -> None:
        self._fresh_authority(iterations, at_task_start=not final)

    def _recipe(self, task: int) -> Mapping[str, Any]:
        current = private_json(self.state / "approved-plans.json")
        if digest(current) != self.config["approved_plans_digest"]:
            raise loop.PhaseBlocked("APPROVED_PLANS_CHANGED")
        recipe = current["tasks"].get(str(task))
        if not isinstance(recipe, Mapping) or set(recipe) != {"plan", "demo_plan"}:
            raise loop.PhaseBlocked("NEXT_TASK_PLAN_REQUIRED")
        return recipe

    def task_adapter(
        self, store: loop.CheckpointStore, iterations: int
    ) -> TaskPhaseRuntime:
        saved = store.load()
        if (
            saved is None
            or saved["schema_version"] != "3.0"
            or saved["policy_sha"] != self.identity["policy_sha"]
        ):
            raise loop.PhaseBlocked("PINNED_CHECKPOINT_REQUIRED")
        value = private_json(store.directory / "handover.json")
        if store.directory == self.state:
            approved = self.plans["initial_handover_digest"]
        else:
            approved = handover.handover_digest(value)
            provenance = value.get("mandate_provenance")
            if (
                not isinstance(provenance, Mapping)
                or provenance.get("digest") != self.config["approved_mandate_digest"]
            ):
                raise loop.PhaseBlocked("MANDATE_APPROVAL_MISMATCH")
        if (
            handover.handover_digest(value) != approved
            or any(
                value.get(key) != saved[key]
                for key in (
                    "repository",
                    "epic",
                    "task",
                    "base_ref",
                    "base_sha",
                    "head_ref",
                )
            )
            or value.get("trusted_policy")
            != {
                "source": "pinned_policy_sha",
                "policy_sha": self.identity["policy_sha"],
            }
        ):
            raise loop.PhaseBlocked("HANDOVER_IDENTITY_MISMATCH")
        reviewed = ReviewedWorkflow(
            root=self.root,
            directory=store.directory,
            value=value,
            approved_digest=approved,
            adapter=self._adapter(),
        )

        def demo() -> Mapping[str, Any]:
            current = store.load()
            if current is None or current["merge_sha"] is None:
                raise loop.PhaseBlocked("DEMO_PENDING")
            plan = copy.deepcopy(dict(self._recipe(current["task"])["demo_plan"]))
            plan["roadmap_sha"] = current["merge_sha"]
            return plan

        def authority() -> tuple[dict[str, Any], str, delivery.MandateContext]:
            return self.authority(iterations)

        engine = TaskPhaseRuntime(
            store=store,
            authority=authority,
            artifacts=reviewed.artifacts,
            demo_plan=demo,
            demo_completion=lambda: private_json(
                store.directory / "demo-completion.json"
            ),
            approved_order=tuple(self.config["approved_order"]),
        )
        engine.continuation_enabled = True
        engine.bootstrap = BootstrapTask(engine, reviewed, authority)
        return engine

    def select(self) -> queue.QueuePlan:
        return structural_queue(
            self.issues,
            repository=self.identity["repository"],
            epic=self.identity["epic"],
            approved_order=tuple(self.config["approved_order"]),
        )

    def prepare(
        self,
        previous: Mapping[str, Any],
        task: int,
        store: loop.CheckpointStore,
        iterations: int,
    ) -> None:
        recipe = self._recipe(task)
        plan = copy.deepcopy(dict(recipe["plan"]))
        mandate, approved, context = self.authority(iterations)
        branches = self.github.branches(mandate["repository"])
        base = branches.get(mandate["roadmap_ref"])
        main = branches.get("main")
        if (
            base != previous["merge_sha"]
            or not isinstance(base, str)
            or not isinstance(main, str)
        ):
            raise loop.PhaseBlocked("STALE_BASE")
        plan.update(base_sha=base, default_sha=main)
        if (
            plan.get("schema_version") != "2.0"
            or plan.get("task") != task
            or plan.get("default_ref") != "main"
            or plan.get("policy_sha") != mandate["policy_sha"]
        ):
            raise loop.PhaseBlocked("NEXT_TASK_PLAN_REQUIRED")
        issue = handover.GitHubCLI().issue(mandate["repository"], task)
        body = issue.get("body")
        if not isinstance(body, str):
            raise loop.PhaseBlocked("CRITERIA_PROVENANCE_MISMATCH")
        try:
            planner._validate_inputs(
                mandate,
                plan,
                root=self.root,
                approved_mandate_digest=approved,
                issue_body=body,
                mandate_context=context,
            )
        except planner.PlanningError as error:
            raise loop.PhaseBlocked(error.machine_code) from None
        if handover._git(self.root, "status", "--porcelain=v1", "-z"):
            raise loop.PhaseBlocked("DIRTY_WORKTREE")
        if handover._repository_identity(self.root) != mandate["repository"]:
            raise loop.PhaseBlocked("REPOSITORY_MISMATCH")
        # Only scoped read/import and fast-forward local ref synchronization.
        # No force fetch/push and no modification of the task's checked-out ref.
        mutated = False
        tracking_imported = False
        expected_roadmap = handover._git(
            self.root, "rev-parse", f"refs/heads/{mandate['roadmap_ref']}"
        )

        def guard(*, task_created: bool = False) -> None:
            refs = self.github.branches(mandate["repository"])
            if (
                handover._repository_identity(self.root) != mandate["repository"]
                or refs.get(mandate["roadmap_ref"]) != base
                or refs.get("main") != main
                or handover._git(self.root, "branch", "--show-current")
                != previous["head_ref"]
                or handover._git(self.root, "rev-parse", "HEAD")
                != previous["head_sha"]
                or handover._git(self.root, "status", "--porcelain=v1", "-z")
                or handover._git(
                    self.root, "rev-parse", f"refs/heads/{mandate['roadmap_ref']}"
                )
                != expected_roadmap
                or branch._sha(self.root, f"refs/heads/{plan['head_ref']}")
                != (base if task_created else None)
            ):
                raise loop.PhaseBlocked("PREPARATION_IDENTITY_CHANGED")
            if tracking_imported and (
                branch._sha(self.root, "refs/remotes/origin/main") != main
                or branch._sha(
                    self.root, f"refs/remotes/origin/{mandate['roadmap_ref']}"
                )
                != base
            ):
                raise loop.PhaseBlocked("PREPARATION_IDENTITY_CHANGED")
            self._guard_mutation(iterations)

        try:
            guard()
            # Fetch can update tracking refs even if a later guard denies the
            # operation or the command fails after a partial transfer.
            mutated = True
            handover._git(
                self.root,
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                f"https://github.com/{mandate['repository']}.git",
                f"refs/heads/{mandate['roadmap_ref']}:refs/remotes/origin/{mandate['roadmap_ref']}",
                "refs/heads/main:refs/remotes/origin/main",
            )
            tracking_imported = True
            old = handover._git(
                self.root, "rev-parse", f"refs/heads/{mandate['roadmap_ref']}"
            )
            if old != base:
                if (
                    handover._git(self.root, "branch", "--show-current")
                    == mandate["roadmap_ref"]
                ):
                    raise loop.LoopError("ROADMAP_CHECKED_OUT")
                handover._git(self.root, "merge-base", "--is-ancestor", old, base)
                guard()
                handover._git(
                    self.root,
                    "update-ref",
                    f"refs/heads/{mandate['roadmap_ref']}",
                    base,
                    old,
                )
                mutated = True
                expected_roadmap = base
            guard()
            mandate, approved, context = self._fresh_authority(iterations)
            result = planner.prepare_handover(
                mandate,
                plan,
                root=self.root,
                approved_mandate_digest=approved,
                github=self.github,
                issue_body=body,
                mandate_context=context,
                before_create=guard,
            )
            if result.machine_code != "OK" or result.handover is None:
                raise loop.LoopError(result.machine_code)
            mutated = True
            guard(task_created=True)
            handover._git(self.root, "switch", plan["head_ref"])
            if handover._git(self.root, "rev-parse", "HEAD") != base:
                raise loop.LoopError("STALE_HEAD")
            store.directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            store.directory.parent.chmod(0o700)
            workflow.StateStore(store.directory).save("handover", result.handover)
            store.save(
                {
                    "schema_version": "3.0",
                    "repository": mandate["repository"],
                    "epic": mandate["epic"],
                    "task": task,
                    "pr": None,
                    "base_ref": mandate["roadmap_ref"],
                    "base_sha": base,
                    "head_ref": plan["head_ref"],
                    "head_sha": base,
                    "policy_sha": mandate["policy_sha"],
                    "phase": "PLAN",
                    "completed_phases": [],
                    "pending_phase": None,
                    "merge_sha": None,
                    "status": "READY",
                    "rate_limit_pause": None,
                }
            )
        except handover.HandoverError:
            raise loop.LoopError("SUCCESSOR_PREPARATION_UNKNOWN") from None
        except loop.PhaseBlocked:
            if mutated:
                raise loop.LoopError("SUCCESSOR_PREPARATION_UNKNOWN") from None
            raise

    def reconcile_plan(
        self, previous: Mapping[str, Any], task: int, store: loop.CheckpointStore
    ) -> str:
        try:
            saved = store.load()
            recipe = self._recipe(task)
            ref = recipe["plan"]["head_ref"]
            if saved is not None:
                value = private_json(store.directory / "handover.json")
                if (
                    saved["task"] == task
                    and saved["phase"] == "PLAN"
                    and saved["base_sha"] == previous["merge_sha"]
                    and saved["policy_sha"] == self.identity["policy_sha"]
                    and value.get("task") == task
                    and value.get("head_ref") == ref
                    and handover._git(self.root, "rev-parse", "HEAD")
                    == saved["head_sha"]
                    and handover._git(self.root, "branch", "--show-current") == ref
                ):
                    return "APPLIED"
                return "UNKNOWN"
            # An absent successor cannot prove that the preceding fetch did
            # not import objects or update tracking refs. Without a complete
            # preparation receipt, retain the intent for owner reconciliation.
            return "UNKNOWN"
        except Exception:
            pass
        return "UNKNOWN"

    def _sync_final(
        self,
        owner: Mapping[str, Any],
        *,
        verified_main_merge: str | None = None,
        iterations: int,
    ) -> None:
        from tools import agent_epic_policy_binding as binding

        repo, ref = owner["repository"], owner["roadmap_ref"]
        sha = owner["roadmap_sha"]

        def live() -> str:
            refs = self.github.branches(repo)
            if refs.get("main") != (verified_main_merge or owner["main_sha"]):
                raise loop.PhaseBlocked("MAIN_CHANGED")
            return refs.get(ref, "")

        try:
            binding.import_verified_merge(
                self.root,
                repo,
                owner["policy_sha"],
                sha,
                live,
                before_import=lambda: self._guard_mutation(iterations, final=True),
            )
            handover._git(
                self.root, "merge-base", "--is-ancestor", owner["main_sha"], sha
            )
            old = handover._git(self.root, "rev-parse", f"refs/heads/{ref}")
            if old != sha:
                if handover._git(self.root, "branch", "--show-current") == ref:
                    raise loop.PhaseBlocked("ROADMAP_CHECKED_OUT")
                handover._git(self.root, "merge-base", "--is-ancestor", old, sha)
                if live() != sha:
                    raise loop.PhaseBlocked("STALE_BASE")
                self._guard_mutation(iterations, final=True)
                handover._git(self.root, "update-ref", f"refs/heads/{ref}", sha, old)
            if live() != sha:
                raise loop.PhaseBlocked("STALE_BASE")
        except (binding.BindingError, handover.HandoverError):
            raise loop.PhaseBlocked("FINAL_SYNC_UNCONFIRMED") from None

    def final_resume(self, iterations: int) -> Mapping[str, Any]:
        approved_final = self.config["approved_final_digest"]
        if (
            not isinstance(approved_final, str)
            or delivery.DIGEST_RE.fullmatch(approved_final) is None
        ):
            raise loop.PhaseBlocked("FINAL_DEMO_UNCONFIRMED")

        def authority() -> tuple[dict[str, Any], str, delivery.MandateContext]:
            return self.authority(iterations)

        engine = FinalRuntime(
            root=self.root,
            directory=self.state,
            authority=authority,
            approval=lambda: private_json(self.state / "final-approval.json"),
            approved_digest=approved_final,
            lesson=lambda: private_json(self.state / "verified-lesson.json"),
            adapter=self._adapter(),
        )
        owner = engine._owner()
        from tools.agent_epic_final_delivery import authorization_code

        code = authorization_code(engine._request("create_roadmap_pr"), authority)
        if code is not None:
            raise loop.PhaseBlocked(code)
        if handover._git(self.root, "status", "--porcelain=v1", "-z"):
            raise loop.PhaseBlocked("DIRTY_WORKTREE")
        merged_main = None
        created = engine._request("create_roadmap_pr")
        entry = engine.ledger.load().get(created.operation_id)
        if entry is not None and entry.get("status") == "APPLIED":
            if entry.get("request") != asdict(created):
                raise loop.PhaseBlocked("OPERATION_ID_COLLISION")
            receipt = entry.get("receipt")
            pr = receipt.get("pr") if isinstance(receipt, Mapping) else None
            if type(pr) is not int or pr <= 0:
                raise loop.PhaseBlocked("FINAL_RECEIPT_INVALID")
            info = engine.transport.pull_request(
                engine._request("merge_roadmap_pr", pr=pr)
            )
            if info.get("state") == "MERGED":
                merge = info.get("mergeCommit")
                merged_main = merge.get("oid") if isinstance(merge, Mapping) else None
                if (
                    not isinstance(merged_main, str)
                    or delivery.SHA_RE.fullmatch(merged_main) is None
                ):
                    raise loop.PhaseBlocked("FINAL_RECEIPT_INVALID")
        self._sync_final(owner, verified_main_merge=merged_main, iterations=iterations)
        self._guard_mutation(iterations, final=True)
        handover._git(self.root, "switch", owner["roadmap_ref"])
        return asdict(engine.resume())

    def resume(self) -> dict[str, Any]:
        return Session(
            store=self.store,
            adapter=self.task_adapter,
            select=self.select,
            prepare=self.prepare,
            reconcile_plan=self.reconcile_plan,
            final_resume=self.final_resume,
            maximum_tasks=self.maximum,
            initial_iterations=self.config["task_iterations"],
        ).resume()
