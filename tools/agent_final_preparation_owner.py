"""Read fixed private owner evidence; shapes alone never prove owner approval.

The installed caller supplies source_verifier, which must reread the original
owner command, launch, candidate/CI and real final-demo sources. This isolated
reader neither installs that verifier nor performs final effects.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from tools import agent_final_preparation_records as records
from tools.agent_epic_loop import PhaseBlocked

SourceVerifier = Callable[
    [dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]], bool
]


def _invalid() -> NoReturn:
    raise PhaseBlocked("FINAL_APPROVAL_INVALID")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _private(fd: int, *, directory: bool) -> None:
    if os.name != "posix":
        _invalid()
    value = os.fstat(fd)
    valid_type = (
        stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
    )
    if (
        not valid_type
        or value.st_uid != getattr(os, "getuid")()
        or value.st_mode & 0o077
        or (not directory and value.st_nlink != 1)
    ):
        _invalid()


def read_owner_approval(
    seed: Path,
    candidate: dict[str, Any],
    *,
    max_bytes: int,
    installed_guard: Callable[[], None],
    source_verifier: SourceVerifier,
) -> dict[str, Any] | None:
    """Return None only for wholly absent evidence; otherwise verify or block.

    installed_guard must establish this exact managed seed outside worktrees and
    model write mounts. source_verifier is trusted installation code, never a
    candidate-controlled callback or a self-reported success flag.
    """
    candidate = records.candidate(candidate)
    records.integer(max_bytes, 262144, 1)
    if os.name != "posix":
        _invalid()
    installed_guard()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_DIRECTORY")
    seed_fd = owner_fd = -1
    try:
        seed_fd = os.open(seed, flags)
        _private(seed_fd, directory=True)
        try:
            owner_fd = os.open("final-owner", flags, dir_fd=seed_fd)
        except FileNotFoundError:
            return None
        _private(owner_fd, directory=True)
        names = ("approval.json", "approval-pin.json", "install-receipt.json")
        present = [name for name in names if name in os.listdir(owner_fd)]
        if not present:
            return None
        if len(present) != 3:
            _invalid()
        values: list[dict[str, Any]] = []
        for name in names:
            fd = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK"),
                dir_fd=owner_fd,
            )
            try:
                _private(fd, directory=False)
                if os.fstat(fd).st_size > max_bytes:
                    _invalid()
                raw = bytearray()
                while len(raw) <= max_bytes:
                    chunk = os.read(fd, min(65536, max_bytes + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) > max_bytes:
                    _invalid()
                value = json.loads(raw, object_pairs_hook=_pairs)
                if not isinstance(value, dict):
                    _invalid()
                values.append(value)
            finally:
                os.close(fd)
        approval = records.approval(values[0])
        pin = records.owner_pin(values[1])
        receipt = records.owner_receipt(values[2])
        shared = records.IDENTITY | {"owner_identity", "candidate_digest"}
        for value in (approval, pin, receipt):
            if any(value[key] != candidate[key] for key in shared):
                _invalid()
        for key in ("pr", "main_sha", "roadmap_sha", "policy_sha", "pr_ci_digest"):
            if approval[key] != candidate[key]:
                _invalid()
        if (
            pin["approval_digest"] != approval["approved_digest"]
            or receipt["approval_digest"] != approval["approved_digest"]
            or pin["installation_receipt_digest"] != receipt["record_digest"]
            or pin["approved_at"] != approval["approved_at"]
            or receipt["approved_at"] != approval["approved_at"]
        ):
            _invalid()
        installed_guard()
        if source_verifier(candidate, approval, pin, receipt) is not True:
            _invalid()
        return approval
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise PhaseBlocked("FINAL_APPROVAL_INVALID") from exc
    finally:
        if owner_fd >= 0:
            os.close(owner_fd)
        if seed_fd >= 0:
            os.close(seed_fd)


def _current_owner_names(parent: int) -> set[str]:
    """Observe at most six names; never allocate an unbounded sibling list."""
    import sys

    names: set[str] = set()
    entries = os.scandir(parent)
    try:
        for ordinal, entry in enumerate(entries, 1):
            if ordinal > 5:
                _invalid()
            names.add(entry.name)
    finally:
        primary = sys.exc_info()[1]
        try:
            entries.close()
        except BaseException as error:
            if primary is None:
                if isinstance(error, (OSError, ValueError)):
                    _invalid()
                raise
    return names


def _current_owner_close(descriptors: tuple[int, ...]) -> None:
    import sys

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
            _invalid()
        raise failure


def read_current_owner_approval(
    seed: Path,
    candidate: dict[str, Any],
    *,
    max_bytes: int,
    installed_guard: Callable[[], None],
    source_verifier: SourceVerifier,
) -> dict[str, Any] | None:
    """Fixed schema2 complete namespace; legacy reader above is unchanged.

    Installed callbacks remain the original fixed source composition, never a
    new caller-selected admission mode. Complete five originals are mandatory.
    """
    candidate = records.candidate(candidate)
    records.integer(max_bytes, 262144, 1)
    if os.name != "posix":
        _invalid()
    installed_guard()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_DIRECTORY")
    seed_fd = owner_fd = -1
    try:
        seed_fd = os.open(seed, flags)
        _private(seed_fd, directory=True)
        try:
            owner_fd = os.open("final-owner", flags, dir_fd=seed_fd)
        except FileNotFoundError:
            return None
        _private(owner_fd, directory=True)
        names = ("approval.json", "approval-pin.json", "install-receipt.json")
        if _current_owner_names(owner_fd) != {
            *names, "command-source.json", "final-demo-source.json",
        }:
            _invalid()
        values: list[dict[str, Any]] = []
        for name in names:
            fd = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK"),
                dir_fd=owner_fd,
            )
            try:
                _private(fd, directory=False)
                if os.fstat(fd).st_size > max_bytes:
                    _invalid()
                raw = bytearray()
                while len(raw) <= max_bytes:
                    chunk = os.read(fd, min(65536, max_bytes + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) > max_bytes:
                    _invalid()
                value = json.loads(raw, object_pairs_hook=_pairs)
                if not isinstance(value, dict):
                    _invalid()
                values.append(value)
            finally:
                _current_owner_close((fd,))
        approval = records.approval(values[0])
        pin = records.owner_pin(values[1])
        receipt = records.owner_receipt(values[2])
        shared = records.IDENTITY | {"owner_identity", "candidate_digest"}
        for value in (approval, pin, receipt):
            if any(value[key] != candidate[key] for key in shared):
                _invalid()
        for key in ("pr", "main_sha", "roadmap_sha", "policy_sha", "pr_ci_digest"):
            if approval[key] != candidate[key]:
                _invalid()
        if (
            pin["approval_digest"] != approval["approved_digest"]
            or receipt["approval_digest"] != approval["approved_digest"]
            or pin["installation_receipt_digest"] != receipt["record_digest"]
            or pin["approved_at"] != approval["approved_at"]
            or receipt["approved_at"] != approval["approved_at"]
        ):
            _invalid()
        installed_guard()
        if source_verifier(candidate, approval, pin, receipt) is not True:
            _invalid()
        return approval
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise PhaseBlocked("FINAL_APPROVAL_INVALID") from exc
    finally:
        _current_owner_close((owner_fd, seed_fd))
