"""Only real validated workflow review requests can produce artifact receipts."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tools import agent_coordinator_handover as handover
from tools import agent_epic_loop as loop
from tools import agent_epic_workflow as bridge
from tools import agent_orchestrate as workflow


class NeverAdapter:
    def execute(self, request: dict[str, Any], session_id: str) -> dict[str, Any]:
        pytest.fail("no production executor in this test")

    def review(
        self, request: dict[str, Any], trusted_policy: dict[str, str], session_id: str
    ) -> dict[str, Any]:
        pytest.fail("no production controller in this test")


def test_workflow_artifacts_cannot_live_inside_checkout(tmp_path: Path) -> None:
    with pytest.raises(loop.LoopError, match="UNSAFE_STORAGE"):
        bridge.ReviewedWorkflow(
            root=tmp_path,
            directory=tmp_path / "state",
            value={},
            approved_digest="synthetic",
            adapter=NeverAdapter(),
        )


def test_role_adapter_rejects_mutable_task_checkout(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX owner-only deployment")
    with pytest.raises(loop.LoopError, match="UNTRUSTED_RUNTIME_COMMAND"):
        bridge.codex_role_adapter(
            policy_root=tmp_path, root=tmp_path, policy_sha="a" * 40
        )


def test_controller_has_explicit_bounded_full_diff_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX owner-only deployment")
    from types import SimpleNamespace

    policy = tmp_path / "policy"
    for path in (policy, policy / "tools", policy / "docs", policy / "docs/schemas"):
        path.mkdir(mode=0o700)
    script = policy / "tools/codex_adapter.py"
    script.write_text("# synthetic frozen adapter\n", encoding="utf-8")
    script.chmod(0o600)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=script.read_bytes()
        ),
    )
    adapter = bridge.codex_role_adapter(
        policy_root=policy, root=tmp_path / "repo", policy_sha="a" * 40
    )
    command = adapter.controller_command
    assert command[command.index("--input-limit") + 1] == "1000000"
    assert "--input-limit" not in adapter.executor_command


def test_missing_artifact_is_not_successful_workflow_evidence(tmp_path: Path) -> None:
    value = {"repository": "OWNER/repository", "epic": 248, "task": 274}
    runner = bridge.ReviewedWorkflow(
        root=tmp_path / "repo",
        directory=tmp_path / "state",
        value=value,
        approved_digest="sha256:" + "a" * 64,
        adapter=NeverAdapter(),
    )
    assert runner.artifacts() == {}
    with pytest.raises(handover.HandoverError):
        runner.run()
    assert runner.artifacts() == {}


def test_saved_workflow_without_captured_review_is_not_reexecuted_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    runner = bridge.ReviewedWorkflow(
        root=tmp_path / "repo",
        directory=tmp_path / "state",
        value={},
        approved_digest="synthetic",
        adapter=NeverAdapter(),
    )
    monkeypatch.setattr(
        handover,
        "validate_handover",
        lambda *args, **kwargs: SimpleNamespace(contract=SimpleNamespace()),
    )
    monkeypatch.setattr(
        workflow,
        "run",
        lambda *args, **kwargs: workflow.RunResult("PASS", "OK", 0, "synthetic"),
    )
    with pytest.raises(loop.PhaseBlocked, match="WORKFLOW_REVIEW_NOT_VERIFIED"):
        runner.run()
    assert runner.artifacts() == {}
