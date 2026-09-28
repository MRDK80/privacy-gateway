#!/usr/bin/env python3
"""Advance one epic task through fixed phases with crash-safe reconciliation."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast

SCHEMA_VERSION = "1.0"
SHA_RE = re.compile(r"[0-9a-f]{40}")
PHASES = (
    "PLAN",
    "RUN_TASK",
    "PR_CI",
    "MERGE",
    "POST_MERGE",
    "DEMO",
    "TASK_DONE",
    "NEXT_TASK",
)
CHECKPOINT_KEYS = {
    "schema_version",
    "repository",
    "epic",
    "task",
    "pr",
    "base_ref",
    "base_sha",
    "head_ref",
    "head_sha",
    "phase",
    "completed_phases",
    "pending_phase",
    "merge_sha",
    "status",
    "rate_limit_pause",
}
IDENTITY_KEYS = {
    "repository",
    "epic",
    "task",
    "pr",
    "base_ref",
    "base_sha",
    "head_ref",
    "head_sha",
}
LIVE_KEYS = IDENTITY_KEYS


class LoopError(Exception):
    """Fail-closed supervisor error safe to expose as a machine code."""

    def __init__(self, machine_code: str) -> None:
        super().__init__(machine_code)
        self.machine_code = machine_code


@dataclass(frozen=True)
class AdvanceResult:
    status: str
    machine_code: str
    phase: str
    next_phase: str | None
    resume_command: str


@dataclass(frozen=True)
class Status:
    status: str
    phase: str
    next_phase: str | None
    pending_phase: str | None
    resume_command: str


Effect = Callable[[], Mapping[str, object]]
Reconcile = Callable[[str], tuple[str, Mapping[str, object] | None]]


def default_directory() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured) if configured else Path.home() / ".local" / "state"
    return base / "privacy-gateway" / "epic-runner"


def _exact(value: Any, keys: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise LoopError(code)
    return cast(Mapping[str, Any], value)


def _next_phase(phase: str) -> str | None:
    index = PHASES.index(phase)
    return PHASES[index + 1] if index + 1 < len(PHASES) else None


def validate_checkpoint(value: Any) -> dict[str, Any]:
    """Validate the closed authority and progress checkpoint."""
    item = _exact(value, CHECKPOINT_KEYS, "CHECKPOINT_INVALID")
    phase = item["phase"]
    completed = item["completed_phases"]
    pending = item["pending_phase"]
    if not isinstance(phase, str) or phase not in PHASES:
        raise LoopError("CHECKPOINT_INVALID")
    expected_completed = list(PHASES[1 : PHASES.index(phase) + 1])
    merge_required = PHASES.index(phase) >= PHASES.index("POST_MERGE")
    if (
        item["schema_version"] != SCHEMA_VERSION
        or not isinstance(item["repository"], str)
        or item["repository"].count("/") != 1
        or not all(
            isinstance(item[key], int)
            and not isinstance(item[key], bool)
            and item[key] > 0
            for key in ("epic", "task", "pr")
        )
        or item["epic"] == item["task"]
        or not isinstance(item["base_ref"], str)
        or not item["base_ref"].startswith(f"roadmap/{item['epic']}-")
        or not isinstance(item["head_ref"], str)
        or item["head_ref"].startswith("roadmap/")
        or not isinstance(item["base_sha"], str)
        or SHA_RE.fullmatch(item["base_sha"]) is None
        or not isinstance(item["head_sha"], str)
        or SHA_RE.fullmatch(item["head_sha"]) is None
        or not isinstance(completed, list)
        or completed != expected_completed
        or (pending is not None and pending != _next_phase(phase))
        or (
            item["merge_sha"] is not None
            and (
                not isinstance(item["merge_sha"], str)
                or SHA_RE.fullmatch(item["merge_sha"]) is None
            )
        )
        or (merge_required != (item["merge_sha"] is not None))
        or item["status"]
        not in {
            "READY",
            "RUNNING",
            "ESCALATE",
            "BLOCKED",
            "PAUSED_RATE_LIMIT",
            "TASK_DONE",
        }
        or not _valid_rate_limit_pause(item["rate_limit_pause"], item["status"])
        or (phase == "NEXT_TASK" and item["status"] != "TASK_DONE")
    ):
        raise LoopError("CHECKPOINT_INVALID")
    return dict(item)


def _valid_rate_limit_pause(value: Any, status: Any) -> bool:
    if value is None:
        return bool(status != "PAUSED_RATE_LIMIT")
    if status != "PAUSED_RATE_LIMIT" or not isinstance(value, Mapping):
        return False
    if set(value) != {"exhausted_windows", "resets_at", "next_check_at"}:
        return False
    windows = value["exhausted_windows"]
    resets_at = value["resets_at"]
    next_check_at = value["next_check_at"]
    return bool(
        isinstance(windows, list)
        and bool(windows)
        and all(window in {"primary", "secondary"} for window in windows)
        and (resets_at is None or isinstance(resets_at, int))
        and (next_check_at is None or isinstance(next_check_at, int))
    )


class CheckpointStore:
    """Atomic private state deliberately forbidden inside the repository."""

    def __init__(self, directory: Path, repository_root: Path) -> None:
        self.directory = directory.resolve()
        root = repository_root.resolve()
        try:
            self.directory.relative_to(root)
        except ValueError:
            pass
        else:
            raise LoopError("UNSAFE_STORAGE")
        self.path = self.directory / "epic-loop-checkpoint.json"

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LoopError("CHECKPOINT_READ_FAILED") from error
        if isinstance(value, dict) and set(value) == CHECKPOINT_KEYS - {
            "rate_limit_pause"
        }:
            value["rate_limit_pause"] = None
        return validate_checkpoint(value)

    def save(self, value: Mapping[str, Any]) -> None:
        checked = validate_checkpoint(value)
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.directory, 0o700)
            descriptor, temporary = tempfile.mkstemp(
                dir=self.directory, prefix=".epic-loop-", suffix=".tmp"
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(checked, stream, sort_keys=True, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except OSError as error:
            raise LoopError("CHECKPOINT_WRITE_FAILED") from error


class RunnerLock:
    """Non-blocking process lock for one private epic state directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self._stream: BinaryIO | None = None

    @staticmethod
    def _lock(stream: BinaryIO) -> None:
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return
        fcntl = importlib.import_module("fcntl")
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(stream: BinaryIO) -> None:
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
        fcntl = importlib.import_module("fcntl")
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> RunnerLock:
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.directory, 0o700)
            stream = (self.directory / "epic-loop.lock").open("a+b")
            os.chmod(stream.name, 0o600)
            self._lock(stream)
        except (BlockingIOError, PermissionError) as error:
            stream.close()
            raise LoopError("RUNNER_LOCKED") from error
        except OSError as error:
            raise LoopError("LOCK_FAILED") from error
        self._stream = stream
        return self

    def __exit__(self, *_args: object) -> None:
        if self._stream is not None:
            self._unlock(self._stream)
            self._stream.close()
            self._stream = None


