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
    assert_supported,
    derive_generation_schema,
    load_schema,
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
