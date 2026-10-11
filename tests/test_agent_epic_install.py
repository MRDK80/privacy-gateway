"""Synthetic installation candidates; no real runtime state or credentials."""

import json
import os
import sys
import time
from pathlib import Path

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_epic_install as install
from tools import agent_epic_loop as loop
from tools.agent_coordinator_handover import handover_digest

from tests.test_agent_epic_delivery import _mandate
from tests.test_agent_epic_loop import _checkpoint
from tests.test_agent_epic_runtime_cli import policy_fixture
from tests.test_agent_epic_snapshot import git

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX private install")


def test_prepare_uses_git_not_mutable_head(tmp_path: Path) -> None:
    root, _, sha, _ = policy_fixture(tmp_path)
    (root / "tools/agent_epic_runtime.py").write_text("untrusted edit")
    candidate = tmp_path / "candidate"
    report = install.prepare(root, sha, "example/project", candidate)
    assert report["status"] == "PREPARED"
    assert (candidate / "policy/tools/agent_epic_runtime.py").read_text() == "pass\n"
    assert stat_mode(candidate) == 0o700


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_install_requires_exact_external_approval_and_is_idempotent(
    tmp_path: Path,
) -> None:
    root, _, sha, _ = policy_fixture(tmp_path)
    candidate, destination = tmp_path / "candidate", tmp_path / "installed"
    report = install.prepare(root, sha, "example/project", candidate)
    with pytest.raises(install.InstallError, match="INSTALL_APPROVAL_MISMATCH"):
        install.activate(root, candidate, destination, "sha256:" + "0" * 64)
    assert not destination.exists()
    digest = report["candidate_digest"]
    assert (
        install.activate(root, candidate, destination, digest)["status"] == "INSTALLED"
    )
    assert install.activate(root, candidate, destination, digest)["status"] == "NO_OP"
    assert not (destination / "mandate.json").exists()
    assert stat_mode(destination / "policy/tools/agent_epic_runtime.py") == 0o600


@pytest.mark.parametrize("damage", ["entry", "manifest", "symlink", "mode"])
def test_candidate_tampering_never_installs(tmp_path: Path, damage: str) -> None:
    root, _, sha, _ = policy_fixture(tmp_path)
    candidate, destination = tmp_path / "candidate", tmp_path / "installed"
    report = install.prepare(root, sha, "example/project", candidate)
    entry = candidate / "runtime.py"
    if damage == "entry":
        entry.write_text("altered")
    elif damage == "manifest":
        manifest = candidate / "candidate.json"
        value = json.loads(manifest.read_text())
        value["extra"] = True
        manifest.write_text(json.dumps(value))
    elif damage == "symlink":
        entry.unlink()
        entry.symlink_to(root / "tools/agent_epic_runtime_cli.py")
    else:
        candidate.chmod(0o755)
    with pytest.raises(install.InstallError):
        install.activate(root, candidate, destination, report["candidate_digest"])
    assert not destination.exists()


def test_conflicting_install_and_pending_state_unchanged(tmp_path: Path) -> None:
    root, _, sha, _ = policy_fixture(tmp_path)
    candidate, destination = tmp_path / "candidate", tmp_path / "installed"
    report = install.prepare(root, sha, "example/project", candidate)
    destination.mkdir(mode=0o700)
    pending = destination / "checkpoint.json"
    pending.write_text("synthetic pending intent\n")
    before = pending.read_bytes()
    with pytest.raises(install.InstallError):
        install.activate(root, candidate, destination, report["candidate_digest"])
    assert pending.read_bytes() == before


def test_failed_publish_cleans_staging_and_does_not_create_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, sha, _ = policy_fixture(tmp_path)
    candidate, destination = tmp_path / "candidate", tmp_path / "installed"
    report = install.prepare(root, sha, "example/project", candidate)

    def interrupted(*args: object) -> None:
        raise OSError("synthetic diagnostic must stay private")

    monkeypatch.setattr(os, "rename", interrupted)
    with pytest.raises(install.InstallError) as error:
        install.activate(root, candidate, destination, report["candidate_digest"])
    assert str(error.value) == "INSTALL_FAILED"
    assert not destination.exists()
    assert not list(tmp_path.glob(".pgw-install-*"))


def test_wrong_sha_and_worktree_destination_refused(tmp_path: Path) -> None:
    root, _, sha, _ = policy_fixture(tmp_path)
    with pytest.raises(install.InstallError):
        install.prepare(root, "0" * 40, "example/project", tmp_path / "candidate")
    with pytest.raises(install.InstallError):
        install.prepare(root, sha, "example/project", root / "candidate")


