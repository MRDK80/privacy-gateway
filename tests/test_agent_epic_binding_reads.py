"""Offline regression paths for guarded pinned delivery binding reads."""

import json
from pathlib import Path
from typing import Any

import pytest
from tools import agent_coordinator_handover as coordinator
from tools import agent_epic_delivery as delivery
from tools import agent_epic_loop as loop
from tools import agent_epic_recovery as recovery
from tools import agent_github_read as reads

from tests.test_agent_epic_runtime import build
from tests.test_agent_github_read import TRANSIENT, Clock, runner


@pytest.mark.parametrize("change", ["expiry", "revocation", "identity", "owner_stop"])
def test_binding_wait_uses_runtime_authority_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    engine, _ = build(tmp_path, monkeypatch, "RUN_TASK")
    saved = engine.store.load()
    assert saved is not None
    saved.update(schema_version="3.0", policy_sha="a" * 40)
    engine.store.save(saved)
    root = engine.store.repository_root
    monkeypatch.setattr(
        coordinator, "_repository_identity", lambda _: "OWNER/repository"
    )
    monkeypatch.setattr(
        coordinator,
        "_git",
        lambda _, *args: str(root) if args == ("rev-parse", "--show-toplevel") else "",
    )
    clock = Clock()
    starts: list[float] = []
    original, _, _ = engine.authority()
    mandate = dict(original)
    if change == "expiry":
        mandate["expires_at"] = 212
        approval = mandate["approval"]
        assert isinstance(approval, dict)
        approval["mandate_digest"] = delivery.mandate_digest(mandate)
    approved = delivery.mandate_digest(mandate)
    engine.authority = lambda: (
        mandate,
        approved,
        delivery.MandateContext(200 + int(clock.now), "OWNER", 150, 0, 0),
    )
    control = recovery.Control(engine.store.directory, root)
    if change == "owner_stop":
        control.save(control.load() | {"stopped": False})

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.now == 12:
            if change == "revocation":
                mandate["revoked"] = True
            elif change == "identity":
                engine.store.save(saved | {"head_sha": "d" * 40})
            elif change == "owner_stop":
                control.stop()

    item = runner(clock, [TRANSIENT, (0, "[]", "")], starts)
    item.sleep = sleep

    def factory(*, guard: Any) -> Any:
        item.guard = guard
        return item

    monkeypatch.setattr(reads, "Reader", factory)
    ledger = delivery.Ledger(engine.store.directory, root, read=engine._github_read)
    request = delivery.Request(
        operation_id="synthetic-binding",
        operation="commit_task",
        schema_version="3.0",
        policy_sha="a" * 40,
        **{key: saved[key] for key in loop.IDENTITY_KEYS},
    )
    assert delivery._binding_code(request, ledger) == "BINDING_FACTS_UNCONFIRMED"
    assert len(starts) == 1 and sum(clock.sleeps) == 5
    assert not ledger.path.exists()
    if change == "owner_stop":
        assert control.load()["stopped"] and control.load()["generation"] == 1


@pytest.mark.parametrize(
    "response",
    [
        (1, "", "unknown timeout"),
        (1, "", "HTTP 403 forbidden; TLS handshake timeout"),
        (0, "invalid JSON", ""),
        (0, json.dumps([{}]), ""),
        (
            0,
            json.dumps(
                [
                    [
                        {
                            "name": "roadmap/248-autonomous-epic-runner",
                            "commit": {"sha": "d" * 40},
                        }
                    ]
                ]
            ),
            "",
        ),
    ],
)
def test_binding_failure_does_not_retry_unconfirmed_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: tuple[int, str, str]
) -> None:
    engine, _ = build(tmp_path, monkeypatch, "RUN_TASK")
    saved = engine.store.load()
    assert saved is not None
    root = engine.store.repository_root
    monkeypatch.setattr(
        coordinator, "_repository_identity", lambda _: "OWNER/repository"
    )
    monkeypatch.setattr(
        coordinator,
        "_git",
        lambda _, *args: str(root) if args == ("rev-parse", "--show-toplevel") else "",
    )
    clock = Clock()
    starts: list[float] = []
    item = runner(clock, [response], starts)

    def factory(*, guard: Any) -> Any:
        item.guard = guard
        return item

    monkeypatch.setattr(reads, "Reader", factory)
    ledger = delivery.Ledger(engine.store.directory, root, read=engine._github_read)
    request = delivery.Request(
        operation_id="synthetic-binding",
        operation="commit_task",
        schema_version="3.0",
        policy_sha="a" * 40,
        **{key: saved[key] for key in loop.IDENTITY_KEYS},
    )
    assert delivery._binding_code(request, ledger) in {
        "BINDING_FACTS_UNCONFIRMED",
        "BINDING_BASE_MISMATCH",
    }
    assert len(starts) == 1 and not clock.sleeps and not ledger.path.exists()
