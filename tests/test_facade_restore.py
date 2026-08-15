"""Тесты библиотечного восстановления текста — issue #14.

Проверяют ``PrivacyGateway.restore``: round-trip, строгий режим, проверку
целостности до возврата открытого текста и трансляцию внутренних ошибок
в публичные. Реальный keyring не задействован.

Данные — только синтетика.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway.crypto import generate_key
from privacy_gateway.exceptions import (
    IntegrityError,
    KeyStoreError,
    PrivacyGatewayError,
    RestoreError,
    StrictTokenError,
)
from privacy_gateway.facade import (
    GatewayConfig,
    PreparedPayload,
    PrivacyGateway,
)
from privacy_gateway.keystore import KeyNotFoundError

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"
SYNTH_PHONE = "+7 900 000-00-00"
SYNTH_TEXT = f"Свяжитесь: {SYNTH_EMAIL}, сервер {SYNTH_IP}\n"
SYNTH_MULTI = f"{SYNTH_EMAIL}, {SYNTH_IP}, {SYNTH_PHONE}\n"
SYNTH_UNICODE = f"Отчёт готов. Адрес: {SYNTH_EMAIL}\n"

_ENTITIES_CONFIG = Path("config.example") / "entities.yaml"


@pytest.fixture()
def fernet_key() -> bytes:
    return generate_key()


@pytest.fixture()
def mock_keyring(fernet_key: bytes) -> Iterator[bytes]:
    """Подменяет доступ к ключам в подготовке и восстановлении."""
    with patch("privacy_gateway.keystore.get_key", return_value=fernet_key):
        with patch(
            "privacy_gateway.restore.get_all_keys",
            return_value=[fernet_key],
        ):
            yield fernet_key


def _gateway(workspace: Path, *, strict: bool = True) -> PrivacyGateway:
    return PrivacyGateway(
        GatewayConfig(
            entities_config_path=_ENTITIES_CONFIG,
            workspace_dir=workspace,
            strict=strict,
        )
    )


def _workspace_of(base: Path) -> Path:
    return next(p for p in base.iterdir() if p.is_dir())


def _round_trip(
    gateway: PrivacyGateway, text: str
) -> tuple[PreparedPayload, str]:
    prepared = gateway.prepare(text)
    return prepared, prepared.text


# --------------------------------------------------------------------------
# Round-trip
# --------------------------------------------------------------------------


def test_round_trip_returns_original(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Неизменённый ответ обработчика восстанавливается точно."""
    gateway = _gateway(tmp_path)
    prepared, response = _round_trip(gateway, SYNTH_TEXT)

    restored = gateway.restore(response, context=prepared.context)

    assert restored.text == SYNTH_TEXT
    assert restored.tokens_restored > 0
    assert restored.tokens_missing == 0


def test_round_trip_unicode(tmp_path: Path, mock_keyring: bytes) -> None:
    """Round-trip сохраняет кириллицу побайтово."""
    gateway = _gateway(tmp_path)
    prepared, response = _round_trip(gateway, SYNTH_UNICODE)

    restored = gateway.restore(response, context=prepared.context)

    assert restored.text == SYNTH_UNICODE