def test_wrong_owner_and_symlink_parent_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, sha, _ = policy_fixture(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(install.InstallError, match="UNSAFE_INSTALL_DIRECTORY"):
        install.prepare(root, sha, "example/project", alias / "candidate")
    from tools import agent_epic_runtime_cli as guards

    monkeypatch.setattr(guards, "owner_uid", lambda: os.getuid() + 1)
    with pytest.raises(install.InstallError, match="UNSAFE_INSTALL_DIRECTORY"):
        install.prepare(root, sha, "example/project", tmp_path / "candidate")


def test_git_symlink_and_unsafe_parent_refused(tmp_path: Path) -> None:
    root, _, _, _ = policy_fixture(tmp_path)
    (root / "tools/link.py").symlink_to("agent_epic_runtime.py")
    git(root, "add", "tools/link.py")
    git(root, "commit", "-qm", "synthetic unsafe inventory")
    sha = git(root, "rev-parse", "HEAD")
    with pytest.raises(install.InstallError, match="POLICY_INVENTORY_INVALID"):
        install.prepare(root, sha, "example/project", tmp_path / "candidate")
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o770)
    parent.chmod(0o770)
    with pytest.raises(install.InstallError, match="UNSAFE_INSTALL_DIRECTORY"):
        install.prepare(root, sha, "example/project", parent / "candidate")


def test_altered_policy_and_valid_conflicting_install(tmp_path: Path) -> None:
    root, _, sha, _ = policy_fixture(tmp_path)
    candidate, destination = tmp_path / "candidate", tmp_path / "installed"
    report = install.prepare(root, sha, "example/project", candidate)
    install.activate(root, candidate, destination, report["candidate_digest"])
    (candidate / "policy/tools/agent_epic_runtime.py").write_text("altered")
    with pytest.raises(install.InstallError):
        install.activate(
            root, candidate, tmp_path / "second", report["candidate_digest"]
        )
    other = tmp_path / "other"
    report2 = install.prepare(root, sha, "different/project", other)
    before = (destination / "candidate.json").read_bytes()
    with pytest.raises(install.InstallError, match="INSTALL_DESTINATION_CONFLICT"):
        install.activate(root, other, destination, report2["candidate_digest"])
    assert (destination / "candidate.json").read_bytes() == before


def runtime_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    epic: bool = False,
) -> tuple[Path, Path, Path]:
    root, _, sha, _ = policy_fixture(tmp_path)
    candidate = tmp_path / "candidate"
    install.prepare(root, sha, "OWNER/repository", candidate)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    binary = state / "adapter-bin"
    binary.mkdir(mode=0o700)
    script = binary / "runtime.py"
    script.write_bytes((candidate / "runtime.py").read_bytes())
    script.chmod(0o600)
    command = [sys.executable, str(script)]
    adapter: dict[str, object] = {
        "schema_version": "2.0" if epic else "1.0",
        "live_command": command,
        "phase_commands": {p: command for p in loop.PHASES[1:]},
        "reconcile_commands": {p: command for p in loop.PHASES[1:]},
        "timeout_seconds": 30,
        "output_limit": 4096,
    }
    if epic:
        adapter["epic_command"] = command
    mandate = _mandate()
    mandate["policy_sha"] = sha
    mandate["schema_version"] = "3.0" if epic else "2.0"
    approved = delivery.mandate_digest(mandate)
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approval["mandate_digest"] = approved
    checkpoint = _checkpoint()
    if epic:
        checkpoint.update(schema_version="3.0", policy_sha=sha)
    handover = {
        key: checkpoint[key]
        for key in ("repository", "epic", "task", "base_ref", "base_sha", "head_ref")
    }
    if epic:
        handover.update(
            schema_version="2.0",
            trusted_policy={"source": "pinned_policy_sha", "policy_sha": sha},
        )
    plans = {
        "schema_version": "1.0",
        "initial_handover_digest": handover_digest(handover),
        "tasks": {},
    }
    config = {
        "schema_version": "1.0",
        "policy_root": str(candidate / "policy"),
        "approved_mandate_digest": approved,
        "owner_identity": "OWNER",
        "started_at": 150,
        "task_iterations": 0,
        "follow_up_issues": 0,
        "approved_order": [253],
        **(
            {
                "approved_plans_digest": install.digest(plans),
                "approved_final_digest": None,
            }
            if epic
            else {"approved_handover_digest": handover_digest(handover)}
        ),
    }
    for name, value in (
        ("runtime-adapter.json", adapter),
        ("mandate.json", mandate),
        ("epic-runtime.json" if epic else "runtime-task.json", config),
        ("checkpoint.json", checkpoint),
        ("handover.json", handover),
        ("approved-plans.json", plans),
    ):
        path = state / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    monkeypatch.setattr(time, "time", lambda: 200)
    monkeypatch.setattr(
        loop.CommandRuntimeAdapter, "_bubblewrap", lambda: Path("/synthetic/bwrap")
    )
    return root, candidate, state


