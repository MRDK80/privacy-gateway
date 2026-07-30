"""Safety validator — Этап Э5.

Валидатор проверяет **выход** токенизатора, а не вход.
Его задача — доказать, что в подготовленном тексте не осталось
ничего чувствительного, то есть найти то, что детектор Э3 пропустил.

Архитектурное решение (зафиксированно намеренно):
    Валидатор реализован как независимый строгий набор правил.
    Импортировать `detector.py` или переиспользовать его конфигурацию
    ЗАПРЕЩЕНО. Дублирование паттернов здесь — сознательная плата
    за независимость рубежей:
    - детектор Э3: ищет сущности для замены, фильтры ложных срабатываний
      включены, порог энтропии сбалансированный;
    - валидатор Э5: доказывает отсутствие остатков, фильтры выключены,
      порог энтропии заведомо ниже, поведение при сомнении — BLOCKED.

Публичный API:
    validate(text: str) -> ValidationResult
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal

from privacy_gateway.models import EntityType, ProcessingStatus

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Порог Шеннона для высокоэнтропийных строк.
# Детектор Э3 использует ~3.5; валидатор намеренно ниже — 3.0.
_ENTROPY_THRESHOLD: float = 3.0
# Минимальная длина строки для проверки энтропии.
_ENTROPY_MIN_LEN: int = 16

# ---------------------------------------------------------------------------
# Паттерны негативной проверки (независимы от detector.py)
# ---------------------------------------------------------------------------

# EMAIL
_RE_EMAIL = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# IPv4
_RE_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)

# IPv6 — полная и сокращённая формы
_RE_IPV6 = re.compile(
    r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b"
    r"|\b(?:[0-9a-fA-F]{1,4}:){1,7}:\b"
    r"|\b:(?::[0-9a-fA-F]{1,4}){1,7}\b"
    r"|\b::(?:[fF]{4}(?::0{1,4})?:)?"
    r"(?:(?:25[0-5]|(?:2[0-4]|1?\d)?\d)\.){3}"
    r"(?:25[0-5]|(?:2[0-4]|1?\d)?\d)\b"
    r"|\b::\b",
    re.IGNORECASE,
)

# PHONE — российские и международные форматы
_RE_PHONE = re.compile(
    r"(?:"
    r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    r"|\+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{2,4}[\s\-]?\d{2,4}(?:[\s\-]?\d{2,4})?"
    r")"
)

# Ключевые слова секретов с присвоением значения
_RE_SECRET_KW = re.compile(
    r"(?:password|passwd|пароль|token|api[_\-]?key|apikey"
    r"|secret|private[_\-]?key|client[_\-]?secret|access[_\-]?key)"
    r"\s*[:=]\s*\S+",
    re.IGNORECASE,
)

# BEGIN PRIVATE KEY блоки (паттерн для поиска в чужом тексте, не секрет)
_RE_PEM = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE)  # pragma: allowlist secret

_NEGATIVE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("email", _RE_EMAIL),
    ("ipv4", _RE_IPV4),
    ("ipv6", _RE_IPV6),
    ("phone", _RE_PHONE),
    ("secret_keyword", _RE_SECRET_KW),
    ("pem_key", _RE_PEM),
]

# ---------------------------------------------------------------------------
# Паттерны позитивной проверки токенов
# ---------------------------------------------------------------------------

# Корректный токен: [ENTITYTYPE_N], где ENTITYTYPE — значение EntityType, N — цифры
_VALID_ENTITY_TYPES: frozenset[str] = frozenset(e.value for e in EntityType)

# Любая последовательность вида [...] — потенциальный токен или его фрагмент
_RE_BRACKET_SEQUENCE = re.compile(r"\[[^\[\]]*\]")

# Вложенные или обрывки скобок
_RE_UNCLOSED_BRACKET = re.compile(r"\[[^\[\]]*$|^[^\[\]]*\]")
_RE_NESTED_BRACKET = re.compile(r"\[\[[^\]]*\]|\[[^\]]*\]\]")


def _is_valid_token(token_text: str) -> bool:
    """Вернуть True, если строка — корректный токен вида [TYPE_N]."""
    if not (token_text.startswith("[") and token_text.endswith("]")):
        return False
    inner = token_text[1:-1]
    parts = inner.rsplit("_", 1)
    if len(parts) != 2:
        return False
    type_part, num_part = parts
    if not num_part.isdigit():
        return False
    return type_part in _VALID_ENTITY_TYPES


# ---------------------------------------------------------------------------
# Энтропия Шеннона
# ---------------------------------------------------------------------------

def _shannon_entropy(s: str) -> float:
    """Вычислить энтропию Шеннона строки (бит на символ)."""
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return float(-sum((c / n) * math.log2(c / n) for c in freq.values()))


def _find_high_entropy_tokens(text: str) -> list[tuple[int, int]]:
    """Найти высокоэнтропийные слова/токены (не являющиеся корректными токенами).

    Возвращает список (start, end) позиций.
    """
    findings: list[tuple[int, int]] = []
    for m in re.finditer(r"[^\s,;.!?\"'`]+", text):
        word = m.group()
        if _is_valid_token(word):
            continue
        long_enough = len(word) >= _ENTROPY_MIN_LEN
        high_entropy = _shannon_entropy(word) >= _ENTROPY_THRESHOLD
        if long_enough and high_entropy:
            findings.append((m.start(), m.end()))
    return findings


# ---------------------------------------------------------------------------
# Модели результата
# ---------------------------------------------------------------------------

@dataclass
class ValidationFinding:
    """Одна находка валидатора.

    Исходное значение НЕ сохраняется — только тип, позиция, длина
    и опциональный маскированный фрагмент первых/последних 2 символов.
    """

    kind: Literal["negative", "positive"]
    rule: str  # имя правила, например "email", "malformed_token"
    start: int
    length: int
    masked: str  # усечённый маскированный фрагмент, например "us****.com"


@dataclass
class ValidationResult:
    """Результат валидации текста.

    status:
        ProcessingStatus.OK      — текст безопасен для отправки.
        ProcessingStatus.BLOCKED — текст содержит остаточные PII/секреты
                                   или некорректные токены; отправлять нельзя.
        ProcessingStatus.PENDING — обнаружены аномалии, требующие решения
                                   человека (например, неизвестный тип токена);
                                   автоматически отправлять нельзя.

    negative_triggered:
        True, если сработала хотя бы одна негативная проверка (остатки PII).
    positive_triggered:
        True, если сработала хотя бы одна позитивная проверка формата.
    findings:
        Список находок; не содержит исходных значений в открытом виде.
    """

    status: ProcessingStatus
    negative_triggered: bool
    positive_triggered: bool
    findings: list[ValidationFinding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def validate(text: str) -> ValidationResult:  # noqa: C901
    """Проверить токенизированный текст на отсутствие чувствительных остатков.

    Выполняет два независимых прохода:
    1. Негативная проверка — поиск остатков PII/секретов.
    2. Позитивная проверка — контроль формата токенов.

    Семантика статусов:
        OK      — обе проверки не выявили нарушений.
        BLOCKED — обнаружены остаточные PII/секреты (негативная)
                или явно искажённые токены (позитивная).
        PENDING — обнаружен токен с неизвестным типом сущности;
                автоматическая отправка невозможна, нужно ручное решение.

    При любой неоднозначности статус не может быть OK (fail closed).
    PENDING не является «мягким OK» — отправка при PENDING также запрещена.

    Args:
        text: Токенизированный текст (выход tokenizer.tokenize).

    Returns:
        ValidationResult с итоговым статусом и списком находок.
    """
    findings: list[ValidationFinding] = []
    negative_triggered = False
    positive_triggered = False
    # секрет → безусловный BLOCKED
    has_secret = False
    # неизвестный тип токена → PENDING (если нет ничего хуже)
    has_unknown_token = False
    # искажённый токен → BLOCKED
    has_malformed_token = False

    # ------------------------------------------------------------------
    # 1. Негативная проверка
    # ------------------------------------------------------------------
    for rule_name, pattern in _NEGATIVE_RULES:
        for m in pattern.finditer(text):
            negative_triggered = True
            val = m.group()
            # Маскируем: оставляем первые 2 и последние 2 символа
            if len(val) > 6:
                masked = val[:2] + "*" * (len(val) - 4) + val[-2:]
            else:
                masked = "*" * len(val)
            findings.append(ValidationFinding(
                kind="negative",
                rule=rule_name,
                start=m.start(),
                length=len(val),
                masked=masked,
            ))
            if rule_name in ("secret_keyword", "pem_key"):
                has_secret = True

    # Высокоэнтропийные строки
    for start, end in _find_high_entropy_tokens(text):
        negative_triggered = True
        val = text[start:end]
        if len(val) > 6:
            masked = val[:2] + "*" * (len(val) - 4) + val[-2:]
        else:
            masked = "*" * len(val)
        findings.append(ValidationFinding(
            kind="negative",
            rule="high_entropy",
            start=start,
            length=end - start,
            masked=masked,
        ))

    # ------------------------------------------------------------------
    # 2. Позитивная проверка формата токенов
    # ------------------------------------------------------------------
    # Вложенные скобки
    for m in _RE_NESTED_BRACKET.finditer(text):
        positive_triggered = True
        has_malformed_token = True
        findings.append(ValidationFinding(
            kind="positive",
            rule="nested_brackets",
            start=m.start(),
            length=len(m.group()),
            masked="[nested]",
        ))

    # Все [...] последовательности
    for m in _RE_BRACKET_SEQUENCE.finditer(text):
        token_text = m.group()
        if _is_valid_token(token_text):
            continue  # корректный токен — ОК
        positive_triggered = True
        inner = token_text[1:-1]
        parts = inner.rsplit("_", 1)
        known_type = (
            len(parts) == 2
            and parts[0] in _VALID_ENTITY_TYPES
        )
        if known_type:
            has_malformed_token = True
            rule = "malformed_token"
        else:
            has_unknown_token = True
            rule = "unknown_token_type"
        findings.append(ValidationFinding(
            kind="positive",
            rule=rule,
            start=m.start(),
            length=len(token_text),
            masked=token_text[:4] + "..." if len(token_text) > 4 else token_text,
        ))

    # Обрывки токенов (незакрытые [ или висячие ])
    depth = 0
    for i, ch in enumerate(text):
        if ch == "[":
            depth += 1
        elif ch == "]":
            if depth == 0:
                positive_triggered = True
                has_malformed_token = True
                findings.append(ValidationFinding(
                    kind="positive",
                    rule="dangling_bracket",
                    start=i,
                    length=1,
                    masked="]",
                ))
            else:
                depth -= 1
    if depth > 0:
        positive_triggered = True
        has_malformed_token = True
        findings.append(ValidationFinding(
            kind="positive",
            rule="unclosed_bracket",
            start=text.rfind("["),
            length=1,
            masked="[",
        ))

    # ------------------------------------------------------------------
    # 3. Определение итогового статуса (fail closed)
    # ------------------------------------------------------------------
    if has_secret or negative_triggered or has_malformed_token:
        status = ProcessingStatus.BLOCKED
    elif has_unknown_token:
        status = ProcessingStatus.PENDING
    else:
        status = ProcessingStatus.OK

    return ValidationResult(
        status=status,
        negative_triggered=negative_triggered,
        positive_triggered=positive_triggered,
        findings=findings,
    )
