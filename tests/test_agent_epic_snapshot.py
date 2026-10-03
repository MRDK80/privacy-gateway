"""Local synthetic Git repositories for exact reviewed artifact publication."""

import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_epic_snapshot as publication
from tools import agent_gate as gate

from tests.test_agent_epic_delivery import _mandate


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def setup(root: Path) -> tuple[delivery.Request, dict[str, object], dict[str, object]]:
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "synthetic")
    git(root, "config", "user.email", "synthetic@example.invalid")
    (root / "task.txt").write_text("before\n", encoding="utf-8")
    git(root, "add", "task.txt")
    git(root, "commit", "-qm", "baseline")
    base = git(root, "rev-parse", "HEAD")
    git(root, "branch", "roadmap/248-test")
    git(root, "checkout", "-qb", "codex/274-test")
    (root / "task.txt").write_text("after\n", encoding="utf-8")
    with gate.trusted_snapshot_session(
        root=root, base_sha=base, allowed_paths=("task.txt",)
    ) as session:
        evidence = gate.build_evidence(
            session,
            [
                gate.CheckResult(
                    id=name,
                    argv=gate.CHECK_COMMANDS[name],
                    status="passed",
                    exit_code=0,
                    duration_seconds=0,
                    summary="synthetic",
                    metrics={},
                )
                for name in gate.PROFILES[gate.FULL_PROFILE]
            ],
        )
        sha = session.snapshot_commit
    request = delivery.Request(
        operation_id="synthetic-commit",
        operation="commit_task",
        repository="OWNER/repository",
        epic=248,
        task=274,
        pr=None,
        base_ref="roadmap/248-test",
        base_sha=base,
        head_ref="codex/274-test",
        head_sha=base,
        schema_version="2.0",
    )
    verdict: dict[str, object] = {
        "schema_version": "1.0",
        "task": request.task,
        "base_sha": request.base_sha,
        "head_ref": request.head_ref,
        "verdict": "PASS",
        "reviewed_state": {"snapshot_commit": sha},
    }
    return request, evidence, verdict


def test_publishes_exact_gate_commit_and_leaves_roadmap_untouched(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    request, evidence, verdict = setup(root)
    publisher = publication.SnapshotCommit(root, ("task.txt",), evidence, verdict)
    assert publisher.revalidate(request)
    receipt = publisher.effect(request)
    snapshot = evidence["snapshot"]
    assert isinstance(snapshot, dict)
    assert git(root, "rev-parse", "HEAD") == snapshot["snapshot_commit"]
    assert receipt["head_sha"] == snapshot["snapshot_commit"]
    assert git(root, "rev-parse", request.base_ref) == request.base_sha
    assert git(root, "status", "--porcelain") == ""
    assert publisher.reconcile(request) == ("APPLIED", receipt)


@pytest.mark.parametrize("changed", ["tree", "controller", "gate"])
def test_stale_or_incomplete_evidence_never_moves_ref(
    tmp_path: Path, changed: str
) -> None:
    root = tmp_path / "repo"
    request, evidence, verdict = setup(root)
    if changed == "tree":
        (root / "task.txt").write_text("different\n", encoding="utf-8")
    elif changed == "controller":
        verdict["reviewed_state"] = {"snapshot_commit": "a" * 40}
    else:
        evidence["status"] = "failed"
    publisher = publication.SnapshotCommit(root, ("task.txt",), evidence, verdict)
    assert not publisher.revalidate(request)
    with pytest.raises(delivery.OutcomeUnknown):
        publisher.effect(request)
    assert git(root, "rev-parse", "HEAD") == request.head_sha


def test_partial_index_update_is_unknown_not_safe_to_repeat(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    request, evidence, verdict = setup(root)
    publisher = publication.SnapshotCommit(root, ("task.txt",), evidence, verdict)
    git(root, "add", "task.txt")
    assert publisher.reconcile(request) == ("UNKNOWN", None)


def test_unrelated_staged_change_blocks_publication(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    request, evidence, verdict = setup(root)
    (root / "other.txt").write_text("synthetic\n", encoding="utf-8")
    git(root, "add", "other.txt")
    assert not publication.SnapshotCommit(
        root, ("task.txt",), evidence, verdict
    ).revalidate(request)


def test_broader_grant_cannot_publish_unreviewed_changed_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    request, evidence, verdict = setup(root)
    publisher = publication.SnapshotCommit(
        root, ("task.txt", "unused.txt"), evidence, verdict
    )
    assert publisher.revalidate(request)
    (root / "unused.txt").write_text("unreviewed synthetic change\n", encoding="utf-8")
    assert not publisher.revalidate(request)
    with pytest.raises(delivery.OutcomeUnknown):
        publisher.effect(request)
    assert git(root, "rev-parse", "HEAD") == request.base_sha


def test_authority_is_checked_again_immediately_before_ref_index_writes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    request, evidence, verdict = setup(root)
    publisher = publication.SnapshotCommit(
        root, ("task.txt",), evidence, verdict, authorize=lambda _: False
    )
    with pytest.raises(delivery.OutcomeUnknown):
        publisher.effect(request)
    assert git(root, "rev-parse", "HEAD") == request.head_sha
    assert git(root, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize("failure", ["revoke", "expire"])
def test_authority_changed_during_snapshot_preparation_prevents_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from collections.abc import Iterator
    from typing import Any

    root = tmp_path / "repo"
    request, evidence, verdict = setup(root)
    mandate = _mandate()
    mandate.update(
        policy_sha=request.base_sha,
        roadmap_ref=request.base_ref,
        task_grants={
            "274": {"causal_scope": ["synthetic"], "allowed_paths": ["task.txt"]}
        },
    )
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approved = delivery.mandate_digest(mandate)
    approval["mandate_digest"] = approved
    now = 200

    def authorize(value: delivery.Request) -> bool:
        return (
            delivery._authorized(
                value,
                mandate,
                approved,
                delivery.MandateContext(now, "OWNER", 150, 0, 0),
            )
            is None
        )

    publisher = publication.SnapshotCommit(
        root, ("task.txt",), evidence, verdict, authorize=authorize
    )
    original = gate.trusted_snapshot_session
    calls = 0

    @contextmanager
    def prepared(*args: Any, **kwargs: Any) -> Iterator[gate.SnapshotSession]:
        nonlocal calls, now
        with original(*args, **kwargs) as session:
            calls += 1
            if calls == 2:
                if failure == "revoke":
                    mandate["revoked"] = True
                else:
                    now = 1001
            yield session

    monkeypatch.setattr(gate, "trusted_snapshot_session", prepared)
    mutations: list[str] = []
    original_git = publisher._git

    def observe(*args: str) -> str:
        if args[0] in {"fetch", "read-tree", "update-ref"}:
            mutations.append(args[0])
        return original_git(*args)

    monkeypatch.setattr(publisher, "_git", observe)
    with pytest.raises(delivery.OutcomeUnknown):
        publisher.effect(request)
    assert mutations == []
    assert git(root, "rev-parse", "HEAD") == request.base_sha
