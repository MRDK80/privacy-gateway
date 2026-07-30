"""Слой токенизации текста — Э4.

Публичный контракт:
    tokenize(text, entities) -> tuple[str, list[TokenRecord]]

Область стабильности токена: **вариант A** — внутри одного документа.
Нумерация ведётся счётчиком по порядку первого вхождения (детерминированно).
Состояние между запусками не сохраняется.

Формат токена: [TYPE_N], например [EMAIL_1], [PHONE_2], [HOST_1].
Токен не содержит фрагментов исходного значения.

Пересекающиеся/вложенные диапазоны: сущности сортируются по убыванию
start перед подстановкой, что корректно работает при любых непересекающихся
и вложенных диапазонах. Реально пересекающиеся (overlapping) диапазоны
отбрасываются в пользу раннего (меньший start), позднее вхождение пропускается
с предупреждением (не raises, чтобы не ломать пайплайн).

Стратегия присвоения токена вынесена в TokenAssignmentStrategy,
чтобы поведение можно было заменить без изменения вызывающего кода.
"""

from __future__ import annotations

import logging
from typing import Protocol

from privacy_gateway.models import DetectedEntity, TokenRecord

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

    def __init__(self) -> None:  # noqa: D107
        self._counters: dict[str, int] = {}
        self._seen: dict[str, str] = {}

    def assign(self, entity: DetectedEntity, value: str) -> str:  # noqa: D102
        if value in self._seen:
            return self._seen[value]
        type_key = entity.entity_type.value
        self._counters[type_key] = self._counters.get(type_key, 0) + 1
        token = f"[{type_key}_{self._counters[type_key]}]"
        self._seen[value] = token
        return token


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
