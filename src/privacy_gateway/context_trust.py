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
3. Привязка destructive operation к сущности (дополнение ADR-34, issue #52).
   Рекурсивное удаление начинается от объекта, закреплённого дескриптором
   (Linux) или handle (Windows), и ни на одном шаге не принимает заново
   разрешённый pathname как основание для удаления. Карантинное имя в
   destructive-вызовах не участвует; финальное снятие имени выполняется только
   при совпадении идентичности с ранее проверенной.

Если закрепление подтвердить нельзя, операция завершается fail closed:
остаточный карантинный объект допустим, удаление неподтверждённой сущности —
нет. Path-based рекурсивное удаление как fallback не используется.

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
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Final, cast

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
#: ``scandir`` относительно дескриптора доступны из stdlib, поэтому обход
#: дерева выполняется от удерживаемого дескриптора проверенного каталога.
#: На Windows ложно: ``os.supports_dir_fd`` пуст, и используется
#: handle-механизм через stdlib ``ctypes``.
SUPPORTS_DIR_FD: Final = (
    os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
    and os.scandir in os.supports_fd
)

_OPEN_DIR_FLAGS: Final = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_OPEN_MARKER_FLAGS: Final = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)

_UNTRUSTED: Final = "Рабочий каталог не признан доверенным."
_NOT_OWNED: Final = "Рабочий каталог не признан принадлежащим контексту."
_UNAVAILABLE: Final = "Рабочий каталог недоступен."
_CHANGED: Final = "Рабочий каталог изменился во время проверки."
_UNPINNABLE: Final = "Рабочий каталог нельзя удалить безопасно."


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
    структуре либо чужом handle. Единственная точка разбора маркера: все
    пути чтения обязаны трактовать формат одинаково.
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


def _require_marker(workspace_fd: int, handle: str, secret: str) -> None:
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


def _unlink_tree_pinned(dir_fd: int) -> None:
    """Удалить содержимое каталога относительно удерживаемого дескриптора.

    Каждый дочерний каталог открывается относительно родителя без следования
    ссылкам, и обход продолжается только если открытый объект — та же
    сущность, что была перечислена (``samestat``). Имена от корня файловой
    системы повторно не разрешаются.

    Полнота уборки best-effort: отдельный отказ пропускается, решение о
    безопасности уже принято на границе закрепления.
    """
    for entry in os.scandir(dir_fd):
        if entry.is_dir(follow_symlinks=False):
            try:
                expected = entry.stat(follow_symlinks=False)
                child_fd = os.open(entry.name, _OPEN_DIR_FLAGS, dir_fd=dir_fd)
            except OSError:
                continue
            try:
                if not os.path.samestat(expected, os.fstat(child_fd)):
                    continue
                _unlink_tree_pinned(child_fd)
            except OSError:
                continue
            finally:
                os.close(child_fd)
            try:
                os.rmdir(entry.name, dir_fd=dir_fd)
            except OSError:
                continue
        else:
            try:
                os.unlink(entry.name, dir_fd=dir_fd)
            except OSError:
                continue


def _rmdir_verified(
    parent_fd: int, quarantine: str, identity: tuple[int, int]
) -> None:
    """Снять карантинное имя только для ранее проверенной сущности.

    Единственная операция, которой приходится назвать имя: снятие пустого
    каталога. Она выполняется лишь при совпадении идентичности, иначе имя
    остаётся, а объект — нет.
    """
    try:
        if path_identity(quarantine, dir_fd=parent_fd) != identity:
            return
        os.rmdir(quarantine, dir_fd=parent_fd)
    except (ContextTrustError, OSError):
        return


