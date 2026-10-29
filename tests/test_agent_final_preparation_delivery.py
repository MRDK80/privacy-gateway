"""Core-only trusted callbacks: preparation cannot inherit final write authority."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_final_preparation_delivery as driver
from tools import agent_final_preparation_records as records
from tools.agent_epic_loop import PhaseBlocked

from tests.test_agent_epic_delivery import _mandate


class MemoryLedger:
    """Core persistence fault fixture, not an installed private-file proof."""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}
        self.after_save: Any = None

    def assert_owned(self) -> None:
        pass

    def load(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self.values)

    def save(self, entries: dict[str, dict[str, Any]]) -> None:
        self.values = copy.deepcopy(entries)
        if self.after_save is not None:
            self.after_save(entries)


class Sources:
    def __init__(self) -> None:
        self.mandate = _mandate()
        self.mandate.update(schema_version="3.0", operations=["create_roadmap_pr"])
        self.approved = delivery.mandate_digest(self.mandate)
        approval = self.mandate["approval"]
        assert isinstance(approval, dict)
        approval["mandate_digest"] = self.approved
        self.now = 200
        self.ledger = MemoryLedger()
        self.effects: list[str] = []
        self.reconciliations = 0
        self.mode = "NOT_APPLIED"
        self.checks = 0
        self.ready = records.seal(
            {
                "schema_version": "1.0",
                "status": "READY",
                "machine_code": "OK",
                **{
                    key: "c" * 64
                    for key in records.ASSESSMENT - {"status", "machine_code"}
                },
            }
        )
        self.request = records.seal(
            {
                "schema_version": "1.0",
                "repository": self.mandate["repository"],
                "epic": self.mandate["epic"],
                "roadmap_ref": self.mandate["roadmap_ref"],
                "main_sha": "a" * 40,
                "roadmap_sha": "b" * 40,
                "policy_sha": self.mandate["policy_sha"],
                "mandate_digest": self.approved,
                "launch_digest": "sha256:" + "b" * 64,
                "operation": "create_roadmap_pr",
                "preparation_digest": self.ready["record_digest"],
            },
            self_digest="operation_id",
        )

    def authority(self) -> Any:
        return (
            self.mandate,
            self.approved,
            delivery.MandateContext(self.now, "OWNER", 150, 0, 0),
        )

    def binding(self, value: Any) -> None:
        if (
            value["mandate_digest"] != self.approved
            or value["launch_digest"] != self.request["launch_digest"]
        ):
            raise PhaseBlocked("FINAL_APPROVAL_INVALID")

    def assessment(self, value: Any) -> dict[str, Any]:
        self.checks += 1
        return self.ready

    def receipt(self, value: Any) -> dict[str, object]:
        return {
            "pr": 50,
            "repository": value.repository,
            "base_ref": "main",
            "head_ref": value.roadmap_ref,
            "main_sha": value.main_sha,
            "roadmap_sha": value.roadmap_sha,
            "operation_id": value.operation_id,
        }

    def effect(self, value: Any) -> dict[str, object]:
        self.effects.append(value.operation)
        return self.receipt(value)

    def reconcile(self, value: Any) -> tuple[str, dict[str, object] | None]:
        self.reconciliations += 1
        return self.mode, self.receipt(value) if self.mode == "APPLIED" else None

    def run(self) -> delivery.Result:
        return driver.deliver(
            self.request,
            authority=self.authority,
            binding=self.binding,
            assessment=self.assessment,
            ledger=self.ledger,
            effect=self.effect,
            reconcile=self.reconcile,
        )


def test_preparation_creates_only_once_and_reconciles_original_receipt() -> None:
    source = Sources()
    assert source.run().status == "APPLIED"
    assert source.effects == ["create_roadmap_pr"]
    assert source.checks == 3
    source.mode = "APPLIED"
    assert source.run().status == "NO_OP"
    assert source.effects == ["create_roadmap_pr"]
    assert source.reconciliations == 1


@pytest.mark.parametrize("operation", ["merge_roadmap_pr", "close_epic", "update_epic"])
def test_no_final_write_path_even_when_requested(operation: str) -> None:
    source = Sources()
    source.request = records.seal(
        {**source.request, "operation": operation}, self_digest="operation_id"
    )
    assert source.run().status == "BLOCKED"
    assert source.effects == []


def test_task_grants_do_not_grant_create_roadmap_pr() -> None:
    source = Sources()
    source.mandate["operations"] = ["merge_task_pr", "close_task"]
    source.approved = delivery.mandate_digest(source.mandate)
    approval = source.mandate["approval"]
    assert isinstance(approval, dict)
    approval["mandate_digest"] = source.approved
    source.request = records.seal(
        {**source.request, "mandate_digest": source.approved},
        self_digest="operation_id",
    )
    assert source.run().machine_code == "MANDATE_AUTHORITY_MISSING"
    assert source.effects == []


def test_expiry_after_intent_preserves_pending_and_prevents_write() -> None:
    source = Sources()

    def saved(entries: Any) -> None:
        source.now = 1001

    source.ledger.after_save = saved
    assert source.run().machine_code == "MANDATE_EXPIRED"
    assert source.effects == []
    assert source.ledger.values[source.request["operation_id"]]["status"] == "INTENT"


def test_unknown_create_is_read_only_reconciled_even_after_expiry() -> None:
    source = Sources()

    def unknown(value: Any) -> Any:
        source.effects.append(value.operation)
        raise delivery.OutcomeUnknown

    source.effect = unknown  # type: ignore[method-assign]
    assert source.run().status == "ESCALATE"
    source.now = 1001
    source.mode = "APPLIED"
    assert source.run().status == "NO_OP"
    assert source.effects == ["create_roadmap_pr"]


def test_proven_not_applied_cannot_reopen_expired_write() -> None:
    source = Sources()
    source.ledger.values[source.request["operation_id"]] = {
        "request": source.request,
        "status": "UNKNOWN",
        "receipt": None,
    }
    source.now = 1001
    assert source.run().machine_code == "MANDATE_EXPIRED"
    assert source.reconciliations == 1
    assert source.effects == []


def test_ambiguous_reconciliation_never_repeats_create() -> None:
    source = Sources()
    source.ledger.values[source.request["operation_id"]] = {
        "request": source.request,
        "status": "UNKNOWN",
        "receipt": None,
    }
    source.mode = "UNKNOWN"
    assert source.run().machine_code == "ESCALATE_UNKNOWN_OUTCOME"
    assert source.effects == []


def test_changed_preparation_source_does_not_match_self_declared_request() -> None:
    source = Sources()
    source.ready = records.seal({**source.ready, "snapshot_digest": "d" * 64})
    assert source.run().machine_code == "FINAL_APPROVAL_INVALID"
    assert source.effects == []
    assert source.ledger.values == {}


def test_retained_other_creation_identity_blocks_duplicate_path() -> None:
    source = Sources()
    old = records.seal(
        {**source.request, "roadmap_sha": "c" * 40}, self_digest="operation_id"
    )
    source.ledger.values[old["operation_id"]] = {
        "request": old,
        "status": "UNKNOWN",
        "receipt": None,
    }
    assert source.run().machine_code == "FINAL_RECEIPT_INVALID"
    assert source.effects == []


def test_candidate_cannot_redirect_original_launch_binding() -> None:
    source = Sources()
    original_launch = source.request["launch_digest"]
    source.request = records.seal(
        {**source.request, "launch_digest": "sha256:" + "f" * 64},
        self_digest="operation_id",
    )

    def binding(value: Any) -> None:
        if value["launch_digest"] != original_launch:
            raise PhaseBlocked("FINAL_APPROVAL_INVALID")

    source.binding = binding  # type: ignore[method-assign]
    assert source.run().status == "BLOCKED"
    assert source.effects == []


def test_drift_during_intent_save_blocks_create() -> None:
    source = Sources()

    def saved(entries: Any) -> None:
        source.ready = records.seal({**source.ready, "snapshot_digest": "d" * 64})

    source.ledger.after_save = saved
    result = source.run()
    assert result.machine_code == "FINAL_APPROVAL_INVALID"
    assert source.effects == []
    assert source.ledger.values[source.request["operation_id"]]["status"] == "INTENT"


def test_new_required_child_during_intent_save_blocks_create() -> None:
    source = Sources()

    def saved(entries: Any) -> None:
        source.ready = records.seal(
            {**source.ready, "status": "BLOCKED", "machine_code": "OPEN_REQUIRED_TASKS"}
        )

    source.ledger.after_save = saved
    result = source.run()
    assert result.machine_code == "OPEN_REQUIRED_TASKS"
    assert source.effects == []
    assert source.ledger.values[source.request["operation_id"]]["status"] == "INTENT"
