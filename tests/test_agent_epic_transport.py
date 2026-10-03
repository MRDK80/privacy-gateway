"""Concrete delivery argv and fail-closed reconciliation, no network."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_epic_transport as transport

from tests.test_agent_coordinator_delivery import _gate


def report() -> dict[str, Any]:
    gate = _gate()
    gate["checks"][0]["metrics"] = {"passed": 10, "skipped": 0}
    gate["checks"][1]["metrics"] = {"errors": 0}
    gate["checks"][2]["metrics"] = {"source_files": 1}
    gate["checks"][3]["metrics"] = {
        "hooks": [{"id": "Detect secrets", "outcome": "passed"}]
    }
    return {
        "gate": gate,
        "environment": {"python_version": "3.12.3"},
        "allowed_paths": ["task.txt"],
        "review_verdict": "PASS",
    }


def metadata(argv: Sequence[str]) -> str:
    if argv[1] == "diff":
        return "task.txt\n"
    if str(argv[-1]).endswith("pyproject.toml"):
        return (
            '[project]\nrequires-python = ">=3.11"\nclassifiers = ['
            '"Programming Language :: Python :: 3.11", '
            '"Programming Language :: Python :: 3.12"]\n'
        )
    return (
        "jobs:\n  test:\n    strategy:\n      matrix:\n"
        "        os: [ubuntu-latest, windows-latest]\n"
        '        python-version: ["3.11", "3.12"]\n'
    )


def test_task_pr_body_reports_primary_gate_and_pending_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = transport.GhTaskTransport(tmp_path)
    setattr(client, "reporting", report)
    monkeypatch.setattr(client, "command", metadata)
    calls: list[tuple[str, ...]] = []

    def submit(argv: Sequence[str]) -> dict[str, int]:
        calls.append(tuple(argv))
        return {"number": 278}

    monkeypatch.setattr(client, "json", submit)
    monkeypatch.setattr(client, "reconcile", lambda _: ("APPLIED", {"pr": 278}))
    client.effect(request("create_task_pr"))
    body = next(part[5:] for part in calls[0] if part.startswith("body="))
    assert "Python 3.12.3" in body and "10 passed" in body
    assert "mypy .: passed; 1 source files" in body
    assert "pre-commit run --all-files" in body and "Detect secrets" in body
    assert "GitHub Review absent" in body and "PR CI: pending" in body
    assert "post-merge CI: pending" in body
    assert "pip install -e" in body and ">=3.11" in body
    assert "a" * 40 in body and "b" * 40 in body and "task.txt" in body


def test_missing_primary_reporting_blocks_pr_before_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = transport.GhTaskTransport(tmp_path)
    monkeypatch.setattr(client, "json", lambda _: pytest.fail("no PR write"))
    with pytest.raises(delivery.OutcomeUnknown):
        client.effect(request("create_task_pr"))


@pytest.mark.parametrize("failure", ["revoke", "expire"])
def test_authority_changed_during_body_preparation_prevents_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from tests.test_agent_epic_delivery import _mandate

    client = transport.GhTaskTransport(tmp_path)
    value = request("create_task_pr")
    mandate = _mandate()
    mandate.update(
        roadmap_ref=value.base_ref,
        task_grants={
            "274": {"causal_scope": ["synthetic"], "allowed_paths": ["task.txt"]}
        },
    )
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approved = delivery.mandate_digest(mandate)
    approval["mandate_digest"] = approved
    now = 200

    def read(argv: Sequence[str]) -> str:
        nonlocal now
        result = metadata(argv)
        if failure == "revoke":
            mandate["revoked"] = True
        else:
            now = 1001
        return result

    def guard(current: delivery.Request) -> None:
        if (
            delivery._authorized(
                current,
                mandate,
                approved,
                delivery.MandateContext(now, "OWNER", 150, 0, 0),
            )
            is not None
        ):
            raise delivery.OutcomeUnknown

    setattr(client, "reporting", report)
    setattr(client, "before_write", guard)
    monkeypatch.setattr(client, "command", read)
    monkeypatch.setattr(
        client, "json", lambda _: pytest.fail("no POST after loss of authority")
    )
    with pytest.raises(delivery.OutcomeUnknown):
        client.effect(value)


def request(operation: str) -> delivery.Request:
    return delivery.Request(
        operation_id="synthetic-operation",
        operation=operation,
        repository="OWNER/repository",
        epic=248,
        task=274,
        pr=None if operation in {"push_task", "create_task_pr"} else 278,
        base_ref="roadmap/248-test",
        base_sha="a" * 40,
        head_ref="codex/274-test",
        head_sha="b" * 40,
        merge_sha="c" * 40 if operation in {"close_task", "update_epic"} else None,
        schema_version="2.0",
    )


def capture(
    client: transport.GhTaskTransport,
    calls: list[tuple[str, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def command(argv: Sequence[str]) -> str:
        calls.append(tuple(argv))
        return ""

    monkeypatch.setattr(client, "command", command)


def test_push_pins_exact_head_and_never_force_pushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []
    value = request("push_task")
    client = transport.GhTaskTransport(tmp_path)
    capture(client, calls, monkeypatch)
    monkeypatch.setattr(
        client, "reconcile", lambda _: ("APPLIED", {"head_sha": value.head_sha})
    )
    assert client.effect(value)["head_sha"] == value.head_sha
    assert calls == [
        (
            "git",
            "push",
            "--",
            "https://github.com/OWNER/repository.git",
            "b" * 40 + ":refs/heads/codex/274-test",
        )
    ]


def test_merge_matches_head_and_does_not_bypass_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []
    client = transport.GhTaskTransport(tmp_path)
    capture(client, calls, monkeypatch)
    monkeypatch.setattr(
        client, "reconcile", lambda _: ("APPLIED", {"merge_sha": "c" * 40})
    )
    client.effect(request("merge_task_pr"))
    assert calls == [
        (
            "gh",
            "pr",
            "merge",
            "278",
            "--repo",
            "OWNER/repository",
            "--merge",
            "--match-head-commit",
            "b" * 40,
        )
    ]
    assert "--admin" not in calls[0]


def test_ambiguous_create_pr_is_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = transport.GhTaskTransport(tmp_path)
    monkeypatch.setattr(client, "json", lambda _: [[{"number": 278}, {"number": 279}]])
    assert client.reconcile(request("create_task_pr")) == ("UNKNOWN", None)


def test_duplicate_epic_markers_are_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = transport.GhTaskTransport(tmp_path)
    value = request("update_epic")
    monkeypatch.setattr(
        client,
        "json",
        lambda _: [
            [
                {"id": 1, "body": client.comment(value)},
                {"id": 2, "body": client.comment(value)},
            ]
        ],
    )
    assert client.reconcile(value) == ("UNKNOWN", None)


def test_write_error_never_exposes_raw_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = transport.GhTaskTransport(tmp_path)

    def unavailable(_: object) -> str:
        raise delivery.OutcomeUnknown

    monkeypatch.setattr(client, "command", unavailable)
    with pytest.raises(delivery.OutcomeUnknown):
        client.effect(request("merge_task_pr"))


def test_transport_rejects_unsupported_operation_before_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = transport.GhTaskTransport(tmp_path)
    monkeypatch.setattr(client, "command", lambda _: pytest.fail("must not execute"))
    with pytest.raises(delivery.OutcomeUnknown):
        client.effect(replace(request("push_task"), operation="force_push"))
