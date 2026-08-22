"""Тесты публичного контракта библиотечного API Privacy Gateway (issue #14).

Проверяют только контракт: иерархию публичных исключений, непрозрачность
контекста восстановления и отсутствие чувствительных данных в repr моделей.
Хранилище ключей, конвейер подготовки и CLI здесь не задействованы.

Данные — только синтетика.
"""

from __future__ import annotations

import base64
import dataclasses
import json
from pathlib import Path

import pytest

from privacy_gateway.exceptions import (
    ConfigurationError,
    DetectionError,
    IntegrityError,
    KeyStoreError,
    PrivacyGatewayError,
    RestoreError,
    StrictTokenError,
)
from privacy_gateway.facade import (
    CONTEXT_FORMAT_VERSION,
    GatewayConfig,
    PreparedPayload,
    RestoreContext,
    RestoredPayload,
)

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"
SYNTH_TEXT = f"Связь: {SYNTH_EMAIL}, {SYNTH_IP}"

_PUBLIC_ERRORS = [
    ConfigurationError,
    DetectionError,
    IntegrityError,
    KeyStoreError,
    RestoreError,
    StrictTokenError,
]


def _make_context(tmp_path: Path) -> RestoreContext:
    return RestoreContext(
        _handle="0123456789abcdef",
        _route_path=tmp_path / "route.json",
        _workspace_dir=tmp_path,
        _base_dir=tmp_path,
        _owned_workspace=True,
        _correlation_id="corr-1",
        _signature="0" * 64,
    )


def _encode(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


# --------------------------------------------------------------------------
# Публичные исключения
# --------------------------------------------------------------------------


@pytest.mark.parametrize("error_cls", _PUBLIC_ERRORS)
def test_public_errors_share_base_category(
    error_cls: type[PrivacyGatewayError],
) -> None:
    """Любую публичную ошибку можно поймать одной базовой категорией."""
    with pytest.raises(PrivacyGatewayError):
        raise error_cls("synthetic failure")


def test_strict_token_error_is_restore_error() -> None:
    """Строгий отказ по токенам обрабатывается как невозможность восстановления."""
    assert issubclass(StrictTokenError, RestoreError)
    with pytest.raises(RestoreError):
        raise StrictTokenError("synthetic strict failure")


def test_detection_error_keeps_neutral_status() -> None:
    """DetectionError несёт нейтральный признак решения без значений."""
    error = DetectionError("preparation stopped", status="BLOCKED")
    assert error.status == "BLOCKED"
    assert SYNTH_EMAIL not in str(error)


def test_detection_error_status_is_optional() -> None:
    """Признак решения не обязателен."""
    assert DetectionError("preparation stopped").status is None


def test_public_error_preserves_cause() -> None:
    """Исходная причина сохраняется через exception chaining."""
    original = ValueError("internal detail")
    with pytest.raises(IntegrityError) as exc_info:
        try:
            raise original
        except ValueError as exc:
            raise IntegrityError("integrity check failed") from exc
    assert exc_info.value.__cause__ is original


# --------------------------------------------------------------------------
# Непрозрачность контекста
# --------------------------------------------------------------------------


def test_context_repr_is_opaque(tmp_path: Path) -> None:
    """repr и str контекста не раскрывают его содержимое."""
    context = _make_context(tmp_path)
    assert repr(context) == "RestoreContext(<opaque>)"
    assert str(context) == "RestoreContext(<opaque>)"
    assert str(tmp_path) not in repr(context)
    assert "corr-1" not in repr(context)


def test_context_has_no_public_fields(tmp_path: Path) -> None:
    """У контекста нет публичных полей с данными."""
    field_names = [f.name for f in dataclasses.fields(_make_context(tmp_path))]
    assert field_names
    assert all(name.startswith("_") for name in field_names)


def test_context_roundtrip(tmp_path: Path) -> None:
    """Контекст переживает сериализацию и восстанавливается без потерь."""
    context = _make_context(tmp_path)
    assert RestoreContext.from_token(context.to_token()) == context


def test_context_token_has_no_payload(tmp_path: Path) -> None:
    """Токен контекста не содержит текста и значений."""
    token = _make_context(tmp_path).to_token()
    decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    assert SYNTH_EMAIL not in decoded
    assert SYNTH_IP not in decoded
    assert SYNTH_TEXT not in decoded


def test_from_token_rejects_garbage() -> None:
    """Повреждённый токен приводит к публичной ошибке восстановления."""
    with pytest.raises(RestoreError):
        RestoreContext.from_token("not-a-valid-token")


def test_from_token_rejects_unknown_version(tmp_path: Path) -> None:
    """Неизвестная версия формата контекста отклоняется."""
    token = _encode(
        {
            "v": "999",
            "handle": "0123456789abcdef",
            "route": str(tmp_path / "route.json"),
            "workspace": str(tmp_path),
            "owned": True,
            "correlation_id": None,
        }
    )
    with pytest.raises(RestoreError):
        RestoreContext.from_token(token)


def test_from_token_rejects_incomplete(tmp_path: Path) -> None:
    """Неполный токен отклоняется без частичного восстановления."""
    token = _encode(
        {
            "v": CONTEXT_FORMAT_VERSION,
            "handle": "0123456789abcdef",
            "workspace": str(tmp_path),
        }
    )
    with pytest.raises(RestoreError):
        RestoreContext.from_token(token)


# --------------------------------------------------------------------------
# Публичные модели
# --------------------------------------------------------------------------


def test_prepared_payload_repr_hides_text(tmp_path: Path) -> None:
    """repr подготовленного результата не раскрывает защищённый текст."""
    payload = PreparedPayload(
        text="Связь: [EMAIL_1], [HOST_1]",
        context=_make_context(tmp_path),
        correlation_id="corr-1",
        token_count=2,
    )
    rendered = repr(payload)
    assert "[EMAIL_1]" not in rendered
    assert "corr-1" not in rendered
    assert "token_count=2" in rendered
    assert "context=<opaque>" in rendered


def test_restored_payload_repr_hides_text() -> None:
    """repr восстановленного результата не раскрывает исходные значения."""
    payload = RestoredPayload(
        text=SYNTH_TEXT,
        correlation_id="corr-1",
        tokens_restored=2,
        tokens_missing=0,
    )
    rendered = repr(payload)
    assert SYNTH_EMAIL not in rendered
    assert SYNTH_IP not in rendered
    assert "corr-1" not in rendered
    assert "tokens_restored=2" in rendered


def test_models_are_immutable(tmp_path: Path) -> None:
    """Публичные модели неизменяемы."""
    payload = RestoredPayload(text=SYNTH_TEXT)
    with pytest.raises(dataclasses.FrozenInstanceError):
        payload.text = "changed"  # type: ignore[misc]

    context = _make_context(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        context._handle = "changed"  # type: ignore[misc]


def test_gateway_config_defaults_are_fail_closed() -> None:
    """По умолчанию конфигурация строгая и не сохраняет артефакты."""
    config = GatewayConfig()
    assert config.strict is True
    assert config.keep_artifacts is False
    assert config.workspace_dir is None
    assert config.routing_config_path is None
    assert config.entities_config_path is None
