"""Слой хранения ключа шифрования — Э4 / Э8.

Публичный контракт:
    get_key()          -> bytes               (один активный ключ для шифрования)
    get_all_keys()     -> list[bytes]         (все ключи, первый — активный, для MultiFernet)
    key_exists()       -> bool                (проверка без вывода значения)
    create_key(force)  -> bytes               (создать и сохранить новый ключ)
    rotate_key()       -> bytes               (ротация: новый ключ становится первым)
    delete_old_keys()  -> int                 (удалить все ключи кроме первого)
    delete_key()       -> None                (удалить все ключи полностью)

Хранилище: системный keyring через библиотеку ``keyring``.

Константы (зафиксированы для Э7):
    SERVICE_NAME = "privacy-gateway"
    USERNAME     = "fernet-key"

Схема хранения нескольких ключей (ADR-23):
    Ключи хранятся как отдельные записи keyring:
      SERVICE_NAME / "fernet-key"    — активный (первый) ключ
      SERVICE_NAME / "fernet-key-1"  — второй старый ключ
      SERVICE_NAME / "fernet-key-2"  — третий старый ключ
      ...
    При ротации: текущий "fernet-key" становится "fernet-key-N",
    новый ключ записывается как "fernet-key" (активный).
    Старый USERNAME="fernet-key" всегда указывает на активный ключ.
    Кодировка latin-1 и константы сервиса не меняются.

Fail closed:
    Если активный backend отсутствует, недоступен или является
    небезопасной (plaintext / in-memory) реализацией — поднимается
    KeystoreError, запись ключа не производится.

Allowlist безопасных backend-ов:
    - keyring.backends.SecretService.Keyring
    - keyring.backends.macOS.Keyring
    - keyring.backends.Windows.WinVaultKeyring

CI / headless-сценарий:
    Установить переменную окружения PGW_KEYRING_BACKEND в FQCN
    нужного backend (например для тестов: keyring.backends.fail.Keyring
    заменяется mock-объектом). Для headless Linux:
        dbus-run-session -- bash -c \
            'echo -n "" | gnome-keyring-daemon --unlock && pgw ...'
    Приложение и демон должны работать в одной D-Bus сессии.

Ключ НИКОГДА не логируется, не помещается в repr/str и не записывается
в файлы репозитория.
"""

from __future__ import annotations

import importlib
import os
from typing import cast

import keyring
import keyring.backend

from privacy_gateway.crypto import generate_key

SERVICE_NAME: str = "privacy-gateway"
USERNAME: str = "fernet-key"

_SAFE_BACKENDS: frozenset[str] = frozenset(
    {
        "keyring.backends.SecretService.Keyring",
        "keyring.backends.macOS.Keyring",
        "keyring.backends.Windows.WinVaultKeyring",
    }
)

# Максимальное количество старых ключей, хранимых при ротации
_MAX_OLD_KEYS: int = 9


class KeystoreError(Exception):
    """Ошибка доступа к защищённому хранилищу ключей."""


class KeyNotFoundError(KeystoreError):
    """Ключ не найден в хранилище."""


class KeyExistsError(KeystoreError):
    """Ключ уже существует; перезапись без --force не выполняется."""


def _get_backend() -> keyring.backend.KeyringBackend:
    """Вернуть проверенный backend.

    Если переменная PGW_KEYRING_BACKEND задана — инстанциировать backend
    по FQCN из неё; иначе использовать системный.

    В обоих случаях backend проверяется по allowlist _SAFE_BACKENDS.
    Поднимает KeystoreError, если backend небезопасен или недоступен.
    """
    env_override = os.environ.get("PGW_KEYRING_BACKEND")
    if env_override:
        module_name, _, class_name = env_override.rpartition(".")
        mod = importlib.import_module(module_name)
        backend: keyring.backend.KeyringBackend = cast(
            keyring.backend.KeyringBackend, getattr(mod, class_name)()
        )
    else:
        backend = keyring.get_keyring()

    fqcn = f"{type(backend).__module__}.{type(backend).__qualname__}"
    if fqcn not in _SAFE_BACKENDS:
        raise KeystoreError(
            f"Unsafe or unavailable keyring backend: {fqcn!r}. "
            "Use a system keyring (SecretService / macOS Keychain / Windows Vault). "
            "For CI/headless set PGW_KEYRING_BACKEND or use "
            "dbus-run-session + gnome-keyring-daemon."
        )
    return backend


def _old_username(index: int) -> str:
    """Вернуть имя записи для i-го старого ключа (index >= 1)."""
    return f"{USERNAME}-{index}"


def get_key() -> bytes:
    """Получить активный Fernet-ключ из системного хранилища.

    Raises:
        KeystoreError:    Небезопасный или недоступный backend.
        KeyNotFoundError: Ключ ещё не создан (вызовите create_key()).
    """
    backend = _get_backend()
    value = backend.get_password(SERVICE_NAME, USERNAME)
    if value is None:
        raise KeyNotFoundError(
            f"No key found for service={SERVICE_NAME!r}, username={USERNAME!r}. "
            "Run 'pgw key create' first."
        )
    return value.encode("latin-1")


