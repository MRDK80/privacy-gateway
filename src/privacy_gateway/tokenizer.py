"""Слой токенизации текста — Э4.

Публичный контракт:
    tokenize(text, entities) -> tuple[str, list[TokenRecord]]

Область стабильности токена: **вариант A** — внутри одного документа.
Нумерация ведётся счётчиком по порядку первого вхождения (детерминированно).
Состояние между запусками не сохраняется.

Формат токена: [TYPE_N], например [EMAIL_1], [PHONE_2], [HOST_1].
Токен не содержит фрагментов исходного значения.

Диапазоны обрабатываются справа налево. При пересечении следующий
диапазон пропускается с предупреждением. Если пропущенный LOC покрыт
выбранными диапазонами лишь частично, токенизация завершается ошибкой.

Стратегия присвоения токена вынесена в TokenAssignmentStrategy,
чтобы поведение можно было заменить без изменения вызывающего кода.
"""

from __future__ import annotations

import logging
from typing import Protocol

from privacy_gateway.models import (
    ConfigurationError,
    DetectedEntity,
    EntityType,
    TokenRecord,
)

log = logging.getLogger(__name__)


class TokenAssignmentStrategy(Protocol):
    """Протокол стратегии присвоения токенов.

    Принимает сущность и возвращает стабильный токен-строку.
    """

    def assign(self, entity: DetectedEntity, value: str) -> str:  # noqa: D102
        ...


class PerDocumentStrategy:
    """Вариант A: счётчик внутри документа.

    Сбрасывается при создании нового экземпляра.
    """

    def __init__(
        self, supplemental_spans: frozenset[tuple[int, int]] = frozenset()
    ) -> None:
        """Optionally distinguish supplemental spans from accepted findings."""
        self._supplemental_spans = supplemental_spans
        self._supplemental_tokens: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}
        self._seen: dict[str, str] = {}
        self._seen_locations: dict[str, str] = {}

    def assign(self, entity: DetectedEntity, value: str) -> str:  # noqa: D102
        seen = (
            self._seen_locations if entity.entity_type == EntityType.LOC else self._seen
        )
        type_key = entity.entity_type.value
        if (entity.start, entity.end) in self._supplemental_spans:
            existing = seen.get(value)
            if existing is not None and existing.startswith(f"[{type_key}_"):
                return existing
            key = type_key, value
            if key not in self._supplemental_tokens:
                self._supplemental_tokens[key] = self._new_token(type_key)
            return self._supplemental_tokens[key]
        if value in seen:
            return seen[value]
        token = self._new_token(type_key)
        seen[value] = token
        return token

    def _new_token(self, type_key: str) -> str:
        self._counters[type_key] = self._counters.get(type_key, 0) + 1
        return f"[{type_key}_{self._counters[type_key]}]"


def tokenize(
    text: str,
    entities: list[DetectedEntity],
    original_values: list[str] | None = None,
    strategy: TokenAssignmentStrategy | None = None,
) -> tuple[str, list[TokenRecord]]:
    """Заменить обнаруженные сущности в *text* на токены.

    Args:
        text:            Исходный текст.
        entities:        Список DetectedEntity (могут содержать перекрытия).
        original_values: Исходные значения параллельно entities; если None —
                         извлекаются из *text* по диапазонам [start, end).
        strategy:        Стратегия присвоения токенов; по умолчанию
                         PerDocumentStrategy (вариант A).

    Returns:
        Кортеж (tokenized_text, list[TokenRecord]).
    """
    if not entities:
        return text, []

    if strategy is None:
        strategy = PerDocumentStrategy()

    if original_values is not None and len(original_values) != len(entities):
        raise ValueError("original_values must have the same length as entities")

    indexed = sorted(enumerate(entities), key=lambda t: t[1].start, reverse=True)

    token_map: dict[int, TokenRecord] = {}
    covered: list[tuple[int, int]] = []

    for orig_idx, entity in indexed:
        s, e = entity.start, entity.end
        overlaps = any(not (e <= cs or s >= ce) for cs, ce in covered)
        if overlaps:
            log.warning(
                "Skipping overlapping entity %r at [%d, %d)",
                entity.entity_type.value,
                s,
                e,
            )
            continue
        covered.append((s, e))

        value = (
            original_values[orig_idx]
            if original_values is not None
            else text[s:e]
        )
        token = strategy.assign(entity, value)
        token_map[orig_idx] = TokenRecord(
            token=token,
            entity_type=entity.entity_type,
            fingerprint=entity.fingerprint,
            secret_kind=entity.secret_kind,
        )

    skipped_locations = [
        (entity.start, entity.end)
        for idx, entity in indexed
        if entity.entity_type == EntityType.LOC and idx not in token_map
    ]
    if skipped_locations:
        spans = sorted(covered)
        for start, end in skipped_locations:
            cursor = start
            for left, right in spans:
                if left > cursor or cursor >= end:
                    break
                cursor = max(cursor, right)
            if cursor < end:
                raise ConfigurationError(
                    "Tokenization would leave part of a LOC unprotected."
                )

    result = text
    for orig_idx, entity in indexed:
        if orig_idx not in token_map:
            continue
        s, e = entity.start, entity.end
        result = result[:s] + token_map[orig_idx].token + result[e:]

    records_ordered = [
        token_map[i]
        for i, _ in sorted(enumerate(entities), key=lambda t: t[1].start)
        if i in token_map
    ]
    return result, records_ordered
