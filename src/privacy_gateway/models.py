"""Типизированные модели данных Privacy Gateway — Этап Э2."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class EntityType(str, Enum):
    """Допустимые типы обнаруживаемых сущностей."""

    PERSON = "PERSON"
    ROLE = "ROLE"
    ORG = "ORG"
    DEPARTMENT = "DEPARTMENT"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    HOST = "HOST"
    ENDPOINT = "ENDPOINT"
    RESOURCE = "RESOURCE"
    SYSTEM = "SYSTEM"
    PROJECT = "PROJECT"
    AMOUNT = "AMOUNT"
    METRIC = "METRIC"
    DOCUMENT = "DOCUMENT"
    DATE = "DATE"
    DURATION = "DURATION"
    ENVIRONMENT = "ENVIRONMENT"


class DetectionConfidence(str, Enum):
    """Уровень уверенности обнаружения."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class InputSource(str, Enum):
    """Источник входного текста."""

    FILE = "file"
    STDIN = "stdin"


@dataclass
class InputText:
    """Входной текст с метаданными источника. Значение text не раскрывается в repr."""

    text: str
    source: InputSource
    encoding: str
    path: Path | None = field(default=None)

    def __repr__(self) -> str:  # noqa: D105
        src = self.path.name if self.path else self.source.value
        return (
            f"InputText(source={self.source.value!r}, "
            f"encoding={self.encoding!r}, "
            f"chars={len(self.text)}, "
            f"ref={src!r})"
        )

    def __str__(self) -> str:  # noqa: D105
        return repr(self)


def _safe_fingerprint(value: str) -> str:
    """Вычислить SHA-256 от UTF-8 значения и вернуть первые 12 hex-символов."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


@dataclass
class DetectedEntity:
    """Обнаруженная сущность.

    Интервал полузакрытый: [start, end).
    Исходное значение НЕ сохраняется — только fingerprint.
    repr и str не раскрывают значение.
    """

    entity_type: EntityType
    start: int
    end: int
    confidence: DetectionConfidence
    source: str  # "regex" | "dictionary"
    fingerprint: str
    secret_kind: str | None = field(default=None)  # PASSWORD / API_TOKEN / etc.

    def __post_init__(self) -> None:  # noqa: D105
        if self.start >= self.end:
            raise ValueError(f"start must be < end, got [{self.start}, {self.end})")

    def __repr__(self) -> str:  # noqa: D105
        sk = f", secret_kind={self.secret_kind!r}" if self.secret_kind else ""
        return (
            f"DetectedEntity(type={self.entity_type.value!r}, "
            f"[{self.start}, {self.end}), "
            f"confidence={self.confidence.value!r}, "
            f"source={self.source!r}, "
            f"fingerprint={self.fingerprint!r}"
            f"{sk})"
        )

    def __str__(self) -> str:  # noqa: D105
        return repr(self)


# ---------------------------------------------------------------------------
# Исключения
# ---------------------------------------------------------------------------


class InputError(Exception):
    """Базовое исключение для ошибок входного текста."""


class UnsupportedInputError(InputError):
    """Неподдерживаемый тип файла или источник."""


class EncodingError(InputError):
    """Ошибка декодирования входного текста."""


class ConfigurationError(Exception):
    """Ошибка загрузки или валидации конфигурации."""
