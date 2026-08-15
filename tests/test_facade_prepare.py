"""Тесты библиотечной подготовки текста — issue #14.

Проверяют фасад ``PrivacyGateway.prepare`` поверх существующего конвейера:
успешный путь, fail-closed, трансляцию ошибок и управление артефактами.
Реальный keyring не задействован.

Данные — только синтетика.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway.crypto import generate_key
from privacy_gateway.exceptions import (
    ConfigurationError,
    DetectionError,
    KeyStoreError,
)
from privacy_gateway.facade import GatewayConfig, PrivacyGateway
from privacy_gateway.keystore import KeyNotFoundError

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"
SYNTH_PHONE = "+7 900 000-00-00"
SYNTH_TEXT = f"Свяжитесь: {SYNTH_EMAIL}, сервер {SYNTH_IP}\n"
SYNTH_MULTI = f"{SYNTH_EMAIL}, {SYNTH_IP}, {SYNTH_PHONE}\n"
SYNTH_UNICODE = f"Отчёт готов. Адрес: {SYNTH_EMAIL}\n"
SYNTH_SECRET = "password = hunter2hunter2\n"  # pragma: allowlist secret

_ENTITIES_CONFIG = Path("config.example") / "entities.yaml"


@pytest.fixture()
def fernet_key() -> bytes:
    return generate_key()


@pytest.fixture()
def mock_keyring(fernet_key: bytes) -> Iterator[bytes]:
    """Подменяет получение ключа без обращения к системному keyring."""
    with patch("privacy_gateway.keystore.get_key", return_value=fernet_key):
        yield fernet_key


def _gateway(workspace: Path | None = None, **kwargs: object) -> PrivacyGateway:
    config = GatewayConfig(
        entities_config_path=_ENTITIES_CONFIG,
        workspace_dir=workspace,
        **kwargs,  # type: ignore[arg-type]
    )
    return PrivacyGateway(config)


# --------------------------------------------------------------------------
# Успешный путь
# --------------------------------------------------------------------------


def test_prepare_protects_values(tmp_path: Path, mock_keyring: bytes) -> None:
    """Защищённый текст не содержит исходных значений."""
    prepared = _gateway(tmp_path).prepare(SYNTH_TEXT, correlation_id="corr-1")

    assert SYNTH_EMAIL not in prepared.text
    assert SYNTH_IP not in prepared.text
    assert prepared.token_count > 0
    assert prepared.correlation_id == "corr-1"


def test_prepare_handles_multiple_values(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Несколько защищаемых значений обрабатываются за один вызов."""
    prepared = _gateway(tmp_path).prepare(SYNTH_MULTI)

    assert SYNTH_EMAIL not in prepared.text
    assert SYNTH_IP not in prepared.text
    assert prepared.token_count >= 2


def test_prepare_preserves_unicode(tmp_path: Path, mock_keyring: bytes) -> None:
    """Кириллица вне защищаемых значений сохраняется без изменений."""
    prepared = _gateway(tmp_path).prepare(SYNTH_UNICODE)

    assert "Отчёт готов." in prepared.text
    assert SYNTH_EMAIL not in prepared.text


def test_prepare_isolates_operations(tmp_path: Path, mock_keyring: bytes) -> None:
    """Каждая подготовка получает собственный рабочий подкаталог."""
    gateway = _gateway(tmp_path)
    first = gateway.prepare(SYNTH_TEXT)
    second = gateway.prepare(SYNTH_TEXT)

    assert first.context != second.context
    assert len(list(tmp_path.iterdir())) == 2


def test_prepare_uses_temporary_workspace_by_default(
    mock_keyring: bytes,
) -> None:
    """Без явного каталога артефакты размещаются во временном каталоге."""
    gateway = _gateway()
    prepared = gateway.prepare(SYNTH_TEXT)
    try:
        assert SYNTH_EMAIL not in prepared.text
    finally:
        gateway.discard(prepared.context)


def test_prepare_does_not_expose_manifest(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Публичный результат не раскрывает содержимое защищённых артефактов."""
    prepared = _gateway(tmp_path).prepare(SYNTH_TEXT)
    rendered = repr(prepared) + repr(prepared.context)

    assert SYNTH_EMAIL not in rendered
    assert "manifest" not in rendered
    assert str(tmp_path) not in rendered


# --------------------------------------------------------------------------
# Fail-closed и ошибки
# --------------------------------------------------------------------------


def test_prepare_blocks_secret(tmp_path: Path, mock_keyring: bytes) -> None:
    """Обнаруженный секрет останавливает подготовку по правилу fail-closed."""
    with pytest.raises(DetectionError) as exc_info:
        _gateway(tmp_path).prepare(SYNTH_SECRET)

    assert exc_info.value.status == "BLOCKED"


def test_prepare_removes_artifacts_on_failure(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """При отказе рабочий подкаталог не остаётся на диске."""
    with pytest.raises(DetectionError):
        _gateway(tmp_path).prepare(SYNTH_SECRET)

    assert list(tmp_path.iterdir()) == []


def test_prepare_error_does_not_leak_values(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Сообщение об отказе не содержит значений исходного текста."""
    with pytest.raises(DetectionError) as exc_info:
        _gateway(tmp_path).prepare(SYNTH_SECRET)

    message = str(exc_info.value)
    assert "hunter2" not in message  # pragma: allowlist secret
    assert SYNTH_EMAIL not in message


def test_prepare_without_key_raises_public_error(tmp_path: Path) -> None:
    """Отсутствие ключа транслируется в публичную ошибку хранилища ключей."""
    with patch(
        "privacy_gateway.keystore.get_key",
        side_effect=KeyNotFoundError("no key"),
    ):
        with pytest.raises(KeyStoreError) as exc_info:
            _gateway(tmp_path).prepare(SYNTH_TEXT)

    assert isinstance(exc_info.value.__cause__, KeyNotFoundError)


def test_prepare_rejects_invalid_routing_config(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Некорректный конфиг маршрутизации даёт публичную ошибку конфигурации."""
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text("unknown_key: value\n", encoding="utf-8")
    config = GatewayConfig(
        routing_config_path=routing_path,
        entities_config_path=_ENTITIES_CONFIG,
        workspace_dir=tmp_path / "work",
    )

    with pytest.raises(ConfigurationError):
        PrivacyGateway(config).prepare(SYNTH_TEXT)


def test_prepare_rejects_empty_text(tmp_path: Path, mock_keyring: bytes) -> None:
    """Пустой ввод не даёт защищённого текста."""
    with pytest.raises(DetectionError):
        _gateway(tmp_path).prepare("   \n")


# --------------------------------------------------------------------------
# Управление артефактами
# --------------------------------------------------------------------------


def test_discard_removes_workspace(tmp_path: Path, mock_keyring: bytes) -> None:
    """Явное освобождение удаляет защищённые артефакты."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)

    gateway.discard(prepared.context)

    assert list(tmp_path.iterdir()) == []


def test_discard_is_idempotent(tmp_path: Path, mock_keyring: bytes) -> None:
    """Повторное освобождение не приводит к ошибке."""
    gateway = _gateway(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)

    gateway.discard(prepared.context)
    gateway.discard(prepared.context)

    assert list(tmp_path.iterdir()) == []


def test_keep_artifacts_preserves_workspace(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """При keep_artifacts артефакты остаются под управлением потребителя."""
    gateway = _gateway(tmp_path, keep_artifacts=True)
    prepared = gateway.prepare(SYNTH_TEXT)

    gateway.discard(prepared.context)

    assert list(tmp_path.iterdir()) != []