def _remove_pinned(workspace: Path, handle: str, secret: str) -> None:
    """Удалить каталог, закреплённый дескриптором (Linux и подобные).

    Дескриптор проверенного каталога удерживается до конца обхода: после
    ``renameat`` идентичность и маркер сверяются повторно уже по этому
    дескриптору, а дерево удаляется относительно него. Карантинное имя
    участвует только в финальном ``rmdir`` пустого каталога и лишь при
    совпадении идентичности.
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
            try:
                if identity_from_stat(os.fstat(workspace_fd)) != identity:
                    raise ContextTrustError(_CHANGED)
                _require_marker(workspace_fd, handle, secret)
            except ContextTrustError:
                _restore_pinned_quietly(parent_fd, quarantine, workspace.name)
                raise
            _unlink_tree_pinned(workspace_fd)
        finally:
            os.close(workspace_fd)
        _rmdir_verified(parent_fd, quarantine, identity)
    finally:
        os.close(parent_fd)


if sys.platform == "win32":  # pragma: no cover - платформенная ветка
    import ctypes
    from ctypes import wintypes

    _GENERIC_READ: Final = 0x80000000
    _DELETE: Final = 0x00010000
    _SYNCHRONIZE: Final = 0x00100000
    _FILE_LIST_DIRECTORY: Final = 0x00000001
    _FILE_TRAVERSE: Final = 0x00000020
    _FILE_READ_ATTRIBUTES: Final = 0x00000080
    _FILE_SHARE_ALL: Final = 0x00000007
    _OPEN_EXISTING: Final = 3
    _FILE_FLAG_BACKUP_SEMANTICS: Final = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x00200000
    _INVALID_HANDLE_VALUE: Final = -1
    _FILE_ATTRIBUTE_DIRECTORY: Final = 0x00000010
    _FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x00000400

    _FileDispositionInfo: Final = 4
    _FileAttributeTagInfo: Final = 9
    _FileIdBothDirectoryInfo: Final = 10
    _FileIdBothDirectoryRestartInfo: Final = 11
    _FileIdInfo: Final = 18
    _FileDispositionInfoEx: Final = 21

    _DISPOSITION_DELETE: Final = 0x00000001
    _DISPOSITION_POSIX_SEMANTICS: Final = 0x00000002
    _DISPOSITION_IGNORE_READONLY: Final = 0x00000010

    _FILE_OPEN: Final = 1
    _FILE_DIRECTORY_FILE: Final = 0x00000001
    _FILE_NON_DIRECTORY_FILE: Final = 0x00000040
    _FILE_SYNCHRONOUS_IO_NONALERT: Final = 0x00000020
    _FILE_OPEN_REPARSE_POINT: Final = 0x00200000

    _ERROR_NOT_SUPPORTED: Final = 50
    _ERROR_INVALID_PARAMETER: Final = 87
    _ERROR_NO_MORE_FILES: Final = 18
    _ERROR_HANDLE_EOF: Final = 38

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]

    class _FileId128(ctypes.Structure):
        _fields_ = [("Identifier", ctypes.c_ubyte * 16)]

    class _FileIdInfoStruct(ctypes.Structure):
        _fields_ = [
            ("VolumeSerialNumber", ctypes.c_ulonglong),
            ("FileId", _FileId128),
        ]

    class _FileAttributeTagInfoStruct(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    class _FileDispositionInfoStruct(ctypes.Structure):
        _fields_ = [("DeleteFile", ctypes.c_ubyte)]

    class _FileDispositionInfoExStruct(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD)]

    class _FileIdBothDirInfoStruct(ctypes.Structure):
        _fields_ = [
            ("NextEntryOffset", wintypes.DWORD),
            ("FileIndex", wintypes.DWORD),
            ("CreationTime", wintypes.LARGE_INTEGER),
            ("LastAccessTime", wintypes.LARGE_INTEGER),
            ("LastWriteTime", wintypes.LARGE_INTEGER),
            ("ChangeTime", wintypes.LARGE_INTEGER),
            ("EndOfFile", wintypes.LARGE_INTEGER),
            ("AllocationSize", wintypes.LARGE_INTEGER),
            ("FileAttributes", wintypes.DWORD),
            ("FileNameLength", wintypes.DWORD),
            ("EaSize", wintypes.DWORD),
            ("ShortNameLength", ctypes.c_char),
            ("ShortName", wintypes.WCHAR * 12),
            ("FileId", wintypes.LARGE_INTEGER),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [
            ("Status", ctypes.c_void_p),
            ("Information", ctypes.c_void_p),
        ]

    _ntdll.NtCreateFile.restype = wintypes.LONG
    _ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]

    _WIN_DIR_ACCESS: Final = (
        _GENERIC_READ | _DELETE | _SYNCHRONIZE | _FILE_READ_ATTRIBUTES
    )
    _WIN_CHILD_ACCESS: Final = (
        _DELETE
        | _SYNCHRONIZE
        | _FILE_READ_ATTRIBUTES
        | _FILE_LIST_DIRECTORY
        | _FILE_TRAVERSE
    )

    def _win_open_directory(path: Path) -> int:
        """Открыть handle каталога без следования reparse point.

        Raises:
            ContextTrustError: Каталог недоступен.
        """
        handle = _kernel32.CreateFileW(
            str(path),
            _WIN_DIR_ACCESS,
            _FILE_SHARE_ALL,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE or handle is None:
            raise ContextTrustError(_UNAVAILABLE)
        return handle

    def _win_close(handle: int) -> None:
        """Закрыть handle, игнорируя отказ закрытия."""
        _kernel32.CloseHandle(handle)

    def _win_identity(handle: int) -> tuple[int, bytes]:
        """Вернуть идентичность закреплённого объекта.

        ``FileIdInfo`` даёт серийный номер тома и 128-битный идентификатор
        файла: аналог пары ``(st_dev, st_ino)``.

        Raises:
            ContextTrustError: Идентичность получить не удалось.
        """
        info = _FileIdInfoStruct()
        ok = _kernel32.GetFileInformationByHandleEx(
            handle, _FileIdInfo, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            raise ContextTrustError(_UNAVAILABLE)
        return (info.VolumeSerialNumber, bytes(info.FileId.Identifier))

    def _win_attributes(handle: int) -> int:
        """Вернуть атрибуты закреплённого объекта.

        Raises:
            ContextTrustError: Атрибуты получить не удалось.
        """
        info = _FileAttributeTagInfoStruct()
        ok = _kernel32.GetFileInformationByHandleEx(
            handle,
            _FileAttributeTagInfo,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            raise ContextTrustError(_UNAVAILABLE)
        return int(info.FileAttributes)

    def _win_children(handle: int) -> list[tuple[str, int]]:
        """Перечислить детей относительно handle: ``[(имя, атрибуты)]``.

        Raises:
            ContextTrustError: Перечисление не удалось.
        """
        buffer = ctypes.create_string_buffer(64 * 1024)
        entries: list[tuple[str, int]] = []
        info_class = _FileIdBothDirectoryRestartInfo
        name_offset = _FileIdBothDirInfoStruct.FileName.offset
        while True:
            ok = _kernel32.GetFileInformationByHandleEx(
                handle, info_class, buffer, ctypes.sizeof(buffer)
            )
            if not ok:
                error = ctypes.get_last_error()
                if error in (_ERROR_NO_MORE_FILES, _ERROR_HANDLE_EOF):
                    break
                raise ContextTrustError(_UNAVAILABLE)
            info_class = _FileIdBothDirectoryInfo
            offset = 0
            while True:
                entry = _FileIdBothDirInfoStruct.from_buffer(buffer, offset)
                name = ctypes.wstring_at(
                    ctypes.addressof(buffer) + offset + name_offset,
                    entry.FileNameLength // ctypes.sizeof(wintypes.WCHAR),
                )
                if name not in (".", ".."):
                    entries.append((name, int(entry.FileAttributes)))
                if entry.NextEntryOffset == 0:
                    break
                offset += entry.NextEntryOffset
        return entries

    def _win_open_child(parent: int, name: str, *, directory: bool) -> int:
        """Открыть ребёнка относительно handle родителя.

        Win32 не предоставляет ``openat``-семантику, поэтому используется
        ``NtCreateFile`` с ``ObjectAttributes.RootDirectory``: имя
        разрешается только внутри закреплённого каталога.

        Raises:
            ContextTrustError: Ребёнка открыть не удалось.
        """
        child = wintypes.HANDLE()
        unicode_name = _UnicodeString()
        name_buffer = ctypes.create_unicode_buffer(name)
        unicode_name.Buffer = ctypes.cast(name_buffer, wintypes.LPWSTR)
        unicode_name.Length = len(name) * ctypes.sizeof(wintypes.WCHAR)
        unicode_name.MaximumLength = unicode_name.Length
        attributes = _ObjectAttributes()
        attributes.Length = ctypes.sizeof(_ObjectAttributes)
        attributes.RootDirectory = parent
        attributes.ObjectName = ctypes.pointer(unicode_name)
        attributes.Attributes = 0
        attributes.SecurityDescriptor = None
        attributes.SecurityQualityOfService = None
        iosb = _IoStatusBlock()
        options = _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_OPEN_REPARSE_POINT
        options |= (
            _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
        )
        status = _ntdll.NtCreateFile(
            ctypes.byref(child),
            _WIN_CHILD_ACCESS,
            ctypes.byref(attributes),
            ctypes.byref(iosb),
            None,
            0,
            _FILE_SHARE_ALL,
            _FILE_OPEN,
            options,
            None,
            0,
        )
        if status != 0 or child.value is None:
            raise ContextTrustError(_UNAVAILABLE)
        return cast(int, child.value)

    def _win_read_marker(dir_handle: int, handle: str) -> str | None:
        """Прочитать маркер владения относительно handle каталога."""
        try:
            marker_handle = _win_open_child(
                dir_handle, OWNER_MARKER_NAME, directory=False
            )
        except ContextTrustError:
            return None
        try:
            buffer = ctypes.create_string_buffer(4096)
            read = wintypes.DWORD(0)
            ok = _kernel32.ReadFile(
                marker_handle,
                buffer,
                ctypes.sizeof(buffer) - 1,
                ctypes.byref(read),
                None,
            )
            if not ok:
                return None
            raw = buffer.raw[: read.value].decode("ascii", errors="replace")
        finally:
            _win_close(marker_handle)
        return _parse_marker(raw, handle)

    def _win_require_marker(dir_handle: int, handle: str, secret: str) -> None:
        """Сверить секрет маркера с аутентифицированным секретом."""
        found = _win_read_marker(dir_handle, handle)
        if found is None or not hmac.compare_digest(found, secret):
            raise ContextTrustError(_NOT_OWNED)

    def _win_delete(handle: int) -> None:
        """Пометить закреплённый объект к удалению через его handle.

        Сначала используется ``FileDispositionInfoEx`` с POSIX-семантикой:
        имя уходит из namespace немедленно. На сборках ниже 16299 вызов
        отвечает ``ERROR_NOT_SUPPORTED``, и применяется базовый
        ``FileDispositionInfo``: объект исчезает при закрытии handle.
        Диспозиция необратима, поэтому ставится последней операцией.

        Raises:
            ContextTrustError: Ни один механизм не сработал.
        """
        extended = _FileDispositionInfoExStruct(
            _DISPOSITION_DELETE
            | _DISPOSITION_POSIX_SEMANTICS
            | _DISPOSITION_IGNORE_READONLY
        )
        ok = _kernel32.SetFileInformationByHandle(
            handle,
            _FileDispositionInfoEx,
            ctypes.byref(extended),
            ctypes.sizeof(extended),
        )
        if ok:
            return
        error = ctypes.get_last_error()
        if error not in (_ERROR_NOT_SUPPORTED, _ERROR_INVALID_PARAMETER):
            raise ContextTrustError(_UNAVAILABLE)
        legacy = _FileDispositionInfoStruct(1)
        ok = _kernel32.SetFileInformationByHandle(
            handle,
            _FileDispositionInfo,
            ctypes.byref(legacy),
            ctypes.sizeof(legacy),
        )
        if not ok:
            raise ContextTrustError(_UNAVAILABLE)

    def _win_unlink_tree(dir_handle: int) -> None:
        """Удалить содержимое каталога, двигаясь только по handle-ам.

        Порядок строго снизу вверх: базовая диспозиция вступает в силу при
        закрытии handle, поэтому каталог должен быть пуст к моменту своего
        удаления. Полнота уборки best-effort.
        """
        for name, attributes in _win_children(dir_handle):
            is_dir = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
            is_reparse = bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
            try:
                child = _win_open_child(
                    dir_handle, name, directory=is_dir and not is_reparse
                )
            except ContextTrustError:
                continue
            try:
                if is_dir and not is_reparse:
                    _win_unlink_tree(child)
                _win_delete(child)
            except ContextTrustError:
                continue
            finally:
                _win_close(child)

    def _win_restore_quietly(quarantine: Path, workspace: Path) -> None:
        """Вернуть отцепленный каталог под исходное имя, если оно свободно."""
        if workspace.exists():
            return
        try:
            os.rename(quarantine, workspace)
        except OSError:
            return

    def _remove_handle_pinned(
        workspace: Path, handle: str, secret: str
    ) -> None:
        """Удалить каталог, закреплённый handle (Windows).

        Handle удерживается всю операцию: идентичность и маркер сверяются
        повторно после отцепления по тому же handle, дети открываются
        относительно него, а сам объект удаляется через собственную
        диспозицию. Карантинное имя в destructive-вызовах не участвует.
        """
        dir_handle = _win_open_directory(workspace)
        detached: Path | None = None
        try:
            attributes = _win_attributes(dir_handle)
            if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
                raise ContextTrustError(_UNTRUSTED)
            if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise ContextTrustError(_UNTRUSTED)
            identity = _win_identity(dir_handle)
            _win_require_marker(dir_handle, handle, secret)

            quarantine = workspace.parent / _new_quarantine_name()
            try:
                os.rename(workspace, quarantine)
            except OSError as exc:
                raise ContextTrustError(_UNAVAILABLE) from exc
            detached = quarantine

            if _win_identity(dir_handle) != identity:
                raise ContextTrustError(_CHANGED)
            _win_require_marker(dir_handle, handle, secret)

            _win_unlink_tree(dir_handle)
            _win_delete(dir_handle)
            detached = None
        except ContextTrustError:
            if detached is not None:
                _win_restore_quietly(detached, workspace)
            raise
        finally:
            _win_close(dir_handle)


def remove_verified_workspace(
    workspace: Path, handle: str, secret: str
) -> None:
    """Удалить подтверждённый рабочий каталог как проверенную сущность.

    Публичное поведение одинаково на всех платформах: удаляется только
    объект, чья идентичность и маркер владения подтверждены на границе
    удаления и остаются подтверждёнными на всём протяжении обхода. При любом
    расхождении не удаляется ничего. Низкоуровневый механизм различается —
    см. дополнение к ADR-34 по issue #52.

    Если ни descriptor-relative, ни handle-механизм недоступны, операция
    завершается fail closed: path-based рекурсивное удаление как fallback не
    используется.

    Raises:
        ContextTrustError: Объект недоступен, не подтверждён, подменён либо
            закрепление на этой платформе недоказуемо.
    """
    if SUPPORTS_DIR_FD:
        _remove_pinned(workspace, handle, secret)
        return
    if sys.platform == "win32":
        _remove_handle_pinned(workspace, handle, secret)
        return
    raise ContextTrustError(_UNPINNABLE)