@pytest.mark.parametrize("epic", [False, True])
def test_readiness_is_read_only_and_has_no_private_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    epic: bool,
) -> None:
    root, candidate, state = runtime_fixture(tmp_path, monkeypatch, epic=epic)
    before = {p: p.read_bytes() for p in state.rglob("*") if p.is_file()}
    observed: list[tuple[str, str]] = []
    report = install.readiness(
        root,
        candidate,
        state,
        epic=epic,
        github_identity="OWNER",
        namespace_probe=lambda: None,
        github_probe=lambda repo, identity: observed.append((repo, identity)),
    )
    assert report["status"] == "READY"
    assert observed == [("OWNER/repository", "OWNER")]
    assert {p: p.read_bytes() for p in state.rglob("*") if p.is_file()} == before
    assert str(tmp_path) not in json.dumps(report)


@pytest.mark.parametrize(
    "fault", ["revoked", "expired", "schema", "profile", "namespace"]
)
def test_readiness_failure_before_any_candidate_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    root, candidate, state = runtime_fixture(tmp_path, monkeypatch)
    path = state / "mandate.json"
    mandate = json.loads(path.read_text())
    if fault == "revoked":
        mandate["revoked"] = True
    elif fault == "expired":
        monkeypatch.setattr(time, "time", lambda: 10000)
    elif fault == "schema":
        config_path = state / "runtime-task.json"
        config = json.loads(config_path.read_text())
        config["unexpected"] = True
        config_path.write_text(json.dumps(config))
    if fault == "revoked":
        approved = delivery.mandate_digest(mandate)
        mandate["approval"]["mandate_digest"] = approved
        path.write_text(json.dumps(mandate))
        config_path = state / "runtime-task.json"
        config = json.loads(config_path.read_text())
        config["approved_mandate_digest"] = approved
        config_path.write_text(json.dumps(config))

    def namespace() -> None:
        if fault == "namespace":
            raise RuntimeError("private diagnostic sentinel")

    report = install.readiness(
        root,
        candidate,
        state,
        epic=fault == "profile",
        github_identity="OWNER",
        namespace_probe=namespace,
        github_probe=lambda *_: None,
    )
    assert report["status"] == "BLOCKED"
    assert "private diagnostic" not in json.dumps(report)


@pytest.mark.parametrize("epic", [False, True])
@pytest.mark.parametrize("fault", ["handover", "order", "recovery", "policy"])
def test_readiness_preserves_runtime_guard_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    epic: bool,
    fault: str,
) -> None:
    root, candidate, state = runtime_fixture(tmp_path, monkeypatch, epic=epic)
    config_path = state / ("epic-runtime.json" if epic else "runtime-task.json")
    config = json.loads(config_path.read_text())
    if fault == "order":
        config["approved_order"] = [True]
    elif fault == "recovery":
        path = state / "bootstrap-recovery.json"
        path.write_text("{}")
        path.chmod(0o600)
    else:
        path = state / "handover.json"
        handover = json.loads(path.read_text())
        if fault == "handover":
            handover["task"] = 999
        elif epic:
            handover["trusted_policy"]["policy_sha"] = "0" * 40
        else:
            handover["base_sha"] = "0" * 40
        path.write_text(json.dumps(handover))
        if epic:
            path = state / "approved-plans.json"
            plans = json.loads(path.read_text())
            plans["initial_handover_digest"] = handover_digest(handover)
            path.write_text(json.dumps(plans))
            config["approved_plans_digest"] = install.digest(plans)
        else:
            config["approved_handover_digest"] = handover_digest(handover)
    config_path.write_text(json.dumps(config))
    report = install.readiness(
        root,
        candidate,
        state,
        epic=epic,
        github_identity="OWNER",
        namespace_probe=lambda: None,
        github_probe=lambda *_: None,
    )
    assert report["status"] == "BLOCKED"
