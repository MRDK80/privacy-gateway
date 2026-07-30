"""Дополнительные тесты кроссплатформенности детектора — Windows и Linux пути."""

from __future__ import annotations

from pathlib import Path

import pytest

from privacy_gateway.detector import DetectorConfig, detect_entities, load_config
from privacy_gateway.models import EntityType

_CONFIG_PATH = Path("config.example") / "entities.yaml"


@pytest.fixture
def cfg() -> DetectorConfig:
    return load_config(_CONFIG_PATH)


def test_detect_linux_path_opt(cfg: DetectorConfig) -> None:
    """Linux/POSIX абсолютный путь определяется как RESOURCE."""
    text = "Конфиг расположен в /opt/synthetic-app/config/settings.yaml"
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.RESOURCE for e in entities)


def test_detect_linux_path_home(cfg: DetectorConfig) -> None:
    """Путь вида /home/user/data/file.txt определяется как RESOURCE."""
    text = "Данные в /home/synth-user/data/export.csv сформированы."
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.RESOURCE for e in entities)


def test_detect_linux_path_var_log(cfg: DetectorConfig) -> None:
    """Путь /var/log/... определяется как RESOURCE."""
    text = "Лог: /var/log/synthetic/app.log"
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.RESOURCE for e in entities)


def test_detect_windows_path_still_works(cfg: DetectorConfig) -> None:
    """Windows-путь с буквой диска по-прежнему определяется."""
    text = "Результаты сохранены в C:\\SyntheticData\\report.txt"
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.RESOURCE for e in entities)


def test_detect_unc_path_still_works(cfg: DetectorConfig) -> None:
    """UNC-путь по-прежнему определяется."""
    text = "Файлы по пути \\\\synth-server\\share\\docs\\report"
    entities = detect_entities(text, cfg)
    assert any(e.entity_type == EntityType.RESOURCE for e in entities)


def test_url_path_not_split_into_resource(cfg: DetectorConfig) -> None:
    """Путь внутри URL не выделяется отдельно как RESOURCE."""
    text = "API: https://api.example-test.local/v1/data/items"
    entities = detect_entities(text, cfg)
    endpoints = [
        (e.start, e.end)
        for e in entities
        if e.entity_type == EntityType.ENDPOINT
    ]
    assert endpoints
    for e in entities:
        if e.entity_type == EntityType.RESOURCE:
            for es, ee in endpoints:
                assert not (es <= e.start and e.end <= ee)
