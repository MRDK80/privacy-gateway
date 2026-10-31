"""Only invocation-bound transport errors can interrupt a role."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tools import agent_orchestrate as workflow
from tools import agent_quota_process as process
from tools import agent_quota_role as transport
from tools.agent_quota_wait import bare_digest


def event() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "role": "controller",
        "invocation_digest": bare_digest({"session_id": "synthetic"}),
        "thread_digest": "a" * 64,
        "turn_digest": "b" * 64,
        "error_code": "rateLimitExceeded",
        "backend_digest": "c" * 64,
        "outcome": "UNKNOWN",
    }


def test_bound_event_retains_unknown_outcome() -> None:
    value = transport.interruption(
        {"role_interruption": event()},
        role="controller",
        payload={"session_id": "synthetic"},
        backend_digest="c" * 64,
    )
    assert value["outcome"] == "UNKNOWN"


@pytest.mark.parametrize(
    "key,value",
    [
        ("role", "executor"),
        ("invocation_digest", "d" * 64),
        ("backend_digest", "d" * 64),
        ("outcome", "NOT_APPLIED"),
        ("error_code", "contextWindowExceeded"),
        ("error_code", {}),
        ("turn_digest", "private turn"),
        ("message", "private diagnostic"),
    ],
)
def test_error_channel_rejects_foreign_or_unproven_event(
    key: str, value: object
) -> None:
    forged = event()
    forged[key] = value
    with pytest.raises(workflow.OrchestrationError, match="ROLE_INTERRUPTION_INVALID"):
        transport.interruption(
            {"role_interruption": forged},
            role="controller",
            payload={"session_id": "synthetic"},
            backend_digest="c" * 64,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX bounded role transport")
def test_actual_child_error_channel_and_success_cannot_forge_pause(
    tmp_path: Path,
) -> None:
    import sys

    path = tmp_path / "adapter.py"
    path.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "value = " + repr(event()) + "\n"
        "print(json.dumps({'role_interruption': value}))\n"
        "sys.exit(20 if sys.argv[1] == 'quota' else 0)\n"
    )
    adapter = transport.CommandAdapter(
        [sys.executable, str(path), "quota"],
        [sys.executable, str(path), "success"],
        root=tmp_path,
        timeout_seconds=3,
        output_limit=4096,
        backend_digest="c" * 64,
        guard=lambda: None,
    )
    # The closed event is controller-bound: an executor cannot borrow it.
    with pytest.raises(workflow.OrchestrationError, match="ROLE_INTERRUPTION_INVALID"):
        adapter.execute({}, "synthetic")
    # A zero-exit model report cannot turn itself into a transport exception.
    with pytest.raises(workflow.OrchestrationError, match="MALFORMED_OUTPUT"):
        adapter.review({}, {}, "synthetic")


@pytest.mark.parametrize("role", ["executor", "controller"])
def test_windows_role_transport_refuses_before_child_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    # Replace only the runner's platform dependency, leaving pathlib intact.
    monkeypatch.setattr(process, "os", SimpleNamespace(name="nt"))
    spawn = Mock(side_effect=AssertionError("Unsupported host spawned a child"))
    monkeypatch.setattr(subprocess, "Popen", spawn)
    on_interruption = Mock()
    adapter = transport.CommandAdapter(
        ["synthetic-executor"],
        ["synthetic-controller"],
        root=tmp_path,
        timeout_seconds=3,
        output_limit=4096,
        backend_digest="c" * 64,
        guard=lambda: None,
        on_interruption=on_interruption,
    )
    with pytest.raises(workflow.OrchestrationError, match="^QUOTA_SOURCE_UNSUPPORTED$"):
        if role == "executor":
            adapter.execute({}, "synthetic")
        else:
            adapter.review({}, {}, "synthetic")
    spawn.assert_not_called()
    on_interruption.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="POSIX private diagnostic FD")
@pytest.mark.parametrize("exit_code,output", [
    (0, ""), (1, ""), (0, "private-canary"), (1, "private-canary"),
])
def test_malformed_diagnostic_is_metadata_only_and_one_shot(
    tmp_path: Path, exit_code: int, output: str,
) -> None:
    import json
    import sys

    path = tmp_path / "first.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    command = [
        sys.executable, "-c",
        f"import sys;sys.stdout.write({output!r});sys.exit({exit_code})",
    ]
    adapter = transport.CommandAdapter(
        command, command, root=tmp_path, timeout_seconds=3,
        output_limit=4096, backend_digest="c" * 64, guard=lambda: None,
    )
    try:
        with transport._malformed_diagnostic(fd):
            for _ in range(2):
                with pytest.raises(
                    workflow.OrchestrationError, match="^MALFORMED_OUTPUT$",
                ):
                    adapter._call(command, {})
    finally:
        os.close(fd)
    raw = path.read_bytes()
    assert len(raw) <= 512 and raw.count(b"\n") == 1
    assert b"private-canary" not in raw
    assert json.loads(raw) == {
        "schema_version": "diagnostic1", "role": "executor",
        "returncode": exit_code, "stdout_bytes": len(output),
        "parse_stage": "JSON_LOAD_VALUE_ERROR",
    }
    assert transport._malformed_sink.get() is None


def test_unbound_diagnostic_never_touches_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    touched = Mock(side_effect=AssertionError("unbound diagnostic touched FD"))
    with monkeypatch.context() as patch:
        patch.setattr(os, "fstat", touched)
        patch.setattr(os, "write", touched)
        transport._note_malformed_output("controller", 0, 12)
    touched.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="POSIX private diagnostic FD")
def test_diagnostic_write_error_is_secondary_and_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fd = os.open(
        tmp_path / "first.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
    )
    try:
        with transport._malformed_diagnostic(fd):
            write = Mock(side_effect=OSError("private-canary"))
            monkeypatch.setattr(os, "write", write)
            transport._note_malformed_output("controller", -9, 0)
            transport._note_malformed_output("controller", -9, 0)
            assert write.call_count == 1
            with pytest.raises(RuntimeError, match="^DIAGNOSTIC_SETUP_STOP$"):
                with transport._malformed_diagnostic(fd):
                    raise AssertionError("nested binding admitted")
    finally:
        os.close(fd)
    assert transport._malformed_sink.get() is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX private diagnostic FD")
@pytest.mark.parametrize("kind", ["mode", "hardlink", "offset", "bool", "closed"])
def test_diagnostic_invalid_binding_fails_before_body(
    tmp_path: Path, kind: str,
) -> None:
    path = tmp_path / "first.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if kind == "mode":
            path.chmod(0o644)
        elif kind == "hardlink":
            os.link(path, tmp_path / "second")
        elif kind == "offset":
            os.lseek(fd, 100, os.SEEK_SET)
        elif kind == "closed":
            os.close(fd)
        selected = True if kind == "bool" else fd
        with pytest.raises(RuntimeError, match="^DIAGNOSTIC_SETUP_STOP$"):
            with transport._malformed_diagnostic(selected):
                raise AssertionError("invalid binding reached original body")
    finally:
        if kind != "closed":
            os.close(fd)
    assert transport._malformed_sink.get() is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX private diagnostic FD")
def test_context_copy_shares_latch_and_system_exit_resets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextvars import copy_context

    fd = os.open(
        tmp_path / "first.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
    )
    write = Mock(return_value=1)  # Partial write never retries/refunds.
    try:
        with pytest.raises(SystemExit, match="17"):
            with transport._malformed_diagnostic(fd):
                monkeypatch.setattr(os, "write", write)
                copied = copy_context()
                copied.run(transport._note_malformed_output, "controller", 0, 0)
                transport._note_malformed_output("controller", 0, 0)
                assert write.call_count == 1
                raise SystemExit(17)
    finally:
        os.close(fd)
    assert transport._malformed_sink.get() is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX private diagnostic FD")
def test_native_role_does_not_inherit_diagnostic_fd(tmp_path: Path) -> None:
    import sys

    path = tmp_path / "first.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    command = [sys.executable, "-c", (
        "import os\ntry:os.fstat(" + str(fd) + ")\n"
        "except OSError:print('{}')\nelse:raise SystemExit(99)\n"
    )]
    adapter = transport.CommandAdapter(
        command, command, root=tmp_path, timeout_seconds=3,
        output_limit=4096, backend_digest="c" * 64, guard=lambda: None,
    )
    try:
        with transport._malformed_diagnostic(fd):
            assert adapter._call(command, {}) == {}
    finally:
        os.close(fd)
    assert path.read_bytes() == b""


@pytest.mark.skipif(os.name != "posix", reason="POSIX private diagnostic FD")
def test_parent_readback_rejects_raw_or_replaced_file(tmp_path: Path) -> None:
    from tests.test_agent_final_learning_lifecycle_genuine import (
        _malformed_readback,
        _malformed_sink_file,
    )

    fd, path, identity = _malformed_sink_file(tmp_path)
    try:
        os.write(fd, b'private-canary\n')
    finally:
        os.close(fd)
    result = _malformed_readback(path, identity, failed=True)
    assert result == "\nDIAGNOSTIC_UNAVAILABLE_OR_INCOMPLETE"
    assert "private-canary" not in result
    with pytest.raises(RuntimeError, match="^DIAGNOSTIC_VALIDATION_STOP$"):
        _malformed_readback(path, identity, failed=False)
    original = tmp_path / "retained"
    path.rename(original)
    path.symlink_to(original)
    assert _malformed_readback(path, identity, failed=True) == result
