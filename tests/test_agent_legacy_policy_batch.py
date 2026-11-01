"""Fresh legacy policy proof: bounded Git fanout without cached trust."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_runtime_cli as cli

from tests.test_agent_epic_runtime_cli import policy_fixture
from tests.test_agent_epic_snapshot import git

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX frozen policy")


def observe(monkeypatch: pytest.MonkeyPatch, fault: str = "") -> list[list[str]]:
    original = subprocess.run
    calls: list[list[str]] = []

    def run(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        is_inventory = argv[1:4] == ["ls-tree", "-r", "-z"]
        if is_inventory and fault == "oserror":
            raise OSError("synthetic optimization transport failure")
        if is_inventory and fault == "timeout":
            raise subprocess.TimeoutExpired(argv, 30)
        if is_inventory and fault == "interrupt":
            raise KeyboardInterrupt()
        r = original(argv, *args, **kwargs)
        if is_inventory and fault.startswith("inventory_"):
            data = r.stdout
            if fault == "inventory_nonzero":
                return subprocess.CompletedProcess(r.args, 1, b"", b"synthetic failure")
            if fault == "inventory_nul":
                data = data[:-1]
            elif fault == "inventory_mode":
                data = b"invalid" + data[6:]
            elif fault == "inventory_order":
                data = b"\0".join(reversed(data[:-1].split(b"\0"))) + b"\0"
            elif fault == "inventory_cap":
                data = b"x" * (1024 * 1024 + 1)
            return subprocess.CompletedProcess(r.args, 0, data, r.stderr)
        if argv[0] == "git":
            calls.append(argv[1:])
        if argv[1:] == ["cat-file", "--batch"] and fault:
            data = r.stdout
            if fault == "empty":
                data = b""
            elif fault == "truncated":
                data = data[:-1]
            elif fault == "extra":
                data += b"extra\n"
            elif fault == "wrong_hash":
                data = b"f" * 40 + data[40:]
            elif fault == "wrong_type":
                data = data.replace(b" blob ", b" tree ", 1)
            elif fault == "payload":
                start = data.index(b"\n") + 1
                data = data[:start] + bytes([data[start] ^ 1]) + data[start + 1 :]
            elif fault == "object_cap":
                end = data.index(b"\n")
                data = data[:end].rsplit(b" ", 1)[0] + b" 8388609\n" + data[end + 1 :]
            elif fault == "reordered":
                end = data.index(b"\n")
                cut = end + 1 + int(data[:end].split()[-1]) + 1
                data = data[cut:] + data[:cut]
            return subprocess.CompletedProcess(
                r.args, 1 if fault == "nonzero" else 0, data, r.stderr
            )
        return r

    monkeypatch.setattr(subprocess, "run", run)
    return calls


@pytest.mark.parametrize("extra_count", [0, 40])
def test_fresh_proof_has_constant_git_fanout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_count: int
) -> None:
    root, policy, sha, entry = policy_fixture(tmp_path)
    for index in range(extra_count):
        relative = f"tools/synthetic_{index}.py"
        (root / relative).write_bytes(b"pass\n")
        (policy / relative).write_bytes(b"pass\n")
        (policy / relative).chmod(0o600)
    if extra_count:
        git(root, "add", "tools")
        git(root, "commit", "-qm", "synthetic inventory growth")
        sha = git(root, "rev-parse", "HEAD")
    calls = observe(monkeypatch)
    cli.verify_policy(policy, root, sha, entry=entry)
    assert [c[0] for c in calls] == ["show", "ls-tree", "ls-tree", "cat-file"]
    calls.clear()
    (policy / "tools/codex_adapter.py").write_bytes(b"fresh mutation\n")
    with pytest.raises(cli.DeploymentError, match="^UNTRUSTED_RUNTIME_COMMAND$"):
        cli.verify_policy(policy, root, sha, entry=entry)
    assert [c[0] for c in calls] == ["show", "ls-tree", "ls-tree", "cat-file"]


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "truncated",
        "extra",
        "wrong_hash",
        "wrong_type",
        "nonzero",
        "reordered",
        "payload",
        "object_cap",
        "inventory_nonzero",
        "inventory_nul",
        "inventory_mode",
        "inventory_order",
        "inventory_cap",
        "oserror",
        "timeout",
    ],
)
def test_invalid_batch_uses_legacy_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root, policy, sha, entry = policy_fixture(tmp_path)
    calls = observe(monkeypatch, fault)
    cli.verify_policy(policy, root, sha, entry=entry)
    if fault.startswith("inventory_") or fault in {"oserror", "timeout"}:
        assert ["cat-file", "--batch"] not in calls
    else:
        assert ["cat-file", "--batch"] in calls
    assert sum(c[0] == "show" for c in calls) > 1
    (policy / "tools/codex_adapter.py").write_bytes(b"changed\n")
    with pytest.raises(cli.DeploymentError, match="^UNTRUSTED_RUNTIME_COMMAND$"):
        cli.verify_policy(policy, root, sha, entry=entry)


def test_fallback_preserves_local_failure_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, policy, sha, entry = policy_fixture(tmp_path)
    (policy / "tools/codex_adapter.py").chmod(0o644)
    observe(monkeypatch, "nonzero")
    with pytest.raises(cli.DeploymentError, match="^UNTRUSTED_RUNTIME_COMMAND$"):
        cli.verify_policy(policy, root, sha, entry=entry)


@pytest.mark.parametrize("relative", ["tools/binary.py", "tools/name with space.py"])
def test_binary_and_legacy_path_parity(tmp_path: Path, relative: str) -> None:
    root, policy, _, entry = policy_fixture(tmp_path)
    contents = b"\x00\xff\nsynthetic\r\n"
    (root / relative).write_bytes(contents)
    (policy / relative).write_bytes(contents)
    (policy / relative).chmod(0o600)
    git(root, "add", "tools")
    git(root, "commit", "-qm", "additional synthetic policy")
    cli.verify_policy(policy, root, git(root, "rev-parse", "HEAD"), entry=entry)


def test_replace_objects_keep_legacy_semantics(tmp_path: Path) -> None:
    root, policy, sha, entry = policy_fixture(tmp_path)
    oid = git(root, "rev-parse", sha + ":tools/codex_adapter.py")
    replacement = root / "replacement"
    replacement.write_bytes(b"replacement bytes\n")
    new_oid = git(root, "hash-object", "-w", str(replacement))
    git(root, "replace", oid, new_oid)
    for name in ["codex_adapter.py", "agent_epic_runtime.py"]:
        (policy / "tools" / name).write_bytes(b"replacement bytes\n")
    cli.verify_policy(policy, root, sha, entry=entry)


def test_fast_path_does_not_swallow_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, policy, sha, entry = policy_fixture(tmp_path)
    observe(monkeypatch, "interrupt")
    with pytest.raises(KeyboardInterrupt):
        cli.verify_policy(policy, root, sha, entry=entry)
