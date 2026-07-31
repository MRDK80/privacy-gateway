"""Keystore — управление Fernet-ключами через системный keyring (Э8).

Публичный контракт:
    get_key() -> bytes (один активный ключ для шифрования)
    get_all_keys() -> list[bytes] (все ключи, первый — активный,
        для MultiFernet)
    key_exists() -> bool (проверка без вывода значения)
    create_key(force) -> bytes (создать и сохранить новый ключ)
    rotate_key() -> bytes (ротация: новый активный, старый для чтения)

Все методы выбрасывают подклассы KeystoreError при сбое.
Адрес keystore (service/username) не выводится в сообщениях об ошибках.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import keyring

from privacy_gateway.crypto import generate_key

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

_SERVICE = "privacy_gateway"
_ACTIVE_KEY = "fernet_key"          # имя активного ключа
_RETIRED_KEY = "fernet_key_retired" # имя старого ключа (после ротации)


# ---------------------------------------------------------------------------
# Иерархия исключений
# ---------------------------------------------------------------------------


class KeystoreError(Exception):
    """Базовое исключение для всех ошибок keystore."""


class KeyNotFoundError(KeystoreError):
    """Ключ не найден в keyring."""


class KeyExistsError(KeystoreError):
    """Ключ уже существует (вызов create_key без force=True)."""


class UnsafeBackendError(KeystoreError):
    """Keyring использует небезопасный backend (plaintext-файл и т.п.)."""


# ---------------------------------------------------------------------------
# Внутренние хелперы
# ---------------------------------------------------------------------------


def _get_raw(name: str) -> str | None:
    """Получить строку из keyring или None."""
    return keyring.get_password(_SERVICE, name)


def _set_raw(name: str, value: str) -> None:
    """Сохранить строку в keyring."""
    keyring.set_password(_SERVICE, name, value)


def _delete_raw(name: str) -> None:
    """Удалить запись из keyring (игнорировать отсутствие)."""
    try:
        keyring.delete_password(_SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass


def _encode_keys(keys: list[bytes]) -> str:
    """Сериализовать список ключей в JSON-строку."""
    return json.dumps([k.decode() for k in keys])


def _decode_keys(raw: str) -> list[bytes]:
    """Десериализовать JSON-строку в список ключей."""
    data = json.loads(raw)
    if isinstance(data, str):
        # Обратная совместимость: одиночный ключ как строка
        return [data.encode()]
    return [item.encode() for item in data]


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


def key_exists() -> bool:
    """Вернуть True, если активный ключ присутствует в keyring."""
    return _get_raw(_ACTIVE_KEY) is not None


def get_key() -> bytes:
    """Вернуть один активный ключ для шифрования.

    Raises:
        KeyNotFoundError: если ключ не найден.
    """
    raw = _get_raw(_ACTIVE_KEY)
    if raw is None:
        raise KeyNotFoundError(
            "Активный ключ не найден. Запустите 'pgw key create'."
        )
    keys = _decode_keys(raw)
    return keys[0]


def get_all_keys() -> list[bytes]:
    """Вернуть все ключи: [активный, ...старые] для MultiFernet.

    Raises:
        KeyNotFoundError: если активный ключ не найден.
    """
    raw = _get_raw(_ACTIVE_KEY)
    if raw is None:
        raise KeyNotFoundError(
            "Активный ключ не найден. Запустите 'pgw key create'."
        )
    keys = _decode_keys(raw)

    # Добавить retired-ключ, если есть
    retired_raw = _get_raw(_RETIRED_KEY)
    if retired_raw is not None:
        retired = _decode_keys(retired_raw)
        keys.extend(retired)

    return keys


def create_key(*, force: bool = False) -> bytes:
    """Создать новый Fernet-ключ и сохранить в keyring.

    Args:
        force: если True — перезаписать существующий ключ.

    Returns:
        Новый ключ в виде bytes.

    Raises:
        KeyExistsError: если ключ уже есть и force=False.
    """
    if not force and key_exists():
        raise KeyExistsError(
            "Ключ уже существует. Используйте force=True для перезаписи."
        )
    new_key = generate_key()
    _set_raw(_ACTIVE_KEY, _encode_keys([new_key]))
    _delete_raw(_RETIRED_KEY)
    return new_key


def rotate_key() -> bytes:
    """Ротация: новый ключ становится активным, старый уходит в retired.

    Raises:
        KeyNotFoundError: если активного ключа нет.
    """
    current_keys = get_all_keys()  # выбросит KeyNotFoundError если нет ключа
    new_key = generate_key()
    # Сохранить старые ключи как retired
    _set_raw(_RETIRED_KEY, _encode_keys(current_keys))
    # Новый ключ — единственный активный
    _set_raw(_ACTIVE_KEY, _encode_keys([new_key]))
    return new_key
