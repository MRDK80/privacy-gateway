"""Доверенность и принадлежность контекста восстановления (ADR-34, issue #43).

Удаление рабочего каталога возможно только для контекста, который доказал
владение именно этим каталогом:

1. Секрет владения. Подготовка генерирует случайный секрет, пишет его в маркер
   ``.pgw-owner`` внутри рабочего каталога (``O_EXCL``, права только для
   владельца) вместе с версией формата и handle, и подписывает полезную
   нагрузку токена HMAC-SHA256 на этом секрете.
2. Принадлежность. Перед удалением проверяются canonical containment в
   доверенной базе из конфигурации фасада, отказ следовать symlink/junction,
   совпадение handle в маркере и совпадение MAC.
3. Race-safe граница удаления (дополнение ADR-34). Проверенный объект
   файловой системы отцепляется от атакуемого имени и удаляется как
   подтверждённая сущность, а не как заново разрешённый pathname. На
   платформах с descriptor-relative семантикой каталог закрепляется открытым
   дескриптором; на остальных используется отцепление имени с повторной
   сверкой идентичности.

Секрет живёт ровно столько, сколько рабочий каталог. Keystore в проверке не
участвует, поэтому ротация или удаление ключа не мешают освобождению
ресурсов — существующая гарантия cleanup не ослаблена.

Строковый containment (``startswith`` и эквиваленты) не используется: сравнение
идёт по компонентам пути после ``os.path.realpath``.

Модуль не логирует и не помещает в сообщения секреты, открытый текст и
локальные пути.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Final

#: Доменное разделение MAC контекста. Меняется вместе с версией формата.
CONTEXT_MAC_LABEL: Final = b"privacy-gateway/restore-context/v2"

#: Имя файла маркера владения внутри рабочего каталога.
OWNER_MARKER_NAME: Final = ".pgw-owner"

#: Версия формата маркера владения.
MARKER_FORMAT_VERSION: Final = "1"

#: Префикс имени, под которым проверенный каталог отцепляется перед удалением.
QUARANTINE_PREFIX: Final = ".pgw-quarantine-"

_MARKER_MODE: Final = 0o600
_SECRET_BYTES: Final = 32
_QUARANTINE_BYTES: Final = 16

#: Доступна ли descriptor-relative семантика удаления.
#:
#: На Linux и подобных ОС истинно: ``openat``/``renameat``/``fstatat`` и
#: symlink-устойчивый обход дерева в ``shutil.rmtree`` доступны из stdlib.
#: На Windows ложно: ``os.supports_dir_fd`` пуст, эквивалента ``openat`` в
#: поддерживаемом Python нет, и используется отцепление по имени.
SUPPORTS_DIR_FD: Final = (
    os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
    and bool(getattr(shutil.rmtree, "avoids_symlink_attacks", False))
)

_OPEN_DIR_FLAGS: Final = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_OPEN_MARKER_FLAGS: Final = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)

_UNTRUSTED: Final = "Рабочий каталог не признан доверенным."
_NOT_OWNED: Final = "Рабочий каталог не признан принадлежащим контексту."
_UNAVAILABLE: Final = "Рабочий каталог недоступен."
_CHANGED: Final = "Рабочий каталог изменился во время проверки."


class ContextTrustError(Exception):
    """Внутренний отказ проверки доверенности контекста.

    Частью публичного контракта не является: фасад транслирует её в
    ``RestoreError`` через exception chaining.
    """


def new_workspace_secret() -> str:
    """Создать секрет владения для одного рабочего каталога."""
    return secrets.token_hex(_SECRET_BYTES)


def _derive(secret: str) -> bytes:
    """Вывести ключ MAC из секрета владения."""
    return hmac.new(
        secret.encode("ascii"), CONTEXT_MAC_LABEL, hashlib.sha256
    ).digest()


def canonical_payload(payload: dict[str, Any]) -> bytes:
    """Вернуть каноническую форму полезной нагрузки токена.

    Поле подписи исключается, порядок ключей детерминирован, разделители
    фиксированы: одна и та же нагрузка всегда даёт один и тот же MAC.
    """
    body = {key: value for key, value in payload.items() if key != "sig"}
    return json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    """Подписать полезную нагрузку секретом владения."""
    return hmac.new(
        _derive(secret), canonical_payload(payload), hashlib.sha256
    ).hexdigest()


def verify_payload(
    payload: dict[str, Any],
    signature: str | None,
    secret: str,
) -> bool:
    """Проверить подпись контекста секретом владения.

    Сравнение постоянного времени; при отсутствующей подписи — ``False``.
    """
    if not signature:
        return False
    expected = sign_payload(payload, secret)
    return hmac.compare_digest(expected, signature)


def write_owner_marker(workspace: Path, handle: str, secret: str) -> None:
    """Закрепить владение рабочим каталогом.

    Файл создаётся исключительно (``O_EXCL``) с правами только для владельца
    и содержит версию формата, handle операции и секрет владения.

    Raises:
        OSError: Маркер не удалось создать.
    """
    marker = workspace / OWNER_MARKER_NAME
    descriptor = os.open(
        marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _MARKER_MODE
    )
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(f"{MARKER_FORMAT_VERSION}\n{handle}\n{secret}\n")


def _parse_marker(raw: str, handle: str) -> str | None:
    """Разобрать содержимое маркера и вернуть секрет для этого handle.

    Возвращает ``None`` при неизвестной версии формата, повреждённой
    структуре либо чужом handle. Единственная точка разбора маркера: путь по
    имени и путь по дескриптору обязаны трактовать формат одинаково.
    """
    lines = raw.splitlines()
    if len(lines) != 3:
        return None
    version, stored_handle, secret = lines
    if version != MARKER_FORMAT_VERSION or not secret:
        return None
    if not hmac.compare_digest(stored_handle, handle):
        return None
    return secret


def read_owner_secret(workspace: Path, handle: str) -> str | None:
    """Вернуть секрет владения для заявленного handle.

    Возвращает ``None``, если маркер отсутствует, не является обычным файлом,
    имеет неизвестную версию формата, повреждён либо принадлежит другому
    handle. Ссылкам не следует.
    """
    marker = workspace / OWNER_MARKER_NAME
    try:
        info = marker.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or is_reparse_point(info):
        return None
    try:
        raw = marker.read_text(encoding="ascii")
    except (OSError, ValueError):
        return None
    return _parse_marker(raw, handle)


def read_owner_secret_fd(workspace_fd: int, handle: str) -> str | None:
    """Вернуть секрет владения, читая маркер относительно дескриптора.

    Открытие идёт через ``dir_fd``, поэтому компоненты пути повторно не
    разрешаются: маркер читается внутри уже закреплённого каталога.
    """
    try:
        marker_fd = os.open(
            OWNER_MARKER_NAME, _OPEN_MARKER_FLAGS, dir_fd=workspace_fd
        )
    except OSError:
        return None
    try:
        info = os.fstat(marker_fd)
        if not stat.S_ISREG(info.st_mode) or is_reparse_point(info):
            return None
        with os.fdopen(
            marker_fd, "r", encoding="ascii", closefd=False
        ) as stream:
            raw = stream.read()
    except (OSError, ValueError):
        return None
    finally:
        os.close(marker_fd)
    return _parse_marker(raw, handle)


def resolve_trusted_base(configured: Path | None) -> Path:
    """Вернуть доверенную базу рабочих каталогов в канонической форме.

    Источник — конфигурация фасада; при ``None`` библиотека владеет
    подкаталогами системного временного каталога.
    """
    base = configured if configured is not None else Path(tempfile.gettempdir())
    return canonical_path(base)


def canonical_path(path: Path) -> Path:
    """Вернуть путь после разрешения ссылок и относительных компонентов."""
    return Path(os.path.realpath(path))


def is_contained(base: Path, workspace: Path) -> bool:
    """Проверить строгое вложение канонического пути в доверенную базу.

    Сравнение идёт по компонентам пути: sibling-каталог с общим строковым
    префиксом имени вложенным не считается.
    """
    try:
        relative = workspace.relative_to(base)
    except ValueError:
        return False
    parts = relative.parts
    return bool(parts) and ".." not in parts


def is_reparse_point(info: os.stat_result) -> bool:
    """Определить reparse point (junction, symlink) на Windows."""
    attributes = getattr(info, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & flag)


def is_real_directory(path: Path) -> bool:
    """Проверить, что путь — настоящий каталог, а не ссылка на каталог."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not is_reparse_point(info)


