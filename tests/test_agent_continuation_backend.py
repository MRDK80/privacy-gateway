"""Synthetic pinned settings and native-origin receipt boundaries (#301)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_continuation as records
from tools import agent_continuation_backend as backend


def document() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "source": {
            "executable": "/synthetic/native",
            "executable_sha256": "a" * 64,
            "version_digest": "b" * 64,
            "request_timeout_seconds": 5,
        },
        "model": "synthetic-model",
        "original_reasoning": "low",
        "repeat_reasoning_levels": ["medium", "high"],
    }


@pytest.mark.parametrize("change", ["unknown", "timeout", "model", "efforts"])
def test_backend_contract_rejects_unapproved_source_and_settings(change: str) -> None:
    value = document()
    if change == "unknown":
        value["prompt"] = "synthetic"
    elif change == "timeout":
        value["source"]["request_timeout_seconds"] = True
    elif change == "model":
        value["model"] = "synthetic\nmodel"
    else:
        value["repeat_reasoning_levels"] = ["medium", "max"]
    with pytest.raises(records.ContinuationError):
        backend.document(value)


def test_completion_receipt_binds_actual_output_and_observed_exit() -> None:
    owner = document()
    observed = {
        "model": owner["model"],
        "reasoning": "low",
        "thread_digest": "c" * 64,
        "turn_digest": "d" * 64,
    }
    output = {"synthetic": True}
    receipt, terminal = backend.completed_receipt(
        owner, observed, role="controller", invocation="e" * 64, output=output
    )
    assert (
        backend.validate_completion(
            receipt,
            terminal,
            owner,
            role="controller",
            invocation="e" * 64,
            output=output,
            observed_exit=0,
            reasoning="low",
        )
        == receipt
    )
    for exit_code in (None, 1):
        with pytest.raises(records.ContinuationError):
            backend.validate_completion(
                receipt,
                terminal,
                owner,
                role="controller",
                invocation="e" * 64,
                output=output,
                observed_exit=exit_code,
                reasoning="low",
            )
    receipt["effective"]["reasoning"] = "high"
    receipt["receipt_digest"] = records.digest(
        {k: v for k, v in receipt.items() if k != "receipt_digest"}
    )
    with pytest.raises(records.ContinuationError):
        backend.validate_completion(
            receipt,
            terminal,
            owner,
            role="controller",
            invocation="e" * 64,
            output=output,
            observed_exit=0,
            reasoning="low",
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX pinned executable ownership")
def test_transport_readiness_checks_pinned_version_without_inference(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from tools import agent_epic_runtime_cli as guards

    executable = tmp_path / "synthetic-codex"
    executable.write_bytes(b"synthetic fixed native source")
    executable.chmod(0o700)
    value = document()
    value["source"]["executable"] = str(executable)
    value["source"]["executable_sha256"] = hashlib.sha256(
        executable.read_bytes()
    ).hexdigest()
    value["source"]["version_digest"] = hashlib.sha256(
        b"codex-cli 0.157.1"
    ).hexdigest()
    calls: list[list[str]] = []

    def read(command: list[str]) -> bytes:
        calls.append(command)
        return b"codex-cli 0.157.1\n"

    monkeypatch.setattr(guards, "_quota_read", read)
    guards.continuation_transport_guard(value)
    assert calls == [[str(executable), "--version"]]
    monkeypatch.setattr(guards, "_quota_read", lambda _: b"codex-cli unknown")
    with pytest.raises(guards.DeploymentError):
        guards.continuation_transport_guard(value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX private settings lock/store")
def test_settings_adapter_observes_child_exit_and_retains_single_source(
    tmp_path: Any,
) -> None:
    import json
    import sys

    from tools import agent_orchestrate as workflow

    from tests.test_agent_continuation import owner as profile_document

    root = tmp_path / "repo"
    root.mkdir()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    source = document()
    profile = profile_document()
    profile["backend_digest"] = records.digest(source)
    profile["limits"]["max_prompt_bytes"] = 262144
    script = state / "synthetic-child.py"
    script.write_text(
        "import json, sys\n"
        "sys.path.insert(0, " + repr(str(Path.cwd())) + ")\n"
        "from tools.agent_continuation_backend import completed_receipt\n"
        "from tools.agent_continuation import digest\n"
        "payload = json.load(sys.stdin)\n"
        "output = {'synthetic': True}\n"
        "owner = " + repr(source) + "\n"
        "observed = {'model': owner['model'], 'reasoning': 'low', "
        "'thread_digest': 'c'*64, 'turn_digest': 'd'*64}\n"
        "receipt, terminal = completed_receipt(owner, observed, role='executor', "
        "invocation=digest(payload), output=output)\n"
        "print(json.dumps({'role_output': output, 'settings_receipt': receipt, "
        "'terminal_fact': terminal}))\n"
    )
    delegate = workflow.CommandAdapter(
        [sys.executable, str(script)], [sys.executable, str(script)],
        root=root, timeout_seconds=3, output_limit=65536,
    )
    adapter = backend.SettingsAdapter(delegate, source, profile, state, lambda: None)
    request = {"contract": {"issue": 301}, "repair_iteration": 0,
               "synthetic": True}
    assert adapter.execute(request, "synthetic") == {"synthetic": True}
    invocation = records.digest({**request, "session_id": "synthetic"})
    retained = adapter.store.load(invocation)
    assert retained is not None
    assert retained["terminal"]["adapter_exit_code"] == 0
    assert retained["receipt"]["effective"]["reasoning"] == "low"
    with pytest.raises(records.ContinuationError, match="SETTINGS_CONFLICT"):
        adapter.execute(request, "synthetic")
    assert json.loads(
        (state / "role-settings" / ("receipt-" + invocation + ".json")).read_text()
    ) == retained
