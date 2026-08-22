"""Regression-тесты границы удаления — issue #52, дополнение ADR-34.

Проверяют главное утверждение #52: рекурсивное удаление адресует
подтверждённую сущность файловой системы, а не имя. Ключевой сценарий —
подмена карантинного имени настоящим каталогом после последней проверки и
непосредственно перед первым destructive-вызовом.

Механизмы различаются по платформам, поэтому и тесты различаются:

* descriptor-relative ветвь (Linux и подобные) — точка вмешательства
  ``os.scandir`` относительно удерживаемого дескриптора;
* handle-ветвь (Windows) — точка вмешательства ``_win_children``.

Ветвь, недоступная на текущей платформе, помечается ``skipif`` и реально
исполняется в exact CI на соответствующем раннере: Windows-проверка не
скрывается полностью, как требует #52.

Остаточное поведение после успешного удаления различается по платформам и
задокументировано в дополнении ADR-34: на Linux финальный ``rmdir`` называет
имя только для проверки идентичности карантинного имени, а сама проверенная
сущность (уже под другим именем после атаки) никогда не адресуется по имени
и остаётся пустым каталогом. На Windows диспозиция ставится на handle,
который идентифицирует объект по identity, а не по текущему имени, поэтому
сущность удаляется целиком независимо от того, как она называется в момент
удаления. Обе гарантии укладываются в требование #52: подтверждённая
сущность не остаётся неудалённой, а неподтверждённая — не удаляется.

Инвариант «нет закрепления — нет удаления» проверяется отдельно и на любой
платформе: ``discard`` обязан завершиться fail closed и не вызывать
path-based рекурсивное удаление.

Все пути временные, данные синтетические (ADR-25). Реальный keyring не
задействован.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from collections.abc import Callable
from unittest.mock import patch

import pytest

from privacy_gateway import context_trust as trust
from privacy_gateway import keystore as _keystore
from privacy_gateway.crypto import generate_key
from privacy_gateway.exceptions import RestoreError
from privacy_gateway.facade import GatewayConfig, PrivacyGateway

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"
SYNTH_TEXT = f"Свяжитесь: {SYNTH_EMAIL}, сервер {SYNTH_IP}\n"

_ENTITIES_CONFIG = Path("config.example") / "entities.yaml"
_MISMATCHED_IDENTITY = (0, 0)
_CONTROL_NAME = "CONTROL.txt"
_CONTROL_TEXT = "do-not-delete\n"

pinned_only = pytest.mark.skipif(
    not trust.SUPPORTS_DIR_FD,
    reason="descriptor-relative ветвь недоступна на этой платформе",
)
handle_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="handle-ветвь исполняется на windows-latest в exact CI",
)
posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="на Windows недоступность закрепления проверяется иначе",
)


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


def _quarantine_leftovers(base: Path) -> list[Path]:
    return [
        path
        for path in base.iterdir()
        if path.name.startswith(trust.QUARANTINE_PREFIX)
    ]


def _plant_decoy(base: Path, state: dict[str, Path]) -> None:
    """Подменить карантинное имя другим настоящим каталогом.

    Вызывается ровно на границе: проверенный объект уже отцеплен и повторно
    сверен, но ни один дочерний объект ещё не удалён. Атакующий уводит
    проверенную сущность под другое имя и занимает освободившееся карантинное
    имя собственным каталогом с контрольным содержимым.
    """
    quarantine = next(
        path
        for path in base.iterdir()
        if path.name.startswith(trust.QUARANTINE_PREFIX)
    )
    moved = quarantine.with_name(quarantine.name + "-moved")
    os.rename(quarantine, moved)
    quarantine.mkdir()
    (quarantine / _CONTROL_NAME).write_text(_CONTROL_TEXT, encoding="utf-8")
    (quarantine / "sub").mkdir()
    (quarantine / "sub" / "deep.txt").write_text("deep\n", encoding="utf-8")
    state["decoy"] = quarantine
    state["moved"] = moved


def _assert_decoy_survived(state: dict[str, Path], victim: Path) -> None:
    """Проверить, что подставленный каталог и victim не затронуты."""
    decoy = state["decoy"]
    assert (decoy / _CONTROL_NAME).is_file()
    assert (decoy / "sub" / "deep.txt").is_file()
    assert (victim / "keep.txt").is_file()


def _assert_swap_defended_pinned(state: dict[str, Path], victim: Path) -> None:
    """Linux: проверенная сущность остаётся пустым каталогом под чужим именем.

    Финальный ``rmdir`` называет имя только для проверки идентичности
    карантинного имени (которое теперь указывает на decoy и не совпадает),
    поэтому сама проверенная сущность, уже переехавшая под имя ``-moved``,
    никаким destructive-вызовом по имени не адресуется. Дети у неё вычищены
    относительно дескриптора, но сам каталог остаётся.
    """
    _assert_decoy_survived(state, victim)
    moved = state["moved"]
    assert moved.is_dir()
    assert not any(moved.iterdir())


def _assert_swap_defended_handle(state: dict[str, Path], victim: Path) -> None:
    """Windows: диспозиция на handle удаляет сущность целиком по identity.

    В отличие от Linux, диспозиция ставится на сам handle закреплённого
    объекта и не зависит от его текущего имени: сущность, переехавшая под имя
    ``-moved``, удаляется полностью при закрытии handle.
    """
    _assert_decoy_survived(state, victim)
    assert not state["moved"].exists()


def _identity_mismatch() -> AbstractContextManager[Any]:
    """Смоделировать расхождение идентичности на границе удаления.

    На Windows ``_win_identity`` вызывается дважды — до и после отцепления,
    поэтому подмена через ``return_value`` не создаёт расхождения: оба вызова
    вернули бы одно и то же значение. Первый вызов обязан вернуть настоящую
    идентичность, второй — расходящуюся.
    """
    if sys.platform == "win32" and not trust.SUPPORTS_DIR_FD:
        real_identity: Callable[[int], tuple[int, bytes]] = vars(trust)[
            "_win_identity"
        ]
        calls = {"count": 0}

        def fake(handle: int) -> tuple[int, bytes]:
            calls["count"] += 1
            if calls["count"] == 1:
                return real_identity(handle)
            return _MISMATCHED_IDENTITY  # type: ignore[return-value]

        return patch.object(trust, "_win_identity", side_effect=fake)

    return patch.object(
        trust, "path_identity", return_value=_MISMATCHED_IDENTITY
    )


# ---------------------------------------------------------------------------
# Подмена карантинного имени настоящим каталогом
# ---------------------------------------------------------------------------


@pinned_only
def test_quarantine_swap_real_directory_pinned(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Подмена quarantine перед обходом не удаляет подставленный каталог.

    Доказывает, что обход идёт от удерживаемого дескриптора проверенного
    каталога: подставленный под тем же именем настоящий каталог остаётся
    целым, а вычищается именно проверенная сущность.
    """
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    state: dict[str, Path] = {}
    real_scandir = os.scandir

    def scandir(target: Any, *args: Any, **kwargs: Any) -> Any:
        if not state and isinstance(target, int):
            _plant_decoy(base, state)
        return real_scandir(target, *args, **kwargs)

    with patch("os.scandir", side_effect=scandir):
        gateway.discard(prepared.context)

    _assert_swap_defended_pinned(state, victim)


