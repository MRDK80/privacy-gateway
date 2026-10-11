"""Pinned planning commands and full bounded independent-review input."""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tools import agent_epic_loop as loop
from tools import agent_epic_planning as planning
from tools import agent_epic_planning_roles as roles
from tools import agent_orchestrate as workflow
from tools import codex_adapter as codex
from tools.agent_epic_runtime_cli import canonical_digest

from tests.test_agent_epic_planning import (
    BODY,
    arguments,
    git,
)
from tests.test_agent_epic_planning import (
    facts as facts,
)
from tests.test_agent_epic_planning import (
    roles as responses,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX installed roles")


def request(facts: dict[str, Any]) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    body = BODY + "\n## Outside\nAdditional requirement; never omit this from review.\n"
    invoke = responses(facts, [])

    def capture(role: str, value: Mapping[str, Any]) -> dict[str, Any]:
        if role == "review":
            captured.update(copy.deepcopy(value))
        return dict(invoke(role, dict(value)))

    planning.Admission(facts["state"], facts["root"]).admit(
        **arguments(facts, body),
        dependencies=[{"number": 250, "state": "CLOSED"}],
        invoke=capture,
        guard=lambda: None,
    )
    return captured


@pytest.mark.parametrize("role", ["planner", "review"])
def test_actual_codex_argv_is_fixed_read_only_and_full_data_is_bounded(
    facts: dict[str, Any], monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    value = request(facts)
    if role == "planner":
        value["proposal"] = None
    raw = json.dumps(value)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    monkeypatch.setattr(workflow, "_git", lambda *args: "Synthetic trusted policy")
    monkeypatch.setattr(codex, "_assert_version", lambda *args: None)
    seen: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        seen.append(argv)
        assert argv[:2] == ["codex", "exec"]
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv
        assert "--ignore-user-config" in argv and "--ignore-rules" in argv
        assert "--strict-config" in argv and "--ephemeral" in argv
        assert "-m" not in argv
        prompt = kwargs["input"]
        full = json.loads(
            prompt.split("<untrusted-planning-data>")[1].split(
                "</untrusted-planning-data>"
            )[0]
        )
        assert full == value
        assert full["issue_body"].endswith(
            "Additional requirement; never omit this from review.\n"
        )
        assert full["criteria"] == ["- First criterion", "- Second criterion"]
        assert full["dependencies"] == [{"number": 250, "state": "CLOSED"}]
        assert full["grant"] == facts["mandate"]["task_grants"]["298"]
        assert full["task_profile"] == facts["owner"]["tasks"]["298"]
        assert "independently evaluate the complete issue body" in prompt
        assert "trusted-policy" in prompt
        output = (
            {
                "schema_version": "1.0",
                "plan_digest": canonical_digest(value["proposal"]),
                "verdict": "NEEDS_DECISION",
                "findings": [{"criterion_index": -1, "code": "OUTSIDE_REQUIREMENT"}],
            }
            if role == "review"
            else facts["proposal"]
        )
        Path(argv[argv.index("-o") + 1]).write_text(json.dumps(output))
        schema = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text())
        assert schema["additionalProperties"] is False
        if role == "review":
            assert schema["properties"]["plan_digest"]["enum"] == [
                canonical_digest(value["proposal"])
            ]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    result = roles.adapt(
        argparse.Namespace(root=facts["root"], policy_sha=facts["sha"], role=role)
    )
    assert seen and (role == "planner" or result["verdict"] == "NEEDS_DECISION")


@pytest.mark.parametrize(
    "fault", ["extra", "oversize", "policy", "dependency_open", "dependency_malformed"]
)
def test_role_rejects_unbounded_or_unbound_input_before_invocation(
    facts: dict[str, Any], monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    value = request(facts)
    if fault == "extra":
        value["command"] = ["untrusted"]
    elif fault == "oversize":
        value["issue_body"] = "X" * value["limits"]["max_input_chars"]
    elif fault == "policy":
        value["identity"]["policy_sha"] = "f" * 40
    elif fault == "dependency_open":
        value["dependencies"][0]["state"] = "OPEN"
    else:
        value["dependencies"][0]["state"] = []
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(value)))
    monkeypatch.setattr(
        codex, "_run_codex", lambda **kwargs: pytest.fail("must not invoke")
    )
    with pytest.raises(codex.AdapterError, match="INVALID_REQUEST"):
        roles.adapt(
            argparse.Namespace(
                root=facts["root"], policy_sha=facts["sha"], role="review"
            )
        )


@pytest.mark.parametrize("fault", [None, "modified", "symlink"])
def test_outer_command_uses_verified_installed_bytes_and_no_input_argv(
    facts: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str | None,
) -> None:
    value = request(facts)
    root = facts["root"]
    for name in ("tools/codex_adapter.py", "tools/agent_epic_planning_roles.py"):
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("# Synthetic pinned role\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "synthetic pinned inventory")
    sha = git(root, "rev-parse", "HEAD")
    policy = tmp_path / "installed"
    for directory in (
        policy,
        policy / "tools",
        policy / "docs",
        policy / "docs/schemas",
    ):
        directory.mkdir(mode=0o700)
    for name in ("tools/codex_adapter.py", "tools/agent_epic_planning_roles.py"):
        path = policy / name
        path.write_bytes((root / name).read_bytes())
        path.chmod(0o600)
    script = policy / "tools/agent_epic_planning_roles.py"
    if fault == "modified":
        script.write_text("# Modified bytes\n")
    elif fault == "symlink":
        script.unlink()
        script.symlink_to(root / "tools/agent_epic_planning_roles.py")
    original = subprocess.run
    invoked: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        if argv[0] == "git":
            return original(argv, **kwargs)
        invoked.append(argv)
        assert argv == [
            str(Path(sys.executable).absolute()),
            "-B",
            str(script),
            "--role",
            "review",
            "--root",
            str(root.resolve()),
            "--policy-sha",
            sha,
        ]
        assert json.loads(kwargs["input"]) == value
        return SimpleNamespace(returncode=0, stdout=json.dumps({"synthetic": True}))

    monkeypatch.setattr(subprocess, "run", run)
    adapter = roles.PlanningRoles(root=root, policy_root=policy, policy_sha=sha)
    if fault is None:
        assert adapter("review", value) == {"synthetic": True}
        assert len(invoked) == 1
    else:
        with pytest.raises(loop.LoopError, match="UNTRUSTED_RUNTIME_COMMAND"):
            adapter("review", value)
        assert invoked == []
