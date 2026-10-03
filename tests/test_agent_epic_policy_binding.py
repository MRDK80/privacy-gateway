"""Synthetic fail-closed policy/base separation tests."""

from copy import deepcopy

import pytest
from tools import agent_epic_policy_binding as binding

POLICY = "a" * 40
BASE = "b" * 40


def value() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "repository": "example/project",
        "epic": 248,
        "roadmap_ref": "roadmap/248-example",
        "policy_sha": POLICY,
        "base_sha": BASE,
    }


def validate(candidate: dict[str, object], *, live: str = BASE) -> None:
    binding.validate_binding(
        candidate,
        repository="example/project",
        epic=248,
        roadmap_ref="roadmap/248-example",
        policy_sha=POLICY,
        live_base_sha=live,
        is_ancestor=lambda policy, base: policy == POLICY and base == BASE,
    )


def test_moving_base_preserves_pinned_policy() -> None:
    candidate = value()
    original = deepcopy(candidate)
    validate(candidate)
    assert candidate == original


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema_version", "2.0"),
        ("repository", "other/project"),
        ("epic", True),
        ("roadmap_ref", "main"),
        ("policy_sha", BASE),
        ("base_sha", "b" * 39),
        ("extra", "untrusted"),
    ],
)
def test_rejects_scope_or_closed_schema_changes(
    field: str, replacement: object
) -> None:
    candidate = value()
    candidate[field] = replacement
    with pytest.raises(binding.BindingError):
        validate(candidate)


def test_stale_live_base_is_rejected() -> None:
    with pytest.raises(binding.BindingError, match="BASE_MISMATCH"):
        validate(value(), live=POLICY)


@pytest.mark.parametrize("result", [False, None, 1, "true"])
def test_only_explicit_ancestry_proof_is_accepted(result: object) -> None:
    with pytest.raises(binding.BindingError, match="ANCESTRY_UNCONFIRMED"):
        binding.validate_binding(
            value(),
            repository="example/project",
            epic=248,
            roadmap_ref="roadmap/248-example",
            policy_sha=POLICY,
            live_base_sha=BASE,
            is_ancestor=lambda _policy, _base: result,
        )


def test_failed_fact_query_is_redacted() -> None:
    def unavailable(_policy: str, _base: str) -> bool:
        raise RuntimeError("synthetic private diagnostic")

    with pytest.raises(binding.BindingError) as error:
        binding.validate_binding(
            value(),
            repository="example/project",
            epic=248,
            roadmap_ref="roadmap/248-example",
            policy_sha=POLICY,
            live_base_sha=BASE,
            is_ancestor=unavailable,
        )
    assert str(error.value) == "ANCESTRY_UNCONFIRMED"
    assert error.value.__cause__ is None


def test_migration_is_pure_and_does_not_approve_authority() -> None:
    legacy = value()
    legacy.pop("schema_version")
    legacy["base_sha"] = POLICY
    original = deepcopy(legacy)
    migrated = binding.migrate_legacy_binding(legacy)
    assert legacy == original
    assert migrated == {"schema_version": "1.0", **original}
    assert "approval" not in migrated


def test_migration_cannot_infer_moving_base_authority() -> None:
    legacy = value()
    legacy.pop("schema_version")
    with pytest.raises(binding.BindingError, match="LEGACY_POLICY_BASE_MISMATCH"):
        binding.migrate_legacy_binding(legacy)
