"""Real child-boundary tests over an isolated synthetic App Server."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from tools import agent_quota_source as source
from tools.agent_quota import QuotaError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX quota source")


def fake(tmp_path: Path, *, forbidden: bool = False) -> dict[str, object]:
    executable = tmp_path / "codex-fixture"
    executable.write_text(
        """#!/usr/bin/python3
import json, sys
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.157.1")
    sys.exit(0)
assert sys.argv[1:] == ["app-server"]
first = json.loads(sys.stdin.readline())
assert first["method"] == "initialize"
print(json.dumps({"id": 1, "result": {}}), flush=True)
ack = json.loads(sys.stdin.readline())
assert ack["method"] == "initialized"
read = json.loads(sys.stdin.readline())
assert read["method"] == "account/rateLimits/read"
"""
        + (
            'print(json.dumps({"id": 8, '
            '"method": "item/commandExecution/requestApproval"}), flush=True)\n'
            if forbidden
            else """print(json.dumps({"id": 2, "result": {
    "rateLimitsByLimitId": {"codex": {
    "limitId": "codex", "primary": {"usedPercent": 100, "resetsAt": 9000},
    "secondary": None
}}}}), flush=True)
"""
        )
    )
    executable.chmod(0o700)
    return {
        "executable": str(executable),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "version_digest": hashlib.sha256(b"codex-cli 0.157.1").hexdigest(),
        "request_timeout_seconds": 3,
    }


def test_fixed_read_protocol(tmp_path: Path) -> None:
    calls: list[int] = []
    result = source.read(fake(tmp_path), "codex", guard=lambda: calls.append(1))
    assert result == {
        "primary": {"usedPercent": 100, "resetsAt": 9000},
        "secondary": None,
    }
    assert len(calls) >= 3


def test_never_answers_server_approval(tmp_path: Path) -> None:
    with pytest.raises(QuotaError, match="QUOTA_SOURCE_REQUEST_FORBIDDEN"):
        source.read(fake(tmp_path, forbidden=True), "codex", guard=lambda: None)


def test_changed_binary_is_blocked_before_execution(tmp_path: Path) -> None:
    config = fake(tmp_path)
    Path(str(config["executable"])).write_text("changed")
    with pytest.raises(QuotaError, match="QUOTA_SOURCE_CHANGED"):
        source.read(config, "codex", guard=lambda: None)


@pytest.mark.parametrize("rpc_id", [True, 2, 99])
def test_initialize_response_requires_exact_integer_rpc_id(
    tmp_path: Path,
    rpc_id: object,
) -> None:
    config = fake(tmp_path)
    path = Path(str(config["executable"]))
    script = path.read_text().replace(
        '{"id": 1, "result": {}}',
        '{"id": ' + repr(rpc_id) + ', "result": {}}',
    )
    path.write_text(script)
    config["executable_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(QuotaError, match="QUOTA_SOURCE_PROTOCOL_INVALID"):
        source.read(config, "codex", guard=lambda: None)
