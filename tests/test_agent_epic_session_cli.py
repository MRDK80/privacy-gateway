"""Versioned whole-epic command keeps owner/namespace/envelope guards."""

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
from tests.test_agent_epic_loop import (
    active_runtime_namespace as active_runtime_namespace,
)


@pytest.mark.usefixtures("active_runtime_namespace")
def test_v2_epic_command_runs_owner_only_script_in_real_namespace(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (tmp_path / "repo").mkdir()
    scripts = state / "adapter-bin"
    scripts.mkdir(mode=0o700)
    script = scripts / "runtime.py"
    script.write_text(
        "import json\nprint(json.dumps({'schema_version':'1.0','status':'BLOCKED',"
        "'machine_code':'FINAL_DEMO_UNCONFIRMED','result':None}))\n",
        encoding="utf-8",
    )
    script.chmod(0o600)
    command = [sys.executable, str(script)]
    value = {
        "schema_version": "2.0",
        "live_command": command,
        "epic_command": command,
        "phase_commands": {phase: command for phase in loop.PHASES[1:]},
        "reconcile_commands": {phase: command for phase in loop.PHASES[1:]},
        "timeout_seconds": 10,
        "output_limit": 4096,
    }
    path = state / "runtime-adapter.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    adapter = loop.CommandRuntimeAdapter.load(path, tmp_path / "repo", state)
    assert adapter.resume_epic()["machine_code"] == "FINAL_DEMO_UNCONFIRMED"
    value["schema_version"] = "1.0"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(loop.LoopError, match="RUNTIME_CONFIG_INVALID"):
        loop.CommandRuntimeAdapter.load(path, tmp_path / "repo", state)


def test_epic_entry_redacts_unknown_errors_in_closed_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unavailable(*args: object) -> None:
        raise OSError("synthetic private input and path")

    monkeypatch.setattr(cli, "build_session", unavailable)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runtime",
            "epic_resume",
            "--state-dir",
            str(tmp_path / "state"),
            "--repository-root",
            str(tmp_path / "repo"),
        ],
    )
    assert cli.main() == 0
    value = json.loads(capsys.readouterr().out)
    assert set(value) == {"schema_version", "status", "machine_code", "result"}
    assert value["status"] == "ESCALATE" and value["result"] is None
    assert "private" not in json.dumps(value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-only deployment")
def test_epic_entry_cannot_import_code_before_pinned_policy_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    config: dict[str, Any] = {key: "synthetic" for key in cli.EPIC_CONFIG_KEYS}
    config.update(
        schema_version="1.0",
        started_at=150,
        owner_identity="OWNER",
        task_iterations=0,
        follow_up_issues=0,
        approved_order=[274],
    )
    mandate = _mandate()
    mandate["schema_version"] = "3.0"
    approved = delivery.mandate_digest(mandate)
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approval["mandate_digest"] = approved
    config["approved_mandate_digest"] = approved
    monkeypatch.setattr(time, "time", lambda: 200)
    for name, value in (
        ("epic-runtime.json", config),
        ("mandate.json", mandate),
    ):
        target = state / name
        target.write_text(json.dumps(value), encoding="utf-8")
        target.chmod(0o600)

    def refuse(*args: object, **kwargs: object) -> None:
        raise cli.DeploymentError("UNTRUSTED_RUNTIME_COMMAND")

    monkeypatch.setattr(cli, "verify_policy", refuse)
    with pytest.raises(cli.DeploymentError, match="UNTRUSTED_RUNTIME_COMMAND"):
        cli.build_session(state, tmp_path / "repo")
