"""Owner-private current-invocation deadline context, without effect authority."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import sys
import time
import uuid
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

from tools.agent_epic_runtime_cli import DeploymentError, owner_uid, secure_directory
from tools.agent_epic_wait import digest

FD_ENV = "PGW_WAIT_FRAME_FD"
NONCE_ENV = "PGW_WAIT_INVOCATION_ID"
KEYS = {
    "schema_version",
    "repository",
    "epic",
    "policy_sha",
    "runtime_config_digest",
    "command_digest",
    "invocation_id",
    "parent_frame_digest",
    "started_at",
    "deadline_at",
}


class FrameError(DeploymentError):
    pass


def _valid(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != KEYS:
        raise FrameError("WAIT_FRAME_INVALID")
    if (
        value["schema_version"] != "1.0"
        or not isinstance(value["repository"], str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]) is None
        or type(value["epic"]) is not int
        or value["epic"] <= 0
        or not isinstance(value["policy_sha"], str)
        or re.fullmatch(r"[a-f0-9]{40}", value["policy_sha"]) is None
        or not isinstance(value["invocation_id"], str)
        or re.fullmatch(r"[a-f0-9]{32}", value["invocation_id"]) is None
        or any(
            not isinstance(value[key], str)
            or re.fullmatch(r"sha256:[a-f0-9]{64}", value[key]) is None
            for key in ("runtime_config_digest", "command_digest")
        )
        or (
            value["parent_frame_digest"] is not None
            and (
                not isinstance(value["parent_frame_digest"], str)
                or re.fullmatch(r"sha256:[a-f0-9]{64}", value["parent_frame_digest"])
                is None
            )
        )
        or any(
            type(value[key]) not in {int, float}
            or not math.isfinite(value[key])
            or value[key] < 0
            for key in ("started_at", "deadline_at")
        )
        or value["started_at"] >= value["deadline_at"]
    ):
        raise FrameError("WAIT_FRAME_INVALID")
    return value


def _directory(state: Path, root: Path) -> Path:
    if sys.platform == "win32":
        raise FrameError("WAIT_FRAME_UNSUPPORTED")
    if any(path.is_symlink() for path in (state, *state.parents)):
        raise FrameError("WAIT_FRAME_UNSAFE")
    secure_directory(state, root)
    path = state / "invocations"
    if path.is_symlink():
        raise FrameError("WAIT_FRAME_UNSAFE")
    path.mkdir(mode=0o700, exist_ok=True)
    return secure_directory(path, root)


def create(
    *,
    state: Path,
    root: Path,
    repository: str,
    epic: int,
    policy_sha: str,
    runtime_config_digest: str,
    command: Sequence[str],
    deadline_at: float,
    now: float,
    parent: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    """Pinned parent calls this once before a fixed child invocation."""
    if parent is not None:
        previous = _valid(dict(parent))
        if (
            any(
                previous[key] != current
                for key, current in (
                    ("repository", repository),
                    ("epic", epic),
                    ("policy_sha", policy_sha),
                    ("runtime_config_digest", runtime_config_digest),
                )
            )
            or now < previous["started_at"]
        ):
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        deadline_at = min(deadline_at, previous["deadline_at"])
    value = _valid(
        {
            "schema_version": "1.0",
            "repository": repository,
            "epic": epic,
            "policy_sha": policy_sha,
            "runtime_config_digest": runtime_config_digest,
            "command_digest": digest(list(command)),
            "invocation_id": uuid.uuid4().hex,
            "parent_frame_digest": digest(dict(parent)) if parent is not None else None,
            "started_at": now,
            "deadline_at": deadline_at,
        }
    )
    path = _directory(state, root) / (value["invocation_id"] + ".json")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return value, os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))


def environment(value: Mapping[str, Any], descriptor: int) -> dict[str, str]:
    return {FD_ENV: str(descriptor), NONCE_ENV: str(value["invocation_id"])}


def clear(environment: MutableMapping[str, str]) -> None:
    environment.pop(FD_ENV, None)
    environment.pop(NONCE_ENV, None)


def consume(
    *,
    state: Path,
    root: Path,
    environment: MutableMapping[str, str],
    repository: str,
    epic: int,
    policy_sha: str,
    runtime_config_digest: str,
    command: Sequence[str],
    now: float,
) -> dict[str, Any]:
    marker, nonce = environment.pop(FD_ENV, None), environment.pop(NONCE_ENV, None)
    if sys.platform == "win32":
        raise FrameError("WAIT_FRAME_UNSUPPORTED")
    if (
        marker is None
        or nonce is None
        or re.fullmatch(r"[0-9]{1,10}", marker) is None
        or re.fullmatch(r"[a-f0-9]{32}", nonce) is None
    ):
        raise FrameError("WAIT_FRAME_REQUIRED")
    descriptor = int(marker)
    try:
        import fcntl

        info = os.fstat(descriptor)
        os.set_inheritable(descriptor, False)
        if (
            (fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE) != os.O_RDONLY
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > 16384
        ):
            raise FrameError("WAIT_FRAME_UNSAFE")
        path = _directory(state, root) / (nonce + ".json")
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        raw = os.pread(descriptor, 16385, 0)
        value = _valid(json.loads(raw))
        if (
            any(
                value[key] != expected
                for key, expected in (
                    ("invocation_id", nonce),
                    ("repository", repository),
                    ("epic", epic),
                    ("policy_sha", policy_sha),
                    ("runtime_config_digest", runtime_config_digest),
                    ("command_digest", digest(list(command))),
                )
            )
            or not value["started_at"] <= now < value["deadline_at"]
        ):
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        return value
    except (OSError, ValueError, ImportError) as error:
        raise FrameError("WAIT_FRAME_INVALID") from error


def _owner_close_all(descriptors: Sequence[int]) -> None:
    """Attempt every owned close, preserving an active primary failure."""
    primary = sys.exc_info()[1]
    failure: BaseException | None = None
    for descriptor in descriptors:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except BaseException as error:
                if failure is None:
                    failure = error
    if primary is None and failure is not None:
        if isinstance(failure, (OSError, ValueError)):
            raise FrameError("WAIT_FRAME_INVALID") from None
        raise failure


class OwnerFrameSources:
    """Bounded original frame facts, never human/installation authority.

    Only the fixed installed owner admission consumes this source object. All
    paths and comparison identities are derived from existing managed originals.
    """

    MAX_ENTRIES = 4096
    MAX_FRAME_BYTES = 16384
    MAX_TOTAL_BYTES = 64 * 1024 * 1024

    def _initialize(self, state: Path, root: Path) -> None:
        from tools import agent_epic_runtime_cli as guards

        self.state, self.root = state, root
        self._seed = self._namespace = -1
        self._bytes = self._entries = 0
        self._writing_temp: tuple[str, int] | None = None
        self._records: dict[str, tuple[dict[str, Any], tuple[int, ...]]] = {}
        self.sources = guards.final_preparation_original_sources(state, root)
        if self.sources["config"].get("schema_version") != "8.0":
            raise FrameError("FINAL_APPROVAL_INVALID")
        self._seed = guards._final_preseed_seed(state, root)

    def _open_graph(self) -> None:
        from tools import agent_epic_runtime_cli as guards

        self._namespace = guards._final_preseed_directory(self._seed, "invocations")
        self._namespace_identity = self._identity(os.fstat(self._namespace))
        self._guard()
        self._scan()
        selected = self._records.get(self.anchor["invocation_id"])
        if selected is None or digest(selected[0]) != self.anchor["frame_digest"]:
            raise FrameError("FINAL_APPROVAL_INVALID")
        self.frame = dict(selected[0])
        self.parent = self._association()
        self.revalidate()

    def __init__(self, state: Path, root: Path) -> None:
        from tools import agent_epic_runtime_cli as guards
        from tools import agent_final_preparation_records as records

        self._seed = self._namespace = -1
        try:
            self._initialize(state, root)
            preparation = guards._final_preseed_directory(
                self._seed, "final-preparation",
            )
            try:
                final = records.state2(guards._final_preseed_read(
                    preparation, "final-state.json",
                    self.sources["profile"]["max_artifact_bytes"],
                ))
            finally:
                _owner_close_all((preparation,))
            self.anchor = records.owner_wait(final["owner_wait"])
            if final["calls_consumed"] != 1:
                raise FrameError("FINAL_APPROVAL_INVALID")
            for key in records.IDENTITY:
                expected = (self.sources["config"]["approved_launch_digest"]
                            if key == "launch_digest" else self.sources["profile"][key])
                if final[key] != expected:
                    raise FrameError("FINAL_APPROVAL_INVALID")
            self._open_graph()
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            self.close()
            raise FrameError("FINAL_APPROVAL_INVALID") from None
        except BaseException:
            self.close()
            raise

    @classmethod
    def for_publication(cls, session: Any, anchor: dict[str, Any]) -> OwnerFrameSources:
        """Fixed writer-side route proof before first nonnull anchor publication."""
        from tools import agent_final_preparation_records as records
        from tools.agent_epic_session_runtime import SessionRuntime
        from tools.agent_epic_wait_scope import Scope

        scope = session.wait_scope if type(session) is SessionRuntime else None
        if (
            type(scope) is not Scope or scope.read_only or scope._owner_original
            or scope.active_stage != "FINAL:FINAL_GATE"
            or scope._session_origin not in {"QUOTA_WAKE", "EPIC_COMMAND"}
        ):
            raise FrameError("FINAL_APPROVAL_INVALID")
        result = cls.__new__(cls)
        result._seed = result._namespace = -1
        try:
            scope.guard()
            result._initialize(session.state, session.root)
            if session.config != result.sources["config"]:
                raise FrameError("FINAL_APPROVAL_INVALID")
            result.anchor = records.owner_wait(anchor)
            result._open_graph()
            if result.frame != scope.frame:
                raise FrameError("WAIT_FRAME_BINDING_CHANGED")
            if (
                (scope._session_origin == "QUOTA_WAKE" and result.parent is not None)
                or (scope._session_origin == "EPIC_COMMAND" and result.parent is None)
            ):
                raise FrameError("WAIT_FRAME_BINDING_CHANGED")
            scope.guard()
            return result
        except BaseException:
            result.close()
            raise

    @staticmethod
    def _identity(info: os.stat_result) -> tuple[int, ...]:
        return (
            info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        )

    def _wait_original(self) -> dict[str, Any]:
        from tools import agent_epic_loop as loop
        from tools import agent_epic_runtime_cli as guards
        from tools import agent_epic_wait as wait

        loop.RunnerLock.current_owned(self.state / "session-control")
        loop.RunnerLock.current_owned(self.state)
        identity = {
            "repository": self.sources["mandate"]["repository"],
            "epic": self.sources["mandate"]["epic"],
            "policy_sha": self.sources["mandate"]["policy_sha"],
            "wait_digest": self.sources["config"]["approved_wait_digest"],
            "stage": "FINAL:FINAL_GATE",
        }
        name = digest(identity).removeprefix("sha256:")
        directory = self.state / "read-waits" / name
        loop.RunnerLock.current_owned(directory)
        base = stage = -1
        try:
            base = guards._final_preseed_directory(self._seed, "read-waits")
            stage = guards._final_preseed_directory(base, name)
            expected = {"wait.json", "epic-loop.lock"}
            if self._writing_temp is not None:
                temporary, descriptor = self._writing_temp
                if re.fullmatch(r"\.owner-wait-[0-9a-f]{32}\.tmp", temporary) is None:
                    raise FrameError("WAIT_STORAGE_UNSAFE")
                guards._final_preseed_private(descriptor, directory=False)
                opened = os.fstat(descriptor)
                current = os.stat(temporary, dir_fd=stage, follow_symlinks=False)
                if (
                    (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                    or stat.S_IMODE(opened.st_mode) != 0o600
                    or opened.st_size > 1_000_000
                ):
                    raise FrameError("WAIT_STORAGE_UNSAFE")
                expected.add(temporary)
            names: set[str] = set()
            with os.scandir(stage) as entries:
                for ordinal, entry in enumerate(entries, 1):
                    if ordinal > len(expected):
                        raise FrameError("WAIT_STORAGE_UNSAFE")
                    names.add(entry.name)
            if names != expected:
                raise FrameError("WAIT_STORAGE_UNSAFE")
            profile = guards.wait_profile_guard(
                self.state, self.sources["config"], self.sources["mandate"],
            )
            if profile is None:
                raise FrameError("WAIT_PROFILE_REQUIRED")
            value = wait.validate_wait_record(
                guards._final_preseed_read(stage, "wait.json", 1_000_000),
                identity, profile["limits"],
            )
            now = time.time()
            if (
                value["started_at"] != self.anchor["stage_started_at"]
                or not value["started_at"] <= value["last_seen_at"] <= now
                or not now < min(value["deadline_at"], self.anchor["deadline_at"])
                or not isinstance(value["ci"], dict)
                or value["ci"]["identity_digest"] != self.anchor["wait_identity_digest"]
            ):
                raise FrameError("WAIT_FRAME_BINDING_CHANGED")
            return value
        finally:
            _owner_close_all((stage, base))

    def _guard(self) -> None:
        from tools import agent_epic_runtime_cli as guards

        self._wait_original()
        now = time.time()
        mandate, config = self.sources["mandate"], self.sources["config"]
        deadline = min(
            self.anchor["deadline_at"], mandate["expires_at"],
            config["started_at"] + mandate["limits"]["max_duration_seconds"],
        )
        if not self.anchor["stage_started_at"] <= now < deadline:
            raise FrameError("WAIT_INVOCATION_EXPIRED")
        current = guards.final_preparation_original_sources(self.state, self.root)
        if current != self.sources:
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        guards.managed_launch_guard(self.state, self.root)
        guards.bootstrap_authority(config, mandate, epic=True)
        control = guards._final_preseed_read(self._seed, "launch-control.json", 262144)
        adapter = guards._final_preseed_read(self._seed, "runtime-adapter.json", 262144)
        if (
            control.get("state") != "RUNNING"
            or digest(control) != self.anchor["control_digest"]
            or guards.launch_config_digest(config, adapter)
            != self.anchor["runtime_config_digest"]
        ):
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")

    def _read(self, name: str) -> tuple[dict[str, Any], tuple[int, ...]]:
        from tools import agent_epic_runtime_cli as guards

        self._guard()
        descriptor = os.open(
            name, os.O_RDONLY | getattr(os, "O_NOFOLLOW")
            | getattr(os, "O_NONBLOCK"), dir_fd=self._namespace,
        )
        try:
            guards._final_preseed_private(descriptor, directory=False)
            before = self._identity(os.fstat(descriptor))
            if before[5] > self.MAX_FRAME_BYTES:
                raise FrameError("WAIT_FRAME_INVALID")
            raw = bytearray()
            while len(raw) <= self.MAX_FRAME_BYTES:
                self._guard()
                remaining = self.MAX_TOTAL_BYTES - self._bytes
                if remaining <= 0:
                    raise FrameError("WAIT_FRAME_INVALID")
                # Capacity is checked before each raw read; actual returned bytes
                # charge the same global counter, including every reread/EOF.
                request = min(1024, self.MAX_FRAME_BYTES + 1 - len(raw), remaining)
                chunk = os.read(descriptor, request)
                self._bytes += len(chunk)
                self._guard()
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) > self.MAX_FRAME_BYTES:
                raise FrameError("WAIT_FRAME_INVALID")
            if self._identity(os.fstat(descriptor)) != before:
                raise FrameError("WAIT_FRAME_BINDING_CHANGED")
            value = _valid(json.loads(
                raw, object_pairs_hook=guards._final_preseed_pairs,
            ))
            if value["invocation_id"] + ".json" != name:
                raise FrameError("WAIT_FRAME_INVALID")
            return value, before
        finally:
            _owner_close_all((descriptor,))

    def _scan(self) -> None:
        with os.scandir(self._namespace) as entries:
            for entry in entries:
                self._entries += 1
                if self._entries > self.MAX_ENTRIES:
                    raise FrameError("WAIT_FRAME_INVALID")
                if re.fullmatch(r"[0-9a-f]{32}\.json", entry.name) is None:
                    raise FrameError("WAIT_FRAME_INVALID")
                value, identity = self._read(entry.name)
                key = value["invocation_id"]
                if key in self._records:
                    raise FrameError("WAIT_FRAME_INVALID")
                self._records[key] = value, identity
        if self._identity(os.fstat(self._namespace)) != self._namespace_identity:
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")

    def _association(self) -> dict[str, Any] | None:
        from tools import agent_epic_runtime_cli as guards
        from tools.agent_epic_wait_scope import command_identity

        frame, mandate = self.frame, self.sources["mandate"]
        for key in ("repository", "epic", "policy_sha"):
            if frame[key] != mandate[key]:
                raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        if frame["runtime_config_digest"] != self.anchor["runtime_config_digest"]:
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        parent_digest = frame["parent_frame_digest"]
        if parent_digest is None:
            if frame["command_digest"] != digest(["DISCOVERY", "LOCAL"]):
                raise FrameError("WAIT_FRAME_BINDING_CHANGED")
            return None
        adapter = guards._final_preseed_read(self._seed, "runtime-adapter.json", 262144)
        command = adapter.get("epic_command")
        if (
            not isinstance(command, list)
            or not all(isinstance(v, str) for v in command)
        ):
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        if frame["command_digest"] != digest(list(command_identity(command))):
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        parents = [value for value, _identity in self._records.values()
                   if digest(value) == parent_digest]
        if len(parents) != 1:
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        parent = parents[0]
        if (
            parent["parent_frame_digest"] is not None
            or parent["command_digest"] != digest(["DISCOVERY", "LOCAL"])
            or any(parent[k] != frame[k] for k in (
                "repository", "epic", "policy_sha", "runtime_config_digest",
            ))
            or not parent["started_at"] <= frame["started_at"]
            or frame["deadline_at"] > parent["deadline_at"]
        ):
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        return dict(parent)

    def revalidate(self) -> None:
        from tools import agent_epic_runtime_cli as guards

        self._guard()
        current_seed = current_namespace = -1
        try:
            current_seed = guards._final_preseed_seed(self.state, self.root)
            old_seed, new_seed = os.fstat(self._seed), os.fstat(current_seed)
            if (old_seed.st_dev, old_seed.st_ino) != (new_seed.st_dev, new_seed.st_ino):
                raise FrameError("WAIT_FRAME_BINDING_CHANGED")
            current_namespace = guards._final_preseed_directory(
                current_seed, "invocations",
            )
            if self._identity(os.fstat(current_namespace)) != self._namespace_identity:
                raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        finally:
            _owner_close_all((current_namespace, current_seed))
        if self._identity(os.fstat(self._namespace)) != self._namespace_identity:
            raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        for key, (_value, identity) in self._records.items():
            info = os.stat(key + ".json", dir_fd=self._namespace, follow_symlinks=False)
            if self._identity(info) != identity:
                raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        selected = [self.frame] + ([self.parent] if self.parent is not None else [])
        for expected in selected:
            value, identity = self._read(expected["invocation_id"] + ".json")
            if (
                value != expected
                or identity != self._records[value["invocation_id"]][1]
            ):
                raise FrameError("WAIT_FRAME_BINDING_CHANGED")
        self._guard()

    def close(self) -> None:
        descriptors = self._namespace, self._seed
        self._namespace = self._seed = -1
        _owner_close_all(descriptors)

    def __enter__(self) -> OwnerFrameSources:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