@handle_only
def test_quarantine_swap_real_directory_handle(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """То же на handle-ветви: дети открываются относительно handle."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    state: dict[str, Path] = {}
    real_children = vars(trust)["_win_children"]

    def children(handle: int) -> Any:
        if not state:
            _plant_decoy(base, state)
        return real_children(handle)

    with patch.object(trust, "_win_children", side_effect=children):
        gateway.discard(prepared.context)

    _assert_swap_defended_handle(state, victim)


# ---------------------------------------------------------------------------
# Секрет владения на границе удаления
# ---------------------------------------------------------------------------


@pinned_only
def test_post_rename_secret_mismatch_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Чужой секрет после отцепления приводит к отказу без удаления.

    Три чтения маркера: prefilter фасада по имени, проверка до отцепления и
    проверка после отцепления. Последняя возвращает чужой секрет с тем же
    handle — операция обязана завершиться fail closed.
    """
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    genuine = trust.read_owner_secret(workspace, prepared.context._handle)
    assert genuine is not None
    secrets_seen = iter([genuine, trust.new_workspace_secret()])

    with patch.object(
        trust,
        "read_owner_secret_fd",
        side_effect=lambda *args, **kwargs: next(secrets_seen),
    ):
        with pytest.raises(RestoreError):
            gateway.discard(prepared.context)

    assert workspace.is_dir()
    assert (workspace / trust.OWNER_MARKER_NAME).is_file()
    assert (workspace / "prompt.txt").is_file()
    assert _quarantine_leftovers(base) == []


@handle_only
def test_post_rename_secret_mismatch_handle_branch(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """То же на handle-ветви: маркер перечитывается через handle."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    genuine = trust.read_owner_secret(workspace, prepared.context._handle)
    assert genuine is not None
    secrets_seen = iter([genuine, trust.new_workspace_secret()])

    with patch.object(
        trust,
        "_win_read_marker",
        side_effect=lambda *args, **kwargs: next(secrets_seen),
    ):
        with pytest.raises(RestoreError):
            gateway.discard(prepared.context)

    assert workspace.is_dir()
    assert _quarantine_leftovers(base) == []


# ---------------------------------------------------------------------------
# Инвариант: нет закрепления — нет удаления
# ---------------------------------------------------------------------------


@posix_only
def test_pinning_unavailable_fails_closed(
    tmp_path: Path, mock_keyring: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без закрепления удаление не выполняется вообще.

    Проверяется само поведение кода, а не конкретная платформа: при
    недоступности descriptor-relative и handle-механизма ``discard`` обязан
    отказать и не откатиться к path-based рекурсивному удалению.
    """
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    monkeypatch.setattr(trust, "SUPPORTS_DIR_FD", False)

    with patch.object(shutil, "rmtree") as rmtree:
        with pytest.raises(RestoreError):
            gateway.discard(prepared.context)

    assert rmtree.call_count == 0
    assert workspace.is_dir()
    assert (workspace / trust.OWNER_MARKER_NAME).is_file()
    assert _quarantine_leftovers(base) == []


# ---------------------------------------------------------------------------
# Валидная уборка
# ---------------------------------------------------------------------------


def test_valid_cleanup_removes_workspace_without_path_removal(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Валидный owned workspace удаляется и без path-based обхода."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    (workspace / "nested" / "deep").mkdir(parents=True)
    (workspace / "nested" / "deep" / "leaf.txt").write_text(
        "leaf\n", encoding="utf-8"
    )

    with patch.object(shutil, "rmtree") as rmtree:
        gateway.discard(prepared.context)

    assert rmtree.call_count == 0
    assert not workspace.exists()
    assert _quarantine_leftovers(base) == []


def test_valid_cleanup_survives_unavailable_keystore(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Недоступный keystore не мешает освобождению ресурсов."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    with patch(
        "privacy_gateway.keystore.get_key",
        side_effect=_keystore.KeystoreError("keystore недоступен"),
    ):
        gateway.discard(prepared.context)

    assert not workspace.exists()
    assert _quarantine_leftovers(base) == []


def test_repeated_discard_is_idempotent(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Повторный ``discard`` на удалённом каталоге не поднимает ошибку."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)

    gateway.discard(prepared.context)
    gateway.discard(prepared.context)

    assert _quarantine_leftovers(base) == []


# ---------------------------------------------------------------------------
# Отказные пути
# ---------------------------------------------------------------------------


def test_identity_mismatch_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Расхождение идентичности на границе не удаляет ничего."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    victim = _victim(tmp_path)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    with _identity_mismatch():
        with pytest.raises(RestoreError):
            gateway.discard(prepared.context)

    assert (victim / "keep.txt").is_file()
    assert workspace.is_dir()
    assert (workspace / trust.OWNER_MARKER_NAME).is_file()
    assert _quarantine_leftovers(base) == []


def test_missing_marker_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Отсутствующий маркер владения запрещает удаление."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    (workspace / trust.OWNER_MARKER_NAME).unlink()

    with pytest.raises(RestoreError):
        gateway.discard(prepared.context)

    assert workspace.is_dir()
    assert (workspace / "prompt.txt").is_file()
    assert _quarantine_leftovers(base) == []


def test_foreign_marker_secret_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Маркер с тем же handle, но чужим секретом, удаления не даёт."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)
    marker = workspace / trust.OWNER_MARKER_NAME
    marker.write_text(
        f"{trust.MARKER_FORMAT_VERSION}\n"
        f"{prepared.context._handle}\n"
        f"{trust.new_workspace_secret()}\n",
        encoding="ascii",
    )

    with pytest.raises(RestoreError):
        gateway.discard(prepared.context)

    assert workspace.is_dir()
    assert _quarantine_leftovers(base) == []


def test_detach_failure_fails_closed(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Отказ отцепления не приводит к удалению по имени."""
    base = tmp_path / "base"
    gateway = _gateway(base)
    prepared = gateway.prepare(SYNTH_TEXT)
    workspace = _workspace_of(base)

    with patch.object(os, "rename", side_effect=OSError("rename отказал")):
        with patch.object(shutil, "rmtree") as rmtree:
            with pytest.raises(RestoreError):
                gateway.discard(prepared.context)

    assert rmtree.call_count == 0
    assert workspace.is_dir()
    assert (workspace / trust.OWNER_MARKER_NAME).is_file()
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

    with _identity_mismatch():
        with pytest.raises(RestoreError) as exc_info:
            gateway.discard(prepared.context)

    message = str(exc_info.value)
    assert SYNTH_EMAIL not in message
    assert SYNTH_IP not in message
    assert secret not in message
    assert str(workspace) not in message
    assert str(base) not in message
