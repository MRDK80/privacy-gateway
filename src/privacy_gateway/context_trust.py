"""Доверенность и принадлежность контекста восстановления (ADR-34, issue #43).

Модуль реализует два независимых механизма, которые вместе делают удаление
рабочего каталога возможным только для контекста, выданного доверенной
областью ``PrivacyGateway``:

1. Аутентификация сериализованного контекста. HMAC-SHA256 по канонической
   форме полезной нагрузки токена. Ключ MAC выводится из ключевого материала
   keystore с доменным разделением и в токен не попадает. Проверка идёт по
   активному и retired ключам (ADR-06, ADR-23), поэтому ротация ключа не
   аннулирует уже выданные контексты.
2. Подтверждение принадлежности рабочего каталога. Canonical containment в
   доверенной базе (доверенная база берётся из конфигурации фасада, не из
   токена), отказ следовать symlink/junction и маркер владения внутри самого
   каталога.

Строкового containment (``startswith`` и эквиваленты) модуль не использует:
сравнение идёт по компонентам пути после ``os.path.realpath``.

Модуль не логирует и не помещает в сообщения ключевой материал, открытый
текст и локальные пути.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Final

from privacy_gateway import keystore as _keystore

#: Доменное разделение MAC контекста. Меняется вместе с версией формата.
CONTEXT_MAC_LABEL: Final = b"privacy-gateway/restore-context/v2"

#: Доменное разделение маркера владения рабочим каталогом.
OWNER_MARKER_LABEL: Final = b"privacy-gateway/workspace-owner/v1"

#: Имя файла маркера владения внутри рабочего каталога.
OWNER_MARKER_NAME: Final = ".pgw-owner"

_MARKER_MODE: Final = 0o600


class ContextTrustError(Exception):
    """Внутренний отказ проверки доверенности контекста.

    Частью публичного контракта не является: фасад транслирует её в
    ``RestoreError`` или ``KeyStoreError`` через exception chaining.
    """


def _derive(key: bytes, label: bytes) -> bytes:
    """Вывести ключ MAC из ключевого материала keystore."""
    return hmac.new(key, label, hashlib.sha256).digest()


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


def sign_payload(payload: dict[str, Any], key: bytes) -> str:
    """Подписать полезную нагрузку контекста активным ключом."""
    mac = hmac.new(
        _derive(key, CONTEXT_MAC_LABEL),
        canonical_payload(payload),
        hashlib.sha256,
    )
    return mac.hexdigest()


def verify_payload(
    payload: dict[str, Any],
    signature: str | None,
    keys: list[bytes],
) -> bool:
    """Проверить подпись контекста по всем доступным ключам.

    Возвращает ``True`` только при совпадении MAC. Сравнение — постоянного
    времени; перебор ключей не завершается досрочно.
    """
    if not signature:
        return False
    message = canonical_payload(payload)
    matched = False
    for key in keys:
        expected = hmac.new(
            _derive(key, CONTEXT_MAC_LABEL), message, hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(expected, signature):
            matched = True
    return matched


def trusted_keys() -> list[bytes]:
    """Вернуть ключевой материал для проверки доверенности (ADR-23)."""
    try:
        return _keystore.get_all_keys()
    except _keystore.KeystoreError as exc:
        raise ContextTrustError("Ключевой материал недоступен.") from exc


def owner_marker_value(handle: str, key: bytes) -> str:
    """Вычислить значение маркера владения для рабочего каталога."""
    mac = hmac.new(
        _derive(key, OWNER_MARKER_LABEL),
        handle.encode("utf-8"),
        hashlib.sha256,
    )
    return mac.hexdigest()


def write_owner_marker(workspace: Path, handle: str, key: bytes) -> None:
    """Записать маркер владения внутрь рабочего каталога.

    Файл создаётся исключительно (``O_EXCL``) с правами только для владельца.
    Секрет в файл не попадает: хранится только MAC от handle.

    Raises:
        OSError: Маркер не удалось создать.
    """
    marker = workspace / OWNER_MARKER_NAME
    descriptor = os.open(
        marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _MARKER_MODE
    )
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(owner_marker_value(handle, key))


def verify_owner_marker(workspace: Path, handle: str, keys: list[bytes]) -> bool:
    """Проверить, что каталог помечен как принадлежащий доверенной области."""
    marker = workspace / OWNER_MARKER_NAME
    try:
        info = marker.lstat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or is_reparse_point(info):
        return False
    try:
        stored = marker.read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return False
    matched = False
    for key in keys:
        if hmac.compare_digest(owner_marker_value(handle, key), stored):
            matched = True
    return matched


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
