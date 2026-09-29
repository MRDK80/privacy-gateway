#!/usr/bin/env python3
"""Advance one epic task through fixed phases with crash-safe reconciliation."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast

SCHEMA_VERSION = "1.0"
SHA_RE = re.compile(r"[0-9a-f]{40}")
MACHINE_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
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


class PhaseBlocked(Exception):
    """A verified phase is not ready and no ambiguous side effect occurred."""

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


class RuntimeAdapter(Protocol):
    """Narrow production boundary used by the checkpoint supervisor."""

    def live(self) -> Mapping[str, Any]: ...

    def effect(self, phase: str) -> Mapping[str, object]: ...

    def reconcile(
        self, phase: str
    ) -> tuple[str, Mapping[str, object] | None]: ...

    def merge_sha(self) -> str | None: ...


RUNTIME_CONFIG_KEYS = {
    "schema_version",
    "live_command",
    "phase_commands",
    "reconcile_commands",
    "timeout_seconds",
    "output_limit",
}


class CommandRuntimeAdapter:
    """Run closed argv adapters and accept only bounded JSON receipts."""

    def __init__(
        self,
        *,
        root: Path,
        live_command: tuple[str, ...],
        phase_commands: Mapping[str, tuple[str, ...]],
        reconcile_commands: Mapping[str, tuple[str, ...]],
        timeout_seconds: int,
        output_limit: int,
    ) -> None:
        self.root = root.resolve()
        self.live_command = live_command
        self.phase_commands = dict(phase_commands)
        self.reconcile_commands = dict(reconcile_commands)
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit
        self._merge_sha: str | None = None

    @staticmethod
    def _command(value: Any) -> tuple[str, ...]:
        if (
            not isinstance(value, list)
            or not value
            or not all(
                isinstance(item, str)
                and bool(item)
                and "\x00" not in item
                and "\n" not in item
                for item in value
            )
        ):
            raise LoopError("RUNTIME_CONFIG_INVALID")
        if not Path(value[0]).is_absolute():
            raise LoopError("RUNTIME_CONFIG_INVALID")
        return tuple(value)

    @classmethod
    def load(
        cls, path: Path, repository_root: Path, state_directory: Path
    ) -> CommandRuntimeAdapter:
        if os.name == "nt":
            raise LoopError("RUNTIME_ADAPTER_UNSUPPORTED")
        if not any(
            candidate.is_file() and os.access(candidate, os.X_OK)
            for candidate in (Path("/usr/bin/bwrap"), Path("/bin/bwrap"))
        ):
            raise LoopError("BUBBLEWRAP_REQUIRED")
        config_is_symlink = path.is_symlink()
        requested = path.resolve()
        root = repository_root.resolve()
        state = state_directory.resolve()
        expected_path = state / "runtime-adapter.json"
        if requested != expected_path or config_is_symlink:
            raise LoopError("UNSAFE_RUNTIME_CONFIG")
        try:
            state_info = state.stat()
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(requested, flags)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                owner = getattr(os, "getuid", lambda: info.st_uid)()
                if (
                    not stat.S_ISDIR(state_info.st_mode)
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_uid != owner
                    or state_info.st_uid != owner
                    or stat.S_IMODE(info.st_mode) & 0o077
                    or stat.S_IMODE(state_info.st_mode) & 0o077
                ):
                    raise LoopError("UNSAFE_RUNTIME_CONFIG")
                value = json.load(stream)
        except (OSError, ValueError) as error:
            raise LoopError("RUNTIME_CONFIG_READ_FAILED") from error
        item = _exact(value, RUNTIME_CONFIG_KEYS, "RUNTIME_CONFIG_INVALID")
        phase_values = item["phase_commands"]
        reconcile_values = item["reconcile_commands"]
        expected_phases = set(PHASES[1:])
        if (
            item["schema_version"] != "1.0"
            or not isinstance(phase_values, Mapping)
            or set(phase_values) != expected_phases
            or not isinstance(reconcile_values, Mapping)
            or set(reconcile_values) != expected_phases
            or not isinstance(item["timeout_seconds"], int)
            or isinstance(item["timeout_seconds"], bool)
            or not 1 <= item["timeout_seconds"] <= 86400
            or not isinstance(item["output_limit"], int)
            or isinstance(item["output_limit"], bool)
            or not 1024 <= item["output_limit"] <= 1_000_000
        ):
            raise LoopError("RUNTIME_CONFIG_INVALID")
        return cls(
            root=root,
            live_command=cls._command(item["live_command"]),
            phase_commands={
                phase: cls._command(phase_values[phase]) for phase in expected_phases
            },
            reconcile_commands={
                phase: cls._command(reconcile_values[phase])
                for phase in expected_phases
            },
            timeout_seconds=item["timeout_seconds"],
            output_limit=item["output_limit"],
        )

    def _run(self, command: Sequence[str]) -> Mapping[str, Any]:
        if os.name == "nt":
            raise LoopError("RUNTIME_ADAPTER_UNSUPPORTED")

        def terminate_tree(process: subprocess.Popen[bytes]) -> None:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                        check=False,
                    )
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()

        def limit_output_files() -> None:
            resource = importlib.import_module("resource")
            resource.setrlimit(
                resource.RLIMIT_FSIZE, (self.output_limit, self.output_limit)
            )

        try:
            with (
                tempfile.TemporaryFile() as stdout_file,
                tempfile.TemporaryFile() as stderr_file,
            ):
                process = subprocess.Popen(
                    list(command),
                    cwd=self.root,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    preexec_fn=limit_output_files,
                    start_new_session=os.name != "nt",
                )
                deadline = time.monotonic() + self.timeout_seconds
                overflow = False
                while process.poll() is None:
                    if (
                        os.fstat(stdout_file.fileno()).st_size > self.output_limit
                        or os.fstat(stderr_file.fileno()).st_size > self.output_limit
                    ):
                        overflow = True
                        terminate_tree(process)
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        terminate_tree(process)
                        raise subprocess.TimeoutExpired(command, self.timeout_seconds)
                    try:
                        process.wait(timeout=min(0.05, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                return_code = process.wait()
                if (
                    overflow
                    or os.fstat(stdout_file.fileno()).st_size > self.output_limit
                    or os.fstat(stderr_file.fileno()).st_size > self.output_limit
                ):
                    raise LoopError("RUNTIME_ADAPTER_FAILED")
                stdout_file.seek(0)
                stdout = stdout_file.read(self.output_limit + 1)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise LoopError("RUNTIME_ADAPTER_FAILED") from error
        if return_code != 0:
            raise LoopError("RUNTIME_ADAPTER_FAILED")
        try:
            output = stdout.decode("utf-8")
        except UnicodeDecodeError as error:
            raise LoopError("RUNTIME_ADAPTER_INVALID") from error
        try:
            value = json.loads(output)
        except ValueError as error:
            raise LoopError("RUNTIME_ADAPTER_INVALID") from error
        if not isinstance(value, Mapping):
            raise LoopError("RUNTIME_ADAPTER_INVALID")
        return cast(Mapping[str, Any], value)

    def live(self) -> Mapping[str, Any]:
        value = self._run(self.live_command)
        allowed = LIVE_KEYS | {"merge_sha"}
        if not set(value).issubset(allowed) or not LIVE_KEYS.issubset(value):
            raise LoopError("RUNTIME_ADAPTER_INVALID")
        if (
            not isinstance(value["repository"], str)
            or not value["repository"]
            or not all(
                isinstance(value[key], int)
                and not isinstance(value[key], bool)
                and value[key] > 0
                for key in ("epic", "task", "pr")
            )
            or not all(
                isinstance(value[key], str) and bool(value[key])
                for key in ("base_ref", "head_ref")
            )
            or not all(
                isinstance(value[key], str)
                and SHA_RE.fullmatch(value[key]) is not None
                for key in ("base_sha", "head_sha")
            )
        ):
            raise LoopError("RUNTIME_ADAPTER_INVALID")
        merge_sha = value.get("merge_sha")
        if merge_sha is not None and (
            not isinstance(merge_sha, str) or SHA_RE.fullmatch(merge_sha) is None
        ):
            raise LoopError("RUNTIME_ADAPTER_INVALID")
        self._merge_sha = merge_sha
        return {key: value[key] for key in LIVE_KEYS}

    def effect(self, phase: str) -> Mapping[str, object]:
        value = self._run(self.phase_commands[phase])
        if set(value) != {"status", "machine_code", "receipt"}:
            raise LoopError("RUNTIME_ADAPTER_INVALID")
        code = value["machine_code"]
        if not isinstance(code, str) or MACHINE_CODE_RE.fullmatch(code) is None:
            raise LoopError("RUNTIME_ADAPTER_INVALID")
        if value["status"] == "BLOCKED" and value["receipt"] is None:
            raise PhaseBlocked(code)
        receipt = value["receipt"]
        if value["status"] != "APPLIED" or not isinstance(receipt, Mapping):
            raise LoopError("RUNTIME_ADAPTER_INVALID")
        return cast(Mapping[str, object], receipt)

    def reconcile(
        self, phase: str
    ) -> tuple[str, Mapping[str, object] | None]:
        value = self._run(self.reconcile_commands[phase])
        if set(value) != {"state", "receipt"}:
            raise LoopError("RUNTIME_ADAPTER_INVALID")
        state, receipt = value["state"], value["receipt"]
        if state not in {"APPLIED", "NOT_APPLIED", "UNKNOWN"} or not (
            receipt is None or isinstance(receipt, Mapping)
        ):
            raise LoopError("RUNTIME_ADAPTER_INVALID")
        return cast(str, state), cast(Mapping[str, object] | None, receipt)

    def merge_sha(self) -> str | None:
        return self._merge_sha


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
        self.repository_root = root
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
    return (
        "python tools/agent_epic_loop.py resume"
        f" --state-dir {shlex.quote(str(store.directory))}"
        f" --repository-root {shlex.quote(str(store.repository_root))}"
    )


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


def _valid_demo_receipt(value: Mapping[str, object], merge_sha: object) -> bool:
    if set(value) != {"status", "baseline_sha", "roadmap_sha", "reason"}:
        return False
    status = value["status"]
    reason = value["reason"]
    return bool(
        status in {"CONSUMER_DEMO_READY", "DEMO_NOT_APPLICABLE"}
        and isinstance(value["baseline_sha"], str)
        and SHA_RE.fullmatch(value["baseline_sha"]) is not None
        and value["roadmap_sha"] == merge_sha
        and (
            (status == "CONSUMER_DEMO_READY" and reason is None)
            or (
                status == "DEMO_NOT_APPLICABLE"
                and isinstance(reason, str)
                and bool(reason.strip())
            )
        )
    )


def _valid_phase_receipt(
    phase: str, value: Mapping[str, object], merge_sha: object
) -> bool:
    if phase == "DEMO":
        return _valid_demo_receipt(value, merge_sha)
    return set(value) == {"phase"} and value["phase"] == phase


def _block_demo(
    saved: Mapping[str, Any], store: CheckpointStore
) -> AdvanceResult:
    blocked = dict(saved)
    blocked["pending_phase"] = None
    blocked["status"] = "BLOCKED"
    store.save(blocked)
    return _result("BLOCKED", "DEMO_PENDING", blocked, store)


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
                    if not _valid_phase_receipt(
                        target_phase, receipt, saved["merge_sha"]
                    ):
                        if target_phase != "DEMO":
                            return _result(
                                "ESCALATE", "RECEIPT_INVALID", saved, store
                            )
                        return _block_demo(saved, store)
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
            except PhaseBlocked as error:
                blocked = dict(pending)
                blocked["pending_phase"] = None
                blocked["status"] = "BLOCKED"
                store.save(blocked)
                return _result("BLOCKED", error.machine_code, blocked, store)
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
            if not _valid_phase_receipt(target_phase, receipt, saved["merge_sha"]):
                if target_phase != "DEMO":
                    failed = dict(pending)
                    failed["status"] = "ESCALATE"
                    store.save(failed)
                    return _result("ESCALATE", "RECEIPT_INVALID", failed, store)
                return _block_demo(pending, store)
            completed = _complete(pending, target_phase, merge_sha)
            store.save(completed)
            return _result("CONTINUE", "OK", completed, store)
    except LoopError as error:
        fallback = store.load() or dict(expected)
        return _result("ESCALATE", error.machine_code, fallback, store)


def resume(*, store: CheckpointStore, adapter: RuntimeAdapter) -> AdvanceResult:
    """Continue from the first unfinished phase until completion or a safe stop."""
    saved = store.load()
    if saved is None:
        raise LoopError("CHECKPOINT_NOT_FOUND")
    if saved["phase"] == PHASES[-1]:
        return _result("TASK_DONE", "ALREADY_COMPLETED", saved, store)
    if saved["status"] == "PAUSED_RATE_LIMIT":
        return _result("PAUSED_RATE_LIMIT", "RATE_LIMIT_PAUSED", saved, store)
    result: AdvanceResult | None = None
    for _ in PHASES[1:]:
        saved = store.load()
        if saved is None:
            raise LoopError("CHECKPOINT_NOT_FOUND")
        target = _next_phase(cast(str, saved["phase"]))
        if target is None:
            return _result("TASK_DONE", "OK", saved, store)
        reconciled: tuple[str, Mapping[str, object] | None] | None = None
        if saved["pending_phase"] is not None:
            try:
                reconciled = adapter.reconcile(target)
            except LoopError as error:
                return _result("ESCALATE", error.machine_code, saved, store)
            if reconciled[0] == "UNKNOWN":
                return _result(
                    "ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", saved, store
                )
        try:
            live = adapter.live()
        except LoopError as error:
            return _result("ESCALATE", error.machine_code, saved, store)
        merge_sha = adapter.merge_sha() if target == "POST_MERGE" else None

        def run_effect() -> Mapping[str, object]:
            return adapter.effect(target)

        def run_reconcile(
            phase: str,
        ) -> tuple[str, Mapping[str, object] | None]:
            if reconciled is not None:
                return reconciled
            return adapter.reconcile(phase)

        result = advance(
            saved,
            live=live,
            store=store,
            target_phase=target,
            effect=run_effect,
            reconcile=run_reconcile,
            merge_sha=merge_sha,
        )
        if result.status not in {"CONTINUE", "NO_OP"}:
            return result
        if result.phase == PHASES[-1]:
            completed = store.load()
            if completed is None:
                raise LoopError("CHECKPOINT_NOT_FOUND")
            return _result("TASK_DONE", result.machine_code, completed, store)
    if result is None:
        raise LoopError("CHECKPOINT_INVALID")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "resume"):
        child = subparsers.add_parser(command)
        child.add_argument("--state-dir", type=Path, required=True)
        child.add_argument("--repository-root", type=Path, default=Path.cwd())
        if command == "resume":
            child.add_argument("--runtime-config", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        store = CheckpointStore(args.state_dir, args.repository_root)
        status = inspect(store)
        value = asdict(status)
        if args.command == "resume":
            runtime_config = args.runtime_config or (
                args.state_dir / "runtime-adapter.json"
            )
            adapter = CommandRuntimeAdapter.load(
                runtime_config, args.repository_root, args.state_dir
            )
            value = asdict(resume(store=store, adapter=adapter))
        print(json.dumps(value, sort_keys=True))
        if args.command == "status":
            return 0
        return 0 if value["status"] in {"TASK_DONE", "CONTINUE", "NO_OP"} else 2
    except LoopError as error:
        print(json.dumps({"status": "ESCALATE", "machine_code": error.machine_code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
