"""End-to-end synthetic pilot for the coordinator workflow (#239)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from tools import agent_coordinator_branch as branch
from tools import agent_coordinator_delivery as delivery
from tools import agent_coordinator_discovery as discovery
from tools import agent_coordinator_handover as handover
from tools import agent_coordinator_resume as resume
from tools import agent_orchestrate as workflow
from tools.agent_memory import RetrospectiveStore


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


class PilotGitHub:
    def __init__(self, sha: str) -> None:
        self.sha = sha
        self.head_sha = sha
        self.merge_sha = "c" * 40
        self.pr_state = "OPEN"
        self.issue_body = "Implement the synthetic pilot.\nPR CI succeeds.\n"

    def repository(self, _repository: str) -> dict[str, Any]:
        return {
            "nameWithOwner": "OWNER/repository",
            "defaultBranchRef": {"name": "main"},
        }

    def open_epics(self, _repository: str) -> list[dict[str, Any]]:
        return [self.issue(_repository, 232)]

    def issue(self, _repository: str, number: int) -> dict[str, Any]:
        if number == 232:
            return {
                "number": 232,
                "title": "synthetic epic",
                "state": "OPEN",
                "url": "https://example.invalid/issues/232",
                "labels": [{"name": "EPIC"}],
                "parent": None,
                "subIssues": {
                    "nodes": [
                        {
                            "number": 239,
                            "state": "OPEN",
                            "title": "test: synthetic pilot",
                            "url": "https://example.invalid/issues/239",
                        }
                    ],
                    "totalCount": 1,
                },
                "blockedBy": {"nodes": [], "totalCount": 0},
            }
        return {
            "number": 239,
            "title": "test: synthetic pilot",
            "state": "OPEN",
            "url": "https://example.invalid/issues/239",
            "labels": [],
            "parent": {"number": 232, "state": "OPEN"},
            "subIssues": {"nodes": [], "totalCount": 0},
            "blockedBy": {"nodes": [], "totalCount": 0},
            "body": self.issue_body,
        }

    def open_pull_requests(self, _repository: str) -> list[dict[str, Any]]:
        return []

    def branches(self, _repository: str) -> dict[str, str]:
        return {
            "main": self.sha,
            "roadmap/232-agent-coordinator": self.sha,
        }

    def compare(self, _repository: str, _base: str, _head: str) -> str:
        return "identical"

    def pull_request(self, _repository: str, _number: int) -> dict[str, Any]:
        merge = {"oid": self.merge_sha} if self.pr_state == "MERGED" else None
        return {
            "state": self.pr_state,
            "baseRefName": "roadmap/232-agent-coordinator",
            "baseRefOid": self.sha,
            "headRefName": "test/239-synthetic-pilot",
            "headRefOid": self.head_sha,
            "mergeCommit": merge,
            "files": [{"path": "pilot.txt"}],
        }

    def pr_checks(self, _repository: str, _number: int) -> list[dict[str, str]]:
        return self._checks()

    def commit_checks(self, _repository: str, _sha: str) -> list[dict[str, str]]:
        return self._checks()

    def ref_sha(self, _repository: str, _ref: str) -> str:
        return self.merge_sha

    @staticmethod
    def _checks() -> list[dict[str, str]]:
        return [
            {
                "name": name,
                "state": "SUCCESS",
                "link": f"https://example.invalid/checks/{name}",
            }
            for name in sorted(delivery.REQUIRED_CHECKS)
        ]


class PilotAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.executor_calls = 0
        self.controller_calls = 0
        self.trusted_policy = ""
        self.pending_delivery: list[str] = []

    def execute(self, request: dict[str, Any], _session_id: str) -> dict[str, Any]:
        self.executor_calls += 1
        contract = request["contract"]
        (self.root / "pilot.txt").write_text("synthetic result\n", encoding="utf-8")
        return {
            "schema_version": "1.0",
            "role": "executor",
            "task_issue": contract["issue"],
            "base_sha": contract["base_sha"],
            "head_sha": request["head_sha"],
            "status": "completed",
            "acceptance_criteria": [
                {
                    "requirement": "Implement the synthetic pilot.",
                    "status": "met",
                    "evidence": "synthetic fixture changed inside the allowlist",
                }
            ],
            "changed_files": ["pilot.txt"],
            "checks": [],
            "residual_risks": [],
            "stop_reason": "scope_complete",
        }

    def review(
        self,
        request: dict[str, Any],
        trusted_policy: dict[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        self.controller_calls += 1
        self.trusted_policy = trusted_policy["AGENTS.md"]
        self.pending_delivery = list(request["pending_delivery_criteria"])
        return {
            "schema_version": "1.0",
            "role": "controller",
            "task_issue": request["issue"],
            "base_sha": request["base_sha"],
            "head_sha": request["head_sha"],
            "review_basis": {
                "trust_source_kind": "base_sha",
                "head_policy_applied": False,
                "executor_self_assessment_treated_as_evidence_only": True,
            },
            "verdict": "PASS",
            "repair_iteration": request["repair_iteration"],
            "escalation_reason": None,
            "blocking_findings": [],
            "notes": ["PR CI remains an external delivery gate"],
        }


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Synthetic Pilot")
    _git(root, "config", "user.email", "pilot@example.invalid")
    _git(root, "remote", "add", "origin", "https://github.com/OWNER/repository.git")
    (root / "AGENTS.md").write_text("trusted policy\n", encoding="utf-8")
    (root / "CONTRIBUTING.md").write_text("trusted process\n", encoding="utf-8")
    (root / "pilot.txt").write_text("synthetic fixture\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "synthetic base")
    sha = _git(root, "rev-parse", "HEAD")
    _git(root, "branch", "roadmap/232-agent-coordinator", sha)
    _git(root, "update-ref", "refs/remotes/origin/main", sha)
    _git(root, "update-ref", "refs/remotes/origin/roadmap/232-agent-coordinator", sha)
    return root, sha


def _handover(sha: str, body: str) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "1.0",
        "repository": "OWNER/repository",
        "epic": 232,
        "task": 239,
        "criteria_source": {
            "kind": "github_issue",
            "issue": 239,
            "state": "OPEN",
            "body_sha256": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
        },
        "acceptance_criteria": [
            "Implement the synthetic pilot.",
            "PR CI succeeds.",
        ],
        "delivery_criterion_indices": [2],
        "base_ref": "roadmap/232-agent-coordinator",
        "base_sha": sha,
        "head_ref": "test/239-synthetic-pilot",
        "head_sha": sha,
        "causal_scope": ["synthetic coordinator pilot only"],
        "allowed_paths": ["pilot.txt"],
        "forbidden_actions": ["commit", "push", "create_pr", "comment", "merge"],
        "permissions": {
            "commit": False,
            "push": False,
            "create_pr": False,
            "comment": False,
            "merge": False,
        },
        "budgets": {
            "max_minutes": 10,
            "max_repair_iterations": 1,
            "max_report_chars": 20000,
        },
        "required_gate": {"profile": "repository-full", "version": "1"},
        "trusted_policy": {"source": "base_sha", "base_sha": sha},
        "task_class": "test",
        "approval": {"plan_digest": "pending"},
    }
    value["approval"]["plan_digest"] = handover.handover_digest(value)
    return value


def _gate(base_sha: str, head_sha: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = [
        {
            "id": check,
            "argv": [check],
            "cwd": "<snapshot-checkout>",
            "status": "passed",
            "exit_code": 0,
            "duration_seconds": 0.1,
            "summary": f"{check} passed in synthetic pilot",
            "metrics": {},
        }
        for check in ("pytest", "ruff", "mypy", "pre-commit")
    ]
    return {
        "schema_version": "1.0",
        "profile": "repository-full",
        "profile_version": "1",
        "status": "passed",
        "complete": True,
        "machine_code": "OK",
        "expected_checks": [item["id"] for item in checks],
        "executed_checks": [item["id"] for item in checks],
        "snapshot": {
            "base_sha": base_sha,
            "snapshot_method": "commit",
            "snapshot_commit": head_sha,
            "tree_hash": "d" * 64,
            "diff_sha256": "e" * 64,
            "provenance_complete": True,
        },
        "checks": checks,
    }


def test_full_coordinator_chain_reaches_done_only_after_post_merge_ci(
    tmp_path: Path,
) -> None:
    root, base_sha = _repository(tmp_path)
    github = PilotGitHub(base_sha)

    plan = discovery.discover(
        discovery.Options("OWNER/repository", 232, 239), github
    )
    assert plan.state == "PLAN_APPROVAL"
    assert plan.base_sha == base_sha

    prepared = branch.prepare_branch(
        branch.Options(
            "OWNER/repository",
            232,
            239,
            "main",
            base_sha,
            "roadmap/232-agent-coordinator",
            base_sha,
            "test/239-synthetic-pilot",
            "origin",
            root,
            True,
        ),
        github,
    )
    assert prepared.status == "CREATED"
    _git(root, "switch", "test/239-synthetic-pilot")

    value = _handover(base_sha, github.issue_body)
    adapter = PilotAdapter(root)

    def run_workflow(contract: Any) -> workflow.RunResult:
        return workflow.run(
            contract,
            root=root,
            storage=workflow.StateStore(tmp_path / "workflow-state"),
            adapter=adapter,
            gate=lambda _contract: {
                "status": "passed",
                "machine_code": "OK",
                "exit_code": 0,
            },
            memory=RetrospectiveStore(tmp_path / "workflow-memory", root),
        )

    run_result = handover.run_handover(
        value,
        root=root,
        approved_digest=value["approval"]["plan_digest"],
        runner=run_workflow,
        github=github,
    )
    assert run_result.status == "FAIL_ESCALATE"
    assert run_result.machine_code == "EXTERNAL_GATE_PENDING"
    assert adapter.executor_calls == adapter.controller_calls == 1
    assert adapter.trusted_policy == "trusted policy"
    assert adapter.pending_delivery == ["PR CI succeeds."]

    _git(root, "add", "pilot.txt")
    _git(root, "commit", "-m", "synthetic pilot result")
    head_sha = _git(root, "rev-parse", "HEAD")
    github.head_sha = head_sha
    evidence = _gate(base_sha, head_sha)
    options = delivery.Options(
        "pr",
        "OWNER/repository",
        232,
        239,
        246,
        "roadmap/232-agent-coordinator",
        base_sha,
        "test/239-synthetic-pilot",
        head_sha,
        ("pilot.txt",),
        None,
    )
    pr_result = delivery.assess(options, evidence, github)
    assert pr_result.task_status == "TASK READY FOR REVIEW"

    github.pr_state = "MERGED"
    post_merge = delivery.assess(
        delivery.Options(
            **{
                **options.__dict__,
                "phase": "post-merge",
                "merge_sha": github.merge_sha,
            }
        ),
        evidence,
        github,
    )
    assert post_merge.task_status == "TASK DONE"

    checkpoint = {
        "schema_version": "1.0",
        "repository": "OWNER/repository",
        "epic": 232,
        "task": 239,
        "base_ref": options.base_ref,
        "base_sha": base_sha,
        "head_ref": options.head_ref,
        "head_sha": head_sha,
        "handover_digest": value["approval"]["plan_digest"],
        "policy": {"source": "base_sha", "base_sha": base_sha},
        "approval": {"plan_digest": value["approval"]["plan_digest"]},
        "allowed_paths": ["pilot.txt"],
        "budgets": value["budgets"],
        "repair_iterations": 0,
        "completed_actions": [],
        "pending_action": None,
        "status": "READY",
    }
    live = {
        key: checkpoint[key]
        for key in (
            "repository",
            "epic",
            "task",
            "base_ref",
            "base_sha",
            "head_ref",
            "head_sha",
        )
    } | {"clean": True}
    store = resume.CheckpointStore(tmp_path / "private-state", root)
    first = resume.resume(
        checkpoint,
        live=live,
        store=store,
        action="handover_started",
        effect=lambda: None,
    )
    second = resume.resume(
        checkpoint,
        live=live,
        store=store,
        action="handover_started",
        effect=lambda: None,
    )
    assert first.status == "CONTINUE"
    assert second.status == "NO_OP"
