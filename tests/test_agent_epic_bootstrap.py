"""Bootstrap regressions: real PR identity can only be bound once."""

from pathlib import Path

import pytest
from tools import agent_epic_loop as loop


def checkpoint(*, version: str = "2.0", pr: int | None = None) -> dict[str, object]:
    return {
        "schema_version": version,
        "repository": "OWNER/repository",
        "epic": 248,
        "task": 274,
        "pr": pr,
        "base_ref": "roadmap/248-autonomous-epic-runner",
        "base_sha": "a" * 40,
        "head_ref": "codex/274-task",
        "head_sha": "a" * 40,
        "phase": "PLAN",
        "completed_phases": [],
        "pending_phase": None,
        "merge_sha": None,
        "status": "READY",
        "rate_limit_pause": None,
    }


def identity(value: dict[str, object]) -> dict[str, object]:
    return {key: value[key] for key in loop.IDENTITY_KEYS}


def test_pinned_checkpoint_migration_is_explicit_and_pure() -> None:
    original = checkpoint()
    migrated = loop.migrate_pinned_checkpoint(original, policy_sha="a" * 40)
    assert original == checkpoint()
    assert migrated["schema_version"] == "3.0"
    assert migrated["policy_sha"] == "a" * 40
    assert loop.validate_checkpoint(migrated) == migrated
    with pytest.raises(loop.LoopError, match="MIGRATION_POLICY_MISMATCH"):
        loop.migrate_pinned_checkpoint(original, policy_sha="b" * 40)


def test_pinned_binding_preserves_policy_sha(tmp_path: Path) -> None:
    initial = checkpoint(version="3.0") | {"policy_sha": "c" * 40}
    fresh = identity(initial) | {"pr": 278, "head_sha": "b" * 40}
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    result = loop.advance(
        initial,
        live=identity(initial),
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: {"phase": "RUN_TASK", "delivery_identity": fresh},
        reconcile=lambda _: ("NOT_APPLIED", None),
        binding_live=lambda: fresh,
    )
    assert result.status == "CONTINUE"
    persisted = store.load()
    assert persisted is not None and persisted["policy_sha"] == "c" * 40


def test_bootstrap_accepts_absent_pr_but_legacy_does_not() -> None:
    assert loop.validate_checkpoint(checkpoint())["pr"] is None
    with pytest.raises(loop.LoopError, match="CHECKPOINT_INVALID"):
        loop.validate_checkpoint(checkpoint(version="1.0"))


def test_binding_requires_fresh_identity_and_persists_real_pr(tmp_path: Path) -> None:
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    initial = checkpoint()
    fresh = identity(initial) | {"pr": 278, "head_sha": "b" * 40}
    receipt = {"phase": "RUN_TASK", "delivery_identity": fresh}
    calls: list[str] = []

    def fresh_live() -> dict[str, object]:
        calls.append("fresh")
        return fresh

    result = loop.advance(
        initial,
        live=identity(initial),
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: receipt,
        reconcile=lambda _: ("NOT_APPLIED", None),
        binding_live=fresh_live,
    )
    assert result.status == "CONTINUE"
    saved = store.load()
    assert saved is not None
    assert saved["pr"] == 278 and saved["head_sha"] == "b" * 40
    assert saved["pending_phase"] is None
    assert calls == ["fresh"]


@pytest.mark.parametrize(
    "changed", ["repository", "epic", "task", "base_ref", "base_sha", "head_ref"]
)
def test_binding_cannot_change_scope(tmp_path: Path, changed: str) -> None:
    initial = checkpoint()
    bound = identity(initial) | {"pr": 278, "head_sha": "b" * 40}
    bound[changed] = 999 if changed in {"epic", "task"} else "wrong"
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    result = loop.advance(
        initial,
        live=identity(initial),
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: {"phase": "RUN_TASK", "delivery_identity": bound},
        reconcile=lambda _: ("NOT_APPLIED", None),
        binding_live=lambda: bound,
    )
    assert result.status == "ESCALATE"
    saved = store.load()
    assert saved is not None and saved["pr"] is None
    assert saved["pending_phase"] == "RUN_TASK"


def test_crash_after_pr_creation_reconciles_without_repeating_write(
    tmp_path: Path,
) -> None:
    initial = checkpoint() | {"pending_phase": "RUN_TASK", "status": "ESCALATE"}
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    store.save(initial)
    fresh = identity(initial) | {"pr": 278, "head_sha": "b" * 40}
    result = loop.advance(
        initial,
        live=fresh,
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: pytest.fail("must not repeat create PR"),
        reconcile=lambda _: (
            "APPLIED",
            {"phase": "RUN_TASK", "delivery_identity": fresh},
        ),
        binding_live=lambda: fresh,
    )
    assert result.status == "NO_OP"
    saved = store.load()
    assert saved is not None and saved["pr"] == 278


