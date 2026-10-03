"""Deployment guard and closed command envelopes, all offline."""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_epic_loop as loop
from tools import agent_epic_runtime_cli as cli

from tests.test_agent_epic_delivery import _mandate
from tests.test_agent_epic_snapshot import git, setup


def policy_fixture(tmp_path: Path) -> tuple[Path, Path, str, Path]:
    root = tmp_path / "repo"
    setup(root)
    private = tmp_path / "policy"
    private.mkdir(mode=0o700)
    values = {
        "tools/__init__.py": "",
        "tools/agent_epic_runtime_cli.py": "synthetic entry\n",
        "tools/agent_epic_runtime.py": "pass\n",
        "tools/codex_adapter.py": "pass\n",
        "docs/schemas/synthetic.json": "{}\n",
    }
    for relative, contents in values.items():
        original = root / relative
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_text(contents, encoding="utf-8")
        copy = private / relative
        copy.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        for parent in copy.parents:
            if parent == private:
                break
            parent.chmod(0o700)
        copy.write_text(contents, encoding="utf-8")
        copy.chmod(0o600)
    git(root, "add", "tools", "docs")
    git(root, "commit", "-qm", "synthetic policy")
    entry = tmp_path / "entry.py"
    entry.write_text(values["tools/agent_epic_runtime_cli.py"], encoding="utf-8")
    entry.chmod(0o600)
    return root, private, git(root, "rev-parse", "HEAD"), entry


def test_windows_private_runtime_is_explicitly_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(cli, "os", SimpleNamespace(name="nt"))
    with pytest.raises(cli.DeploymentError, match="RUNTIME_ADAPTER_UNSUPPORTED"):
        cli.owner_uid()


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-only deployment")
@pytest.mark.parametrize("epic", [False, True])
@pytest.mark.parametrize("failure", ["unapproved", "expired", "revoked"])
def test_candidate_policy_cannot_execute_before_authority_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, epic: bool, failure: str
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    mandate = _mandate()
    if epic:
        mandate["schema_version"] = "3.0"
    if failure == "expired":
        mandate["expires_at"] = 190
    if failure == "revoked":
        mandate["revoked"] = True
    approved = delivery.mandate_digest(mandate)
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approval["mandate_digest"] = approved
    keys = cli.EPIC_CONFIG_KEYS if epic else cli.CONFIG_KEYS
    config: dict[str, Any] = {key: "synthetic" for key in keys}
    config.update(
        schema_version="1.0",
        owner_identity="OWNER",
        started_at=150,
        task_iterations=0,
        follow_up_issues=0,
        approved_order=[274],
        approved_mandate_digest="sha256:" + "0" * 64
        if failure == "unapproved"
        else approved,
    )
    for name, value in (
        ("mandate.json", mandate),
        ("epic-runtime.json" if epic else "runtime-task.json", config),
    ):
        path = state / name
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
    monkeypatch.setattr(time, "time", lambda: 200)

    def candidate(*args: object, **kwargs: object) -> None:
        pytest.fail("candidate-policy loading must not be reached")

    monkeypatch.setattr(cli, "verify_policy", candidate)
    code = {
        "unapproved": "MANDATE_APPROVAL_MISMATCH",
        "expired": "MANDATE_EXPIRED",
        "revoked": "MANDATE_REVOKED",
    }[failure]
    with pytest.raises(cli.DeploymentError, match=code):
        (cli.build_session if epic else cli.build_runtime)(state, tmp_path / "repo")


@pytest.mark.parametrize("epic", [False, True])
@pytest.mark.parametrize(
    "now,iterations,followups,owner,started",
    [
        (200, 0, 0, "OWNER", 150),
        (99, 0, 0, "OWNER", 150),
        (1000, 0, 0, "OWNER", 150),
        (200, 1000, 0, "OWNER", 150),
        (200, 0, 1000, "OWNER", 150),
        (200, 0, 0, "other", 150),
        (200, 0, 0, "OWNER", 99),
    ],
)
def test_bootstrap_lifecycle_matches_canonical_guard(
    monkeypatch: pytest.MonkeyPatch,
    epic: bool,
    now: int,
    iterations: int,
    followups: int,
    owner: str,
    started: int,
) -> None:
    mandate = _mandate()
    mandate["schema_version"] = "3.0"
    approved = delivery.mandate_digest(mandate)
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approval["mandate_digest"] = approved
    config = {
        "approved_mandate_digest": approved,
        "owner_identity": owner,
        "started_at": started,
        "task_iterations": iterations,
        "follow_up_issues": followups,
    }
    monkeypatch.setattr(time, "time", lambda: now)
    code = delivery.mandate_lifecycle_code(
        mandate,
        delivery.MandateContext(now, owner, started, iterations, followups),
        at_task_start=not epic,
    )
    if code is None:
        cli.bootstrap_authority(config, mandate, epic=epic)
    else:
        with pytest.raises(cli.DeploymentError, match=code):
            cli.bootstrap_authority(config, mandate, epic=epic)


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-only deployment")
def test_frozen_policy_must_be_byte_identical_to_pinned_git_sha(tmp_path: Path) -> None:
    root, policy, sha, entry = policy_fixture(tmp_path)
    cli.verify_policy(policy, root, sha, entry=entry)
    (policy / "tools" / "codex_adapter.py").write_text("different\n", encoding="utf-8")
    with pytest.raises(cli.DeploymentError, match="UNTRUSTED_RUNTIME_COMMAND"):
        cli.verify_policy(policy, root, sha, entry=entry)


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-only deployment")
def test_unknown_policy_module_cannot_be_imported(tmp_path: Path) -> None:
    root, policy, sha, entry = policy_fixture(tmp_path)
    extra = policy / "tools" / "extra.py"
    extra.write_text("pass\n", encoding="utf-8")
    extra.chmod(0o600)
    with pytest.raises(cli.DeploymentError, match="UNTRUSTED_RUNTIME_COMMAND"):
        cli.verify_policy(policy, root, sha, entry=entry)


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-only deployment")
def test_entrypoint_cannot_differ_from_policy_revision(tmp_path: Path) -> None:
    root, policy, sha, entry = policy_fixture(tmp_path)
    entry.write_text("wrong entry\n", encoding="utf-8")
    with pytest.raises(cli.DeploymentError, match="UNTRUSTED_RUNTIME_COMMAND"):
        cli.verify_policy(policy, root, sha, entry=entry)


def test_blocked_phase_has_closed_zero_exit_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Engine:
        def effect(self, phase: str) -> None:
            raise loop.PhaseBlocked("CI_PENDING")

    monkeypatch.setattr(cli, "build_runtime", lambda *args: Engine())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runtime",
            "effect",
            "--phase",
            "PR_CI",
            "--state-dir",
            str(tmp_path / "state"),
            "--repository-root",
            str(tmp_path / "repo"),
        ],
    )
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "BLOCKED",
        "machine_code": "CI_PENDING",
        "receipt": None,
    }


def test_unknown_reconciliation_does_not_emit_private_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unavailable(*args: object) -> None:
        raise OSError("synthetic private path and payload")

    monkeypatch.setattr(cli, "build_runtime", unavailable)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runtime",
            "reconcile",
            "--phase",
            "MERGE",
            "--state-dir",
            str(tmp_path / "state"),
            "--repository-root",
            str(tmp_path / "repo"),
        ],
    )
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out) == {"state": "UNKNOWN", "receipt": None}
