"""Round-trip и валидационные тесты моделей — подготовка к Этапу Э4."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from privacy_gateway.detector import DetectorConfig, detect_entities, load_config
from privacy_gateway.models import (
    DetectedEntity,
    DetectionConfidence,
    EntityType,
    ManifestEntry,
    ProcessingStatus,
    TokenRecord,
)

_CONFIG_PATH = Path("config.example") / "entities.yaml"


@pytest.fixture
def cfg() -> DetectorConfig:
    return load_config(_CONFIG_PATH)


# ---------------------------------------------------------------------------
# ProcessingStatus
# ---------------------------------------------------------------------------

def test_processing_status_values() -> None:
    """ProcessingStatus содержит ровно три ожидаемых значения."""
    assert set(ProcessingStatus) == {
        ProcessingStatus.OK,
        ProcessingStatus.BLOCKED,
        ProcessingStatus.PENDING,
    }


def test_processing_status_str_enum() -> None:
    """ProcessingStatus — StrEnum: значение == строка."""
    assert ProcessingStatus.OK == "OK"
    assert ProcessingStatus.BLOCKED == "BLOCKED"


# ---------------------------------------------------------------------------
# DetectedEntity round-trip
# ---------------------------------------------------------------------------

def test_detected_entity_round_trip_basic() -> None:
    """DetectedEntity → to_dict → from_dict → равен исходному."""
    original = DetectedEntity(
        entity_type=EntityType.EMAIL,
        start=10,
        end=35,
        confidence=DetectionConfidence.HIGH,
        source="regex",
        fingerprint="abc123def456",
    )
    restored = DetectedEntity.from_dict(original.to_dict())
    assert restored == original


def test_detected_entity_round_trip_with_secret_kind() -> None:
    """DetectedEntity с secret_kind сохраняется и восстанавливается корректно."""
    original = DetectedEntity(
        entity_type=EntityType.HOST,
        start=0,
        end=20,
        confidence=DetectionConfidence.HIGH,
        source="regex",
        fingerprint="000111222333",
        secret_kind="API_TOKEN",
    )
    restored = DetectedEntity.from_dict(original.to_dict())
    assert restored == original
    assert restored.secret_kind == "API_TOKEN"


def test_detected_entity_round_trip_via_json_string() -> None:
    """DetectedEntity → JSON-строка → from_dict → равен исходному."""
    original = DetectedEntity(
        entity_type=EntityType.PHONE,
        start=5,
        end=22,
        confidence=DetectionConfidence.MEDIUM,
        source="regex",
        fingerprint="feeddeadbeef",
    )
    json_str = json.dumps(original.to_dict(), ensure_ascii=False)
    restored = DetectedEntity.from_dict(json.loads(json_str))
    assert restored == original


def test_detected_entity_from_dict_invalid_entity_type() -> None:
    """from_dict с неизвестным entity_type выбрасывает ValueError."""
    bad = {
        "entity_type": "NONEXISTENT_TYPE",
        "start": 0,
        "end": 5,
        "confidence": "HIGH",
        "source": "regex",
        "fingerprint": "aabbccddeeff",
    }
    with pytest.raises(ValueError):
        DetectedEntity.from_dict(bad)


def test_detected_entity_from_dict_invalid_range() -> None:
    """from_dict с start >= end выбрасывает ValueError."""
    bad = {
        "entity_type": "EMAIL",
        "start": 10,
        "end": 10,
        "confidence": "HIGH",
        "source": "regex",
        "fingerprint": "aabbccddeeff",
    }
    with pytest.raises(ValueError):
        DetectedEntity.from_dict(bad)


def test_detected_entity_from_dict_missing_field() -> None:
    """from_dict без обязательного поля выбрасывает KeyError."""
    bad = {
        "entity_type": "EMAIL",
        "start": 0,
        "end": 5,
        # confidence отсутствует
        "source": "regex",
        "fingerprint": "aabbccddeeff",
    }
    with pytest.raises(KeyError):
        DetectedEntity.from_dict(bad)


def test_detected_entity_to_dict_no_secret_kind_key() -> None:
    """to_dict без secret_kind не включает ключ secret_kind в словарь."""
    entity = DetectedEntity(
        entity_type=EntityType.DATE,
        start=0,
        end=10,
        confidence=DetectionConfidence.HIGH,
        source="regex",
        fingerprint="112233445566",
    )
    d = entity.to_dict()
    assert "secret_kind" not in d


# ---------------------------------------------------------------------------
# TokenRecord round-trip
# ---------------------------------------------------------------------------

def test_token_record_round_trip() -> None:
    """TokenRecord → to_dict → from_dict → равен исходному."""
    original = TokenRecord(
        token="[EMAIL_1]",
        entity_type=EntityType.EMAIL,
        fingerprint="abc123def456",
    )
    restored = TokenRecord.from_dict(original.to_dict())
    assert restored == original


def test_token_record_round_trip_with_secret_kind() -> None:
    """TokenRecord с secret_kind корректно сериализуется."""
    original = TokenRecord(
        token="[SECRET_1]",
        entity_type=EntityType.HOST,
        fingerprint="deadbeef0000",
        secret_kind="PASSWORD",
    )
    restored = TokenRecord.from_dict(original.to_dict())
    assert restored == original


def test_token_record_round_trip_via_json_string() -> None:
    """TokenRecord → JSON-строка → from_dict → равен исходному."""
    original = TokenRecord(
        token="[PHONE_1]",
        entity_type=EntityType.PHONE,
        fingerprint="cafebabe1234",
    )
    json_str = json.dumps(original.to_dict(), ensure_ascii=False)
    restored = TokenRecord.from_dict(json.loads(json_str))
    assert restored == original


def test_token_record_from_dict_invalid_entity_type() -> None:
    """TokenRecord.from_dict с неизвестным entity_type выбрасывает ValueError."""
    bad = {
        "token": "[X_1]",
        "entity_type": "UNKNOWN",
        "fingerprint": "000000000000",
    }
    with pytest.raises(ValueError):
        TokenRecord.from_dict(bad)


# ---------------------------------------------------------------------------
# ManifestEntry round-trip
# ---------------------------------------------------------------------------

def test_manifest_entry_round_trip() -> None:
    """ManifestEntry → to_dict → from_dict → равен исходному."""
    original = ManifestEntry(
        token="[EMAIL_1]",
        entity_type=EntityType.EMAIL,
        fingerprint="abc123def456",
        encrypted_value=b"\x00\x01\x02synthetic_ciphertext",
    )
    restored = ManifestEntry.from_dict(original.to_dict())
    assert restored == original


def test_manifest_entry_round_trip_via_json_string() -> None:
    """ManifestEntry → JSON-строка → from_dict → равен исходному."""
    original = ManifestEntry(
        token="[HOST_1]",
        entity_type=EntityType.HOST,
        fingerprint="deadbeef1234",
        encrypted_value=b"synthetic_encrypted_bytes_xyz",
    )
    json_str = json.dumps(original.to_dict(), ensure_ascii=False)
    restored = ManifestEntry.from_dict(json.loads(json_str))
    assert restored == original


def test_manifest_entry_encrypted_value_is_bytes() -> None:
    """Десериализованный encrypted_value — bytes, не строка."""
    original = ManifestEntry(
        token="[PHONE_1]",
        entity_type=EntityType.PHONE,
        fingerprint="cafebabe5678",
        encrypted_value=b"\xde\xad\xbe\xef",
    )
    restored = ManifestEntry.from_dict(original.to_dict())
    assert isinstance(restored.encrypted_value, bytes)
    assert restored.encrypted_value == b"\xde\xad\xbe\xef"


def test_manifest_entry_with_secret_kind() -> None:
    """ManifestEntry с secret_kind корректно сериализуется."""
    original = ManifestEntry(
        token="[SECRET_1]",
        entity_type=EntityType.HOST,
        fingerprint="000111222333",
        encrypted_value=b"cipher",
        secret_kind="API_TOKEN",
    )
    restored = ManifestEntry.from_dict(original.to_dict())
    assert restored == original


def test_manifest_entry_to_dict_no_secret_kind_key() -> None:
    """to_dict без secret_kind не включает ключ secret_kind."""
    entry = ManifestEntry(
        token="[DATE_1]",
        entity_type=EntityType.DATE,
        fingerprint="112233445566",
        encrypted_value=b"data",
    )
    d = entry.to_dict()
    assert "secret_kind" not in d


# ---------------------------------------------------------------------------
# detect_entities — пустой вход и вход без сущностей
# ---------------------------------------------------------------------------

def test_detect_empty_string_returns_empty_list(cfg: DetectorConfig) -> None:
    """detect_entities с пустой строкой возвращает пустой список без исключения."""
    result = detect_entities("", cfg)
    assert result == []


def test_detect_text_without_entities_returns_empty_list(
    cfg: DetectorConfig,
) -> None:
    """Текст без PII и секретов возвращает пустой список."""
    text = "Сегодня хорошая погода и никаких чувствительных данных."
    result = detect_entities(text, cfg)
    assert result == []


def test_detect_crlf_text_finds_entities(cfg: DetectorConfig) -> None:
    """Текст с CRLF-переносами строк корректно обрабатывается детектором."""
    text = "Строка один.\r\nКонтакт: synth-user@example-test.local\r\nСтрока три."
    result = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.EMAIL for e in result)
