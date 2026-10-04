"""Bounded recovery effects, using only synthetic identities and receipts."""

import json
import subprocess
from pathlib import Path

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_epic_loop as loop
from tools import agent_epic_recovery as recovery


def test_stop_is_durable_and_probe_cannot_reopen(tmp_path: Path) -> None:
    control = recovery.Control(tmp_path / "state", tmp_path / "repo")
    control.stop("unknown")
    value = recovery.Control(tmp_path / "state", tmp_path / "repo").load()
    assert value["generation"] == 1
    assert value["pending"] is None
    assert value["category"] == "unknown"
    assert value["stopped"] is True


def test_unknown_diagnostic_is_bounded(tmp_path: Path) -> None:
    control = recovery.Control(tmp_path / "state", tmp_path / "repo")
    with pytest.raises(recovery.RecoveryError, match="RECOVERY_STATE_INVALID"):
        control.stop("synthetic sensitive sentinel")
    assert not control.path.exists()


def test_concurrent_recovery_is_blocked(tmp_path: Path) -> None:
    first = recovery.Control(tmp_path / "state", tmp_path / "repo")
    second = recovery.Control(tmp_path / "state", tmp_path / "repo")
    with first.lock():
        with pytest.raises(loop.LoopError, match="RUNNER_LOCKED"):
            with second.lock():
                pytest.fail("second process boundary cannot enter")


def test_approval_digest_excludes_no_authority_fields() -> None:
    left = {"schema_version": "1.0", "generation": 0, "budgets": {"push": 1}}
    assert recovery.digest(left) != recovery.digest(left | {"generation": 1})
    assert recovery.digest(left) != recovery.digest(left | {"budgets": {"push": 2}})


def test_storage_inside_repository_is_forbidden(tmp_path: Path) -> None:
    with pytest.raises(recovery.RecoveryError, match="UNSAFE_RECOVERY_STORAGE"):
        recovery.Control(tmp_path / "repo" / "state", tmp_path / "repo")


def approved_control(
    tmp_path: Path,
) -> tuple[recovery.Control, dict[str, object], dict[str, object]]:
    control = recovery.Control(tmp_path / "state", tmp_path / "repo")
    binding: dict[str, object] = {
        "checkpoint_digest": "synthetic",
        "artifact_digest": "synthetic",
        "mandate_digest": "synthetic",
        "repository": "OWNER/repository",
        "epic": 248,
        "task": 284,
        "policy_sha": "a" * 40,
    }
    approval = binding | {
        "schema_version": "1.0",
        "owner_identity": "OWNER",
        "issued_at": 100,
        "expires_at": 300,
        "generation": 0,
        "budgets": {"push": 1},
    }
    control.authorize(
        approval,
        recovery.digest(approval),
        binding,
        now=200,
        owner="OWNER",
        operation_ids={"push"},
    )
    return control, approval, binding


def test_reserved_attempt_survives_crash_and_requires_new_generation(
    tmp_path: Path,
) -> None:
    control, approval, binding = approved_control(tmp_path)
    control.reserve("push", 1)
    restarted = recovery.Control(control.directory, tmp_path / "repo")
    with pytest.raises(recovery.RecoveryError, match="RECOVERY_INTERRUPTED"):
        restarted.authorize(
            approval,
            recovery.digest(approval),
            binding,
            now=200,
            owner="OWNER",
            operation_ids={"push"},
        )
    assert restarted.load()["attempts"] == {"push": 1}
    with pytest.raises(recovery.RecoveryError, match="RECOVERY_APPROVAL_INVALID"):
        restarted.authorize(
            approval,
            recovery.digest(approval),
            binding,
            now=200,
            owner="OWNER",
            operation_ids={"push"},
        )


def test_finite_budget_is_not_refunded_by_success(tmp_path: Path) -> None:
    control, _, _ = approved_control(tmp_path)
    control.reserve("push", 1)
    control.complete("push")
    with pytest.raises(recovery.RecoveryError, match="RECOVERY_BUDGET_EXHAUSTED"):
        control.reserve("push", 1)
    assert control.load()["stopped"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", 1),
        ("owner_identity", "OTHER"),
        ("expires_at", 200),
        ("budgets", {"push": True}),
        ("policy_sha", "b" * 40),
    ],
)
def test_invalid_owner_authorization_never_reserves(
    tmp_path: Path, field: str, value: object
) -> None:
    control, approval, binding = approved_control(tmp_path)
    control.stop()
    altered = approval | {field: value}
    with pytest.raises(recovery.RecoveryError):
        control.authorize(
            altered,
            recovery.digest(approval),
            binding,
            now=200,
            owner="OWNER",
            operation_ids={"push"},
        )
    assert control.load()["attempts"] == {}


def test_direct_legacy_driver_cannot_bypass_recovery_stop(tmp_path: Path) -> None:
    from tests.test_agent_epic_delivery import _run

    control = recovery.Control(tmp_path / "private", tmp_path / "repository")
    control.stop()
    result = _run(tmp_path, effect=lambda _: pytest.fail("write after stop"))
    assert result.machine_code == "RECOVERY_PROFILE_REQUIRED"


def command_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> recovery.Control:
    import time

    control, approval, _ = approved_control(tmp_path)
    for name, value in (
        (
            "runtime-task.json",
            {
                "schema_version": "2.0",
                "approved_recovery_digest": recovery.digest(approval),
            },
        ),
        ("bootstrap-recovery-approval.json", approval),
    ):
        path = control.directory / name
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
    monkeypatch.setattr(time, "time", lambda: 200)
    return control


