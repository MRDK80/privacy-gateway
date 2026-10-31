"""Config7/8 planning adapter inheritance; real guards, synthetic transport only."""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_planning_roles as roles
from tools import agent_epic_runtime_cli as guards
from tools import codex_adapter as codex
from tools.agent_continuation import digest
from tools.agent_learning import LearningError
from tools.agent_orchestrate import StateStore

from tests import learning_planning_native_fixture as native
from tests.test_agent_epic_loop import (
    active_runtime_namespace as active_runtime_namespace,
)
from tests.test_agent_learning_workflow_binding import initial_config8

POSIX = pytest.mark.skipif(os.name != "posix", reason="Original private state guards")


def _case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, role: str,
) -> tuple[argparse.Namespace, dict[str, Any], Path]:
    value = native.prepared(tmp_path, monkeypatch)
    state = Path(value["state"])
    if version == "8.0":
        initial_config8(value)
    store = StateStore(state)
    config = store.load("epic-runtime")
    mandate = store.load("mandate")
    planning = store.load("planning-profile")
    owner = store.load("learning-profile")
    assert config is not None and mandate is not None
    assert planning is not None and owner is not None
    assert config["schema_version"] == version
    assert not (state / "invocations").exists()
    body = "## Acceptance\n- synthetic criterion\n"
    wire = {
        "schema_version": "1.0",
        "identity": {"repository": mandate["repository"], "epic": mandate["epic"],
                     "task": 303, "policy_sha": value["sha"]},
        "evidence": {"issue_body_digest": hashlib.sha256(body.encode()).hexdigest(),
                     "default_sha": value["sha"], "base_sha": value["sha"]},
        "issue_body": body,
        "criteria": ["- synthetic criterion"],
        "dependencies": [],
        "task_profile": copy.deepcopy(planning["tasks"]["303"]),
        "grant": copy.deepcopy(mandate["task_grants"]["303"]),
        "limits": copy.deepcopy(planning["limits"]),
        "remaining_seconds": 120,
        "proposal": None if role == "planner" else {"synthetic": "proposal"},
        "untrusted_verified_hints": {
            "schema_version": "1.0", "role": "planner" if role == "planner"
            else "planning_review", "catalog_digest": owner["catalog_digest"],
            "selection_digest": digest([]), "hints": [],
        },
    }
    args = argparse.Namespace(root=Path(value["root"]), policy_sha=value["sha"],
                              role=role, learning_state=state)
    return args, wire, state


def _response(wire: dict[str, Any]) -> dict[str, Any]:
    if wire["proposal"] is not None:
        return {"schema_version": "1.0",
                "plan_digest": guards.canonical_digest(wire["proposal"]),
                "verdict": "PASS", "findings": []}
    identity, evidence, grant = wire["identity"], wire["evidence"], wire["grant"]
    plan = {
        "schema_version": "2.0", **identity,
        "criteria_source": {"kind": "github_issue", "issue": identity["task"],
                            "state": "OPEN",
                            "body_sha256": evidence["issue_body_digest"]},
        "acceptance_criteria": wire["criteria"], "delivery_criterion_indices": [],
        "default_ref": "main", "default_sha": evidence["default_sha"],
        "base_ref": wire["task_profile"]["demo_template"]["roadmap_ref"],
        "base_sha": evidence["base_sha"], "head_ref": "codex/303-unit",
        "causal_scope": copy.deepcopy(grant["causal_scope"]),
        "allowed_paths": grant["allowed_paths"],
        "changes_policy_or_architecture": False,
        "budgets": {"max_minutes": 1, "max_repair_iterations": 0,
                    "max_report_chars": 10000},
        "task_class": wire["task_profile"]["task_class"],
    }
    return {"schema_version": "1.0", "plan": plan,
            "demo_plan": wire["task_profile"]["demo_template"],
            "criteria_mapping": [{"criterion_index": index,
                                  "allowed_paths": grant["allowed_paths"],
                                  "reason": "Synthetic scoped unit response"}
                                 for index in range(len(wire["criteria"]))]}


