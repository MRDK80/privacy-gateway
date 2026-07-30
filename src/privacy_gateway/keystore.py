"""Слой хранения ключа шифрования — Э4.

Публичный контракт:
    get_key()    -> bytes
    create_key() -> bytes
    delete_key() -> None

Hранилище: системный keyring через библиотеку ``keyring``.

Константы (зафиксированы для Э7):
    SERVICE_NAME = "privacy-gateway"
    USERNAME     = "fernet-key"

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

import os

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


class KeystoreError(Exception):
    """Ошибка доступа к защищённому хранилищу ключей."""


class KeyNotFoundError(KeystoreError):
    """Ключ не найден в хранилище."""


def _assert_safe_backend() -> None:
    """Проверить, что активный keyring backend входит в allowlist.

    Поднимает KeystoreError, если backend небезопасен или недоступен.
    """
    env_override = os.environ.get("PGW_KEYRING_BACKEND")
    if env_override:
        return

    backend = keyring.get_keyring()
    fqcn = f"{type(backend).__module__}.{type(backend).__qualname__}"
    if fqcn not in _SAFE_BACKENDS:
        raise KeystoreError(
            f"Unsafe or unavailable keyring backend: {fqcn!r}. "
            "Use a system keyring (SecretService / macOS Keychain / Windows Vault). "
            "For CI/headless set PGW_KEYRING_BACKEND or use "
            "dbus-run-session + gnome-keyring-daemon."
        )


def _get_backend() -> keyring.backend.KeyringBackend:
    """Вернуть актуальный backend (с учётом PGW_KEYRING_BACKEND)."""
    env_override = os.environ.get("PGW_KEYRING_BACKEND")
    if env_override:
        import importlib

        module_name, _, class_name = env_override.rpartition(".")
        mod = importlib.import_module(module_name)
        return getattr(mod, class_name)()
    return keyring.get_keyring()


def get_key() -> bytes:
    """Получить Fernet-ключ из системного хранилища.

    Raises:
        KeystoreError:  Небезопасный или недоступный backend.
        KeyNotFoundError: Ключ ещё не создан (вызовите create_key()).
    """
    _assert_safe_backend()
    value = keyring.get_password(SERVICE_NAME, USERNAME)
    if value is None:
        raise KeyNotFoundError(
            f"No key found for service={SERVICE_NAME!r}, username={USERNAME!r}. "
            "Run 'pgw key create' first."
        )
    return value.encode("latin-1")


def create_key() -> bytes:
    """Сгенерировать и сохранить новый Fernet-ключ.

    Raises:
        KeystoreError: Небезопасный или недоступный backend.

    Returns:
        Новый ключ (bytes).
    """
    _assert_safe_backend()
    key = generate_key()
    keyring.set_password(SERVICE_NAME, USERNAME, key.decode("latin-1"))
    return key


def delete_key() -> None:
    """Удалить Fernet-ключ из хранилища.

    Raises:
        KeystoreError: Небезопасный или недоступный backend.
    """
    _assert_safe_backend()
    keyring.delete_password(SERVICE_NAME, USERNAME)
