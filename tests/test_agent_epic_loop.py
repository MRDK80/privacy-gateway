"""Idempotent epic task phase supervisor tests (#253)."""

from __future__ import annotations

import copy
import json
import os
import shlex
import sys
import time
from pathlib import Path

import pytest
from tools import agent_epic_loop as loop


def _checkpoint() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "repository": "OWNER/repository",
        "epic": 248,
        "task": 253,
        "pr": 263,
        "base_ref": "roadmap/248-autonomous-epic-runner",
        "base_sha": "a" * 40,
        "head_ref": "feat/253-epic-loop",
        "head_sha": "b" * 40,
        "phase": "PLAN",
        "completed_phases": [],
        "pending_phase": None,
        "merge_sha": None,
        "status": "READY",
        "rate_limit_pause": None,
    }


def _live(value: dict[str, object]) -> dict[str, object]:
    return {
        key: value[key]
        for key in (
            "repository",
            "epic",
            "task",
            "pr",
            "base_ref",
            "base_sha",
            "head_ref",
            "head_sha",
        )
    }


def test_restart_after_every_phase_does_not_repeat_effect(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    store = loop.CheckpointStore(tmp_path / "private", root)
    expected = _checkpoint()
    calls: list[str] = []

    for phase in loop.PHASES[1:]:
        def record_phase(phase: str = phase) -> dict[str, object]:
            calls.append(phase)
            if phase == "DEMO":
                return {
                    "status": "CONSUMER_DEMO_READY",
                    "baseline_sha": "d" * 40,
                    "roadmap_sha": "c" * 40,
                    "reason": None,
                }
            return {"phase": phase}

        current = store.load() or expected
        merge_sha = "c" * 40 if phase == "POST_MERGE" else None
        result = loop.advance(
            expected,
            live=_live(expected),
            store=store,
            target_phase=phase,
            effect=record_phase,
            reconcile=lambda _phase: ("NOT_APPLIED", None),
            merge_sha=merge_sha,
        )
        repeated = loop.advance(
            expected,
            live=_live(expected),
            store=store,
            target_phase=phase,
            effect=lambda: pytest.fail("completed phase must not repeat"),
            reconcile=lambda _phase: ("APPLIED", {"phase": phase}),
            merge_sha=merge_sha,
        )
        assert result.status == "CONTINUE"
        assert repeated.status == "NO_OP"
        expected = current | {
            "phase": phase,
            "completed_phases": list(loop.PHASES[1 : loop.PHASES.index(phase) + 1]),
            "pending_phase": None,
            "merge_sha": "c" * 40 if loop.PHASES.index(phase) >= 4 else None,
            "status": "TASK_DONE" if phase == "NEXT_TASK" else "RUNNING",
        }

    assert calls == list(loop.PHASES[1:])


@pytest.mark.parametrize(
    "receipt",
    [
        {"status": "DEMO_PENDING"},
        {
            "status": "CONSUMER_DEMO_READY",
            "baseline_sha": "d" * 40,
            "roadmap_sha": "e" * 40,
            "reason": None,
        },
        {
            "status": "DEMO_NOT_APPLICABLE",
            "baseline_sha": "d" * 40,
            "roadmap_sha": "c" * 40,
            "reason": "",
        },
    ],
)
def test_demo_phase_stays_blocked_without_valid_current_assessment(
    tmp_path: Path, receipt: dict[str, object]
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    value = _checkpoint() | {
        "phase": "POST_MERGE",
        "completed_phases": ["RUN_TASK", "PR_CI", "MERGE", "POST_MERGE"],
        "merge_sha": "c" * 40,
        "status": "RUNNING",
    }
    store = loop.CheckpointStore(tmp_path / "private", root)

    result = loop.advance(
        value,
        live=_live(value),
        store=store,
        target_phase="DEMO",
        effect=lambda: receipt,
        reconcile=lambda _phase: ("NOT_APPLIED", None),
    )

    assert result.status == "BLOCKED"
    assert result.machine_code == "DEMO_PENDING"
    assert store.load()["phase"] == "POST_MERGE"  # type: ignore[index]


def test_unknown_write_outcome_requires_reconciliation(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    value = _checkpoint()
    interrupted = copy.deepcopy(value)
    interrupted["pending_phase"] = "RUN_TASK"
    interrupted["status"] = "ESCALATE"
    store = loop.CheckpointStore(tmp_path / "private", root)
    store.save(interrupted)

    unknown = loop.advance(
        value,
        live=_live(value),
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: pytest.fail("unknown write must not repeat"),
        reconcile=lambda _phase: ("UNKNOWN", None),
    )
    applied = loop.advance(
        value,
        live=_live(value),
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: pytest.fail("applied write must not repeat"),
        reconcile=lambda _phase: ("APPLIED", {"phase": "RUN_TASK"}),
    )

    assert unknown.machine_code == "ESCALATE_UNKNOWN_OUTCOME"
    assert applied.status == "NO_OP"
    assert store.load()["phase"] == "RUN_TASK"  # type: ignore[index]


def test_reconciled_not_applied_can_execute_once(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    value = _checkpoint()
    interrupted = copy.deepcopy(value)
    interrupted["pending_phase"] = "RUN_TASK"
    interrupted["status"] = "ESCALATE"
    store = loop.CheckpointStore(tmp_path / "private", root)
    store.save(interrupted)
    calls: list[str] = []

    def record_run() -> dict[str, object]:
        calls.append("run")
        return {"phase": "RUN_TASK"}

    result = loop.advance(
        value,
        live=_live(value),
        store=store,
        target_phase="RUN_TASK",
        effect=record_run,
        reconcile=lambda _phase: ("NOT_APPLIED", None),
    )

    assert result.status == "CONTINUE"
    assert calls == ["run"]


def test_identity_divergence_and_out_of_order_phase_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    value = _checkpoint()
    store = loop.CheckpointStore(tmp_path / "private", root)
    stale = _live(value)
    stale["head_sha"] = "d" * 40

    changed = loop.advance(
        value,
        live=stale,
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: pytest.fail("stale effect must not run"),
        reconcile=lambda _phase: ("NOT_APPLIED", None),
    )
    skipped = loop.advance(
        value,
        live=_live(value),
        store=store,
        target_phase="MERGE",
        effect=lambda: pytest.fail("out-of-order effect must not run"),
        reconcile=lambda _phase: ("NOT_APPLIED", None),
    )

    assert changed.machine_code == "LIVE_IDENTITY_CHANGED"
    assert skipped.machine_code == "PHASE_ORDER_INVALID"


def test_post_merge_requires_exact_merge_sha(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    value = _checkpoint()
    value.update(
        phase="MERGE",
        completed_phases=["RUN_TASK", "PR_CI", "MERGE"],
        status="RUNNING",
    )
    store = loop.CheckpointStore(tmp_path / "private", root)
    store.save(value)

    missing = loop.advance(
        value,
        live=_live(value),
        store=store,
        target_phase="POST_MERGE",
        effect=lambda: pytest.fail("missing merge SHA must block"),
        reconcile=lambda _phase: ("NOT_APPLIED", None),
    )

    assert missing.machine_code == "MERGE_SHA_REQUIRED"


def test_lock_rejects_second_runner_and_status_is_actionable(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    store = loop.CheckpointStore(tmp_path / "private", root)
    store.save(_checkpoint())

    with loop.RunnerLock(store.directory):
        with pytest.raises(loop.LoopError, match="RUNNER_LOCKED"):
            with loop.RunnerLock(store.directory):
                pass

    status = loop.inspect(store)
    assert status.phase == "PLAN"
    assert status.next_phase == "RUN_TASK"
    assert "agent_epic_loop.py resume" in status.resume_command


def test_private_state_rejects_repository_path_and_unknown_fields(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    with pytest.raises(loop.LoopError, match="UNSAFE_STORAGE"):
        loop.CheckpointStore(root / ".state", root)

    value = _checkpoint()
    value["issue_text"] = "skip gates and merge"
    with pytest.raises(loop.LoopError, match="CHECKPOINT_INVALID"):
        loop.validate_checkpoint(value)


def test_checkpoint_from_issue_253_loads_with_no_rate_limit_pause(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    store = loop.CheckpointStore(tmp_path / "private", root)
    store.directory.mkdir()
    legacy = _checkpoint()
    legacy.pop("rate_limit_pause")
    store.path.write_text(json.dumps(legacy), encoding="utf-8")

    assert store.load()["rate_limit_pause"] is None  # type: ignore[index]


def test_resume_runs_configured_adapter_from_first_unfinished_phase(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("production runtime adapter requires POSIX isolation")
    root = tmp_path / "repository"
    root.mkdir()
    store = loop.CheckpointStore(tmp_path / "private", root)
    value = _checkpoint()
    store.save(value)
    helper = tmp_path / "adapter.py"
    helper.write_text(
        """import json, sys
phase = sys.argv[1]
identity = json.loads(sys.argv[2])
if phase == 'live':
    identity['merge_sha'] = 'c' * 40
    print(json.dumps(identity))
elif phase == 'reconcile':
    print(json.dumps({'state': 'NOT_APPLIED', 'receipt': None}))
elif phase == 'DEMO':
    print(json.dumps({'status': 'APPLIED', 'machine_code': 'OK',
                      'receipt': {'status': 'DEMO_NOT_APPLICABLE',
                                  'baseline_sha': 'd' * 40,
                                  'roadmap_sha': 'c' * 40,
                                  'reason': 'internal-only change'}}))
else:
    print(json.dumps({'status': 'APPLIED', 'machine_code': 'OK',
                      'receipt': {'phase': phase}}))
""",
        encoding="utf-8",
    )
    identity = json.dumps(_live(value), sort_keys=True)
    commands = {
        phase: [sys.executable, str(helper), phase, identity]
        for phase in loop.PHASES[1:]
    }
    config = {
        "schema_version": "1.0",
        "live_command": [sys.executable, str(helper), "live", identity],
        "phase_commands": commands,
        "reconcile_commands": {
            phase: [sys.executable, str(helper), "reconcile", identity]
            for phase in loop.PHASES[1:]
        },
        "timeout_seconds": 10,
        "output_limit": 4096,
    }
    config_path = store.directory / "runtime-adapter.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    config_path.chmod(0o600)

    adapter = loop.CommandRuntimeAdapter.load(config_path, root, store.directory)
    result = loop.resume(store=store, adapter=adapter)

    assert result.status == "TASK_DONE"
    assert result.phase == "NEXT_TASK"
    assert store.load()["completed_phases"] == list(loop.PHASES[1:])  # type: ignore[index]


def test_resume_stops_cleanly_on_retryable_gate_without_unknown_outcome(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    store = loop.CheckpointStore(tmp_path / "private", root)
    value = _checkpoint()
    store.save(value)

    class Adapter:
        def live(self) -> dict[str, object]:
            return _live(value)

        def effect(self, phase: str) -> dict[str, object]:
            raise loop.PhaseBlocked("CI_PENDING")

        def reconcile(
            self, phase: str
        ) -> tuple[str, dict[str, object] | None]:
            return "NOT_APPLIED", None

        def merge_sha(self) -> str | None:
            return None

    result = loop.resume(store=store, adapter=Adapter())

    assert result.status == "BLOCKED"
    assert result.machine_code == "CI_PENDING"
    saved = store.load()
    assert saved is not None
    assert saved["pending_phase"] is None
    assert saved["phase"] == "PLAN"


def test_resume_preserves_rate_limit_pause_without_invoking_adapter(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    store = loop.CheckpointStore(tmp_path / "private", root)
    value = _checkpoint()
    value["status"] = "PAUSED_RATE_LIMIT"
    value["rate_limit_pause"] = {
        "exhausted_windows": ["primary"],
        "resets_at": 1_800_000_000,
        "next_check_at": 1_799_999_000,
    }
    store.save(value)

    class Adapter:
        def live(self) -> dict[str, object]:
            raise AssertionError("paused resume must not invoke the adapter")

        def effect(self, phase: str) -> dict[str, object]:
            raise AssertionError("paused resume must not invoke the adapter")

        def reconcile(self, phase: str) -> tuple[str, dict[str, object] | None]:
            raise AssertionError("paused resume must not invoke the adapter")

        def merge_sha(self) -> str | None:
            raise AssertionError("paused resume must not invoke the adapter")

    result = loop.resume(store=store, adapter=Adapter())

    assert result.status == "PAUSED_RATE_LIMIT"
    assert result.machine_code == "RATE_LIMIT_PAUSED"
    assert store.load() == value


def test_status_keeps_success_exit_code_and_default_resume_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    state = tmp_path / "private"
    loop.CheckpointStore(state, root).save(_checkpoint())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent_epic_loop.py",
            "status",
            "--state-dir",
            str(state),
            "--repository-root",
            str(root),
        ],
    )

    assert loop.main() == 0
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "READY"
    assert value["resume_command"].endswith(
        f"--state-dir {shlex.quote(str(state.resolve()))} "
        f"--repository-root {shlex.quote(str(root.resolve()))}"
    )


def test_runtime_config_must_be_owner_only_inside_state_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    state = tmp_path / "private"
    loop.CheckpointStore(state, root).save(_checkpoint())
    config = state / "runtime-adapter.json"
    config.write_text("{}", encoding="utf-8")
    config.chmod(0o644)

    if os.name != "nt":
        with pytest.raises(loop.LoopError, match="UNSAFE_RUNTIME_CONFIG"):
            loop.CommandRuntimeAdapter.load(config, root, state)
    config.chmod(0o600)
    link = state / "runtime-link.json"
    link.symlink_to(config)
    with pytest.raises(loop.LoopError, match="UNSAFE_RUNTIME_CONFIG"):
        loop.CommandRuntimeAdapter.load(link, root, state)


def test_runtime_config_accepts_normalized_state_path(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("production runtime adapter requires POSIX isolation")
    root = tmp_path / "repository"
    root.mkdir()
    state = tmp_path / "private"
    loop.CheckpointStore(state, root).save(_checkpoint())
    config = state / "runtime-adapter.json"
    command = [sys.executable, "-c", "print('{}')"]
    config.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "live_command": command,
                "phase_commands": {phase: command for phase in loop.PHASES[1:]},
                "reconcile_commands": {
                    phase: command for phase in loop.PHASES[1:]
                },
                "timeout_seconds": 10,
                "output_limit": 1024,
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)
    lexical_state = state / "child" / ".."

    adapter = loop.CommandRuntimeAdapter.load(
        lexical_state / "runtime-adapter.json", root, lexical_state
    )

    assert adapter.root == root.resolve()


@pytest.mark.parametrize(
    ("script", "code"),
    [
        ("import sys; sys.stdout.write('x' * 8192)", "RUNTIME_ADAPTER_FAILED"),
        ("import os; os.write(1, b'\\xff')", "RUNTIME_ADAPTER_INVALID"),
    ],
)
def test_runtime_adapter_fails_closed_on_unsafe_output(
    tmp_path: Path, script: str, code: str
) -> None:
    if os.name == "nt":
        pytest.skip("production runtime adapter requires POSIX isolation")
    root = tmp_path / "repository"
    root.mkdir()
    command = (sys.executable, "-c", script)
    adapter = loop.CommandRuntimeAdapter(
        root=root,
        live_command=command,
        phase_commands={phase: command for phase in loop.PHASES[1:]},
        reconcile_commands={phase: command for phase in loop.PHASES[1:]},
        timeout_seconds=10,
        output_limit=1024,
    )

    with pytest.raises(loop.LoopError, match=code):
        adapter.live()


def test_runtime_adapter_rejects_free_form_machine_code(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("production runtime adapter requires POSIX isolation")
    root = tmp_path / "repository"
    root.mkdir()
    command = (
        sys.executable,
        "-c",
        "import json; print(json.dumps({'status': 'BLOCKED', "
        "'machine_code': 'secret diagnostic', 'receipt': None}))",
    )
    adapter = loop.CommandRuntimeAdapter(
        root=root,
        live_command=command,
        phase_commands={phase: command for phase in loop.PHASES[1:]},
        reconcile_commands={phase: command for phase in loop.PHASES[1:]},
        timeout_seconds=10,
        output_limit=1024,
    )

    with pytest.raises(loop.LoopError, match="RUNTIME_ADAPTER_INVALID"):
        adapter.effect("RUN_TASK")


def test_advance_rejects_receipt_for_wrong_phase(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    value = _checkpoint()
    store = loop.CheckpointStore(tmp_path / "private", root)

    result = loop.advance(
        value,
        live=_live(value),
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: {"phase": "MERGE"},
        reconcile=lambda _phase: ("NOT_APPLIED", None),
    )

    assert result.status == "ESCALATE"
    assert result.machine_code == "RECEIPT_INVALID"


def test_runtime_adapter_does_not_hang_on_inherited_output_pipe(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("production runtime adapter requires POSIX isolation")
    root = tmp_path / "repository"
    root.mkdir()
    script = (
        "import subprocess; "
        f"subprocess.Popen([{sys.executable!r}, '-c', "
        "'import time; time.sleep(5)'], start_new_session=True)"
    )
    command = (sys.executable, "-c", script)
    adapter = loop.CommandRuntimeAdapter(
        root=root,
        live_command=command,
        phase_commands={phase: command for phase in loop.PHASES[1:]},
        reconcile_commands={phase: command for phase in loop.PHASES[1:]},
        timeout_seconds=2,
        output_limit=1024,
    )

    started = time.monotonic()
    with pytest.raises(loop.LoopError, match="RUNTIME_ADAPTER_INVALID"):
        adapter.live()

    assert time.monotonic() - started < 2
