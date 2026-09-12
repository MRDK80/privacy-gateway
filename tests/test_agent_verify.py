"""Contract and failure-path tests for tools/agent_verify.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "agent_verify.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_verify", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module()


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Synthetic User")
    _git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    (tmp_path / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-m", "base")
    _git(tmp_path, "branch", "roadmap/162-agent-orchestration")
    _git(tmp_path, "switch", "-c", "build/163-agent-verification-gate")
    (tmp_path / "tracked.txt").write_text("task\n", encoding="utf-8")
    _git(tmp_path, "commit", "-am", "task")
    return tmp_path


def _options(root: Path, phase: str = "pre-commit") -> Any:
    return gate.Options(
        phase=phase,
        base="roadmap/162-agent-orchestration",
        head="build/163-agent-verification-gate",
        remote="origin",
        output_format="json",
        log_dir=None,
        root=root,
    )


def test_pre_commit_reports_diff_without_writing(repository: Path) -> None:
    (repository / "untracked.txt").write_text("synthetic\n", encoding="utf-8")
    before = _git(repository, "status", "--porcelain=v1")
    result = gate.verify(_options(repository))
    after = _git(repository, "status", "--porcelain=v1")

    assert result.machine_code == "OK"
    assert result.changed_files == ["tracked.txt", "untracked.txt"]
    assert before == after


def test_wrong_current_branch_has_stable_failure(repository: Path) -> None:
    _git(repository, "switch", "main")
    result = gate.verify(_options(repository))

    assert result.machine_code == "WRONG_BRANCH"
    assert result.exit_code == 11


def test_dirty_worktree_fails_pre_push(repository: Path) -> None:
    (repository / "untracked.txt").write_text("synthetic\n", encoding="utf-8")
    result = gate.verify(_options(repository, "pre-push"))

    assert result.machine_code == "DIRTY_WORKTREE"
    assert result.exit_code == 12


def test_missing_ancestry_has_stable_failure(repository: Path) -> None:
    _git(repository, "switch", "--orphan", "unrelated")
    (repository / "other.txt").write_text("other\n", encoding="utf-8")
    _git(repository, "add", "other.txt")
    _git(repository, "commit", "-m", "unrelated")
    options = gate.Options(
        phase="pre-commit",
        base="roadmap/162-agent-orchestration",
        head="unrelated",
        remote="origin",
        output_format="json",
        log_dir=None,
        root=repository,
    )

    result = gate.verify(options)

    assert result.machine_code == "ANCESTRY_MISSING"
    assert result.exit_code == 13


def test_diff_error_is_reported(repository: Path) -> None:
    (repository / "tracked.txt").write_text("task   \n", encoding="utf-8")
    _git(repository, "commit", "-am", "bad whitespace")

    result = gate.verify(_options(repository))

    assert result.machine_code == "DIFF_ERROR"
    assert result.exit_code == 14


def test_post_push_requires_matching_remote_ref(repository: Path) -> None:
    result = gate.verify(_options(repository, "post-push"), quality_commands=[])

    assert result.machine_code == "REMOTE_MISMATCH"
    assert result.exit_code == 16


def test_json_contract_does_not_include_command_output(repository: Path) -> None:
    result = gate.verify(_options(repository))
    payload = json.loads(gate.render_json(result))

    assert payload["schema_version"] == "1.0"
    assert payload["machine_code"] == "OK"
    assert set(payload) == {
        "base",
        "changed_files",
        "checks",
        "exit_code",
        "head",
        "head_sha",
        "log_directory",
        "machine_code",
        "phase",
        "remote_sha",
        "schema_version",
        "status",
    }
    assert "stdout" not in gate.render_json(result)
    assert "stderr" not in gate.render_json(result)


def test_log_directory_inside_repository_is_rejected(repository: Path) -> None:
    options = _options(repository)
    options = gate.Options(
        phase=options.phase,
        base=options.base,
        head=options.head,
        remote=options.remote,
        output_format=options.output_format,
        log_dir=repository / "agent-logs",
        root=repository,
    )

    result = gate.verify(options)

    assert result.machine_code == "USAGE_ERROR"
    assert not (repository / "agent-logs").exists()
