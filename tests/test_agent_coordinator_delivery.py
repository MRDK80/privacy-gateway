"""Read-only coordinator delivery assessment tests (#237)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "agent_coordinator_delivery.py"
)
SPEC = importlib.util.spec_from_file_location("agent_coordinator_delivery", MODULE_PATH)
assert SPEC and SPEC.loader
delivery = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = delivery
SPEC.loader.exec_module(delivery)

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
MERGE_SHA = "c" * 40


def _options(**changes: Any) -> Any:
    values = {
        "phase": "pr",
        "repository": "OWNER/repository",
        "epic": 232,
        "task": 237,
        "pr": 244,
        "base_ref": "roadmap/232-agent-coordinator",
        "base_sha": BASE_SHA,
        "head_ref": "feat/237-agent-coordinator-delivery",
        "head_sha": HEAD_SHA,
        "allowed_paths": (
            "docs/ADR-237-agent-coordinator-delivery.md",
            "docs/agent-coordinator.md",
            "tools/agent_coordinator_delivery.py",
            "tests/test_agent_coordinator_delivery.py",
        ),
        "merge_sha": None,
    }
    return delivery.Options(**(values | changes))


def _gate(**snapshot_changes: str) -> dict[str, Any]:
    snapshot = {
        "base_sha": BASE_SHA,
        "snapshot_method": "commit",
        "snapshot_commit": HEAD_SHA,
        "tree_hash": "d" * 64,
        "diff_sha256": "e" * 64,
        "provenance_complete": True,
    } | snapshot_changes
    checks = []
    for check_id in ("pytest", "ruff", "mypy", "pre-commit"):
        checks.append(
            {
                "id": check_id,
                "argv": [check_id],
                "cwd": "<snapshot-checkout>",
                "status": "passed",
                "exit_code": 0,
                "duration_seconds": 1.0,
                "summary": f"{check_id} passed",
                "metrics": {"passed": 10} if check_id == "pytest" else {},
            }
        )
    return {
        "schema_version": "1.0",
        "profile": "repository-full",
        "profile_version": "1",
        "status": "passed",
        "complete": True,
        "machine_code": "OK",
        "expected_checks": ["pytest", "ruff", "mypy", "pre-commit"],
        "executed_checks": ["pytest", "ruff", "mypy", "pre-commit"],
        "snapshot": snapshot,
        "checks": checks,
    }


def _check(name: str, state: str = "SUCCESS") -> dict[str, str]:
    return {"name": name, "state": state, "link": f"https://example.invalid/{name}"}


def _green_checks() -> list[dict[str, str]]:
    return [_check(name) for name in sorted(delivery.REQUIRED_CHECKS)]


class FakeGitHub:
    def __init__(
        self, *, checks: list[dict[str, str]] | None = None, **pr: Any
    ) -> None:
        self.pr = {
            "state": "OPEN",
            "baseRefName": "roadmap/232-agent-coordinator",
            "baseRefOid": BASE_SHA,
            "headRefName": "feat/237-agent-coordinator-delivery",
            "headRefOid": HEAD_SHA,
            "mergeCommit": None,
            "files": [
                {"path": "tools/agent_coordinator_delivery.py"},
                {"path": "tests/test_agent_coordinator_delivery.py"},
            ],
        } | pr
        self.checks = _green_checks() if checks is None else checks
        self.calls = 0

    def pull_request(self, repository: str, number: int) -> dict[str, Any]:
        assert repository == "OWNER/repository"
        assert number == 244
        self.calls += 1
        return dict(self.pr)

    def pr_checks(self, repository: str, number: int) -> list[dict[str, str]]:
        return list(self.checks)

    def commit_checks(self, repository: str, sha: str) -> list[dict[str, str]]:
        assert sha == MERGE_SHA
        return list(self.checks)

    def ref_sha(self, repository: str, ref: str) -> str:
        return MERGE_SHA


def test_pr_pass_requires_full_local_gate_scope_direction_and_exact_ci() -> None:
    result = delivery.assess(_options(), _gate(), FakeGitHub())
    assert result.machine_code == "OK"
    assert result.task_status == "TASK READY FOR REVIEW"
    assert len(result.github_checks) == 5
    assert [item["id"] for item in result.local_checks] == [
        "pytest",
        "ruff",
        "mypy",
        "pre-commit",
    ]


def test_stale_gate_wrong_base_and_scope_escape_fail_closed() -> None:
    stale = delivery.assess(_options(), _gate(snapshot_commit="f" * 40), FakeGitHub())
    assert stale.machine_code == "LOCAL_GATE_INVALID"

    wrong = delivery.assess(_options(), _gate(), FakeGitHub(baseRefName="main"))
    assert wrong.machine_code == "WRONG_DIRECTION"

    escaped = delivery.assess(
        _options(), _gate(), FakeGitHub(files=[{"path": "unapproved.py"}])
    )
    assert escaped.machine_code == "SCOPE_ESCAPE"


def test_missing_pending_and_skipped_checks_never_pass() -> None:
    missing = delivery.assess(_options(), _gate(), FakeGitHub(checks=[]))
    assert missing.machine_code == "CI_MISSING"

    pending_checks = _green_checks()
    pending_checks[0] = _check(pending_checks[0]["name"], "QUEUED")
    pending = delivery.assess(_options(), _gate(), FakeGitHub(checks=pending_checks))
    assert pending.machine_code == "CI_PENDING"

    skipped_checks = _green_checks()
    skipped_checks[0] = _check(skipped_checks[0]["name"], "SKIPPED")
    skipped = delivery.assess(_options(), _gate(), FakeGitHub(checks=skipped_checks))
    assert skipped.machine_code == "CI_FAILED"


def test_post_merge_uses_new_roadmap_sha_and_commit_checks() -> None:
    client = FakeGitHub(
        state="MERGED",
        mergeCommit={"oid": MERGE_SHA},
    )
    result = delivery.assess(
        _options(phase="post-merge", merge_sha=MERGE_SHA), _gate(), client
    )
    assert result.machine_code == "OK"
    assert result.task_status == "TASK DONE"


def test_unconfirmed_merge_and_post_merge_pending_block_done() -> None:
    unmerged = delivery.assess(
        _options(phase="post-merge", merge_sha=MERGE_SHA), _gate(), FakeGitHub()
    )
    assert unmerged.machine_code == "MERGE_UNCONFIRMED"

    checks = _green_checks()
    checks[-1] = _check(checks[-1]["name"], "IN_PROGRESS")
    pending = delivery.assess(
        _options(phase="post-merge", merge_sha=MERGE_SHA),
        _gate(),
        FakeGitHub(checks=checks, state="MERGED", mergeCommit={"oid": MERGE_SHA}),
    )
    assert pending.machine_code == "CI_PENDING"
    assert pending.task_status is None


def test_pr_identity_change_after_checks_invalidates_assessment() -> None:
    class MovingGitHub(FakeGitHub):
        def pull_request(self, repository: str, number: int) -> dict[str, Any]:
            value = super().pull_request(repository, number)
            if self.calls > 1:
                value["headRefOid"] = "f" * 40
            return value

    result = delivery.assess(_options(), _gate(), MovingGitHub())
    assert result.machine_code == "STALE_IDENTITY"
