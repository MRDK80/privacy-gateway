"""Real isolated Git snapshot; synthetic canonical remote state and authority."""

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from tools import agent_coordinator_branch as branch
from tools import agent_epic_bootstrap as bootstrap
from tools import agent_epic_delivery as delivery
from tools import agent_epic_loop as loop
from tools import agent_epic_recovery as recovery
from tools import agent_epic_runtime as runtime
from tools.agent_epic_snapshot import SnapshotCommit
from tools.agent_epic_workflow import ReviewedWorkflow

from tests.test_agent_epic_bootstrap import checkpoint
from tests.test_agent_epic_delivery import _mandate
from tests.test_agent_epic_runtime import Issues
from tests.test_agent_epic_snapshot import git, setup
from tests.test_agent_epic_workflow import NeverAdapter


def build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, Any, Any, Any, Any]:
    root = tmp_path / "repo"
    request, gate, review = setup(root)
    git(root, "remote", "add", "origin", "https://github.com/OWNER/repository.git")
    saved = checkpoint(version="3.0") | {
        "policy_sha": request.base_sha,
        "base_ref": request.base_ref,
        "base_sha": request.base_sha,
        "head_ref": request.head_ref,
        "head_sha": request.head_sha,
        "pending_phase": "RUN_TASK",
        "status": "ESCALATE",
    }
    review.update(schema_version="2.0", policy_sha=request.base_sha)
    artifact = {"gate": gate, "review_receipt": review, "allowed_paths": ["task.txt"]}
    store = loop.CheckpointStore(tmp_path / "state", root)
    store.save(saved)
    mandate = _mandate()
    mandate.update(
        schema_version="3.0",
        policy_sha=request.base_sha,
        roadmap_ref=request.base_ref,
        task_grants={
            "274": {"causal_scope": ["synthetic"], "allowed_paths": ["task.txt"]}
        },
    )
    mandate["approval"]["mandate_digest"] = delivery.mandate_digest(mandate)  # type: ignore[index]

    def authority() -> tuple[Any, str, delivery.MandateContext]:
        return (
            mandate,
            delivery.mandate_digest(mandate),
            delivery.MandateContext(200, "OWNER", 150, 0, 0),
        )

    reviewed = ReviewedWorkflow(
        root=root,
        directory=store.directory,
        value=saved
        | {
            "schema_version": "2.0",
            "causal_scope": ["synthetic"],
            "allowed_paths": ["task.txt"],
            "trusted_policy": {
                "source": "pinned_policy_sha",
                "policy_sha": request.base_sha,
            },
        },
        approved_digest="synthetic",
        adapter=NeverAdapter(),
    )
    reviewed.artifact_store.save(reviewed.key, artifact)
    monkeypatch.setattr(
        reviewed, "run", lambda: pytest.fail("recovery never reruns executor")
    )
    engine = runtime.TaskPhaseRuntime(
        store=store,
        authority=authority,
        artifacts=reviewed.artifacts,
        demo_plan=lambda: {},
        approved_order=(),
    )
    engine.issues = Issues()
    monkeypatch.setattr(engine.github, "ref_sha", lambda *_: request.base_sha)
    monkeypatch.setattr(
        branch.GitHubCLI, "branches", lambda *_: {request.base_ref: request.base_sha}
    )
    monkeypatch.setattr(bootstrap, "_repository_identity", lambda _: request.repository)
    assert isinstance(gate["snapshot"], dict)
    head = gate["snapshot"]["snapshot_commit"]
    remote: dict[str, dict[str, object]] = {}
    calls: list[str] = []

    def probe(value: delivery.Request) -> tuple[str, dict[str, object] | None]:
        receipt = remote.get(value.operation)
        return ("APPLIED", receipt) if receipt is not None else ("NOT_APPLIED", None)

    def effect(value: delivery.Request) -> dict[str, object]:
        calls.append(value.operation)
        receipt: dict[str, object] = {"head_sha": value.head_sha} | (
            {"pr": 278} if value.operation == "create_task_pr" else {}
        )
        remote[value.operation] = receipt
        return receipt

    monkeypatch.setattr(engine.transport, "reconcile", probe)
    monkeypatch.setattr(engine.transport, "effect", effect)
    approval_path = store.directory / "bootstrap-recovery-approval.json"

    def approve(generation: int) -> None:
        identities = []
        for operation in ("commit_task", "push_task", "create_task_pr"):
            fields = {key: saved[key] for key in loop.IDENTITY_KEYS} | {
                "head_sha": request.head_sha if operation == "commit_task" else head,
                "schema_version": "3.0",
                "policy_sha": request.base_sha,
                "operation": operation,
            }
            identities.append(
                hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
            )
        approval = {
            "schema_version": "1.0",
            "checkpoint_digest": recovery.checkpoint_digest(saved),
            "artifact_digest": recovery.artifact_digest(artifact),
            "mandate_digest": delivery.mandate_digest(mandate),
            "repository": request.repository,
            "epic": request.epic,
            "task": request.task,
            "policy_sha": request.base_sha,
            "owner_identity": "OWNER",
            "issued_at": 100,
            "expires_at": 300,
            "generation": generation,
            "budgets": dict.fromkeys(identities, 1),
        }
        approval_path.write_text(json.dumps(approval), encoding="utf-8")
        approval_path.chmod(0o600)

    approve(0)
    engine.recovery = recovery.BootstrapRecovery(
        store.directory,
        root,
        authority,
        lambda: recovery.digest(recovery.private_value(approval_path)),
    )
    engine.bootstrap = bootstrap.BootstrapTask(engine, reviewed, authority)
    return engine, calls, remote, approve, mandate


