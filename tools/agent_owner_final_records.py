"""Neutral five-original shape facts; not installation or human authority.

This primitive performs no writes and accepts no verifier callback. Future fixed
installed composition still owes actual candidate/CI, mount and authority proof.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

from tools import agent_epic_runtime_cli as guards
from tools import agent_final_preparation_records as records
from tools import agent_owner_final_sources as sources
from tools.agent_epic_loop import PhaseBlocked

_NAMES = (
    "approval.json",
    "approval-pin.json",
    "install-receipt.json",
    "command-source.json",
    "final-demo-source.json",
)


@dataclass(frozen=True, repr=False)
class OriginalShapes:
    """Untrusted source-shape facts; never an approval or permission result."""

    approval: dict[str, Any] = field(repr=False)
    pin: dict[str, Any] = field(repr=False)
    receipt: dict[str, Any] = field(repr=False)
    command: dict[str, Any] = field(repr=False)
    demo: dict[str, Any] = field(repr=False)


def _invalid() -> NoReturn:
    raise PhaseBlocked("FINAL_APPROVAL_INVALID") from None


def _identity(fd: int) -> tuple[int, ...]:
    value = os.fstat(fd)
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _names(parent: int) -> set[str]:
    """Acquire at most six entries, retaining at most the fixed five names."""
    names: set[str] = set()
    with os.scandir(parent) as entries:
        for ordinal, entry in enumerate(entries, 1):
            if ordinal > len(_NAMES):
                _invalid()
            names.add(entry.name)
    return names


def _fingerprint(parent: int, name: str, cap: int) -> tuple[tuple[int, ...], str]:
    fd = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK"),
        dir_fd=parent,
    )
    try:
        guards._final_preseed_private(fd, directory=False)
        before = _identity(fd)
        if os.fstat(fd).st_size > cap:
            _invalid()
        raw = bytearray()
        while len(raw) <= cap:
            chunk = os.read(fd, min(65536, cap + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > cap or _identity(fd) != before:
            _invalid()
        return before, hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def _links(
    values: list[dict[str, Any]], candidate: dict[str, Any], issued: int, expires: int
) -> OriginalShapes:
    approval, pin, receipt, command, demo = (
        records.approval(values[0]),
        records.owner_pin(values[1]),
        records.owner_receipt(values[2]),
        sources.command(values[3]),
        sources.demo(values[4]),
    )
    shared = records.IDENTITY | {"owner_identity", "candidate_digest"}
    for value in (approval, pin, receipt, command, demo):
        if any(value[key] != candidate[key] for key in shared):
            _invalid()
    for value in (approval, command, demo):
        if any(
            value[key] != candidate[key]
            for key in ("pr", "main_sha", "roadmap_sha", "policy_sha", "pr_ci_digest")
        ):
            _invalid()
    approved = approval["approved_digest"]
    at = approval["approved_at"]
    if (
        any(value["approval_digest"] != approved for value in (pin, receipt, command))
        or any(value["approved_at"] != at for value in (pin, receipt, command))
        or pin["installation_receipt_digest"] != receipt["record_digest"]
        or receipt["owner_command_digest"] != command["record_digest"]
        or approval["final_demo_digest"] != demo["record_digest"]
        or command["final_demo_digest"] != demo["record_digest"]
        or not issued <= demo["started_at"] <= demo["completed_at"] <= at <= expires
    ):
        _invalid()
    return OriginalShapes(approval, pin, receipt, command, demo)


def read_original_shapes(
    seed: Path,
    root: Path,
    candidate: dict[str, Any],
    *,
    max_bytes: int,
    issued_at: int,
    expires_at: int,
) -> OriginalShapes | None:
    """Compare five shapes with supplied *untrusted* candidate/time representation.

    seed/root must be fixed trusted-context paths, never JSON artifact selectors.
    None means all five absent. No result proves candidate freshness, installation,
    mounts, human invocation, lifecycle authority or permission to act.
    """
    seed_fd = owner_fd = -1
    try:
        if os.name != "posix":
            _invalid()
        candidate = records.candidate(candidate)
        records.integer(max_bytes, 262144, 1)
        records.integer(issued_at)
        records.integer(expires_at)
        if issued_at > expires_at:
            _invalid()
        seed_fd = guards._final_preseed_seed(seed, root)
        seed_identity = _identity(seed_fd)
        try:
            owner_fd = guards._final_preseed_directory(seed_fd, "final-owner")
        except FileNotFoundError:
            names: set[str] = set()
        else:
            names = _names(owner_fd)
        if owner_fd < 0 or not names:
            # Reanchor even the absent result; never create a missing namespace.
            check_seed = guards._final_preseed_seed(seed, root)
            try:
                if _identity(check_seed) != seed_identity:
                    _invalid()
                if owner_fd < 0:
                    try:
                        unexpected = guards._final_preseed_directory(
                            seed_fd, "final-owner"
                        )
                    except FileNotFoundError:
                        pass
                    else:
                        os.close(unexpected)
                        _invalid()
                else:
                    check_owner = guards._final_preseed_directory(
                        seed_fd, "final-owner"
                    )
                    try:
                        if _identity(check_owner) != _identity(owner_fd) or _names(
                            owner_fd
                        ):
                            _invalid()
                    finally:
                        os.close(check_owner)
            finally:
                os.close(check_seed)
            return None
        if names != set(_NAMES):
            _invalid()
        owner_identity = _identity(owner_fd)
        before = [_fingerprint(owner_fd, name, max_bytes) for name in _NAMES]
        values = [
            guards._final_preseed_read(owner_fd, name, max_bytes) for name in _NAMES
        ]
        result = _links(values, candidate, issued_at, expires_at)
        if [_fingerprint(owner_fd, name, max_bytes) for name in _NAMES] != before:
            _invalid()
        check_seed = guards._final_preseed_seed(seed, root)
        try:
            check_owner = guards._final_preseed_directory(seed_fd, "final-owner")
            try:
                if (
                    _identity(check_seed) != seed_identity
                    or _identity(check_owner) != owner_identity
                    or _identity(owner_fd) != owner_identity
                    or _names(owner_fd) != set(_NAMES)
                ):
                    _invalid()
            finally:
                os.close(check_owner)
        finally:
            os.close(check_seed)
        return result
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        guards.DeploymentError,
        PhaseBlocked,
    ):
        _invalid()
    finally:
        if owner_fd >= 0:
            os.close(owner_fd)
        if seed_fd >= 0:
            os.close(seed_fd)
