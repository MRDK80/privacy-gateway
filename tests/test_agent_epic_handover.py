"""Epic mandate to narrow coordinator handover tests (#251)."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "agent_epic_handover.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_epic_handover", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = _load_module()


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repository"
    root.mkdir(parents=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "remote", "add", "origin", "https://github.com/OWNER/repo.git")
    (root / "tools").mkdir()
    (root / "tests").mkdir()
    (root / "tools" / "existing.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("policy\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    sha = _git(root, "rev-parse", "HEAD")
    _git(root, "branch", "roadmap/248-runner", sha)
    _git(root, "update-ref", "refs/remotes/origin/main", sha)
    _git(root, "update-ref", "refs/remotes/origin/roadmap/248-runner", sha)
    return root, sha


BODY = "minimal handover\nexact gates\n"


def test_mandate_migration_invalidates_approval_without_mutating_input() -> None:
    value = _mandate("a" * 40)
    original_digest = planner.mandate_digest(value)
    candidate = planner.migrate_pinned_mandate(value)
    assert planner.mandate_digest(value) == original_digest
    assert candidate["schema_version"] == "3.0"
    assert candidate["policy_sha"] == value["policy_sha"]
    assert candidate["approval"] == {
        "mandate_digest": "pending",
        "approved_by": "",
        "approved_at": 0,
    }
    assert planner.mandate_lifecycle_code(candidate, _context()) is not None
    plan = _plan("a" * 40)
    assert (
        planner.migrate_pinned_plan(plan, policy_sha="a" * 40)["schema_version"]
        == "2.0"
    )
    assert plan["schema_version"] == "1.0"


def test_pinned_plan_uses_new_base_without_changing_policy(tmp_path: Path) -> None:
    root, policy_sha = _repository(tmp_path)
    (root / "tools" / "existing.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "delivery base advances")
    base_sha = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/heads/roadmap/248-runner", base_sha)
    _git(root, "update-ref", "refs/remotes/origin/main", base_sha)
    _git(root, "update-ref", "refs/remotes/origin/roadmap/248-runner", base_sha)
    mandate = _mandate(policy_sha)
    mandate["schema_version"] = "3.0"
    digest = planner.mandate_digest(mandate)
    mandate["approval"]["mandate_digest"] = digest
    plan = _plan(base_sha)
    plan["schema_version"] = "2.0"
    plan["policy_sha"] = policy_sha
    result = planner.prepare_handover(
        mandate,
        plan,
        root=root,
        approved_mandate_digest=digest,
        github=FakeGitHub(base_sha),
        issue_body=BODY,
        mandate_context=_context(),
    )
    assert result.machine_code == "OK"
    assert result.handover["schema_version"] == "2.0"
    assert result.handover["base_sha"] == base_sha
    assert result.handover["trusted_policy"] == {
        "source": "pinned_policy_sha",
        "policy_sha": policy_sha,
    }


class FakeGitHub:
    def __init__(self, sha: str) -> None:
        self.sha = sha
        self.parent = 248

    def repository(self, _repository: str) -> dict[str, Any]:
        return {
            "nameWithOwner": "OWNER/repo",
            "defaultBranchRef": {"name": "main"},
        }

    def issue(self, _repository: str, number: int) -> dict[str, Any]:
        if number == 248:
            return {
                "number": 248,
                "state": "OPEN",
                "subIssues": {"nodes": [{"number": 251}]},
            }
        return {"number": 251, "state": "OPEN", "parent": {"number": self.parent}}

    def branches(self, _repository: str) -> dict[str, str]:
        return {"main": self.sha, "roadmap/248-runner": self.sha}

    def compare(self, _repository: str, _base: str, _head: str) -> str:
        return "identical"


def _mandate(sha: str) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "2.0",
        "repository": "OWNER/repo",
        "epic": 248,
        "roadmap_ref": "roadmap/248-runner",
        "policy_sha": sha,
        "issued_at": 100,
        "expires_at": 1000,
        "owner_identity": "OWNER",
        "limits": {
            "max_duration_seconds": 600,
            "max_task_iterations": 10,
            "max_follow_up_issues": 3,
        },
        "revoked": False,
        "operations": ["create_local_task_branch", "build_task_handover"],
        "task_grants": {
            "251": {
                "causal_scope": ["epic mandate handover"],
                "allowed_paths": [
                    "tools/existing.py",
                    "tools/new.py",
                    "tests/test_new.py",
                    "unknown/new.py",
                ],
            }
        },
        "approval": {
            "mandate_digest": "pending",
            "approved_by": "OWNER",
            "approved_at": 100,
        },
    }
    value["approval"]["mandate_digest"] = planner.mandate_digest(value)
    return value


def _context(**changes: Any) -> Any:
    values = {
        "now": 200,
        "owner_identity": "OWNER",
        "started_at": 150,
        "task_iterations": 0,
        "follow_up_issues": 0,
    }
    values.update(changes)
    return planner.MandateContext(**values)


@pytest.mark.parametrize("revoked", [0, 1])
def test_numeric_revoked_is_not_a_boolean(revoked: int) -> None:
    mandate = _mandate("a" * 40)
    mandate["revoked"] = revoked

    assert planner.mandate_lifecycle_code(mandate, _context()) == "MANDATE_INVALID"


def _plan(sha: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "repository": "OWNER/repo",
        "epic": 248,
        "task": 251,
        "criteria_source": {
            "kind": "github_issue",
            "issue": 251,
            "state": "OPEN",
            "body_sha256": "sha256:" + hashlib.sha256(BODY.encode()).hexdigest(),
        },
        "acceptance_criteria": ["minimal handover", "exact gates"],
        "delivery_criterion_indices": [2],
        "default_ref": "main",
        "default_sha": sha,
        "base_ref": "roadmap/248-runner",
        "base_sha": sha,
        "head_ref": "feat/251-handover",
        "causal_scope": ["epic mandate handover"],
        "allowed_paths": ["tools/existing.py", "tests/test_new.py"],
        "changes_policy_or_architecture": False,
        "budgets": {
            "max_minutes": 60,
            "max_repair_iterations": 2,
            "max_report_chars": 20_000,
        },
        "task_class": "feature",
    }


def _prepare(root: Path, sha: str, **changes: Any) -> Any:
    mandate = _mandate(sha)
    plan = _plan(sha)
    plan.update(changes)
    return planner.prepare_handover(
        mandate,
        plan,
        root=root,
        approved_mandate_digest=mandate["approval"]["mandate_digest"],
        github=FakeGitHub(sha),
        issue_body=BODY,
        mandate_context=_context(),
    )


def test_exact_subset_creates_branch_and_versioned_handover(tmp_path: Path) -> None:
    root, sha = _repository(tmp_path)

    result = _prepare(root, sha)

    assert result.state == "HANDOVER"
    assert result.machine_code == "OK"
    assert result.handover is not None
    assert result.handover["allowed_paths"] == [
        "tools/existing.py",
        "tests/test_new.py",
    ]
    assert result.handover["mandate_provenance"] == {
        "schema_version": "2.0",
        "digest": _mandate(sha)["approval"]["mandate_digest"],
        "policy_sha": sha,
    }
    assert result.handover["approval"]["plan_digest"].startswith("sha256:")
    assert _git(root, "rev-parse", "feat/251-handover") == sha


def test_plan_cannot_expand_mandate_scope_or_authority(tmp_path: Path) -> None:
    root, sha = _repository(tmp_path)
    expanded_path = _prepare(root, sha, allowed_paths=["tools/ungranted.py"])
    expanded_scope = _prepare(root, sha, causal_scope=["ungranted scope"])
    architecture = _prepare(root, sha, changes_policy_or_architecture=True)

    assert expanded_path.machine_code == "PLAN_SCOPE_EXPANSION"
    assert expanded_scope.machine_code == "PLAN_SCOPE_EXPANSION"
    assert architecture.machine_code == "POLICY_DECISION_REQUIRED"
    assert not (root / ".git" / "refs" / "heads" / "feat" / "251-handover").exists()


def test_dirty_tree_and_stale_ref_stop_before_branch_write(tmp_path: Path) -> None:
    dirty_root, dirty_sha = _repository(tmp_path / "dirty")
    (dirty_root / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    dirty = _prepare(dirty_root, dirty_sha)
    assert dirty.machine_code == "DIRTY_WORKTREE"

    stale_root, stale_sha = _repository(tmp_path / "stale")
    client = FakeGitHub(stale_sha)
    client.sha = "f" * 40
    mandate = _mandate(stale_sha)
    stale = planner.prepare_handover(
        mandate,
        _plan(stale_sha),
        root=stale_root,
        approved_mandate_digest=mandate["approval"]["mandate_digest"],
        github=client,
        issue_body=BODY,
        mandate_context=_context(),
    )
    assert stale.machine_code == "REMOTE_STATE_MISMATCH"


def test_new_path_parent_and_symlink_escape_require_decision(tmp_path: Path) -> None:
    root, sha = _repository(tmp_path)
    unknown_parent = _prepare(root, sha, allowed_paths=["unknown/new.py"])
    assert unknown_parent.machine_code == "UNCONFIRMED_DIRECTORY"

    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    mandate = _mandate(sha)
    mandate["task_grants"]["251"]["allowed_paths"].append("escape/new.py")
    mandate["approval"]["mandate_digest"] = planner.mandate_digest(mandate)
    escaped = planner.prepare_handover(
        mandate,
        _plan(sha) | {"allowed_paths": ["escape/new.py"]},
        root=root,
        approved_mandate_digest=mandate["approval"]["mandate_digest"],
        github=FakeGitHub(sha),
        issue_body=BODY,
        mandate_context=_context(),
    )
    assert escaped.machine_code == "PATH_ESCAPE"


def test_changed_mandate_or_plan_has_new_digest_and_old_approval_fails(
    tmp_path: Path,
) -> None:
    root, sha = _repository(tmp_path)
    mandate = _mandate(sha)
    approved = mandate["approval"]["mandate_digest"]
    mandate["task_grants"]["251"]["allowed_paths"].append("tools/extra.py")

    result = planner.prepare_handover(
        mandate,
        _plan(sha),
        root=root,
        approved_mandate_digest=approved,
        github=FakeGitHub(sha),
        issue_body=BODY,
        mandate_context=_context(),
    )

    assert result.machine_code == "MANDATE_APPROVAL_MISMATCH"
    assert not (root / ".git" / "refs" / "heads" / "feat" / "251-handover").exists()
