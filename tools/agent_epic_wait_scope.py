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
    def for_root(
        cls, state: Path, root: Path, *, read_only: bool = False
    ) -> Scope | None:
        seed = guards.managed_state(state)
        path = seed / "epic-runtime.json"
        if not path.exists():
            return None
        config = guards.private_json(path)
        if config.get("schema_version") not in {"4.0", "5.0", "6.0"}:
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
    def inherited(
        cls, state: Path, root: Path, *, read_only: bool = False
    ) -> Scope | None:
        seed = guards.managed_state(state)
        path = seed / "epic-runtime.json"
        if not path.exists():
            frames.clear(os.environ)
            return None
        config = guards.private_json(path)
        if config.get("schema_version") not in {"4.0", "5.0", "6.0"}:
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
        return cls(seed, root, value, read_only=read_only)

    def guard(self) -> None:
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
        current = guards.private_json(path) if path.exists() else None
        if current != self.control or (
            not self.read_only and current is not None and current["state"] != "RUNNING"
        ):
            raise frames.FrameError("OWNER_CONTROL_CHANGED")

    def _stage_guard(self, stage: str) -> tuple[Callable[[], None], float]:
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
        if stage not in self._readers:
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
