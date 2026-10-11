"""Owner-private current-invocation deadline context, without effect authority."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import sys
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