@pytest.mark.parametrize("interrupted", ["push_task", "create_task_pr"])
@pytest.mark.parametrize("applied", [False, True])
def test_partial_bootstrap_retains_receipts_and_never_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupted: str,
    applied: bool,
) -> None:
    engine, calls, remote, approve, _ = build(tmp_path, monkeypatch)
    original = engine.transport.effect

    def fail(value: delivery.Request) -> dict[str, object]:
        if value.operation == interrupted:
            if applied:
                original(value)
            raise delivery.OutcomeUnknown
        return cast(dict[str, object], original(value))

    monkeypatch.setattr(engine.transport, "effect", fail)
    with pytest.raises(loop.LoopError, match="ESCALATE_UNKNOWN_OUTCOME"):
        engine.effect("RUN_TASK")
    entries_before = delivery.Ledger(
        engine.store.directory, engine.store.repository_root
    ).load()
    commit_before = next(
        item for item in entries_before.values() if item["operation"] == "commit_task"
    )
    assert commit_before["status"] == "APPLIED"
    assert git(engine.store.repository_root, "status", "--porcelain") == ""
    stopped = engine.recovery.control.load()
    assert stopped["stopped"] is True and stopped["generation"] == 1
    count = list(calls)
    # Even fresh canonical proof of applied/absent operations is not permission.
    engine.reconcile("RUN_TASK")
    with pytest.raises(recovery.RecoveryError, match="RECOVERY_APPROVAL_INVALID"):
        engine.effect("RUN_TASK")
    assert calls == count
    monkeypatch.setattr(engine.transport, "effect", original)
    approve(1)
    result = engine.effect("RUN_TASK")
    assert result["delivery_identity"]["pr"] == 278
    assert calls == ["push_task", "create_task_pr"]
    entries_after = delivery.Ledger(
        engine.store.directory, engine.store.repository_root
    ).load()
    assert (
        next(
            item
            for item in entries_after.values()
            if item["operation"] == "commit_task"
        )
        == commit_before
    )
    assert all(item["status"] == "APPLIED" for item in entries_after.values())
    assert engine.reconcile("RUN_TASK")[0] == "APPLIED"
    assert set(remote) == {"push_task", "create_task_pr"}


def test_stale_snapshot_never_pushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, calls, _, _, _ = build(tmp_path, monkeypatch)
    (engine.store.repository_root / "task.txt").write_text(
        "synthetic altered\n", encoding="utf-8"
    )
    with pytest.raises(loop.LoopError):
        engine.effect("RUN_TASK")
    assert calls == []


def test_revocation_during_canonical_probe_prevents_next_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, calls, _, _, mandate = build(tmp_path, monkeypatch)
    original = engine.transport.reconcile

    def revoke(value: delivery.Request) -> Any:
        result = original(value)
        mandate["revoked"] = True
        return result

    monkeypatch.setattr(engine.transport, "reconcile", revoke)
    with pytest.raises(loop.LoopError):
        engine.effect("RUN_TASK")
    assert calls == []


def test_malformed_not_applied_receipt_never_authorizes_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, calls, _, _, _ = build(tmp_path, monkeypatch)
    monkeypatch.setattr(
        engine.transport,
        "reconcile",
        lambda _: ("NOT_APPLIED", {"synthetic": "invalid receipt"}),
    )
    with pytest.raises(loop.LoopError):
        engine.effect("RUN_TASK")
    assert calls == []


