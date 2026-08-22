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
import tempfile
from pathlib import Path
from typing import Any, Final

#: Доменное разделение MAC контекста. Меняется вместе с версией формата.
CONTEXT_MAC_LABEL: Final = b"privacy-gateway/restore-context/v2"

#: Имя файла маркера владения внутри рабочего каталога.
OWNER_MARKER_NAME: Final = ".pgw-owner"

#: Версия формата маркера владения.
MARKER_FORMAT_VERSION: Final = "1"

_MARKER_MODE: Final = 0o600
_SECRET_BYTES: Final = 32


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
    lines = raw.splitlines()
    if len(lines) != 3:
        return None
    version, stored_handle, secret = lines
    if version != MARKER_FORMAT_VERSION or not secret:
        return None
    if not hmac.compare_digest(stored_handle, handle):
        return None
    return secret


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


def workspace_identity(path: Path) -> tuple[int, int]:
    """Вернуть идентичность каталога для повторной проверки перед удалением.

    Пара ``(st_dev, st_ino)`` снимается без следования ссылкам и позволяет
    отказаться от удаления, если компонент пути подменили после проверки.

    Raises:
        ContextTrustError: Путь не является настоящим каталогом.
    """
    try:
        info = path.lstat()
    except OSError as exc:
        raise ContextTrustError("Рабочий каталог недоступен.") from exc
    if not stat.S_ISDIR(info.st_mode) or is_reparse_point(info):
        raise ContextTrustError("Рабочий каталог не признан доверенным.")
    return (info.st_dev, info.st_ino)
