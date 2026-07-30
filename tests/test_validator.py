"""Тесты Э5: safety validator.

Все тестовые данные синтетические.
Реальные адреса, ключи и пароли не используются.
"""

from __future__ import annotations  # noqa: I001

from privacy_gateway.models import ProcessingStatus
from privacy_gateway.validator import ValidationResult, validate


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def _is_ok(result: ValidationResult) -> bool:
    return result.status == ProcessingStatus.OK


def _is_blocked(result: ValidationResult) -> bool:
    return result.status == ProcessingStatus.BLOCKED


# ---------------------------------------------------------------------------
# Негативная проверка
# ---------------------------------------------------------------------------


def test_residual_email_blocks() -> None:
    """Остаточный email после токенизации → BLOCKED."""
    result = validate("Отправьте письмо на user@example.com для подтверждения.")
    assert _is_blocked(result)
    assert result.negative_triggered


def test_residual_ip_blocks() -> None:
    """Остаточный IPv4 → BLOCKED."""
    result = validate("Сервер доступен по адресу 192.0.2.10 на порту 443.")
    assert _is_blocked(result)
    assert result.negative_triggered


def test_residual_ipv6_blocks() -> None:
    """Остаточный IPv6 → BLOCKED."""
    result = validate("IPv6-адрес хоста: 2001:db8::1 — не должен остаться.")
    assert _is_blocked(result)
    assert result.negative_triggered


def test_residual_phone_blocks() -> None:
    """Остаточный телефон → BLOCKED."""
    result = validate("Позвоните по номеру +7 900 000-00-00 для консультации.")
    assert _is_blocked(result)
    assert result.negative_triggered


def test_any_secret_blocks() -> None:
    """Секрет → BLOCKED безусловно."""
    result = validate("Настройки: password=SuperSecret123")  # pragma: allowlist secret
    assert _is_blocked(result)
    assert result.negative_triggered
    assert any(f.rule == "secret_keyword" for f in result.findings)


def test_high_entropy_string_blocks() -> None:
    """High-entropy строка, которую Э3 мог пропустить, ловится валидатором.

    Используем строку с энтропией >= 3.0 и длиной >= 16 символов.
    """
    high_entropy_str = "aB3xQ9mZpLwY7nKv2RtS5dUe"  # pragma: allowlist secret
    result = validate(f"Значение поля: {high_entropy_str}")
    assert _is_blocked(result)
    assert result.negative_triggered


# ---------------------------------------------------------------------------
# Позитивная проверка формата токенов
# ---------------------------------------------------------------------------


def test_fully_tokenized_text_is_ok() -> None:
    """Полностью токенизированный текст без остатков → OK."""
    result = validate(
        "Письмо от [PERSON_1] получено. Ответьте на [EMAIL_1] до [DATE_1]."
    )
    assert _is_ok(result)
    assert not result.negative_triggered
    assert not result.positive_triggered


def test_malformed_token_not_ok() -> None:
    """Искажённые токены не дают OK."""
    r1 = validate("Контакт: [EMAIL_]")
    assert not _is_ok(r1)

    r2 = validate("Контакт: [EMAIL 1]")
    assert not _is_ok(r2)

    r3 = validate("Контакт: [[EMAIL_1]]")
    assert not _is_ok(r3)


def test_unknown_token_type_not_ok() -> None:
    """[UNKNOWN_1] не даёт OK (PENDING или BLOCKED, но не OK)."""
    result = validate("Данные: [UNKNOWN_1]")
    assert not _is_ok(result)
    assert result.positive_triggered


# ---------------------------------------------------------------------------
# Граничные случаи
# ---------------------------------------------------------------------------


def test_empty_text() -> None:
    """Пустая строка → OK (нечего блокировать, нечего проверять).

    Семантика: пустой текст не содержит ни остатков, ни токенов.
    Статус OK; findings пустой.
    """
    result = validate("")
    assert _is_ok(result)
    assert result.findings == []


def test_whitespace_only_text() -> None:
    """Текст только из пробелов → OK."""
    result = validate("   \t\n  ")
    assert _is_ok(result)


def test_plain_text_without_entities_is_ok() -> None:
    """Обычный текст без PII и без токенов → OK."""
    result = validate("Сегодня хорошая погода и ничего чувствительного нет.")
    assert _is_ok(result)


# ---------------------------------------------------------------------------
# Независимость рубежей
# ---------------------------------------------------------------------------


def test_validator_catches_what_detector_misses() -> None:
    """Валидатор блокирует то, что детектор Э3 пропускает.

    Сценарий: высокоэнтропийная строка без ключевых слов. Детектор Э3
    не имеет правила для «случайно выглядящих» строк — он ищет
    конкретные паттерны. Строка «aB3xQ9mZpLwY7nKv2RtS5dUe» не попадает
    ни под один паттерн Э3, но валидатор Э5 ловит её по Shannon-энтропии.
    """
    from privacy_gateway.detector import DetectorConfig, detect_entities

    high_entropy_token = "aB3xQ9mZpLwY7nKv2RtS5dUe"  # pragma: allowlist secret
    text = f"Значение поля: {high_entropy_token}"

    config = DetectorConfig()
    detector_entities = detect_entities(text, config)
    assert not any(
        text[e.start:e.end] == high_entropy_token for e in detector_entities
    ), "Детектор Э3 неожиданно нашёл высокоэнтропийную строку — тест теряет смысл"

    result = validate(text)
    assert _is_blocked(result), (
        f"Валидатор не заблокировал высокоэнтропийную строку (статус={result.status})"
    )


# ---------------------------------------------------------------------------
# Безопасность отчёта
# ---------------------------------------------------------------------------


def test_findings_do_not_leak_values() -> None:
    """ValidationResult не содержит исходных значений в открытом виде."""
    sensitive_email = "user@example.com"
    sensitive_ip = "192.0.2.10"
    result = validate(
        f"Письмо: {sensitive_email}, сервер: {sensitive_ip}"
    )
    assert _is_blocked(result)
    for finding in result.findings:
        assert sensitive_email not in finding.masked, (
            f"Finding {finding.rule!r} раскрывает email: {finding.masked!r}"
        )
        assert sensitive_ip not in finding.masked, (
            f"Finding {finding.rule!r} раскрывает IP: {finding.masked!r}"
        )
        assert "@" not in finding.rule
        assert "." not in finding.rule or finding.rule in ("secret_keyword",)


# ---------------------------------------------------------------------------
# Интеграция с токенизатором
# ---------------------------------------------------------------------------


def test_tokenizer_output_passes_validator() -> None:
    """Нормальный выход tokenize() из Э4 проходит валидацию со статусом OK."""
    from privacy_gateway.detector import DetectorConfig, detect_entities
    from privacy_gateway.tokenizer import tokenize

    text = (
        "Письмо от Ивана Иванова на адрес user@example.com получено "
        "с хоста 192.0.2.10."
    )
    config = DetectorConfig(
        enabled_regex_types={"EMAIL", "HOST"},
    )
    entities = detect_entities(text, config)
    tokenized_text, _records = tokenize(text, entities)

    result = validate(tokenized_text)
    assert _is_ok(result), (
        f"Выход токенизатора не прошёл валидацию: "
        f"status={result.status}, findings={result.findings}"
    )
