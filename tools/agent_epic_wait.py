"""Private finite read/CI reservations, without effect or approval authority."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from tools import agent_epic_loop as loop
from tools import agent_github_read as reads
from tools import agent_orchestrate as workflow
from tools.agent_epic_runtime_cli import (
    DeploymentError,
    owner_uid,
    private_json,
    secure_directory,
)

IDENTITY_KEYS = {"repository", "epic", "policy_sha", "wait_digest", "stage"}
RECORD_KEYS = {
    "schema_version",
    "identity",
    "started_at",
    "deadline_at",
    "last_seen_at",
    "requests",
    "ci",
}
REQUEST_KEYS = {"command_digest", "attempts", "status", "category", "next_check_at"}
CI_KEYS = {"identity_digest", "attempts", "status", "next_check_at"}
MAXIMA = {
    "max_phase_seconds": 86400,
    "max_read_attempts": 11,
    "max_ci_polls": 11,
    "max_read_commands": 512,
}
STATUSES = {"RESERVED", "WAITING", "READY", "FAILED"}


class WaitError(loop.PhaseBlocked):
    """Only fixed runtime codes; no transport output or command arguments."""


def digest(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
    )


def _exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise WaitError("WAIT_STATE_INVALID")
    return cast(dict[str, Any], value)


def limits(value: Any) -> dict[str, int]:
    current = _exact(value, set(MAXIMA))
    if any(
        type(current[key]) is not int or not 1 <= current[key] <= bound
        for key, bound in MAXIMA.items()
    ):
        raise WaitError("WAIT_PROFILE_INVALID")
    return cast(dict[str, int], current)


def _identity(value: Any) -> dict[str, Any]:
    current = _exact(value, IDENTITY_KEYS)
    if (
        not isinstance(current["repository"], str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", current["repository"])
        is None
        or type(current["epic"]) is not int
        or current["epic"] <= 0
        or not isinstance(current["policy_sha"], str)
        or re.fullmatch(r"[0-9a-f]{40}", current["policy_sha"]) is None
        or not isinstance(current["wait_digest"], str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", current["wait_digest"]) is None
        or not isinstance(current["stage"], str)
        or re.fullmatch(
            r"DISCOVERY|TASK:[1-9][0-9]*:[A-Z_]{1,32}|"
            r"SUCCESSOR:[1-9][0-9]*:[1-9][0-9]*|FINAL:[A-Z_]{1,32}|"
            r"RECONCILE:(?:FINAL|[1-9][0-9]*):[A-Z_]{1,32}",
            current["stage"],
        )
        is None
    ):
        raise WaitError("WAIT_STATE_INVALID")
    return current


def _time(value: Any) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise WaitError("WAIT_STATE_INVALID")
    return float(value)


def _validate_wait_item(item: dict[str, Any], digest_key: str, maximum: int) -> None:
    if (
        not isinstance(item[digest_key], str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", item[digest_key]) is None
        or type(item["attempts"]) is not int
        or not 1 <= item["attempts"] <= maximum
        or item["status"] not in STATUSES
    ):
        raise WaitError("WAIT_STATE_INVALID")
    if item["status"] == "WAITING":
        _time(item["next_check_at"])
    elif item["next_check_at"] is not None:
        raise WaitError("WAIT_STATE_INVALID")


def validate_wait_record(
    raw: Any, identity: dict[str, Any], profile_limits: dict[str, int]
) -> dict[str, Any]:
    value = _exact(raw, RECORD_KEYS)
    if value["schema_version"] != "1.0" or _identity(value["identity"]) != identity:
        raise WaitError("WAIT_STATE_INVALID")
    start, last, deadline = (
        _time(value[key]) for key in ("started_at", "last_seen_at", "deadline_at")
    )
    if (
        not start <= last <= deadline
        or deadline > start + profile_limits["max_phase_seconds"]
    ):
        raise WaitError("WAIT_STATE_INVALID")
    requests = value["requests"]
    if (
        not isinstance(requests, dict)
        or len(requests) > profile_limits["max_read_commands"]
        or set(requests) != {str(i) for i in range(1, len(requests) + 1)}
    ):
        raise WaitError("WAIT_STATE_INVALID")
    for raw_item in requests.values():
        item = _exact(raw_item, REQUEST_KEYS)
        _validate_wait_item(item, "command_digest", profile_limits["max_read_attempts"])
        if item["category"] not in {None, "timeout", "network"} or (
            item["status"] == "WAITING" and item["category"] is None
        ):
            raise WaitError("WAIT_STATE_INVALID")
    if value["ci"] is not None:
        _validate_wait_item(
            _exact(value["ci"], CI_KEYS),
            "identity_digest",
            profile_limits["max_ci_polls"],
        )
    return value


class Reader:
    """One stage budget. READY is evidence of completion, never a fact cache."""

    _owner_assessing: bool

    def __init__(
        self,
        *,
        directory: Path,
        root: Path,
        identity: dict[str, Any],
        limits: dict[str, int],
        deadline_at: float,
        guard: Callable[[], None] | None = None,
        run: Callable[..., Any] | None = None,
        now: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._owner_original = False
        self._owner_wait_identity: tuple[int, ...] | None = None
        self._owner_scope: Any = None
        self.identity = copy.deepcopy(_identity(identity))
        self.limits = globals()["limits"](limits)
        self.state, self.root = directory, root
        self.directory = (
            directory / "read-waits" / digest(self.identity).removeprefix("sha256:")
        )
        self.current_deadline = _time(deadline_at)
        self.guard = guard or (lambda: None)
        self.run, self.now, self.monotonic, self.sleep = (
            run or subprocess.run,
            now,
            monotonic,
            sleep,
        )
        self._depth = 0
        self._active_poll = False
        self._storage(create=True)
        # Profile replacement needs owner disposition, including retained counters.
        for child in (directory / "read-waits").iterdir():
            if (
                not child.is_dir()
                or child.is_symlink()
                or re.fullmatch(r"[0-9a-f]{64}", child.name) is None
            ):
                raise WaitError("WAIT_STORAGE_UNSAFE")
            secure_directory(child, root)
            path = child / "wait.json"
            if path.exists() or path.is_symlink():
                self._private(path.lstat())
                other = private_json(path)
                previous = _identity(other.get("identity"))
                if previous["wait_digest"] != self.identity["wait_digest"]:
                    raise WaitError("WAIT_PROFILE_DISPOSITION_REQUIRED")
                if digest(previous).removeprefix("sha256:") != child.name:
                    raise WaitError("WAIT_STATE_INVALID")

    @classmethod
    def owner_original(cls, scope: Any) -> Reader:
        """Use only the current fixed original-source invocation, never defaults."""
        from tools.agent_owner_final_admission import _current_scope

        source = _current_scope(scope)
        if source.frames is None:
            raise WaitError("WAIT_FRAME_BINDING_CHANGED")
        # Internal fixed factory: no caller-supplied clock/run/guard/limits/frame.
        result = cls.__new__(cls)
        result._owner_original = True
        result._owner_scope = scope
        result.identity = copy.deepcopy(source.identity)
        result.limits = globals()["limits"](source.limits)
        result.state, result.root = source.state, source.root
        result.directory = source.directory
        result.current_deadline = source.frames.anchor["deadline_at"]
        result.guard = scope.guard
        result.run, result.now = subprocess.run, time.time
        result.monotonic, result.sleep = time.monotonic, time.sleep
        result._depth = 0
        result._active_poll = False
        result._owner_assessing = False
        result._owner_continuing = False
        result._owner_wait_identity = None
        result._storage()
        result._load()
        return result

    def _owner_parent(self) -> int:
        from tools import agent_epic_runtime_cli as guards
        from tools.agent_owner_final_admission import _current_scope

        source = _current_scope(self._owner_scope)
        if self.directory != source.directory:
            raise WaitError("WAIT_STORAGE_UNSAFE")
        guards.secure_directory(self.directory, self.root)
        descriptor = os.open(
            self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY")
            | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK"),
        )
        try:
            guards._final_preseed_private(descriptor, directory=True)
            opened, current = os.fstat(descriptor), self.directory.lstat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise WaitError("WAIT_STORAGE_UNSAFE")
            return descriptor
        except BaseException:
            from tools.agent_epic_wait_frame import _owner_close_all

            _owner_close_all((descriptor,))
            raise

    def _owner_load(self) -> dict[str, Any]:
        from tools import agent_epic_runtime_cli as guards
        from tools.agent_epic_wait_frame import OwnerFrameSources, _owner_close_all

        parent = self._owner_parent()
        try:
            before = os.stat("wait.json", dir_fd=parent, follow_symlinks=False)
            self._private(before)
            if stat.S_IMODE(before.st_mode) != 0o600:
                raise WaitError("WAIT_STORAGE_UNSAFE")
            value = self._validate(guards._final_preseed_read(
                parent, "wait.json", 1_000_000,
            ))
            after = os.stat("wait.json", dir_fd=parent, follow_symlinks=False)
            identity = OwnerFrameSources._identity(before)
            if OwnerFrameSources._identity(after) != identity:
                raise WaitError("WAIT_STATE_INVALID")
            self._owner_wait_identity = identity
            value["deadline_at"] = min(value["deadline_at"], self.current_deadline)
            return value
        finally:
            _owner_close_all((parent,))

    def _owner_confirm(self, parent: int, expected: bytes) -> None:
        """Exactly reread the serialized replacement through its anchored inode."""
        from tools import agent_epic_runtime_cli as guards
        from tools.agent_epic_wait_frame import OwnerFrameSources, _owner_close_all

        descriptor = os.open(
            "wait.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW")
            | getattr(os, "O_NONBLOCK"), dir_fd=parent,
        )
        try:
            guards._final_preseed_private(descriptor, directory=False)
            before = OwnerFrameSources._identity(os.fstat(descriptor))
            if before != self._owner_wait_identity or before[5] != len(expected):
                raise WaitError("WAIT_STATE_INVALID")
            actual = bytearray()
            while len(actual) <= len(expected):
                chunk = os.read(descriptor, min(65536, len(expected) + 1 - len(actual)))
                if not chunk:
                    break
                actual.extend(chunk)
            canonical = os.stat("wait.json", dir_fd=parent, follow_symlinks=False)
            if (
                bytes(actual) != expected
                or OwnerFrameSources._identity(os.fstat(descriptor)) != before
                or OwnerFrameSources._identity(canonical) != before
            ):
                raise WaitError("WAIT_STATE_INVALID")
        finally:
            _owner_close_all((descriptor,))

    def _owner_save(self, value: dict[str, Any]) -> None:
        from tools.agent_epic_wait_frame import OwnerFrameSources, _owner_close_all
        from tools.agent_owner_final_admission import _current_scope

        source = _current_scope(self._owner_scope)
        if source.frames is None or self._owner_wait_identity is None:
            raise WaitError("WAIT_STATE_INVALID")
        raw = json.dumps(
            self._validate(value), sort_keys=True, separators=(",", ":"),
        ).encode()
        if len(raw) > 1_000_000:
            raise WaitError("WAIT_STATE_INVALID")
        parent = descriptor = -1
        name: str | None = None
        try:
            parent = self._owner_parent()
            current = os.stat("wait.json", dir_fd=parent, follow_symlinks=False)
            if OwnerFrameSources._identity(current) != self._owner_wait_identity:
                raise WaitError("WAIT_STATE_INVALID")
            name = ".owner-wait-" + uuid.uuid4().hex + ".tmp"
            descriptor = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK"),
                0o600, dir_fd=parent,
            )
            source.frames._writing_temp = name, descriptor
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise WaitError("WAIT_STATE_INVALID")
                view = view[written:]
            os.fsync(descriptor)
            source.guard()
            latest = os.stat("wait.json", dir_fd=parent, follow_symlinks=False)
            if OwnerFrameSources._identity(latest) != self._owner_wait_identity:
                raise WaitError("WAIT_STATE_INVALID")
            os.replace(name, "wait.json", src_dir_fd=parent, dst_dir_fd=parent)
            name = None
            source.frames._writing_temp = None
            os.fsync(parent)
            self._owner_wait_identity = OwnerFrameSources._identity(
                os.fstat(descriptor),
            )
            source.guard()
            self._owner_confirm(parent, raw)
        finally:
            # Only this invocation's exclusive current temporary is eligible for
            # best-effort cleanup. Prior/foreign residuals are never adopted.
            if name is not None and parent >= 0 and descriptor >= 0:
                try:
                    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    opened = os.fstat(descriptor)
                    if (current.st_dev, current.st_ino) == (
                        opened.st_dev, opened.st_ino,
                    ):
                        os.unlink(name, dir_fd=parent)
                except OSError:
                    pass
            source.frames._writing_temp = None
            _owner_close_all((descriptor, parent))

    @staticmethod
    def _private(info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > 1_000_000
        ):
            raise WaitError("WAIT_STORAGE_UNSAFE")

    def _storage(self, *, create: bool = False) -> None:
        if self._owner_original:
            from tools.agent_owner_final_admission import _current_scope

            source = _current_scope(self._owner_scope)
            if create or source.frames is None:
                raise WaitError("WAIT_STORAGE_UNSAFE")
            source.frames._wait_original()
            return
        try:
            for ancestor in (self.directory, *self.directory.parents):
                if ancestor.is_symlink():
                    raise WaitError("WAIT_STORAGE_UNSAFE")
            secure_directory(self.state, self.root)
            base = self.state / "read-waits"
            if base.exists() and len(list(base.iterdir())) > 4096:
                raise WaitError("WAIT_BUDGET_EXHAUSTED")
            for child in (base, self.directory):
                if child.exists():
                    secure_directory(child, self.root)
                elif create:
                    child.mkdir(mode=0o700)
                    secure_directory(child, self.root)
            for name in ("wait.json", "epic-loop.lock"):
                path = self.directory / name
                if path.exists() or path.is_symlink():
                    self._private(path.lstat())
        except (OSError, DeploymentError):
            raise WaitError("WAIT_STORAGE_UNSAFE") from None

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if self._owner_original:
            self._storage()
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._storage()
        descriptor = os.open(
            self.directory / "epic-loop.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "a+b") as stream:
            self._private(os.fstat(stream.fileno()))
            try:
                loop.RunnerLock._lock(stream)
            except BlockingIOError:
                raise WaitError("WAIT_LOCK_BUSY") from None
            self._depth = 1
            try:
                yield
            finally:
                self._depth = 0
                loop.RunnerLock._unlock(stream)

    def _validate(self, raw: Any) -> dict[str, Any]:
        return validate_wait_record(raw, self.identity, self.limits)

    @staticmethod
    def _item(item: dict[str, Any], digest_key: str, maximum: int) -> None:
        _validate_wait_item(item, digest_key, maximum)

    def _load(self) -> dict[str, Any]:
        if self._owner_original:
            return self._owner_load()
        self._storage()
        path = self.directory / "wait.json"
        if path.exists():
            value = self._validate(private_json(path))
            value["deadline_at"] = min(value["deadline_at"], self.current_deadline)
            return value
        started = _time(self.now())
        return {
            "schema_version": "1.0",
            "identity": copy.deepcopy(self.identity),
            "started_at": started,
            "deadline_at": min(
                started + self.limits["max_phase_seconds"], self.current_deadline
            ),
            "last_seen_at": started,
            "requests": {},
            "ci": None,
        }

    def _save(self, value: dict[str, Any]) -> None:
        if self._owner_original:
            self._owner_save(value)
            return
        self._storage()
        workflow.StateStore(self.directory).save("wait", self._validate(value))

    def inspect(self) -> dict[str, Any]:
        with self._locked():
            return copy.deepcopy(self._load())

    def _check(self, value: dict[str, Any]) -> float:
        current = _time(self.now())
        if current < value["last_seen_at"]:
            raise WaitError("WAIT_CLOCK_ROLLBACK")
        if current >= value["deadline_at"]:
            raise WaitError("WAIT_DEADLINE_EXCEEDED")
        self.guard()
        value["last_seen_at"] = current
        return current

    def _waiting(self, value: dict[str, Any], next_at: float) -> None:
        while True:
            current = self._check(value)
            self._save(value)
            remaining = next_at - current
            if remaining <= 0:
                return
            before = self.monotonic()
            self.sleep(min(1.0, remaining, value["deadline_at"] - current))
            if self.monotonic() < before:
                raise WaitError("WAIT_CLOCK_ROLLBACK")

    def _owner_command(self, argv: Sequence[str]) -> str:
        if not self._owner_assessing or not self._active_poll:
            raise WaitError("WAIT_OUTCOME_UNKNOWN")
        with self._locked():
            value = self._load()
            self._check(value)
            if (
                value["ci"] is None
                or value["ci"]["status"] not in {"RESERVED", "READY"}
                or (value["ci"]["status"] == "READY" and not self._owner_continuing)
                or any(v["status"] != "READY" for v in value["requests"].values())
                or len(value["requests"]) >= self.limits["max_read_commands"]
            ):
                raise WaitError("WAIT_OUTCOME_UNKNOWN")
            nonci = value["ci"]["status"] == "READY"
            if nonci:
                from tools.agent_owner_final_admission import _owner_nonci_read

                _owner_nonci_read(self._owner_scope, tuple(argv))
            item = {
                "command_digest": digest(tuple(argv)), "attempts": 1,
                "status": "RESERVED", "category": None, "next_check_at": None,
            }
            value["requests"][str(len(value["requests"]) + 1)] = item
            self._save(value)
            self.guard()
            dispatch = _time(self.now())
            if dispatch < value["last_seen_at"]:
                raise WaitError("WAIT_CLOCK_ROLLBACK")
            if dispatch >= value["deadline_at"]:
                raise WaitError("WAIT_DEADLINE_EXCEEDED")
            try:
                completed = self.run(
                    list(argv), capture_output=True, text=True, check=False,
                    timeout=min(120.0, value["deadline_at"] - dispatch),
                )
            except (subprocess.TimeoutExpired, OSError):
                # No result proves whether the remote read completed.
                raise WaitError("WAIT_OUTCOME_UNKNOWN") from None
            if (
                completed.returncode != 0 or not isinstance(completed.stdout, str)
                or len(completed.stdout) > 1_000_000
            ):
                item["status"] = "FAILED"
                value["ci"]["status"] = "FAILED"
                self._save(value)
                raise WaitError("WAIT_READ_FAILED")
            output = completed.stdout
            if nonci:
                from tools.agent_owner_final_admission import _owner_nonci_observe

                _owner_nonci_observe(self._owner_scope, tuple(argv), output)
            item["status"] = "READY"
            self._check(value)
            self._save(value)
            return output

    def owner_assess_once(self) -> Mapping[str, Any]:
        from tools.agent_owner_final_admission import _assess_current

        if not self._owner_original or self._owner_assessing:
            raise WaitError("WAIT_OUTCOME_UNKNOWN")
        with self._locked():
            value = self._load()
            self._check(value)
            ci = value["ci"]
            if (
                ci is None or ci["status"] != "READY"
                or ci["attempts"] >= self.limits["max_ci_polls"]
                or any(v["status"] != "READY" for v in value["requests"].values())
            ):
                raise WaitError("WAIT_OUTCOME_UNKNOWN")
            ci.update(
                attempts=ci["attempts"] + 1, status="RESERVED", next_check_at=None,
            )
            self._save(value)
            self._owner_assessing = self._active_poll = True
            try:
                try:
                    result = _assess_current(self._owner_scope)
                except loop.PhaseBlocked as error:
                    # A closed deterministic source rejection is not a retryable
                    # transport outcome. Other/uncertain failures stay RESERVED.
                    if error.machine_code in {
                        "FINAL_APPROVAL_INVALID", "FINAL_STAGE_GATE_INVALID",
                        "SESSION_SCOPE_CHANGED",
                    }:
                        rejected = self._load()
                        self._check(rejected)
                        rejected["ci"].update(status="FAILED", next_check_at=None)
                        self._save(rejected)
                    raise
                latest = self._load()
                self._check(latest)
                if result.get("status") != "PASS":
                    latest["ci"].update(status="FAILED", next_check_at=None)
                    self._save(latest)
                    raise WaitError("WAIT_READ_FAILED")
                latest["ci"].update(status="READY", next_check_at=None)
                self._save(latest)
                return result
            finally:
                self._owner_assessing = self._active_poll = False

    def owner_continue_once(self) -> Mapping[str, Any]:
        """Fixed normal Session continuation; not a caller-selected assessor."""
        from tools.agent_owner_final_admission import _continue_current, _current_scope

        if not self._owner_original or self._owner_assessing:
            raise WaitError("WAIT_OUTCOME_UNKNOWN")
        source = _current_scope(self._owner_scope)
        if source.normal_session is None or source.session is not source.normal_session:
            raise WaitError("WAIT_READ_INELIGIBLE")
        with self._locked():
            value = self._load()
            self._check(value)
            ci = value["ci"]
            if (
                ci is None or ci["status"] != "READY"
                or ci["attempts"] >= self.limits["max_ci_polls"]
                or any(v["status"] != "READY" for v in value["requests"].values())
            ):
                raise WaitError("WAIT_OUTCOME_UNKNOWN")
            ci.update(
                attempts=ci["attempts"] + 1, status="RESERVED", next_check_at=None,
            )
            self._save(value)
            self._owner_assessing = self._active_poll = self._owner_continuing = True
            try:
                result = _continue_current(self._owner_scope)
                latest = self._load()
                self._check(latest)
                if result.get("status") == "ESCALATE":
                    # Preserve an uncertain read/CI reservation verbatim. An
                    # already verified B may be READY, but no effect is replayed.
                    return result
                if result.get("status") not in {
                    "WAITING_OWNER_FINAL_APPROVAL", "ROADMAP_DONE",
                }:
                    latest["ci"].update(status="FAILED", next_check_at=None)
                    self._save(latest)
                    raise WaitError("WAIT_READ_FAILED")
                latest["ci"].update(status="READY", next_check_at=None)
                self._save(latest)
                return result
            except loop.PhaseBlocked as error:
                if error.machine_code in {
                    "FINAL_APPROVAL_INVALID", "FINAL_STAGE_GATE_INVALID",
                    "SESSION_SCOPE_CHANGED",
                }:
                    rejected = self._load()
                    self._check(rejected)
                    rejected["ci"].update(status="FAILED", next_check_at=None)
                    self._save(rejected)
                raise
            finally:
                self._owner_assessing = self._active_poll = False
                self._owner_continuing = False

    def owner_next_phase(self, *, pending: bool = False) -> None:
        """Consume the next original CI allowance; never create another identity."""
        from tools.agent_owner_final_admission import _current_scope

        source = _current_scope(self._owner_scope)
        if (
            not self._owner_original or not self._owner_continuing
            or not self._owner_assessing or not self._active_poll
            or source.normal_session is None
            or source.session is not source.normal_session
            or type(pending) is not bool
        ):
            raise WaitError("WAIT_READ_INELIGIBLE")
        with self._locked():
            value = self._load()
            self._check(value)
            ci = value["ci"]
            if (
                ci is None or ci["status"] not in {"RESERVED", "READY"}
                or ci["attempts"] >= self.limits["max_ci_polls"]
                or any(v["status"] != "READY" for v in value["requests"].values())
            ):
                raise WaitError("WAIT_CI_EXHAUSTED")
            if pending:
                ci.update(
                    status="WAITING",
                    next_check_at=_time(self.now()) + reads.DELAYS[ci["attempts"] - 1],
                )
                self._save(value)
                self._waiting(value, ci["next_check_at"])
            source._verified_b = None
            source._observed_merge_sha = None
            ci.update(
                attempts=ci["attempts"] + 1, status="RESERVED", next_check_at=None,
            )
            self._save(value)
            self.guard()

    def owner_gate_completed(self) -> None:
        """Complete the actual verified B; expose only its closed reconcile reads."""
        from tools.agent_owner_final_admission import _current_scope, _owner_nonci_guard

        if not self._owner_original or not self._owner_continuing:
            raise WaitError("WAIT_READ_INELIGIBLE")
        source = _current_scope(self._owner_scope)
        ticket = _owner_nonci_guard(source)
        with self._locked():
            value = self._load()
            self._check(value)
            ci = value["ci"]
            if (
                ci is None or ci["status"] != "RESERVED"
                or ci["attempts"] != ticket.attempt
                or ci["identity_digest"] != ticket.ci_identity
                or any(v["status"] != "READY" for v in value["requests"].values())
            ):
                raise WaitError("WAIT_OUTCOME_UNKNOWN")
            ci.update(status="READY", next_check_at=None)
            self._save(value)
            self.guard()

    def command(self, argv: Sequence[str]) -> str:
        repo = self.identity["repository"]
        if (
            not reads.read_only(argv)
            or (
                argv[1] == "api"
                and not (
                    (len(argv) == 3 and argv[2] == f"repos/{repo}")
                    or any(arg.startswith(f"repos/{repo}/") for arg in argv[2:])
                )
            )
            or (argv[1] != "api" and argv[5] != repo)
        ):
            raise WaitError("WAIT_READ_INELIGIBLE")
        if self._owner_original:
            return self._owner_command(argv)
        command_digest = digest(tuple(argv))
        with self._locked():
            value = self._load()
            self._check(value)
            if any(
                item["status"] == "RESERVED" for item in value["requests"].values()
            ) or (
                value["ci"] is not None
                and value["ci"]["status"] == "RESERVED"
                and not self._active_poll
            ):
                raise WaitError("WAIT_OUTCOME_UNKNOWN")
            matches = [
                item
                for item in value["requests"].values()
                if item["command_digest"] == command_digest
                and item["status"] == "WAITING"
            ]
            if len(matches) > 1:
                raise WaitError("WAIT_STATE_INVALID")
            if any(
                item["command_digest"] == command_digest and item["status"] == "FAILED"
                for item in value["requests"].values()
            ):
                raise WaitError("WAIT_READ_FAILED")
            if matches:
                item = matches[0]
            else:
                if len(value["requests"]) >= self.limits["max_read_commands"]:
                    raise WaitError("WAIT_BUDGET_EXHAUSTED")
                item = {
                    "command_digest": command_digest,
                    "attempts": 0,
                    "status": "READY",
                    "category": None,
                    "next_check_at": None,
                }
                value["requests"][str(len(value["requests"]) + 1)] = item
            while True:
                if item["status"] == "WAITING":
                    self._waiting(value, item["next_check_at"])
                if item["attempts"] >= self.limits["max_read_attempts"]:
                    raise WaitError("WAIT_READ_EXHAUSTED")
                current = self._check(value)
                item.update(
                    attempts=item["attempts"] + 1,
                    status="RESERVED",
                    category=None,
                    next_check_at=None,
                )
                if self._active_poll:
                    value["ci"].update(status="RESERVED", next_check_at=None)
                self._save(value)
                self.guard()
                completed = current

                def complete() -> None:
                    nonlocal completed
                    completed = _time(self.now())

                try:
                    output = reads.attempt(
                        argv,
                        run=self.run,
                        timeout=min(120.0, value["deadline_at"] - current),
                        on_complete=complete,
                    )
                except reads.ReadFailure as error:
                    if (
                        error.category == "unknown"
                        or item["attempts"] == self.limits["max_read_attempts"]
                    ):
                        item.update(status="FAILED", category=None, next_check_at=None)
                        self._save(value)
                        raise WaitError(
                            "WAIT_READ_FAILED"
                            if error.category == "unknown"
                            else "WAIT_READ_EXHAUSTED"
                        ) from None
                    item.update(
                        status="WAITING",
                        category=error.category,
                        next_check_at=completed + reads.DELAYS[item["attempts"] - 1],
                    )
                    if self._active_poll:
                        value["ci"].update(
                            status="WAITING", next_check_at=item["next_check_at"]
                        )
                    self._save(value)
                else:
                    item.update(status="READY", category=None, next_check_at=None)
                    self._save(value)
                    self._check(value)
                    self._save(value)
                    return output

    def poll_ci(
        self, identity: Mapping[str, Any], assess: Callable[[], Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        """Only pinned callers supply the read-only exact-SHA assessor callback."""
        if self._owner_original:
            raise WaitError("WAIT_READ_INELIGIBLE")
        binding = digest(dict(identity))
        with self._locked():
            value = self._load()
            self._check(value)
            ci = value["ci"]
            if ci is not None and ci["identity_digest"] != binding:
                raise WaitError("WAIT_CI_IDENTITY_CHANGED")
            if (ci is not None and ci["status"] == "RESERVED") or any(
                item["status"] == "RESERVED" for item in value["requests"].values()
            ):
                raise WaitError("WAIT_OUTCOME_UNKNOWN")
            if ci is not None and ci["status"] == "FAILED":
                raise WaitError("WAIT_CI_FAILED")
            if ci is None:
                ci = {
                    "identity_digest": binding,
                    "attempts": 0,
                    "status": "READY",
                    "next_check_at": None,
                }
                value["ci"] = ci
            while True:
                if ci["status"] == "WAITING":
                    self._waiting(value, ci["next_check_at"])
                if ci["attempts"] >= self.limits["max_ci_polls"]:
                    raise WaitError("WAIT_CI_EXHAUSTED")
                ci.update(
                    attempts=ci["attempts"] + 1,
                    status="RESERVED",
                    next_check_at=None,
                )
                self._save(value)
                self.guard()
                self._active_poll = True
                try:
                    result = assess()
                except Exception:
                    value = self._load()
                    ci = value["ci"]
                    ci.update(status="FAILED", next_check_at=None)
                    self._save(value)
                    raise
                finally:
                    self._active_poll = False
                value = self._load()
                ci = value["ci"]
                if result.get("machine_code") == "CI_PENDING":
                    if ci["attempts"] >= self.limits["max_ci_polls"]:
                        ci.update(status="FAILED", next_check_at=None)
                        self._save(value)
                        raise WaitError("WAIT_CI_EXHAUSTED")
                    ci.update(
                        status="WAITING",
                        next_check_at=_time(self.now())
                        + reads.DELAYS[ci["attempts"] - 1],
                    )
                    self._save(value)
                    continue
                ci.update(
                    status="READY"
                    if result.get("status") in {"pass", "PASS"}
                    else "FAILED",
                    next_check_at=None,
                )
                self._save(value)
                self._check(value)
                self._save(value)
                return result
