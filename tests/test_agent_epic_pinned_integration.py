"""Offline concrete Git ancestry and versioned policy consumer regressions."""

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tools import agent_coordinator_branch as branch
from tools import agent_coordinator_handover as coordinator
from tools import agent_epic_delivery as delivery
from tools import agent_epic_policy_binding as binding
from tools import agent_epic_snapshot as publication
from tools import agent_orchestrate as workflow

from tests.test_agent_coordinator_handover import FakeGitHub, _repository, _value
from tests.test_agent_epic_delivery import _mandate
from tests.test_agent_epic_snapshot import git, setup


def test_import_exact_merge_missing_locally_without_moving_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote"
    request, _, _ = setup(remote)
    git(remote, "add", "task.txt")
    git(remote, "commit", "-qm", "base")
    base = git(remote, "rev-parse", "HEAD")
    root = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(remote), str(root)], check=True, capture_output=True
    )
    git(root, "remote", "set-url", "origin", "https://github.com/OWNER/repository.git")
    (remote / "task.txt").write_text("remote merge\n", encoding="utf-8")
    git(remote, "add", "task.txt")
    git(remote, "commit", "-qm", "remote-only merge")
    merge = git(remote, "rev-parse", "HEAD")
    original = coordinator._git

    def scoped(directory: Path, *args: str) -> str:
        if args[0] == "fetch":
            assert args == (
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "https://github.com/OWNER/repository.git",
                merge,
            )
            args = (*args[:3], str(remote), args[4])
        return original(directory, *args)

    monkeypatch.setattr(coordinator, "_git", scoped)
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", merge], cwd=root, capture_output=True
        ).returncode
        != 0
    )
    binding.import_verified_merge(root, request.repository, base, merge, lambda: merge)
    assert git(root, "rev-parse", "HEAD") == base
    assert not (root / ".git" / "FETCH_HEAD").exists()
    assert git(root, "merge-base", base, merge) == base
    with pytest.raises(binding.BindingError, match="BINDING_BASE_MISMATCH"):
        binding.import_verified_merge(
            root, request.repository, base, merge, lambda: base
        )


def test_coordinator_materializes_policy_from_pinned_not_delivery_revision(
    tmp_path: Path,
) -> None:
    root, policy = _repository(tmp_path)
    (root / "AGENTS.md").write_text("untrusted newer policy\n", encoding="utf-8")
    git(root, "add", "AGENTS.md")
    git(root, "commit", "-qm", "new delivery baseline")
    base = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/heads/roadmap/232-agent-coordinator", base)
    value = _value(base)
    value["schema_version"] = "2.0"
    value["trusted_policy"] = {"source": "pinned_policy_sha", "policy_sha": policy}
    value["mandate_provenance"] = {
        "schema_version": "3.0",
        "digest": "sha256:" + "a" * 64,
        "policy_sha": policy,
    }
    approved = coordinator.handover_digest(value)
    value["approval"]["plan_digest"] = approved
    contract = coordinator.validate_handover(
        value, root=root, approved_digest=approved, github=FakeGitHub()
    ).contract
    assert contract.base_sha == base and contract.policy_sha == policy
    assert workflow._load_trusted_policy(root, contract)["AGENTS.md"] == "policy"
    value.pop("mandate_provenance")
    approved = coordinator.handover_digest(value)
    value["approval"]["plan_digest"] = approved
    with pytest.raises(coordinator.HandoverError, match="POLICY_PROVENANCE_MISMATCH"):
        coordinator.validate_handover(
            value, root=root, approved_digest=approved, github=FakeGitHub()
        )


def test_legacy_coordinator_accepts_actual_v2_planner_provenance(
    tmp_path: Path,
) -> None:
    root, sha = _repository(tmp_path)
    value = _value(sha)
    value["mandate_provenance"] = {
        "schema_version": "2.0",
        "digest": "sha256:" + "a" * 64,
        "policy_sha": sha,
    }
    approved = coordinator.handover_digest(value)
    value["approval"]["plan_digest"] = approved
    result = coordinator.validate_handover(
        value, root=root, approved_digest=approved, github=FakeGitHub()
    )
    assert result.contract.policy_sha is None


def test_snapshot_requires_policy_bound_review_receipt(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    old, evidence, receipt = setup(root)
    request = replace(old, schema_version="3.0", policy_sha=old.base_sha)
    publisher = publication.SnapshotCommit(root, ("task.txt",), evidence, receipt)
    assert not publisher.revalidate(request)
    receipt.update(schema_version="2.0", policy_sha=old.base_sha)
    assert publisher.revalidate(request)
    receipt["policy_sha"] = "c" * 40
    assert not publisher.revalidate(request)


@pytest.mark.parametrize("failure", [None, "stale", "missing-policy", "wrong-repo"])
def test_delivery_v3_requires_concrete_fresh_binding_before_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    root = tmp_path / "repo"
    old, _, _ = setup(root)
    git(root, "remote", "add", "origin", "https://github.com/OWNER/repository.git")
    mandate = _mandate()
    mandate.update(
        schema_version="3.0", policy_sha=old.base_sha, roadmap_ref=old.base_ref
    )
    if failure == "missing-policy":
        mandate["policy_sha"] = "c" * 40
    if failure == "wrong-repo":
        git(root, "remote", "set-url", "origin", "https://github.com/other/project.git")
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    digest = delivery.mandate_digest(mandate)
    approval["mandate_digest"] = digest
    request = replace(
        old, task=252, schema_version="3.0", policy_sha=str(mandate["policy_sha"])
    )
    monkeypatch.setattr(
        branch.GitHubCLI,
        "branches",
        lambda _self, _repo: {
            old.base_ref: "b" * 40 if failure == "stale" else old.base_sha
        },
    )
    effects: list[str] = []

    def effect(value: delivery.Request) -> dict[str, object]:
        effects.append(value.operation)
        return {"synthetic": True}

    result = delivery.deliver(
        request,
        mandate=mandate,
        approved_mandate_digest=digest,
        assessment={},
        ledger=delivery.Ledger(tmp_path / "private", root),
        revalidate=lambda _: True,
        effect=effect,
        mandate_context=delivery.MandateContext(200, "OWNER", 150, 0, 0),
    )
    assert result.status == ("APPLIED" if failure is None else "BLOCKED")
    assert effects == (["commit_task"] if failure is None else [])


def test_post_merge_binding_checks_merge_descends_from_actual_base(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    request, _, _ = setup(root)
    git(root, "remote", "add", "origin", "https://github.com/OWNER/repository.git")
    git(root, "add", "task.txt")
    git(root, "commit", "-qm", "merge surrogate")
    merge = git(root, "rev-parse", "HEAD")
    value: dict[str, Any] = {
        "schema_version": "1.0",
        "repository": request.repository,
        "epic": request.epic,
        "roadmap_ref": request.base_ref,
        "base_sha": request.base_sha,
        "policy_sha": request.base_sha,
    }
    binding.validate_live_binding(
        value,
        repository_root=root,
        repository=request.repository,
        epic=request.epic,
        roadmap_ref=request.base_ref,
        policy_sha=request.base_sha,
        branches=lambda _: {request.base_ref: merge},
        merge_sha=merge,
    )
    with pytest.raises(binding.BindingError, match="BINDING_BASE_MISMATCH"):
        binding.validate_live_binding(
            value,
            repository_root=root,
            repository=request.repository,
            epic=request.epic,
            roadmap_ref=request.base_ref,
            policy_sha=request.base_sha,
            branches=lambda _: {request.base_ref: request.base_sha},
            merge_sha=merge,
        )
