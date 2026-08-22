"""Regression-тесты race-safe границы удаления — issue #43, дополнение ADR-34.

Проверяют именно то, что не покрывал PR #50: подмену после последней
проверки и непосредственно перед destructive operation. Подменяется граница
файловой системы, а не тайминг, поэтому тесты детерминированные.

Покрываются оба механизма: закрепление дескриптором (Linux) и отцепление
имени с повторной сверкой (путь для платформ без ``openat``-семантики).
Второй механизм проверяется и на Linux — принудительным переключением
``SUPPORTS_DIR_FD``, потому что Windows CI не может создавать symlink без
соответствующей привилегии.

Все пути временные, данные синтетические (ADR-25). Реальный keyring не
задействован.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway import context_trust as trust
from privacy_gateway.crypto import generate_key
from privacy_gateway.exceptions import RestoreError
from privacy_gateway.facade import GatewayConfig, PrivacyGateway

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"
SYNTH_TEXT = f"Свяжитесь: {SYNTH_EMAIL}, сервер {SYNTH_IP}\n"

_ENTITIES_CONFIG = Path("config.example") / "entities.yaml"


@pytest.fixture()
def mock_keyring() -> Iterator[bytes]:
    """Подменяет доступ к ключам в подготовке и восстановлении."""
    key = generate_key()
    with patch("privacy_gateway.keystore.get_key", return_value=key):
        with patch(
            "privacy_gateway.restore.get_all_keys", return_value=[key]
        ):
            yield key


def _gateway(base: Path) -> PrivacyGateway:
    base.mkdir(parents=True, exist_ok=True)
    return PrivacyGateway(
        GatewayConfig(
            entities_config_path=_ENTITIES_CONFIG,
            workspace_dir=base,
        )
    )


def _workspace_of(base: Path) -> Path:
    return next(
        path
        for path in base.iterdir()
        if path.is_dir() and not path.name.startswith(trust.QUARANTINE_PREFIX)
    )


def _victim(root: Path) -> Path:
    victim = root / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep\n", encoding="utf-8")
    return victim


def _symlinks_supported(root: Path) -> bool:
    probe = root / "probe-link"
    try:
        probe.symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def _quarantine_leftovers(base: Path) -> list[Path]:
    return [
        path
        for path in base.iterdir()
        if path.name.startswith(trust.QUARANTINE_PREFIX)
    ]


def _swap_original_name_before_removal(
    workspace: Path, victim: Path
) -> object:
    """Собрать обёртку rmtree, подставляющую ссылку под исходное имя.

    Момент вызова обёртки — после отцепления и последней сверки
    идентичности, то есть ровно на границе перед первым destructive syscall.
    Атакующий занимает освободившееся исходное имя ссылкой на victim.
    """
    real_rmtree = shutil.rmtree

    def wrapper(path: object, *args: object, **kwargs: object) -> None:
        if not workspace.exists() and not workspace.is_symlink():
            os.symlink(victim, workspace, target_is_directory=True)
        real_rmtree(path, *args, **kwargs)  # type: ignore[arg-type]

    return wrapper


# ---------------------------------------------------------------------------
# Подмена после последней проверки
# ---------------------------------------------------------------------------


def test_name_swapped_before_removal_does_not_touch_victim(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Подмена исходного имени перед удалением не затрагивает victim.

    Доказывает, что destructive operation опирается на отцепленную
    проверенную сущность, а не на заново разрешаемый атакуемый pathname.
    """
    base = tmp_path / "base"
    gateway = _gateway(base)
    if not _symlinks_supported(tmp_path):
        pytest.skip("Файловая система или права не поддерживают ссылки.")
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    with patch(
        "shutil.rmtree",
        side_effect=_swap_original_name_before_removal(workspace, victim),
    ):
        gateway.discard(prepared.context)

    assert (victim / "keep.txt").is_file()
    assert victim.is_dir()
    assert workspace.is_symlink()
    assert _quarantine_leftovers(base) == []

    workspace.unlink()


