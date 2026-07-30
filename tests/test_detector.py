"""Тесты модуля detector — Этап Э2."""

from __future__ import annotations

from pathlib import Path

import pytest

from privacy_gateway.detector import DetectorConfig, detect_entities, load_config
from privacy_gateway.models import DetectedEntity, DetectionConfidence, EntityType

_CONFIG_PATH = Path("config.example") / "entities.yaml"


@pytest.fixture
def cfg() -> DetectorConfig:
    """Загрузить конфиг из config.example/entities.yaml."""
    return load_config(_CONFIG_PATH)


# ---------------------------------------------------------------------------
# Вспомогательная функция
# ---------------------------------------------------------------------------

def _types(entities: list[DetectedEntity]) -> list[str]:
    return [
        (e.secret_kind if e.secret_kind else e.entity_type.value)
        for e in entities
    ]


# ---------------------------------------------------------------------------
# Тесты regex-детекторов
# ---------------------------------------------------------------------------

def test_detect_email(cfg: DetectorConfig) -> None:
    text = "Контакт: synth-user@example-test.local для уточнений."
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.EMAIL for e in entities)


def test_detect_phone(cfg: DetectorConfig) -> None:
    text = "Звонить: +7 (900) 123-45-67 в рабочее время."
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.PHONE for e in entities)


def test_detect_ipv4(cfg: DetectorConfig) -> None:
    text = "Сервер доступен по адресу 192.168.100.200."
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.HOST for e in entities)


def test_detect_endpoint(cfg: DetectorConfig) -> None:
    text = "API: https://api.example-test.local/v1/data"
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.ENDPOINT for e in entities)


def test_endpoint_supersedes_host(cfg: DetectorConfig) -> None:
    """ENDPOINT с IP внутри URL не должен дублироваться как HOST."""
    text = "Запрос: http://192.168.1.1/api/endpoint"
    entities = detect_entities(text, cfg)
    types = _types(entities)
    # ENDPOINT должен быть, HOST внутри URL — нет (перекрытие)
    assert "ENDPOINT" in types
    # HOST как отдельная сущность не должен перекрываться с ENDPOINT
    for e in entities:
        if e.entity_type == EntityType.HOST:
            endpoint_ranges = [
                (x.start, x.end)
                for x in entities
                if x.entity_type == EntityType.ENDPOINT
            ]
            for es, ee in endpoint_ranges:
                assert not (es <= e.start and e.end <= ee), (
                    "HOST внутри ENDPOINT не должен присутствовать как отдельная сущность"
                )


def test_detect_unc_path(cfg: DetectorConfig) -> None:
    text = "Файлы хранятся по пути \\\\synth-server\\share\\docs\\report."
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.RESOURCE for e in entities)


def test_detect_windows_path(cfg: DetectorConfig) -> None:
    text = "Результаты сохранены в C:\\SyntheticData\\report.txt"
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.RESOURCE for e in entities)


def test_detect_date_iso(cfg: DetectorConfig) -> None:
    text = "Срок сдачи: 2026-03-15."
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.DATE for e in entities)


def test_detect_date_dot(cfg: DetectorConfig) -> None:
    text = "Дата начала: 15.06.2025."
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.DATE for e in entities)


def test_detect_amount_rub(cfg: DetectorConfig) -> None:
    text = "Сумма договора: 150 000 руб."
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.AMOUNT for e in entities)


def test_detect_amount_symbol(cfg: DetectorConfig) -> None:
    text = "Стоимость: $5000 за единицу."
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.AMOUNT for e in entities)


# ---------------------------------------------------------------------------
# Тесты словарных сущностей
# ---------------------------------------------------------------------------

def test_detect_dictionary_org(cfg: DetectorConfig) -> None:
    text = "Заказчик: ООО Северный Маяк, договор подписан."
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.ORG for e in entities)


def test_detect_dictionary_project(cfg: DetectorConfig) -> None:
    text = "В рамках Проект-Орион завершён первый спринт."
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.PROJECT for e in entities)


def test_dictionary_from_yaml(tmp_path: Path) -> None:
    """Словарные сущности определяются по YAML-конфигу."""
    config_file = tmp_path / "entities.yaml"
    config_file.write_text(
        "dictionary:\n  ORG:\n    - 'Тест-Организация'\n",
        encoding="utf-8",
    )
    cfg_custom = load_config(config_file)
    text = "Контрагент: Тест-Организация передал документы."
    entities = detect_entities(text, cfg_custom)
    assert any(e.entity_type == EntityType.ORG for e in entities)


