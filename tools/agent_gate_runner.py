"""Independent timer + typed event transport for reviewed local test units.

This is an internal operator component, not a provider/runtime launcher.
"""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.agent_gate_journal import EVENT_LIMIT, GateBlocked, Journal, digest
from tools.agent_gate_state import State

INTERVAL = 900.0


def boot_identity() -> str:
    if os.name != "posix":
        raise GateBlocked("UNSUPPORTED_PLATFORM")
    path = Path("/proc/sys/kernel/random/boot_id")
    data = path.read_bytes()
    if not 1 <= len(data) <= 64:
        raise GateBlocked("BOOT_IDENTITY_INVALID")
    import hashlib

    return hashlib.sha256(data).hexdigest()


class Checkpoints:
    """Clock-driven saving; no dependency on a test finishing or producing output."""

    def __init__(
        self,
        save: Callable[[float, float], None],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.clock = clock
        self.save = save
        self.started = clock()
        self.due = self.started + INTERVAL
        self.last_success = self.started
        self.save(self.started, 0.0)

    def tick(self) -> None:
        now = self.clock()
        if now >= self.due:
            overdue = max(0.0, now - self.due)
            self.save(now, overdue)
            self.last_success = now
            self.due += (int((now - self.due) // INTERVAL) + 1) * INTERVAL
            if overdue > 5:
                raise GateBlocked("CHECKPOINT_OVERDUE")

    def finish(self) -> None:
        now = self.clock()
        self.save(now, max(0.0, now - self.due))
        self.last_success = now


def _group_empty(group: int) -> bool:
    """Only an existence probe; never infer ownership or signal a group from it."""
    try:
        getattr(os, "killpg")(group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def child(
    command: Sequence[str],
    *,
    root: Path,
    environment: Mapping[str, str],
    ids: list[str],
    receive: Callable[[dict[str, Any]], None],
    tick: Callable[[], None],
    deadline: float,
    spawn: Callable[[int], None],
    before_spawn: Callable[[], None] = lambda: None,
    observe: Callable[[dict[str, Any]], None] = lambda value: None,
) -> None:
    """One child/session, bounded report+ack pipes. No raw output retained."""
    if os.name != "posix":
        raise GateBlocked("UNSUPPORTED_PLATFORM")
    encoded_ids = json.dumps(ids)
    if len(encoded_ids.encode()) > 24 * 1024 * 1024:
        raise GateBlocked("UNIT_ARGUMENT_CAPACITY")
    event_read, event_write = os.pipe()
    ack_read, ack_write = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    buffer = b""
    eof = False
    ids_file = tempfile.TemporaryFile(mode="w+b")
    ids_file.write(encoded_ids.encode())
    ids_file.seek(0)
    env = dict(environment)
    env.update(
        PGW_GATE_EVENT_FD=str(event_write),
        PGW_GATE_ACK_FD=str(ack_read),
        PGW_GATE_IDS_FD=str(ids_file.fileno()),
    )
    try:
        before_spawn()
        process = subprocess.Popen(
            list(command),
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(event_write, ack_read, ids_file.fileno()),
            start_new_session=True,
        )
        spawn(process.pid)
        os.close(event_write)
        event_write = -1
        os.close(ack_read)
        ack_read = -1
        while True:
            tick()
            if time.monotonic() >= deadline:
                raise GateBlocked("CHILD_DEADLINE")
            readable = [] if eof else select.select([event_read], [], [], 0.1)[0]
            if readable:
                data = os.read(event_read, EVENT_LIMIT)
                if not data:
                    eof = True
                buffer += data
                if len(buffer) > EVENT_LIMIT:
                    raise GateBlocked("REPORTER_EVENT_CAPACITY")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise GateBlocked("REPORTER_EVENT_INVALID")
                    receive(event)
                    if not select.select([], [ack_write], [], 1)[1]:
                        raise GateBlocked("REPORTER_ACK_TIMEOUT")
                    os.write(ack_write, b"+")
            code = process.poll()
            if code is not None and eof:
                if buffer or code != 0 or not _group_empty(process.pid):
                    raise GateBlocked("CHILD_TERMINAL_OR_OWNERSHIP_UNKNOWN")
                return
            if eof:
                time.sleep(0.05)
    finally:
        # Observe only actual parent facts; EMPTY is not a CLEAN/authority grant.
        before = process.poll() if process is not None else None
        cleanup_attempt = "NONE"
        cleanup_error = False
        try:
            # Signals remain limited to our unreaped direct child, never a group.
            if process is not None and process.poll() is None:
                cleanup_attempt = "TERMINATE"
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    cleanup_attempt = "TERMINATE_THEN_KILL"
                    process.kill()
                    process.wait(timeout=2)
        except BaseException:
            cleanup_error = True
            raise
        finally:
            after = process.poll() if process is not None else None
            group = "NOT_SPAWNED"
            if process is not None:
                try:
                    group = "EMPTY" if _group_empty(process.pid) else "PRESENT"
                except OSError:
                    group = "UNKNOWN"
            try:
                ids_file.close()
                for fd in (event_read, event_write, ack_read, ack_write):
                    if fd >= 0:
                        os.close(fd)
            finally:
                observe(
                    {
                        "observed": True,
                        "spawned": process is not None,
                        "pid": process.pid if process is not None else None,
                        "exit_before_cleanup": before,
                        "exit_after_cleanup": after,
                        "direct_child_reaped": process is not None
                        and after is not None,
                        "group": group,
                        "cleanup_attempt": cleanup_attempt,
                        "cleanup_error": cleanup_error,
                    }
                )


class Runner:
    """Requires an approved profile and fresh identity callback, not a hash flag.

    The caller owns verifying the reviewed partition/toolchain/closed environment
    and initial finite authority. The class never fabricates these approvals.
    """

    def __init__(
        self,
        store: Journal,
        plan: dict[str, Any],
        *,
        identity: Callable[[], str],
        expires_at: float,
        deadline: float,
        clock: Callable[[], float] = time.monotonic,
        allow_recovery: bool = False,
    ) -> None:
        self.store = store
        self.state = State(plan)
        self.identity = identity
        self.deadline = deadline
        self.expires_at = expires_at
        self.clock = clock
        launch = store.genesis["launch"]
        if launch.get("plan") != plan or launch.get("expires_at") != expires_at:
            raise GateBlocked("LAUNCH_BINDING_INVALID")
        if launch.get("deadline") != deadline or launch.get("boot") != boot_identity():
            raise GateBlocked("ORIGINAL_CLOCK_BINDING_INVALID")
        self.max_collections = launch["max_collections"]
        if type(self.max_collections) is not int or not 1 <= self.max_collections <= 16:
            raise GateBlocked("COLLECTION_BUDGET_INVALID")
        self.inventory_verified = False
        self.expected = launch["fingerprint"]
        self.guard()
        for index, event in enumerate(store.events):
            if event.get("kind") == "recovery_dispatch":
                from tools.agent_gate_recovery import validate

                validate(
                    event["permit"],
                    launch=launch,
                    genesis=digest(store.genesis),
                    frontier=store.frontiers[index],
                    sequence=index,
                    state=self.state,
                    authorized_at=event["authorized_at"],
                )
            if (
                event.get("kind") == "terminal"
                and event.get("fingerprint") != self.expected
            ):
                raise GateBlocked("TERMINAL_FINGERPRINT_CHANGED")
            self.state.apply(event)
        # A fresh Runner cannot silently retry an original unresolved intent.
        if not allow_recovery:
            self.state.remaining()
        self._check_clock(original_only=allow_recovery)
        self.timer = Checkpoints(self._save, clock=clock)

    def _check_clock(self, *, original_only: bool = False) -> None:
        expiries = [self.expires_at]
        if not original_only and self.state.reservation_expires_at is not None:
            expiries.append(self.state.reservation_expires_at)
        if not original_only and self.state.collection_expires_at is not None:
            expiries.append(self.state.collection_expires_at)
        if time.time() >= min(expiries) or self.clock() >= self.deadline:
            raise GateBlocked("RUN_EXPIRED")

    def guard(self, *, original_only: bool = False) -> None:
        if self.store.poisoned:
            raise GateBlocked("STORE_POISONED")
        self._check_clock(original_only=original_only)
        if self.identity() != self.expected:
            raise GateBlocked("FINGERPRINT_CHANGED")
        self._check_clock(original_only=original_only)  # Identity I/O consumes time.

    def _save(self, now: float, overdue: float) -> None:
        self.store.checkpoint(
            {
                "observed_monotonic": now,
                "observed_wall": time.time(),
                "overdue_seconds": overdue,
                "state": self.state.snapshot(),
            }
        )

    def _report(self, event: dict[str, Any]) -> None:
        if event.get("kind") not in ("collected", "started", "report", "session"):
            raise GateBlocked("REPORTER_ROLE_VIOLATION")
        self._receive(event)

    def _receive(self, event: dict[str, Any]) -> None:
        # State validation precedes persistence; acknowledged only once committed.
        if self.store.poisoned:
            raise GateBlocked("STORE_POISONED")
        try:
            self.state.apply(event)
            self.store.append(event)
        except BaseException:
            self.store.poisoned = True
            raise

    def tick(self) -> None:
        self._check_clock()
        self.timer.tick()

    def _spawn(self, pid: int) -> None:
        self._check_clock()
        self._receive(
            {
                "kind": "spawn",
                "pid": pid,
                "group": pid,
                "boot": self.store.genesis["launch"]["boot"],
                "scope": (
                    "inventory"
                    if self.state.collecting
                    else "quality"
                    if self.state.quality_active is not None
                    else "unit"
                ),
            }
        )

    def _supervise_child(
        self,
        command: Sequence[str],
        *,
        root: Path,
        environment: Mapping[str, str],
        ids: list[str],
        receive: Callable[[dict[str, Any]], None],
    ) -> None:
        observed: dict[str, Any] = {
            "observed": False,
            "spawned": None,
            "pid": None,
            "exit_before_cleanup": None,
            "exit_after_cleanup": None,
            "direct_child_reaped": None,
            "group": "UNKNOWN",
            "cleanup_attempt": "UNKNOWN",
            "cleanup_error": None,
        }
        try:
            child(
                command,
                root=root,
                environment=environment,
                ids=ids,
                receive=receive,
                tick=self.tick,
                deadline=self.deadline,
                spawn=self._spawn,
                before_spawn=self._check_clock,
                observe=observed.update,
            )
            self.guard()
        except BaseException as error:
            if not self.store.poisoned:
                from tools.agent_gate_recovery import unresolved

                scope, index, attempt = unresolved(self.state)
                category = "OTHER"
                if isinstance(error, GateBlocked):
                    category = "GATE_BLOCKED"
                elif isinstance(error, OSError):
                    category = "OS_ERROR"
                elif isinstance(error, ValueError):
                    category = "INVALID_REPORT"
                elif isinstance(error, subprocess.TimeoutExpired):
                    category = "TIMEOUT"
                elif isinstance(error, (KeyboardInterrupt, SystemExit)):
                    category = "INTERRUPTED"
                reason = "OTHER"
                allowed = {
                    "REPORTER_ROLE_VIOLATION",
                    "RUN_EXPIRED",
                    "FINGERPRINT_CHANGED",
                    "CHILD_DEADLINE",
                    "CHILD_TERMINAL_OR_OWNERSHIP_UNKNOWN",
                    "REPORTER_EVENT_CAPACITY",
                    "REPORTER_EVENT_INVALID",
                    "REPORTER_ACK_TIMEOUT",
                    "UNEXPECTED_QUALITY_EVENT",
                }
                if (
                    isinstance(error, GateBlocked)
                    and error.args
                    and isinstance(error.args[0], str)
                    and error.args[0] in allowed
                ):
                    reason = error.args[0]
                try:
                    self._receive(
                        {
                            "kind": "failure_observation",
                            "scope": scope,
                            "index": index,
                            "attempt": attempt,
                            "category": category,
                            "reason": reason,
                            "observed": observed,
                        }
                    )
                    self.timer.finish()
                except BaseException as diagnostic_error:
                    raise GateBlocked(
                        "FAILURE_OBSERVATION_FAILED"
                    ) from diagnostic_error
            raise

    def verify_inventory(
        self, *, root: Path, python: Path, environment: Mapping[str, str]
    ) -> None:
        self.guard()
        if self.state.collections >= self.max_collections:
            raise GateBlocked("COLLECTION_BUDGET_EXHAUSTED")
        self._receive(
            {"kind": "inventory_dispatch", "attempt": self.state.collections + 1}
        )
        self._execute_inventory(root=root, python=python, environment=environment)

    def _execute_inventory(
        self, *, root: Path, python: Path, environment: Mapping[str, str]
    ) -> None:
        command = [
            str(python),
            "-m",
            "pytest",
            "-q",
            "--collect-only",
            "-p",
            "tools.agent_gate_pytest",
        ]
        self._supervise_child(
            command,
            root=root,
            environment=environment,
            ids=self.state.plan["inventory"],
            receive=self._report,
        )
        self._receive({"kind": "inventory_terminal", "ownership": "CLEAN"})
        self.inventory_verified = True
        self.timer.finish()

    def run_unit(
        self, unit: int, *, root: Path, python: Path, environment: Mapping[str, str]
    ) -> None:
        self.guard()
        if not self.inventory_verified:
            raise GateBlocked("FRESH_INVENTORY_REQUIRED")
        if unit not in self.state.remaining():
            raise GateBlocked("UNIT_NOT_REMAINING")
        self._receive({"kind": "dispatch", "unit": unit, "attempt": 1})
        self._execute_unit(unit, root=root, python=python, environment=environment)

    def _execute_unit(
        self, unit: int, *, root: Path, python: Path, environment: Mapping[str, str]
    ) -> None:
        command = [
            str(python),
            "-m",
            "pytest",
            "-q",
            "-p",
            "tools.agent_gate_pytest",
            *self.state.plan["units"][unit],
        ]
        self._supervise_child(
            command,
            root=root,
            environment=environment,
            ids=self.state.plan["units"][unit],
            receive=self._report,
        )
        self._receive(
            {
                "kind": "terminal",
                "exit_code": 0,
                "ownership": "CLEAN",
                "fingerprint": self.expected,
            }
        )
        self.timer.finish()

    def run_quality(
        self, name: str, *, root: Path, python: Path, environment: Mapping[str, str]
    ) -> None:
        self.guard()
        commands = {
            "ruff": ["ruff", "check", "."],
            "mypy": ["mypy", "."],
            "precommit": ["pre_commit", "run", "--all-files"],
        }
        if name not in commands:
            raise GateBlocked("QUALITY_NAME_INVALID")
        self._receive({"kind": "quality_dispatch", "name": name})

        self._execute_quality(
            commands[name], root=root, python=python, environment=environment, name=name
        )

    def _execute_quality(
        self,
        command: list[str],
        *,
        root: Path,
        python: Path,
        environment: Mapping[str, str],
        name: str,
    ) -> None:
        def reject(event: dict[str, Any]) -> None:
            raise GateBlocked("UNEXPECTED_QUALITY_EVENT")

        self._supervise_child(
            [str(python), "-m", *command],
            root=root,
            environment=environment,
            ids=[],
            receive=reject,
        )
        self._receive(
            {"kind": "quality_terminal", "name": name, "fingerprint": self.expected}
        )
        self.timer.finish()

    def recover(
        self,
        permit: dict[str, Any],
        approved_digest: str,
        *,
        root: Path,
        python: Path,
        environment: Mapping[str, str],
    ) -> None:
        from tools.agent_gate_recovery import disposition_event

        self.guard(original_only=True)
        event = disposition_event(
            self.store, self.state, permit, approved_digest, time.time()
        )
        self._receive(event)  # One fsync-committed consume+reservation before spawn.
        self.guard()  # The NEW reservation, never the old lease, controls spawn.
        scope = permit["scope"]
        if scope != "inventory":
            self._receive(
                {
                    "kind": "recovery_inventory_dispatch",
                    "attempt": self.state.collections + 1,
                }
            )
        self._execute_inventory(root=root, python=python, environment=environment)
        if self.state.active is not None:
            self._execute_unit(
                self.state.active, root=root, python=python, environment=environment
            )
        elif self.state.quality_active is not None:
            name = self.state.quality_active
            commands = {
                "ruff": ["ruff", "check", "."],
                "mypy": ["mypy", "."],
                "precommit": ["pre_commit", "run", "--all-files"],
            }
            self._execute_quality(
                commands[name],
                root=root,
                python=python,
                environment=environment,
                name=name,
            )

    def complete(self) -> bool:
        self.guard()
        return (
            not self.state.remaining()
            and set(self.state.quality) == {"ruff", "mypy", "precommit"}
            and all(value is not None for value in self.state.quality.values())
        )

    def pause(self) -> None:
        self.guard()
        self._receive({"kind": "pause"})
        self.timer.finish()
