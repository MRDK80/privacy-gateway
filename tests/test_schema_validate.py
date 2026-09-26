"""Contract tests for the project-owned schema validator (#181)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.schema_validate import (  # noqa: E402
    DeriveUnsupported,
    SchemaViolation,
    UnsupportedKeyword,
    assert_generation_ready,
    assert_supported,
    derive_generation_schema,
    load_schema,
    prune_generation_nulls,
    validate,
)

SCHEMA_DIR = REPO_ROOT / "docs" / "schemas"
EXAMPLE_DIR = REPO_ROOT / "examples" / "agent-contracts"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_canonical_schemas_are_inside_supported_subset() -> None:
    for name in sorted(path.name for path in SCHEMA_DIR.glob("*.schema.json")):
        assert_supported(_load(SCHEMA_DIR / name))


def test_canonical_examples_validate() -> None:
    pairs = (
        ("task-contract.schema.json", "task.json"),
        ("executor-report.schema.json", "executor-pass.json"),
        ("controller-verdict.schema.json", "controller-pass.json"),
        ("controller-verdict.schema.json", "controller-fail.json"),
    )
    for schema_name, example_name in pairs:
        schema = load_schema(SCHEMA_DIR / schema_name)
        validate(_load(EXAMPLE_DIR / example_name), schema)


def test_pass_verdict_rejects_blocking_findings() -> None:
    schema = load_schema(SCHEMA_DIR / "controller-verdict.schema.json")
    verdict = _load(EXAMPLE_DIR / "controller-pass.json")
    verdict["blocking_findings"] = _load(EXAMPLE_DIR / "controller-fail.json")[
        "blocking_findings"
    ]
    with pytest.raises(SchemaViolation) as failure:
        validate(verdict, schema)
    assert failure.value.keyword == "maxItems"


def test_unknown_keyword_fails_closed() -> None:
    with pytest.raises(UnsupportedKeyword) as failure:
        assert_supported({"type": "object", "oneOf": []})
    assert failure.value.keyword == "oneOf"


def test_unknown_type_name_fails_closed() -> None:
    with pytest.raises(UnsupportedKeyword):
        assert_supported({"type": "number"})


def test_recursion_is_bounded() -> None:
    schema: dict[str, Any] = {"type": "object"}
    instance: dict[str, Any] = {}
    cursor_schema = schema
    cursor_instance = instance
    for _ in range(64):
        child_schema: dict[str, Any] = {"type": "object"}
        cursor_schema["properties"] = {"child": child_schema}
        cursor_instance["child"] = {}
        cursor_schema = child_schema
        cursor_instance = cursor_instance["child"]
    with pytest.raises(SchemaViolation) as failure:
        validate(instance, schema)
    assert failure.value.keyword == "max-depth"


def test_derived_schema_drops_composition_and_constraints() -> None:
    schema = load_schema(SCHEMA_DIR / "controller-verdict.schema.json")
    derived = derive_generation_schema(schema)
    encoded = json.dumps(derived)
    for keyword in ("allOf", '"if"', '"then"', "minItems", "maxItems", "pattern"):
        assert keyword not in encoded
    assert derived["additionalProperties"] is False
    assert derived["properties"]["verdict"]["enum"]


def test_derived_schema_is_weaker_than_canonical() -> None:
    for schema_name, example_name in (
        ("executor-report.schema.json", "executor-pass.json"),
        ("controller-verdict.schema.json", "controller-pass.json"),
        ("controller-verdict.schema.json", "controller-fail.json"),
    ):
        schema = load_schema(SCHEMA_DIR / schema_name)
        derived = derive_generation_schema(schema)
        validate(_load(EXAMPLE_DIR / example_name), derived)


def test_derive_refuses_unknown_keyword() -> None:
    with pytest.raises(DeriveUnsupported):
        derive_generation_schema({"type": "object", "patternProperties": {}})


def test_validator_still_enforces_under_optimized_interpreter(tmp_path: Path) -> None:
    script = tmp_path / "optimized_check.py"
    lines = (
        "import sys",
        "sys.path.insert(0, " + repr(str(REPO_ROOT)) + ")",
        "from tools.schema_validate import SchemaViolation, validate",
        "try:",
        "    validate(1, {'type': 'string'})",
        "except SchemaViolation:",
        "    raise SystemExit(0)",
        "raise SystemExit(1)",
    )
    script.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-O", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _generation_defects(node: Any, path: str = "$") -> list[str]:
    """Collect strict structured output defects of a derived schema (#192)."""
    defects: list[str] = []
    if isinstance(node, dict):
        if "properties" in node:
            names = sorted(node["properties"])
            required = sorted(str(item) for item in node.get("required", []))
            if node.get("additionalProperties") is not False:
                defects.append(path + ":open-object")
            if names != required:
                defects.append(path + ":partial-required")
        for key, child in node.items():
            defects += _generation_defects(child, f"{path}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            defects += _generation_defects(child, f"{path}[{index}]")
    return defects


def test_derived_schemas_close_required_and_additional_properties() -> None:
    for name in sorted(path.name for path in SCHEMA_DIR.glob("*.schema.json")):
        derived = derive_generation_schema(load_schema(SCHEMA_DIR / name))
        assert _generation_defects(derived) == [], name


def test_derived_optional_property_is_nullable_and_required() -> None:
    schema = load_schema(SCHEMA_DIR / "controller-verdict.schema.json")
    finding = derive_generation_schema(schema)["properties"]["blocking_findings"][
        "items"
    ]
    assert "location" in finding["required"]
    assert finding["properties"]["location"]["type"] == ["object", "null"]
    assert finding["properties"]["location"]["properties"]["line"]["type"] == [
        "integer",
        "null",
    ]


def test_generation_ready_rejects_partial_required() -> None:
    with pytest.raises(DeriveUnsupported) as failure:
        assert_generation_ready(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["kept"],
                "properties": {
                    "kept": {"type": "string"},
                    "dropped": {"type": "string"},
                },
            }
        )
    assert failure.value.keyword == "partial-required"


def test_generation_ready_rejects_open_object() -> None:
    with pytest.raises(DeriveUnsupported) as failure:
        assert_generation_ready(
            {
                "type": "object",
                "required": ["kept"],
                "properties": {"kept": {"type": "string"}},
            }
        )
    assert failure.value.keyword == "open-object"


def test_derive_keeps_an_optional_const_without_widening() -> None:
    derived = derive_generation_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"role": {"const": "controller"}},
        }
    )
    assert derived["required"] == ["role"]
    assert derived["properties"]["role"] == {"const": "controller", "type": "string"}


def test_generation_ready_reports_the_child_defect_first() -> None:
    with pytest.raises(DeriveUnsupported) as failure:
        assert_generation_ready({"type": "object", "properties": {"role": {}}})
    assert failure.value.path == "$.role"
    assert failure.value.keyword == "missing-type"


def test_pruning_restores_canonical_shape_for_nullable_optionals() -> None:
    schema = load_schema(SCHEMA_DIR / "controller-verdict.schema.json")
    response = _load(EXAMPLE_DIR / "controller-fail.json")
    response["blocking_findings"][0]["location"] = None
    validate(response, derive_generation_schema(schema))
    with pytest.raises(SchemaViolation):
        validate(response, schema)
    pruned = prune_generation_nulls(response, schema)
    validate(pruned, schema)
    assert "location" not in pruned["blocking_findings"][0]


def test_pruning_keeps_null_required_by_the_canonical_schema() -> None:
    schema = load_schema(SCHEMA_DIR / "controller-verdict.schema.json")
    response = _load(EXAMPLE_DIR / "controller-pass.json")
    response["escalation_reason"] = None
    pruned = prune_generation_nulls(response, schema)
    assert pruned["escalation_reason"] is None
    validate(pruned, schema)