def test_name_swap_before_removal_without_dir_fd(
    tmp_path: Path, mock_keyring: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """То же на пути без descriptor-relative семантики (ветка Windows)."""
    monkeypatch.setattr(trust, "SUPPORTS_DIR_FD", False)
    base = tmp_path / "base"
    gateway = _gateway(base)
    if not _symlinks_supported(tmp_path):
        pytest.skip("Файловая система или права не поддерживают ссылки.")
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    with patch(
        "shutil.rmtree",
        side_effect=_swap_original_name_before_removal(workspace, victim),
    ):
        gateway.discard(prepared.context)

    assert (victim / "keep.txt").is_file()
    assert workspace.is_symlink()
    assert _quarantine_leftovers(base) == []

    workspace.unlink()


def test_identity_mismatch_after_detach_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Расхождение идентичности после отцепления не удаляет ничего.

    Моделирует подмену самого объекта на границе отцепления: проверенная
    сущность и объект под карантинным именем различаются.
    """
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    marker = workspace / trust.OWNER_MARKER_NAME

    with patch(
        "privacy_gateway.context_trust.path_identity",
        return_value=(0, 0),
    ):
        with pytest.raises(RestoreError):
            gateway.discard(prepared.context)

    assert (victim / "keep.txt").is_file()
    assert workspace.is_dir()
    assert marker.is_file()
    assert _quarantine_leftovers(base) == []


def test_identity_mismatch_without_dir_fd_fails_closed(
    tmp_path: Path, mock_keyring: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ветка без dir_fd тоже отказывает и возвращает исходное имя."""
    monkeypatch.setattr(trust, "SUPPORTS_DIR_FD", False)
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    identities = iter([trust.path_identity(workspace), (0, 0)])

    with patch(
        "privacy_gateway.context_trust.path_identity",
        side_effect=lambda *args, **kwargs: next(identities),
    ):
        with pytest.raises(RestoreError):
            gateway.discard(prepared.context)

    assert (victim / "keep.txt").is_file()
    assert workspace.is_dir()
    assert (workspace / trust.OWNER_MARKER_NAME).is_file()
    assert _quarantine_leftovers(base) == []


def test_marker_secret_must_match_pinned_directory(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Секрет из закреплённого каталога обязан совпадать с секретом MAC.

    Аутентичность токена привязана к закреплённой сущности: подмена
    содержимого маркера между path-проверкой и границей удаления отказывает.
    """
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    with patch(
        "privacy_gateway.context_trust.read_owner_secret_fd",
        return_value=trust.new_workspace_secret(),
    ):
        with patch(
            "privacy_gateway.context_trust.read_owner_secret",
            wraps=trust.read_owner_secret,
        ):
            with pytest.raises(RestoreError):
                gateway.discard(prepared.context)

    assert workspace.is_dir()
    assert _quarantine_leftovers(base) == []


# ---------------------------------------------------------------------------
# Валидный путь и гигиена сообщений
# ---------------------------------------------------------------------------


def test_valid_workspace_still_removed_on_both_paths(
    tmp_path: Path, mock_keyring: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Новая граница не ослабляет уборку валидного owned workspace."""
    for pinned in (True, False):
        base = tmp_path / f"base-{int(pinned)}"
        monkeypatch.setattr(
            trust, "SUPPORTS_DIR_FD", trust.SUPPORTS_DIR_FD and pinned
        )
        gateway = _gateway(base)
        prepared = gateway.prepare(SYNTH_TEXT)
        workspace = _workspace_of(base)

        gateway.discard(prepared.context)

        assert not workspace.exists()
        assert _quarantine_leftovers(base) == []


def test_boundary_failure_message_leaks_nothing(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Отказ на границе удаления не раскрывает пути, секреты и значения."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    secret = trust.read_owner_secret(workspace, prepared.context._handle)
    assert secret is not None

    with patch(
        "privacy_gateway.context_trust.path_identity",
        return_value=(0, 0),
    ):
        with pytest.raises(RestoreError) as exc_info:
            gateway.discard(prepared.context)

    message = str(exc_info.value)
    assert SYNTH_EMAIL not in message
    assert SYNTH_IP not in message
    assert secret not in message
    assert str(workspace) not in message
    assert str(base) not in message