def test_direct_command_effect_stays_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = command_fixture(tmp_path, monkeypatch)
    control.stop()
    adapter = loop.CommandRuntimeAdapter(
        root=tmp_path / "repo",
        live_command=(),
        phase_commands={"RUN_TASK": ()},
        reconcile_commands={},
        timeout_seconds=1,
        output_limit=1024,
        state_directory=control.directory,
    )
    monkeypatch.setattr(
        adapter, "_run", lambda _: pytest.fail("runtime write after stop")
    )
    with pytest.raises(recovery.RecoveryError, match="RECOVERY_STOPPED"):
        adapter.effect("RUN_TASK")


def test_invalid_command_response_closes_authorization_without_raw_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = command_fixture(tmp_path, monkeypatch)
    adapter = loop.CommandRuntimeAdapter(
        root=tmp_path / "repo",
        live_command=(),
        phase_commands={"RUN_TASK": ()},
        reconcile_commands={},
        timeout_seconds=1,
        output_limit=1024,
        state_directory=control.directory,
    )
    monkeypatch.setattr(
        adapter, "_run", lambda _: {"synthetic sensitive sentinel": True}
    )
    with pytest.raises(loop.LoopError, match="RUNTIME_ADAPTER_INVALID"):
        adapter.effect("RUN_TASK")
    assert control.load()["category"] == "invalid_adapter_response"
    assert "sentinel" not in control.path.read_text(encoding="utf-8")
    with pytest.raises(recovery.RecoveryError, match="RECOVERY_STOPPED"):
        adapter.effect("RUN_TASK")


@pytest.mark.parametrize("outcome", ["APPLIED", "NOT_APPLIED"])
def test_resume_and_direct_advance_do_not_auto_release_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    from tests.test_agent_epic_bootstrap import checkpoint, identity

    control = command_fixture(tmp_path, monkeypatch)
    control.stop()
    saved = checkpoint() | {"pending_phase": "RUN_TASK", "status": "ESCALATE"}
    store = loop.CheckpointStore(control.directory, tmp_path / "repo")
    store.save(saved)

    class Adapter:
        def effect(self, phase: str) -> dict[str, object]:
            pytest.fail("effect after stop")

        def live(self) -> dict[str, object]:
            return identity(saved)

        def reconcile(self, phase: str) -> tuple[str, None]:
            return outcome, None

        def merge_sha(self) -> None:
            return None

    assert loop.resume(store=store, adapter=Adapter()).status == "ESCALATE"
    assert (
        loop.advance(
            saved,
            live=identity(saved),
            store=store,
            target_phase="RUN_TASK",
            effect=lambda: pytest.fail("direct phase write after stop"),
            reconcile=lambda _: (outcome, None),
        ).status
        == "ESCALATE"
    )
    assert control.load()["generation"] == 1


def test_timeout_diagnostic_does_not_copy_exception_text() -> None:
    error = delivery.OutcomeUnknown()
    error.__cause__ = subprocess.TimeoutExpired("synthetic sensitive sentinel", 1)
    assert recovery.diagnostic(error) == "timeout"
    assert recovery.diagnostic(RuntimeError("network synthetic sentinel")) == "unknown"


def test_unknown_read_probe_never_opens_control(tmp_path: Path) -> None:
    control, _, _ = approved_control(tmp_path)
    control.stop()
    with pytest.raises(recovery.RecoveryError, match="RECOVERY_STOPPED"):
        control.check_entry()
    assert control.load()["stopped"] is True


def test_unknown_fields_cannot_extend_recovery_authority(tmp_path: Path) -> None:
    control, approval, binding = approved_control(tmp_path)
    extended = approval | {"allow_merge": True}
    with pytest.raises(recovery.RecoveryError, match="RECOVERY_APPROVAL_INVALID"):
        control.authorize(
            extended,
            recovery.digest(extended),
            binding,
            now=200,
            owner="OWNER",
            operation_ids={"push"},
        )
    state = control.load() | {"allow_merge": True}
    control.path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(recovery.RecoveryError, match="RECOVERY_STATE_INVALID"):
        control.load()


def test_recovery_profile_cannot_bypass_rate_limit_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_agent_epic_bootstrap import checkpoint, identity

    control = command_fixture(tmp_path, monkeypatch)
    saved = checkpoint() | {
        "pending_phase": "RUN_TASK",
        "status": "PAUSED_RATE_LIMIT",
        "rate_limit_pause": {
            "exhausted_windows": ["primary"],
            "resets_at": 300,
            "next_check_at": 300,
        },
    }
    store = loop.CheckpointStore(control.directory, tmp_path / "repo")
    store.save(saved)

    class Adapter:
        def effect(self, phase: str) -> dict[str, object]:
            pytest.fail("recovery cannot bypass quota pause")

        def live(self) -> dict[str, object]:
            pytest.fail("quota resume requires its separate revalidation")

        def reconcile(self, phase: str) -> tuple[str, None]:
            pytest.fail("paused supervisor must not invoke runtime")

        def merge_sha(self) -> None:
            return None

    assert loop.resume(store=store, adapter=Adapter()).status == "PAUSED_RATE_LIMIT"
    assert (
        loop.advance(
            saved,
            live=identity(saved),
            store=store,
            target_phase="RUN_TASK",
            effect=lambda: pytest.fail("direct effect after quota pause"),
            reconcile=lambda _: pytest.fail("direct reconciliation after quota pause"),
        ).status
        == "PAUSED_RATE_LIMIT"
    )