def _resume_command(store: CheckpointStore) -> str:
    return f"python tools/agent_epic_loop.py resume --state-dir {store.directory}"


def inspect(store: CheckpointStore) -> Status:
    """Return a bounded operator status without authority-bearing free text."""
    saved = store.load()
    if saved is None:
        raise LoopError("CHECKPOINT_NOT_FOUND")
    return Status(
        cast(str, saved["status"]),
        cast(str, saved["phase"]),
        _next_phase(cast(str, saved["phase"])),
        cast(str | None, saved["pending_phase"]),
        _resume_command(store),
    )


def _result(
    status: str, code: str, saved: Mapping[str, Any], store: CheckpointStore
) -> AdvanceResult:
    phase = cast(str, saved["phase"])
    return AdvanceResult(
        status, code, phase, _next_phase(phase), _resume_command(store)
    )


def _complete(
    saved: Mapping[str, Any], target_phase: str, merge_sha: str | None
) -> dict[str, Any]:
    updated = dict(saved)
    updated["phase"] = target_phase
    updated["completed_phases"] = [*saved["completed_phases"], target_phase]
    updated["pending_phase"] = None
    if target_phase == "POST_MERGE":
        updated["merge_sha"] = merge_sha
    updated["status"] = "TASK_DONE" if target_phase == "NEXT_TASK" else "RUNNING"
    return updated


def advance(
    expected: Mapping[str, Any],
    *,
    live: Mapping[str, Any],
    store: CheckpointStore,
    target_phase: str,
    effect: Effect,
    reconcile: Reconcile,
    merge_sha: str | None = None,
) -> AdvanceResult:
    """Advance exactly one phase; reconcile any prior uncertain effect first."""
    try:
        wanted = validate_checkpoint(expected)
        facts = _exact(live, LIVE_KEYS, "LIVE_IDENTITY_CHANGED")
        if any(facts[key] != wanted[key] for key in IDENTITY_KEYS):
            raise LoopError("LIVE_IDENTITY_CHANGED")
        with RunnerLock(store.directory):
            saved = store.load()
            if saved is None:
                saved = wanted
                store.save(saved)
            if any(saved[key] != wanted[key] for key in IDENTITY_KEYS):
                raise LoopError("RESUME_IDENTITY_CHANGED")
            current = cast(str, saved["phase"])
            if target_phase == current:
                return _result("NO_OP", "ALREADY_COMPLETED", saved, store)
            if target_phase != _next_phase(current):
                raise LoopError("PHASE_ORDER_INVALID")
            if target_phase == "POST_MERGE":
                if merge_sha is None or SHA_RE.fullmatch(merge_sha) is None:
                    raise LoopError("MERGE_SHA_REQUIRED")
            elif merge_sha is not None:
                raise LoopError("MERGE_SHA_UNEXPECTED")

            if saved["pending_phase"] is not None:
                state, receipt = reconcile(target_phase)
                if state == "APPLIED" and receipt:
                    completed = _complete(saved, target_phase, merge_sha)
                    store.save(completed)
                    return _result("NO_OP", "ALREADY_APPLIED", completed, store)
                if state != "NOT_APPLIED":
                    return _result(
                        "ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", saved, store
                    )

            pending = dict(saved)
            pending["pending_phase"] = target_phase
            pending["status"] = "RUNNING"
            store.save(pending)
            try:
                receipt = effect()
            except Exception:
                failed = dict(pending)
                failed["status"] = "ESCALATE"
                store.save(failed)
                return _result(
                    "ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", failed, store
                )
            if not receipt:
                failed = dict(pending)
                failed["status"] = "ESCALATE"
                store.save(failed)
                return _result(
                    "ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", failed, store
                )
            completed = _complete(pending, target_phase, merge_sha)
            store.save(completed)
            return _result("CONTINUE", "OK", completed, store)
    except LoopError as error:
        fallback = store.load() or dict(expected)
        return _result("ESCALATE", error.machine_code, fallback, store)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "resume"):
        child = subparsers.add_parser(command)
        child.add_argument("--state-dir", type=Path, required=True)
        child.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        store = CheckpointStore(args.state_dir, args.repository_root)
        status = inspect(store)
        value = asdict(status)
        if args.command == "resume":
            value["status"] = "ADAPTER_REQUIRED"
        print(json.dumps(value, sort_keys=True))
        return 0 if args.command == "status" else 2
    except LoopError as error:
        print(json.dumps({"status": "ESCALATE", "machine_code": error.machine_code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
