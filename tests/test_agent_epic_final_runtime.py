"""Concrete final phase assessment never transfers PR CI to main post-merge."""

from dataclasses import replace
from pathlib import Path

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
