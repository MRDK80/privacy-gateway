"""Coordinator handover validation and workflow invocation tests (#236)."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "agent_coordinator_handover.py"
)
SPEC = importlib.util.spec_from_file_location("agent_coordinator_handover", MODULE_PATH)
assert SPEC and SPEC.loader
handover = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = handover
SPEC.loader.exec_module(handover)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "remote", "add", "origin", "https://github.com/OWNER/repository.git")
    (root / "AGENTS.md").write_text("policy\n", encoding="utf-8")
    (root / "CONTRIBUTING.md").write_text("process\n", encoding="utf-8")
    (root / "target.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "roadmap/232-agent-coordinator")
    _git(root, "switch", "-c", "feat/236-agent-coordinator-handover")
    return root, _git(root, "rev-parse", "HEAD")


BODY = "implement handover\nPR CI succeeds\n"


class FakeGitHub:
    def issue(self, repository: str, number: int) -> dict[str, Any]:
        return {
            "number": number,
            "state": "OPEN",
            "parent": {"number": 232},
            "body": BODY,
        }


def _value(sha: str) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "1.0",
        "repository": "OWNER/repository",
        "epic": 232,
        "task": 236,
        "criteria_source": {
            "kind": "github_issue",
            "issue": 236,
            "state": "OPEN",
            "body_sha256": "sha256:" + hashlib.sha256(BODY.encode()).hexdigest(),
        },
        "acceptance_criteria": ["implement handover", "PR CI succeeds"],
        "delivery_criterion_indices": [2],
        "base_ref": "roadmap/232-agent-coordinator",
        "base_sha": sha,
        "head_ref": "feat/236-agent-coordinator-handover",
        "head_sha": sha,
        "causal_scope": ["coordinator handover only"],
        "allowed_paths": ["target.py"],
        "forbidden_actions": ["commit", "push", "create_pr", "comment", "merge"],
        "permissions": {
            "commit": False,
            "push": False,
            "create_pr": False,
            "comment": False,
            "merge": False,
        },
        "budgets": {
            "max_minutes": 60,
            "max_repair_iterations": 2,
            "max_report_chars": 20000,
        },
        "required_gate": {"profile": "repository-full", "version": "1"},
        "trusted_policy": {"source": "base_sha", "base_sha": sha},
        "task_class": "feature",
        "approval": {"plan_digest": "pending"},
    }
    value["approval"]["plan_digest"] = handover.handover_digest(value)
    return value


def test_valid_handover_invokes_existing_workflow_with_narrow_contract(
    tmp_path: Path,
) -> None:
    root, sha = _repository(tmp_path)
    value = _value(sha)
    called = []

    def runner(contract: Any) -> Any:
        called.append(contract)
        return handover.workflow.RunResult("PASS", "OK", 0, "run-1")

    result = handover.run_handover(
        value,
        root=root,
        approved_digest=value["approval"]["plan_digest"],
        runner=runner,
        github=FakeGitHub(),
    )

    assert result.status == "PASS"
    assert len(called) == 1
    assert called[0].allowed_paths == ("target.py",)
    assert called[0].delivery_criterion_indices == (2,)
    assert called[0].permissions == handover.workflow.ActionPermissions()


def test_changed_scope_requires_new_approval_and_never_invokes_workflow(
    tmp_path: Path,
) -> None:
    root, sha = _repository(tmp_path)
    value = _value(sha)
    approval = value["approval"]["plan_digest"]
    value["allowed_paths"].append("extra.py")
    calls: list[Any] = []

    result = handover.run_handover(
        value,
        root=root,
        approved_digest=approval,
        runner=calls.append,
        github=FakeGitHub(),
    )

    assert result.machine_code == "APPROVAL_MISMATCH"
    assert calls == []


def test_dirty_tree_blocks_before_workflow(tmp_path: Path) -> None:
    root, sha = _repository(tmp_path)
    value = _value(sha)
    (root / "target.py").write_text("value = 2\n", encoding="utf-8")
    calls: list[Any] = []

    result = handover.run_handover(
        value,
        root=root,
        approved_digest=value["approval"]["plan_digest"],
        runner=calls.append,
        github=FakeGitHub(),
    )

    assert result.machine_code == "DIRTY_WORKTREE"
    assert calls == []


def test_stale_head_and_permission_expansion_fail_closed(tmp_path: Path) -> None:
    root, sha = _repository(tmp_path)
    value = _value(sha)
    value["head_sha"] = "f" * 40
    value["approval"]["plan_digest"] = handover.handover_digest(value)

    stale = handover.run_handover(
        value,
        root=root,
        approved_digest=value["approval"]["plan_digest"],
        runner=lambda contract: handover.workflow.RunResult("PASS", "OK", 0, "bad"),
        github=FakeGitHub(),
    )
    assert stale.machine_code == "STALE_HEAD"

    value = _value(sha)
    value["permissions"]["push"] = True
    value["approval"]["plan_digest"] = handover.handover_digest(value)
    expanded = handover.run_handover(
        value,
        root=root,
        approved_digest=value["approval"]["plan_digest"],
        runner=lambda contract: handover.workflow.RunResult("PASS", "OK", 0, "bad"),
        github=FakeGitHub(),
    )
    assert expanded.machine_code == "PERMISSION_EXPANSION"


def test_wrong_gate_or_malformed_contract_never_invokes_workflow(
    tmp_path: Path,
) -> None:
    root, sha = _repository(tmp_path)
    value = _value(sha)
    value["required_gate"]["profile"] = "diff-only"
    value["approval"]["plan_digest"] = handover.handover_digest(value)
    calls: list[Any] = []

    result = handover.run_handover(
        value,
        root=root,
        approved_digest=value["approval"]["plan_digest"],
        runner=calls.append,
        github=FakeGitHub(),
    )
    assert result.machine_code == "GATE_INVALID"
    assert calls == []

    del value["causal_scope"]
    value["approval"]["plan_digest"] = handover.handover_digest(value)
    malformed = handover.run_handover(
        value,
        root=root,
        approved_digest=value["approval"]["plan_digest"],
        runner=calls.append,
        github=FakeGitHub(),
    )
    assert malformed.machine_code == "HANDOVER_INVALID"
    assert calls == []