def identity_from_stat(info: os.stat_result) -> tuple[int, int]:
    """Вернуть идентичность объекта файловой системы из результата stat."""
    return (info.st_dev, info.st_ino)


def path_identity(
    name: str | Path, *, dir_fd: int | None = None
) -> tuple[int, int]:
    """Вернуть идентичность каталога по имени без следования ссылкам.

    При заданном ``dir_fd`` имя разрешается относительно уже открытого
    каталога, а не от корня файловой системы.

    Raises:
        ContextTrustError: Объект недоступен либо не является настоящим
            каталогом.
    """
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as exc:
        raise ContextTrustError(_UNAVAILABLE) from exc
    if not stat.S_ISDIR(info.st_mode) or is_reparse_point(info):
        raise ContextTrustError(_UNTRUSTED)
    return identity_from_stat(info)


def _new_quarantine_name() -> str:
    """Вернуть непредсказуемое имя для отцепления проверенного каталога."""
    return f"{QUARANTINE_PREFIX}{secrets.token_hex(_QUARANTINE_BYTES)}"


def _open_directory(name: str | Path, dir_fd: int | None = None) -> int:
    """Открыть каталог без следования ссылкам и вернуть дескриптор.

    Raises:
        ContextTrustError: Каталог недоступен либо является ссылкой.
    """
    try:
        return os.open(name, _OPEN_DIR_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        raise ContextTrustError(_UNAVAILABLE) from exc


def _require_marker(
    workspace_fd: int, handle: str, secret: str
) -> None:
    """Проверить маркер владения внутри закреплённого каталога.

    Сверяется не только наличие маркера с тем же handle, но и совпадение
    секрета с тем, на котором проверен MAC контекста: это привязывает
    аутентичность токена к закреплённой сущности, а не к пути.
    """
    found = read_owner_secret_fd(workspace_fd, handle)
    if found is None or not hmac.compare_digest(found, secret):
        raise ContextTrustError(_NOT_OWNED)


def _restore_pinned_quietly(
    parent_fd: int, quarantine: str, name: str
) -> None:
    """Вернуть отцепленный объект под исходное имя, если оно свободно.

    Best-effort: при неудаче объект остаётся под карантинным именем и не
    удаляется. Fail-closed важнее, чем полнота уборки.
    """
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        try:
            os.rename(
                quarantine, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd
            )
        except OSError:
            return


def _restore_detached_quietly(quarantine: Path, workspace: Path) -> None:
    """Вернуть отцепленный каталог под исходное имя, если оно свободно."""
    if workspace.exists():
        return
    try:
        os.rename(quarantine, workspace)
    except OSError:
        return


def _detach_pinned(
    parent_fd: int, name: str, identity: tuple[int, int]
) -> str:
    """Отцепить проверенный каталог внутри закреплённого родителя.

    После ``renameat`` идентичность объекта под карантинным именем сверяется
    с идентичностью, снятой с дескриптора. Несовпадение означает, что имя
    подменили: ничего не удаляется.
    """
    quarantine = _new_quarantine_name()
    try:
        os.rename(name, quarantine, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except OSError as exc:
        raise ContextTrustError(_UNAVAILABLE) from exc
    try:
        detached = path_identity(quarantine, dir_fd=parent_fd)
    except ContextTrustError:
        _restore_pinned_quietly(parent_fd, quarantine, name)
        raise
    if detached != identity:
        _restore_pinned_quietly(parent_fd, quarantine, name)
        raise ContextTrustError(_CHANGED)
    return quarantine


def _remove_pinned(workspace: Path, handle: str, secret: str) -> None:
    """Удалить каталог, закреплённый дескриптором (descriptor-relative путь).

    Все проверки идут по дескрипторам, отцепление — ``renameat`` внутри
    закреплённого родителя, обход дерева — относительно того же родителя.
    Атакуемое имя после отцепления в удалении не участвует.
    """
    parent_fd = _open_directory(workspace.parent)
    try:
        workspace_fd = _open_directory(workspace.name, parent_fd)
        try:
            info = os.fstat(workspace_fd)
            if not stat.S_ISDIR(info.st_mode):
                raise ContextTrustError(_UNTRUSTED)
            identity = identity_from_stat(info)
            _require_marker(workspace_fd, handle, secret)
            quarantine = _detach_pinned(parent_fd, workspace.name, identity)
        finally:
            os.close(workspace_fd)
        shutil.rmtree(quarantine, dir_fd=parent_fd, ignore_errors=True)
    finally:
        os.close(parent_fd)


def _remove_detached(workspace: Path, handle: str, secret: str) -> None:
    """Удалить каталог через отцепление имени с повторной сверкой.

    Путь для платформ без ``openat``-семантики. Гарантия слабее, чем при
    закреплении дескриптором, и опирается на два факта: после ``rename``
    атакуемое имя в удалении не участвует, а карантинное имя случайно и
    известно только этому процессу. Идентичность и маркер повторно
    проверяются уже под карантинным именем.
    """
    identity = path_identity(workspace)
    found = read_owner_secret(workspace, handle)
    if found is None or not hmac.compare_digest(found, secret):
        raise ContextTrustError(_NOT_OWNED)

    quarantine = workspace.parent / _new_quarantine_name()
    try:
        os.rename(workspace, quarantine)
    except OSError as exc:
        raise ContextTrustError(_UNAVAILABLE) from exc

    try:
        if path_identity(quarantine) != identity:
            raise ContextTrustError(_CHANGED)
        if read_owner_secret(quarantine, handle) is None:
            raise ContextTrustError(_NOT_OWNED)
    except ContextTrustError:
        _restore_detached_quietly(quarantine, workspace)
        raise

    shutil.rmtree(quarantine, ignore_errors=True)


def remove_verified_workspace(
    workspace: Path, handle: str, secret: str
) -> None:
    """Удалить подтверждённый рабочий каталог как проверенную сущность.

    Публичное поведение одинаково на всех платформах: удаляется только
    объект, чья идентичность и маркер владения подтверждены непосредственно
    на границе удаления; при любом расхождении не удаляется ничего.
    Низкоуровневый механизм различается — см. дополнение к ADR-34.

    Рекурсивное удаление после отцепления выполняется best-effort: решение о
    безопасности принято на границе отцепления, а частичный отказ уборки не
    меняет доверия и сохраняет прежнее наблюдаемое поведение ``discard``.

    Raises:
        ContextTrustError: Объект недоступен, не подтверждён либо подменён.
    """
    if SUPPORTS_DIR_FD:
        _remove_pinned(workspace, handle, secret)
        return
    _remove_detached(workspace, handle, secret)
