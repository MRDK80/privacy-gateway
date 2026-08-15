"""Тесты YAML-маршрутизации — Э6."""

from __future__ import annotations

from pathlib import Path

import pytest

from privacy_gateway.models import ConfigurationError
from privacy_gateway.routing import RoutingConfig, load_routing_config


def test_defaults_when_no_file(tmp_path: Path) -> None:
    """Отсутствие файла — безопасные умолчания."""
    cfg = load_routing_config(None)
    assert isinstance(cfg, RoutingConfig)
    assert cfg.overwrite is False
    assert len(cfg.tokenize_types) > 0
    assert cfg.block_unconditionally == []


def test_valid_config(tmp_path: Path) -> None:
    """Валидный конфиг загружается корректно."""
    p = tmp_path / "routing.yaml"
    p.write_text(
        "rules:\n"
        "  tokenize:\n"
        "    - EMAIL\n"
        "    - HOST\n",
        encoding="utf-8",
    )
    cfg = load_routing_config(p)
    assert cfg.tokenize_types == ["EMAIL", "HOST"]


def test_unknown_top_key_raises(tmp_path: Path) -> None:
    """Неизвестный ключ на верхнем уровне — ConfigurationError."""
    p = tmp_path / "routing.yaml"
    p.write_text("unknown_key: foo\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Unknown config keys"):
        load_routing_config(p)


def test_unknown_rules_key_raises(tmp_path: Path) -> None:
    """Неизвестный ключ в rules — ConfigurationError."""
    p = tmp_path / "routing.yaml"
    p.write_text(
        "rules:\n"
        "  tokenize:\n"
        "    - EMAIL\n"
        "  evil_key: foo\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Unknown keys in rules"):
        load_routing_config(p)


def test_yaml_injection_blocked(tmp_path: Path) -> None:
    """Защита от YAML-инъекции через safe_load."""
    p = tmp_path / "routing.yaml"
    p.write_text(
        "output_dir: !!python/object/apply:os.system ['id']\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception):  # yaml.constructor.ConstructorError
        load_routing_config(p)


def test_conflict_tokenize_block_raises(tmp_path: Path) -> None:
    """Конфликт tokenize / block_unconditionally — ConfigurationError."""
    p = tmp_path / "routing.yaml"
    p.write_text(
        "rules:\n"
        "  tokenize:\n"
        "    - EMAIL\n"
        "  block_unconditionally:\n"
        "    - EMAIL\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ConfigurationError,
        match="both in tokenize and block_unconditionally",
    ):
        load_routing_config(p)


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    """Битый YAML — ConfigurationError."""
    p = tmp_path / "routing.yaml"
    p.write_text(": invalid: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Malformed YAML"):
        load_routing_config(p)


def test_none_path_returns_defaults() -> None:
    """Путь None — умолчания без ошибки."""
    cfg = load_routing_config(None)
    assert isinstance(cfg, RoutingConfig)


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    """Отсутствующий файл — умолчания без ошибки."""
    cfg = load_routing_config(tmp_path / "nonexistent.yaml")
    assert isinstance(cfg, RoutingConfig)


def test_invalid_entity_type_raises(tmp_path: Path) -> None:
    """Невалидный тип сущности — ConfigurationError."""
    p = tmp_path / "routing.yaml"
    p.write_text(
        "rules:\n"
        "  tokenize:\n"
        "    - INVALID_TYPE\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Unknown entity type"):
        load_routing_config(p)