def get_all_keys() -> list[bytes]:
    """Получить все ключи для MultiFernet.

    Первый элемент — активный (для шифрования), остальные — старые
    (для расшифрования манифестов, созданных до ротации).

    Raises:
        KeystoreError:    Небезопасный или недоступный backend.
        KeyNotFoundError: Ни одного ключа нет.
    """
    backend = _get_backend()
    active_value = backend.get_password(SERVICE_NAME, USERNAME)
    if active_value is None:
        raise KeyNotFoundError(
            f"No key found for service={SERVICE_NAME!r}, username={USERNAME!r}. "
            "Run 'pgw key create' first."
        )
    keys: list[bytes] = [active_value.encode("latin-1")]
    for i in range(1, _MAX_OLD_KEYS + 1):
        old_value = backend.get_password(SERVICE_NAME, _old_username(i))
        if old_value is None:
            break
        keys.append(old_value.encode("latin-1"))
    return keys


def key_exists() -> bool:
    """Проверить наличие активного ключа без вывода значения.

    Raises:
        KeystoreError: Небезопасный или недоступный backend.
    """
    backend = _get_backend()
    return backend.get_password(SERVICE_NAME, USERNAME) is not None


def create_key(*, force: bool = False) -> bytes:
    """Сгенерировать и сохранить новый Fernet-ключ.

    Без force=True: если ключ уже есть, поднимает KeyExistsError.
    С force=True: перезаписывает существующий ключ без сохранения старых.

    Внимание: force=True делает все ранее созданные манифесты
    нечитаемыми. Для ротации используйте rotate_key().

    Raises:
        KeyExistsError: Ключ уже существует, force=False.
        KeystoreError:  Небезопасный или недоступный backend.

    Returns:
        Новый ключ (bytes). Значение не печатается нигде.
    """
    backend = _get_backend()
    if not force:
        existing = backend.get_password(SERVICE_NAME, USERNAME)
        if existing is not None:
            raise KeyExistsError(
                "A key already exists. Use --force to overwrite "
                "(WARNING: all existing manifests will become unreadable). "
                "For safe rotation use 'pgw key rotate'."
            )
    key = generate_key()
    backend.set_password(SERVICE_NAME, USERNAME, key.decode("latin-1"))
    return key


def rotate_key() -> bytes:
    """Ротация ключа: новый ключ становится активным, старый — сдвигается.

    Процедура (ADR-23):
    1. Читать все существующие старые ключи.
    2. Сдвинуть текущий активный ключ в "fernet-key-1" (старые — дальше).
    3. Записать новый ключ в "fernet-key" (активный).
    Обратная совместимость: манифесты, созданные до ротации,
    читаются через MultiFernet (старые ключи остаются в хранилище).

    Raises:
        KeyNotFoundError: Активный ключ отсутствует (вызовите create_key() сначала).
        KeystoreError:    Небезопасный или недоступный backend.

    Returns:
        Новый активный ключ (bytes). Значение не печатается нигде.
    """
    backend = _get_backend()

    active_value = backend.get_password(SERVICE_NAME, USERNAME)
    if active_value is None:
        raise KeyNotFoundError(
            f"No active key found for service={SERVICE_NAME!r}. "
            "Run 'pgw key create' first."
        )

    # Собрать все существующие старые ключи
    old_values: list[str] = []
    for i in range(1, _MAX_OLD_KEYS + 1):
        val = backend.get_password(SERVICE_NAME, _old_username(i))
        if val is None:
            break
        old_values.append(val)

    # Сдвинуть активный ключ в "fernet-key-1", остальные дальше
    all_old = [active_value] + old_values
    for idx, val in enumerate(all_old, start=1):
        if idx <= _MAX_OLD_KEYS:
            backend.set_password(SERVICE_NAME, _old_username(idx), val)

    # Записать новый активный ключ
    new_key = generate_key()
    backend.set_password(SERVICE_NAME, USERNAME, new_key.decode("latin-1"))
    return new_key


def delete_old_keys() -> int:
    """Удалить все старые ключи, оставив только активный.

    Предупреждение: манифесты, зашифрованные удалёнными ключами,
    станут невосстановимыми. Перед вызовом убедитесь, что все нужные
    манифесты перешифрованы на активный ключ.

    Raises:
        KeystoreError: Небезопасный или недоступный backend.

    Returns:
        Количество удалённых старых ключей.
    """
    backend = _get_backend()
    deleted = 0
    for i in range(1, _MAX_OLD_KEYS + 1):
        val = backend.get_password(SERVICE_NAME, _old_username(i))
        if val is None:
            break
        try:
            backend.delete_password(SERVICE_NAME, _old_username(i))
            deleted += 1
        except Exception:  # noqa: BLE001
            pass
    return deleted


def delete_key() -> None:
    """Удалить все ключи (активный и старые) из хранилища.

    Raises:
        KeystoreError: Небезопасный или недоступный backend.
    """
    backend = _get_backend()
    delete_old_keys()
    backend.delete_password(SERVICE_NAME, USERNAME)
