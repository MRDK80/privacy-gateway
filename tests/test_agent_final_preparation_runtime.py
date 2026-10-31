"""Original-source and payload-bound current-scope tests; transport is synthetic."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_install as install
from tools import agent_epic_runtime_cli as guards
from tools import agent_final_preparation_records as records
from tools import agent_final_preparation_runtime as runtime
from tools.agent_continuation import digest
from tools.agent_continuation_backend import SettingsAdapter
from tools.agent_epic_loop import (
    PHASES,
    CheckpointStore,
    LoopError,
    PhaseBlocked,
    RunnerLock,
)
from tools.agent_epic_wait_scope import Scope
from tools.agent_final_preparation_review import AggregateReview
from tools.agent_final_preparation_role import RoleCounter
from tools.agent_final_preparation_storage import Store
from tools.agent_learning_planning_catalog import catalog_digest

from tests.test_agent_continuation import owner as continuation_owner
from tests.test_agent_continuation_backend import document
from tests.test_agent_epic_loop import _checkpoint
from tests.test_agent_epic_loop import (
    active_runtime_namespace as active_runtime_namespace,
)
from tests.test_agent_final_preseed_guard import fixture, seal, write
from tests.test_agent_learning import profile as learning_owner
from tests.test_agent_role_app_server import child

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX owned native scope")


def original_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Synthetic original setup completed before any scope; no guard is replaced."""
    root, seed = fixture(tmp_path, monkeypatch)
    config = guards.private_json(seed / "epic-runtime.json")
    mandate = guards.private_json(seed / "mandate.json")
    identity = {
        key: mandate[key]
        for key in ("repository", "epic", "roadmap_ref", "policy_sha", "owner_identity")
    }
    identity["mandate_digest"] = config["approved_mandate_digest"]
    policy = Path(config["policy_root"])
    schemas = policy / "docs" / "schemas"
    for directory in (policy, policy / "docs", schemas, seed / "session-control"):
        directory.mkdir(mode=0o700)
    catalog = {"schema_version": "1.0", "rules": []}
    for name in ("agent-learning-catalog", "agent-learning-planning-catalog"):
        write(schemas / (name + ".json"), catalog)
    learning = {**learning_owner(), **identity}
    learning["catalog_digest"] = catalog_digest(catalog, catalog)
    wait = {
        "schema_version": "1.0",
        **identity,
        "limits": {
            "max_phase_seconds": 600,
            "max_read_attempts": 11,
            "max_ci_polls": 11,
            "max_read_commands": 512,
        },
    }
    continuation = {**continuation_owner(), **identity}
    continuation["backend_digest"] = digest(document())
    for name, profile in (
        ("learning", learning),
        ("wait", wait),
        ("continuation", continuation),
    ):
        write(seed / (name + "-profile.json"), profile)
        config["approved_" + name + "_digest"] = guards.canonical_digest(profile)
    write(seed / "role-backend.json", document())
    manifest = {
        "schema_version": "1.0",
        "repository": mandate["repository"],
        "policy_sha": mandate["policy_sha"],
        "inventory_digest": "sha256:" + "1" * 64,
    }
    write(policy.parent / "candidate.json", manifest)
    adapter = guards.private_json(seed / "runtime-adapter.json")
    adapter["timeout_seconds"] = 600
    write(seed / "runtime-adapter.json", adapter)
    launch = guards.private_json(seed / "launch.json")
    launch["installation_digest"] = guards.canonical_digest(manifest)
    launch["runtime_config_digest"] = guards.launch_config_digest(
        config, guards.private_json(seed / "runtime-adapter.json")
    )
    config["approved_launch_digest"] = guards.canonical_digest(launch)
    write(seed / "launch.json", launch)
    write(seed / "epic-runtime.json", config)
    zero_path = seed / "final-preparation" / "role-state.json"
    zero = seal(
        {
            **guards.private_json(zero_path),
            "launch_digest": config["approved_launch_digest"],
        }
    )
    write(zero_path, zero)
    directory = seed / "final-initialization"
    command = seal(
        {
            **guards.private_json(directory / "command-source.json"),
            "launch_digest": config["approved_launch_digest"],
            "counter_zero_digest": digest(zero),
        }
    )
    approval = seal(
        {
            **guards.private_json(directory / "approval.json"),
            "launch_digest": config["approved_launch_digest"],
            "counter_zero_digest": digest(zero),
            "owner_command_digest": digest(command),
        },
        "approved_digest",
    )
    receipt = seal(
        {
            **guards.private_json(directory / "install-receipt.json"),
            "launch_digest": config["approved_launch_digest"],
            "counter_zero_digest": digest(zero),
            "owner_command_digest": digest(command),
            "initialization_digest": digest(approval),
        }
    )
    pin = seal(
        {
            **guards.private_json(directory / "approval-pin.json"),
            "launch_digest": config["approved_launch_digest"],
            "initialization_digest": digest(approval),
            "installation_receipt_digest": digest(receipt),
        }
    )
    for name, value in (
        ("command-source", command),
        ("approval", approval),
        ("install-receipt", receipt),
        ("approval-pin", pin),
    ):
        write(directory / (name + ".json"), value)
    write(
        seed / "launch-control.json",
        {
            "schema_version": "1.0",
            "launch_digest": config["approved_launch_digest"],
            "state": "RUNNING",
            "generation": 0,
        },
    )
    checkpoint = _checkpoint()
    checkpoint.update(
        schema_version="3.0",
        repository=mandate["repository"],
        epic=mandate["epic"],
        policy_sha=mandate["policy_sha"],
        phase="NEXT_TASK",
        status="TASK_DONE",
        merge_sha="a" * 40,
        completed_phases=list(PHASES[1:]),
    )
    CheckpointStore(seed, root).save(checkpoint)
    write(
        seed / "session-control/cursor.json",
        {
            "schema_version": "1.0",
            "repository": mandate["repository"],
            "epic": mandate["epic"],
            "policy_sha": mandate["policy_sha"],
            "seed_task": checkpoint["task"],
            "current_task": checkpoint["task"],
            "completed_tasks": [checkpoint["task"]],
            "pending_task": None,
            "mode": "FINAL",
        },
    )
    return root, seed


@contextmanager
def owned(seed: Path, root: Path) -> Iterator[runtime._OriginalSources]:
    with (
        RunnerLock(seed / "session-control") as supervisor,
        RunnerLock(seed) as ownership,
    ):
        scope = frame(seed, root)
        with scope.context("FINAL:FINAL_GATE"):
            guards.validate_final_preseed(seed, root)
            yield runtime._OriginalSources(seed, root, supervisor, ownership, scope)


