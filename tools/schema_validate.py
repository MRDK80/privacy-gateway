"""Project-owned JSON Schema subset validator for agent contracts (#181).

The repository deliberately avoids a runtime dependency on ``jsonschema``.
Only the closed keyword subset used by ``docs/schemas/*.json`` is supported and
every unknown construct fails closed. All checks raise explicit exceptions so
that the validator keeps working under ``python -O``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

MAX_DEPTH: Final[int] = 32
MAX_DOCUMENT_CHARS: Final[int] = 1_000_000

ANNOTATION_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"$schema", "$id", "title", "description"}
)
STRUCTURAL_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"type", "const", "enum", "required", "additionalProperties"}
)
CONSTRAINT_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "pattern",
        "minimum",
        "maximum",
    }
)
COMPOSITION_KEYWORDS: Final[frozenset[str]] = frozenset({"allOf", "if", "then"})
SUPPORTED_KEYWORDS: Final[frozenset[str]] = (
    ANNOTATION_KEYWORDS
    | STRUCTURAL_KEYWORDS
    | CONSTRAINT_KEYWORDS
    | COMPOSITION_KEYWORDS
    | frozenset({"properties", "items"})
)
TYPE_NAMES: Final[frozenset[str]] = frozenset(
    {"object", "array", "string", "integer", "boolean", "null"}
)


class SchemaError(Exception):
    """Base class for fail-closed schema handling failures."""

    machine_code = "SCHEMA_ERROR"


class UnsupportedKeyword(SchemaError):
    """The schema uses a construct this validator refuses to interpret."""

    machine_code = "SCHEMA_UNSUPPORTED"

    def __init__(self, path: str, keyword: str) -> None:
        super().__init__(f"unsupported schema construct at {path}: {keyword}")
        self.path = path
        self.keyword = keyword


class SchemaViolation(SchemaError):
    """The instance does not satisfy the schema."""

    machine_code = "SCHEMA_VIOLATION"

    def __init__(self, path: str, keyword: str) -> None:
        super().__init__(f"instance violates {keyword} at {path}")
        self.path = path
        self.keyword = keyword


class DeriveUnsupported(SchemaError):
    """A generation schema cannot be derived without weakening guarantees."""

    machine_code = "SCHEMA_DERIVE_UNSUPPORTED"

    def __init__(self, path: str, keyword: str) -> None:
        super().__init__(f"cannot derive generation schema at {path}: {keyword}")
        self.path = path
        self.keyword = keyword


def _as_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise UnsupportedKeyword(path, "object-expected")
    return value


def _as_sequence(value: Any, path: str) -> Sequence[Any]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise UnsupportedKeyword(path, "array-expected")
    return value


def _type_names(declared: Any, path: str) -> tuple[str, ...]:
    if isinstance(declared, str):
        return (declared,)
    if isinstance(declared, list) and all(isinstance(item, str) for item in declared):
        return tuple(str(item) for item in declared)
    raise UnsupportedKeyword(path, "type")


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return isinstance(value, str)


def assert_supported(schema: Any, *, path: str = "$", depth: int = 0) -> None:
    """Reject any schema construct outside the supported subset."""
    if depth > MAX_DEPTH:
        raise UnsupportedKeyword(path, "max-depth")
    node = _as_mapping(schema, path)
    for keyword in node:
        if keyword not in SUPPORTED_KEYWORDS:
            raise UnsupportedKeyword(path, keyword)
    declared = node.get("type")
    if declared is not None:
        for name in _type_names(declared, path):
            if name not in TYPE_NAMES:
                raise UnsupportedKeyword(path, f"type:{name}")
    properties = node.get("properties")
    if properties is not None:
        for key, child in _as_mapping(properties, path).items():
            assert_supported(child, path=f"{path}.{key}", depth=depth + 1)
    items = node.get("items")
    if items is not None:
        assert_supported(items, path=f"{path}[]", depth=depth + 1)
    for index, condition in enumerate(_as_sequence(node.get("allOf", []), path)):
        branch_path = f"{path}.allOf[{index}]"
        branch = _as_mapping(condition, branch_path)
        if set(branch) != {"if", "then"}:
            raise UnsupportedKeyword(branch_path, "allOf-shape")
        assert_supported(branch["if"], path=f"{branch_path}.if", depth=depth + 1)
        assert_supported(branch["then"], path=f"{branch_path}.then", depth=depth + 1)


def _validate_object(
    instance: dict[str, Any],
    node: Mapping[str, Any],
    path: str,
    depth: int,
) -> None:
    properties = _as_mapping(node.get("properties", {}), path)
    for name in _as_sequence(node.get("required", []), path):
        if str(name) not in instance:
            raise SchemaViolation(path, "required")
    if node.get("additionalProperties") is False:
        for key in instance:
            if key not in properties:
                raise SchemaViolation(f"{path}.{key}", "additionalProperties")
    for key, child in instance.items():
        if key in properties:
            validate(child, properties[key], path=f"{path}.{key}", depth=depth + 1)


def _validate_array(
    instance: list[Any],
    node: Mapping[str, Any],
    path: str,
    depth: int,
) -> None:
    minimum_items = node.get("minItems")
    if minimum_items is not None and len(instance) < int(minimum_items):
        raise SchemaViolation(path, "minItems")
    maximum_items = node.get("maxItems")
    if maximum_items is not None and len(instance) > int(maximum_items):
        raise SchemaViolation(path, "maxItems")
    if node.get("uniqueItems") is True:
        encoded = [json.dumps(item, sort_keys=True) for item in instance]
        if len(encoded) != len(set(encoded)):
            raise SchemaViolation(path, "uniqueItems")
    items = node.get("items")
    if items is not None:
        for index, item in enumerate(instance):
            validate(item, items, path=f"{path}[{index}]", depth=depth + 1)


def _validate_scalar(instance: Any, node: Mapping[str, Any], path: str) -> None:
    if isinstance(instance, str):
        if len(instance) < int(node.get("minLength", 0)):
            raise SchemaViolation(path, "minLength")
        pattern = node.get("pattern")
        if pattern is not None and re.search(str(pattern), instance) is None:
            raise SchemaViolation(path, "pattern")
    if isinstance(instance, int) and not isinstance(instance, bool):
        minimum = node.get("minimum")
        if minimum is not None and instance < int(minimum):
            raise SchemaViolation(path, "minimum")
        maximum = node.get("maximum")
        if maximum is not None and instance > int(maximum):
            raise SchemaViolation(path, "maximum")


def _conforms(instance: Any, schema: Any, depth: int) -> bool:
    try:
        validate(instance, schema, path="$", depth=depth)
    except SchemaViolation:
        return False
    return True


def validate(instance: Any, schema: Any, *, path: str = "$", depth: int = 0) -> None:
    """Validate an instance, raising :class:`SchemaViolation` on the first fault."""
    if depth > MAX_DEPTH:
        raise SchemaViolation(path, "max-depth")
    node = _as_mapping(schema, path)
    if "const" in node and instance != node["const"]:
        raise SchemaViolation(path, "const")
    if "enum" in node:
        options = _as_sequence(node["enum"], path)
        if not any(instance == option for option in options):
            raise SchemaViolation(path, "enum")
    declared = node.get("type")
    if declared is not None:
        names = _type_names(declared, path)
        if not any(_matches_type(instance, name) for name in names):
            raise SchemaViolation(path, "type")
    if isinstance(instance, dict):
        _validate_object(instance, node, path, depth)
    elif isinstance(instance, list):
        _validate_array(instance, node, path, depth)
    else:
        _validate_scalar(instance, node, path)
    for index, condition in enumerate(_as_sequence(node.get("allOf", []), path)):
        branch = _as_mapping(condition, f"{path}.allOf[{index}]")
        if _conforms(instance, branch["if"], depth + 1):
            validate(instance, branch["then"], path=path, depth=depth + 1)


def derive_generation_schema(
    schema: Any, *, path: str = "$", depth: int = 0
) -> dict[str, Any]:
    """Build a strictly weaker schema usable with ``codex exec --output-schema``.

    Composition and constraint keywords are removed because strict structured
    output modes do not enforce them. Any keyword without a derivation rule
    raises :class:`DeriveUnsupported` instead of silently degrading.
    """
    if depth > MAX_DEPTH:
        raise DeriveUnsupported(path, "max-depth")
    node = _as_mapping(schema, path)
    derived: dict[str, Any] = {}
    for keyword, value in node.items():
        if keyword in ANNOTATION_KEYWORDS:
            continue
        if keyword in CONSTRAINT_KEYWORDS or keyword in COMPOSITION_KEYWORDS:
            continue
        if keyword in STRUCTURAL_KEYWORDS:
            derived[keyword] = value
            continue
        if keyword == "properties":
            derived["properties"] = {
                key: derive_generation_schema(
                    child, path=f"{path}.{key}", depth=depth + 1
                )
                for key, child in _as_mapping(value, path).items()
            }
            continue
        if keyword == "items":
            derived["items"] = derive_generation_schema(
                value, path=f"{path}[]", depth=depth + 1
            )
            continue
        raise DeriveUnsupported(path, keyword)
    if derived.get("type") == "object" and "additionalProperties" not in derived:
        derived["additionalProperties"] = False
    return derived


def load_schema(path: Path) -> Mapping[str, Any]:
    """Load and pre-validate a canonical schema document."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise UnsupportedKeyword(str(path), "unreadable") from error
    if len(text) > MAX_DOCUMENT_CHARS:
        raise UnsupportedKeyword(str(path), "document-too-large")
    try:
        schema = json.loads(text)
    except ValueError as error:
        raise UnsupportedKeyword(str(path), "malformed-json") from error
    assert_supported(schema)
    return _as_mapping(schema, "$")
