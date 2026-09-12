"""Deterministic documentation contracts for agent roles and schemas (#164)."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "docs" / "schemas"
EXAMPLE_DIR = REPO_ROOT / "examples" / "agent-contracts"

EXAMPLES = {
    "task-contract.schema.json": ("task.json",),
    "executor-report.schema.json": ("executor-pass.json",),
    "controller-verdict.schema.json": (
        "controller-pass.json",
        "controller-fail.json",
    ),
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, {"object": dict, "array": list, "string": str}[expected])


def _validate(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the deliberately small JSON Schema subset used by these files."""
    if "const" in schema:
        assert value == schema["const"], path
    if "enum" in schema:
        assert value in schema["enum"], path

    declared = schema.get("type")
    if declared is not None:
        expected = [declared] if isinstance(declared, str) else declared
        assert any(_matches_type(value, item) for item in expected), path

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        assert set(required) <= set(value), path
        if schema.get("additionalProperties") is False:
            assert set(value) <= set(properties), path
        for key, child in value.items():
            if key in properties:
                _validate(child, properties[key], f"{path}.{key}")

    if isinstance(value, list):
        assert len(value) >= schema.get("minItems", 0), path
        if "maxItems" in schema:
            assert len(value) <= schema["maxItems"], path
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            assert len(encoded) == len(set(encoded)), path
        if "items" in schema:
            for index, item in enumerate(value):
                _validate(item, schema["items"], f"{path}[{index}]")

    if isinstance(value, str):
        assert len(value) >= schema.get("minLength", 0), path
        if "pattern" in schema:
            assert re.search(schema["pattern"], value), path
    if isinstance(value, int) and not isinstance(value, bool):
        assert value >= schema.get("minimum", value), path
        assert value <= schema.get("maximum", value), path

    for condition in schema.get("allOf", []):
        try:
            _validate(value, condition["if"], path)
        except AssertionError:
            continue
        _validate(value, condition["then"], path)


@pytest.mark.parametrize(
    ("schema_name", "example_name"),
    [
        (schema_name, example_name)
        for schema_name, example_names in EXAMPLES.items()
        for example_name in example_names
    ],
)
def test_agent_examples_validate_against_schema(
    schema_name: str, example_name: str
) -> None:
    schema = _load(SCHEMA_DIR / schema_name)
    example = _load(EXAMPLE_DIR / example_name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    _validate(example, schema)


def test_controller_schema_has_exact_verdicts_and_finding_contract() -> None:
    schema = _load(SCHEMA_DIR / "controller-verdict.schema.json")
    properties = schema["properties"]
    assert properties["verdict"]["enum"] == [
        "PASS",
        "PASS_WITH_NOTES",
        "FAIL_RETRY",
        "FAIL_ESCALATE",
    ]
    finding = properties["blocking_findings"]["items"]
    assert set(finding["required"]) == {
        "severity",
        "requirement",
        "evidence",
        "required_fix",
    }
    assert finding["properties"]["location"]["properties"]["line"]["minimum"] == 1


def test_pass_and_fail_examples_exercise_blocking_semantics() -> None:
    passed = _load(EXAMPLE_DIR / "controller-pass.json")
    failed = _load(EXAMPLE_DIR / "controller-fail.json")
    assert passed["verdict"] == "PASS"
    assert passed["blocking_findings"] == []
    assert failed["verdict"] == "FAIL_RETRY"
    assert failed["blocking_findings"]

    schema = _load(SCHEMA_DIR / "controller-verdict.schema.json")
    invalid_pass = deepcopy(passed)
    invalid_pass["blocking_findings"] = failed["blocking_findings"]
    with pytest.raises(AssertionError):
        _validate(invalid_pass, schema)

    invalid_fail = deepcopy(failed)
    invalid_fail["blocking_findings"] = []
    with pytest.raises(AssertionError):
        _validate(invalid_fail, schema)


def test_task_contract_is_deny_by_default_and_budgeted() -> None:
    schema = _load(SCHEMA_DIR / "task-contract.schema.json")
    permissions = schema["properties"]["permissions"]
    assert permissions["additionalProperties"] is False
    assert permissions["properties"]["external_write"] == {"const": False}
    assert permissions["properties"]["merge"] == {"const": False}
    budget = schema["properties"]["budget"]["properties"]
    assert budget["max_repair_iterations"]["maximum"] == 2
    assert {"max_minutes", "max_report_chars"} <= set(budget)


def test_controller_review_basis_rejects_head_policy() -> None:
    schema = _load(SCHEMA_DIR / "controller-verdict.schema.json")
    verdict = _load(EXAMPLE_DIR / "controller-pass.json")
    verdict["review_basis"]["head_policy_applied"] = True
    with pytest.raises(AssertionError):
        _validate(verdict, schema)


def test_role_document_preserves_trust_and_visibility_boundaries() -> None:
    text = (REPO_ROOT / "docs" / "agent-contracts.md").read_text(encoding="utf-8")
    required_markers = (
        "недоверенными данными",
        "base_sha",
        "read-only policy bundle",
        "FAIL_ESCALATE",
        "Controller независим",
        "по умолчанию read-only",
        "не пишет код",
        "Chain-of-thought",
        "deny-by-default",
        "raw retrospectives",
        "usage metrics",
        "AGENTS.md` автоматически не переписывается",
    )
    for marker in required_markers:
        assert marker in text, marker
