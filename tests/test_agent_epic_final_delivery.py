"""Final delivery cannot inherit task write authority or skip final gates."""

from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_epic_final as final
from tools import agent_epic_final_delivery as driver

from tests.test_agent_epic_delivery import _mandate


def request(operation: str = "merge_roadmap_pr") -> Any:
    return driver.Request(
        operation_id=operation,
        operation=operation,
        repository="OWNER/repository",
        epic=248,
        roadmap_ref="roadmap/248-autonomous-epic-runner",
        roadmap_sha="b" * 40,
        policy_sha="a" * 40,
        main_sha="a" * 40,
        pr=None if operation == "create_roadmap_pr" else 278,
        merge_sha="c" * 40 if operation in {"close_epic", "update_epic"} else None,
    )


def run(tmp_path: Path, *, granted: bool, ready: bool = True) -> Any:
    mandate = _mandate()
    mandate["schema_version"] = "3.0"
    if granted:
        mandate["operations"] = ["merge_roadmap_pr"]
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    digest = delivery.mandate_digest(mandate)
    approval["mandate_digest"] = digest
    effects: list[str] = []

    def authority() -> Any:
        return mandate, digest, delivery.MandateContext(200, "OWNER", 150, 0, 0)

    def gate(value: Any) -> final.FinalGate:
        return (
            final.FinalGate("PASS", "OK", "ROADMAP READY FOR RELEASE")
            if ready
            else final.FinalGate("BLOCKED", "FINAL_DEMO_UNCONFIRMED", "BLOCKED")
        )

    def effect(value: Any) -> dict[str, object]:
        effects.append(value.operation)
        return {"merge_sha": "c" * 40}

    result = driver.deliver(
        request(),
        authority=authority,
        gate=gate,
        ledger=delivery.Ledger(tmp_path / "private", tmp_path / "repo"),
        effect=effect,
        reconcile=lambda _: ("NOT_APPLIED", None),
    )
    assert effects == (["merge_roadmap_pr"] if granted and ready else [])
    return result


def test_task_allowlist_does_not_authorize_final_merge(tmp_path: Path) -> None:
    assert run(tmp_path, granted=False).machine_code == "MANDATE_AUTHORITY_MISSING"


def test_unconfirmed_final_demo_blocks_write(tmp_path: Path) -> None:
    assert (
        run(tmp_path, granted=True, ready=False).machine_code
        == "FINAL_DEMO_UNCONFIRMED"
    )


def test_explicit_final_authority_and_gate_allow_one_write(tmp_path: Path) -> None:
    assert run(tmp_path, granted=True).status == "APPLIED"


def test_expiry_during_intent_save_prevents_final_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mandate = _mandate()
    mandate.update(schema_version="3.0", operations=["merge_roadmap_pr"])
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approved = delivery.mandate_digest(mandate)
    approval["mandate_digest"] = approved
    ledger = delivery.Ledger(tmp_path / "private", tmp_path / "repo")
    now = 200
    original = ledger.save

    def save(entries: Any) -> None:
        nonlocal now
        original(entries)
        now = 1001

    monkeypatch.setattr(ledger, "save", save)
    result = driver.deliver(
        request(),
        authority=lambda: (
            mandate,
            approved,
            delivery.MandateContext(now, "OWNER", 150, 0, 0),
        ),
        gate=lambda _: final.FinalGate("PASS", "OK", "ROADMAP READY FOR RELEASE"),
        ledger=ledger,
        effect=lambda _: pytest.fail("no effect after expiry"),
        reconcile=lambda _: ("UNKNOWN", None),
    )
    assert result.machine_code == "MANDATE_EXPIRED"
    assert ledger.load()[request().operation_id]["status"] == "INTENT"


def test_unknown_final_merge_reconciles_without_second_write(tmp_path: Path) -> None:
    mandate = _mandate()
    mandate.update(schema_version="3.0", operations=["merge_roadmap_pr"])
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approved = delivery.mandate_digest(mandate)
    approval["mandate_digest"] = approved
    ledger = delivery.Ledger(tmp_path / "private", tmp_path / "repo")
    calls: list[str] = []

    def effect(value: Any) -> dict[str, object]:
        calls.append("write")
        raise delivery.OutcomeUnknown

    def authority() -> Any:
        return mandate, approved, delivery.MandateContext(200, "OWNER", 150, 0, 0)

    def attempt(state: str) -> Any:
        return driver.deliver(
            request(),
            authority=authority,
            gate=lambda _: final.FinalGate("PASS", "OK", "ROADMAP READY FOR RELEASE"),
            ledger=ledger,
            effect=effect,
            reconcile=lambda _: (
                state,
                {"merge_sha": "c" * 40} if state == "APPLIED" else None,
            ),
        )

    assert attempt("UNKNOWN").status == "ESCALATE"
    assert attempt("UNKNOWN").status == "ESCALATE"
    assert attempt("APPLIED").status == "NO_OP"
    assert calls == ["write"]


def test_finalization_at_exact_task_limit_does_not_start_a_task() -> None:
    mandate = _mandate()
    mandate.update(schema_version="3.0", operations=["merge_roadmap_pr"])
    approval = mandate["approval"]
    assert isinstance(approval, dict)
    approved = delivery.mandate_digest(mandate)
    approval["mandate_digest"] = approved
    context = delivery.MandateContext(200, "OWNER", 150, 10, 0)
    assert delivery.mandate_lifecycle_code(mandate, context) == "MANDATE_TASK_LIMIT"
    assert (
        driver.authorization_code(request(), lambda: (mandate, approved, context))
        is None
    )


@pytest.mark.parametrize("operation", ["close_epic", "update_epic"])
def test_final_close_requires_merge_identity(operation: str) -> None:
    from dataclasses import replace

    assert driver.valid_request(request(operation))
    assert not driver.valid_request(replace(request(operation), merge_sha=None))
