"""Two synthetic supervised tasks through admission and real delivery guards."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_coordinator_branch as branch
from tools import agent_coordinator_handover as handover
from tools import agent_epic_delivery as delivery
from tools import agent_epic_loop as loop
from tools import agent_epic_session_runtime as runtime
from tools import agent_epic_workflow as reviewed
from tools import agent_gate as gate
from tools import agent_orchestrate as workflow
from tools.agent_epic_runtime_cli import canonical_digest, private_json

from tests.test_agent_coordinator_delivery import _green_checks
from tests.test_agent_epic_handover import _git
from tests.test_agent_epic_planning_integration import blocker, planning_setup
from tests.test_agent_epic_session_runtime import private

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX installed runtime")


def test_two_task_resume_runs_admitted_successor_and_delivery_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, previous, successor, issues, roles = planning_setup(tmp_path, monkeypatch)
    root, base = session.root, previous["merge_sha"]
    path = "tools/existing.py"
    mandate = private_json(session.state / "mandate.json")
    mandate["operations"] += ["commit_task", "push_task", "create_task_pr"]
    mandate["task_grants"] = {
        str(task): {"causal_scope": ["epic mandate handover"], "allowed_paths": [path]}
        for task in (250, 251)
    }
    mandate["approval"]["mandate_digest"] = delivery.mandate_digest(mandate)
    private(session.state / "mandate.json", mandate)
    session.config = dict(session.config) | {
        "approved_mandate_digest": delivery.mandate_digest(mandate),
        "approved_order": [250, 251],
    }
    issues[250]["state"] = "OPEN"
    issues[251]["blockedBy"] = {"nodes": [blocker(250, "OPEN")]}
    refs: dict[str, str] = {"main": base, previous["base_ref"]: base}
    pulls: dict[int, dict[str, Any]] = {}
    comments: list[dict[str, Any]] = []
    writes: list[tuple[int, str]] = []
    phases: list[tuple[int, str]] = []
    executed: list[int] = []

    class Remote:
        def issue(self, repository: str, number: int) -> dict[str, Any]:
            return session.issues.issue(repository, number)

        def repository(self, repository: str) -> dict[str, Any]:
            return {"nameWithOwner": repository, "defaultBranchRef": {"name": "main"}}

        def branches(self, repository: str) -> dict[str, str]:
            return dict(refs)

        def compare(self, *args: Any) -> str:
            return "ahead"

        def ref_sha(self, repository: str, ref: str) -> str:
            return refs[ref]

        def pull_request(self, repository: str, number: int) -> dict[str, Any]:
            return copy.deepcopy(pulls[number])

        def pr_checks(self, repository: str, number: int) -> list[dict[str, str]]:
            assert number in pulls
            return _green_checks()

        def commit_checks(self, repository: str, sha: str) -> list[dict[str, str]]:
            assert sha in {
                value["mergeCommit"]["oid"]
                for value in pulls.values()
                if value["mergeCommit"]
            }
            return _green_checks()

    remote = Remote()
    session.github = remote
    monkeypatch.setattr(branch.GitHubCLI, "branches", lambda *args: dict(refs))
    original_git = handover._git

    def local_git(directory: Path, *args: str) -> str:
        if args[0] == "fetch":
            for ref in ("main", previous["base_ref"]):
                _git(root, "update-ref", f"refs/remotes/origin/{ref}", refs[ref])
            return ""
        return original_git(directory, *args)

    monkeypatch.setattr(handover, "_git", local_git)

    # Precompute the synthetic first artifact to pin the successor demo profile
    # before starting the supervised session. This is no owner demo receipt.
    (root / path).write_text("VALUE = 250\n")
    with gate.trusted_snapshot_session(
        root=root, base_sha=base, allowed_paths=(path,)
    ) as snapshot:
        first_head = snapshot.snapshot_commit
    (root / path).write_text("VALUE = 2\n")
    owner = private_json(session.state / "planning-profile.json")
    owner["mandate_digest"] = delivery.mandate_digest(mandate)
    owner["tasks"]["251"]["demo_template"]["roadmap_sha"] = first_head
    private(session.state / "planning-profile.json", owner)
    session.config = dict(session.config) | {
        "approved_planning_digest": canonical_digest(owner)
    }

    original_role = getattr(runtime, "PlanningRoles")(
        root=root, policy_root=tmp_path, policy_sha=previous["policy_sha"]
    )

    def invoke(role: str, request: dict[str, Any]) -> dict[str, Any]:
        assert executed == [250]
        assert request["dependencies"] == [{"number": 250, "state": "CLOSED"}]
        reserved = private_json(session.state / "planning/251/admission.json")
        assert reserved["pending"]["request_digest"] == canonical_digest(request)
        result = original_role(role, request)
        if role == "planner":
            result = copy.deepcopy(dict(result))
            result["plan"]["base_sha"] = first_head
            result["demo_plan"] = copy.deepcopy(owner["tasks"]["251"]["demo_template"])
        return dict(result)

    monkeypatch.setattr(runtime, "PlanningRoles", lambda **kwargs: invoke)
    seed = copy.deepcopy(original_role("planner", {"issue_body": issues[250]["body"]}))
    roles.clear()
    seed["plan"].update(task=250, head_ref="codex/250-seed")
    seed["plan"]["criteria_source"]["issue"] = 250
    seed["demo_plan"]["task"] = 250
    session.plans["tasks"] = {"250": {key: seed[key] for key in ("plan", "demo_plan")}}
    private(session.state / "approved-plans.json", session.plans)
    session.config = dict(session.config) | {
        "approved_plans_digest": canonical_digest(session.plans)
    }
    session.prepare(previous, 250, session.store, 0)
    session.plans["initial_handover_digest"] = handover.handover_digest(
        private_json(session.state / "handover.json")
    )
    private(session.state / "approved-plans.json", session.plans)
    session.config = dict(session.config) | {
        "approved_plans_digest": canonical_digest(session.plans)
    }

    def run(self: reviewed.ReviewedWorkflow) -> dict[str, Any]:
        task = self.value["task"]
        executed.append(task)
        if task == 251:
            record = private_json(session.state / "planning/251/admission.json")
            assert record["status"] == "ADMITTED" and record["attempts"] == {
                "planner": 1,
                "review": 1,
            }
        (root / path).write_text(f"VALUE = {task}\n")
        with gate.trusted_snapshot_session(
            root=root, base_sha=self.value["base_sha"], allowed_paths=(path,)
        ) as snapshot:
            evidence = gate.build_evidence(
                snapshot,
                [
                    gate.CheckResult(
                        id=name,
                        argv=gate.CHECK_COMMANDS[name],
                        status="passed",
                        exit_code=0,
                        duration_seconds=0,
                        summary="synthetic offline integration fixture",
                        metrics={},
                    )
                    for name in gate.PROFILES[gate.FULL_PROFILE]
                ],
            )
            head = snapshot.snapshot_commit
        artifact = {
            "gate": evidence,
            "allowed_paths": [path],
            "review_receipt": {
                "schema_version": "2.0",
                "task": task,
                "base_sha": self.value["base_sha"],
                "head_ref": self.value["head_ref"],
                "policy_sha": previous["policy_sha"],
                "verdict": "PASS",
                "reviewed_state": {"snapshot_commit": head},
            },
        }
        self.artifact_store.save(self.key, artifact)
        return artifact

    monkeypatch.setattr(reviewed.ReviewedWorkflow, "run", run)
    original_adapter = session.task_adapter

    def adapter(store: loop.CheckpointStore, iterations: int) -> Any:
        engine = original_adapter(store, iterations)
        engine.github, engine.issues = remote, session.issues
        original_effect = engine.effect

        def effect(phase: str) -> Any:
            current = store.load()
            assert current is not None
            phases.append((current["task"], phase))
            return original_effect(phase)

        monkeypatch.setattr(engine, "effect", effect)
        monkeypatch.setattr(
            engine.transport, "pr_body", lambda **kwargs: "Synthetic fixture"
        )

        def command(argv: Any) -> str:
            current = store.load()
            assert current is not None
            task = current["task"]
            if tuple(argv[:2]) == ("git", "push"):
                writes.append((task, "push_task"))
                head, ref = argv[-1].split(":refs/heads/")
                refs[ref] = head
                return ""
            if tuple(argv[:2]) == ("git", "ls-remote"):
                ref = argv[-1].removeprefix("refs/heads/")
                return f"{refs[ref]}\trefs/heads/{ref}\n" if ref in refs else ""
            if argv[0] == "git":
                return _git(root, *argv[1:])
            if tuple(argv[:3]) == ("gh", "pr", "merge"):
                writes.append((task, "merge_task_pr"))
                pull = pulls[int(argv[3])]
                pull.update(state="MERGED", mergeCommit={"oid": pull["headRefOid"]})
                refs[current["base_ref"]] = pull["headRefOid"]
                return ""
            assert tuple(argv[:3]) == ("gh", "issue", "close")
            writes.append((task, "close_task"))
            issues[task]["state"] = "CLOSED"
            issues[251]["blockedBy"]["nodes"][0]["state"] = issues[250]["state"]
            return ""

        def post(argv: Any) -> dict[str, Any]:
            current = store.load()
            assert current is not None
            task = current["task"]
            if "repos/OWNER/repo/pulls" in argv:
                writes.append((task, "create_task_pr"))
                number = task + 1000
                pulls[number] = {
                    "number": number,
                    "state": "OPEN",
                    "isCrossRepository": False,
                    "baseRefName": current["base_ref"],
                    "baseRefOid": current["base_sha"],
                    "headRefName": current["head_ref"],
                    "headRefOid": refs[current["head_ref"]],
                    "mergeCommit": None,
                    "files": [{"path": path}],
                }
                return {"number": number}
            writes.append((task, "update_epic"))
            comments.append(
                {
                    "id": task,
                    "body": engine.transport.comment(engine._request("update_epic")),
                    "user": {"login": "OWNER"},
                }
            )
            return comments[-1]

        def read_json(argv: Any) -> Any:
            engine.authority()
            if tuple(argv[:3]) == ("gh", "issue", "view"):
                return remote.issue("OWNER/repo", int(argv[3]))
            if tuple(argv[:3]) == ("gh", "pr", "view"):
                return remote.pull_request("OWNER/repo", int(argv[3]))
            if "pulls?state=all" in argv[-1]:
                ref = argv[-1].split("&head=OWNER:")[1].split("&base=")[0]
                return [
                    [
                        {
                            "number": number,
                            "state": "open",
                            "head": {
                                "sha": pull["headRefOid"],
                                "ref": ref,
                                "repo": {"full_name": "OWNER/repo"},
                            },
                            "base": {
                                "sha": pull["baseRefOid"],
                                "ref": pull["baseRefName"],
                                "repo": {"full_name": "OWNER/repo"},
                            },
                        }
                        for number, pull in pulls.items()
                        if pull["headRefName"] == ref
                    ]
                ]
            return [comments]

        monkeypatch.setattr(engine.transport, "command", command)
        monkeypatch.setattr(engine.transport, "json", post)
        monkeypatch.setattr(engine.transport, "read_json", read_json)
        return engine

    monkeypatch.setattr(session, "task_adapter", adapter)
    # The existing final owner approval boundary is the expected terminal stop.
    with pytest.raises(loop.PhaseBlocked, match="FINAL_DEMO_UNCONFIRMED"):
        session.resume()
    assert executed == [250, 251] and roles == ["planner", "review"]
    assert phases == [(task, phase) for task in (250, 251) for phase in loop.PHASES[1:]]
    assert writes == [
        (task, operation)
        for task in (250, 251)
        for operation in (
            "push_task",
            "create_task_pr",
            "merge_task_pr",
            "close_task",
            "update_epic",
        )
    ]
    first, second = session.store.load(), successor.load()
    assert first is not None and second is not None
    assert first["status"] == second["status"] == "TASK_DONE"
    assert second["base_sha"] == first["merge_sha"] == first_head
    assert second["policy_sha"] == first["policy_sha"] == previous["policy_sha"]
    assert session.select().state == "FINAL_GATE"
    cursor = workflow.StateStore(session.state / "session-control").load("cursor")
    assert (
        cursor is not None
        and cursor["completed_tasks"] == [250, 251]
        and cursor["mode"] == "FINAL"
    )
    for store in (session.store, successor):
        entries = delivery.Ledger(store.directory, root).load()
        assert {entry["operation"] for entry in entries.values()} == {
            "commit_task",
            "push_task",
            "create_task_pr",
            "merge_task_pr",
            "close_task",
            "update_epic",
        }
        assert {entry["status"] for entry in entries.values()} == {"APPLIED"}
        assert not (store.directory / "demo-completion.json").exists()
    before = list(writes)
    with pytest.raises(loop.PhaseBlocked, match="FINAL_DEMO_UNCONFIRMED"):
        session.resume()
    assert writes == before and executed == [250, 251]
