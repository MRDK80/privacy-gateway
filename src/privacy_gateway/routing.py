"""YAML-маршрутизация — Этап Э6.

Публичный контракт:
    load_routing_config(path: Path | None) -> RoutingConfig
    verify_manifest_integrity(route_data: dict, manifest_path: Path) -> None  [Э7]

Загрузка YAML производится ТОЛЬКО через yaml.safe_load.
Неизвестные ключи верхнего уровня и секции rules — явная ошибка.
Попытка разрешить пропуск секретов через конфиг — ошибка конфигурации.
Отсутствие файла конфигурации — работа на безопасных умолчаниях.

Схема конфига (src/privacy_gateway/routing.py:RoutingConfig):

  output_dir: "./pgw_out"          # каталог для артефактов (str)
  overwrite: false                  # перезаписывать ли существующие файлы
  rules:
    tokenize:                       # типы сущностей, которые токенизировать
      - EMAIL
      - PHONE
      - HOST
      - PERSON
    block_unconditionally:          # типы, блокирующие обработку безусловно
      - SECRET                      # (зарезервировано; секреты всегда блокируют)

Поля rules.tokenize и rules.block_unconditionally принимают только
значения из EntityType. Поле block_unconditionally не может содержать
типы из tokenize — это ошибка конфигурации.
Конфиг не может отключить проверку секретов — fail closed.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from privacy_gateway.models import ConfigurationError, EntityType

# Ключи, разрешённые на верхнем уровне конфига
_ALLOWED_TOP_KEYS: frozenset[str] = frozenset({"output_dir", "overwrite", "rules"})
# Ключи, разрешённые внутри секции rules
_ALLOWED_RULES_KEYS: frozenset[str] = frozenset({"tokenize", "block_unconditionally"})

# Известные версии формата route.json
_KNOWN_VERSIONS: frozenset[str] = frozenset({"1.0", "1.1"})

# Безопасные умолчания
_DEFAULT_OUTPUT_DIR = "./pgw_out"
_DEFAULT_TOKENIZE: list[str] = [
    EntityType.EMAIL.value,
    EntityType.PHONE.value,
    EntityType.HOST.value,
    EntityType.PERSON.value,
    EntityType.ORG.value,
    EntityType.DOCUMENT.value,
    EntityType.AMOUNT.value,
    EntityType.METRIC.value,
    EntityType.DATE.value,
    EntityType.DURATION.value,
    EntityType.ENDPOINT.value,
    EntityType.RESOURCE.value,
    EntityType.SYSTEM.value,
    EntityType.PROJECT.value,
    EntityType.ROLE.value,
    EntityType.DEPARTMENT.value,
    EntityType.ENVIRONMENT.value,
]


@dataclass
class RoutingConfig:
    """Конфигурация маршрутизации обработки.

    output_dir:            Каталог для записи артефактов.
    overwrite:             Перезаписывать ли существующие prompt.txt / route.json.
    tokenize_types:        Типы сущностей для токенизации.
    block_unconditionally: Типы, вызывающие немедленную блокировку.
    """

    output_dir: str = _DEFAULT_OUTPUT_DIR
    overwrite: bool = False
    tokenize_types: list[str] = field(default_factory=lambda: list(_DEFAULT_TOKENIZE))
    block_unconditionally: list[str] = field(default_factory=list)


def _validate_entity_type_list(values: Any, field_name: str) -> list[str]:
    """Проверить, что values — список строк из EntityType."""
    if not isinstance(values, list):
        raise ConfigurationError(
            f"rules.{field_name} must be a list, got {type(values).__name__!r}"
        )
    valid = frozenset(e.value for e in EntityType)
    result: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise ConfigurationError(
                f"rules.{field_name} items must be strings, got {type(item).__name__!r}"
            )
        if item not in valid:
            raise ConfigurationError(
                f"Unknown entity type {item!r} in rules.{field_name}. "
                f"Valid types: {sorted(valid)}"
            )
        result.append(item)
    return result


def load_routing_config(path: Path | None) -> RoutingConfig:
    """Загрузить и провалидировать конфигурацию маршрутизации из YAML.

    Если path is None или файл отсутствует — возвращает безопасные умолчания.
    Использует ТОЛЬКО yaml.safe_load.
    Неизвестные ключи — явная ошибка ConfigurationError.
    Попытка ослабить проверку секретов — ошибка ConfigurationError (fail closed).

    Args:
        path: Путь к YAML-файлу конфигурации или None.

    Returns:
        RoutingConfig с загруженными или умолчательными значениями.

    Raises:
        ConfigurationError: Ошибка формата, неизвестные ключи или
                            попытка ослабить безопасность.
    """
    if path is None or not path.exists():
        return RoutingConfig()

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"Cannot read config file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"Malformed YAML in config file {path}: {exc}"
        ) from exc

    if data is None:
        # Пустой файл — умолчания
        return RoutingConfig()

    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Config must be a YAML mapping, got {type(data).__name__!r}"
        )

    # Проверка неизвестных ключей верхнего уровня
    unknown_top = set(data.keys()) - _ALLOWED_TOP_KEYS
    if unknown_top:
        raise ConfigurationError(
            f"Unknown config keys: {sorted(unknown_top)}. "
            f"Allowed: {sorted(_ALLOWED_TOP_KEYS)}"
        )

    cfg = RoutingConfig()

    if "output_dir" in data:
        val = data["output_dir"]
        if not isinstance(val, str):
            raise ConfigurationError(
                f"output_dir must be a string, got {type(val).__name__!r}"
            )
        cfg.output_dir = val

    if "overwrite" in data:
        val = data["overwrite"]
        if not isinstance(val, bool):
            raise ConfigurationError(
                f"overwrite must be a boolean, got {type(val).__name__!r}"
            )
        cfg.overwrite = val

    if "rules" in data:
        rules = data["rules"]
        if rules is None:
            rules = {}
        if not isinstance(rules, dict):
            raise ConfigurationError(
                f"rules must be a YAML mapping, got {type(rules).__name__!r}"
            )
        # Проверка неизвестных ключей в rules
        unknown_rules = set(rules.keys()) - _ALLOWED_RULES_KEYS
        if unknown_rules:
            raise ConfigurationError(
                f"Unknown keys in rules: {sorted(unknown_rules)}. "
                f"Allowed: {sorted(_ALLOWED_RULES_KEYS)}"
            )

        if "tokenize" in rules:
            cfg.tokenize_types = _validate_entity_type_list(
                rules["tokenize"], "tokenize"
            )

        if "block_unconditionally" in rules:
            cfg.block_unconditionally = _validate_entity_type_list(
                rules["block_unconditionally"], "block_unconditionally"
            )

        # Проверка: конфиг не может ослабить проверку секретов
        # (нельзя включить тип из block_unconditionally в tokenize и наоборот)
        conflict = set(cfg.tokenize_types) & set(cfg.block_unconditionally)
        if conflict:
            raise ConfigurationError(
                f"Types cannot be both in tokenize and block_unconditionally: "
                f"{sorted(conflict)}"
            )

    return cfg


def verify_manifest_integrity(
    route_data: dict,  # type: ignore[type-arg]
    manifest_path: Path,
) -> None:
    """Проверить соответствие manifest.json контрольной сумме в route.json.

    Функция предназначена для вызова из команды restore (Э7) перед
    расшифровкой манифеста. Обнаруживает рассинхронизацию пары артефактов
    (route.json и manifest.json от разных запусков prepare) и повреждение
    файла манифеста целиком.

    Поведение по версии:
    - "1.1", поле есть, сумма совпадает  → возвращает None молча.
    - "1.1", поле есть, сумма не совпадает → ConfigurationError.
    - "1.1", поля manifest_sha256 нет      → ConfigurationError (ошибка формата).
    - "1.0"                                → проверка пропускается без ошибки.
    - Неизвестная версия                   → ConfigurationError.

    Сообщения об ошибках не раскрывают содержимое манифеста.
    Допустимо: путь, ожидаемая и фактическая суммы, версия формата.

    Args:
        route_data:    Словарь, загруженный из route.json.
        manifest_path: Путь к файлу manifest.json на диске.

    Raises:
        ConfigurationError: Несовпадение суммы, отсутствие обязательного поля,
                            неизвестная версия формата или отсутствие файла.
    """
    version = route_data.get("format_version", "")

    if version not in _KNOWN_VERSIONS:
        raise ConfigurationError(
            f"Unknown route.json format_version: {version!r}. "
            f"Supported versions: {sorted(_KNOWN_VERSIONS)}"
        )

    if version == "1.0":
        # Обратная совместимость: v1.0 не содержит manifest_sha256
        return

    # version == "1.1"
    if "manifest_sha256" not in route_data:
        raise ConfigurationError(
            f"route.json format_version is \"1.1\" but field manifest_sha256 "
            f"is missing. The file may be malformed or truncated."
        )

    expected_hex: str = route_data["manifest_sha256"]

    if not manifest_path.exists():
        raise ConfigurationError(
            f"manifest.json not found at path: {manifest_path}. "
            f"The file may have been moved or deleted."
        )

    try:
        actual_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise ConfigurationError(
            f"Cannot read manifest file at {manifest_path}: {exc}"
        ) from exc

    actual_hex = hashlib.sha256(actual_bytes).hexdigest()

    if not hmac.compare_digest(actual_hex, expected_hex):
        raise ConfigurationError(
            f"manifest.json integrity check failed for {manifest_path}. "
            f"Expected SHA-256: {expected_hex}, "
            f"actual SHA-256: {actual_hex}. "
            f"The manifest may have been modified or replaced after prepare."
        )