def test_round_trip_multiple_values(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Несколько защищённых значений восстанавливаются одновременно."""
    gateway = _gateway(tmp_path)
    prepared, response = _round_trip(gateway, SYNTH_MULTI)

    restored = gateway.restore(response, context=prepared.context)

    assert restored.text == SYNTH_MULTI
    assert restored.tokens_restored >= 2


def test_correlation_id_propagates(tmp_path: Path, mock_keyring: bytes) -> None:
    """Идентификатор операции переносится из контекста и переопределяется."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT, correlation_id="corr-1")

    inherited = gateway.restore(prepared.text, context=prepared.context)
    overridden = gateway.restore(
        prepared.text, context=prepared.context, correlation_id="corr-2"
    )

    assert inherited.correlation_id == "corr-1"
    assert overridden.correlation_id == "corr-2"


def test_context_is_reusable(tmp_path: Path, mock_keyring: bytes) -> None:
    """Контекст пригоден для повторного восстановления."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)

    first = gateway.restore(prepared.text, context=prepared.context)
    second = gateway.restore(prepared.text, context=prepared.context)

    assert first.text == second.text


def test_missing_token_is_not_an_error(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Отсутствующий во внешнем ответе токен фиксируется счётчиком."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    truncated = "Ответ без токенов.\n"

    restored = gateway.restore(truncated, context=prepared.context)

    assert restored.tokens_missing > 0
    assert restored.tokens_restored == 0


def test_restored_payload_repr_hides_values(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """repr результата не раскрывает восстановленные значения."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)

    restored = gateway.restore(prepared.text, context=prepared.context)

    assert SYNTH_EMAIL not in repr(restored)
    assert SYNTH_IP not in repr(restored)


# --------------------------------------------------------------------------
# Строгий режим
# --------------------------------------------------------------------------


def test_unknown_token_is_strict_failure(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Неизвестный токен во внешнем ответе даёт строгий отказ."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)

    with pytest.raises(StrictTokenError) as exc_info:
        gateway.restore(prepared.text + " [EMAIL_99]", context=prepared.context)

    assert isinstance(exc_info.value, RestoreError)
    assert isinstance(exc_info.value, PrivacyGatewayError)


def test_malformed_token_is_strict_failure(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Искажённый токен во внешнем ответе даёт строгий отказ."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)

    with pytest.raises(StrictTokenError):
        gateway.restore(prepared.text + " [email_1]", context=prepared.context)


def test_lenient_mode_requires_explicit_config(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Мягкий режим включается только явной конфигурацией."""
    strict_gateway = _gateway(tmp_path / "strict")
    prepared = strict_gateway.prepare(SYNTH_TEXT)
    response = prepared.text + " [EMAIL_99]"

    with pytest.raises(StrictTokenError):
        strict_gateway.restore(response, context=prepared.context)

    lenient_gateway = _gateway(tmp_path / "strict", strict=False)
    restored = lenient_gateway.restore(response, context=prepared.context)

    assert "[EMAIL_99]" in restored.text


# --------------------------------------------------------------------------
# Целостность и ключи
# --------------------------------------------------------------------------


def test_tampered_artifacts_raise_integrity_error(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Повреждение защищённых артефактов обнаруживается до возврата текста."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    manifest = _workspace_of(tmp_path) / "manifest.json"
    with manifest.open("ab") as handle:
        handle.write(b" ")

    with pytest.raises(IntegrityError):
        gateway.restore(prepared.text, context=prepared.context)


def test_foreign_key_raises_integrity_error(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Расшифровка чужим ключом не возвращает открытый текст."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)

    with patch(
        "privacy_gateway.restore.get_all_keys",
        return_value=[generate_key()],
    ):
        with pytest.raises(IntegrityError) as exc_info:
            gateway.restore(prepared.text, context=prepared.context)

    assert SYNTH_EMAIL not in str(exc_info.value)


def test_missing_key_raises_keystore_error(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Отсутствие ключа при восстановлении даёт публичную ошибку хранилища."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)

    with patch(
        "privacy_gateway.restore.get_all_keys",
        side_effect=KeyNotFoundError("no key"),
    ):
        with pytest.raises(KeyStoreError):
            gateway.restore(prepared.text, context=prepared.context)


def test_discarded_context_is_invalid(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """После освобождения ресурсов контекст перестаёт быть действительным."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    gateway.discard(prepared.context)

    with pytest.raises(RestoreError):
        gateway.restore(prepared.text, context=prepared.context)


def test_failure_does_not_return_plaintext(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Ни одно сообщение об отказе не содержит исходных значений."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)

    with pytest.raises(StrictTokenError) as exc_info:
        gateway.restore(prepared.text + " [EMAIL_99]", context=prepared.context)

    message = str(exc_info.value)
    assert SYNTH_EMAIL not in message
    assert SYNTH_IP not in message
