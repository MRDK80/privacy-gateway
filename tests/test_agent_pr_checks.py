"""Deterministic GitHub PR/check integration tests (#167)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "agent_pr_checks.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_pr_checks", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checks = _load_module()


class FakeGitHub:
    def __init__(self, snapshots: list[list[dict[str, str]]], **pr: Any) -> None:
        self.snapshots = snapshots
        self.pr = {
            "number": 167,
            "url": "https://example.invalid/pull/171",
            "baseRefName": "roadmap/162-agent-orchestration",
            "headRefName": "ci/167-agent-pr-checks",
            "headRefOid": "a" * 40,
            "files": [{"path": "tools/agent_pr_checks.py"}],
            "reviews": [{"state": "APPROVED", "author": {"login": "reviewer"}}],
            **pr,
        }
        self.pr_calls = 0

    def pull_request(self, _number: int) -> dict[str, Any]:
        self.pr_calls += 1
        return dict(self.pr)

    def pull_request_checks(self, _number: int) -> list[dict[str, str]]:
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]


def _options(**values: Any) -> Any:
    defaults = {
        "pr": 171,
        "expected_base": "roadmap/162-agent-orchestration",
        "expected_head": "ci/167-agent-pr-checks",
        "expected_sha": "a" * 40,
        "timeout_seconds": 0,
        "poll_seconds": 0,
    }
    return checks.Options(**(defaults | values))


def _check(name: str, state: str) -> dict[str, str]:
    return {"name": name, "state": state, "link": f"https://example.invalid/{name}"}


def test_green_requires_every_applicable_check_successful() -> None:
    client = FakeGitHub(
        [[_check("CI 3.11", "SUCCESS"), _check("pre-commit", "SUCCESS")]]
    )
    result = checks.verify(_options(), client, sleep=lambda _seconds: None)
    assert result.machine_code == "OK"
    assert result.status == "green"
    assert result.head_sha == "a" * 40


def test_pending_waits_then_succeeds() -> None:
    client = FakeGitHub([[_check("CI", "IN_PROGRESS")], [_check("CI", "SUCCESS")]])
    result = checks.verify(
        _options(timeout_seconds=5, poll_seconds=1),
        client,
        sleep=lambda _seconds: None,
        monotonic=iter([0.0, 0.0, 1.0]).__next__,
    )
    assert result.machine_code == "OK"


def test_non_success_terminal_states_are_explicit() -> None:
    for state in ("FAILURE", "CANCELLED", "TIMED_OUT", "SKIPPED", "NEUTRAL"):
        result = checks.verify(
            _options(), FakeGitHub([[_check("CI", state)]]), sleep=lambda _: None
        )
        assert result.machine_code == "CHECKS_FAILED"
        assert result.checks[0].state == state


def test_pending_timeout_is_not_green() -> None:
    result = checks.verify(
        _options(), FakeGitHub([[_check("CI", "QUEUED")]]), sleep=lambda _: None
    )
    assert result.machine_code == "CHECKS_PENDING"


def test_empty_checks_are_not_green() -> None:
    result = checks.verify(_options(), FakeGitHub([[]]), sleep=lambda _: None)
    assert result.machine_code == "NO_CHECKS"


def test_wrong_direction_and_stale_sha_fail_closed() -> None:
    wrong = FakeGitHub([[_check("CI", "SUCCESS")]], baseRefName="main")
    assert checks.verify(_options(), wrong).machine_code == "WRONG_DIRECTION"
    stale = FakeGitHub([[_check("CI", "SUCCESS")]], headRefOid="b" * 40)
    assert checks.verify(_options(), stale).machine_code == "STALE_HEAD"


def test_head_change_during_poll_fails_closed() -> None:
    class MovingHead(FakeGitHub):
        def pull_request(self, number: int) -> dict[str, Any]:
            value = super().pull_request(number)
            if self.pr_calls > 1:
                value["headRefOid"] = "b" * 40
            return value

    client = MovingHead([[_check("CI", "SUCCESS")]])
    assert checks.verify(_options(), client).machine_code == "PR_CHANGED"


def test_protected_path_requires_human_approval() -> None:
    client = FakeGitHub(
        [[_check("CI", "SUCCESS")]],
        reviews=[],
        files=[{"path": "AGENTS.md"}],
    )
    result = checks.verify(_options(), client)
    assert result.machine_code == "HUMAN_REVIEW_REQUIRED"


def test_private_artifact_is_blocked_before_ci() -> None:
    private_paths = (
        "raw-retrospectives/run.json",
        "known-pitfalls/pending.json",
        "usage-metrics/run.json",
        "config.local/agent.json",
    )
    for private_path in private_paths:
        client = FakeGitHub(
            [[_check("CI", "SUCCESS")]],
            files=[{"path": private_path}],
        )
        result = checks.verify(_options(), client)
        assert result.machine_code == "PRIVATE_ARTIFACT"


def test_public_synthetic_fixture_is_not_treated_as_private() -> None:
    client = FakeGitHub(
        [[_check("CI", "SUCCESS")]],
        files=[{"path": "tests/fixtures/synthetic-retrospective.json"}],
    )
    result = checks.verify(_options(), client)
    assert result.machine_code == "OK"


def test_failure_evidence_keeps_link_but_not_untrusted_description() -> None:
    item = _check("CI", "FAILURE") | {"description": "untrusted diagnostic"}
    result = checks.verify(_options(), FakeGitHub([[item]]))
    payload = checks.render_json(result)
    assert "https://example.invalid/CI" in payload
    assert "untrusted diagnostic" not in payload