def test_crash_after_commit_reconciles_without_repeating_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, calls, _, approve, _ = build(tmp_path, monkeypatch)
    original = SnapshotCommit.effect
    commits: list[str] = []

    def interrupted(self: SnapshotCommit, value: delivery.Request) -> Any:
        original(self, value)
        commits.append(value.operation_id)
        raise delivery.OutcomeUnknown

    monkeypatch.setattr(SnapshotCommit, "effect", interrupted)
    with pytest.raises(loop.LoopError, match="ESCALATE_UNKNOWN_OUTCOME"):
        engine.effect("RUN_TASK")
    assert len(commits) == 1 and calls == []
    assert engine.recovery.control.load()["generation"] == 1
    monkeypatch.setattr(SnapshotCommit, "effect", original)
    approve(1)
    engine.effect("RUN_TASK")
    assert len(commits) == 1
    assert calls == ["push_task", "create_task_pr"]


def test_partial_index_never_proves_absent_or_repeats_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, calls, _, approve, _ = build(tmp_path, monkeypatch)
    original = SnapshotCommit._git
    mutations: list[str] = []

    def interrupted(self: SnapshotCommit, *arguments: str) -> str:
        result = original(self, *arguments)
        if arguments[0] in {"fetch", "read-tree", "update-ref"}:
            mutations.append(arguments[0])
        if arguments[0] == "read-tree":
            raise delivery.OutcomeUnknown
        return result

    monkeypatch.setattr(SnapshotCommit, "_git", interrupted)
    with pytest.raises(loop.LoopError, match="ESCALATE_UNKNOWN_OUTCOME"):
        engine.effect("RUN_TASK")
    before = list(mutations)
    assert before == ["fetch", "read-tree"]
    assert engine.reconcile("RUN_TASK") == ("UNKNOWN", None)
    approve(1)
    with pytest.raises(loop.LoopError, match="ESCALATE_UNKNOWN_OUTCOME"):
        engine.effect("RUN_TASK")
    assert mutations == before and calls == []
    assert engine.recovery.control.load()["generation"] == 2


def test_artifact_save_failure_preserves_all_receipts_and_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, calls, _, approve, _ = build(tmp_path, monkeypatch)
    storage = engine.bootstrap.reviewed.artifact_store
    original = storage.save

    def interrupted(*args: Any) -> None:
        raise OSError("synthetic artifact save interruption")

    monkeypatch.setattr(storage, "save", interrupted)
    with pytest.raises(OSError):
        engine.effect("RUN_TASK")
    assert calls == ["push_task", "create_task_pr"]
    entries = delivery.Ledger(
        engine.store.directory, engine.store.repository_root
    ).load()
    assert all(value["status"] == "APPLIED" for value in entries.values())
    assert engine.reconcile("RUN_TASK")[0] == "APPLIED"
    assert engine.recovery.control.load()["stopped"] is True
    monkeypatch.setattr(storage, "save", original)
    approve(1)
    engine.effect("RUN_TASK")
    assert calls == ["push_task", "create_task_pr"]


def test_recovery_supervisor_binds_pr_then_returns_for_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    engine, calls, _, _, _ = build(tmp_path, monkeypatch)
    monkeypatch.setattr(time, "time", lambda: 200)
    approval = recovery.private_value(
        engine.store.directory / "bootstrap-recovery-approval.json"
    )
    config_path = engine.store.directory / "runtime-task.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "approved_recovery_digest": recovery.digest(approval),
            }
        ),
        encoding="utf-8",
    )
    config_path.chmod(0o600)

    class Adapter:
        def effect(self, phase: str) -> dict[str, object]:
            return dict(engine.effect(phase))

        def live(self) -> dict[str, object]:
            return dict(engine.bootstrap.reviewed.artifacts()["delivery_identity"])

        def reconcile(self, phase: str) -> Any:
            return engine.reconcile(phase)

        def merge_sha(self) -> None:
            return None

    result = loop.resume(store=engine.store, adapter=Adapter())
    assert result.status == "NO_OP" and result.phase == "RUN_TASK"
    saved = engine.store.load()
    assert saved is not None and saved["pr"] == 278 and saved["pending_phase"] is None
    assert calls == ["push_task", "create_task_pr"]
    second = loop.resume(store=engine.store, adapter=Adapter())
    assert second.machine_code == "RECOVERY_COMPLETE_REVIEW_REQUIRED"
    assert calls == ["push_task", "create_task_pr"]
