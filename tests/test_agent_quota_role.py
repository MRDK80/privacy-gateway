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
