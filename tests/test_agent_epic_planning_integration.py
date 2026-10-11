"""Admitted successor execution bindings and managed planning entry, offline."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_coordinator_handover as handover
from tools import agent_epic_delivery as delivery
from tools import agent_epic_entry as entry
from tools import agent_epic_install as install
from tools import agent_epic_loop as loop
from tools import agent_epic_planning as planning
from tools import agent_epic_session_runtime as runtime
from tools.agent_epic_runtime_cli import canonical_digest, private_json

from tests.test_agent_epic_entry import managed
from tests.test_agent_epic_session_runtime import build, private

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX installed runtime")
BODY = (
    "## Acceptance\n- minimal handover\n- exact gates\n\n"
    "## Other\nFull source review.\n"
)


def profile(mandate: dict[str, Any], task: int, sha: str) -> dict[str, Any]:
    template = {
        "schema_version": "1.0",
        "repository": mandate["repository"],
        "epic": mandate["epic"],
        "task": task,
        "baseline_ref": "main",
        "baseline_sha": sha,
        "roadmap_ref": mandate["roadmap_ref"],
        "roadmap_sha": sha,
        "consumer_visible": False,
        "not_applicable_reason": "Synthetic internal fixture",
        "scenario": None,
        "parameters": [],
        "old_argv": [],
        "new_argv": [],
    }
    return {
        "schema_version": "1.0",
        **{
            key: mandate[key]
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        },
        "mandate_digest": delivery.mandate_digest(mandate),
        "tasks": {
            str(task): {
                "criteria_sections": ["Acceptance"],
                "task_class": "internal",
                "demo_template": template,
            }
        },
        "limits": {
            "max_planner_attempts": 2,
            "max_review_attempts": 2,
            "max_input_chars": 100000,
            "max_output_chars": 20000,
            "max_paths": 4,
        },
    }


def admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[runtime.SessionRuntime, loop.CheckpointStore]:
    session, previous, store = build(tmp_path, monkeypatch)
    mandate = private_json(session.state / "mandate.json")
    mandate["operations"] += ["close_task", "update_epic", "merge_task_pr"]
    mandate["approval"]["mandate_digest"] = delivery.mandate_digest(mandate)
    private(session.state / "mandate.json", mandate)
    session.config = dict(session.config) | {
        "approved_mandate_digest": delivery.mandate_digest(mandate)
    }
    owner = profile(mandate, 251, previous["merge_sha"])
    plan = copy.deepcopy(session.plans["tasks"]["251"]["plan"])
    plan.update(
        base_sha=previous["merge_sha"],
        default_sha=previous["merge_sha"],
        head_ref="codex/251-admitted",
        task_class="internal",
    )
    plan["criteria_source"]["body_sha256"] = planning.body_digest(BODY)
    plan["acceptance_criteria"] = ["- minimal handover", "- exact gates"]
    plan["delivery_criterion_indices"] = []
    plan["allowed_paths"] = ["tools/existing.py"]
    plan["budgets"] = {
        "max_minutes": 5,
        "max_repair_iterations": 2,
        "max_report_chars": 10000,
    }
    proposal = {
        "schema_version": "1.0",
        "plan": plan,
        "demo_plan": owner["tasks"]["251"]["demo_template"],
        "criteria_mapping": [
            {
                "criterion_index": i,
                "allowed_paths": plan["allowed_paths"],
                "reason": "Minimal scope",
            }
            for i in range(2)
        ],
    }
    session.plans["tasks"] = {}
    private(session.state / "approved-plans.json", session.plans)
    private(session.state / "planning-profile.json", owner)
    session.config = dict(session.config) | {
        "schema_version": "3.0",
        "approved_plans_digest": canonical_digest(session.plans),
        "approved_planning_digest": canonical_digest(owner),
        "policy_root": str(tmp_path / "policy"),
        "approved_order": [251],
    }
    issue = {"number": 251, "state": "OPEN", "parent": {"number": 248}, "body": BODY}
    monkeypatch.setattr(handover.GitHubCLI, "issue", lambda *args: dict(issue))
    calls: list[str] = []

    def invoke(role: str, request: dict[str, Any]) -> dict[str, Any]:
        calls.append(role)
        assert request["issue_body"] == BODY
        if role == "planner":
            return proposal
        return {
            "schema_version": "1.0",
            "plan_digest": canonical_digest(request["proposal"]),
            "verdict": "PASS",
            "findings": [],
        }

    monkeypatch.setattr(runtime, "PlanningRoles", lambda **kwargs: invoke)
    monkeypatch.setattr(session, "_adapter", lambda: None)
    session.prepare(previous, 251, store, 1)
    assert calls == ["planner", "review"]
    assert store.load() is not None
    return session, store


def test_admitted_authority_uses_plan_default_and_declared_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, store = admitted(tmp_path, monkeypatch)
    engine = session.task_adapter(store, 1)
    grants = engine.authority()[0]["task_grants"]
    assert isinstance(grants, dict) and grants["251"]
    assert engine.continuation_queue is not None
    monkeypatch.setattr(session.github, "branches", lambda _: {"main": "f" * 40})
    with pytest.raises(loop.PhaseBlocked, match="PLANNING_ADMISSION_CHANGED"):
        engine.authority()


def test_admitted_authority_keeps_checkpoint_and_handover_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, store = admitted(tmp_path, monkeypatch)
    engine = session.task_adapter(store, 1)
    assert engine.authority()[0]["epic"] == 248
    saved = store.load()
    assert saved is not None
    store.save(saved | {"head_ref": "codex/251-replacement"})
    with pytest.raises(loop.PhaseBlocked, match="PLANNING_ADMISSION_CHANGED"):
        engine.authority()
    store.save(saved)
    value = private_json(store.directory / "handover.json")
    value["budgets"]["max_minutes"] = 1
    value["approval"]["plan_digest"] = handover.handover_digest(value)
    private(store.directory / "handover.json", value)
    with pytest.raises(loop.PhaseBlocked, match="PLANNING_ADMISSION_CHANGED"):
        engine.authority()


@pytest.mark.parametrize(
    "field",
    [
        "criteria_source",
        "acceptance_criteria",
        "delivery_criterion_indices",
        "causal_scope",
        "allowed_paths",
        "budgets",
        "task_class",
        "mandate_provenance",
    ],
)
def test_admitted_handover_cannot_replace_execution_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    session, store = admitted(tmp_path, monkeypatch)
    value = private_json(store.directory / "handover.json")
    replacements = {
        "criteria_source": value["criteria_source"]
        | {"body_sha256": "sha256:" + "e" * 64},
        "acceptance_criteria": ["- exact gates"],
        "delivery_criterion_indices": [0],
        "causal_scope": ["replacement"],
        "allowed_paths": ["tools/new.py"],
        "budgets": value["budgets"] | {"max_minutes": 1},
        "task_class": "documentation",
        "mandate_provenance": value["mandate_provenance"] | {"policy_sha": "e" * 40},
    }
    value[field] = replacements[field]
    value["approval"]["plan_digest"] = handover.handover_digest(value)
    private(store.directory / "handover.json", value)
    with pytest.raises(loop.PhaseBlocked, match="PLANNING_ADMISSION_CHANGED"):
        session.task_adapter(store, 1).authority()


def test_config3_prepare_readiness_and_digest_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, installed, state = managed(tmp_path, monkeypatch)
    (state / "launch.json").unlink()
    mandate = private_json(state / "mandate.json")
    owner = profile(mandate, 253, mandate["policy_sha"])
    # A planning entry needs an already owner-granted task.
    mandate["task_grants"]["253"] = {
        "causal_scope": ["synthetic"],
        "allowed_paths": ["task.txt"],
    }
    mandate["approval"]["mandate_digest"] = delivery.mandate_digest(mandate)
    owner["mandate_digest"] = delivery.mandate_digest(mandate)
    private(state / "mandate.json", mandate)
    private(state / "planning-profile.json", owner)
    config = private_json(state / "epic-runtime.json") | {
        "schema_version": "3.0",
        "approved_launch_digest": None,
        "approved_planning_digest": canonical_digest(owner),
        "approved_mandate_digest": delivery.mandate_digest(mandate),
    }
    private(state / "epic-runtime.json", config)
    report = entry.prepare(root, installed, state)
    config["approved_launch_digest"] = report["candidate_digest"]
    private(state / "epic-runtime.json", config)
    assert (
        install.readiness(
            root,
            installed,
            state,
            epic=True,
            github_identity="OWNER",
            namespace_probe=lambda: None,
            github_probe=lambda *_: None,
        )["status"]
        == "READY"
    )
    owner["limits"]["max_paths"] += 1
    private(state / "planning-profile.json", owner)
    from tools import agent_epic_runtime_cli as guards

    with pytest.raises(guards.DeploymentError, match="PLANNING_APPROVAL_MISMATCH"):
        guards.managed_launch_guard(state, root, allow_initial=True)
    config["approved_planning_digest"] = canonical_digest(owner)
    private(state / "epic-runtime.json", config)
    with pytest.raises(guards.DeploymentError, match="LAUNCH_CONFIG_CHANGED"):
        guards.managed_launch_guard(state, root, allow_initial=True)


@pytest.mark.parametrize("status", ["INTENT", "UNKNOWN", "APPLIED"])
def test_closed_admitted_task_requires_exact_current_close_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    session, store = admitted(tmp_path, monkeypatch)
    loaded = store.load()
    assert loaded is not None
    saved = dict(loaded)
    saved.update(
        pr=278,
        head_sha="b" * 40,
        merge_sha="c" * 40,
        phase="DEMO",
        pending_phase="TASK_DONE",
        completed_phases=list(loop.PHASES[1:6]),
    )
    store.save(saved)
    monkeypatch.setattr(
        handover.GitHubCLI,
        "issue",
        lambda *args: {
            "number": 251,
            "state": "CLOSED",
            "parent": {"number": 248},
            "body": BODY,
        },
    )
    engine = session.task_adapter(store, 1)
    request = engine._request("close_task")
    ledger = delivery.Ledger(store.directory, session.root)
    ledger.save(
        {
            request.operation_id: delivery._entry(
                request,
                status,
                {"task": 251, "merge_sha": "c" * 40} if status == "APPLIED" else None,
            )
        }
    )
    assert engine.authority()[0]["epic"] == 248
    ledger.save({request.operation_id: delivery._entry(request, "FAILED", None)})
    with pytest.raises(loop.PhaseBlocked, match="CRITERIA_PROVENANCE_MISMATCH"):
        engine.authority()
    ledger.save(
        {
            request.operation_id: delivery._entry(request, status, None)
            | {"request": delivery._request_fields(request) | {"head_sha": "d" * 40}}
        }
    )
    with pytest.raises(loop.PhaseBlocked, match="CRITERIA_PROVENANCE_MISMATCH"):
        engine.authority()


@pytest.mark.parametrize("recovered", [False, True])
def test_genuinely_admitted_successor_closes_updates_and_recalculates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovered: bool
) -> None:
    from tools import agent_coordinator_branch as branch

    from tests.test_agent_coordinator_delivery import _gate, _green_checks
    from tests.test_agent_epic_handover import _git

    session, store = admitted(tmp_path, monkeypatch)
    loaded = store.load()
    assert loaded is not None
    saved = dict(loaded)
    (session.root / "tools/existing.py").write_text("VALUE = 3\n")
    _git(session.root, "add", ".")
    _git(session.root, "commit", "-m", "synthetic admitted execution")
    head = _git(session.root, "rev-parse", "HEAD")
    _git(session.root, "update-ref", "refs/heads/roadmap/248-runner", head)
    saved.update(
        pr=278,
        head_sha=head,
        merge_sha=head,
        phase="DEMO",
        pending_phase="TASK_DONE",
        completed_phases=list(loop.PHASES[1:6]),
    )
    store.save(saved)
    refs = {"main": saved["base_sha"], saved["base_ref"]: head}
    monkeypatch.setattr(session.github, "branches", lambda _: dict(refs))
    monkeypatch.setattr(branch.GitHubCLI, "branches", lambda *args: dict(refs))
    state = "CLOSED" if recovered else "OPEN"
    reads: list[int] = []

    class Issues:
        def issue(self, repository: str, number: int) -> dict[str, Any]:
            reads.append(number)
            if number == 248:
                return {
                    "number": 248,
                    "state": "OPEN",
                    "subIssues": {
                        "nodes": [{"number": 250}, {"number": 251}, {"number": 251}]
                    },
                }
            return {
                "number": number,
                "title": "Synthetic task",
                "url": "https://github.com/OWNER/repo/issues/251",
                "state": state if number == 251 else "CLOSED",
                "parent": {"number": 248},
                "body": BODY,
                "blockedBy": {"nodes": []},
            }

    issues = Issues()
    monkeypatch.setattr(session, "issues", issues)
    monkeypatch.setattr(handover.GitHubCLI, "issue", issues.issue)
    engine = session.task_adapter(store, 1)
    engine.issues = issues
    engine.artifacts = lambda: {
        "gate": _gate(base_sha=saved["base_sha"], snapshot_commit=head),
        "allowed_paths": ["tools/existing.py"],
    }

    class GitHub:
        def pull_request(self, repository: str, number: int) -> dict[str, Any]:
            return {
                "number": number,
                "state": "MERGED",
                "isCrossRepository": False,
                "baseRefName": saved["base_ref"],
                "baseRefOid": saved["base_sha"],
                "headRefName": saved["head_ref"],
                "headRefOid": head,
                "mergeCommit": {"oid": head},
                "files": [{"path": "tools/existing.py"}],
            }

        def ref_sha(self, repository: str, ref: str) -> str:
            return str(refs[ref])

        def pr_checks(self, repository: str, number: int) -> list[dict[str, str]]:
            return _green_checks()

        def commit_checks(self, repository: str, sha: str) -> list[dict[str, str]]:
            assert sha == head
            return _green_checks()

    engine.github = GitHub()
    writes: list[str] = []
    comments: list[dict[str, Any]] = []

    def command(argv: Any) -> str:
        nonlocal state
        assert tuple(argv[:3]) == ("gh", "issue", "close")
        writes.append("close_task")
        state = "CLOSED"
        return ""

    def post(argv: Any) -> dict[str, Any]:
        assert "POST" in argv
        writes.append("update_epic")
        request = engine._request("update_epic")
        comments.append(
            {
                "id": 123,
                "body": engine.transport.comment(request),
                "user": {"login": "OWNER"},
            }
        )
        return comments[-1]

    def read_json(argv: Any) -> Any:
        engine.authority()
        if tuple(argv[:3]) == ("gh", "issue", "view"):
            return issues.issue("OWNER/repo", 251)
        return [comments]

    monkeypatch.setattr(engine.transport, "command", command)
    monkeypatch.setattr(engine.transport, "json", post)
    monkeypatch.setattr(engine.transport, "read_json", read_json)
    ledger = delivery.Ledger(store.directory, session.root)
    if recovered:
        request = engine._request("close_task")
        ledger.save({request.operation_id: delivery._entry(request, "UNKNOWN", None)})
    assert engine.effect("TASK_DONE") == {"phase": "TASK_DONE"}
    assert writes == (["update_epic"] if recovered else ["close_task", "update_epic"])
    entries = ledger.load()
    assert {entry["status"] for entry in entries.values()} == {"APPLIED"}
    assert {entry["operation"] for entry in entries.values()} == {
        "close_task",
        "update_epic",
    }
    saved.update(
        phase="TASK_DONE",
        pending_phase="NEXT_TASK",
        completed_phases=list(loop.PHASES[1:7]),
    )
    store.save(saved)
    reads.clear()
    assert engine.effect("NEXT_TASK") == {"phase": "NEXT_TASK"}
    assert reads.count(251) == 1
    assert session.select().state == "FINAL_GATE"
