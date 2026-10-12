"""Actual synthetic child boundaries for first-party role error attribution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from tools import agent_role_app_server as roles

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX role backend")


def child(tmp_path: Path, *, code: str | None = None, foreign: bool = False) -> Path:
    path = tmp_path / "role-fixture.py"
    script = "code = " + repr(code) + "\nforeign = " + repr(foreign) + "\n"
    script += """import json, sys

def read(method):
    value = json.loads(sys.stdin.readline())
    assert value["method"] == method
    return value

def emit(value):
    print(json.dumps(value), flush=True)
read("initialize")
emit({"id": 1, "result": {}})
read("initialized")
start = read("thread/start")
assert start["params"]["ephemeral"] is True
assert start["params"]["approvalPolicy"] == "never"
assert start["params"]["config"]["project_doc_max_bytes"] == 0
emit({"id": 2, "result": {"thread": {"id": "synthetic-thread"},
    "approvalPolicy": "never", "cwd": start["params"]["cwd"],
    "instructionSources": [], "sandbox": {"type": "readOnly"}}})
read("turn/start")
emit({"id": 3, "result": {"turn": {"id": "synthetic-turn"}}})
if code:
    emit({"method": "error", "params": {
        "threadId": "foreign" if foreign else "synthetic-thread",
        "turnId": "synthetic-turn", "willRetry": False,
        "error": {"message": "private diagnostic deliberately not propagated",
            "codexErrorInfo": code}}})
    emit({"method": "turn/completed", "params": {"threadId": "synthetic-thread",
        "turn": {"id": "synthetic-turn", "status": "failed"}}})
else:
    emit({"method": "item/completed", "params": {"threadId": "synthetic-thread",
        "turnId": "synthetic-turn", "item": {"type": "agentMessage",
            "text": json.dumps({"synthetic": True})}}})
    emit({"method": "turn/completed", "params": {"threadId": "synthetic-thread",
        "turn": {"id": "synthetic-turn", "status": "completed"}}})
"""
    path.write_text(script)
    return path


def run(path: Path) -> dict[str, object]:
    # Synthetic source only; this test is not an actual namespace readiness probe.
    return roles.run(
        [sys.executable, str(path), "--ro-bind"],
        role="controller",
        workdir=path.parent,
        prompt="synthetic",
        schema={},
        model=None,
        timeout=3,
        output_limit=1000,
        invocation="a" * 64,
        backend_digest="b" * 64,
        environment=dict(os.environ),
        guard=lambda: None,
    )


def test_success_returns_only_final_json(tmp_path: Path) -> None:
    assert run(child(tmp_path)) == {"synthetic": True}


@pytest.mark.parametrize("code", ["rateLimitExceeded", "usageLimitExceeded"])
def test_terminal_bound_first_party_quota_is_redacted(
    tmp_path: Path, code: str
) -> None:
    with pytest.raises(roles.QuotaInterrupted) as error:
        run(child(tmp_path, code=code))
    event = error.value.event
    assert event["error_code"] == code
    assert event["invocation_digest"] == "a" * 64
    assert event["outcome"] == "UNKNOWN"
    assert "message" not in event and "private" not in str(event)


def test_foreign_turn_never_classifies_quota(tmp_path: Path) -> None:
    with pytest.raises(roles.RoleError, match="ROLE_EVENT_BINDING_CHANGED"):
        run(child(tmp_path, code="rateLimitExceeded", foreign=True))


def test_context_limit_is_separate_from_quota(tmp_path: Path) -> None:
    with pytest.raises(roles.RoleError, match="ROLE_NON_QUOTA_ERROR"):
        run(child(tmp_path, code="contextWindowExceeded"))


def test_duplicate_initialize_never_starts_second_thread(tmp_path: Path) -> None:
    path = child(tmp_path)
    path.write_text(
        path.read_text().replace(
            'emit({"id": 1, "result": {}})',
            'emit({"id": 1, "result": {}})\nemit({"id": 1, "result": {}})',
        )
    )
    with pytest.raises(roles.RoleError, match="ROLE_PROTOCOL_INVALID"):
        run(path)


def test_malformed_effective_sandbox_fails_closed(tmp_path: Path) -> None:
    path = child(tmp_path)
    path.write_text(
        path.read_text().replace(
            '"sandbox": {"type": "readOnly"}',
            '"sandbox": "readOnly"',
        )
    )
    with pytest.raises(roles.RoleError, match="ROLE_EFFECTIVE_POLICY_UNVERIFIED"):
        run(path)


def test_blocked_prompt_pipe_obeys_transport_deadline(tmp_path: Path) -> None:
    import time

    path = child(tmp_path)
    path.write_text(
        path.read_text().replace(
            'read("turn/start")',
            'import time\ntime.sleep(10)\nread("turn/start")',
        )
    )
    started = time.monotonic()
    with pytest.raises(roles.RoleError, match="ROLE_BACKEND_TIMEOUT"):
        roles.run(
            [sys.executable, str(path), "--ro-bind"],
            role="controller",
            workdir=tmp_path,
            prompt="synthetic" * 100_000,
            schema={},
            model=None,
            timeout=1,
            output_limit=1000,
            invocation="a" * 64,
            backend_digest="b" * 64,
            environment=dict(os.environ),
            guard=lambda: None,
        )
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("effective", ["low", "high", None])
def test_config6_requires_bound_effective_reasoning_before_turn(
    tmp_path: Path, effective: str | None
) -> None:
    path = child(tmp_path)
    s = path.read_text().replace(
        '"instructionSources": [], "sandbox": {"type": "readOnly"}}})',
        '"instructionSources": [], "model": "synthetic-model", '
        + '"reasoningEffort": ' + repr(effective)
        + ', "sandbox": {"type": "readOnly"}}})',
    ).replace(
        'read("turn/start")',
        'assert start["params"]["config"]["model_reasoning_effort"] == "low"\n'
        'turn = read("turn/start")\n'
        'assert "effort" not in turn["params"] and "model" not in turn["params"]',
    )
    path.write_text(s)
    observed: list[dict[str, object]] = []
    def invoke() -> dict[str, object]:
        return roles.run(
            [sys.executable, str(path), "--ro-bind"], role="controller",
            workdir=tmp_path, prompt="synthetic", schema={},
            model="synthetic-model", timeout=3, output_limit=1000,
            invocation="a" * 64, backend_digest="b" * 64,
            environment=dict(os.environ), guard=lambda: None, reasoning="low",
            on_settings=observed.append,
        )
    if effective == "low":
        assert invoke() == {"synthetic": True}
        assert len(observed) == 1
        assert observed[0]["model"] == "synthetic-model"
        assert observed[0]["reasoning"] == "low"
        assert set(observed[0]) == {
            "model", "reasoning", "thread_digest", "turn_digest"
        }
    else:
        with pytest.raises(roles.RoleError, match="ROLE_EFFECTIVE_POLICY_UNVERIFIED"):
            invoke()
        assert not observed
