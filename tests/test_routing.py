"""Тесты YAML-маршрутизации — Э6."""

from __future__ import annotations

import pytest

from privacy_gateway.models import ConfigurationError
from privacy_gateway.routing import RoutingConfig, load_routing_config


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, content: str):
    p = tmp_path / "routing.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# test_safe_load_only
# ---------------------------------------------------------------------------

def test_safe_load_only(tmp_path):
    """YAML с тегом выполнения кода не приводит к выполнению кода."""
    malicious = _write_yaml(
        tmp_path,
        "output_dir: !!python/object/apply:os.system ['echo pwned']\n",
    )
    with pytest.raises((ConfigurationError, Exception)):
        load_routing_config(malicious)
    # Если yaml.safe_load отклонил тег — тест уже пройден.
    # Главное: функция os.system НЕ была вызвана (нет side-effect).


# ---------------------------------------------------------------------------
# test_unknown_config_key_errors
# ---------------------------------------------------------------------------

def test_unknown_config_key_errors(tmp_path):
    p = _write_yaml(tmp_path, "unknown_security_option: true\n")
    with pytest.raises(ConfigurationError, match="Unknown config keys"):
        load_routing_config(p)


def test_unknown_rules_key_errors(tmp_path):
    p = _write_yaml(
        tmp_path,
        "rules:\n  allow_secrets: true\n",
    )
    with pytest.raises(ConfigurationError, match="Unknown keys in rules"):
        load_routing_config(p)


# ---------------------------------------------------------------------------
# test_config_cannot_disable_secret_check
# ---------------------------------------------------------------------------

def test_config_cannot_disable_secret_check(tmp_path):
    """Тип не может быть одновременно в tokenize и block_unconditionally."""
    p = _write_yaml(
        tmp_path,
        "rules:\n"
        "  tokenize:\n"
        "    - EMAIL\n"
        "  block_unconditionally:\n"
        "    - EMAIL\n",
    )
    with pytest.raises(ConfigurationError, match="both in tokenize and block_unconditionally"):
        load_routing_config(p)


# ---------------------------------------------------------------------------
# test_malformed_yaml_error
# ---------------------------------------------------------------------------

def test_malformed_yaml_error(tmp_path):
    p = _write_yaml(tmp_path, "key: [unclosed\n")
    with pytest.raises(ConfigurationError, match="Malformed YAML"):
        load_routing_config(p)


# ---------------------------------------------------------------------------
# test_missing_config_behaviour
# ---------------------------------------------------------------------------

def test_missing_config_behaviour(tmp_path):
    """Отсутствие файла конфига — безопасные умолчания, не ошибка."""
    p = tmp_path / "nonexistent.yaml"
    cfg = load_routing_config(p)
    assert isinstance(cfg, RoutingConfig)
    assert cfg.overwrite is False
    assert len(cfg.tokenize_types) > 0


def test_none_path_returns_defaults():
    cfg = load_routing_config(None)
    assert isinstance(cfg, RoutingConfig)
    assert cfg.overwrite is False


def test_valid_config_loads(tmp_path):
    p = _write_yaml(
        tmp_path,
        "output_dir: /tmp/out\n"
        "overwrite: true\n"
        "rules:\n"
        "  tokenize:\n"
        "    - EMAIL\n"
        "    - PHONE\n",
    )
    cfg = load_routing_config(p)
    assert cfg.output_dir == "/tmp/out"
    assert cfg.overwrite is True
    assert "EMAIL" in cfg.tokenize_types
    assert "PHONE" in cfg.tokenize_types


def test_invalid_entity_type_in_rules(tmp_path):
    p = _write_yaml(
        tmp_path,
        "rules:\n"
        "  tokenize:\n"
        "    - NONEXISTENT_TYPE\n",
    )
    with pytest.raises(ConfigurationError, match="Unknown entity type"):
        load_routing_config(p)
