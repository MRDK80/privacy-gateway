"""Типизированные модели данных Privacy Gateway — Этапы Э2–Э4."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class EntityType(StrEnum):
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


class DetectionConfidence(StrEnum):
    """Уровень уверенности обнаружения."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class InputSource(StrEnum):
    """Источник входного текста."""

    FILE = "file"
    STDIN = "stdin"


class ProcessingStatus(StrEnum):
    """Итоговый статус обработки текста.

    Семантика значений (зафиксирована в Э5):

    OK
        Текст полностью токенизирован, все найденные PII и секреты заменены
        корректными токенами вида ``[TYPE_N]``, остаточных чувствительных
        данных не обнаружено. Текст **можно отправлять** внешней LLM.

    BLOCKED
        Текст содержит необработанные секреты, остаточные PII (email, IP,
        телефон и т.д.) или явно искажённые токены (неправильный формат,
        вложенные/незакрытые скобки). Автоматическая отправка **запрещена**;
        требуется ручная обработка или повторная токенизация.

    PENDING
        Обнаружены аномалии, которые требуют решения человека: например,
        токен с неизвестным типом сущности (``[UNKNOWN_1]``), чья природа
        не может быть определена автоматически. Автоматическая отправка
        **запрещена**. PENDING **не является «мягким OK»** — до получения
        явного одобрения оператора текст считается небезопасным.

    Принцип fail closed: при любой неоднозначности статус не может быть OK.
    """

    OK = "OK"
    BLOCKED = "BLOCKED"
    PENDING = "PENDING"


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

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать в JSON-совместимый словарь."""
        d: dict[str, Any] = {
            "entity_type": self.entity_type.value,
            "start": self.start,
            "end": self.end,
            "confidence": self.confidence.value,
            "source": self.source,
            "fingerprint": self.fingerprint,
        }
        if self.secret_kind is not None:
            d["secret_kind"] = self.secret_kind
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DetectedEntity:
        """Десериализовать из словаря (результат to_dict / JSON).

        Raises:
            KeyError: Обязательное поле отсутствует.
            ValueError: Некорректное значение enum-поля или диапазон [start, end).
        """
        return cls(
            entity_type=EntityType(data["entity_type"]),
            start=int(data["start"]),
            end=int(data["end"]),
            confidence=DetectionConfidence(data["confidence"]),
            source=str(data["source"]),
            fingerprint=str(data["fingerprint"]),
            secret_kind=data.get("secret_kind"),
        )


@dataclass
class TokenRecord:
    """Запись о токене: связывает стабильный токен с fingerprint сущности.

    Исходное значение НЕ хранится — только fingerprint.
    Зашифрованное значение (encrypted_value) хранится в ManifestEntry.
    """

    token: str  # например «[EMAIL_1]»
    entity_type: EntityType
    fingerprint: str
    secret_kind: str | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать в JSON-совместимый словарь."""
        d: dict[str, Any] = {
            "token": self.token,
            "entity_type": self.entity_type.value,
            "fingerprint": self.fingerprint,
        }
        if self.secret_kind is not None:
            d["secret_kind"] = self.secret_kind
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenRecord:
        """Десериализовать из словаря.

        Raises:
            KeyError: Обязательное поле отсутствует.
            ValueError: Некорректное значение EntityType.
        """
        return cls(
            token=str(data["token"]),
            entity_type=EntityType(data["entity_type"]),
            fingerprint=str(data["fingerprint"]),
            secret_kind=data.get("secret_kind"),
        )


@dataclass
class ManifestEntry:
    """Запись зашифрованного манифеста для одного токена.

    encrypted_value — bytes зашифрованного исходного значения (Fernet / AES-GCM).
    Формат шифрования фиксируется в Э4; здесь — только контейнер.
    """

    token: str
    entity_type: EntityType
    fingerprint: str
    encrypted_value: bytes
    secret_kind: str | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать в JSON-совместимый словарь (encrypted_value → hex)."""
        d: dict[str, Any] = {
            "token": self.token,
            "entity_type": self.entity_type.value,
            "fingerprint": self.fingerprint,
            "encrypted_value": self.encrypted_value.hex(),
        }
        if self.secret_kind is not None:
            d["secret_kind"] = self.secret_kind
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManifestEntry:
        """Десериализовать из словаря.

        Raises:
            KeyError: Обязательное поле отсутствует.
            ValueError: Некорректные данные.
        """
        return cls(
            token=str(data["token"]),
            entity_type=EntityType(data["entity_type"]),
            encrypted_value=bytes.fromhex(data["encrypted_value"]),
            fingerprint=str(data["fingerprint"]),
            secret_kind=data.get("secret_kind"),
        )


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


class RestoreStrictError(Exception):
    """Строгий отказ restore по неизвестному или искажённому токену (ADR-21).

    Отличие от RestoreError (ошибка конфигурации / целостности):
    RestoreStrictError означает, что структура артефактов корректна,
    но LLM вернула токен, которого нет в манифесте, либо токен искажён.
    Это другой класс проблем — реакция оператора иная:
      - RestoreError (код 3): проверить пару артефактов route/manifest.
      - RestoreStrictError (код 5): проверить ответ LLM, возможно повторить запрос.
    Зафиксировано в ADR-21.
    """
