"""Regression-тесты доверенности контекста восстановления — issue #43, ADR-34.

Проверяют, что удаление рабочего каталога возможно только для контекста,
доказавшего владение именно этим каталогом, и что гарантия cleanup не зависит
от доступности ключа. Все пути — временные, данные — только синтетика
(ADR-25). Реальный keyring не задействован.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway import context_trust as trust
from privacy_gateway.crypto import generate_key
from privacy_gateway.exceptions import RestoreError
from privacy_gateway.facade import (
    CONTEXT_FORMAT_VERSION,
    GatewayConfig,
    PrivacyGateway,
    RestoreContext,
)
from privacy_gateway.keystore import KeyNotFoundError

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"
SYNTH_TEXT = f"Свяжитесь: {SYNTH_EMAIL}, сервер {SYNTH_IP}\n"

_ENTITIES_CONFIG = Path("config.example") / "entities.yaml"
_FOREIGN_HANDLE = "ffffffffffffffffffffffffffffffff"


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


def _gateway(base: Path, *, keep_artifacts: bool = False) -> PrivacyGateway:
    base.mkdir(parents=True, exist_ok=True)
    return PrivacyGateway(
        GatewayConfig(
            entities_config_path=_ENTITIES_CONFIG,
            workspace_dir=base,
            keep_artifacts=keep_artifacts,
        )
    )


def _workspace_of(base: Path) -> Path:
    return next(path for path in base.iterdir() if path.is_dir())


def _victim(root: Path) -> Path:
    """Создать посторонний каталог, который не должен быть затронут."""
    victim = root / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep\n", encoding="utf-8")
    return victim


def _plant_marker(directory: Path, handle: str) -> str:
    """Подложить маркер владения: атакующий с правом записи в каталог."""
    secret = trust.new_workspace_secret()
    trust.write_owner_marker(directory, handle, secret)
    return secret


def _resigned(
    context: RestoreContext, secret: str, **changes: object
) -> RestoreContext:
    """Пересобрать контекст с изменёнными полями и действительной подписью."""
    updated = dataclasses.replace(context, **changes)  # type: ignore[arg-type]
    return dataclasses.replace(
        updated,
        _signature=trust.sign_payload(updated._payload(), secret),
    )


def _forge_token(context: RestoreContext, **changes: object) -> str:
    """Собрать токен с подменёнными полями и прежней подписью."""
    forged = dataclasses.replace(context, **changes)  # type: ignore[arg-type]
    return forged.to_token()


# ---------------------------------------------------------------------------
# Валидный путь
# ---------------------------------------------------------------------------


def test_valid_owned_context_is_discarded(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Валидный owned workspace удаляется, посторонние пути не затронуты."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    gateway.discard(prepared.context)

    assert not workspace.exists()
    assert (victim / "keep.txt").is_file()


def test_valid_token_round_trip_can_discard(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Контекст, прошедший сериализацию, сохраняет право на удаление."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    restored_context = RestoreContext.from_token(prepared.context.to_token())
    gateway.discard(restored_context)

    assert not workspace.exists()


def test_discard_works_without_keystore(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Освобождение ресурсов не зависит от доступности ключа (#43).

    Гарантия cleanup должна выполняться после удаления ключа и при
    заблокированном хранилище: иначе защищённые артефакты останутся на диске.
    """
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    with patch(
        "privacy_gateway.keystore.get_all_keys",
        side_effect=KeyNotFoundError("no key"),
    ):
        with patch(
            "privacy_gateway.keystore.get_key",
            side_effect=KeyNotFoundError("no key"),
        ):
            gateway.discard(prepared.context)

    assert not workspace.exists()


def test_repeated_discard_is_idempotent(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Повторное освобождение не поднимает ошибку и ничего не трогает."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)

    gateway.discard(prepared.context)
    gateway.discard(prepared.context)

    assert (victim / "keep.txt").is_file()


def test_keep_artifacts_preserves_workspace(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """При keep_artifacts артефакты остаются, ошибки нет."""
    base = tmp_path / "base"
    gateway = _gateway(base, keep_artifacts=True)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    gateway.discard(prepared.context)

    assert workspace.is_dir()


# ---------------------------------------------------------------------------
# Аутентичность токена
# ---------------------------------------------------------------------------


def test_tampered_path_in_token_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Подмена пути в сериализованном контексте не удаляет ничего."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    forged = RestoreContext.from_token(
        _forge_token(prepared.context, _workspace_dir=victim)
    )

    with pytest.raises(RestoreError):
        gateway.discard(forged)

    assert (victim / "keep.txt").is_file()
    assert workspace.is_dir()


def test_tampered_signature_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Изменённый код аутентичности не даёт права на удаление."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    forged = RestoreContext.from_token(
        _forge_token(prepared.context, _signature="0" * 64)
    )

    with pytest.raises(RestoreError):
        gateway.discard(forged)

    assert workspace.is_dir()