def frame(seed: Path, root: Path) -> Scope:
    scope = Scope.for_root(seed, root)
    assert scope is not None
    return scope


def test_factory_requires_real_supervisor_before_any_settings_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed = original_sources(tmp_path, monkeypatch)
    with pytest.raises(LoopError, match="LOCK_FAILED"):
        with runtime.aggregate_scope(seed, root, wait_scope=frame(seed, root)):
            pytest.fail("unowned scope admitted")
    assert not (seed / "final-preparation" / "role-settings").exists()


@pytest.mark.parametrize("namespace", ["role-settings", "sources"])
def test_fresh_factory_rejects_partial_zero_before_native_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, namespace: str
) -> None:
    root, seed = original_sources(tmp_path, monkeypatch)
    (seed / "final-preparation" / namespace).mkdir(mode=0o700)

    def forbidden(**options: Any) -> None:
        raise AssertionError("native factory reached before strict admission")

    monkeypatch.setattr(runtime, "codex_role_adapter", forbidden)
    with RunnerLock(seed / "session-control"):
        with pytest.raises(
            guards.DeploymentError, match="FINAL_INITIALIZATION_SOURCE_INVALID"
        ):
            with runtime.aggregate_scope(seed, root, wait_scope=frame(seed, root)):
                pytest.fail("partial restart admitted")
    assert (
        guards.private_json(seed / "final-preparation" / "role-state.json")[
            "calls_consumed"
        ]
        == 0
    )


def test_ongoing_sources_allow_owned_namespace_but_do_not_admit_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed = original_sources(tmp_path, monkeypatch)
    with owned(seed, root) as source:
        guards.validate_final_preseed(seed, root)
        (seed / "final-preparation" / "role-settings").mkdir(mode=0o700)
        source.check()
        with pytest.raises(guards.DeploymentError):
            guards.validate_final_preseed(seed, root)
    with RunnerLock(seed / "session-control"), RunnerLock(seed):
        with pytest.raises(PhaseBlocked, match="FINAL_ROLE_UNRESOLVED"):
            source.check()


@pytest.mark.parametrize(
    "change", ["stop", "pause", "profile-loss", "owner-source-loss", "state-loss"]
)
def test_ongoing_original_drift_or_stop_never_refunds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    root, seed = original_sources(tmp_path, monkeypatch)
    with owned(seed, root) as source:
        guard = source.check
        store = Store(seed, root, source.profile, guard)
        counter = RoleCounter(store, source.config["approved_launch_digest"])
        counter.reserve(
            preparation_identity_digest="1" * 64,
            input_digest="2" * 64,
            backend_digest=digest(document()),
            started_at=150,
            deadline_at=200,
        )
        source.check()
        if change in {"stop", "pause"}:
            path = seed / "launch-control.json"
            control = guards.private_json(path)
            write(
                path, {**control, "state": "STOPPED" if change == "stop" else "PAUSED"}
            )
        else:
            path = {
                "profile-loss": seed / "final-preparation-profile.json",
                "owner-source-loss": seed
                / "final-initialization"
                / "approval-pin.json",
                "state-loss": seed / "final-preparation" / "role-state.json",
            }[change]
            path.unlink()
        with pytest.raises((guards.DeploymentError, PhaseBlocked)):
            source.check()
        if change != "state-loss":
            assert (
                guards.private_json(store.directory / "role-state.json")[
                    "calls_consumed"
                ]
                == 1
            )


def test_consumed_counter_cannot_replay_original_zero_in_continuous_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed = original_sources(tmp_path, monkeypatch)
    path = seed / "final-preparation" / "role-state.json"
    zero = path.read_bytes()
    with owned(seed, root) as source:
        store = Store(seed, root, source.profile, source.check)
        counter = RoleCounter(store, source.config["approved_launch_digest"])
        counter.reserve(
            preparation_identity_digest="1" * 64,
            input_digest="2" * 64,
            backend_digest=digest(document()),
            started_at=150,
            deadline_at=200,
        )
        source.check()
        path.write_bytes(zero)
        with pytest.raises(PhaseBlocked, match="FINAL_ROLE_CALL_SPENT"):
            source.check()


