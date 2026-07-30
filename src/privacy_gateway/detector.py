"""Детерминированное обнаружение потенциально чувствительных сущностей — Этап Э2.

Использует regex и локальные синтетические словари из YAML-конфига.
Автоматическое NER не применяется.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from privacy_gateway.models import (
    ConfigurationError,
    DetectedEntity,
    DetectionConfidence,
    EntityType,
    _safe_fingerprint,
)

# ---------------------------------------------------------------------------
# Таблица приоритетов перекрытий (чем меньше число — тем выше приоритет).
#
# PRIVATE_KEY / CONNECTION_STRING / API_TOKEN / PASSWORD  → 0
# ENDPOINT                                                → 1
# EMAIL                                                   → 2
# RESOURCE                                                → 3
# HOST                                                    → 4
# PHONE                                                   → 5
# AMOUNT                                                  → 6
# DATE                                                    → 7
# словарные сущности                                      → 8
# ---------------------------------------------------------------------------

_SECRET_KIND_PRIORITY: dict[str, int] = {
    "PRIVATE_KEY": 0,
    "CONNECTION_STRING": 0,
    "API_TOKEN": 0,
    "PASSWORD": 0,
}

_TYPE_PRIORITY: dict[EntityType, int] = {
    EntityType.ENDPOINT: 1,
    EntityType.EMAIL: 2,
    EntityType.RESOURCE: 3,
    EntityType.HOST: 4,
    EntityType.PHONE: 5,
    EntityType.AMOUNT: 6,
    EntityType.DATE: 7,
    # словарные типы → 8 (назначается в _DICTIONARY_TYPES)
    EntityType.PERSON: 8,
    EntityType.ORG: 8,
    EntityType.SYSTEM: 8,
    EntityType.PROJECT: 8,
    EntityType.DEPARTMENT: 8,
    EntityType.ROLE: 8,
    EntityType.ENVIRONMENT: 8,
}


@dataclass
class DetectorConfig:
    """Конфигурация детектора, загруженная из YAML."""

    dictionary: dict[str, list[str]] = field(default_factory=dict)
    enabled_regex_types: set[str] = field(default_factory=set)


def load_config(config_path: Path) -> DetectorConfig:
    """Загрузить DetectorConfig из YAML-файла.

    Raises:
        ConfigurationError: Файл недоступен или содержит невалидный YAML.
    """
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"Cannot read config file: {config_path.name!r}."
        ) from exc

    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"Invalid YAML in config {config_path.name!r}."
        ) from exc

    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Config {config_path.name!r} must be a YAML mapping."
        )

    dictionary: dict[str, list[str]] = {}
    raw_dict = data.get("dictionary", {})
    if isinstance(raw_dict, dict):
        for key, values in raw_dict.items():
            if isinstance(values, list):
                dictionary[str(key)] = [str(v) for v in values if v]

    enabled: set[str] = set()
    raw_enabled = data.get("enabled_regex_types", [])
    if isinstance(raw_enabled, list):
        enabled = {str(e).upper() for e in raw_enabled}

    return DetectorConfig(dictionary=dictionary, enabled_regex_types=enabled)


# ---------------------------------------------------------------------------
# Regex-паттерны
# ---------------------------------------------------------------------------

# EMAIL
_RE_EMAIL = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# PHONE — российские и международные форматы
_RE_PHONE = re.compile(
    r"(?:"
    r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    r"|\+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{2,4}[\s\-]?\d{2,4}(?:[\s\-]?\d{2,4})?"
    r")"
)

# HOST — IPv4
_RE_HOST = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)

# ENDPOINT — http/https URL
_RE_ENDPOINT = re.compile(
    r"https?://[^\s\"'<>\[\]]+",
    re.IGNORECASE,
)

# RESOURCE — UNC-пути и Windows-пути с буквой диска
_RE_RESOURCE = re.compile(
    r"(?:"
    r"\\\\[\w\-.]+(?:\\[\w\-. ]+)+"
    r"|[A-Za-z]:\\(?:[\w\-. ]+\\)*[\w\-. ]+"
    r")"
)

# DATE — YYYY-MM-DD, DD.MM.YYYY, DD/MM/YYYY
_RE_DATE = re.compile(
    r"\b(?:"
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"|(?:0[1-9]|[12]\d|3[01])[./](?:0[1-9]|1[0-2])[./]\d{4}"
    r")\b"
)

# AMOUNT — денежные суммы с символом/кодом/словом валюты
_RE_AMOUNT = re.compile(
    r"(?:"
    r"[\$€£¥₽]\s*\d[\d\s,.']*"
    r"|\d[\d\s,.']*\s*(?:USD|EUR|GBP|RUB|руб(?:\.?)|тыс\.?\s*руб(?:\.?)|млн\.?\s*руб(?:\.?)|рублей|долларов|евро)"
    r")",
    re.IGNORECASE,
)

# Синтетические секреты — паттерны для внутреннего обнаружения
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("PASSWORD", re.compile(r"(?:password|passwd|пароль)\s*[:=]\s*\S+", re.IGNORECASE)),
    ("API_TOKEN", re.compile(r"(?:api[_\-]?token|api[_\-]?key|apikey)\s*[:=]\s*\S+", re.IGNORECASE)),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE)),
    ("CONNECTION_STRING", re.compile(
        r"(?:Server|Data Source|Host)\s*=\s*[^;]+;[^;]*(?:Database|Initial Catalog)\s*=\s*[^;]+",
        re.IGNORECASE,
    )),
]


# ---------------------------------------------------------------------------
# Внутренний кандидат перед разрешением перекрытий
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
    entity_type: EntityType
    start: int
    end: int
    confidence: DetectionConfidence
    source: str
    value: str  # только внутри функции; в публичную модель не попадает
    secret_kind: str | None = None
    priority: int = 8


def _priority_of(c: _Candidate) -> int:
    if c.secret_kind and c.secret_kind in _SECRET_KIND_PRIORITY:
        return _SECRET_KIND_PRIORITY[c.secret_kind]
    return _TYPE_PRIORITY.get(c.entity_type, 8)


# ---------------------------------------------------------------------------
# Сбор кандидатов
# ---------------------------------------------------------------------------

def _collect_regex_candidates(
    text: str, enabled: set[str]
) -> list[_Candidate]:
    """Собрать кандидатов из regex-детекторов."""
    candidates: list[_Candidate] = []

    # Секреты — всегда включены
    for sk, pattern in _SECRET_PATTERNS:
        for m in pattern.finditer(text):
            candidates.append(
                _Candidate(
                    entity_type=EntityType.HOST,  # тип-заглушка; выводится через secret_kind
                    start=m.start(),
                    end=m.end(),
                    confidence=DetectionConfidence.HIGH,
                    source="regex",
                    value=m.group(),
                    secret_kind=sk,
                    priority=_SECRET_KIND_PRIORITY[sk],
                )
            )

    def _add(pattern: re.Pattern[str], etype: EntityType, conf: DetectionConfidence) -> None:
        ename = etype.value
        if enabled and ename not in enabled:
            return
        for m in pattern.finditer(text):
            candidates.append(
                _Candidate(
                    entity_type=etype,
                    start=m.start(),
                    end=m.end(),
                    confidence=conf,
                    source="regex",
                    value=m.group(),
                    priority=_TYPE_PRIORITY.get(etype, 8),
                )
            )

    # ENDPOINT до HOST — приоритет выше, разрешится в resolve_overlaps
    _add(_RE_ENDPOINT, EntityType.ENDPOINT, DetectionConfidence.HIGH)
    _add(_RE_EMAIL, EntityType.EMAIL, DetectionConfidence.HIGH)
    _add(_RE_RESOURCE, EntityType.RESOURCE, DetectionConfidence.HIGH)
    _add(_RE_HOST, EntityType.HOST, DetectionConfidence.HIGH)
    _add(_RE_PHONE, EntityType.PHONE, DetectionConfidence.MEDIUM)
    _add(_RE_AMOUNT, EntityType.AMOUNT, DetectionConfidence.MEDIUM)
    _add(_RE_DATE, EntityType.DATE, DetectionConfidence.HIGH)

    return candidates


def _collect_dictionary_candidates(
    text: str, dictionary: dict[str, list[str]]
) -> list[_Candidate]:
    """Собрать кандидатов из словарных сущностей."""
    candidates: list[_Candidate] = []
    text_lower = text.lower()

    _DICT_TYPE_MAP: dict[str, EntityType] = {
        "PERSON": EntityType.PERSON,
        "ORG": EntityType.ORG,
        "SYSTEM": EntityType.SYSTEM,
        "PROJECT": EntityType.PROJECT,
        "DEPARTMENT": EntityType.DEPARTMENT,
        "ROLE": EntityType.ROLE,
        "ENVIRONMENT": EntityType.ENVIRONMENT,
    }

    for type_key, values in dictionary.items():
        etype = _DICT_TYPE_MAP.get(type_key.upper())
        if etype is None:
            continue
        for value in values:
            if not value:
                continue
            val_lower = value.lower()
            start = 0
            while True:
                idx = text_lower.find(val_lower, start)
                if idx == -1:
                    break
                end = idx + len(value)
                candidates.append(
                    _Candidate(
                        entity_type=etype,
                        start=idx,
                        end=end,
                        confidence=DetectionConfidence.MEDIUM,
                        source="dictionary",
                        value=text[idx:end],
                        priority=8,
                    )
                )
                start = idx + 1

    return candidates


# ---------------------------------------------------------------------------
# Разрешение перекрытий
# ---------------------------------------------------------------------------

def _resolve_overlaps(candidates: list[_Candidate]) -> list[_Candidate]:
    """Применить детерминированную политику разрешения перекрытий.

    Политика:
    1. При пересечении — наибольший приоритет (меньшее число).
    2. При равном приоритете — более длинное совпадение.
    3. При равной длине — более раннее начало.
    4. Дубликаты на той же позиции удаляются.
    """
    # Сортируем: приоритет ASC, длина DESC, start ASC
    sorted_cands = sorted(
        candidates,
        key=lambda c: (c.priority, -(c.end - c.start), c.start),
    )

    accepted: list[_Candidate] = []
    for c in sorted_cands:
        overlaps = any(
            not (c.end <= a.start or c.start >= a.end) for a in accepted
        )
        if not overlaps:
            accepted.append(c)

    # Сортируем результат по позиции
    accepted.sort(key=lambda c: c.start)
    return accepted


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def detect_entities(
    text: str, config: DetectorConfig
) -> list[DetectedEntity]:
    """Обнаружить потенциально чувствительные сущности в тексте.

    Args:
        text: Входной текст (строка в памяти).
        config: DetectorConfig, загруженный из YAML.

    Returns:
        Список DetectedEntity, отсортированный по start.
        Исходные значения в публичные модели не попадают.
    """
    candidates: list[_Candidate] = []
    candidates.extend(_collect_regex_candidates(text, config.enabled_regex_types))
    candidates.extend(_collect_dictionary_candidates(text, config.dictionary))

    resolved = _resolve_overlaps(candidates)

    result: list[DetectedEntity] = []
    for c in resolved:
        result.append(
            DetectedEntity(
                entity_type=c.entity_type,
                start=c.start,
                end=c.end,
                confidence=c.confidence,
                source=c.source,
                fingerprint=_safe_fingerprint(c.value),
                secret_kind=c.secret_kind,
            )
        )

    return result