def test_missing_signature_is_rejected(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Контекст без кода аутентичности отклоняется при разборе токена."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    token = _forge_token(prepared.context, _signature=None)

    with pytest.raises(RestoreError):
        RestoreContext.from_token(token)

    assert workspace.is_dir()


def test_legacy_v1_token_is_rejected(tmp_path: Path) -> None:
    """Токен формата v0.4.0 не принимается: политика совместимости fail-closed."""
    import base64
    import json

    legacy = base64.urlsafe_b64encode(
        json.dumps(
            {
                "v": "1",
                "handle": "0" * 32,
                "route": str(tmp_path / "route.json"),
                "workspace": str(tmp_path),
                "owned": True,
                "correlation_id": None,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).decode("ascii")

    assert CONTEXT_FORMAT_VERSION != "1"
    with pytest.raises(RestoreError):
        RestoreContext.from_token(legacy)


def test_corrupted_serialization_is_public_error(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Повреждённая сериализация даёт публичную ошибку без удаления."""
    victim = _victim(tmp_path)

    with pytest.raises(RestoreError):
        RestoreContext.from_token("не токен!!")

    assert (victim / "keep.txt").is_file()


# ---------------------------------------------------------------------------
# Принадлежность и containment
# ---------------------------------------------------------------------------


def test_absolute_path_outside_base_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Подложенный маркер и верная подпись не выводят удаление за базу."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    secret = _plant_marker(victim, prepared.context._handle)

    forged = _resigned(prepared.context, secret, _workspace_dir=victim)

    with pytest.raises(RestoreError):
        gateway.discard(forged)

    assert (victim / "keep.txt").is_file()


def test_traversal_outside_base_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Компонент ``..`` не выводит удаление за доверенную базу."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    secret = _plant_marker(victim, prepared.context._handle)

    forged = _resigned(
        prepared.context,
        secret,
        _workspace_dir=base / ".." / "victim",
    )

    with pytest.raises(RestoreError):
        gateway.discard(forged)

    assert (victim / "keep.txt").is_file()


def test_sibling_prefix_directory_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Sibling внутри базы с чужим handle не удаляется."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    sibling = base / f"{workspace.name}-extra"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("keep\n", encoding="utf-8")
    secret = _plant_marker(sibling, _FOREIGN_HANDLE)

    forged = _resigned(prepared.context, secret, _workspace_dir=sibling)

    with pytest.raises(RestoreError):
        gateway.discard(forged)

    assert (sibling / "keep.txt").is_file()


def test_declared_base_must_match_configuration(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Доверенная база не берётся из токена: подмена базы отклоняется."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    secret = _plant_marker(victim, prepared.context._handle)

    forged = _resigned(
        prepared.context,
        secret,
        _workspace_dir=victim,
        _base_dir=tmp_path,
    )

    with pytest.raises(RestoreError):
        gateway.discard(forged)

    assert (victim / "keep.txt").is_file()


def test_foreign_ownership_scope_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Контекст другой области владения не удаляет её рабочий каталог."""
    base_a = tmp_path / "base-a"
    base_b = tmp_path / "base-b"
    gateway_a = _gateway(base_a)
    gateway_b = _gateway(base_b)
    prepared = gateway_a.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base_a)

    with pytest.raises(RestoreError):
        gateway_b.discard(prepared.context)

    assert workspace.is_dir()


def test_missing_owner_marker_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Каталог без маркера владения не удаляется."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    (workspace / trust.OWNER_MARKER_NAME).unlink()

    with pytest.raises(RestoreError):
        gateway.discard(prepared.context)

    assert workspace.is_dir()


def test_tampered_owner_marker_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Подделанный маркер владения не проходит проверку."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    marker = workspace / trust.OWNER_MARKER_NAME
    marker.write_text(
        f"{trust.MARKER_FORMAT_VERSION}\n{prepared.context._handle}\n"
        f"{trust.new_workspace_secret()}\n",
        encoding="ascii",
    )

    with pytest.raises(RestoreError):
        gateway.discard(prepared.context)

    assert workspace.is_dir()


def test_marker_with_foreign_handle_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Маркер с чужим handle не подтверждает владение."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    marker = workspace / trust.OWNER_MARKER_NAME
    marker.unlink()
    secret = _plant_marker(workspace, _FOREIGN_HANDLE)

    forged = _resigned(prepared.context, secret)

    with pytest.raises(RestoreError):
        gateway.discard(forged)

    assert workspace.is_dir()


# ---------------------------------------------------------------------------
# Ссылки и подмена между проверкой и удалением
# ---------------------------------------------------------------------------


def _symlinks_supported(root: Path) -> bool:
    probe = root / "probe-link"
    try:
        probe.symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def test_workspace_replaced_by_symlink_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Подмена рабочего каталога ссылкой не удаляет внешнюю цель."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    if not _symlinks_supported(tmp_path):
        pytest.skip("Файловая система или права не поддерживают ссылки.")

    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    marker_raw = (workspace / trust.OWNER_MARKER_NAME).read_text(
        encoding="ascii"
    )
    for child in sorted(workspace.iterdir()):
        child.unlink()
    workspace.rmdir()
    os.symlink(victim, workspace, target_is_directory=True)
    (victim / trust.OWNER_MARKER_NAME).write_text(marker_raw, encoding="ascii")

    with pytest.raises(RestoreError):
        gateway.discard(prepared.context)

    assert (victim / "keep.txt").is_file()
    assert workspace.is_symlink()


def test_swapped_path_component_refuses_removal(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Подмена каталога между проверкой и удалением приводит к отказу.

    Проверка детерминированная: подменяется не тайминг, а результат снятия
    идентичности каталога — управляемая граница файловой системы.
    """
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    with patch(
        "privacy_gateway.facade._trust.workspace_identity",
        side_effect=[(1, 1), (1, 2)],
    ):
        with pytest.raises(RestoreError):
            gateway.discard(prepared.context)

    assert workspace.is_dir()


# ---------------------------------------------------------------------------
# Гигиена сообщений
# ---------------------------------------------------------------------------


def test_trust_failure_message_leaks_nothing(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Отказ доверенности не раскрывает значения, секреты и пути."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    secret = _plant_marker(victim, prepared.context._handle)

    forged = _resigned(prepared.context, secret, _workspace_dir=victim)

    with pytest.raises(RestoreError) as exc_info:
        gateway.discard(forged)

    message = str(exc_info.value)
    assert SYNTH_EMAIL not in message
    assert SYNTH_IP not in message
    assert secret not in message
    assert str(victim) not in message
    assert str(base) not in message
