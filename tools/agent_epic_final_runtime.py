"""Concrete final gate/review and final driver, with SHA-bound owner approval."""

from __future__ import annotations

import hashlib
import json
import platform
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tools import agent_coordinator_delivery as checks
from tools import agent_coordinator_handover as handover
from tools import agent_epic_delivery as delivery
from tools import agent_epic_final as final
from tools import agent_epic_final_delivery as driver
from tools import agent_epic_loop as loop
from tools import agent_epic_queue as queue
from tools import agent_gate
from tools import agent_orchestrate as workflow
from tools import agent_verified_lessons as lessons
from tools.agent_epic_final_transport import GhFinalTransport

APPROVAL_FIELDS = {
    "schema_version",
    "repository",
    "epic",
    "roadmap_ref",
    "roadmap_sha",
    "main_sha",
    "policy_sha",
    "owner_identity",
    "demo_confirmed_by_owner",
    "lesson_digest",
}


def digest(value: Mapping[str, Any]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
    )


class FinalRuntime:
    def __init__(
        self,
        *,
        root: Path,
        directory: Path,
        authority: driver.Authority,
        approval: Callable[[], Mapping[str, Any]],
        approved_digest: str,
        lesson: Callable[[], Mapping[str, Any]],
        adapter: workflow.AgentAdapter,
    ) -> None:
        self.root = root
        self.directory = directory
        self.authority = authority
        self.approval = approval
        self.approved_digest = approved_digest
        self.lesson = lesson
        self.adapter = adapter
        self.github = checks.GhClient()
        self.issues = queue.GhClient()
        self.transport = GhFinalTransport(
            root,
            reporting=lambda: self._prepare(self._request("create_roadmap_pr")),
            before_write=self._before_transport_write,
        )
        self.storage = workflow.StateStore(directory / "final-artifacts")
        self.ledger = delivery.Ledger(directory / "final-ledger", root)

    def _owner(self) -> Mapping[str, Any]:
        value = self.approval()
        if set(value) != APPROVAL_FIELDS or value.get("schema_version") != "1.0":
            raise loop.PhaseBlocked("FINAL_APPROVAL_INVALID")
        if digest(value) != self.approved_digest:
            raise loop.PhaseBlocked("FINAL_APPROVAL_MISMATCH")
        mandate, _, context = self.authority()
        if (
            value["owner_identity"] != context.owner_identity
            or value["owner_identity"] != mandate["owner_identity"]
        ):
            raise loop.PhaseBlocked("MANDATE_OWNER_MISMATCH")
        if value["demo_confirmed_by_owner"] is not True:
            raise loop.PhaseBlocked("FINAL_DEMO_UNCONFIRMED")
        record = self.lesson()
        if digest(record) != value["lesson_digest"]:
            raise loop.PhaseBlocked("PROMPT_LESSON_GATE_FAILED")
        try:
            verified = lessons.validate_candidate(record)
        except lessons.LessonError:
            raise loop.PhaseBlocked("PROMPT_LESSON_GATE_FAILED") from None
        if verified.epic != value["epic"]:
            raise loop.PhaseBlocked("PROMPT_LESSON_GATE_FAILED")
        return value

    def _request(
        self, operation: str, *, pr: int | None = None, merge: str | None = None
    ) -> driver.Request:
        value = self._owner()
        identity = {
            key: value[key]
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "roadmap_sha",
                "main_sha",
                "policy_sha",
            )
        }
        return driver.Request(
            operation_id=digest(
                {**identity, "operation": operation, "pr": pr, "merge_sha": merge}
            ),
            operation=operation,
            **identity,
            pr=pr,
            merge_sha=merge,
        )

    def _children(self, request: driver.Request) -> list[int]:
        epic = self.issues.issue(request.repository, request.epic)
        connection = epic.get("subIssues")
        nodes = connection.get("nodes") if isinstance(connection, Mapping) else None
        if epic.get("number") != request.epic or not isinstance(nodes, list):
            raise loop.PhaseBlocked("ISSUE_MISMATCH")
        opened = []
        numbers = set()
        for node in nodes:
            number = node.get("number") if isinstance(node, Mapping) else None
            if type(number) is not int or number <= 0 or number in numbers:
                raise loop.PhaseBlocked("ISSUE_MISMATCH")
            numbers.add(number)
            child = self.issues.issue(request.repository, number)
            parent = child.get("parent")
            if (
                child.get("number") != number
                or not isinstance(parent, Mapping)
                or parent.get("number") != request.epic
            ):
                raise loop.PhaseBlocked("ISSUE_MISMATCH")
            if child.get("state") == "OPEN":
                opened.append(number)
            elif child.get("state") != "CLOSED":
                raise loop.PhaseBlocked("ISSUE_MISMATCH")
        return opened

    def _facts(self, request: driver.Request) -> tuple[str, str, list[int]]:
        if handover._repository_identity(self.root) != request.repository:
            raise loop.PhaseBlocked("REPOSITORY_MISMATCH")
        try:
            handover._git(
                self.root,
                "merge-base",
                "--is-ancestor",
                request.policy_sha,
                request.roadmap_sha,
            )
            handover._git(
                self.root,
                "merge-base",
                "--is-ancestor",
                request.main_sha,
                request.roadmap_sha,
            )
        except handover.HandoverError:
            raise loop.PhaseBlocked("ANCESTRY_UNCONFIRMED") from None
        return (
            self.github.ref_sha(request.repository, request.roadmap_ref),
            self.github.ref_sha(request.repository, "main"),
            self._children(request),
        )

    def _prepare(self, request: driver.Request) -> Mapping[str, Any]:
        key = hashlib.sha256(self.approved_digest.encode()).hexdigest()
        saved = self.storage.load(key)
        if saved:
            return saved
        if (
            handover._git(self.root, "status", "--porcelain=v1", "-z")
            or handover._git(self.root, "rev-parse", "HEAD") != request.roadmap_sha
        ):
            raise loop.PhaseBlocked("FINAL_WORKTREE_INVALID")
        paths = tuple(
            handover._git(
                self.root, "diff", "--name-only", request.main_sha, request.roadmap_sha
            ).splitlines()
        )
        if not paths:
            raise loop.PhaseBlocked("FINAL_DIFF_EMPTY")
        with agent_gate.trusted_snapshot_session(
            root=self.root, base_sha=request.main_sha, allowed_paths=paths
        ) as session:
            agent_gate.verify_environment(session)
            gate = agent_gate.build_evidence(session, agent_gate.run_profile(session))
            negative = agent_gate._default_runner(
                ("pytest", "-q", "tests/test_agent_verified_lessons.py"),
                session.checkout,
                300,
            )
            metrics, parsed = agent_gate._parse_pytest(negative.output)
            negative_passed = (
                negative.exit_code == 0 and parsed and metrics.get("skipped", 0) == 0
            )
        if not agent_gate.gate_evidence_is_passing(gate) or not negative_passed:
            raise loop.PhaseBlocked("FINAL_LOCAL_GATE_FAILED")
        if gate["snapshot"]["tree_hash"] != handover._git(
            self.root, "rev-parse", f"{request.roadmap_sha}^{{tree}}"
        ):
            raise loop.PhaseBlocked("FINAL_TREE_MISMATCH")
        criteria = (
            "Review the cumulative roadmap diff, security invariants "
            "and final delivery contract",
        )
        contract = workflow.TaskContract(
            issue=request.epic,
            epic=request.epic,
            acceptance_criteria=criteria,
            base_ref="main",
            base_sha=request.main_sha,
            head_ref=request.roadmap_ref,
            allowed_paths=paths,
            max_repair_iterations=0,
            policy_sha=request.policy_sha,
        )
        cumulative_diff = handover._git(
            self.root, "diff", "--no-ext-diff", request.main_sha, request.roadmap_sha
        )
        if len(cumulative_diff) > 200_000:
            raise loop.PhaseBlocked("FINAL_DIFF_TOO_LARGE")
        review = {
            "issue": request.epic,
            "acceptance_criteria": list(criteria),
            "base_sha": request.main_sha,
            "head_sha": request.roadmap_sha,
            "repair_iteration": 0,
            "gate_evidence": gate,
            "reviewed_state": {"snapshot_commit": gate["snapshot"]["snapshot_commit"]},
            "diff": cumulative_diff,
            "contract": {
                "issue": request.epic,
                "epic": request.epic,
                "acceptance_criteria": list(criteria),
                "base_ref": "main",
                "base_sha": request.main_sha,
                "head_ref": request.roadmap_ref,
                "allowed_paths": list(paths),
                "policy_sha": request.policy_sha,
                "permissions": asdict(contract.permissions),
                "max_repair_iterations": 0,
                "remaining_repair_iterations": 0,
                "max_minutes": 60,
                "task_class": "final-review",
            },
        }
        verdict = self.adapter.review(
            review, workflow._load_trusted_policy(self.root, contract), key
        )
        if workflow._validate_verdict(verdict, contract, request.roadmap_sha) not in {
            "PASS",
            "PASS_WITH_NOTES",
        }:
            raise loop.PhaseBlocked("FINAL_DIFF_REVIEW_REQUIRED")
        saved = {
            "gate": gate,
            "environment": {"python_version": platform.python_version()},
            "review_verdict": verdict["verdict"],
            "reviewed_sha": request.roadmap_sha,
            "policy_sha": request.policy_sha,
            "negative_tests_passed": True,
        }
        self.storage.save(key, saved)
        return saved

    def gate(self, request: driver.Request) -> final.FinalGate:
        try:
            owner = self._owner()
            if any(
                owner[key] != getattr(request, key)
                for key in (
                    "repository",
                    "epic",
                    "roadmap_ref",
                    "roadmap_sha",
                    "main_sha",
                    "policy_sha",
                )
            ):
                raise loop.PhaseBlocked("FINAL_APPROVAL_MISMATCH")
            roadmap, main, opened = self._facts(request)
            if roadmap != request.roadmap_sha or opened:
                raise loop.PhaseBlocked(
                    "OPEN_REQUIRED_TASKS" if opened else "ROADMAP_SHA_CHANGED"
                )
            if request.operation in driver.POST_MERGE:
                info = self.transport.pull_request(request)
                merge = info.get("mergeCommit")
                if (
                    not isinstance(merge, Mapping)
                    or merge.get("oid") != request.merge_sha
                ):
                    raise loop.PhaseBlocked("MAIN_MERGE_IDENTITY_CHANGED")
                return final.assess_main_post_merge(
                    merge_sha=request.merge_sha or "",
                    live_main_sha=main,
                    pr_state=str(info.get("state")),
                    checks=self.github.commit_checks(
                        request.repository, request.merge_sha or ""
                    ),
                )
            if main != request.main_sha:
                raise loop.PhaseBlocked("MAIN_CHANGED")
            evidence = self._prepare(request)
            snapshot = evidence.get("gate", {}).get("snapshot", {})
            ready = final.assess_final_gate(
                epic=request.epic,
                roadmap_ref=request.roadmap_ref,
                roadmap_sha=request.roadmap_sha,
                live_roadmap_sha=roadmap,
                trusted_main_sha=request.main_sha,
                live_main_sha=main,
                open_children=opened,
                local_gate={
                    command: agent_gate.gate_evidence_is_passing(evidence["gate"])
                    for command in final.LOCAL_GATE
                },
                diff_reviewed=evidence.get("reviewed_sha") == request.roadmap_sha
                and evidence.get("policy_sha") == request.policy_sha
                and snapshot.get("base_sha") == request.main_sha
                and snapshot.get("tree_hash")
                == handover._git(
                    self.root, "rev-parse", f"{request.roadmap_sha}^{{tree}}"
                ),
                prompt_lesson_verified=True,
                prompt_injection_tests_passed=evidence.get("negative_tests_passed")
                is True,
                demo_confirmed_by_owner=owner["demo_confirmed_by_owner"] is True,
            )
            if request.operation == "merge_roadmap_pr" and ready.status == "PASS":
                info = self.transport.pull_request(request)
                return final.assess_roadmap_pr(
                    ready,
                    expected_roadmap_sha=request.roadmap_sha,
                    pr_head_sha=str(info.get("headRefOid")),
                    base_ref=str(info.get("baseRefName")),
                    state=str(info.get("state")),
                    checks=self.github.pr_checks(request.repository, request.pr or 0),
                )
            return ready
        except loop.PhaseBlocked as error:
            return final.FinalGate("BLOCKED", error.machine_code, "BLOCKED")

    def _before_transport_write(self, request: driver.Request) -> None:
        checked = self.gate(request)
        expected = (
            "ROADMAP DONE"
            if request.operation in driver.POST_MERGE
            else "ROADMAP READY FOR RELEASE"
        )
        if (
            checked.status != "PASS"
            or checked.machine_code != "OK"
            or checked.roadmap_status != expected
            or driver.authorization_code(request, self.authority) is not None
        ):
            raise delivery.OutcomeUnknown

    def resume(self) -> delivery.Result:
        pr = None
        merge = None
        for operation in (
            "create_roadmap_pr",
            "merge_roadmap_pr",
            "close_epic",
            "update_epic",
        ):
            request = self._request(operation, pr=pr, merge=merge)
            result = driver.deliver(
                request,
                authority=self.authority,
                gate=self.gate,
                ledger=self.ledger,
                effect=self.transport.effect,
                reconcile=self.transport.reconcile,
            )
            if result.status not in {"APPLIED", "NO_OP"}:
                return result
            if operation == "create_roadmap_pr":
                value = result.receipt.get("pr") if result.receipt else None
                if type(value) is not int or value <= 0:
                    raise loop.LoopError("FINAL_RECEIPT_INVALID")
                pr = value
            if operation == "merge_roadmap_pr":
                value = result.receipt.get("merge_sha") if result.receipt else None
                if (
                    not isinstance(value, str)
                    or delivery.SHA_RE.fullmatch(value) is None
                ):
                    raise loop.LoopError("FINAL_RECEIPT_INVALID")
                merge = value
        return delivery.Result("ROADMAP_DONE", "OK", {"pr": pr, "merge_sha": merge})
