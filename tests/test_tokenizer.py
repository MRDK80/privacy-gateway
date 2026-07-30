"""Тесты слоя токенизации (Э4)."""

from __future__ import annotations

from privacy_gateway.models import DetectedEntity, DetectionConfidence, EntityType
from privacy_gateway.tokenizer import tokenize


def _make_entity(
    entity_type: EntityType,
    start: int,
    end: int,
    confidence: DetectionConfidence = DetectionConfidence.HIGH,
    fingerprint: str = "abcdef012345",
    secret_kind: str | None = None,
) -> DetectedEntity:
    return DetectedEntity(
        entity_type=entity_type,
        start=start,
        end=end,
        confidence=confidence,
        source="regex",
        fingerprint=fingerprint,
        secret_kind=secret_kind,
    )


def test_tokens_are_stable() -> None:
    text = "Contact user@example.com please"  # pragma: allowlist secret
    entity = _make_entity(EntityType.EMAIL, 8, 24, fingerprint="fp1")
    result1, records1 = tokenize(text, [entity])
    result2, records2 = tokenize(text, [entity])
    assert result1 == result2
    assert records1[0].token == records2[0].token


def test_same_entity_same_token() -> None:
    text = "a@b.com and a@b.com and a@b.com"  # pragma: allowlist secret
    e1 = _make_entity(EntityType.EMAIL, 0, 7, fingerprint="fp1")
    e2 = _make_entity(EntityType.EMAIL, 12, 19, fingerprint="fp1")
    e3 = _make_entity(EntityType.EMAIL, 24, 31, fingerprint="fp1")
    _, records = tokenize(text, [e1, e2, e3], ["a@b.com", "a@b.com", "a@b.com"])
    tokens = [r.token for r in records]
    assert len(set(tokens)) == 1


def test_different_entities_different_tokens() -> None:
    text = "user@example.com and admin@example.com"  # pragma: allowlist secret
    e1 = _make_entity(EntityType.EMAIL, 0, 16, fingerprint="fp1")
    e2 = _make_entity(EntityType.EMAIL, 21, 38, fingerprint="fp2")
    _, records = tokenize(text, [e1, e2])
    assert records[0].token != records[1].token


def test_token_does_not_leak_value() -> None:
    text = "host is 192.0.2.10 in network"  # pragma: allowlist secret
    entity = _make_entity(EntityType.HOST, 8, 18, fingerprint="fp_host")
    result, records = tokenize(text, [entity])
    assert "192.0.2.10" not in records[0].token
    assert "192" not in records[0].token


def test_overlapping_entities() -> None:
    """Перекрывающийся диапазон должен быть отброшен без исключения."""
    text = "Call +7 900 000-00-00 now"  # pragma: allowlist secret
    e1 = _make_entity(EntityType.PHONE, 5, 21, fingerprint="fp_phone")
    e2 = _make_entity(EntityType.PHONE, 10, 21, fingerprint="fp_phone2")  # overlap
    result, records = tokenize(text, [e1, e2])
    assert len(records) == 1
    assert "[PHONE_1]" in result


def test_empty_entity_list() -> None:
    text = "No entities here"
    result, records = tokenize(text, [])
    assert result == text
    assert records == []


def test_multiple_entity_types() -> None:
    text = "Email user@example.com phone +7 900 000-00-00"  # pragma: allowlist secret
    e_email = _make_entity(EntityType.EMAIL, 6, 22, fingerprint="fpe")
    e_phone = _make_entity(EntityType.PHONE, 29, 46, fingerprint="fpp")
    result, records = tokenize(text, [e_email, e_phone])
    assert "[EMAIL_1]" in result
    assert "[PHONE_1]" in result
    assert len(records) == 2
