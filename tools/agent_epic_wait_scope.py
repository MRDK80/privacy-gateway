"""Pinned invocation and local authority guards for durable stage waits."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tools import agent_epic_loop as loop
from tools import agent_epic_recovery as recovery
from tools import agent_epic_runtime_cli as guards
from tools import agent_epic_wait as wait
from tools import agent_epic_wait_frame as frames
from tools import agent_orchestrate as workflow


def command_identity(command: Sequence[str]) -> tuple[str, ...]:
    if len(command) < 2:
        raise frames.FrameError("WAIT_FRAME_BINDING_CHANGED")
    return (
        str(Path(command[0]).resolve()),
        str(Path(command[1]).resolve()),
        *command[2:],
    )


class Scope:
    _retained_anchor: dict[str, Any]

    def __init__(
        self,
        state: Path,
        root: Path,
        frame: dict[str, Any],
        *,
        initial: bool = False,
        read_only: bool = False,
    ) -> None:
        self.state = guards.managed_state(state)
        self.root = root
        self.frame = frame
        self.initial = initial
        self.read_only = read_only
        self._owner_original = False
        self._retained_owner = False
        self._session_origin: str | None = None
        self.config = guards.private_json(self.state / "epic-runtime.json")
        self.adapter = guards.private_json(self.state / "runtime-adapter.json")
        self.mandate = guards.private_json(self.state / "mandate.json")
        profile = guards.wait_profile_guard(self.state, self.config, self.mandate)
        if profile is None:
            raise frames.FrameError("WAIT_PROFILE_REQUIRED")
        self.profile = profile
        self._readers: dict[str, wait.Reader] = {}
        self._contexts: set[str] = set()
        self._context_stack: list[str] = []
        control = self.state / "launch-control.json"
        self.control = guards.private_json(control) if control.exists() else None
        self.guard()

    @classmethod
    def retained_owner(cls, state: Path, root: Path) -> Scope | None:
        """Pending no-create route: cannot read, publish, or create a frame."""
        from tools.agent_owner_final_admission import _retained_sources

        saved = _retained_sources(state, root)
        if saved is None:
            return None
        result = cls.__new__(cls)
        result.state, result.root = state, root
        result.config = saved["sources"]["config"]
        result.mandate = saved["sources"]["mandate"]
        profile = guards.wait_profile_guard(state, result.config, result.mandate)
        if profile is None:
            raise frames.FrameError("WAIT_PROFILE_REQUIRED")
        result.profile = profile
        result.adapter = guards.private_json(state / "runtime-adapter.json")
        result.control = guards.private_json(state / "launch-control.json")
        result._retained_anchor = saved["final"]["owner_wait"]
        result._owner_original = False
        result._retained_owner = True
        result._session_origin = None
        result.initial = False
        result.read_only = False
        # There is deliberately no claimed frame here. Every frame/reader API
        # rejects this pending object; the fixed locked factory replaces it.
        result.frame = {}
        result._readers = {}
        result._contexts = set()
        result._context_stack = []
        result.guard()
        return result

    @classmethod
    def owner_original(cls, state: Path, root: Path) -> Scope:
        """Borrow only the already locked, source-verified original invocation."""
        from tools.agent_owner_final_admission import _current_sources

        source = _current_sources(state, root)
        if source.scope is not None or source.frames is None:
            raise frames.FrameError("WAIT_FRAME_BINDING_CHANGED")
        result = cls(
            state, root, dict(source.frames.frame),
            initial=source.frames.frame["parent_frame_digest"] is None,
        )
        result._owner_original = True
        source.scope = result
        result.guard()
        return result

    @classmethod
    def for_root(
        cls, state: Path, root: Path, *, read_only: bool = False
    ) -> Scope | None:
        seed = guards.managed_state(state)
        path = seed / "epic-runtime.json"
        if not path.exists():
            return None
        config = guards.private_json(path)
        if config.get("schema_version") not in {"4.0", "5.0", "6.0", "7.0", "8.0"}:
            return None
        guards.managed_launch_guard(seed, root, read_only=read_only, allow_initial=True)
        mandate = guards.private_json(seed / "mandate.json")
        guards.bootstrap_authority(config, mandate, epic=True)
        profile = guards.wait_profile_guard(seed, config, mandate)
        assert profile is not None
        adapter = guards.private_json(seed / "runtime-adapter.json")
        now = time.time()
        deadline = min(
            now + profile["limits"]["max_phase_seconds"],
            now + adapter["timeout_seconds"],
            mandate["expires_at"],
            config["started_at"] + mandate["limits"]["max_duration_seconds"],
        )
        value, descriptor = frames.create(
            state=seed,
            root=root,
            repository=mandate["repository"],
            epic=mandate["epic"],
            policy_sha=mandate["policy_sha"],
            runtime_config_digest=guards.launch_config_digest(config, adapter),
            command=("DISCOVERY", "LOCAL"),
            deadline_at=deadline,
            now=now,
        )
        os.close(descriptor)
        return cls(seed, root, value, initial=True, read_only=read_only)

    @classmethod
    def for_session_wake(cls, state: Path, root: Path) -> Scope | None:
        """Fixed build_session(wake=True) route; generic roots cannot publish."""
        result = cls.for_root(state, root)
        if result is not None:
            result._session_origin = "QUOTA_WAKE"
        return result

    @classmethod
    def inherited(
        cls, state: Path, root: Path, *, read_only: bool = False
    ) -> Scope | None:
        seed = guards.managed_state(state)
        path = seed / "epic-runtime.json"
        if not path.exists():
            frames.clear(os.environ)
            return None
        config = guards.private_json(path)
        if config.get("schema_version") not in {"4.0", "5.0", "6.0", "7.0", "8.0"}:
            frames.clear(os.environ)
            return None
        mandate = guards.private_json(seed / "mandate.json")
        adapter = guards.private_json(seed / "runtime-adapter.json")
        value = frames.consume(
            state=seed,
            root=root,
            environment=os.environ,
            repository=mandate["repository"],
            epic=mandate["epic"],
            policy_sha=mandate["policy_sha"],
            runtime_config_digest=guards.launch_config_digest(config, adapter),
            command=command_identity((sys.executable, sys.argv[0], *sys.argv[1:])),
            now=time.time(),
        )
        result = cls(seed, root, value, read_only=read_only)
        result._session_origin = "EPIC_COMMAND"
        return result

    def guard(self) -> None:
        if self._retained_owner:
            from tools.agent_owner_final_admission import _retained_sources

            saved = _retained_sources(self.state, self.root)
            current = guards.private_json(self.state / "launch-control.json")
            if (
                saved is None or saved["sources"]["config"] != self.config
                or saved["sources"]["mandate"] != self.mandate
                or saved["final"]["owner_wait"] != self._retained_anchor
                or current != self.control or current.get("state") != "RUNNING"
                or wait.digest(current) != self._retained_anchor["control_digest"]
            ):
                raise frames.FrameError("WAIT_FRAME_BINDING_CHANGED")
            now = time.time()
            if not self._retained_anchor["stage_started_at"] <= now < min(
                self._retained_anchor["deadline_at"], self.mandate["expires_at"],
                self.config["started_at"]
                + self.mandate["limits"]["max_duration_seconds"],
            ):
                raise frames.FrameError("WAIT_INVOCATION_EXPIRED")
            guards.managed_launch_guard(self.state, self.root)
            guards.bootstrap_authority(self.config, self.mandate, epic=True)
            return
        if self._owner_original:
            from tools.agent_owner_final_admission import _current_scope

            _current_scope(self)
            return
        now = time.time()
        if not self.frame["started_at"] <= now < self.frame["deadline_at"]:
            raise frames.FrameError("WAIT_INVOCATION_EXPIRED")
        config = guards.private_json(self.state / "epic-runtime.json")
        adapter = guards.private_json(self.state / "runtime-adapter.json")
        if (
            config != self.config
            or guards.launch_config_digest(config, adapter)
            != self.frame["runtime_config_digest"]
        ):
            raise frames.FrameError("WAIT_FRAME_BINDING_CHANGED")
        mandate = guards.private_json(self.state / "mandate.json")
        guards.bootstrap_authority(config, mandate, epic=True)
        if guards.wait_profile_guard(self.state, config, mandate) != self.profile:
            raise frames.FrameError("WAIT_APPROVAL_MISMATCH")
        guards.managed_launch_guard(
            self.state, self.root, read_only=self.read_only, allow_initial=self.initial
        )
        path = self.state / "launch-control.json"
        current_control = guards.private_json(path) if path.exists() else None
        if current_control != self.control or (
            not self.read_only and current_control is not None
            and current_control["state"] != "RUNNING"
        ):
            raise frames.FrameError("OWNER_CONTROL_CHANGED")

    def _stage_guard(self, stage: str) -> tuple[Callable[[], None], float]:
        if self._owner_original:
            from tools.agent_owner_final_admission import _current_scope

            source = _current_scope(self)
            if stage != "FINAL:FINAL_GATE" or source.frames is None:
                raise frames.FrameError("WAIT_FRAME_BINDING_CHANGED")
            return self.guard, source.frames.anchor["deadline_at"]
        parts = stage.split(":")
        final = parts[0] == "FINAL" or parts[:2] == ["RECONCILE", "FINAL"]
        cursor = (
            workflow.StateStore(self.state / "session-control").load("cursor")
            if final
            else None
        )
        seed = loop.CheckpointStore(self.state, self.root).load()
        task = (
            int(parts[1])
            if parts[0] in {"TASK", "SUCCESSOR", "RECONCILE"} and parts[1] != "FINAL"
            else None
        )
        if final:
            task = (
                cursor["current_task"]
                if cursor is not None
                else (seed["task"] if seed else None)
            )
        if task is None:
            return self.guard, self.frame["deadline_at"]
        directory = (
            self.state
            if seed is not None and seed["task"] == task
            else self.state / "tasks" / str(task)
        )
        store = loop.CheckpointStore(directory, self.root)
        saved = store.load()
        if (
            saved is None
            or saved["task"] != task
            or any(
                saved.get(key) != self.mandate[key]
                for key in ("repository", "epic", "policy_sha")
            )
        ):
            raise loop.LoopError("LIVE_IDENTITY_CHANGED")
        identity = {
            key: saved.get(key)
            for key in (*loop.IDENTITY_KEYS, "policy_sha", "phase", "pending_phase")
        }
        control = recovery.Control(directory, self.root)
        present = recovery.present(directory)
        initial = control.load() if present else None
        path = directory / "runtime-task.json"
        config = recovery.private_value(path) if path.exists() else None
        approved = (
            config.get("approved_recovery_digest") if config is not None else None
        )
        approval = (
            recovery.private_value(directory / "bootstrap-recovery-approval.json")
            if approved is not None
            else None
        )
        deadline = self.frame["deadline_at"]
        owner_path = self.state / "final-approval.json"
        owner = (
            guards.private_json(owner_path) if final and owner_path.exists() else None
        )
        if (
            owner is not None
            and guards.canonical_digest(owner) != self.config["approved_final_digest"]
        ):
            raise loop.LoopError("FINAL_APPROVAL_MISMATCH")
        if approval is not None:
            if (
                set(approval) != recovery.APPROVAL_KEYS
                or recovery.digest(approval) != approved
                or type(approval["expires_at"]) is not int
                or type(approval["issued_at"]) is not int
            ):
                raise loop.LoopError("RECOVERY_STOPPED")
            deadline = min(deadline, approval["expires_at"])

        def guard() -> None:
            self.guard()
            if (
                final
                and workflow.StateStore(self.state / "session-control").load("cursor")
                != cursor
            ):
                raise loop.LoopError("SESSION_SCOPE_CHANGED")
            if owner is not None and guards.private_json(owner_path) != owner:
                raise loop.LoopError("FINAL_APPROVAL_MISMATCH")
            current = store.load()
            if current is None or any(
                current.get(key) != value for key, value in identity.items()
            ):
                raise loop.LoopError("LIVE_IDENTITY_CHANGED")
            if recovery.present(directory) != present:
                raise loop.LoopError("RECOVERY_STOPPED")
            if initial is not None:
                current_control = control.load()
                if any(
                    current_control[key] != initial[key]
                    for key in ("generation", "stopped", "authorization_digest")
                ):
                    raise loop.LoopError("RECOVERY_STOPPED")
            if config is not None and recovery.private_value(path) != config:
                raise loop.LoopError("RECOVERY_STOPPED")
            if approval is not None and (
                recovery.private_value(directory / "bootstrap-recovery-approval.json")
                != approval
                or not approval["issued_at"] <= time.time() < approval["expires_at"]
                or approval["generation"]
                != (initial["generation"] if initial is not None else 0)
                or any(
                    approval[key] != saved[key]
                    for key in ("repository", "epic", "task", "policy_sha")
                )
                or approval["mandate_digest"] != self.config["approved_mandate_digest"]
                or approval["owner_identity"] != self.mandate["owner_identity"]
            ):
                raise loop.LoopError("RECOVERY_STOPPED")

        guard()
        return guard, deadline

    def reader(self, stage: str) -> wait.Reader:
        self.guard()
        if self._retained_owner:
            raise frames.FrameError("WAIT_FRAME_REQUIRED")
        if stage not in self._readers:
            if self._owner_original:
                if stage != "FINAL:FINAL_GATE":
                    raise frames.FrameError("WAIT_FRAME_BINDING_CHANGED")
                self._readers[stage] = wait.Reader.owner_original(self)
                return self._readers[stage]
            guard, deadline = self._stage_guard(stage)
            self._readers[stage] = wait.Reader(
                directory=self.state,
                root=self.root,
                identity={
                    "repository": self.mandate["repository"],
                    "epic": self.mandate["epic"],
                    "policy_sha": self.mandate["policy_sha"],
                    "wait_digest": self.config["approved_wait_digest"],
                    "stage": stage,
                },
                limits=self.profile["limits"],
                deadline_at=deadline,
                guard=guard,
            )
        return self._readers[stage]

    def _active_reader(self) -> wait.Reader:
        """Return only the reader of an already-entered original context."""
        self.guard()
        stage = self.active_stage
        if stage is None or stage not in self._contexts or stage not in self._readers:
            raise frames.FrameError("WAIT_FRAME_REQUIRED")
        return self._readers[stage]

    def recorded(self, stage: str) -> bool:
        """A fixed runtime caller checks presence; context/read validates the record."""
        identity = {
            "repository": self.mandate["repository"],
            "epic": self.mandate["epic"],
            "policy_sha": self.mandate["policy_sha"],
            "wait_digest": self.config["approved_wait_digest"],
            "stage": stage,
        }
        path = (
            self.state
            / "read-waits"
            / wait.digest(identity).removeprefix("sha256:")
            / "wait.json"
        )
        return path.exists() or path.is_symlink()

    @property
    def active_stage(self) -> str | None:
        return self._context_stack[-1] if self._context_stack else None

    @contextmanager
    def context(self, stage: str) -> Iterator[wait.Reader]:
        """Refresh local facts only at trusted callback entry, outside nested waits."""
        reader = self.reader(stage)
        if stage in self._contexts:
            reader.guard()
            self._context_stack.append(stage)
            try:
                yield reader
            finally:
                self._context_stack.pop()
            return
        previous = reader.guard
        guard, deadline = self._stage_guard(stage)
        reader.guard = guard
        reader.current_deadline = min(reader.current_deadline, deadline)
        self._contexts.add(stage)
        self._context_stack.append(stage)
        try:
            reader.guard()
            yield reader
        finally:
            self._context_stack.pop()
            self._contexts.remove(stage)
            reader.guard = previous

    def child(self, command: Sequence[str], timeout: int) -> tuple[dict[str, Any], int]:
        self.guard()
        if self._owner_original or self._retained_owner:
            raise frames.FrameError("WAIT_FRAME_BINDING_CHANGED")
        now = time.time()
        return frames.create(
            state=self.state,
            root=self.root,
            repository=self.mandate["repository"],
            epic=self.mandate["epic"],
            policy_sha=self.mandate["policy_sha"],
            runtime_config_digest=self.frame["runtime_config_digest"],
            command=command_identity(command),
            deadline_at=min(self.frame["deadline_at"], now + timeout),
            now=now,
            parent=self.frame,
        )
