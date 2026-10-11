"""Synthetic durable read waits preserve reservations and authority boundaries."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_wait as wait
from tools import agent_github_read as reads

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Private runtime is POSIX-only")

ARGV = ("gh", "issue", "view", "251", "--repo", "owner/repo", "--json", "number,state")


class Clock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.origin = 100_000
        self.interrupt = False

    def now(self) -> float:
        return self.origin + self.elapsed

    def sleep(self, seconds: float) -> None:
        if self.interrupt:
            raise KeyboardInterrupt
        self.elapsed += seconds


def reader(tmp_path: Path, clock: Clock, run: Any, **options: Any) -> wait.Reader:
    state = tmp_path / "state"
    state.mkdir(mode=0o700, exist_ok=True)
    return wait.Reader(
        directory=state,
        root=tmp_path / "repository",
        identity={
            "repository": "owner/repo",
            "epic": 248,
            "policy_sha": "a" * 40,
            "wait_digest": "sha256:" + "b" * 64,
            "stage": "TASK:251:PR_CI",
        },
        limits={
            "max_phase_seconds": 1000,
            "max_read_attempts": 3,
            "max_ci_polls": 3,
            "max_read_commands": 8,
        },
        deadline_at=100_900,
        now=clock.now,
        monotonic=lambda: clock.elapsed,
        sleep=clock.sleep,
        run=run,
        **options,
    )


def transient() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ARGV, 1, "", 'Get "https://api.github.com": net/http: TLS handshake timeout'
    )


def test_transient_wait_is_relative_to_completion_and_single_read(
    tmp_path: Path,
) -> None:
    clock = Clock()
    calls: list[float] = []

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(clock.elapsed)
        assert kwargs["timeout"] <= 120
        clock.elapsed += 3
        return (
            transient()
            if len(calls) == 1
            else subprocess.CompletedProcess(ARGV, 0, "{}", "")
        )

    current = reader(tmp_path, clock, run)
    assert current.command(ARGV) == "{}"
    assert calls == [0, 63]
    saved = current.inspect()
    assert saved["requests"]["1"]["attempts"] == 2
    assert saved["requests"]["1"]["status"] == "READY"


def test_restart_wait_retains_original_deadline_and_attempts(tmp_path: Path) -> None:
    clock = Clock()
    clock.interrupt = True
    current = reader(tmp_path, clock, lambda *a, **k: transient())
    with pytest.raises(KeyboardInterrupt):
        current.command(ARGV)
    saved = current.inspect()
    assert saved["requests"]["1"]["status"] == "WAITING"
    clock.elapsed = 30
    clock.interrupt = False
    calls: list[float] = []

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(clock.elapsed)
        return subprocess.CompletedProcess(ARGV, 0, "{}", "")

    restored = reader(tmp_path, clock, run)
    assert restored.command(ARGV) == "{}"
    assert calls == [60]
    assert restored.inspect()["deadline_at"] == saved["deadline_at"]
    assert restored.inspect()["requests"]["1"]["attempts"] == 2


def test_crash_reservation_never_reinvokes_or_refunds(tmp_path: Path) -> None:
    clock = Clock()

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    current = reader(tmp_path, clock, crash)
    with pytest.raises(KeyboardInterrupt):
        current.command(ARGV)
    restored = reader(tmp_path, clock, lambda *a, **k: pytest.fail("blind retry"))
    with pytest.raises(wait.WaitError, match="WAIT_OUTCOME_UNKNOWN"):
        restored.command(ARGV)
    assert restored.inspect()["requests"]["1"]["attempts"] == 1


@pytest.mark.parametrize(
    "stderr",
    ["HTTP 403 permission denied timeout", "arbitrary timeout", "invalid JSON"],
)
def test_unclassified_failure_never_waits(tmp_path: Path, stderr: str) -> None:
    clock = Clock()
    current = reader(
        tmp_path,
        clock,
        lambda *a, **k: subprocess.CompletedProcess(ARGV, 1, "", stderr),
    )
    with pytest.raises(wait.WaitError):
        current.command(ARGV)
    assert clock.elapsed == 0
    assert current.inspect()["requests"]["1"]["status"] == "FAILED"
    assert stderr not in json.dumps(current.inspect())


def test_cancel_during_wait_has_no_later_read(tmp_path: Path) -> None:
    clock = Clock()
    calls: list[int] = []

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return transient()

    def guard() -> None:
        if clock.elapsed >= 2:
            raise wait.WaitError("OWNER_STOPPED")

    current = reader(tmp_path, clock, run, guard=guard)
    with pytest.raises(wait.WaitError, match="OWNER_STOPPED"):
        current.command(ARGV)
    assert len(calls) == 1
    assert current.inspect()["requests"]["1"]["attempts"] == 1


@pytest.mark.parametrize(
    "argv",
    [
        ("git", "ls-remote", "origin"),
        ("gh", "api", "repos/other/repo/branches"),
        ("gh", "api", "repos/owner/repo/issues", "--method", "POST"),
    ],
)
def test_git_writes_and_foreign_repository_are_ineligible(
    tmp_path: Path, argv: tuple[str, ...]
) -> None:
    clock = Clock()
    current = reader(
        tmp_path, clock, lambda *a, **k: pytest.fail("ineligible transport")
    )
    with pytest.raises(wait.WaitError, match="WAIT_READ_INELIGIBLE"):
        current.command(argv)


def test_symlink_storage_does_not_create_external_artifacts(tmp_path: Path) -> None:
    external = tmp_path / "outside"
    external.mkdir()
    state = tmp_path / "state"
    state.symlink_to(external, target_is_directory=True)
    clock = Clock()
    with pytest.raises(wait.WaitError):
        reader(tmp_path, clock, lambda *a, **k: pytest.fail("unsafe read"))
    assert list(external.iterdir()) == []


def test_diagnostic_classification_does_not_extend_completion_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    starts: list[float] = []
    classify = reads._category

    def diagnostic(stderr: object) -> str:
        clock.elapsed += 3
        return classify(stderr)

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        starts.append(clock.elapsed)
        clock.elapsed += 7
        return (
            transient()
            if len(starts) == 1
            else subprocess.CompletedProcess(ARGV, 0, "{}", "")
        )

    monkeypatch.setattr(reads, "_category", diagnostic)
    current = reader(tmp_path, clock, run)
    assert current.command(ARGV) == "{}"
    assert starts == [0, 67]
    assert current.inspect()["requests"]["1"]["attempts"] == 2


def test_ci_poll_is_finite_and_retains_identity(tmp_path: Path) -> None:
    clock = Clock()
    current = reader(tmp_path, clock, lambda *a, **k: None)
    polls: list[float] = []

    def assess() -> dict[str, Any]:
        polls.append(clock.elapsed)
        return (
            {"machine_code": "CI_PENDING", "status": "blocked"}
            if len(polls) < 3
            else {"machine_code": "OK", "status": "pass"}
        )

    assert current.poll_ci({"head_sha": "c" * 40}, assess)["status"] == "pass"
    assert polls == [0, 60, 120]
    assert current.inspect()["ci"]["attempts"] == 3
    with pytest.raises(wait.WaitError, match="WAIT_CI_IDENTITY_CHANGED"):
        current.poll_ci({"head_sha": "d" * 40}, assess)
    assert len(polls) == 3


def test_ci_restart_wait_does_not_reset_poll_count(tmp_path: Path) -> None:
    clock = Clock()
    clock.interrupt = True
    current = reader(tmp_path, clock, lambda *a, **k: None)
    binding = {"head_sha": "c" * 40}
    with pytest.raises(KeyboardInterrupt):
        current.poll_ci(
            binding, lambda: {"machine_code": "CI_PENDING", "status": "blocked"}
        )
    assert current.inspect()["ci"]["attempts"] == 1
    clock.interrupt = False
    resumed = reader(tmp_path, clock, lambda *a, **k: None)
    assert (
        resumed.poll_ci(binding, lambda: {"machine_code": "OK", "status": "pass"})[
            "status"
        ]
        == "pass"
    )
    assert clock.elapsed == 60
    assert resumed.inspect()["ci"]["attempts"] == 2


def test_successful_read_is_fresh_and_bounded(tmp_path: Path) -> None:
    clock = Clock()
    calls: list[int] = []

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return subprocess.CompletedProcess(ARGV, 0, "{}", "")

    current = reader(tmp_path, clock, run)
    for _ in range(8):
        assert current.command(ARGV) == "{}"
    with pytest.raises(wait.WaitError, match="WAIT_BUDGET_EXHAUSTED"):
        current.command(ARGV)
    assert len(calls) == 8


def test_original_deadline_and_rollback_fail_closed(tmp_path: Path) -> None:
    clock = Clock()
    current = reader(
        tmp_path, clock, lambda *a, **k: subprocess.CompletedProcess(ARGV, 0, "{}", "")
    )
    current.command(ARGV)
    clock.elapsed = -1
    with pytest.raises(wait.WaitError):
        current.command(ARGV)
    clock.elapsed = 901
    with pytest.raises(wait.WaitError):
        current.command(ARGV)


def test_ci_nested_read_uses_one_lock_and_preserves_reservations(
    tmp_path: Path,
) -> None:
    clock = Clock()
    current = reader(
        tmp_path, clock, lambda *a, **k: subprocess.CompletedProcess(ARGV, 0, "{}", "")
    )

    def assess() -> dict[str, Any]:
        assert current.command(ARGV) == "{}"
        return {"machine_code": "OK", "status": "pass"}

    current.poll_ci({"head_sha": "c" * 40}, assess)
    saved = current.inspect()
    assert saved["requests"]["1"]["status"] == "READY"
    assert saved["ci"]["status"] == "READY"


def test_unrelated_wait_cannot_classify_interrupted_ci_poll(tmp_path: Path) -> None:
    clock = Clock()
    clock.interrupt = True
    current = reader(tmp_path, clock, lambda *a, **k: transient())
    with pytest.raises(KeyboardInterrupt):
        current.command(ARGV)

    def interrupted_before_own_read() -> dict[str, Any]:
        raise KeyboardInterrupt

    binding = {"head_sha": "c" * 40}
    with pytest.raises(KeyboardInterrupt):
        current.poll_ci(binding, interrupted_before_own_read)
    assert current.inspect()["ci"]["status"] == "RESERVED"
    clock.interrupt = False
    restored = reader(tmp_path, clock, lambda *a, **k: pytest.fail("orphan read"))
    with pytest.raises(wait.WaitError, match="WAIT_OUTCOME_UNKNOWN"):
        restored.poll_ci(binding, lambda: pytest.fail("orphan assessor"))
    assert restored.inspect()["ci"]["attempts"] == 1


def test_nested_transient_commits_ci_wait_before_sleep_and_restart(
    tmp_path: Path,
) -> None:
    clock = Clock()
    clock.interrupt = True
    current = reader(tmp_path, clock, lambda *a, **k: transient())
    binding = {"head_sha": "c" * 40}

    def assess() -> dict[str, Any]:
        current.command(ARGV)
        return {"machine_code": "OK", "status": "pass"}

    with pytest.raises(KeyboardInterrupt):
        current.poll_ci(binding, assess)
    saved = current.inspect()
    assert saved["ci"]["status"] == "WAITING"
    assert saved["ci"]["next_check_at"] == saved["requests"]["1"]["next_check_at"]
    clock.interrupt = False
    calls: list[float] = []

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(clock.elapsed)
        return subprocess.CompletedProcess(ARGV, 0, "{}", "")

    restored = reader(tmp_path, clock, run)

    def resume_assessment() -> dict[str, Any]:
        restored.command(ARGV)
        return {"machine_code": "OK", "status": "pass"}

    assert restored.poll_ci(binding, resume_assessment)["status"] == "pass"
    final = restored.inspect()
    assert calls == [60]
    assert final["ci"]["attempts"] == 2
    assert final["requests"]["1"]["attempts"] == 2
    assert len(final["requests"]) == 1
    assert final["deadline_at"] == saved["deadline_at"]
