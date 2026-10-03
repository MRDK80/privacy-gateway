"""Bootstrap sequence with real isolated Git objects and fake remote transport."""

from pathlib import Path

import pytest
from tools import agent_coordinator_branch as branch
from tools import agent_epic_bootstrap as bootstrap
from tools import agent_epic_delivery as delivery
from tools import agent_epic_loop as loop
from tools import agent_epic_runtime as runtime
from tools.agent_epic_workflow import ReviewedWorkflow

from tests.test_agent_epic_bootstrap import checkpoint
from tests.test_agent_epic_delivery import _mandate
from tests.test_agent_epic_runtime import Issues
from tests.test_agent_epic_snapshot import git, setup
from tests.test_agent_epic_workflow import NeverAdapter


@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("strict_subset", [False, True])
def test_real_exact_commit_precedes_push_and_pr_without_live_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned: bool, strict_subset: bool
) -> None:
    root = tmp_path / "repo"
    request, gate, review = setup(root)
    initial = checkpoint() | {
        "base_ref": request.base_ref,
        "base_sha": request.base_sha,
        "head_ref": request.head_ref,
        "head_sha": request.head_sha,
        "pending_phase": "RUN_TASK",
        "status": "RUNNING",
    }
    if pinned:
        initial.update(schema_version="3.0", policy_sha=request.base_sha)
        review.update(schema_version="2.0", policy_sha=request.base_sha)
        git(root, "remote", "add", "origin", "https://github.com/OWNER/repository.git")
        monkeypatch.setattr(
            branch.GitHubCLI,
            "branches",
            lambda _self, _repo: {request.base_ref: request.base_sha},
        )
    store = loop.CheckpointStore(tmp_path / "state", root)
    store.save(initial)
    mandate = _mandate()
    if pinned:
        mandate["schema_version"] = "3.0"
    mandate["policy_sha"] = request.base_sha
    mandate["roadmap_ref"] = request.base_ref
    mandate["task_grants"] = {
        "274": {
            "causal_scope": ["synthetic"],
            "allowed_paths": ["task.txt", "unused.txt"]
            if strict_subset
            else ["task.txt"],
        }
    }
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approval["mandate_digest"] = delivery.mandate_digest(mandate)

    def authority() -> tuple[dict[str, object], str, delivery.MandateContext]:
        return (
            mandate,
            delivery.mandate_digest(mandate),
            delivery.MandateContext(200, "OWNER", 150, 0, 0),
        )

    reviewed = ReviewedWorkflow(
        root=root,
        directory=store.directory,
        value=initial
        | (
            {
                "schema_version": "2.0",
                "causal_scope": ["synthetic"],
                "allowed_paths": ["task.txt"],
                "trusted_policy": {
                    "source": "pinned_policy_sha",
                    "policy_sha": request.base_sha,
                },
            }
            if pinned
            else {}
        ),
        approved_digest="synthetic",
        adapter=NeverAdapter(),
    )
    monkeypatch.setattr(
        reviewed,
        "run",
        lambda: {"gate": gate, "review_receipt": review, "allowed_paths": ["task.txt"]},
    )
    engine = runtime.TaskPhaseRuntime(
        store=store,
        authority=authority,
        artifacts=reviewed.artifacts,
        demo_plan=lambda: {},
        approved_order=(),
    )
    monkeypatch.setattr(engine.github, "ref_sha", lambda repo, ref: request.base_sha)
    engine.issues = Issues()
    monkeypatch.setattr(bootstrap, "_repository_identity", lambda _: request.repository)
    calls: list[str] = []
    snapshot = gate["snapshot"]
    assert isinstance(snapshot, dict)

    def effect(value: delivery.Request) -> dict[str, object]:
        # Git commit already equals the actual reviewed artifact before either write.
        assert git(root, "rev-parse", "HEAD") == snapshot["snapshot_commit"]
        calls.append(value.operation)
        return (
            {"head_sha": value.head_sha, "pr": 278}
            if value.operation == "create_task_pr"
            else {"head_sha": value.head_sha}
        )

    monkeypatch.setattr(engine.transport, "effect", effect)
    result = bootstrap.BootstrapTask(engine, reviewed, authority)()
    assert result["phase"] == "RUN_TASK"
    assert calls == ["push_task", "create_task_pr"]
    bound = result["delivery_identity"]
    assert isinstance(bound, dict) and bound["pr"] == 278
    assert bound["head_sha"] == snapshot["snapshot_commit"]
    assert git(root, "rev-parse", request.base_ref) == request.base_sha
    entries = delivery.Ledger(store.directory, root).load()
    assert {entry["operation"] for entry in entries.values()} == {
        "commit_task",
        "push_task",
        "create_task_pr",
    }
    assert all(entry["status"] == "APPLIED" for entry in entries.values())


def test_expired_mandate_prevents_workflow_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = checkpoint() | {"pending_phase": "RUN_TASK"}
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    store.save(initial)
    mandate = _mandate()

    def authority() -> tuple[dict[str, object], str, delivery.MandateContext]:
        return (
            mandate,
            delivery.mandate_digest(mandate),
            delivery.MandateContext(1001, "OWNER", 900, 0, 0),
        )

    reviewed = ReviewedWorkflow(
        root=store.repository_root,
        directory=store.directory,
        value=initial,
        approved_digest="synthetic",
        adapter=NeverAdapter(),
    )
    monkeypatch.setattr(
        reviewed,
        "run",
        lambda: pytest.fail("expired mandate must stop before model invocation"),
    )
    monkeypatch.setattr(bootstrap, "_repository_identity", lambda _: "OWNER/repository")
    engine = runtime.TaskPhaseRuntime(
        store=store,
        authority=authority,
        artifacts=reviewed.artifacts,
        demo_plan=lambda: {},
        approved_order=(),
    )
    with pytest.raises(loop.PhaseBlocked, match="MANDATE_EXPIRED"):
        bootstrap.BootstrapTask(engine, reviewed, authority)()
