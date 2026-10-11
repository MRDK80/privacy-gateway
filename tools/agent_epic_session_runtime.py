"""Owner-installed concrete planner/task/final factories for an epic session."""

from __future__ import annotations

import copy
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tools import agent_coordinator_branch as branch
from tools import agent_coordinator_handover as handover
from tools import agent_epic_delivery as delivery
from tools import agent_epic_handover as planner
from tools import agent_epic_loop as loop
from tools import agent_epic_planning as admission
from tools import agent_epic_queue as queue
from tools import agent_epic_runtime_cli as guards
from tools import agent_orchestrate as workflow
from tools.agent_epic_bootstrap import BootstrapTask
from tools.agent_epic_final_runtime import FinalRuntime, digest
from tools.agent_epic_planning_roles import PlanningRoles
from tools.agent_epic_runtime import TaskPhaseRuntime
from tools.agent_epic_runtime_cli import managed_launch_guard, private_json
from tools.agent_epic_session import Session, structural_queue
from tools.agent_epic_wait_scope import Scope
from tools.agent_epic_workflow import ReviewedWorkflow, codex_role_adapter


class SessionRuntime:
    def __init__(
        self,
        state: Path,
        root: Path,
        config: Mapping[str, Any],
        *,
        wait_scope: Scope | None = None,
    ) -> None:
        self.state = state
        self.root = root
        self.config = config
        self.wait_scope = wait_scope
        if config.get("schema_version") in {"4.0", "5.0"} and wait_scope is None:
            raise loop.PhaseBlocked("WAIT_FRAME_REQUIRED")
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
        self._read_stage: str | None = None
        read = self._github_read if self.wait_scope is not None else None
        self.issues = queue.GhClient(read=read)
        self.github: branch.GitHubClient = branch.GitHubCLI(read=read)

    def _provenance_client(self) -> handover.GitHubCLI:
        if getattr(self, "wait_scope", None) is None:
            return handover.GitHubCLI()
        return handover.GitHubCLI(read=self._github_read)

    def _github_read(self, argv: Sequence[str]) -> str:
        assert self.wait_scope is not None
        return self.wait_scope.reader(
            self._read_stage or self._current_stage()
        ).command(argv)

    @contextmanager
    def _stage(self, stage: str) -> Iterator[None]:
        previous = self._read_stage
        self._read_stage = stage
        try:
            if getattr(self, "wait_scope", None) is not None:
                assert self.wait_scope is not None
                with self.wait_scope.context(stage):
                    yield
            else:
                yield
        finally:
            self._read_stage = previous

    def _task_stage(
        self, store: loop.CheckpointStore, *, reconcile: bool = False
    ) -> str:
        saved = store.load()
        if saved is None:
            raise loop.LoopError("CHECKPOINT_NOT_FOUND")
        prefix = "RECONCILE" if reconcile else "TASK"
        return f"{prefix}:{saved['task']}:{saved['pending_phase'] or saved['phase']}"

    def _current_stage(self) -> str:
        seed = self.store.load()
        if seed is None:
            raise loop.LoopError("CHECKPOINT_NOT_FOUND")
        cursor = workflow.StateStore(self.state / "session-control").load("cursor")
        task = cursor["current_task"] if cursor is not None else seed["task"]
        if (
            type(task) is not int
            or task <= 0
            or (
                cursor is not None
                and any(
                    cursor[key] != seed[key]
                    for key in ("repository", "epic", "policy_sha")
                )
            )
        ):
            raise loop.LoopError("SESSION_SCOPE_CHANGED")
        directory = (
            self.state if task == seed["task"] else self.state / "tasks" / str(task)
        )
        return self._task_stage(loop.CheckpointStore(directory, self.root))

    def authority(
        self, iterations: int
    ) -> tuple[dict[str, Any], str, delivery.MandateContext]:
        managed_launch_guard(self.state, self.root)
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
        options: dict[str, Any] = {}
        if self.config.get("schema_version") == "5.0":
            options = {"quota_state": self.state, "wait_scope": self.wait_scope}
        return codex_role_adapter(
            policy_root=Path(self.config["policy_root"]),
            root=self.root,
            policy_sha=self.identity["policy_sha"],
            **options,
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
        recipe = self._approved_recipe(task)
        if recipe is not None:
            return recipe
        if guards.planning_enabled(self.config):
            return admission.Admission(self.state, self.root).recipe(
                task, self.config["approved_planning_digest"]
            )
        raise loop.PhaseBlocked("NEXT_TASK_PLAN_REQUIRED")

    def _approved_recipe(self, task: int) -> Mapping[str, Any] | None:
        current = private_json(self.state / "approved-plans.json")
        if digest(current) != self.config["approved_plans_digest"]:
            raise loop.PhaseBlocked("APPROVED_PLANS_CHANGED")
        recipe = current["tasks"].get(str(task))
        if recipe is None:
            return None
        if not isinstance(recipe, Mapping) or set(recipe) != {"plan", "demo_plan"}:
            raise loop.PhaseBlocked("NEXT_TASK_PLAN_REQUIRED")
        return recipe

    def _planning_owner(self, mandate: Mapping[str, Any]) -> Mapping[str, Any]:
        return admission.profile(
            private_json(self.state / "planning-profile.json"),
            approved=self.config["approved_planning_digest"],
            mandate=mandate,
        )

    def _dependency_facts(
        self, task: int, *, closed: bool = False
    ) -> list[dict[str, Any]]:
        """Resolve each structural blocker freshly; queue selection is no receipt."""
        try:
            issue = self.issues.issue(self.identity["repository"], task)
            parent = issue.get("parent")
            if (
                type(issue.get("number")) is not int
                or issue["number"] != task
                or issue.get("state") != ("CLOSED" if closed else "OPEN")
                or not isinstance(parent, Mapping)
                or type(parent.get("number")) is not int
                or parent["number"] != self.identity["epic"]
            ):
                raise ValueError
            facts = queue.dependency_facts(
                issue, repository=self.identity["repository"]
            )
            for fact in facts:
                dependency = self.issues.issue(
                    self.identity["repository"], fact["number"]
                )
                if (
                    type(dependency.get("number")) is not int
                    or dependency["number"] != fact["number"]
                    or dependency.get("state") != fact["state"]
                    or queue.dependency_facts(
                        {"blockedBy": {"nodes": [dependency]}},
                        repository=self.identity["repository"],
                    )
                    != [fact]
                ):
                    raise ValueError
                if fact["state"] != "CLOSED":
                    raise loop.PhaseBlocked("TASK_DEPENDENCIES_BLOCKED")
            return facts
        except (queue.GitHubError, KeyError, TypeError, ValueError):
            raise loop.PhaseBlocked("TASK_DEPENDENCIES_INVALID") from None

    def _confirm_admission(
        self,
        task: int,
        base: str,
        default: str,
        iterations: int,
        *,
        closed_guard: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
        mandate, _, context = self._fresh_authority(iterations)
        issue = self._provenance_client().issue(mandate["repository"], task)
        body = issue.get("body")
        parent = issue.get("parent")
        if (
            issue.get("number") != task
            or not (
                issue.get("state") == "OPEN"
                or (
                    issue.get("state") == "CLOSED"
                    and closed_guard is not None
                    and closed_guard()
                )
            )
            or not isinstance(parent, Mapping)
            or parent.get("number") != mandate["epic"]
            or not isinstance(body, str)
        ):
            raise loop.PhaseBlocked("CRITERIA_PROVENANCE_MISMATCH")
        self._dependency_facts(task, closed=issue["state"] == "CLOSED")
        if self.github.branches(mandate["repository"]).get("main") != default:
            raise loop.PhaseBlocked("PLANNING_ADMISSION_CHANGED")
        return admission.Admission(self.state, self.root).confirm(
            owner=self._planning_owner(mandate),
            mandate=mandate,
            context=context,
            task=task,
            body=body,
            base=base,
            default=default,
        )

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
            with self._stage(
                engine._read_stage
                or self._task_stage(store, reconcile=engine._reconciling)
            ):
                return task_authority()

        def task_authority() -> tuple[dict[str, Any], str, delivery.MandateContext]:
            if (
                guards.planning_enabled(self.config)
                and self._approved_recipe(saved["task"]) is None
            ):
                recipe = admission.Admission(self.state, self.root).recipe(
                    saved["task"], self.config["approved_planning_digest"]
                )
                checked = self._confirm_admission(
                    saved["task"],
                    saved["base_sha"],
                    recipe["plan"]["default_sha"],
                    iterations,
                    closed_guard=engine._bound_close_intent,
                )
                current = private_json(store.directory / "handover.json")
                checkpoint = store.load()
                plan = checked["plan"]
                if (
                    handover.handover_digest(current) != approved
                    or checkpoint is None
                    or any(
                        checkpoint[key] != plan[key]
                        for key in (
                            "repository",
                            "epic",
                            "task",
                            "base_ref",
                            "base_sha",
                            "head_ref",
                            "policy_sha",
                        )
                    )
                    or any(
                        current.get(key) != plan[key]
                        for key in (
                            "repository",
                            "epic",
                            "task",
                            "criteria_source",
                            "acceptance_criteria",
                            "delivery_criterion_indices",
                            "base_ref",
                            "base_sha",
                            "head_ref",
                            "causal_scope",
                            "allowed_paths",
                            "budgets",
                            "task_class",
                        )
                    )
                    or current.get("trusted_policy")
                    != {
                        "source": "pinned_policy_sha",
                        "policy_sha": plan["policy_sha"],
                    }
                    or current.get("mandate_provenance")
                    != {
                        "schema_version": "3.0",
                        "digest": self.config["approved_mandate_digest"],
                        "policy_sha": plan["policy_sha"],
                    }
                ):
                    raise loop.PhaseBlocked("PLANNING_ADMISSION_CHANGED")
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
            wait_scope=getattr(self, "wait_scope", None),
        )
        engine.continuation_enabled = True
        if guards.planning_enabled(self.config):

            def continuation_queue() -> queue.QueuePlan:
                with self._stage(self._task_stage(store)):
                    return self._planning_queue()

            engine.continuation_queue = continuation_queue
        engine.bootstrap = BootstrapTask(engine, reviewed, authority)
        return engine

    def select(self) -> queue.QueuePlan:
        if getattr(self, "wait_scope", None) is not None:
            with self._stage(self._current_stage()):
                return self._select()
        return self._select()

    def _select(self) -> queue.QueuePlan:
        if guards.planning_enabled(self.config):
            return self._planning_queue()
        return structural_queue(
            self.issues,
            repository=self.identity["repository"],
            epic=self.identity["epic"],
            approved_order=tuple(self.config["approved_order"]),
        )

    def _planning_queue(self) -> queue.QueuePlan:
        if getattr(self, "wait_scope", None) is not None and self._read_stage is None:
            with self._stage(self._current_stage()):
                return self._planning_queue_once()
        return self._planning_queue_once()

    def _planning_queue_once(self) -> queue.QueuePlan:
        cursor = workflow.StateStore(self.state / "session-control").load("cursor")
        completed = cursor["completed_tasks"] if cursor is not None else []
        iterations = self.config["task_iterations"] + len(completed)
        mandate, _, context = self._fresh_authority(iterations, at_task_start=False)
        owner = self._planning_owner(mandate)
        order = list(self.config["approved_order"])
        followups = sorted(
            int(number) for number in owner["tasks"] if int(number) not in order
        )
        # Bound the entire approved follow-up set, so structural disappearance
        # cannot refund permission to discover another ungranted issue.
        if (
            self.config["follow_up_issues"] + len(followups)
            > mandate["limits"]["max_follow_up_issues"]
        ):
            raise loop.PhaseBlocked("MANDATE_FOLLOW_UP_LIMIT")
        order.extend(followups)
        parent = self.issues.issue(mandate["repository"], mandate["epic"])
        children = parent.get("subIssues")
        nodes = children.get("nodes") if isinstance(children, Mapping) else None
        if parent.get("number") != mandate["epic"] or not isinstance(nodes, list):
            raise loop.PhaseBlocked("ISSUE_MISMATCH")
        tasks: list[dict[str, Any]] = []
        seen: set[int] = set()
        for node in nodes:
            number = node.get("number") if isinstance(node, Mapping) else None
            if type(number) is not int or number <= 0:
                raise loop.PhaseBlocked("ISSUE_MISMATCH")
            if number in seen:
                continue
            seen.add(number)
            child = self.issues.issue(mandate["repository"], number)
            child_parent = child.get("parent")
            if (
                child.get("number") != number
                or not isinstance(child_parent, Mapping)
                or child_parent.get("number") != mandate["epic"]
            ):
                raise loop.PhaseBlocked("PARENT_MISMATCH")
            if child.get("state") == "OPEN" and (
                number not in order
                or str(number) not in mandate["task_grants"]
                or (number in followups and str(number) not in owner["tasks"])
            ):
                raise loop.PhaseBlocked("PLANNING_TASK_NOT_APPROVED")
            tasks.append(child)
        result = queue.plan_queue(
            epic=mandate["epic"], approved_order=order, tasks=tasks
        )
        if result.state not in {"RUN_TASK", "FINAL_GATE"}:
            raise loop.PhaseBlocked(result.machine_code)
        if result.state == "RUN_TASK":
            code = delivery.mandate_lifecycle_code(mandate, context, at_task_start=True)
            if code is not None:
                raise loop.PhaseBlocked(code)
        return result

    def prepare(
        self,
        previous: Mapping[str, Any],
        task: int,
        store: loop.CheckpointStore,
        iterations: int,
    ) -> None:
        if self.config.get("schema_version") in {"4.0", "5.0"}:
            with self._stage(f"SUCCESSOR:{previous['task']}:{task}"):
                self._prepare(previous, task, store, iterations)
            return
        self._prepare(previous, task, store, iterations)

    def _prepare(
        self,
        previous: Mapping[str, Any],
        task: int,
        store: loop.CheckpointStore,
        iterations: int,
    ) -> None:
        recipe = self._approved_recipe(task)
        derived = recipe is None
        if derived and not guards.planning_enabled(self.config):
            raise loop.PhaseBlocked("NEXT_TASK_PLAN_REQUIRED")
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
        issue = self._provenance_client().issue(mandate["repository"], task)
        body = issue.get("body")
        if not isinstance(body, str):
            raise loop.PhaseBlocked("CRITERIA_PROVENANCE_MISMATCH")
        if derived:
            owner = self._planning_owner(mandate)
            dependencies = self._dependency_facts(task)

            def planning_guard() -> None:
                fresh, _, _ = self._fresh_authority(iterations)
                if self._dependency_facts(task) != dependencies:
                    raise loop.PhaseBlocked("PLANNING_EVIDENCE_CHANGED")
                current_issue = self._provenance_client().issue(
                    fresh["repository"], task
                )
                current_refs = self.github.branches(fresh["repository"])
                parent = current_issue.get("parent")
                if (
                    current_issue.get("number") != task
                    or current_issue.get("state") != "OPEN"
                    or not isinstance(parent, Mapping)
                    or parent.get("number") != fresh["epic"]
                    or current_issue.get("body") != body
                    or current_refs.get(fresh["roadmap_ref"]) != base
                    or current_refs.get("main") != main
                    or digest(self._planning_owner(fresh)) != digest(owner)
                    or handover._repository_identity(self.root) != fresh["repository"]
                    or handover._git(self.root, "status", "--porcelain=v1", "-z")
                ):
                    raise loop.PhaseBlocked("PLANNING_EVIDENCE_CHANGED")

            proposal = admission.Admission(self.state, self.root).admit(
                owner=owner,
                mandate=mandate,
                context=context,
                task=task,
                body=body,
                base=base,
                default=main,
                dependencies=dependencies,
                guard=planning_guard,
                invoke=PlanningRoles(
                    root=self.root,
                    policy_root=Path(self.config["policy_root"]),
                    policy_sha=mandate["policy_sha"],
                ),
            )
            recipe = {"plan": proposal["plan"], "demo_plan": proposal["demo_plan"]}
        assert recipe is not None
        plan = copy.deepcopy(dict(recipe["plan"]))
        if not derived:
            plan.update(base_sha=base, default_sha=main)
        if (
            plan.get("schema_version") != "2.0"
            or plan.get("task") != task
            or plan.get("default_ref") != "main"
            or plan.get("policy_sha") != mandate["policy_sha"]
        ):
            raise loop.PhaseBlocked("NEXT_TASK_PLAN_REQUIRED")
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
                or handover._git(self.root, "rev-parse", "HEAD") != previous["head_sha"]
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
            if derived:
                if self._dependency_facts(task) != dependencies:
                    raise loop.PhaseBlocked("PLANNING_EVIDENCE_CHANGED")
                checked = self._confirm_admission(task, base, main, iterations)
                if checked["plan"] != plan:
                    raise loop.PhaseBlocked("PLANNING_ADMISSION_CHANGED")

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
        with self._stage(f"RECONCILE:{task}:PLAN"):
            return self._reconcile_plan(previous, task, store)

    def _reconcile_plan(
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
        with self._stage("FINAL:FINAL_GATE"):
            return self._final_resume(iterations)

    def _final_resume(self, iterations: int) -> Mapping[str, Any]:
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
            wait_scope=self.wait_scope,
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

    def resume(
        self, *, supervisor_lock: loop.RunnerLock | None = None,
    ) -> dict[str, Any]:
        return Session(
            store=self.store,
            adapter=self.task_adapter,
            select=self.select,
            prepare=self.prepare,
            reconcile_plan=self.reconcile_plan,
            final_resume=self.final_resume,
            maximum_tasks=self.maximum,
            initial_iterations=self.config["task_iterations"],
        ).resume(**(
            {"supervisor_lock": supervisor_lock} if supervisor_lock is not None else {}
        ))