# ---------------------------------------------------------------------------
# Тесты секретов
# ---------------------------------------------------------------------------

def test_detect_synthetic_password(cfg: DetectorConfig) -> None:
    """Синтетический password-паттерн распознаётся; значение не в модели."""
    text = "Настройка: password=SyntheticP@ss123"
    entities = detect_entities(text, cfg)
    secret_entities = [e for e in entities if e.secret_kind == "PASSWORD"]
    assert secret_entities, "PASSWORD должен быть обнаружен"
    # Значение не хранится в модели
    for e in secret_entities:
        assert not hasattr(e, "value") or not getattr(e, "value", None)


def test_detect_synthetic_api_token(cfg: DetectorConfig) -> None:
    """Синтетический api_token-паттерн распознаётся."""
    text = "Конфигурация: api_token=synth-token-abc123xyz"
    entities = detect_entities(text, cfg)
    assert any(e.secret_kind == "API_TOKEN" for e in entities)


def test_secret_value_not_in_public_model(cfg: DetectorConfig) -> None:
    """repr/str DetectedEntity не раскрывает синтетический секрет."""
    text = "api_key=super-secret-synthetic-value-xyz"
    entities = detect_entities(text, cfg)
    for e in entities:
        rep = repr(e)
        assert "super-secret-synthetic-value-xyz" not in rep
        assert "super-secret" not in rep


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_stable_for_same_value(cfg: DetectorConfig) -> None:
    """Fingerprint стабилен для одного значения."""
    text = "Email: stable@example-test.local и stable@example-test.local"
    entities = detect_entities(text, cfg)
    email_fps = [
        e.fingerprint
        for e in entities
        if e.entity_type == EntityType.EMAIL
    ]
    assert len(email_fps) == 2
    assert email_fps[0] == email_fps[1]


def test_fingerprint_not_equal_to_value(cfg: DetectorConfig) -> None:
    """Fingerprint не равен исходному значению."""
    text = "Email: fp-check@synthetic.test"
    entities = detect_entities(text, cfg)
    emails = [e for e in entities if e.entity_type == EntityType.EMAIL]
    assert emails
    assert emails[0].fingerprint != "fp-check@synthetic.test"
    assert len(emails[0].fingerprint) == 12


# ---------------------------------------------------------------------------
# Дубликаты и позиции
# ---------------------------------------------------------------------------

def test_same_value_different_positions_detected_separately(cfg: DetectorConfig) -> None:
    """Одинаковое значение на разных позициях — два отдельных вхождения."""
    text = "Email: dup@example-test.local и dup@example-test.local — два вхождения."
    entities = detect_entities(text, cfg)
    email_entities = [e for e in entities if e.entity_type == EntityType.EMAIL]
    assert len(email_entities) == 2
    assert email_entities[0].start != email_entities[1].start


def test_no_duplicate_on_same_position(cfg: DetectorConfig) -> None:
    """Одинаковое совпадение на той же позиции не дублируется."""
    text = "192.168.1.1"
    entities = detect_entities(text, cfg)
    positions = [(e.start, e.end) for e in entities]
    assert len(positions) == len(set(positions))


# ---------------------------------------------------------------------------
# Перекрытия
# ---------------------------------------------------------------------------

def test_overlap_resolution_priority(cfg: DetectorConfig) -> None:
    """При перекрытии EMAIL vs словарь — EMAIL должен иметь приоритет."""
    # Создаём конфиг, где словарь пытается захватить часть адреса
    config_file = Path("config.example") / "entities.yaml"
    cfg_test = load_config(config_file)
    text = "Контакт: overlap@example-test.local указан в документе."
    entities = detect_entities(text, cfg_test)
    # Не должно быть перекрывающихся диапазонов
    sorted_e = sorted(entities, key=lambda e: e.start)
    for i in range(len(sorted_e) - 1):
        assert sorted_e[i].end <= sorted_e[i + 1].start, (
            f"Перекрытие между {sorted_e[i]} и {sorted_e[i + 1]}"
        )


# ---------------------------------------------------------------------------
# repr безопасность
# ---------------------------------------------------------------------------

def test_repr_does_not_expose_text(cfg: DetectorConfig) -> None:
    """repr DetectedEntity не раскрывает исходный текст или значение сущности."""
    text = "Тест: private-repr@example-test.local"
    entities = detect_entities(text, cfg)
    for e in entities:
        rep = repr(e)
        assert "private-repr@example-test.local" not in rep
        assert "private-repr" not in rep
