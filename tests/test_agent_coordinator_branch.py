"""Fail-closed local branch preparation tests (#235)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "agent_coordinator_branch.py"
)
SPEC = importlib.util.spec_from_file_location("agent_coordinator_branch", MODULE_PATH)
assert SPEC and SPEC.loader
branch = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = branch
SPEC.loader.exec_module(branch)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "agent@example.invalid")
    _git(root, "config", "user.name", "Synthetic Agent")
    (root / "README.md").write_text("main\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "main")
    _git(root, "branch", "-M", "main")
    main_sha = _git(root, "rev-parse", "HEAD")
    _git(root, "branch", "roadmap/232-agent-coordinator", main_sha)
    _git(root, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(
        root,
        "update-ref",
        "refs/remotes/origin/roadmap/232-agent-coordinator",
        main_sha,
    )
    return root


class FakeGitHub:
    def __init__(self, main_sha: str) -> None:
        self.main_sha = main_sha
        self.base_sha = main_sha
        self.task_parent = 232
        self.sub_issue_numbers = [235]
        self.remote_head = False
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise branch.GitHubError("offline")

    def repository(self, _repository: str) -> dict[str, Any]:
        self._check()
        return {
            "nameWithOwner": "MRDK80/privacy-gateway",
            "defaultBranchRef": {"name": "main"},
        }

    def issue(self, _repository: str, number: int) -> dict[str, Any]:
        self._check()
        value: dict[str, Any] = {"number": number, "state": "open"}
        if number == 235:
            value["parent"] = {"number": self.task_parent}
        else:
            value["subIssues"] = {
                "nodes": [{"number": child} for child in self.sub_issue_numbers],
                "totalCount": len(self.sub_issue_numbers),
            }
        return value

    def branches(self, _repository: str) -> dict[str, str]:
        self._check()
        values = {
            "main": self.main_sha,
            "roadmap/232-agent-coordinator": self.base_sha,
        }
        if self.remote_head:
            values["feat/235-agent-coordinator-branch-preflight"] = self.base_sha
        return values

    def compare(self, _repository: str, _base: str, _head: str) -> str:
        self._check()
        return "identical"


def _options(root: Path, *, approved: bool = True) -> Any:
    sha = _git(root, "rev-parse", "HEAD")
    return branch.Options(
        repository="MRDK80/privacy-gateway",
        epic=232,
        task=235,
        default_ref="main",
        default_sha=sha,
        base_ref="roadmap/232-agent-coordinator",
        base_sha=sha,
        head_ref="feat/235-agent-coordinator-branch-preflight",
        remote="origin",
        root=root,
        approved=approved,
    )


def _head(root: Path) -> str | None:
    run = subprocess.run(
        [
            "git",
            "rev-parse",
            "--verify",
            "refs/heads/feat/235-agent-coordinator-branch-preflight",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return run.stdout.strip() if run.returncode == 0 else None


def test_approved_plan_creates_unchecked_out_branch_without_upstream(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    options = _options(root)
    client = FakeGitHub(options.default_sha)

    result = branch.prepare_branch(options, client)

    assert result.status == "CREATED"
    assert result.machine_code == "OK"
    assert result.before_sha is None
    assert result.after_sha == options.base_sha
    assert result.current_branch_before == result.current_branch_after == "main"
    assert result.upstream is None


def test_identical_existing_branch_is_idempotent_no_op(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    options = _options(root)
    client = FakeGitHub(options.default_sha)
    assert branch.prepare_branch(options, client).status == "CREATED"

    result = branch.prepare_branch(options, client)

    assert result.status == "NO_OP"
    assert result.before_sha == result.after_sha == options.base_sha


def test_no_approval_performs_no_git_write(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    options = _options(root, approved=False)

    result = branch.prepare_branch(options, FakeGitHub(options.default_sha))

    assert result.machine_code == "APPROVAL_REQUIRED"
    assert _head(root) is None


def test_dirty_worktree_performs_no_git_write(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    options = _options(root)
    (root / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    result = branch.prepare_branch(options, FakeGitHub(options.default_sha))

    assert result.machine_code == "DIRTY_WORKTREE"
    assert _head(root) is None


def test_unavailable_github_performs_no_git_write(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    options = _options(root)
    client = FakeGitHub(options.default_sha)
    client.fail = True

    result = branch.prepare_branch(options, client)

    assert result.machine_code == "GITHUB_ERROR"
    assert _head(root) is None


def test_stale_remote_tracking_base_performs_no_git_write(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    options = _options(root)
    client = FakeGitHub(options.default_sha)
    _git(root, "checkout", "-b", "advanced")
    (root / "advanced.txt").write_text("advanced\n", encoding="utf-8")
    _git(root, "add", "advanced.txt")
    _git(root, "commit", "-m", "advanced")
    advanced_sha = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "main")
    _git(
        root,
        "update-ref",
        "refs/remotes/origin/roadmap/232-agent-coordinator",
        advanced_sha,
    )

    result = branch.prepare_branch(options, client)

    assert result.machine_code == "WRONG_BASE"
    assert _head(root) is None


def test_wrong_parent_in_either_direction_performs_no_git_write(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    options = _options(root)
    client = FakeGitHub(options.default_sha)
    client.sub_issue_numbers = []

    result = branch.prepare_branch(options, client)

    assert result.machine_code == "ISSUE_MISMATCH"
    assert _head(root) is None


def test_remote_head_conflict_performs_no_git_write(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    options = _options(root)
    client = FakeGitHub(options.default_sha)
    client.remote_head = True

    result = branch.prepare_branch(options, client)

    assert result.machine_code == "REMOTE_STATE_MISMATCH"
    assert _head(root) is None


def test_local_head_at_other_sha_fails_closed(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    options = _options(root)
    _git(root, "checkout", "-b", "other")
    (root / "other.txt").write_text("other\n", encoding="utf-8")
    _git(root, "add", "other.txt")
    _git(root, "commit", "-m", "other")
    other_sha = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "main")
    _git(root, "branch", options.head_ref, other_sha)

    result = branch.prepare_branch(options, FakeGitHub(options.default_sha))

    assert result.machine_code == "HEAD_CONFLICT"
    assert _head(root) == other_sha
