"""Visibility, append-only and metrics tests for private agent memory (#166)."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "agent_memory.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_memory", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


memory = _load_module()
SHA_A = "a" * 40
SHA_B = "b" * 40


def _record(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "task_issue": 166,
        "epic_issue": 162,
        "task_class": "agent-workflow",
        "base_sha": SHA_A,
        "head_sha": SHA_B,
        "duration_seconds": 120,
        "executor_calls": 2,
        "controller_calls": 2,
        "iterations": 2,
        "repair_loops": 1,
        "verdicts": ("FAIL_RETRY", "PASS"),
        "failures": (),
        "false_positives": 0,
        "manual_interventions": 0,
        "findings": 1,
    }
    values.update(overrides)
    return memory.new_record(**values)


def test_store_appends_valid_records_and_preserves_previous_lines(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    store = memory.RetrospectiveStore(tmp_path / "private-memory", repository)

    store.append(_record())
    first = store.path.read_bytes()
    store.append(_record(duration_seconds=60, findings=0))

    lines = store.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert store.path.read_bytes().startswith(first)
    assert [item["duration_seconds"] for item in store.records()] == [120, 60]
    if os.name != "nt":
        assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_retrying_same_record_id_is_idempotent(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    store = memory.RetrospectiveStore(tmp_path / "private-memory", repository)
    record = _record()

    store.append(record)
    store.append(record)

    assert len(list(store.records())) == 1


def test_storage_inside_public_worktree_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(memory.MemoryError) as error:
        memory.RetrospectiveStore(repository / "agent-memory", repository)

    assert error.value.machine_code == "UNSAFE_STORAGE"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"transcript": "private model output"}),
        lambda value: value.update({"iterations": -1}),
        lambda value: value.update({"task_class": "free form text"}),
        lambda value: value.update({"usage": {"source": "unavailable", "units": 1}}),
    ],
)
def test_schema_fails_closed_for_disallowed_or_invalid_fields(
    mutation: Any,
) -> None:
    value = memory.asdict(_record())
    value["verdicts"] = list(value["verdicts"])
    value["failures"] = list(value["failures"])
    mutation(value)

    with pytest.raises(memory.MemoryError):
        memory.validate_record(value)


def test_usage_is_explicitly_unavailable_without_supported_source() -> None:
    value = memory.asdict(_record())

    assert value["usage"] == {"source": "unavailable", "units": None}


def test_aggregate_returns_trends_without_raw_records() -> None:
    records = []
    for item in (
        _record(),
        _record(
            duration_seconds=60,
            controller_calls=1,
            iterations=1,
            repair_loops=0,
            findings=3,
            failures=("GATE_FAILED",),
            verdicts=("PASS_WITH_NOTES",),
            usage=memory.Usage("provider", 40),
        ),
    ):
        value = memory.asdict(item)
        value["verdicts"] = list(value["verdicts"])
        value["failures"] = list(value["failures"])
        records.append(value)

    report = memory.aggregate(records)

    assert report == [
        {
            "task_class": "agent-workflow",
            "tasks": 2,
            "average_duration_seconds": 90.0,
            "average_iterations": 1.5,
            "average_findings": 2.0,
            "failures": 1,
            "usage_observations": 1,
            "average_usage_units": 40.0,
        }
    ]
    encoded = json.dumps(report)
    assert "record_id" not in encoded
    assert "head_sha" not in encoded


def test_corrupt_historical_line_is_not_silently_skipped(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    store = memory.RetrospectiveStore(tmp_path / "private-memory", repository)
    store.append(_record())
    with store.path.open("a", encoding="utf-8") as stream:
        stream.write("{}\n")

    with pytest.raises(memory.MemoryError):
        list(store.records())


def test_promotion_gate_requires_evidence_scans_provenance_and_review() -> None:
    candidate = {
        "schema_version": "1.0",
        "candidate_id": "c" * 32,
        "target": "validator",
        "evidence_count": 2,
        "classified": True,
        "sanitized": True,
        "secret_privacy_scan_passed": True,
        "safe_for_full_disclosure": True,
        "independent_review_passed": True,
        "trust_source": "base_sha",
        "head_instructions_applied": False,
        "separate_change": True,
        "human_approval": False,
    }

    memory.validate_promotion(candidate)
    for field in (
        "sanitized",
        "secret_privacy_scan_passed",
        "safe_for_full_disclosure",
        "independent_review_passed",
    ):
        denied = {**candidate, field: False}
        with pytest.raises(memory.MemoryError) as error:
            memory.validate_promotion(denied)
        assert error.value.machine_code == "PUBLICATION_DENIED"


def test_instruction_promotion_requires_human_and_rejects_head_policy() -> None:
    candidate = {
        "schema_version": "1.0",
        "candidate_id": "d" * 32,
        "target": "policy",
        "evidence_count": 3,
        "classified": True,
        "sanitized": True,
        "secret_privacy_scan_passed": True,
        "safe_for_full_disclosure": True,
        "independent_review_passed": True,
        "trust_source": "base_sha",
        "head_instructions_applied": False,
        "separate_change": True,
        "human_approval": True,
    }

    memory.validate_promotion(candidate)
    for denied in (
        {**candidate, "human_approval": False},
        {**candidate, "head_instructions_applied": True},
    ):
        with pytest.raises(memory.MemoryError):
            memory.validate_promotion(denied)


def test_schema_and_policy_are_safe_public_contracts() -> None:
    schema = json.loads(
        (REPO_ROOT / "docs/schemas/agent-retrospective.schema.json").read_text(
            encoding="utf-8"
        )
    )
    forbidden = {"transcript", "chain_of_thought", "raw_log", "notes", "payload"}
    assert forbidden.isdisjoint(schema["properties"])
    assert schema["properties"]["iterations"]["minimum"] == 0
    promotion_schema = json.loads(
        (REPO_ROOT / "docs/schemas/agent-promotion.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert promotion_schema["properties"]["safe_for_full_disclosure"] == {"const": True}
    assert promotion_schema["properties"]["head_instructions_applied"] == {
        "const": False
    }

    policy = (REPO_ROOT / "docs/agent-memory.md").read_text(encoding="utf-8")
    for marker in (
        "deny-by-default",
        "append",
        "Secret/privacy scan",
        "Независимый reviewer",
        "base SHA/read-only bundle",
        "`AGENTS.md` не обновляется автоматически",
        "test или validator",
    ):
        assert marker.casefold() in policy.casefold(), marker