class NativeSentinel:
    """Wrapper unit transport only; this is not a native completion proof."""

    executor_command = ("synthetic", "executor")
    controller_command = ("synthetic", "controller")
    timeout_seconds = 50
    output_limit = 65536

    def __init__(self, root: Path, guard: Callable[[], None]) -> None:
        self.root, self.guard = root, guard
        self.calls: list[dict[str, Any]] = []

    def _call(self, command: Any, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        return {"synthetic_forwarded": True}


def unit_review(
    root: Path, seed: Path, source: runtime._OriginalSources
) -> tuple[AggregateReview, runtime._ReservedDelegate, NativeSentinel]:
    for args in (
        ("init", "-b", "main"),
        ("config", "user.name", "Synthetic"),
        ("config", "user.email", "synthetic@example.invalid"),
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "synthetic.py").write_text("VALUE=1\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "synthetic base"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "switch", "-c", source.profile["roadmap_ref"]],
        cwd=root,
        check=True,
        capture_output=True,
    )
    (root / "synthetic.py").write_text("VALUE=2\n")
    subprocess.run(
        ["git", "commit", "-am", "synthetic diff"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    guard = source.check
    store = Store(seed, root, source.profile, guard)
    counter = RoleCounter(store, source.config["approved_launch_digest"])
    native = NativeSentinel(root, source.check)
    delegate = runtime._ReservedDelegate(native, counter, source)
    continuation = guards.private_json(seed / "continuation-profile.json")
    adapter = SettingsAdapter(
        delegate, document(), continuation, store.directory, guard
    )
    aggregate = AggregateReview(counter, adapter)
    delegate.aggregate = aggregate
    return aggregate, delegate, native


@pytest.mark.parametrize(
    "attack",
    [
        "zero",
        "missing-reservation",
        "input",
        "backend",
        "preparation",
        "unknown",
        "completed",
        "executor",
        "argv",
        "repair",
        "expired",
        "replay",
        "none",
    ],
)
def test_actual_payload_requires_exact_reserved_authority_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    root, seed = original_sources(tmp_path, monkeypatch)
    with owned(seed, root) as source:
        aggregate, delegate, native = unit_review(root, seed, source)
        payload = {
            "repair_iteration": 0,
            "trusted_policy": {"synthetic": "policy"},
            "session_id": "synthetic",
        }
        identity = aggregate._identity()
        counter = aggregate.counter
        command = delegate.controller_command
        if attack != "zero":
            counter.reserve(
                preparation_identity_digest=digest(identity),
                input_digest=digest(payload),
                backend_digest=digest(document()),
                started_at=100,
                deadline_at=140 if attack == "expired" else 200,
            )
        if attack in {"input", "repair"}:
            payload = {
                **payload,
                "repair_iteration": 1 if attack == "repair" else 0,
                "changed": "synthetic",
            }
        elif attack in {"backend", "preparation", "unknown", "completed"}:
            path = counter.store.directory / (counter.key + ".json")
            reservation = guards.private_json(path)
            update = {
                "backend": {"backend_digest": "f" * 64},
                "preparation": {"preparation_identity_digest": "f" * 64},
                "unknown": {"status": "UNKNOWN"},
                "completed": {
                    "status": "COMPLETED",
                    "settings_digest": "f" * 64,
                    "output_digest": "e" * 64,
                },
            }[attack]
            write(path, records.seal({**reservation, **update}))
        elif attack == "missing-reservation":
            (counter.store.directory / (counter.key + ".json")).unlink()
        elif attack == "executor":
            command = delegate.executor_command
        elif attack == "argv":
            command = (*command, "--changed")
        if attack in {"none", "replay"}:
            assert delegate._call(command, payload) == {"synthetic_forwarded": True}
            assert len(native.calls) == 1
            if attack == "none":
                return
        with pytest.raises(PhaseBlocked, match="FINAL_ROLE_UNRESOLVED"):
            delegate._call(command, payload)
        assert len(native.calls) == (1 if attack == "replay" else 0)


def native_factory_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, delay_seconds: int = 0
) -> tuple[Path, Path, Path, Path]:
    """Complete synthetic policy/owner setup; real fixed adapter/native subprocess.

    No source/ownership/approval/settings check is replaced. Gate command outcomes
    are explicitly synthetic, as in existing aggregate subprocess tests.
    """
    with monkeypatch.context() as setup_patch:
        root, seed = original_sources(tmp_path, setup_patch)
    project = Path(guards.__file__).resolve().parent.parent
    for directory in ("tools", "docs/schemas"):
        destination = root / directory
        destination.mkdir(parents=True, exist_ok=True)
        for source in (project / directory).rglob("*"):
            if source.is_file() and source.suffix in {".py", ".json"}:
                target = destination / source.relative_to(project / directory)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
    for name in ("AGENTS.md", "CONTRIBUTING.md", "pyproject.toml"):
        (root / name).write_bytes((project / name).read_bytes())
    (root / "unit.py").write_text("VALUE = 1\n")

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    git("remote", "add", "origin", "https://github.com/OWNER/repository.git")
    git("add", ".")
    git("commit", "-m", "synthetic complete approved policy")
    base = git("rev-parse", "HEAD")
    installed = tmp_path / "native-policy-candidate"
    install.prepare(root, base, "OWNER/repository", installed)
    config = guards.private_json(seed / "epic-runtime.json")
    mandate = guards.private_json(seed / "mandate.json")
    git("switch", "-c", mandate["roadmap_ref"])
    (root / "unit.py").write_text("VALUE = 2\n")
    git("commit", "-am", "synthetic cumulative diff")
    head = git("rev-parse", "HEAD")
    verdict = {
        "schema_version": "1.0",
        "role": "controller",
        "task_issue": mandate["epic"],
        "base_sha": base,
        "head_sha": head,
        "review_basis": {
            "trust_source_kind": "local_read_only_bundle",
            "head_policy_applied": False,
            "executor_self_assessment_treated_as_evidence_only": True,
        },
        "verdict": "PASS",
        "repair_iteration": 0,
        "escalation_reason": None,
        "blocking_findings": [],
        "notes": [],
    }
    native = child(seed)
    script = (
        native.read_text()
        .replace(
            '"instructionSources": [], "sandbox": {"type": "readOnly"}}})',
            '"instructionSources": [], "model": "synthetic-model", "reasoningEffort": '
            '"low", "sandbox": {"type": "readOnly"}}})',
        )
        .replace('json.dumps({"synthetic": True})', "json.dumps(" + repr(verdict) + ")")
    )
    script = script.replace(
        'read("turn/start")',
        'read("turn/start")\nimport time\ntime.sleep(' + repr(delay_seconds) + ")",
    )
    native.write_text(
        "#!" + sys.executable + "\nimport sys\n"
        "if '--version' in sys.argv:\n"
        " print('codex-cli 0.157.1'); raise SystemExit(0)\n" + script
    )
    native.chmod(0o700)
    backend = document()
    backend["source"].update(
        executable=str(native),
        executable_sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
        version_digest=hashlib.sha256(b"codex-cli 0.157.1").hexdigest(),
    )
    now = int(time.time())
    mandate.update(policy_sha=base, issued_at=now - 1, expires_at=now + 900)
    mandate["approval"]["approved_at"] = now
    unsigned = {
        **mandate,
        "approval": {
            k: v for k, v in mandate["approval"].items() if k != "mandate_digest"
        },
    }
    mandate_digest = guards.canonical_digest(unsigned)
    mandate["approval"]["mandate_digest"] = mandate_digest
    write(seed / "mandate.json", mandate)
    identity = {
        k: mandate[k]
        for k in ("repository", "epic", "roadmap_ref", "policy_sha", "owner_identity")
    }
    identity["mandate_digest"] = mandate_digest
    config.update(
        policy_root=str(installed / "policy"),
        approved_mandate_digest=mandate_digest,
        started_at=now,
    )
    for name in ("wait", "learning", "continuation"):
        profile = {**guards.private_json(seed / (name + "-profile.json")), **identity}
        if name == "continuation":
            profile["backend_digest"] = digest(backend)
            profile["limits"]["max_prompt_bytes"] = 262144
        if name == "learning":
            schemas = installed / "policy/docs/schemas"
            profile["catalog_digest"] = catalog_digest(
                guards.private_json(schemas / "agent-learning-catalog.json"),
                guards.private_json(schemas / "agent-learning-planning-catalog.json"),
            )
        write(seed / (name + "-profile.json"), profile)
        config["approved_" + name + "_digest"] = guards.canonical_digest(profile)
    write(seed / "role-backend.json", backend)
    profile = records.seal(
        {**guards.private_json(seed / "final-preparation-profile.json"), **identity}
    )
    write(seed / "final-preparation-profile.json", profile)
    config["approved_final_preparation_digest"] = guards.canonical_digest(profile)
    launch = guards.private_json(seed / "launch.json")
    # Fixed final-only Session source setup BEFORE original approval/preseed sealing.
    plans = {"schema_version": "1.0", "initial_handover_digest": "1" * 64, "tasks": {}}
    write(seed / "approved-plans.json", plans)
    config["approved_plans_digest"] = guards.canonical_digest(plans)
    launch.update(
        **{
            k: mandate[k]
            for k in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        },
        mandate_digest=mandate_digest,
        installation_digest=guards.canonical_digest(
            guards.private_json(installed / "candidate.json")
        ),
        runtime_config_digest=guards.launch_config_digest(
            config, guards.private_json(seed / "runtime-adapter.json")
        ),
    )
    config["approved_launch_digest"] = guards.canonical_digest(launch)
    write(seed / "launch.json", launch)
    write(seed / "epic-runtime.json", config)
    core = {
        "repository": mandate["repository"],
        "epic": mandate["epic"],
        "mandate_digest": mandate_digest,
        "launch_digest": config["approved_launch_digest"],
    }
    zero = records.seal(
        {
            "schema_version": "1.0",
            **core,
            "calls_consumed": 0,
            "pending_reservation_digest": None,
        }
    )
    write(seed / "final-preparation/role-state.json", zero)
    owner = {**core, "owner_identity": mandate["owner_identity"]}
    references = {
        "preparation_digest": config["approved_final_preparation_digest"],
        "counter_zero_digest": digest(zero),
    }
    initialization = seed / "final-initialization"
    command = records.seal(
        {
            **guards.private_json(initialization / "command-source.json"),
            **owner,
            **references,
            "policy_sha": base,
        }
    )
    approval = seal(
        {
            **guards.private_json(initialization / "approval.json"),
            **owner,
            **references,
            "approved_at": now,
            "owner_command_digest": digest(command),
        },
        "approved_digest",
    )
    receipt = records.seal(
        {
            **guards.private_json(initialization / "install-receipt.json"),
            **owner,
            **references,
            "approved_at": now,
            "owner_command_digest": digest(command),
            "initialization_digest": digest(approval),
        }
    )
    pin = records.seal(
        {
            **guards.private_json(initialization / "approval-pin.json"),
            **owner,
            "initialization_digest": digest(approval),
            "installation_receipt_digest": digest(receipt),
        }
    )
    for name, value in (
        ("command-source", command),
        ("approval", approval),
        ("install-receipt", receipt),
        ("approval-pin", pin),
    ):
        write(initialization / (name + ".json"), value)
    write(
        seed / "launch-control.json",
        {
            "schema_version": "1.0",
            "launch_digest": core["launch_digest"],
            "state": "RUNNING",
            "generation": 0,
        },
    )
    checkpoint = _checkpoint()
    checkpoint.update(
        repository=mandate["repository"],
        epic=mandate["epic"],
        policy_sha=base,
        base_ref=mandate["roadmap_ref"],
        base_sha=base,
        head_sha=head,
        schema_version="3.0",
        phase="NEXT_TASK",
        status="TASK_DONE",
        merge_sha=head,
        completed_phases=list(PHASES[1:]),
    )
    CheckpointStore(seed, root).save(checkpoint)
    write(
        seed / "session-control/cursor.json",
        {
            "repository": mandate["repository"],
            "epic": mandate["epic"],
            "policy_sha": base,
            "schema_version": "1.0",
            "seed_task": checkpoint["task"],
            "current_task": checkpoint["task"],
            "mode": "FINAL",
            "completed_tasks": [checkpoint["task"]],
            "pending_task": None,
        },
    )
    codex_home = tmp_path / "empty-synthetic-codex-home"
    codex_home.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    driver = seed / "composition-driver.py"
    driver.write_text(NATIVE_DRIVER)
    return root, seed, installed / "policy", driver


NATIVE_DRIVER = """

import json, sys, time
from contextlib import nullcontext
from pathlib import Path

sys.dont_write_bytecode = True
policy, root, seed = map(Path, sys.argv[1:4])
mode = sys.argv[4]
sys.path.insert(0, str(policy))
from tools import agent_gate
from tools import agent_final_preparation_runtime as runtime
from tools import agent_final_preparation_storage as storage
from tools import agent_continuation_backend as backend
from tools import agent_continuation_store as native_store
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_wait_scope import Scope


class SyntheticFault(Exception):
    pass


# Gate command results are synthetic; snapshot and all native/original sources are real.
outputs = {
    "pytest": "1 passed in 0.01s",
    "ruff": "All checks passed!",
    "mypy": "Success: no issues found in 1 source file",
    "pre-commit": (
        "Detect secrets....................Passed\\n"
        "trim trailing whitespace..........Passed\\n"
    ),
}


def gate_runner(argv, cwd, timeout_seconds):
    return agent_gate.CommandOutcome("passed", 0, outputs.get(argv[0], ""))


agent_gate.verify_environment = lambda session: None
agent_gate._default_runner = gate_runner
if mode in {"consumed", "reservation", "completion"}:
    previous = storage.Store.write

    def write(self, key, value):
        previous(self, key, value)
        if (
            (
                mode == "consumed"
                and key == "role-state"
                and value["calls_consumed"] == 1
            )
            or (
                mode == "reservation"
                and key.startswith("reservation-")
                and value["status"] == "RESERVED"
            )
            or (
                mode == "completion"
                and key.startswith("reservation-")
                and value["status"] == "COMPLETED"
            )
        ):
            raise SyntheticFault(mode)

    storage.Store.write = write
if mode == "constructor":
    previous = backend.SettingsAdapter.__init__

    def construct(self, delegate, owner, profile, state, guard):
        previous(self, delegate, owner, profile, state, guard)
        if state == seed / "final-preparation":
            raise SyntheticFault(mode)

    backend.SettingsAdapter.__init__ = construct
if mode == "native-receipt":
    previous = backend.SettingsStore.save

    def save(self, invocation, value):
        previous(self, invocation, value)
        if self.store.directory == seed / "final-preparation/role-settings":
            raise SyntheticFault(mode)

    backend.SettingsStore.save = save
if mode == "cache":
    previous = native_store.Store.save

    def save(self, key, value, **limits):
        previous(self, key, value, **limits)
        if self.directory == seed / "final-preparation/sources":
            raise SyntheticFault(mode)

    native_store.Store.save = save
if mode == "reuse":

    def forbidden(*args, **kwargs):
        raise AssertionError("second native role attempted")

    runtime._ReservedDelegate._call = forbidden
if mode == "late-completion":
    previous = runtime.AggregateReview._verify

    def verify(self, raw, identity):
        _, reservation = self.counter.read()
        time.sleep(max(0, reservation["deadline_at"] - time.time()) + 0.05)
        return previous(self, raw, identity)

    runtime.AggregateReview._verify = verify
if mode == "late-publication":
    previous = runtime._PreparationStore._replace

    def replace(self, temporary, path):
        if self._completion_deadline is not None:
            original_guard = self.guard

            def delayed_guard():
                original_guard()
                time.sleep(max(0, self._completion_deadline - time.time()) + 0.05)

            self.guard = delayed_guard
            try:
                return previous(self, temporary, path)
            finally:
                self.guard = original_guard
        return previous(self, temporary, path)

    runtime._PreparationStore._replace = replace
native_started = []


def observe(event, args):
    if event == "subprocess.Popen":
        argv = args[1]
        if (
            isinstance(argv, (list, tuple))
            and "--role" in argv
            and "controller" in argv
        ):
            native_started.append(time.monotonic())
            if mode in {"cursor-drift", "checkpoint-drift"}:
                target = seed / (
                    "session-control/cursor.json"
                    if mode == "cursor-drift"
                    else "epic-loop-checkpoint.json"
                )
                value = json.loads(target.read_text())
                value["mode" if mode == "cursor-drift" else "status"] = (
                    "DONE" if mode == "cursor-drift" else "BLOCKED"
                )
                target.write_text(json.dumps(value))


sys.addaudithook(observe)
if mode == "iterations-exhausted":
    target = seed / "session-control/cursor.json"
    value = json.loads(target.read_text())
    value["completed_tasks"] = [value["current_task"], *range(1000, 1011)]
    target.write_text(json.dumps(value))
try:
    with RunnerLock(seed / "session-control"):
        scope = Scope.for_root(seed, root)
        assert scope is not None
        with (
            nullcontext()
            if mode == "inactive-stage"
            else scope.context("FINAL:FINAL_GATE")
        ):
            with runtime.aggregate_scope(seed, root, wait_scope=scope) as prepared:
                if mode == "reader-loss":
                    scope.reader("FINAL:FINAL_GATE").guard = lambda: None
                now = int(time.time())
                result = prepared.review(
                    started_at=now,
                    deadline_at=now
                    + (
                        3
                        if mode in {"deadline", "late-completion", "late-publication"}
                        else 15 if mode == "short-run" else 60
                    ),
                )
    print(json.dumps({"status": "COMPLETED", "verdict": result["output"]["verdict"]}))
except SyntheticFault:
    print(json.dumps({"status": "FAULT", "point": mode}))
except Exception as error:
    report = {"status": "BLOCKED", "code": str(error), "type": type(error).__name__}
    if mode in {
        "deadline",
        "late-completion",
        "late-publication",
        "cursor-drift",
        "checkpoint-drift",
        "reader-loss",
        "inactive-stage",
        "iterations-exhausted",
    }:
        report["native_started"] = len(native_started)
        report["native_elapsed"] = (
            time.monotonic() - native_started[0] if native_started else None
        )
    print(json.dumps(report))
"""


def run_native_driver(
    root: Path, seed: Path, policy: Path, driver: Path, mode: str
) -> dict[str, Any]:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(driver),
            str(policy),
            str(root),
            str(seed),
            mode,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.usefixtures("active_runtime_namespace")
def test_real_fixed_factory_completes_and_reuses_original_native_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed, policy, driver = native_factory_fixture(tmp_path, monkeypatch)
    assert run_native_driver(root, seed, policy, driver, "run") == {
        "status": "COMPLETED",
        "verdict": "PASS",
    }
    state = guards.validate_final_preseed(seed, root)
    assert state["calls_consumed"] == 1
    receipts = list((seed / "final-preparation/role-settings").glob("receipt-*.json"))
    assert len(receipts) == 1
    receipt = guards.private_json(receipts[0])
    assert receipt["receipt"]["effective"] == {
        "model": "synthetic-model",
        "reasoning": "low",
    }
    assert (seed / "role-settings").is_dir()  # Existing configured factory namespace.
    assert run_native_driver(root, seed, policy, driver, "reuse") == {
        "status": "COMPLETED",
        "verdict": "PASS",
    }
    assert guards.validate_final_preseed(seed, root) == state
    assert (
        len(list((seed / "final-preparation/role-settings").glob("receipt-*.json")))
        == 1
    )


@pytest.mark.usefixtures("active_runtime_namespace")
def test_real_completed_cache_survives_original_reservation_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed, policy, driver = native_factory_fixture(tmp_path, monkeypatch)
    assert run_native_driver(root, seed, policy, driver, "short-run") == {
        "status": "COMPLETED",
        "verdict": "PASS",
    }
    (reservation_path,) = (seed / "final-preparation").glob("reservation-*.json")
    reservation = guards.private_json(reservation_path)
    assert reservation["status"] == "COMPLETED"
    before = {
        path: path.read_bytes() for path in (seed / "final-preparation").rglob("*.json")
    }
    state = guards.validate_final_preseed(seed, root)
    remaining = reservation["deadline_at"] - time.time()
    assert remaining < 16
    time.sleep(max(0, remaining) + 0.1)
    assert time.time() > reservation["deadline_at"]
    assert run_native_driver(root, seed, policy, driver, "reuse") == {
        "status": "COMPLETED",
        "verdict": "PASS",
    }
    assert guards.validate_final_preseed(seed, root) == state
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.usefixtures("active_runtime_namespace")
@pytest.mark.parametrize(
    "point",
    ["constructor", "consumed", "reservation", "native-receipt", "cache", "completion"],
)
def test_real_fixed_factory_fault_boundaries_never_refund_or_repeat_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    root, seed, policy, driver = native_factory_fixture(tmp_path, monkeypatch)
    assert run_native_driver(root, seed, policy, driver, point) == {
        "status": "FAULT",
        "point": point,
    }
    path = seed / "final-preparation/role-state.json"
    original_counter = path.read_bytes()
    assert guards.private_json(path)["calls_consumed"] == (
        0 if point == "constructor" else 1
    )
    if point == "completion":
        assert guards.validate_final_preseed(seed, root)["calls_consumed"] == 1
        assert (
            run_native_driver(root, seed, policy, driver, "reuse")["status"]
            == "COMPLETED"
        )
    else:
        with pytest.raises(
            guards.DeploymentError,
            match=(
                "FINAL_INITIALIZATION_NATIVE_CHAIN_REQUIRED"
                if point in {"reservation", "native-receipt", "cache"}
                else "FINAL_INITIALIZATION_SOURCE_INVALID"
            ),
        ):
            guards.validate_final_preseed(seed, root)
        assert (
            run_native_driver(root, seed, policy, driver, "reuse")["status"]
            == "BLOCKED"
        )
    assert path.read_bytes() == original_counter


@pytest.mark.usefixtures("active_runtime_namespace")
@pytest.mark.parametrize("mode", ["deadline", "late-completion", "late-publication"])
def test_native_deadline_and_late_completion_cannot_create_successful_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    root, seed, policy, driver = native_factory_fixture(
        tmp_path, monkeypatch, delay_seconds=10 if mode == "deadline" else 0
    )
    result = run_native_driver(root, seed, policy, driver, mode)
    assert result["status"] == "BLOCKED", result
    assert result["code"] == "FINAL_ROLE_UNRESOLVED"
    assert result["native_started"] == 1
    if mode == "deadline":
        assert result["native_elapsed"] < 10
        assert not list(
            (seed / "final-preparation/role-settings").glob("receipt-*.json")
        )
    assert (
        guards.private_json(seed / "final-preparation/role-state.json")[
            "calls_consumed"
        ]
        == 1
    )
    with pytest.raises(
        guards.DeploymentError, match="FINAL_INITIALIZATION_NATIVE_CHAIN_REQUIRED"
    ):
        guards.validate_final_preseed(seed, root)
    assert run_native_driver(root, seed, policy, driver, "reuse")["status"] == "BLOCKED"


@pytest.mark.usefixtures("active_runtime_namespace")
@pytest.mark.parametrize(
    "mode",
    [
        "cursor-drift",
        "checkpoint-drift",
        "reader-loss",
        "inactive-stage",
        "iterations-exhausted",
    ],
)
def test_actual_final_stage_and_dynamic_authority_remain_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    root, seed, policy, driver = native_factory_fixture(tmp_path, monkeypatch)
    result = run_native_driver(root, seed, policy, driver, mode)
    assert result["status"] == "BLOCKED", result
    if mode == "iterations-exhausted":
        assert result["code"] == "MANDATE_TASK_LIMIT"
    elif mode == "inactive-stage":
        assert result["code"] == "WAIT_FRAME_REQUIRED"
    elif mode in {"reader-loss", "cursor-drift"}:
        assert result["code"] == "SESSION_SCOPE_CHANGED"
    else:
        assert result["code"] == "CHECKPOINT_INVALID"
    expected = 1 if mode in {"cursor-drift", "checkpoint-drift"} else 0
    assert result["native_started"] == expected
    state = guards.private_json(seed / "final-preparation/role-state.json")
    assert state["calls_consumed"] == expected
    assert not list((seed / "final-preparation/role-settings").glob("receipt-*.json"))


@pytest.mark.parametrize("failure", [False, True])
def test_native_guard_and_timeout_restore_after_reserved_forwarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    root, seed = original_sources(tmp_path, monkeypatch)
    with owned(seed, root) as source:
        aggregate, delegate, native = unit_review(root, seed, source)
        payload = {
            "repair_iteration": 0,
            "trusted_policy": {"synthetic": "policy"},
            "session_id": "synthetic",
        }
        aggregate.counter.reserve(
            preparation_identity_digest=digest(aggregate._identity()),
            input_digest=digest(payload),
            backend_digest=digest(document()),
            started_at=150,
            deadline_at=175,
        )
        original_guard, original_timeout = native.guard, native.timeout_seconds
        observed: list[float] = []

        def transport(command: Any, proposed: dict[str, Any]) -> dict[str, Any]:
            observed.append(native.timeout_seconds)
            native.guard()
            if failure:
                raise RuntimeError("synthetic transport fault")
            return {"synthetic_forwarded": True}

        monkeypatch.setattr(native, "_call", transport)
        if failure:
            with pytest.raises(RuntimeError, match="synthetic transport fault"):
                delegate._call(delegate.controller_command, payload)
        else:
            assert delegate._call(delegate.controller_command, payload) == {
                "synthetic_forwarded": True
            }
        assert observed and 0 < observed[0] <= 25
        assert native.guard is original_guard
        assert native.timeout_seconds == original_timeout
        with pytest.raises(PhaseBlocked, match="FINAL_ROLE_UNRESOLVED"):
            delegate._call(delegate.controller_command, payload)
        state, _ = aggregate.counter.read()
        assert state is not None and state["calls_consumed"] == 1


def test_configured_aggregate_retains_original_bound_guard_and_live_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed = original_sources(tmp_path, monkeypatch)
    with owned(seed, root) as source:
        retained = source.check
        store = runtime._PreparationStore(seed, root, source.profile, retained)
        counter = RoleCounter(store, source.config["approved_launch_digest"])
        native = NativeSentinel(root, retained)
        configured = SettingsAdapter(
            native, source.backend, source.continuation, seed, retained
        )
        factory_calls: list[dict[str, Any]] = []

        def factory(**kwargs: Any) -> SettingsAdapter:
            assert kwargs == {
                "policy_root": Path(source.config["policy_root"]),
                "root": root, "policy_sha": source.profile["policy_sha"],
                "continuation_state": seed, "quota_state": None,
                "wait_scope": source.wait_scope,
            }
            factory_calls.append(kwargs)
            return configured

        # Only the external settings-factory seam is substituted. Source/lock/
        # store/adapter/counter guards and AggregateReview validation stay real.
        monkeypatch.setattr(runtime, "codex_role_adapter", factory)
        aggregate = runtime._configured_aggregate(
            seed, root, source.wait_scope, source, store, counter
        )
        assert len(factory_calls) == 1
        assert type(aggregate.adapter) is SettingsAdapter
        assert aggregate.counter is counter
        assert aggregate.adapter.guard is store.guard is retained
        assert getattr(retained, "__self__") is source
        assert getattr(retained, "__func__") is runtime._OriginalSources.check
        assert aggregate.adapter.root == store.root == root
        assert aggregate.adapter.state == store.directory
        assert aggregate.adapter.store.store.directory == (
            store.directory / "role-settings"
        )
        aggregate.adapter.guard()
        store.assert_owned()
        state, reservation = counter.read()
        assert state is not None and state["calls_consumed"] == 0
        assert reservation is None and native.calls == []
        control = guards.private_json(seed / "launch-control.json")
        write(seed / "launch-control.json", {**control, "state": "STOPPED"})
        with pytest.raises((guards.DeploymentError, PhaseBlocked)):
            aggregate.adapter.guard()
        assert native.calls == []


def test_aggregate_rejects_equal_but_distinct_bound_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed = original_sources(tmp_path, monkeypatch)
    with owned(seed, root) as source:
        retained = source.check
        store = runtime._PreparationStore(seed, root, source.profile, retained)
        counter = RoleCounter(store, source.config["approved_launch_digest"])
        distinct = source.check
        assert distinct is not retained
        assert getattr(distinct, "__self__") is getattr(retained, "__self__")
        assert getattr(distinct, "__func__") is getattr(retained, "__func__")
        adapter = SettingsAdapter(
            NativeSentinel(root, retained), source.backend, source.continuation,
            store.directory, distinct,
        )
        # Satisfy every other constructor predicate; retain identity rejection.
        assert type(adapter) is SettingsAdapter
        assert adapter.root == counter.store.root
        assert adapter.state == counter.store.directory
        assert adapter.store.store.directory == (
            counter.store.directory / "role-settings"
        )
        with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
            AggregateReview(counter, adapter)


@pytest.mark.parametrize("case", ["learning", "forged_learning", "legacy"])
def test_completed_reader_request_variant_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Real entry routing/request comparison; source/settings envelopes doubled.

    Reaching settings lookup proves request/policy comparison, not native or
    installed-source admission. The sole ordinary genuine supplies that proof.
    """
    from types import SimpleNamespace

    from tools import agent_epic_runtime_cli as current_guards
    from tools import agent_final_preparation_review as review_module
    from tools import agent_gate as current_gate
    from tools import agent_orchestrate as workflow

    class SettingsLookupReached(Exception):
        pass

    identity = {
        "epic": 294, "main_sha": "a" * 40, "roadmap_sha": "b" * 40,
        "tree_sha": "c" * 40, "policy_sha": "a" * 40,
    }
    gate = {"snapshot": {"tree_hash": "c" * 40, "snapshot_commit": "b" * 40}}
    contract = workflow.TaskContract(
        issue=294, epic=294, acceptance_criteria=("synthetic review",),
        base_ref="main", base_sha="a" * 40, head_ref="roadmap/294-unit",
        allowed_paths=("unit.py",), max_repair_iterations=0, policy_sha="a" * 40,
    )
    counter = SimpleNamespace(store=SimpleNamespace(root=tmp_path))
    source = SimpleNamespace(
        config={
            "schema_version": "7.0" if case == "legacy" else "8.0",
            "approved_launch_digest": "d" * 64,
        },
        profile={}, check=lambda: None,
    )
    session = SimpleNamespace(state=tmp_path / "state", root=tmp_path)
    monkeypatch.setattr(runtime, "_OwnedCompletedSources", lambda actual: source)
    monkeypatch.setattr(current_guards, "secure_directory", lambda *args: None)
    monkeypatch.setattr(
        runtime, "_ReadOnlyPreparationStore", lambda *args: counter.store,
    )
    monkeypatch.setattr(runtime, "RoleCounter", lambda *args: counter)
    monkeypatch.setattr(
        current_gate, "gate_evidence_is_passing", lambda value: True,
    )
    monkeypatch.setattr(
        workflow, "_load_trusted_policy", lambda *args: {"unit": "policy"},
    )
    monkeypatch.setattr(
        review_module.AggregateReview, "_contract", lambda *args: contract,
    )
    monkeypatch.setattr(
        review_module.AggregateReview, "_git",
        lambda *args: "synthetic cumulative diff",
    )

    def initialize(reader: Any, actual_counter: Any, actual_source: Any) -> None:
        reader.counter, reader.sources = actual_counter, actual_source

    monkeypatch.setattr(runtime._CompletedReview, "__init__", initialize)
    producer_type = (
        review_module.AggregateReview if case == "legacy"
        else review_module._LearningAggregateReview
    )
    producer: Any = object.__new__(producer_type)
    producer.counter = counter
    producer._learning_source_digest = "e" * 64
    request, policy, _contract = producer._request(identity, gate)
    original_request = dict(request)
    if case == "forged_learning":
        request["learning_source_digest"] = "f" * 64
    retained = {
        "identity": identity, "request": request, "policy": policy,
        "session_id": "synthetic-session", "output": {}, "settings_source": {},
    }
    original_invocation = digest({
        **original_request, "trusted_policy": policy, "session_id": "synthetic-session",
    })
    observed: list[type[Any]] = []

    def settings(reader: Any, invocation: str) -> None:
        if invocation != original_invocation:
            return None
        raise SettingsLookupReached

    def completed(reader: Any) -> dict[str, Any]:
        observed.append(type(reader))
        if case == "forged_learning":
            with pytest.raises(PhaseBlocked, match="^FINAL_REVIEW_SETTINGS_MISSING$"):
                reader._validate(retained, identity)
        else:
            with pytest.raises(SettingsLookupReached):
                reader._validate(retained, identity)
        return retained

    monkeypatch.setattr(runtime._CompletedReview, "_settings_original", settings)
    monkeypatch.setattr(runtime._CompletedReview, "completed", completed)
    monkeypatch.setattr(runtime._CompletedReview, "_identity", lambda reader: identity)
    monkeypatch.setattr(
        runtime._CompletedReview, "_environment", lambda reader, record, actual: record,
    )
    assert runtime.read_completed_aggregate_owned(session) is retained
    assert runtime.read_completed_environment_owned(session) is retained
    expected = (
        runtime._CompletedReview
        if case == "legacy" else runtime._CompletedLearningReview
    )
    assert observed == [expected, expected]


@pytest.mark.parametrize("case", [
    "valid_entry_exit_and_transport_full", "entry_policy_failure_before_collect",
    "exit_policy_drift_blocks_publication", "wrong_session_binding_refused",
    "root_binding_changed_refused", "policy_sha_changed_refused",
    "body_exception_cleanup_preserves_primary", "current_reader_default_full",
    "preaggregate_reader_default_full", "legacy_reader_default_full",
    "outside_scope_default_full", "nested_scope_refused",
])
def test_packet_policy_proof_cadence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Actual wrapper/guard/policy route; installed source envelope is doubled."""
    from types import SimpleNamespace

    from tools import agent_coordinator_handover as handover
    from tools import agent_final_learning_collector as collector
    from tools import agent_final_learning_domain as domain
    from tools import agent_learning as core
    from tools import agent_learning_evidence as evidence
    from tools import agent_learning_planning_catalog as planning_catalog
    from tools.agent_epic_session_runtime import SessionRuntime
    from tools.agent_epic_wait_scope import Scope

    root, policy, seed = tmp_path / "root", tmp_path / "policy", tmp_path / "seed"
    root.mkdir()
    policy.mkdir()
    seed.mkdir()
    marker = policy / "unit"
    marker.write_bytes(b"original")
    config = {"schema_version": "8.0"}
    mandate = {"policy_sha": "a" * 40}
    owner = {"repository": "OWNER/repo", "catalog_digest": "unit"}
    counter = {"full": 0, "local": 0, "transport": 0, "collect": 0}

    def full(*args: Any, **kwargs: Any) -> None:
        counter["full"] += 1
        if marker.read_bytes() != b"original":
            raise PhaseBlocked("POLICY_CHANGED")

    def local(*args: Any, **kwargs: Any) -> None:
        counter["local"] += 1

    def read_json(path: Path) -> dict[str, Any]:
        if path.name == "epic-runtime.json":
            return dict(config)
        if path.name == "mandate.json":
            return dict(mandate)
        return {}

    monkeypatch.setattr(guards, "learning_private_json", read_json)
    monkeypatch.setattr(guards, "bootstrap_authority", local)
    monkeypatch.setattr(guards, "managed_launch_guard", local)
    monkeypatch.setattr(guards, "verify_policy_config8_batch", full)
    monkeypatch.setattr(guards, "verify_policy", full)
    monkeypatch.setattr(guards, "learning_profile_guard", lambda *a: owner)
    monkeypatch.setattr(core, "profile", lambda value: value)
    monkeypatch.setattr(core, "catalog", lambda value: value)
    monkeypatch.setattr(planning_catalog, "catalog", lambda value: value)
    monkeypatch.setattr(planning_catalog, "catalog_digest", lambda *a: "unit")
    monkeypatch.setattr(
        handover, "_repository_identity", lambda path: "OWNER/repo",
    )
    session: Any = object.__new__(SessionRuntime)
    scope: Any = object.__new__(Scope)
    frame_reader = SimpleNamespace(guard=lambda: None)
    stage = "FINAL:FINAL_GATE"
    scope._context_stack = [stage]
    scope._readers = {stage: frame_reader}
    session.wait_scope, session.root, session.state = scope, root, seed
    session.config = config

    def transport(consumer: Any, argv: Any) -> str:
        counter["transport"] += 1
        return "{}"

    session._historical_read = transport
    reader_type: Any = domain._FinalSourceReader
    if case == "current_reader_default_full":
        reader_type = domain._CurrentFinalSourceReader
    elif case == "preaggregate_reader_default_full":
        reader_type = domain._PreAggregateFinalSourceReader
    reader: Any = object.__new__(reader_type)
    reader.seed, reader.root, reader.policy = seed, root, policy
    reader.config, reader.mandate = config, mandate
    reader.owner, reader.catalog, reader.planning_catalog = {}, {}, {}
    reader._session = reader._historical_session = session
    reader._alive = True
    historical = SimpleNamespace(reader=reader)
    monkeypatch.setattr(collector, "_FinalPlanningReader", lambda actual: historical)

    def collect(actual: Any, actual_session: Any) -> dict[str, bool]:
        counter["collect"] += 1
        assert actual is historical and actual_session is session
        evidence.SourceReader.guard(reader)
        if case == "wrong_session_binding_refused":
            reader._session = object()
        elif case == "root_binding_changed_refused":
            reader.root = tmp_path / "foreign"
        elif case == "policy_sha_changed_refused":
            mandate["policy_sha"] = "b" * 40
        if case == "nested_scope_refused":
            with reader._packet_policy_proof(session):
                raise AssertionError("nested scope accepted")
        evidence.SourceReader.guard(reader)
        evidence.SourceReader.guard(reader)
        if case == "exit_policy_drift_blocks_publication":
            marker.write_bytes(b"changed")
        if case == "body_exception_cleanup_preserves_primary":
            marker.write_bytes(b"changed")
            raise LookupError("synthetic primary")
        if case == "valid_entry_exit_and_transport_full":
            # One coherent packet: eight local checks, with one before each read.
            for _ in range(3):
                evidence.SourceReader.guard(reader)
            evidence.SourceReader.guard(reader)
            reader._historical_read(("gh", "api", "repos/OWNER/repo/unit"))
            evidence.SourceReader.guard(reader)
            reader._historical_read(("gh", "api", "repos/OWNER/repo/unit"))
        return {"packet": True}

    monkeypatch.setattr(collector, "_collect", collect)
    if case in {"current_reader_default_full", "preaggregate_reader_default_full",
                "legacy_reader_default_full", "outside_scope_default_full"}:
        if case == "legacy_reader_default_full":
            config["schema_version"] = "7.0"
        evidence.SourceReader.guard(reader)
        evidence.SourceReader.guard(reader)
        assert counter["full"] == 2 and counter["local"] == 4
        return
    if case == "entry_policy_failure_before_collect":
        marker.write_bytes(b"changed")
    if case == "body_exception_cleanup_preserves_primary":
        with pytest.raises(LookupError, match="^synthetic primary$"):
            collector.read_learning_packet_owned(session)
    elif case != "valid_entry_exit_and_transport_full":
        with pytest.raises(PhaseBlocked):
            collector.read_learning_packet_owned(session)
    else:
        assert collector.read_learning_packet_owned(session) == {"packet": True}
        assert counter["full"] == 4  # ENTRY, two dispatch proofs, EXIT; old gives8.
        assert counter["transport"] == 2 and counter["local"] == 16
    assert getattr(reader, "_packet_policy_active", None) is None
    if case == "entry_policy_failure_before_collect":
        assert counter["collect"] == 0
