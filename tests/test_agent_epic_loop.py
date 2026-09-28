"""Idempotent epic task phase supervisor tests (#253)."""

from __future__ import annotations

import copy
import json
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
        reconcile=lambda _phase: ("APPLIED", {"receipt": "verified"}),
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
        return {"receipt": "created"}

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