def test_stale_fresh_identity_keeps_pending_intent(tmp_path: Path) -> None:
    initial = checkpoint()
    fresh = identity(initial) | {"pr": 278, "head_sha": "b" * 40}
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    result = loop.advance(
        initial,
        live=identity(initial),
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: {"phase": "RUN_TASK", "delivery_identity": fresh},
        reconcile=lambda _: ("NOT_APPLIED", None),
        binding_live=lambda: identity(initial),
    )
    assert result.status == "ESCALATE"
    saved = store.load()
    assert saved is not None and saved["pending_phase"] == "RUN_TASK"


def test_legacy_migration_is_explicit_and_preserves_identity() -> None:
    old = checkpoint(version="1.0", pr=278)
    migrated = loop.migrate_checkpoint(old)
    assert migrated == old | {"schema_version": "2.0"}
    assert old["schema_version"] == "1.0"
    with pytest.raises(loop.LoopError, match="MIGRATION_PENDING_INTENT"):
        loop.migrate_checkpoint(old | {"pending_phase": "RUN_TASK"})


@pytest.mark.parametrize("pr", [0, -1, True, "278", None])
def test_binding_rejects_invalid_pr(tmp_path: Path, pr: object) -> None:
    initial = checkpoint()
    bound = identity(initial) | {"pr": pr, "head_sha": "b" * 40}
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    result = loop.advance(
        initial,
        live=identity(initial),
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: {"phase": "RUN_TASK", "delivery_identity": bound},
        reconcile=lambda _: ("NOT_APPLIED", None),
        binding_live=lambda: bound,
    )
    assert result.machine_code == "RECEIPT_INVALID"


@pytest.mark.parametrize("pending", [None, "RUN_TASK"])
def test_fresh_read_failure_never_clears_intent(
    tmp_path: Path, pending: str | None
) -> None:
    initial = checkpoint() | {"pending_phase": pending}
    bound = identity(initial) | {"pr": 278, "head_sha": "b" * 40}
    receipt = {"phase": "RUN_TASK", "delivery_identity": bound}
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    store.save(initial)

    def unavailable() -> dict[str, object]:
        raise OSError("synthetic private diagnostic must not escape")

    result = loop.advance(
        initial,
        live=bound if pending else identity(initial),
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: receipt,
        reconcile=lambda _: ("APPLIED", receipt),
        binding_live=unavailable,
    )
    assert result.status == "ESCALATE"
    assert result.machine_code == "BINDING_REVALIDATION_FAILED"
    saved = store.load()
    assert saved is not None and saved["pending_phase"] == "RUN_TASK"
    assert saved["pr"] is None


def test_delivery_identity_cannot_be_rebound(tmp_path: Path) -> None:
    initial = checkpoint(pr=278) | {
        "head_sha": "b" * 40,
        "phase": "RUN_TASK",
        "completed_phases": ["RUN_TASK"],
        "status": "RUNNING",
    }
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    store.save(initial)
    result = loop.advance(
        initial,
        live=identity(initial) | {"head_sha": "c" * 40},
        store=store,
        target_phase="PR_CI",
        effect=lambda: pytest.fail("stale identity must block"),
        reconcile=lambda _: ("NOT_APPLIED", None),
    )
    assert result.machine_code == "LIVE_IDENTITY_CHANGED"
    assert store.load() == initial


def test_plain_phase_receipt_cannot_complete_bootstrap(tmp_path: Path) -> None:
    initial = checkpoint()
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    result = loop.advance(
        initial,
        live=identity(initial),
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: {"phase": "RUN_TASK"},
        reconcile=lambda _: ("NOT_APPLIED", None),
    )
    assert result.machine_code == "RECEIPT_INVALID"
    saved = store.load()
    assert saved is not None and saved["pr"] is None


def test_absent_pr_is_invalid_after_bootstrap() -> None:
    with pytest.raises(loop.LoopError, match="CHECKPOINT_INVALID"):
        loop.validate_checkpoint(
            checkpoint()
            | {
                "phase": "RUN_TASK",
                "completed_phases": ["RUN_TASK"],
            }
        )


def test_loading_legacy_state_does_not_migrate(tmp_path: Path) -> None:
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    initial = checkpoint(version="1.0", pr=278)
    store.save(initial)
    before = store.path.read_bytes()
    assert store.load() == initial
    assert store.path.read_bytes() == before


def test_unknown_bootstrap_outcome_never_repeats_effect(tmp_path: Path) -> None:
    initial = checkpoint() | {"pending_phase": "RUN_TASK", "status": "ESCALATE"}
    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    store.save(initial)
    result = loop.advance(
        initial,
        live=identity(initial),
        store=store,
        target_phase="RUN_TASK",
        effect=lambda: pytest.fail("unknown outcome cannot retry"),
        reconcile=lambda _: ("UNKNOWN", None),
    )
    assert result.machine_code == "ESCALATE_UNKNOWN_OUTCOME"
    assert store.load() == initial
