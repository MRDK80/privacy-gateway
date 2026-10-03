"""Concrete successor planner on real isolated Git refs, without network."""

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from tools import agent_coordinator_handover as handover
from tools import agent_epic_handover as planner
from tools import agent_epic_loop as loop
from tools import agent_epic_session_runtime as runtime
from tools.agent_epic_final_runtime import digest

from tests.test_agent_epic_handover import (
    BODY,
    FakeGitHub,
    _git,
    _mandate,
    _plan,
    _repository,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX owner-only deployment")


def private(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[runtime.SessionRuntime, dict[str, Any], loop.CheckpointStore]:
    root, policy = _repository(tmp_path)
    (root / "tools" / "existing.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "verified previous merge")
    base = _git(root, "rev-parse", "HEAD")
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    mandate = _mandate(policy)
    mandate["schema_version"] = "3.0"
    approved = planner.mandate_digest(mandate)
    mandate["approval"]["mandate_digest"] = approved
    private(state / "mandate.json", mandate)
    plan = _plan(policy) | {"schema_version": "2.0", "policy_sha": policy}
    plans = {
        "schema_version": "1.0",
        "initial_handover_digest": "unused",
        "tasks": {"251": {"plan": plan, "demo_plan": {}}},
    }
    private(state / "approved-plans.json", plans)
    config = {
        "approved_plans_digest": digest(plans),
        "approved_mandate_digest": approved,
        "task_iterations": 0,
        "started_at": 150,
        "owner_identity": "OWNER",
        "follow_up_issues": 0,
        "approved_final_digest": None,
    }
    monkeypatch.setattr(time, "time", lambda: 200)
    engine = runtime.SessionRuntime(state, root, config)
    engine.github = FakeGitHub(base)
    monkeypatch.setattr(handover.GitHubCLI, "issue", lambda *args: {"body": BODY})
    original_git = handover._git

    def local_git(directory: Path, *args: str) -> str:
        if args[0] == "fetch":
            _git(root, "update-ref", "refs/remotes/origin/main", base)
            _git(root, "update-ref", "refs/remotes/origin/roadmap/248-runner", base)
            return ""
        return original_git(directory, *args)

    monkeypatch.setattr(handover, "_git", local_git)
    previous = {
        "merge_sha": base,
        "head_sha": base,
        "head_ref": "main",
        "repository": "OWNER/repo",
        "epic": 248,
        "base_ref": "roadmap/248-runner",
        "policy_sha": policy,
    }
    target = loop.CheckpointStore(state / "tasks" / "251", root)
    return engine, previous, target


def test_successor_uses_fresh_base_and_keeps_pinned_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, previous, target = build(tmp_path, monkeypatch)
    engine.prepare(previous, 251, target, 1)
    saved = target.load()
    assert saved is not None and saved["schema_version"] == "3.0"
    assert saved["base_sha"] == previous["merge_sha"]
    assert saved["policy_sha"] == previous["policy_sha"]
    assert _git(engine.root, "branch", "--show-current") == "feat/251-handover"
    assert engine.reconcile_plan(previous, 251, target) == "APPLIED"
    value = json.loads((target.directory / "handover.json").read_text(encoding="utf-8"))
    assert value["trusted_policy"]["policy_sha"] == previous["policy_sha"]


def test_changed_plan_digest_blocks_before_creating_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, previous, target = build(tmp_path, monkeypatch)
    private(engine.state / "approved-plans.json", {"changed": True})
    with pytest.raises(loop.PhaseBlocked, match="APPROVED_PLANS_CHANGED"):
        engine.prepare(previous, 251, target, 1)
    assert _git(engine.root, "branch", "--show-current") == "main"
    assert target.load() is None


@pytest.mark.parametrize("failure", ["revoke", "expire"])
def test_authority_changed_during_fetch_prevents_successor_ref_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    engine, previous, target = build(tmp_path, monkeypatch)
    original = handover._git
    before = _git(engine.root, "rev-parse", previous["base_ref"])

    def changed(directory: Path, *args: str) -> str:
        result = original(directory, *args)
        if args[0] == "fetch":
            if failure == "revoke":
                mandate = json.loads((engine.state / "mandate.json").read_text())
                mandate["revoked"] = True
                private(engine.state / "mandate.json", mandate)
            else:
                monkeypatch.setattr(time, "time", lambda: 1001)
        return result

    monkeypatch.setattr(handover, "_git", changed)
    with pytest.raises((loop.LoopError, loop.PhaseBlocked)):
        engine.prepare(previous, 251, target, 1)
    assert _git(engine.root, "rev-parse", previous["base_ref"]) == before
    assert _git(engine.root, "branch", "--show-current") == "main"
    assert target.load() is None


@pytest.mark.parametrize("point", ["fetch", "create", "switch"])
@pytest.mark.parametrize("changed_ref", ["main", "roadmap/248-runner"])
def test_live_ref_changed_during_preparation_blocks_next_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
    changed_ref: str,
) -> None:
    engine, previous, target = build(tmp_path, monkeypatch)
    refs = {"main": previous["merge_sha"], previous["base_ref"]: previous["merge_sha"]}
    monkeypatch.setattr(engine.github, "branches", lambda _: dict(refs))
    original_git = handover._git
    original_prepare = planner.prepare_handover
    writes: list[str] = []

    def git_with_drift(directory: Path, *args: str) -> str:
        if args[0] in {"update-ref", "switch"}:
            writes.append(args[0])
        result = original_git(directory, *args)
        if args[0] == "fetch" and point == "fetch":
            refs[changed_ref] = "c" * 40
        return result

    def prepare_with_drift(*args: Any, **kwargs: Any) -> Any:
        if point == "create":
            guard = kwargs["before_create"]

            def changed() -> None:
                refs[changed_ref] = "c" * 40
                guard()

            kwargs["before_create"] = changed
        result = original_prepare(*args, **kwargs)
        if point == "switch":
            refs[changed_ref] = "c" * 40
        return result

    monkeypatch.setattr(handover, "_git", git_with_drift)
    monkeypatch.setattr(planner, "prepare_handover", prepare_with_drift)
    with pytest.raises(loop.LoopError, match="SUCCESSOR_PREPARATION_UNKNOWN"):
        engine.prepare(previous, 251, target, 1)
    assert "switch" not in writes
    if point == "fetch":
        assert "update-ref" not in writes
    if point != "switch":
        assert _git(engine.root, "branch", "--list", "feat/251-handover") == ""
    assert target.load() is None


@pytest.mark.parametrize("identity", ["head", "repository"])
def test_local_identity_changed_during_fetch_blocks_roadmap_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity: str
) -> None:
    engine, previous, target = build(tmp_path, monkeypatch)
    old_road = _git(engine.root, "rev-parse", previous["base_ref"])
    original = handover._git

    def changed(directory: Path, *args: str) -> str:
        result = original(directory, *args)
        if args[0] == "fetch":
            if identity == "head":
                _git(engine.root, "switch", "-c", "synthetic-other")
            else:
                _git(engine.root, "remote", "set-url", "origin", "https://github.com/OTHER/repo.git")
        return result

    monkeypatch.setattr(handover, "_git", changed)
    with pytest.raises(loop.LoopError, match="SUCCESSOR_PREPARATION_UNKNOWN"):
        engine.prepare(previous, 251, target, 1)
    assert _git(engine.root, "rev-parse", previous["base_ref"]) == old_road
    assert target.load() is None


@pytest.mark.parametrize("failure", ["revoke", "expire"])
def test_final_authority_changed_during_import_prevents_roadmap_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    engine, previous, _ = build(tmp_path, monkeypatch)
    owner = {
        "repository": previous["repository"],
        "roadmap_ref": previous["base_ref"],
        "roadmap_sha": previous["merge_sha"],
        "main_sha": previous["merge_sha"],
        "policy_sha": previous["policy_sha"],
    }
    original = handover._git
    before = _git(engine.root, "rev-parse", previous["base_ref"])

    def changed(directory: Path, *args: str) -> str:
        if args[0] == "cat-file":
            raise handover.HandoverError("SYNTHETIC_CACHE_MISS")
        result = original(directory, *args)
        if args[0] == "fetch":
            if failure == "revoke":
                mandate = json.loads((engine.state / "mandate.json").read_text())
                mandate["revoked"] = True
                private(engine.state / "mandate.json", mandate)
            else:
                monkeypatch.setattr(time, "time", lambda: 1001)
        return result

    monkeypatch.setattr(handover, "_git", changed)
    with pytest.raises(loop.PhaseBlocked):
        engine._sync_final(owner, iterations=1)
    assert _git(engine.root, "rev-parse", previous["base_ref"]) == before
    assert _git(engine.root, "branch", "--show-current") == "main"


def test_revocation_before_task_switch_preserves_partial_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, previous, target = build(tmp_path, monkeypatch)
    original = planner.prepare_handover

    def prepared(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        mandate = json.loads((engine.state / "mandate.json").read_text())
        mandate["revoked"] = True
        private(engine.state / "mandate.json", mandate)
        return result

    monkeypatch.setattr(planner, "prepare_handover", prepared)
    with pytest.raises(loop.LoopError, match="SUCCESSOR_PREPARATION_UNKNOWN"):
        engine.prepare(previous, 251, target, 1)
    assert _git(engine.root, "branch", "--show-current") == "main"
    assert target.load() is None


def test_final_factory_cannot_generate_owner_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _, _ = build(tmp_path, monkeypatch)
    with pytest.raises(loop.PhaseBlocked, match="FINAL_DEMO_UNCONFIRMED"):
        engine.final_resume(1)


@pytest.mark.parametrize("failure", ["revoke", "expire"])
def test_session_keeps_fetch_intent_and_does_not_retry_partial_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from tools import agent_epic_queue as queue
    from tools.agent_epic_session import Session

    from tests.test_agent_epic_bootstrap import checkpoint
    from tests.test_agent_epic_runner_e2e import _task

    engine, previous, target = build(tmp_path, monkeypatch)
    engine.store.save(
        checkpoint(version="3.0", pr=278)
        | previous
        | {
            "task": 250,
            "base_sha": previous["policy_sha"],
            "phase": "NEXT_TASK",
            "completed_phases": list(loop.PHASES[1:]),
            "status": "TASK_DONE",
        }
    )
    fetches = 0
    original = handover._git

    def changed(directory: Path, *args: str) -> str:
        nonlocal fetches
        result = original(directory, *args)
        if args[0] == "fetch":
            fetches += 1
            if failure == "revoke":
                mandate = json.loads((engine.state / "mandate.json").read_text())
                mandate["revoked"] = True
                private(engine.state / "mandate.json", mandate)
            else:
                monkeypatch.setattr(time, "time", lambda: 1001)
        return result

    monkeypatch.setattr(handover, "_git", changed)
    session = Session(
        store=engine.store,
        adapter=lambda *args: pytest.fail("completed task must not run again"),
        select=lambda: queue.plan_queue(
            epic=248, approved_order=(251,), tasks=[_task(251)]
        ),
        prepare=engine.prepare,
        reconcile_plan=engine.reconcile_plan,
        final_resume=lambda _: pytest.fail("final must not run"),
        maximum_tasks=3,
    )
    with pytest.raises(loop.LoopError, match="SUCCESSOR_PREPARATION_UNKNOWN"):
        session.resume()
    cursor = session.control.load("cursor")
    assert cursor is not None and cursor["pending_task"] == 251
    assert target.load() is None
    assert engine.reconcile_plan(previous, 251, target) == "UNKNOWN"
    with pytest.raises(loop.LoopError, match="ESCALATE_UNKNOWN_OUTCOME"):
        session.resume()
    assert fetches == 1


def test_final_sync_after_last_task_without_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, previous, _ = build(tmp_path, monkeypatch)
    owner = {
        "repository": previous["repository"],
        "roadmap_ref": previous["base_ref"],
        "roadmap_sha": previous["merge_sha"],
        "main_sha": previous["merge_sha"],
        "policy_sha": previous["policy_sha"],
    }
    engine._sync_final(owner, iterations=1)
    assert (
        _git(engine.root, "rev-parse", "refs/heads/roadmap/248-runner")
        == owner["roadmap_sha"]
    )
    assert _git(engine.root, "branch", "--show-current") == "main"


def test_final_sync_can_resume_after_verified_main_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, previous, _ = build(tmp_path, monkeypatch)
    owner = {
        "repository": previous["repository"],
        "roadmap_ref": previous["base_ref"],
        "roadmap_sha": previous["merge_sha"],
        "main_sha": previous["merge_sha"],
        "policy_sha": previous["policy_sha"],
    }
    monkeypatch.setattr(
        engine.github,
        "branches",
        lambda _: {owner["roadmap_ref"]: owner["roadmap_sha"], "main": "c" * 40},
    )
    with pytest.raises(loop.PhaseBlocked, match="FINAL_SYNC_UNCONFIRMED"):
        engine._sync_final(owner, iterations=1)
    engine._sync_final(owner, verified_main_merge="c" * 40, iterations=1)


def test_final_resume_pending_post_merge_ci_does_not_repeat_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import agent_epic_final as final
    from tools import agent_epic_final_delivery as driver
    from tools.agent_epic_final_runtime import FinalRuntime

    from tests.test_agent_epic_workflow import NeverAdapter

    session, previous, _ = build(tmp_path, monkeypatch)
    mandate = json.loads((session.state / "mandate.json").read_text())
    mandate["operations"] = sorted(driver.OPERATIONS)
    approved = planner.mandate_digest(mandate)
    mandate["approval"]["mandate_digest"] = approved
    private(session.state / "mandate.json", mandate)
    session.config = dict(session.config) | {
        "approved_mandate_digest": approved,
        "approved_final_digest": "sha256:" + "a" * 64,
    }
    owner = {
        "repository": previous["repository"],
        "epic": 248,
        "roadmap_ref": previous["base_ref"],
        "roadmap_sha": previous["merge_sha"],
        "main_sha": previous["policy_sha"],
        "policy_sha": previous["policy_sha"],
    }
    refs = {owner["roadmap_ref"]: owner["roadmap_sha"], "main": owner["main_sha"]}
    monkeypatch.setattr(session.github, "branches", lambda _: dict(refs))
    engine = FinalRuntime(
        root=session.root,
        directory=session.state,
        authority=lambda: session.authority(1),
        approval=lambda: {},
        approved_digest="unused",
        lesson=lambda: {},
        adapter=NeverAdapter(),
    )
    monkeypatch.setattr(engine, "_owner", lambda: owner)
    monkeypatch.setattr(runtime, "FinalRuntime", lambda **kwargs: engine)
    monkeypatch.setattr(session, "_adapter", lambda: None)
    writes: list[str] = []
    pending = True

    def gate(request: Any) -> final.FinalGate:
        if request.operation in driver.POST_MERGE and pending:
            return final.FinalGate("BLOCKED", "MAIN_POST_MERGE_CI_FAILED", "BLOCKED")
        return final.FinalGate(
            "PASS",
            "OK",
            "ROADMAP DONE"
            if request.operation in driver.POST_MERGE
            else "ROADMAP READY FOR RELEASE",
        )

    def effect(request: Any) -> dict[str, Any]:
        writes.append(request.operation)
        if request.operation == "create_roadmap_pr":
            return {"pr": 278}
        if request.operation == "merge_roadmap_pr":
            refs["main"] = "c" * 40
            return {"merge_sha": "c" * 40}
        return {"confirmed": True}

    monkeypatch.setattr(engine, "gate", gate)
    monkeypatch.setattr(engine.transport, "effect", effect)
    monkeypatch.setattr(
        engine.transport,
        "pull_request",
        lambda _: {"state": "MERGED", "mergeCommit": {"oid": "c" * 40}},
    )
    assert session.final_resume(1)["status"] == "BLOCKED"
    pending = False
    assert session.final_resume(1)["status"] == "ROADMAP_DONE"
    assert writes == [
        "create_roadmap_pr",
        "merge_roadmap_pr",
        "close_epic",
        "update_epic",
    ]
