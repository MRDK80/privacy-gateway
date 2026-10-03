"""Concrete phase wiring tested offline with synthetic structural facts."""

from pathlib import Path
from typing import Any

import pytest
from tools import agent_coordinator_delivery as assessment
from tools import agent_epic_delivery as delivery
from tools import agent_epic_loop as loop
from tools import agent_epic_runtime as runtime

from tests.test_agent_consumer_demo import _plan
from tests.test_agent_coordinator_delivery import _gate, _green_checks
from tests.test_agent_epic_bootstrap import checkpoint
from tests.test_agent_epic_delivery import _mandate


class GitHub:
    def __init__(self) -> None:
        self.merged = False
        self.pending = False
        self.cross_repository = False

    def pull_request(self, repository: str, number: int) -> dict[str, Any]:
        return {
            "number": number,
            "isCrossRepository": self.cross_repository,
            "state": "MERGED" if self.merged else "OPEN",
            "baseRefName": "roadmap/248-autonomous-epic-runner",
            "baseRefOid": "a" * 40,
            "headRefName": "codex/274-task",
            "headRefOid": "b" * 40,
            "mergeCommit": {"oid": "c" * 40} if self.merged else None,
            "files": [{"path": "task.txt"}],
        }

    def pr_checks(self, repository: str, number: int) -> list[dict[str, str]]:
        return (
            [item | {"state": "PENDING"} for item in _green_checks()]
            if self.pending
            else _green_checks()
        )

    def commit_checks(self, repository: str, sha: str) -> list[dict[str, str]]:
        assert sha == "c" * 40
        return self.pr_checks(repository, 278)

    def ref_sha(self, repository: str, ref: str) -> str:
        return "c" * 40 if self.merged else "a" * 40


class Issues:
    def issue(self, repository: str, number: int) -> dict[str, Any]:
        return {
            "number": number,
            "parent": {"number": 248},
            "state": "OPEN",
            "subIssues": {"nodes": []},
        }


def build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> tuple[runtime.TaskPhaseRuntime, GitHub]:
    saved = checkpoint(pr=278) | {
        "head_sha": "b" * 40,
        "phase": phase,
        "completed_phases": list(loop.PHASES[1 : loop.PHASES.index(phase) + 1]),
        "status": "RUNNING",
        "pending_phase": loop.PHASES[loop.PHASES.index(phase) + 1],
        "merge_sha": "c" * 40 if loop.PHASES.index(phase) >= 4 else None,
    }
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    store.save(saved)
    monkeypatch.setattr(runtime, "_repository_identity", lambda _: "OWNER/repository")
    mandate = _mandate()
    grants = mandate["task_grants"]
    assert isinstance(grants, dict)
    grants["274"] = {"causal_scope": ["synthetic"], "allowed_paths": ["task.txt"]}
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approval["mandate_digest"] = delivery.mandate_digest(mandate)
    adapter = runtime.TaskPhaseRuntime(
        store=store,
        authority=lambda: (
            mandate,
            delivery.mandate_digest(mandate),
            delivery.MandateContext(200, "OWNER", 150, 0, 0),
        ),
        artifacts=lambda: {"gate": _gate(), "allowed_paths": ["task.txt"]},
        demo_plan=lambda: {},
        approved_order=(),
    )
    gh = GitHub()
    adapter.github = gh
    adapter.issues = Issues()
    return adapter, gh


def test_pr_ci_uses_real_assessment_not_checkpoint_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, gh = build(tmp_path, monkeypatch, "RUN_TASK")
    assert adapter.effect("PR_CI") == {"phase": "PR_CI"}
    gh.pending = True
    with pytest.raises(loop.PhaseBlocked, match="CI_PENDING"):
        adapter.effect("PR_CI")


