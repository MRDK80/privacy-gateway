#!/usr/bin/env python3
"""Fixed installed external human owner procedure; never a runtime entrypoint.

Direct human invocation/input and original observation are external operational
prerequisites. This code checks source consistency, not physical human identity.
No installation/activation/merge is performed by this command.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, NoReturn


class _Invalid(Exception):
    pass


def _invalid() -> NoReturn:
    raise _Invalid("FINAL_APPROVAL_INVALID")


def _close_all(descriptors: tuple[int, ...]) -> None:
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
        _invalid()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _private_bytes(path: Path, maximum: int) -> bytes:
    if path != path.resolve(strict=True) or any(p.is_symlink() for p in path.parents):
        _invalid()
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK"),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode) or before.st_uid != getattr(os, "getuid")()
            or before.st_nlink != 1
            or before.st_mode & 0o077
            or before.st_size > maximum
        ):
            _invalid()
        raw = bytearray()
        while len(raw) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after, canonical = os.fstat(descriptor), path.lstat()
        def identity(item: os.stat_result) -> tuple[int, ...]:
            return (
                item.st_dev, item.st_ino, item.st_uid, item.st_mode, item.st_nlink,
                item.st_size, item.st_mtime_ns, item.st_ctime_ns,
            )
        if (
            len(raw) > maximum
            or identity(before) != identity(after)
            or identity(after) != identity(canonical)
        ):
            _invalid()
        return bytes(raw)
    finally:
        _close_all((descriptor,))


def _private_json(path: Path) -> dict[str, Any]:
    value = json.loads(_private_bytes(path, 262144), object_pairs_hook=_pairs)
    if type(value) is not dict:
        _invalid()
    return value


def _git_blob(root: Path, sha: str, relative: str) -> bytes:
    environment = dict(os.environ)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_NO_LAZY_FETCH"] = "1"
    result = subprocess.run(
        ["git", "-c", "protocol.allow=never", "show", f"{sha}:{relative}"],
        cwd=root, env=environment, capture_output=True, check=False, timeout=30,
    )
    if result.returncode != 0 or len(result.stdout) > 4 * 1024 * 1024:
        _invalid()
    return result.stdout


def _bootstrap(state: Path, root: Path) -> None:
    """Verify own/bootstrap bytes using stdlib before effect-capable imports."""
    if (
        os.name != "posix"
        or state != state.resolve(strict=True)
        or root != root.resolve(strict=True)
    ):
        _invalid()
    config = _private_json(state / "epic-runtime.json")
    mandate = _private_json(state / "mandate.json")
    if (
        config.get("schema_version") != "8.0"
        or config.get("approved_final_digest") is not None
        or type(config.get("policy_root")) is not str
        or type(mandate.get("policy_sha")) is not str
        or re.fullmatch(r"[0-9a-f]{40}", mandate["policy_sha"]) is None
    ):
        _invalid()
    policy = Path(config["policy_root"])
    if (
        policy != policy.resolve(strict=True)
        or Path(__file__).resolve()
        != policy / "tools" / "agent_owner_final_approve.py"
    ):
        _invalid()
    # Exact original mandate digest algorithm; its full schema/lifecycle is
    # revalidated by the verified canonical runtime guard below.
    payload = dict(mandate)
    approval = payload.get("approval")
    if type(approval) is not dict:
        _invalid()
    payload["approval"] = {k: v for k, v in approval.items() if k != "mandate_digest"}
    commitment = "sha256:" + hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    if (
        commitment != config.get("approved_mandate_digest")
        or approval.get("mandate_digest") != commitment
    ):
        _invalid()
    for relative in (
        "tools/__init__.py",
        "tools/agent_epic_runtime_cli.py",
        "tools/agent_owner_final_approve.py",
    ):
        expected = _git_blob(root, mandate["policy_sha"], relative)
        if _private_bytes(policy / relative, 4 * 1024 * 1024) != expected:
            _invalid()
    existing = sys.modules.get("tools")
    if (
        existing is not None
        and Path(existing.__file__ or "").resolve()
        != policy / "tools" / "__init__.py"
    ):
        _invalid()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(policy))
    guards = importlib.import_module("tools.agent_epic_runtime_cli")
    runtime_entry = policy / "tools" / "agent_epic_runtime_cli.py"
    source_file = guards.__file__
    if source_file is None or Path(source_file).resolve() != runtime_entry:
        _invalid()
    guards.verify_policy_config8_batch(
        policy, root, mandate["policy_sha"], entry=runtime_entry,
    )
    guards.managed_launch_guard(state, root)
    guards.bootstrap_authority(config, mandate, epic=True)
    guards.secure_directory(state, root)


def _stdin(maximum: int, deadline: float) -> bytes:
    descriptor = sys.stdin.buffer.fileno()
    kind = os.fstat(descriptor).st_mode
    regular = stat.S_ISREG(kind)
    if not regular and not (stat.S_ISFIFO(kind) or os.isatty(descriptor)):
        _invalid()
    raw = bytearray()
    last = time.time()
    with selectors.DefaultSelector() as selector:
        if not regular:
            selector.register(descriptor, selectors.EVENT_READ)
        while len(raw) <= maximum:
            current = time.time()
            if current < last or current >= deadline:
                _invalid()
            last = current
            if not regular and not selector.select(deadline - current):
                _invalid()
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(raw)))
            if not chunk:
                return bytes(raw)
            raw.extend(chunk)
    _invalid()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--approved-digest", required=True)
    args = parser.parse_args()
    try:
        if re.fullmatch(r"[0-9a-f]{64}", args.approved_digest) is None:
            _invalid()
        _bootstrap(args.state_dir, args.repository_root)
        from tools.agent_epic_session_runtime import SessionRuntime
        from tools.agent_owner_final_admission import (
            _installed_sources,
            _OriginalInvocation,
            _retained_sources,
            _write_owner_originals,
        )
        from tools.agent_owner_final_input import parse_owner_input

        retained = _retained_sources(args.state_dir, args.repository_root)
        if retained is None:
            _invalid()
        original = retained["sources"]
        maximum = original["profile"]["max_artifact_bytes"]
        deadline = min(
            retained["final"]["owner_wait"]["deadline_at"],
            original["mandate"]["expires_at"],
        )
        supplied = parse_owner_input(
            _stdin(maximum, deadline), args.approved_digest,
            issued_at=original["mandate"]["issued_at"],
            expires_at=original["mandate"]["expires_at"],
            max_bytes=maximum,
        )
        with _OriginalInvocation(args.state_dir, args.repository_root) as invocation:
            _installed_sources(invocation)
            session = SessionRuntime.owner_original(
                args.state_dir, args.repository_root,
            )
            with session._stage("FINAL:FINAL_GATE"):
                scope = session.wait_scope
                if scope is None:
                    _invalid()
                from tools.agent_final_preparation_sources import (
                    _historical_capture_lifetime,
                    _owner_capture_segment,
                )

                with _historical_capture_lifetime(session):
                    with _owner_capture_segment(session):
                        scope._active_reader().owner_assess_once()
                        result = _write_owner_originals(invocation, supplied)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt):
        # Neither source paths, parser details, original prose nor transport text
        # are exported. A partial original graph remains for explicit recovery.
        print("FINAL_APPROVAL_INVALID", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