def _transport(
    monkeypatch: pytest.MonkeyPatch, wire: dict[str, Any],
) -> list[list[str]]:
    original = subprocess.run
    seen: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        command = list(argv)
        if command[0] != "codex":
            return original(argv, **kwargs)
        seen.append(command)
        if command == ["codex", "--version"]:
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.157.1\n", "")
        assert command[:2] == ["codex", "exec"]
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--strict-config" in command and "--ephemeral" in command
        assert "--ignore-user-config" in command and "--ignore-rules" in command
        assert "--dangerously-bypass-approvals-and-sandbox" not in command
        assert "-m" not in command
        prompt = kwargs["input"]
        payload = json.loads(prompt.split("<untrusted-planning-data>\n", 1)[1]
                             .split("\n</untrusted-planning-data>", 1)[0])
        assert payload == wire
        schema = json.loads(Path(command[command.index("--output-schema") + 1])
                            .read_text())
        assert schema["additionalProperties"] is False
        response = _response(wire)
        if wire["proposal"] is None:
            scope = response["plan"]["causal_scope"]
            scope_schema = schema["properties"]["plan"]["properties"]["causal_scope"]
            assert isinstance(scope, list) and scope == wire["grant"]["causal_scope"]
            assert scope_schema["items"]["type"] == "string"
            assert all(type(item) is str and item in scope_schema["items"]["enum"]
                       for item in scope)
        Path(command[command.index("-o") + 1]).write_text(json.dumps(response))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(wire)))
    return seen


@POSIX
@pytest.mark.usefixtures("active_runtime_namespace")
@pytest.mark.parametrize("version,role", [
    ("7.0", "planner"), ("7.0", "review"),
    ("8.0", "planner"), ("8.0", "review"),
], ids=["7-planner", "7-review", "8-planner", "8-review"])
def test_managed_learning_roles_preserve_guarded_inheritance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, role: str,
) -> None:
    args, wire, state = _case(tmp_path, monkeypatch, version, role)
    seen = _transport(monkeypatch, wire)
    assert roles.adapt(args) == _response(wire)
    assert len(seen) == 2 and seen[0] == ["codex", "--version"]
    assert seen[1][:2] == ["codex", "exec"]
    assert not (state / "invocations").exists()
    assert not (state / "native-planning-observations.jsonl").exists()


@POSIX
@pytest.mark.usefixtures("active_runtime_namespace")
@pytest.mark.parametrize("fault", ["profile", "stopped-launch", "hint-role"])
def test_original_learning_guards_still_refuse_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    args, wire, state = _case(tmp_path, monkeypatch, "8.0", "planner")
    store = StateStore(state)
    if fault == "profile":
        profile = store.load("learning-profile")
        assert profile is not None
        profile["owner_identity"] = "DIFFERENT_SYNTHETIC_OWNER"
        store.save("learning-profile", profile)
        error: type[Exception] = guards.DeploymentError
        code = "LEARNING_PROFILE_INVALID"
    elif fault == "stopped-launch":
        control = store.load("launch-control")
        assert control is not None
        control["state"] = "STOPPED"
        store.save("launch-control", control)
        error, code = guards.DeploymentError, "OWNER_STOPPED"
    else:
        wire["untrusted_verified_hints"]["role"] = "executor"
        error, code = LearningError, "LEARNING_HINT_INVALID"
    seen = _transport(monkeypatch, wire)
    with pytest.raises(error, match=code):
        roles.adapt(args)
    assert seen == []


@POSIX
@pytest.mark.usefixtures("active_runtime_namespace")
@pytest.mark.parametrize("version", ["6.0", "9.0"])
def test_other_versions_never_gain_learning_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str,
) -> None:
    args, wire, state = _case(tmp_path, monkeypatch, "7.0", "planner")
    store = StateStore(state)
    config = store.load("epic-runtime")
    assert config is not None
    config["schema_version"] = version
    if version == "6.0":
        del config["approved_learning_digest"]
        launch, adapter, control = (store.load(name) for name in (
            "launch", "runtime-adapter", "launch-control",
        ))
        assert launch is not None and adapter is not None and control is not None
        launch["runtime_config_digest"] = guards.launch_config_digest(config, adapter)
        config["approved_launch_digest"] = guards.canonical_digest(launch)
        control["launch_digest"] = config["approved_launch_digest"]
        store.save("launch", launch)
        store.save("launch-control", control)
    store.save("epic-runtime", config)
    seen = _transport(monkeypatch, wire)
    if version == "6.0":
        with pytest.raises(codex.AdapterError, match="INVALID_REQUEST"):
            roles.adapt(args)
    else:
        with pytest.raises(guards.DeploymentError, match="RUNTIME_CONFIG_INVALID"):
            roles.adapt(args)
    assert seen == []


@pytest.mark.skipif(os.name == "posix", reason="Native Windows fail-closed boundary")
def test_native_nonposix_refuses_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = dict.fromkeys(roles.REQUEST_KEYS)
    wire["untrusted_verified_hints"] = {}
    seen = _transport(monkeypatch, wire)
    args = argparse.Namespace(root=tmp_path, policy_sha="a" * 40,
                              role="planner", learning_state=tmp_path / "state")
    with pytest.raises(guards.DeploymentError, match="RUNTIME_ADAPTER_UNSUPPORTED"):
        roles.adapt(args)
    assert seen == []
