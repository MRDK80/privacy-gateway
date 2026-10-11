"""Only real validated workflow review requests can produce artifact receipts."""

import os
import subprocess
import time
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


def test_trusted_factory_capture_pause_saves_matching_terminal_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    from tools import agent_epic_runtime_cli as guards
    from tools import agent_gate
    from tools import agent_quota_source as source
    from tools import agent_quota_supervisor as supervisor
    from tools.agent_quota_wait import QuotaWait, bare_digest

    from tests.test_agent_epic_bootstrap import checkpoint
    from tests.test_agent_orchestrator import _contract, _executor_report, _git
    from tests.test_agent_quota_wait import setup

    if os.name != "posix":
        pytest.skip("Private POSIX runtime")
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir(mode=0o700)
    clock = [1000]
    reader, owner, identity = setup(fixture_dir, clock)
    root, state = reader.root, reader.directory
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Synthetic User")
    _git(root, "config", "user.email", "synthetic@example.invalid")
    for name, contents in (
        ("AGENTS.md", "trusted policy\n"),
        ("CONTRIBUTING.md", "trusted process\n"),
        ("code.py", "VALUE = 1\n"),
    ):
        (root / name).write_text(contents)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "roadmap/162-agent-orchestration")
    _git(root, "switch", "-c", "feat/165-agent-orchestrator")
    (root / "code.py").write_text("VALUE = 2\n")
    contract = _contract(root)
    report = _executor_report(contract, _git(root, "rev-parse", "HEAD"))
    backend = bare_digest(owner["source"])
    policy = tmp_path / "policy"
    for path in (policy, policy / "tools", policy / "docs", policy / "docs/schemas"):
        path.mkdir(mode=0o700)
    script = policy / "tools/codex_adapter.py"
    script.write_text(
        "import hashlib,json,sys\npayload=json.load(sys.stdin)\n"
        "role=sys.argv[sys.argv.index('--role')+1]\n"
        f"if role=='executor':\n print(json.dumps({report!r}))\n sys.exit(0)\n"
        f"event={{'schema_version':'1.0','role':'controller','backend_digest':{backend!r},"
        "'thread_digest':'a'*64,'turn_digest':'b'*64,'outcome':'UNKNOWN',"
        "'error_code':'usageLimitExceeded','invocation_digest':hashlib.sha256("
        "json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False)"
        ".encode()).hexdigest()}\n"
        "print(json.dumps({'role_interruption':event}))\nsys.exit(20)\n"
    )
    script.chmod(0o600)
    original_run = subprocess.run

    def frozen_read(argv: Any, **kwargs: Any) -> Any:
        if argv[:2] == ["git", "show"] and argv[2].startswith("a" * 40):
            return SimpleNamespace(returncode=0, stdout=script.read_bytes())
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", frozen_read)
    monkeypatch.setattr(guards, "managed_launch_guard", lambda *args: None)
    monkeypatch.setattr(guards, "quota_profile_guard", lambda *args: owner)
    monkeypatch.setattr(
        guards, "launch_config_digest", lambda *args: identity["runtime_config_digest"]
    )
    monkeypatch.setattr(time, "time", lambda: clock[0])
    config = {
        "schema_version": "5.0",
        "approved_mandate_digest": owner["mandate_digest"],
        "approved_wait_digest": owner["wait_profile_digest"],
        "approved_quota_digest": bare_digest(owner),
    }
    workflow.StateStore(state).save("epic-runtime", config)
    workflow.StateStore(state).save("runtime-adapter", {})
    workflow.StateStore(state).save(
        "mandate",
        {
            "repository": owner["repository"],
            "epic": owner["epic"],
            "policy_sha": owner["policy_sha"],
        },
    )
    saved = checkpoint(version="3.0")
    saved.update(
        task=300, policy_sha="a" * 40, pending_phase="RUN_TASK", status="RUNNING"
    )
    loop.CheckpointStore(state, root).save(saved)
    monkeypatch.setattr(
        supervisor, "prepare", lambda *args: ({"expires_at": 1100}, b"")
    )

    @contextmanager
    def context(stage: str) -> Any:
        assert stage == reader.identity["stage"]
        yield reader

    scope = SimpleNamespace(context=context)
    current = QuotaWait(
        reader,
        owner,
        identity,
        runtime_deadline=1100,
        now=lambda: clock[0],
        guard=lambda: None,
    )
    value = current._load()
    value["deadline_at"] = 1001
    current._save(value)
    calls: list[str] = []

    def read(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("source")
        assert kwargs["timeout_seconds"] == 1
        assert isinstance(kwargs["guard"].__self__, QuotaWait)
        kwargs["guard"]()
        return {"primary": {"usedPercent": 100, "resetsAt": None}, "secondary": None}

    monkeypatch.setattr(source, "read", read)
    adapter = bridge.codex_role_adapter(
        policy_root=policy,
        root=root,
        policy_sha="a" * 40,
        quota_state=state,
        wait_scope=scope,
    )
    with agent_gate.trusted_snapshot_session(
        root=root, base_sha=contract.base_sha, allowed_paths=contract.allowed_paths
    ) as snapshot:
        evidence = agent_gate.build_evidence(
            snapshot,
            [
                agent_gate.CheckResult(
                    name, ("synthetic",), "passed", 0, 0.0, "passed", {}
                )
                for name in agent_gate.PROFILES[agent_gate.FULL_PROFILE]
            ],
        )
    monkeypatch.setattr(workflow, "_production_gate", lambda *args: evidence)
    monkeypatch.setattr(
        handover,
        "validate_handover",
        lambda *args, **kwargs: SimpleNamespace(contract=contract),
    )
    runner = bridge.ReviewedWorkflow(
        root=root,
        directory=state,
        value={},
        approved_digest="synthetic",
        adapter=adapter,
    )
    with pytest.raises(loop.QuotaPaused):
        runner.run()
    retained = workflow.StateStore(state / "bounded-workflow").load(
        workflow._run_key(contract)
    )
    assert retained is not None
    assert (
        retained["quota_interruption"]["invocation_digest"]
        == retained["invocation_digest"]
    )
    assert retained["quota_interruption"]["backend_digest"] == backend
    assert retained["executor_calls"] == retained["controller_calls"] == 1
    assert workflow.controller_resume_available(
        contract, root=root, storage=workflow.StateStore(state / "bounded-workflow")
    )
    assert runner.controller_resume_available()
    original_start = retained["started"]
    with pytest.raises(loop.QuotaPaused):
        runner.run()
    resumed = workflow.StateStore(state / "bounded-workflow").load(
        workflow._run_key(contract)
    )
    assert resumed is not None
    assert resumed["executor_calls"] == 1 and resumed["controller_calls"] == 2
    assert resumed["started"] == original_start and resumed["repair_iterations"] == 0
    assert calls == ["source"]
