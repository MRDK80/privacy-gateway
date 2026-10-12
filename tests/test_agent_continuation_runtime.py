"""Actual Git/lock/source recovery with synthetic role receipts (#301)."""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tools import agent_continuation as records
from tools import agent_epic_loop as loop
from tools import agent_orchestrate as workflow
from tools.agent_continuation_backend import completed_receipt
from tools.agent_continuation_runtime import Continuation
from tools.agent_continuation_store import Store

from tests.test_agent_continuation import owner
from tests.test_agent_continuation_backend import document
from tests.test_agent_continuation_store import prepared as _prepared_fixture

prepared = _prepared_fixture

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX actual lock/store")


def setup(
    fixture: tuple[Path, Store, workflow.TaskContract], monkeypatch: pytest.MonkeyPatch
) -> tuple[Continuation, dict[str, Any], dict[str, Any]]:
    root, store, task = fixture
    task = replace(task, policy_sha=task.base_sha)
    profile = owner()
    profile["policy_sha"] = task.policy_sha
    profile["roadmap_ref"] = task.base_ref
    backend = document()
    profile["backend_digest"] = records.digest(backend)
    seed = store.directory / "seed"
    storage = workflow.StateStore(seed)
    storage.save(
        "epic-runtime", {"schema_version": "6.0", "started_at": int(time.time())}
    )
    storage.save(
        "mandate",
        {
            "expires_at": int(time.time()) + 3600,
            "limits": {"max_duration_seconds": 3600},
        },
    )
    storage.save("runtime-adapter", {"synthetic": True})
    adapter = SimpleNamespace(
        owner=backend,
        delegate=SimpleNamespace(guard=lambda: None),
        profile=profile,
        state=seed,
        guard=lambda: None,
        store=workflow.StateStore(seed / "role-settings"),
    )
    monkeypatch.setattr(
        workflow,
        "_load_trusted_policy",
        lambda *args: {
            "AGENTS.md": "synthetic policy",
            "CONTRIBUTING.md": "synthetic process",
        },
    )
    manager = Continuation(
        adapter=adapter, contract=task, directory=store.directory, root=root
    )
    request = {"contract": workflow._contract_fields(task), "session_id": "synthetic"}
    invocation = records.digest(request)
    state = {
        "run_id": "a" * 32,
        "started": time.time(),
        "repair_iterations": 0,
        "executor_calls": 1,
        "controller_calls": 0,
        "role_pending": "executor",
        "invocation_digest": invocation,
        "terminal": False,
    }
    return manager, state, request


