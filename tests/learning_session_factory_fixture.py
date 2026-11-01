"""Frozen config6/task3 factory with actual guards and synthetic role transport.

Install/control writes are confined to a new pytest temporary fixture. No real
owner state, service, GitHub writes, inference or host namespace proof.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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
    with_local_gate_sut: bool = False,
    initial_frame_seconds: int = 300,
) -> dict[str, str]:
    assert not base.exists() and not base.is_symlink()
    base.mkdir(mode=0o700)
    root, state = base / "repo", base / "state"
    root.mkdir(mode=0o700)
    state.mkdir(mode=0o700)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
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
    shutil.copyfile(Path.cwd() / "pyproject.toml", root / "pyproject.toml")
    (root / "code.py").write_text("VALUE = 1\n")
    if with_local_gate_sut:
        from tests.test_agent_final_learning_lifecycle_genuine import (
            CONFTEST,
            SMOKE,
        )

        shutil.copytree(
            Path.cwd() / "src/privacy_gateway", root / "src/privacy_gateway"
        )
        for relative in (
            ".pre-commit-config.yaml",
            ".secrets.baseline",
            ".gitignore",
            "README.md",
            "LICENSE",
            *workflow.POLICY_FILES,
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path.cwd() / relative, target)
        tiny = root / "tests"
        tiny.mkdir()
        (tiny / "conftest.py").write_text(CONFTEST)
        (tiny / "test_snapshot_smoke.py").write_text(SMOKE)
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
            "max_phase_seconds": initial_frame_seconds,
            "max_read_attempts": 2,
            "max_ci_polls": 8,
            "max_read_commands": 512,
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
        "timeout_seconds": initial_frame_seconds,
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