@pytest.mark.parametrize("head_state", ["changed", "unavailable", "unchanged"])
def test_remote_head_checked_after_pr_body_before_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, head_state: str
) -> None:
    adapter, gh = build(tmp_path, monkeypatch, "RUN_TASK")
    saved: dict[str, Any] = checkpoint() | {"head_sha": "b" * 40}
    adapter.store.save(saved)
    remote_head = "b" * 40
    writes: list[object] = []

    def ref_sha(repository: str, ref: str) -> str:
        if ref == saved["head_ref"] and head_state == "unavailable":
            raise assessment.GitHubError("SYNTHETIC_UNAVAILABLE")
        return remote_head if ref == saved["head_ref"] else "a" * 40

    def body(**kwargs: object) -> str:
        nonlocal remote_head
        if head_state == "changed":
            remote_head = "d" * 40
        return "Synthetic prepared report"

    monkeypatch.setattr(gh, "ref_sha", ref_sha)
    monkeypatch.setattr(adapter.transport, "pr_body", body)
    monkeypatch.setattr(
        adapter.transport,
        "command",
        lambda argv: "b" * 40 if "rev-parse" in argv else "codex/274-task",
    )
    monkeypatch.setattr(adapter.transport, "json", lambda argv: writes.append(argv))
    monkeypatch.setattr(
        adapter.transport,
        "reconcile",
        lambda _: ("APPLIED", {"pr": 278, "head_sha": "b" * 40}),
    )
    request = delivery.Request(
        operation_id="synthetic-create",
        operation="create_task_pr",
        schema_version="2.0",
        **{key: saved[key] for key in loop.IDENTITY_KEYS},
    )
    if head_state == "unchanged":
        assert adapter.transport.effect(request)["pr"] == 278
        assert len(writes) == 1
    else:
        with pytest.raises(delivery.OutcomeUnknown):
            adapter.transport.effect(request)
        assert writes == []


def test_pending_ci_prevents_merge_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, gh = build(tmp_path, monkeypatch, "PR_CI")
    gh.pending = True
    monkeypatch.setattr(
        adapter.transport, "effect", lambda _: pytest.fail("no write before CI")
    )
    with pytest.raises(loop.PhaseBlocked, match="CI_PENDING"):
        adapter.effect("MERGE")


def test_effect_requires_supervisor_pending_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, _ = build(tmp_path, monkeypatch, "RUN_TASK")
    with pytest.raises(loop.LoopError, match="PHASE_ORDER_INVALID"):
        adapter.effect("MERGE")


def test_missing_workflow_is_explicit_block_not_fake_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, _ = build(tmp_path, monkeypatch, "PLAN")
    with pytest.raises(loop.PhaseBlocked, match="WORKFLOW_NOT_CONFIGURED"):
        adapter.effect("RUN_TASK")


def test_post_merge_requires_separate_commit_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, gh = build(tmp_path, monkeypatch, "MERGE")
    gh.merged = True
    gh.pending = True
    with pytest.raises(loop.PhaseBlocked, match="CI_PENDING"):
        adapter.effect("POST_MERGE")
    gh.pending = False
    assert adapter.effect("POST_MERGE") == {"phase": "POST_MERGE"}


def test_next_task_never_fabricates_final_gate_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, _ = build(tmp_path, monkeypatch, "TASK_DONE")
    with pytest.raises(loop.PhaseBlocked, match="FINAL_GATE_REQUIRED"):
        adapter.effect("NEXT_TASK")


def test_cross_repository_pr_is_not_delivery_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, gh = build(tmp_path, monkeypatch, "RUN_TASK")
    gh.cross_repository = True
    with pytest.raises(loop.LoopError, match="LIVE_IDENTITY_CHANGED"):
        adapter.live()


def test_valid_demo_plan_alone_cannot_authorize_task_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, gh = build(tmp_path, monkeypatch, "DEMO")
    gh.merged = True
    plan = _plan() | {"task": 274, "roadmap_sha": "c" * 40, "baseline_sha": "c" * 40}
    adapter.demo_plan = lambda: plan
    monkeypatch.setattr(
        adapter, "_write", lambda _: pytest.fail("no write without demo receipt")
    )
    with pytest.raises(loop.PhaseBlocked, match="DEMO_COMPLETION_REQUIRED"):
        adapter.effect("TASK_DONE")
    from tools.agent_epic_final_runtime import digest

    receipt = {
        key: plan[key]
        for key in ("repository", "epic", "task", "baseline_sha", "roadmap_sha")
    }
    receipt.update(
        schema_version="1.0",
        plan_digest=digest(plan),
        owner_identity="OWNER",
        completed_by_owner=True,
    )
    adapter.demo_completion = lambda: receipt
    assert adapter._demo(require_completion=True)["status"] == "CONSUMER_DEMO_READY"
    receipt["roadmap_sha"] = "a" * 40
    with pytest.raises(loop.PhaseBlocked, match="DEMO_COMPLETION_REQUIRED"):
        adapter._demo(require_completion=True)


def test_justified_non_applicable_demo_needs_no_completion_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, gh = build(tmp_path, monkeypatch, "DEMO")
    gh.merged = True
    adapter.demo_plan = lambda: (
        _plan()
        | {
            "task": 274,
            "roadmap_sha": "c" * 40,
            "baseline_sha": "c" * 40,
            "consumer_visible": False,
            "not_applicable_reason": "documentation only",
            "scenario": None,
            "parameters": [],
            "old_argv": [],
            "new_argv": [],
        }
    )
    assert adapter._demo(require_completion=True)["status"] == "DEMO_NOT_APPLICABLE"
