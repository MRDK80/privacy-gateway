"""Regression tests for #186: derived generation schemas always carry `type`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from tools.schema_validate import (
    DeriveUnsupported,
    assert_generation_ready,
    derive_generation_schema,
)

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "docs" / "schemas"


def _load(name: str) -> dict[str, Any]:
    text = (SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
    return cast(dict[str, Any], json.loads(text))


def _subschemas(node: Any, path: str = "$") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = [(path, node)]
    if isinstance(node, dict):
        for key, child in (node.get("properties") or {}).items():
            found += _subschemas(child, f"{path}.{key}")
        if node.get("items") is not None:
            found += _subschemas(node["items"], f"{path}[]")
    return found


def test_const_string_yields_string_type() -> None:
    derived = derive_generation_schema(
        {"type": "object", "properties": {"schema_version": {"const": "1.0"}}}
    )
    assert derived["properties"]["schema_version"]["type"] == "string"
    assert derived["properties"]["schema_version"]["const"] == "1.0"


def test_const_false_yields_boolean_type() -> None:
    derived = derive_generation_schema(
        {"type": "object", "properties": {"merge": {"const": False}}}
    )
    assert derived["properties"]["merge"]["type"] == "boolean"


def test_homogeneous_string_enum_yields_string_type() -> None:
    derived = derive_generation_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["status"],
            "properties": {"status": {"enum": ["PASS", "FAIL"]}},
        }
    )
    assert derived["properties"]["status"]["type"] == "string"


def test_declared_type_is_preserved_and_constraints_dropped() -> None:
    derived = derive_generation_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["count"],
            "properties": {"count": {"type": "integer", "minimum": 0}},
        }
    )
    assert derived["properties"]["count"]["type"] == "integer"
    assert "minimum" not in derived["properties"]["count"]


def test_heterogeneous_enum_is_refused() -> None:
    with pytest.raises(DeriveUnsupported) as error:
        derive_generation_schema(
            {"type": "object", "properties": {"mixed": {"enum": ["a", 1]}}}
        )
    assert error.value.machine_code == "SCHEMA_DERIVE_UNSUPPORTED"


def test_empty_enum_is_refused() -> None:
    with pytest.raises(DeriveUnsupported):
        derive_generation_schema(
            {"type": "object", "properties": {"empty": {"enum": []}}}
        )


def test_composition_removed_without_losing_type() -> None:
    canonical = {
        "type": "object",
        "properties": {
            "role": {"const": "executor"},
            "status": {"enum": ["PASS", "FAIL_ESCALATE"]},
            "stop_reason": {"enum": ["SCOPE", "GATE"]},
        },
        "required": ["role", "status"],
        "additionalProperties": False,
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "FAIL_ESCALATE"}}},
                "then": {"required": ["stop_reason"]},
            }
        ],
    }
    derived = derive_generation_schema(canonical)
    assert "allOf" not in derived
    assert derived["properties"]["role"]["type"] == "string"
    assert derived["properties"]["status"]["type"] == "string"
    assert derived["properties"]["stop_reason"]["type"] == ["string", "null"]
    assert derived["properties"]["stop_reason"]["enum"] == [None, "SCOPE", "GATE"]
    assert derived["required"] == ["role", "status", "stop_reason"]


@pytest.mark.parametrize(
    "name", ["executor-report", "controller-verdict", "task-contract"]
)
def test_canonical_schemas_derive_with_type_everywhere(name: str) -> None:
    derived = derive_generation_schema(_load(name))
    missing = [path for path, node in _subschemas(derived) if "type" not in node]
    assert missing == []


def test_self_check_rejects_missing_type() -> None:
    with pytest.raises(DeriveUnsupported) as error:
        assert_generation_ready(
            {"type": "object", "properties": {"schema_version": {"const": "1.0"}}}
        )
    assert error.value.machine_code == "SCHEMA_DERIVE_UNSUPPORTED"
    assert error.value.path == "$.schema_version"


def test_self_check_accepts_derived_executor_report() -> None:
    assert_generation_ready(derive_generation_schema(_load("executor-report")))
