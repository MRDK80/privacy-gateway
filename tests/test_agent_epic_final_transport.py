"""Final argv transport identity, uncertainty and duplicate-PR guards."""

from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_epic_final_delivery as driver
from tools.agent_epic_final_transport import GhFinalTransport

from tests.test_agent_epic_final_delivery import request
from tests.test_agent_epic_transport import metadata, report


def test_roadmap_pr_body_reports_full_gate_and_pending_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = GhFinalTransport(tmp_path)
    setattr(client, "reporting", report)
    monkeypatch.setattr(client, "command", metadata)
    bodies: list[str] = []

    def submit(argv: tuple[str, ...]) -> dict[str, Any]:
        if "POST" in argv:
            bodies.append(next(part[5:] for part in argv if part.startswith("body=")))
            return {"number": 278}
        return info()

    monkeypatch.setattr(client, "json", submit)
    assert client.effect(request("create_roadmap_pr")) == {"pr": 278}
    assert "Python 3.12.3" in bodies[0] and "10 passed" in bodies[0]
    assert "base: main" in bodies[0] and "GitHub Review absent" in bodies[0]
    assert "post-merge CI: pending" in bodies[0]


@pytest.mark.parametrize("failure", ["revoke", "expire"])
def test_final_body_preparation_cannot_outlive_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from tests.test_agent_epic_delivery import _mandate

    client = GhFinalTransport(tmp_path)
    mandate = _mandate()
    mandate.update(schema_version="3.0", operations=["create_roadmap_pr"])
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approved = delivery.mandate_digest(mandate)
    approval["mandate_digest"] = approved
    now = 200

    def read(argv: tuple[str, ...]) -> str:
        nonlocal now
        result = metadata(argv)
        if failure == "revoke":
            mandate["revoked"] = True
        else:
            now = 1001
        return result

    def guard(value: Any) -> None:
        if (
            driver.authorization_code(
                value,
                lambda: (
                    mandate,
                    approved,
                    delivery.MandateContext(now, "OWNER", 150, 0, 0),
                ),
            )
            is not None
        ):
            raise delivery.OutcomeUnknown

    setattr(client, "reporting", report)
    setattr(client, "before_write", guard)
    monkeypatch.setattr(client, "command", read)
    monkeypatch.setattr(
        client, "json", lambda _: pytest.fail("no POST after authority expires")
    )
    with pytest.raises(delivery.OutcomeUnknown):
        client.effect(request("create_roadmap_pr"))


def info(number: int = 278) -> dict[str, Any]:
    return {
        "number": number,
        "state": "OPEN",
        "isCrossRepository": False,
        "baseRefName": "main",
        "baseRefOid": "a" * 40,
        "headRefName": "roadmap/248-autonomous-epic-runner",
        "headRefOid": "b" * 40,
        "mergeCommit": None,
    }


def test_final_merge_uses_exact_head_without_admin_or_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = GhFinalTransport(tmp_path)
    calls: list[tuple[str, ...]] = []

    def command(argv: tuple[str, ...]) -> str:
        calls.append(tuple(argv))
        return ""

    monkeypatch.setattr(transport, "command", command)
    monkeypatch.setattr(
        transport,
        "json",
        lambda argv: info() | {"state": "MERGED", "mergeCommit": {"oid": "c" * 40}},
    )
    assert transport.effect(request())["merge_sha"] == "c" * 40
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


@pytest.mark.parametrize(
    "field,value",
    [
        ("isCrossRepository", True),
        ("baseRefOid", "c" * 40),
        ("headRefOid", "d" * 40),
        ("baseRefName", "other"),
    ],
)
def test_final_reconciliation_rejects_changed_pr_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    transport = GhFinalTransport(tmp_path)
    monkeypatch.setattr(transport, "json", lambda argv: info() | {field: value})
    assert transport.reconcile(request()) == ("UNKNOWN", None)


def test_duplicate_final_pr_is_unknown_not_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = GhFinalTransport(tmp_path)

    def read(argv: tuple[str, ...]) -> object:
        if "--paginate" in argv:
            return [[{"number": 278}, {"number": 279}]]
        return info(int(argv[3]))

    monkeypatch.setattr(transport, "json", read)
    assert transport.reconcile(request("create_roadmap_pr")) == ("UNKNOWN", None)


def test_nonzero_write_is_not_claimed_as_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = GhFinalTransport(tmp_path)

    def unavailable(argv: tuple[str, ...]) -> str:
        raise delivery.OutcomeUnknown

    monkeypatch.setattr(transport, "command", unavailable)
    with pytest.raises(delivery.OutcomeUnknown):
        transport.effect(request())
