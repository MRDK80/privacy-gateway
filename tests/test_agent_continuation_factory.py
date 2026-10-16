"""Frozen config6/task3 factory with actual guards and synthetic role transport.

Install/control writes are confined to a new pytest temporary fixture. No real
owner state, service, GitHub writes, inference or host namespace proof.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_epic_entry as entry
from tools import agent_epic_install as install
from tools import agent_epic_runtime_cli as guards
from tools import agent_orchestrate as workflow

from tests.test_agent_continuation import owner
from tests.test_agent_continuation_backend import document
from tests.test_agent_coordinator_handover import _value
from tests.test_agent_epic_handover import _mandate

SOURCE = Path.cwd()


def build(
    base: Path,
    *,
    extra_tasks: tuple[int, ...] = (),
    delivery_enabled: bool = False,
    with_demo: bool = False,
    with_planning_policy: bool = False,
) -> dict[str, str]:
    assert not base.exists() and not base.is_symlink()
    base.mkdir(mode=0o700)
    root, state = base / "repo", base / "state"
    root.mkdir(mode=0o700)
    state.mkdir(mode=0o700)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()

    shutil.copytree(
        SOURCE / "tools",
        root / "tools",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(SOURCE / "docs/schemas", root / "docs/schemas")
    shutil.copytree(SOURCE / ".github/workflows", root / ".github/workflows")
    (root / "AGENTS.md").write_text("synthetic policy\n")
    (root / "CONTRIBUTING.md").write_text("synthetic process\n")
    if with_planning_policy:
        for name in workflow.POLICY_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("Synthetic planning policy fixture.\n")
    (root / "code.py").write_text("VALUE = 1\n")
    git("init", "-b", "main")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    git("remote", "add", "origin", "https://github.com/OWNER/repo.git")
    git("add", ".")
    git("commit", "-m", "synthetic frozen runtime")
    sha = git("rev-parse", "HEAD")
    git("branch", "roadmap/294-autonomy")
    git("switch", "-c", "codex/301-test")
    accepted = install.prepare(root, sha, "OWNER/repo", base / "candidate")
    installed = base / "installed"
    # This copies candidate files into a NEW fixture directory; no process starts.
    install.activate(root, base / "candidate", installed, accepted["candidate_digest"])
    store = workflow.StateStore(state)
    now = int(time.time())
    mandate = _mandate(sha)
    mandate.update(
        schema_version="3.0",
        epic=294,
        roadmap_ref="roadmap/294-autonomy",
        issued_at=now - 10,
        expires_at=now + 3600,
    )
    mandate["limits"]["max_duration_seconds"] = 3600
    mandate["task_grants"] = {
        "301": {
            "causal_scope": ["synthetic capacity fixture"],
            "allowed_paths": ["code.py"],
        }
    }
    for task in extra_tasks:
        mandate["task_grants"][str(task)] = {
            "causal_scope": ["synthetic capacity fixture"],
            "allowed_paths": ["code.py"],
        }
    if delivery_enabled:
        mandate["operations"] += [
            "commit_task",
            "push_task",
            "create_task_pr",
            "merge_task_pr",
            "close_task",
            "update_epic",
        ]
    mandate["approval"]["approved_at"] = now - 10
    digest = delivery.mandate_digest(mandate)
    mandate["approval"]["mandate_digest"] = digest
    handover = _value(sha)
    body = "synthetic criterion\n"
    handover.update(
        schema_version="2.0",
        repository="OWNER/repo",
        epic=294,
        task=301,
        base_ref="roadmap/294-autonomy",
        head_ref="codex/301-test",
        allowed_paths=["code.py"],
        causal_scope=["synthetic capacity fixture"],
        acceptance_criteria=["synthetic criterion"],
        delivery_criterion_indices=[],
        trusted_policy={"source": "pinned_policy_sha", "policy_sha": sha},
    )
    handover["criteria_source"].update(
        issue=301, body_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest()
    )
    handover["mandate_provenance"] = {
        "schema_version": "3.0",
        "digest": digest,
        "policy_sha": sha,
    }
    from tools.agent_coordinator_handover import handover_digest

    approved = handover_digest(handover)
    handover["approval"]["plan_digest"] = approved
    plans: dict[str, Any] = {
        "schema_version": "1.0",
        "initial_handover_digest": approved,
        "tasks": {},
    }
    if with_demo:
        plans["tasks"]["301"] = {
            "plan": handover,
            "demo_plan": {
                "schema_version": "1.0",
                "repository": "OWNER/repo",
                "epic": 294,
                "task": 301,
                "baseline_ref": "main",
                "baseline_sha": sha,
                "roadmap_ref": "roadmap/294-autonomy",
                "roadmap_sha": sha,
                "consumer_visible": False,
                "not_applicable_reason": "Synthetic internal fixture",
                "scenario": None,
                "parameters": [],
                "old_argv": [],
                "new_argv": [],
            },
        }
    native = state / "native-fixture"
    native.write_text(
        "#!" + sys.executable + "\nimport sys\n"
        'if "--version" in sys.argv:\n print("codex-cli 0.157.1")\n'
        "else:\n raise SystemExit(1)\n"
    )
    native.chmod(0o700)
    backend = document()
    backend["source"].update(
        executable=str(native),
        executable_sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
        version_digest=hashlib.sha256(b"codex-cli 0.157.1").hexdigest(),
    )
    continuation = owner()
    continuation.update(
        policy_sha=sha,
        mandate_digest=digest,
        owner_identity="OWNER",
        roadmap_ref=mandate["roadmap_ref"],
        backend_digest=guards.canonical_digest(backend).removeprefix("sha256:"),
    )
    continuation["limits"]["max_prompt_bytes"] = 262144
    wait = {
        "schema_version": "1.0",
        **{
            key: mandate[key]
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        },
        "mandate_digest": digest,
        "limits": {
            "max_phase_seconds": 300,
            "max_read_attempts": 2,
            "max_ci_polls": 2,
            "max_read_commands": 100,
        },
    }
    config = {
        "schema_version": "6.0",
        "policy_root": str(installed / "policy"),
        "approved_mandate_digest": digest,
        "owner_identity": "OWNER",
        "started_at": now,
        "task_iterations": 0,
        "follow_up_issues": 0,
        "approved_order": [301, *extra_tasks],
        "approved_plans_digest": guards.canonical_digest(plans),
        "approved_final_digest": None,
        "approved_launch_digest": None,
        "approved_planning_digest": None,
        "approved_wait_digest": guards.canonical_digest(wait),
        "approved_quota_digest": None,
        "approved_continuation_digest": guards.canonical_digest(continuation),
    }
    binary = state / "adapter-bin"
    binary.mkdir(mode=0o700)
    script = binary / "runtime.py"
    script.write_bytes((installed / "runtime.py").read_bytes())
    script.chmod(0o600)
    command = [sys.executable, str(script)]
    from tools import agent_epic_loop as loop

    adapter = {
        "schema_version": "2.0",
        "live_command": command,
        "phase_commands": {p: command for p in loop.PHASES[1:]},
        "reconcile_commands": {p: command for p in loop.PHASES[1:]},
        "timeout_seconds": 120,
        "output_limit": 100000,
        "epic_command": command
        + ["epic_resume", "--state-dir", str(state), "--repository-root", str(root)],
    }
    checkpoint = {
        "schema_version": "3.0",
        "repository": "OWNER/repo",
        "epic": 294,
        "task": 301,
        "pr": None,
        "base_ref": "roadmap/294-autonomy",
        "base_sha": sha,
        "head_ref": "codex/301-test",
        "head_sha": sha,
        "policy_sha": sha,
        "phase": "PLAN",
        "completed_phases": [],
        "pending_phase": None,
        "merge_sha": None,
        "status": "READY",
        "rate_limit_pause": None,
    }
    for name, value in [
        ("epic-runtime", config),
        ("mandate", mandate),
        ("handover", handover),
        ("approved-plans", plans),
        ("wait-profile", wait),
        ("continuation-profile", continuation),
        ("role-backend", backend),
        ("runtime-adapter", adapter),
    ]:
        store.save(name, value)
    loop.CheckpointStore(state, root).save(checkpoint)
    launch = entry.prepare(root, installed, state)
    config["approved_launch_digest"] = launch["candidate_digest"]
    store.save("epic-runtime", config)
    # This only writes fixture control JSON; no supervisor/service/model starts.
    entry.control(root, state, "RUNNING")
    return {
        "root": str(root),
        "state": str(state),
        "installed": str(installed),
        "sha": sha,
        "body": body,
    }


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX frozen factory")


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "workspace",
        "source",
        "revocation",
        "ownership",
        "initial_dirty",
        "orphan",
        "unknown",
        "receipt",
        "handoff",
        "role_ownership",
    ],
)
def test_frozen_factory_retains_completed_executor_across_processes(
    tmp_path: Path,
    fault: str | None,
) -> None:
    from tools import agent_gate

    from tests.test_agent_orchestrator import _executor_report

    fixture = build(tmp_path / "fixture")
    root, state, installed = (Path(fixture[k]) for k in ("root", "state", "installed"))
    backend = guards.private_json(state / "role-backend.json")
    handover = guards.private_json(state / "handover.json")
    contract = workflow.TaskContract(
        issue=301,
        epic=294,
        acceptance_criteria=("synthetic criterion",),
        base_ref=handover["base_ref"],
        base_sha=fixture["sha"],
        head_ref=handover["head_ref"],
        allowed_paths=("code.py",),
        task_class=handover["task_class"],
        policy_sha=fixture["sha"],
    )
    calls, role_script = state / "synthetic-calls", state / "synthetic-roles.py"
    role_script.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, " + repr(str(SOURCE)) + ")\n"
        "sys.path.insert(0, " + repr(str(installed / "policy")) + ")\n"
        "from tools.agent_continuation_backend import completed_receipt\n"
        "from tools.agent_continuation import digest\n"
        "from tests.test_agent_orchestrator import _verdict\n"
        "payload = json.load(sys.stdin)\nrole = sys.argv[1]\n"
        "if role == 'executor':\n"
        "    Path(" + repr(str(root / "code.py")) + ").write_text('VALUE = 2\\n')\n"
        "with Path(" + repr(str(calls)) + ").open('a') as stream:\n"
        "    stream.write(role + '\\n')\n"
        "output = "
        + repr(_executor_report(contract, fixture["sha"]))
        + " if role == 'executor' else _verdict(payload, 'PASS')\n"
        "owner = " + repr(backend) + "\n"
        "observed = {'model': owner['model'], 'reasoning': 'low', "
        "'thread_digest': 'b'*64, 'turn_digest': 'c'*64}\n"
        "receipt, terminal = completed_receipt(owner, observed, role=role, "
        "invocation=digest(payload), output=output)\n"
        "print(json.dumps({'role_output': output, 'settings_receipt': receipt, "
        "'terminal_fact': terminal}))\n"
    )

    def production_gate(root: Path, contract: Any) -> dict[str, Any]:
        with agent_gate.trusted_snapshot_session(
            root=root,
            base_sha=contract.base_sha,
            allowed_paths=contract.allowed_paths,
        ) as snapshot:
            return agent_gate.build_evidence(
                snapshot,
                [
                    agent_gate.CheckResult(
                        name, ("synthetic",), "passed", 0, 0.0, "passed", {}
                    )
                    for name in agent_gate.PROFILES[agent_gate.FULL_PROFILE]
                ],
            )

    def invoke(initial: bool) -> int:
        pid = getattr(os, "fork")()
        if pid == 0:
            try:
                import importlib

                sys.dont_write_bytecode = True
                for name in list(sys.modules):
                    if name == "tools" or name.startswith("tools."):
                        del sys.modules[name]
                sys.path.insert(0, str(installed / "policy"))
                cli = importlib.import_module("tools.agent_epic_runtime_cli")
                fresh_loop = importlib.import_module("tools.agent_epic_loop")
                fresh_workflow = importlib.import_module("tools.agent_orchestrate")
                handover_module = importlib.import_module(
                    "tools.agent_coordinator_handover"
                )
                assert cli.__file__ is not None
                assert Path(cli.__file__).is_relative_to(installed)
                # GitHub is a synthetic read source; local authority guards are real.
                handover_module.GitHubCLI.issue = lambda self, repo, number: {
                    "number": number,
                    "state": "OPEN",
                    "parent": {"number": 294},
                    "body": fixture["body"],
                }
                session = cli.build_session(state, root, wake=True)
                with fresh_loop.RunnerLock(state):
                    engine = session.task_adapter(session.store, 0)
                    task_config = cli.private_json(state / "runtime-task.json")
                    assert task_config["schema_version"] == "3.0"
                    assert (
                        task_config["approved_continuation_digest"]
                        == (session.config["approved_continuation_digest"])
                    )
                    reviewed = engine.bootstrap.reviewed
                    adapter = reviewed.adapter
                    assert type(adapter).__name__ == "SettingsAdapter"
                    assert "--continuation-state" in adapter.executor_command
                    assert str(installed / "policy/tools/codex_adapter.py") in (
                        adapter.executor_command
                    )
                    # Only role transport is synthetic; keep the factory's original
                    # pinned-source/lifecycle guard and receipt validation intact.
                    synthetic = fresh_workflow.CommandAdapter(
                        [sys.executable, "-B", str(role_script), "executor"],
                        [sys.executable, "-B", str(role_script), "controller"],
                        root=root,
                        timeout_seconds=5,
                        output_limit=65536,
                    )

                    def transport(command: Any, payload: Any) -> Any:
                        adapter.delegate.guard()
                        assert command in (
                            adapter.executor_command,
                            adapter.controller_command,
                        )
                        role = (
                            "executor"
                            if command == adapter.executor_command
                            else "controller"
                        )
                        return synthetic._call(
                            synthetic.executor_command
                            if role == "executor"
                            else synthetic.controller_command,
                            payload,
                        )

                    adapter.delegate._call = transport
                    setattr(
                        fresh_workflow,
                        "_production_gate",
                        (lambda *_: os._exit(77)) if initial else production_gate,
                    )
                    assert session.wait_scope is not None
                    with session.wait_scope.context("TASK:301:RUN_TASK"):
                        if not initial and fault is None:
                            assert reviewed.controller_resume_available()
                        artifact = reviewed.run()
                    assert artifact["review_receipt"]
                os._exit(0)
            except BaseException as error:
                if fault is not None and (not initial or fault == "initial_dirty"):
                    code = getattr(error, "machine_code", None)
                    if code is None and error.args and isinstance(error.args[0], str):
                        code = error.args[0]
                    (state / "synthetic-factory-stop.json").write_text(
                        json.dumps({"type": type(error).__name__, "code": code})
                    )
                    os._exit(42)
                import traceback

                traceback.print_exc()
                os._exit(2)
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status)

    if fault == "initial_dirty":
        (root / "code.py").write_text("VALUE = 9\n")
        assert invoke(True) == 42
        assert not calls.exists()
        assert json.loads((state / "synthetic-factory-stop.json").read_text()) == {
            "type": "HandoverError",
            "code": "DIRTY_WORKTREE",
        }
        return
    assert invoke(True) == 77
    assert (root / "code.py").read_text() == "VALUE = 2\n"
    canonical = state / "bounded-workflow" / (workflow._run_key(contract) + ".json")
    saved = json.loads(canonical.read_text())
    assert saved["executor_calls"] == 1 and saved["role_pending"] is None
    started = saved["started"]
    proof = saved["completed_executor"]
    if fault == "orphan":
        del saved["completed_executor"]
        workflow.StateStore(state / "bounded-workflow").save(
            workflow._run_key(contract),
            saved,
        )
    elif fault == "unknown":
        saved["role_pending"] = "executor"
        workflow.StateStore(state / "bounded-workflow").save(
            workflow._run_key(contract),
            saved,
        )
    elif fault == "receipt":
        (
            state
            / "role-settings"
            / ("receipt-" + proof["invocation_digest"] + ".json")
        ).unlink()
    elif fault == "handoff":
        next((state / "continuation").glob("handoff-*.json")).unlink()
    elif fault == "workspace":
        (root / "code.py").write_text("VALUE = 3\n")
    elif fault == "source":
        Path(backend["source"]["executable"]).write_text("changed synthetic source")
    elif fault == "revocation":
        mandate = guards.private_json(state / "mandate.json")
        mandate["revoked"] = True
        workflow.StateStore(state).save("mandate", mandate)
    if fault in {"ownership", "role_ownership"}:
        from tools import agent_epic_loop as local_loop

        lock_directory = (
            state if fault == "ownership" else state / "continuation" / "ownership"
        )
        with local_loop.RunnerLock(lock_directory):
            assert invoke(False) == 42
    else:
        assert invoke(False) == (42 if fault else 0)
    assert calls.read_text().splitlines() == (
        ["executor"] if fault else ["executor", "controller"]
    )
    saved = json.loads(canonical.read_text())
    assert saved["executor_calls"] == 1 and saved["started"] == started

    if fault is not None:
        stop = json.loads((state / "synthetic-factory-stop.json").read_text())
        expected = {
            "workspace": {
                "type": "ContinuationError",
                "code": "CONTINUATION_WORKSPACE_CHANGED",
            },
            "orphan": {"type": "HandoverError", "code": "DIRTY_WORKTREE"},
            "unknown": {"type": "HandoverError", "code": "DIRTY_WORKTREE"},
            "receipt": {
                "type": "ContinuationError",
                "code": "CONTINUATION_REASONING_UNVERIFIED",
            },
            "handoff": {
                "type": "ContinuationError",
                "code": "CONTINUATION_STATE_INVALID",
            },
            "role_ownership": {"type": "LoopError", "code": "RUNNER_LOCKED"},
            "source": {
                "type": "DeploymentError",
                "code": "CONTINUATION_SOURCE_CHANGED",
            },
            "revocation": {
                "type": "DeploymentError",
                "code": "MANDATE_APPROVAL_MISMATCH",
            },
            "ownership": {"type": "LoopError", "code": "RUNNER_LOCKED"},
        }
        assert stop == expected[fault]