def test_completed_executor_boundary_rereads_facts_without_replaying_role(
    prepared: tuple[Path, Store, workflow.TaskContract],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, state, request = setup(prepared, monkeypatch)
    manager.outer_deadline = int(time.time()) + 60
    output = {"status": "completed"}
    observed = {
        "model": "synthetic-model",
        "reasoning": "low",
        "thread_digest": "b" * 64,
        "turn_digest": "c" * 64,
    }
    receipt, terminal = completed_receipt(
        manager.adapter.owner,
        observed,
        role="executor",
        invocation=state["invocation_digest"],
        output=output,
    )
    with loop.RunnerLock(manager.directory), manager:
        manager.reserve("executor", request, state)
        manager.adapter.store.save(
            state["invocation_digest"], {"receipt": receipt, "terminal": terminal}
        )
        proof = manager.complete("executor", state, output)
        proof["policy_digest"] = records.digest(
            workflow._load_trusted_policy(manager.root, manager.contract)
        )
        resumed = {**state, "role_pending": None, "completed_executor": proof}
        handoff = manager.prepare_handoff(resumed)
        assert handoff["consumed"]["deadline_at"] == manager.outer_deadline
        assert handoff["consumed"]["executor_calls"] == 1
        assert handoff["source"]["reservation_digest"] == proof["reservation_digest"]
        manager.verify_handoff(resumed)
        assert manager.prepare_handoff(resumed) == handoff
        handoff_key = "handoff-" + records.digest(
            {
                "contract": workflow._contract_fields(manager.contract),
                "run_id": state["run_id"],
            }
        )
        for field in ("budget", "pending", "reasoning"):
            import copy

            forged = copy.deepcopy(handoff)
            if field == "budget":
                forged["consumed"]["executor_calls"] = 0
            elif field == "reasoning":
                forged["consumed"]["original_reasoning"] = "high"
            else:
                forged["pending"] = {
                    "kind": "delivery",
                    "operation_digest": "d" * 64,
                    "reservation_digest": "e" * 64,
                    "outcome": "UNKNOWN",
                    "snapshot_digest": "f" * 64,
                }
            forged["record_digest"] = records.digest(
                {k: v for k, v in forged.items() if k != "record_digest"}
            )
            manager.store.save(handoff_key, forged)
            with pytest.raises(records.ContinuationError):
                manager.verify_handoff(resumed)
        manager.store.save(handoff_key, handoff)
        assert manager.resume_executor(resumed) == proof
        assert resumed["executor_calls"] == 1
        with pytest.raises(records.ContinuationError):
            manager.resume_executor({**resumed, "executor_calls": 0})
        (manager.root / "code.py").write_text("VALUE = 3\n")
        with pytest.raises(
            records.ContinuationError, match="CONTINUATION_WORKSPACE_CHANGED"
        ):
            manager.resume_executor(resumed)


def test_interrupted_executor_partial_snapshot_is_unknown_and_not_completion(
    prepared: tuple[Path, Store, workflow.TaskContract],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, state, request = setup(prepared, monkeypatch)
    with loop.RunnerLock(manager.directory), manager:
        manager.reserve("executor", request, state)
        (manager.root / "code.py").write_text("VALUE = 7\n")
        manager.interrupted(state)
        value = manager.store.load("reservation-" + state["invocation_digest"])
        assert value is not None
        assert value["status"] == "UNKNOWN"
        assert value["snapshot_after"] is not None
        assert value["outcome_digest"] is None
        assert manager.resume_executor(state) is None
        assert state["executor_calls"] == 1


def test_private_ownership_symlink_rejected_before_lock(
    prepared: tuple[Path, Store, workflow.TaskContract],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _, _ = setup(prepared, monkeypatch)
    target = manager.directory / "outside-lock"
    target.mkdir(mode=0o700)
    ownership = manager.store.directory / "ownership"
    ownership.symlink_to(target, target_is_directory=True)
    with loop.RunnerLock(manager.directory):
        with pytest.raises(Exception):
            with manager:
                pass
    assert not (target / "epic-loop.lock").exists()


def test_canonical_workflow_state_rejects_symlink_and_oversized_record(
    prepared: tuple[Path, Store, workflow.TaskContract],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, state, _ = setup(prepared, monkeypatch)
    key = workflow._run_key(manager.contract)
    manager.workflow.save(key, state)
    path = manager.workflow.directory / (key + ".json")
    original = path.read_bytes()
    target = manager.directory / "synthetic-state.json"
    target.write_bytes(original)
    target.chmod(0o600)
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(records.ContinuationError):
        manager.workflow.load(key)
    path.unlink()
    path.write_bytes(b" " * 65537)
    path.chmod(0o600)
    with pytest.raises(records.ContinuationError):
        manager.workflow.load(key)


def test_missing_intermediate_consumed_receipt_blocks_reasoning_chain(
    prepared: tuple[Path, Store, workflow.TaskContract],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, state, request = setup(prepared, monkeypatch)
    observed = {
        "model": "synthetic-model", "reasoning": "low",
        "thread_digest": "b" * 64, "turn_digest": "c" * 64,
    }
    output = {"status": "completed"}
    receipt, terminal = completed_receipt(
        manager.adapter.owner, observed, role="executor",
        invocation=state["invocation_digest"], output=output,
    )
    with loop.RunnerLock(manager.directory), manager:
        manager.reserve("executor", request, state)
        manager.adapter.store.save(
            state["invocation_digest"], {"receipt": receipt, "terminal": terminal}
        )
        proof = manager.complete("executor", state, output)
        consumed = {**state, "repair_iterations": 2,
                    "executor_calls": 3, "controller_calls": 2}
        with pytest.raises(records.ContinuationError):
            manager.reasoning_evidence(consumed, proof)


def test_new_process_rereads_handoff_with_fresh_real_locks(
    prepared: tuple[Path, Store, workflow.TaskContract],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, state, request = setup(prepared, monkeypatch)
    observed = {
        "model": "synthetic-model", "reasoning": "low",
        "thread_digest": "b" * 64, "turn_digest": "c" * 64,
    }
    output = {"status": "completed"}
    receipt, terminal = completed_receipt(
        manager.adapter.owner, observed, role="executor",
        invocation=state["invocation_digest"], output=output,
    )
    with loop.RunnerLock(manager.directory), manager:
        manager.reserve("executor", request, state)
        manager.adapter.store.save(
            state["invocation_digest"], {"receipt": receipt, "terminal": terminal}
        )
        proof = manager.complete("executor", state, output)
        proof["policy_digest"] = records.digest(
            workflow._load_trusted_policy(manager.root, manager.contract)
        )
        resumed = {**state, "role_pending": None, "completed_executor": proof}
        manager.prepare_handoff(resumed)
        manager.workflow.save(workflow._run_key(manager.contract), resumed)
    pid = getattr(os, "fork")()
    if pid == 0:
        try:
            with loop.RunnerLock(manager.directory), manager:
                saved = manager.workflow.load(workflow._run_key(manager.contract))
                assert saved is not None
                manager.verify_handoff(saved)
                assert saved["executor_calls"] == 1
                manager.consume_handoff(saved)
            os._exit(0)
        except BaseException:
            os._exit(1)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    with loop.RunnerLock(manager.directory), manager:
        with pytest.raises(records.ContinuationError):
            manager.verify_handoff(resumed)


@pytest.mark.parametrize("capacity", [None, "CONTEXT_CAPACITY_STRATEGY_REQUIRED",
                                      "CONTEXT_REVIEW_TOO_LARGE"])
def test_actual_workflow_process_restart_skips_completed_executor(
    prepared: tuple[Path, Store, workflow.TaskContract],
    monkeypatch: pytest.MonkeyPatch,
    capacity: str | None,
) -> None:
    import shutil
    import sys

    from tools import agent_epic_install as install
    from tools import agent_gate
    from tools.agent_continuation_backend import SettingsAdapter

    from tests.test_agent_continuation_store import git
    from tests.test_agent_orchestrator import _executor_report

    root, storage, contract = prepared
    source_root = Path.cwd()
    shutil.copytree(source_root / "tools", root / "tools",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(source_root / "docs/schemas", root / "docs/schemas")
    (root / "AGENTS.md").write_text("synthetic policy\n")
    (root / "CONTRIBUTING.md").write_text("synthetic process\n")
    (root / "code.py").write_text("VALUE = 1\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "synthetic frozen runtime")
    sha = git(root, "rev-parse", "HEAD")
    (root / "code.py").write_text("VALUE = 2\n")
    candidate = storage.directory / "candidate"
    installed = storage.directory / "installed"
    accepted = install.prepare(root, sha, "OWNER/repo", candidate)
    install.activate(root, candidate, installed, accepted["candidate_digest"])
    assert install._validate(root, installed)[0]["policy_sha"] == sha
    prepared = (root, storage, replace(contract, base_sha=sha))
    manager, _, _ = setup(prepared, monkeypatch)
    git(manager.root, "branch", manager.contract.base_ref, manager.contract.base_sha)
    manager.contract = replace(
        manager.contract, acceptance_criteria=("synthetic criterion",)
    )
    profile = manager.owner
    profile["limits"]["max_prompt_bytes"] = 262144
    if capacity is not None:
        profile["limits"]["max_prompt_bytes"] = 20000
        profile["limits"]["threshold_permille"] = 700
        size = 16000 if capacity == "CONTEXT_CAPACITY_STRATEGY_REQUIRED" else 21000
        (root / "code.py").write_text("VALUE = " + repr("x" * size) + "\n")
        manager.contract = replace(manager.contract, max_report_chars=65536)
    source = manager.adapter.owner
    script = manager.directory / "synthetic-role.py"
    calls = manager.directory / "synthetic-calls"
    report = _executor_report(manager.contract, manager.contract.base_sha)
    script.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, " + repr(str(Path.cwd())) + ")\n"
        "sys.path.insert(0, " + repr(str(installed / "policy")) + ")\n"
        "from tools.agent_continuation_backend import completed_receipt\n"
        "from tools.agent_continuation import digest\n"
        "from tests.test_agent_orchestrator import _verdict\n"
        "payload = json.load(sys.stdin)\nrole = sys.argv[1]\n"
        "with Path(" + repr(str(calls)) + ").open('a') as stream:\n"
        "    stream.write(role + '\\n')\n"
        "output = " + repr(report) + " if role == 'executor' else "
        "_verdict(payload, 'PASS')\n"
        "owner = " + repr(source) + "\n"
        "observed = {'model': owner['model'], 'reasoning': 'low', "
        "'thread_digest': 'b'*64, 'turn_digest': 'c'*64}\n"
        "receipt, terminal = completed_receipt(owner, observed, role=role, "
        "invocation=digest(payload), output=output)\n"
        "print(json.dumps({'role_output': output, 'settings_receipt': receipt, "
        "'terminal_fact': terminal}))\n"
    )
    delegate = workflow.CommandAdapter(
        [sys.executable, str(script), "executor"],
        [sys.executable, str(script), "controller"],
        root=manager.root, timeout_seconds=5, output_limit=65536,
    )
    delegate.guard = lambda: None  # type: ignore[attr-defined]
    adapter = SettingsAdapter(
        delegate, source, profile, manager.adapter.state, lambda: None
    )
    manager.adapter = adapter
    adapter.continuation = manager  # type: ignore[attr-defined]

    def production_gate(root: Path, contract: workflow.TaskContract) -> dict[str, Any]:
        with agent_gate.trusted_snapshot_session(
            root=root, base_sha=contract.base_sha, allowed_paths=contract.allowed_paths,
        ) as snapshot:
            return agent_gate.build_evidence(snapshot, [
                agent_gate.CheckResult(name, ("synthetic",), "passed", 0, 0.0,
                                       "passed", {})
                for name in agent_gate.PROFILES[agent_gate.FULL_PROFILE]
            ])

    retained = None
    handoff_bytes = None
    for attempt in range(3 if capacity else 2):
        initial = attempt == 0
        pid = getattr(os, "fork")()
        if pid == 0:
            try:
                import importlib

                for name in list(sys.modules):
                    if name == "tools" or name.startswith("tools."):
                        del sys.modules[name]
                sys.path.insert(0, str(installed / "policy"))
                fresh_workflow = importlib.import_module("tools.agent_orchestrate")
                fresh_loop = importlib.import_module("tools.agent_epic_loop")
                fresh_settings = importlib.import_module(
                    "tools.agent_continuation_backend"
                )
                fresh_runtime = importlib.import_module(
                    "tools.agent_continuation_runtime"
                )
                assert fresh_workflow.__file__ is not None
                assert Path(fresh_workflow.__file__).is_relative_to(installed)
                fresh_delegate = fresh_workflow.CommandAdapter(
                    delegate.executor_command, delegate.controller_command,
                    root=manager.root, timeout_seconds=5, output_limit=65536,
                )
                fresh_delegate.guard = lambda: None
                fresh_adapter = fresh_settings.SettingsAdapter(
                    fresh_delegate, source, profile, manager.adapter.state,
                    lambda: None,
                )
                fresh_manager = fresh_runtime.Continuation(
                    adapter=fresh_adapter, contract=manager.contract,
                    directory=manager.directory, root=manager.root,
                )
                fresh_adapter.continuation = fresh_manager
                if capacity and attempt == 2:
                    def blocked_preflight(*args: Any) -> None:
                        raise AssertionError("retained stop must precede preflight")
                    fresh_manager.preflight = blocked_preflight
                setattr(fresh_workflow, "_production_gate",
                        (lambda *_: os._exit(77)) if initial else production_gate)
                with fresh_loop.RunnerLock(manager.directory), fresh_manager:
                    result = fresh_workflow.run(
                        manager.contract, root=manager.root,
                        storage=fresh_manager.workflow, adapter=fresh_adapter,
                    )
                if capacity is not None:
                    assert result.status == "FAIL_ESCALATE"
                    assert result.machine_code == capacity
                os._exit(0 if capacity or result.status == "PASS" else 1)
            except BaseException:
                os._exit(2)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == (77 if initial else 0)
        saved = manager.workflow.load(workflow._run_key(manager.contract))
        assert saved is not None and saved["executor_calls"] == 1
        if initial:
            started = saved["started"]
            assert saved["role_pending"] is None
        else:
            assert saved["started"] == started
            assert saved["controller_calls"] == (0 if capacity else 1)
            assert saved["repair_iterations"] == 0
            if capacity:
                assert saved["terminal"] is False
                assert saved["role_pending"] is None
                assert saved["completed_executor"]
                assert saved["continuation_gate"]
                assert saved["continuation_handoffs"] == 1
                handoffs = list(manager.store.directory.glob("handoff-*.json"))
                assert len(handoffs) == 1
                if retained is None:
                    retained = saved
                    handoff_bytes = handoffs[0].read_bytes()
                else:
                    assert saved == retained
                    assert handoffs[0].read_bytes() == handoff_bytes
                with loop.RunnerLock(manager.directory), manager:
                    manager.verify_handoff(saved)
    assert calls.read_text().splitlines() == (["executor"] if capacity else
                                            ["executor", "controller"])


@pytest.mark.parametrize("changed", ["record", "digest", "oversized", "symlink",
                                     "checkpoint", "workspace", "guard",
                                     "deployment", "directory", "expiry",
                                     "source", "ownership"])
def test_capacity_stop_revalidates_negative_record_and_original_boundary(
    prepared: tuple[Path, Store, workflow.TaskContract],
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    manager, state, request = setup(prepared, monkeypatch)
    observed = {"model": "synthetic-model", "reasoning": "low",
                "thread_digest": "b" * 64, "turn_digest": "c" * 64}
    output = {"status": "completed"}
    receipt, terminal = completed_receipt(
        manager.adapter.owner, observed, role="executor",
        invocation=state["invocation_digest"], output=output,
    )
    with loop.RunnerLock(manager.directory), manager:
        manager.reserve("executor", request, state)
        manager.adapter.store.save(
            state["invocation_digest"], {"receipt": receipt, "terminal": terminal}
        )
        proof = manager.complete("executor", state, output)
        proof["policy_digest"] = records.digest(
            workflow._load_trusted_policy(manager.root, manager.contract)
        )
        state = {**state, "role_pending": None, "completed_executor": proof}
        manager.prepare_handoff(state)
        key = workflow._run_key(manager.contract)
        before = manager.workflow.load(key)
        result = workflow._continuation_capacity_stop(
            manager, state, "CONTEXT_REVIEW_TOO_LARGE"
        )
        assert result is not None and result.machine_code == "CONTEXT_REVIEW_TOO_LARGE"
        stop_files = list((manager.store.directory / "capacity-stops").glob("*.json"))
        assert len(stop_files) == 1
        original_stop = stop_files[0].read_bytes()
        if changed == "record":
            import json

            value = json.loads(original_stop)
            value["unknown"] = True
            stop_files[0].write_text(json.dumps(value))
        elif changed == "digest":
            import json

            value = json.loads(original_stop)
            value["record_digest"] = "0" * 64
            stop_files[0].write_text(json.dumps(value))
        elif changed == "oversized":
            stop_files[0].write_bytes(b" " * 1025)
        elif changed == "symlink":
            target = manager.directory / "synthetic-negative-stop.json"
            target.write_bytes(original_stop)
            target.chmod(0o600)
            stop_files[0].unlink()
            stop_files[0].symlink_to(target)
        elif changed == "directory":
            import shutil

            directory = stop_files[0].parent
            outside = manager.directory / "synthetic-negative-directory"
            shutil.move(str(directory), outside)
            directory.symlink_to(outside, target_is_directory=True)
        elif changed in {"source", "ownership"}:
            from tools.agent_quota import QuotaError

            def unavailable() -> None:
                if changed == "source":
                    raise QuotaError("QUOTA_SOURCE_CHANGED")
                raise loop.LoopError("RUNNER_OWNERSHIP_REQUIRED")
            manager.adapter.guard = unavailable
        elif changed == "deployment":
            from tools.agent_epic_runtime_cli import DeploymentError

            def stopped() -> None:
                raise DeploymentError("OWNER_STOPPED")
            manager.adapter.guard = stopped
        elif changed == "expiry":
            manager.outer_deadline = int(time.time()) - 1
        elif changed == "checkpoint":
            state = {**state, "started": state["started"] - 1}
        elif changed == "workspace":
            (manager.root / "code.py").write_text("VALUE = 9\n")
        else:
            def revoked() -> None:
                raise workflow.OrchestrationError("MANDATE_REVOKED")
            manager.adapter.guard = revoked
        with pytest.raises(workflow.OrchestrationError):
            workflow._continuation_capacity_stop(manager, state)
        assert manager.workflow.load(key) == before
        if changed not in {"record", "digest", "oversized"}:
            assert stop_files[0].read_bytes() == original_stop


@pytest.mark.parametrize("orphan", [False, True])
def test_capacity_stop_record_never_substitutes_missing_completed_executor(
    prepared: tuple[Path, Store, workflow.TaskContract],
    monkeypatch: pytest.MonkeyPatch,
    orphan: bool,
) -> None:
    from tests.test_agent_continuation_store import git

    manager, state, request = setup(prepared, monkeypatch)
    git(manager.root, "branch", manager.contract.base_ref, manager.contract.base_sha)
    adapter = SimpleNamespace(continuation=manager)
    with loop.RunnerLock(manager.directory), manager:
        if orphan:
            manager.reserve("executor", request, state)
            manager.workflow.save(workflow._run_key(manager.contract), state)
        else:
            manager.owner["limits"]["max_prompt_bytes"] = 1
        result = workflow.run(
            manager.contract, root=manager.root,
            storage=manager.workflow, adapter=adapter,
        )
        assert result.machine_code == ("ROLE_OUTCOME_UNKNOWN" if orphan else
                                       "CONTEXT_PROMPT_TOO_LARGE")
        saved = manager.workflow.load(workflow._run_key(manager.contract))
        assert saved is not None and saved["terminal"] is True
        assert saved["executor_calls"] == int(orphan)
        assert saved["controller_calls"] == 0
        assert "completed_executor" not in saved
        assert not (manager.store.directory / "capacity-stops").exists()
        assert not list(manager.store.directory.glob("handoff-*.json"))
        assert workflow.run(
            manager.contract, root=manager.root,
            storage=manager.workflow, adapter=adapter,
        ) == result
