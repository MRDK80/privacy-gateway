"""Concrete final phase assessment never transfers PR CI to main post-merge."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_final_delivery as driver
from tools.agent_epic_final_runtime import FinalRuntime

from tests.test_agent_coordinator_delivery import _gate, _green_checks
from tests.test_agent_epic_snapshot import git, setup
from tests.test_agent_epic_workflow import NeverAdapter


def build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FinalRuntime, driver.Request]:
    root = tmp_path / "repo"
    old, _, _ = setup(root)
    git(root, "add", "task.txt")
    git(root, "commit", "-qm", "synthetic roadmap")
    sha = git(root, "rev-parse", "HEAD")
    request = driver.Request(
        operation_id="final",
        operation="merge_roadmap_pr",
        repository=old.repository,
        epic=248,
        roadmap_ref=old.base_ref,
        roadmap_sha=sha,
        policy_sha=old.base_sha,
        main_sha=old.base_sha,
        pr=278,
    )
    engine = FinalRuntime(
        root=root,
        directory=tmp_path / "private",
        authority=lambda: pytest.fail("read-only gate"),
        approval=lambda: {},
        approved_digest="synthetic",
        lesson=lambda: {},
        adapter=NeverAdapter(),
    )
    monkeypatch.setattr(
        engine,
        "_owner",
        lambda: (
            {
                key: getattr(request, key)
                for key in (
                    "repository",
                    "epic",
                    "roadmap_ref",
                    "roadmap_sha",
                    "main_sha",
                    "policy_sha",
                )
            }
            | {"demo_confirmed_by_owner": True}
        ),
    )
    gate = _gate()
    gate["snapshot"]["tree_hash"] = git(root, "rev-parse", "HEAD^{tree}")
    gate["snapshot"]["base_sha"] = old.base_sha
    monkeypatch.setattr(
        engine,
        "_prepare",
        lambda _: {
            "gate": gate,
            "reviewed_sha": sha,
            "policy_sha": old.base_sha,
            "negative_tests_passed": True,
        },
    )
    monkeypatch.setattr(engine, "_facts", lambda _: (sha, old.base_sha, []))
    monkeypatch.setattr(
        engine.transport,
        "pull_request",
        lambda _: {"headRefOid": sha, "baseRefName": "main", "state": "OPEN"},
    )
    monkeypatch.setattr(engine.github, "pr_checks", lambda *args: _green_checks())
    return engine, request


def test_changed_main_and_pending_pr_ci_block_final_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, request = build(tmp_path, monkeypatch)
    assert engine.gate(request).status == "PASS"
    monkeypatch.setattr(engine, "_facts", lambda _: (request.roadmap_sha, "c" * 40, []))
    assert engine.gate(request).machine_code == "MAIN_CHANGED"
    monkeypatch.setattr(
        engine, "_facts", lambda _: (request.roadmap_sha, request.main_sha, [])
    )
    monkeypatch.setattr(
        engine.github,
        "pr_checks",
        lambda *args: [value | {"state": "PENDING"} for value in _green_checks()],
    )
    assert engine.gate(request).machine_code == "ROADMAP_PR_CI_FAILED"


def test_post_merge_requires_actual_main_checks_and_no_reopened_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, original = build(tmp_path, monkeypatch)
    request = replace(original, operation="close_epic", merge_sha="c" * 40)
    monkeypatch.setattr(engine, "_facts", lambda _: (request.roadmap_sha, "c" * 40, []))
    monkeypatch.setattr(
        engine.transport,
        "pull_request",
        lambda _: {"state": "MERGED", "mergeCommit": {"oid": "c" * 40}},
    )
    monkeypatch.setattr(
        engine.github,
        "commit_checks",
        lambda *args: [value | {"state": "FAILURE"} for value in _green_checks()],
    )
    assert engine.gate(request).machine_code == "MAIN_POST_MERGE_CI_FAILED"
    monkeypatch.setattr(engine.github, "commit_checks", lambda *args: _green_checks())
    assert engine.gate(request).roadmap_status == "ROADMAP DONE"
    monkeypatch.setattr(
        engine, "_facts", lambda _: (request.roadmap_sha, "c" * 40, [275])
    )
    assert engine.gate(request).machine_code == "OPEN_REQUIRED_TASKS"


def test_final_quota_retains_reserved_role_and_blocks_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from typing import cast

    from tools import agent_epic_loop as loop
    from tools import agent_quota_role as transport
    from tools.agent_quota_wait import bare_digest

    engine, _request = build(tmp_path, monkeypatch)
    adapter = transport.CommandAdapter(
        ["synthetic-executor"],
        ["synthetic-controller"],
        root=engine.root,
        timeout_seconds=1,
        output_limit=1000,
        backend_digest="a" * 64,
        guard=lambda: None,
    )
    engine.adapter = adapter
    engine.wait_scope = cast(Any, SimpleNamespace(active_stage="FINAL:FINAL_GATE"))
    calls: list[str] = []

    def quota_review(
        request: dict[str, Any],
        policy: dict[str, str],
        session: str,
    ) -> dict[str, Any]:
        calls.append(session)
        event = {
            "schema_version": "1.0",
            "role": "controller",
            "invocation_digest": bare_digest(
                {
                    **request,
                    "trusted_policy": policy,
                    "session_id": session,
                }
            ),
            "thread_digest": "b" * 64,
            "turn_digest": "c" * 64,
            "error_code": "rateLimitExceeded",
            "backend_digest": "a" * 64,
            "outcome": "UNKNOWN",
        }
        raise loop.QuotaPaused(
            {
                "exhausted_windows": ["primary"],
                "resets_at": 2000,
                "next_check_at": 1300,
            },
            interruption=event,
        )

    monkeypatch.setattr(adapter, "review", quota_review)
    with pytest.raises(loop.QuotaPaused):
        engine._quota_review({"synthetic": True}, {}, "d" * 64)
    with pytest.raises(loop.PhaseBlocked, match="FINAL_ROLE_OUTCOME_UNKNOWN"):
        engine._quota_review({"synthetic": True}, {}, "d" * 64)
    assert len(calls) == 1
    saved = engine.storage.__class__(engine.directory).load("quota-final-pause")
    assert saved is not None and saved["stage"] == "FINAL:FINAL_GATE"
    assert saved["status"] == "PAUSED_RATE_LIMIT"


def test_final_resume_reports_durable_quota_pause_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import agent_epic_loop as loop

    engine, _request = build(tmp_path, monkeypatch)
    calls: list[str] = []

    def paused() -> Any:
        calls.append("call")
        raise loop.QuotaPaused(
            {
                "exhausted_windows": ["primary"],
                "resets_at": 2000,
                "next_check_at": 1300,
            }
        )

    monkeypatch.setattr(engine, "_resume", paused, raising=False)
    result = engine.resume()
    assert result.status == "PAUSED_RATE_LIMIT"
    assert calls == ["call"]


def test_consumed_final_review_blocks_before_repeating_local_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    from tools import agent_coordinator_handover as handover
    from tools import agent_epic_loop as loop
    from tools import agent_quota_role as transport

    engine, request = build(tmp_path, monkeypatch)
    engine.adapter = transport.CommandAdapter(
        ["synthetic-executor"],
        ["synthetic-controller"],
        root=engine.root,
        timeout_seconds=1,
        output_limit=1000,
        backend_digest="a" * 64,
        guard=lambda: None,
    )
    key = hashlib.sha256(engine.approved_digest.encode()).hexdigest()
    engine.storage.__class__(engine.directory / "final-role-reservations").save(
        key,
        {"status": "RESERVED"},
    )
    monkeypatch.setattr(handover, "_git", lambda *args: pytest.fail("no repeated gate"))
    with pytest.raises(loop.PhaseBlocked, match="FINAL_ROLE_OUTCOME_UNKNOWN"):
        FinalRuntime._prepare(engine, request)
