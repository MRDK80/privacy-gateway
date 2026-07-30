"""Тесты слоя хранения ключа (Э4).

Все тесты используют mock keyring backend — реальный системный keyring
не задействуется. Ключевой материал на диск не пишется.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import privacy_gateway.keystore as ks
from privacy_gateway.keystore import (
    KeyNotFoundError,
    KeystoreError,
)


class _SafeBackendMock:
    """Mock backend с FQCN из allowlist."""

    __module__ = "keyring.backends.SecretService"
    __qualname__ = "Keyring"

    storage: dict[tuple[str, str], str]

    def get_password(self, service: str, username: str) -> str | None:
        return self.storage.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.storage[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.storage.pop((service, username), None)


@pytest.fixture()
def safe_backend(monkeypatch: pytest.MonkeyPatch) -> _SafeBackendMock:
    backend = _SafeBackendMock()
    backend.storage = {}
    monkeypatch.setattr("keyring.get_keyring", lambda: backend)
    monkeypatch.delenv("PGW_KEYRING_BACKEND", raising=False)
    return backend


# ---------------------------------------------------------------------------
# Базовые операции
# ---------------------------------------------------------------------------

def test_key_stored_and_retrieved(safe_backend: _SafeBackendMock) -> None:
    key = ks.create_key()
    assert isinstance(key, bytes)
    assert len(key) > 0
    retrieved = ks.get_key()
    assert retrieved == key


def test_key_not_found_raises(safe_backend: _SafeBackendMock) -> None:
    with pytest.raises(KeyNotFoundError):
        ks.get_key()


def test_delete_key(safe_backend: _SafeBackendMock) -> None:
    ks.create_key()
    ks.delete_key()
    with pytest.raises(KeyNotFoundError):
        ks.get_key()


# ---------------------------------------------------------------------------
# Fail-closed: небезопасный backend
# ---------------------------------------------------------------------------

class _PlaintextBackendMock:
    """Mock backend с FQCN вне allowlist; методы рабочие."""

    __module__ = "keyrings.alt.file"
    __qualname__ = "PlaintextKeyring"

    def get_password(self, service: str, username: str) -> str | None:  # pragma: no cover
        return None

    def set_password(self, service: str, username: str, password: str) -> None:  # pragma: no cover
        pass

    def delete_password(self, service: str, username: str) -> None:  # pragma: no cover
        pass


@pytest.mark.parametrize(
    "public_fn",
    [
        pytest.param(lambda: ks.create_key(), id="create_key"),
        pytest.param(lambda: ks.get_key(), id="get_key"),
        pytest.param(lambda: ks.delete_key(), id="delete_key"),
    ],
)
def test_plaintext_backend_rejected(
    monkeypatch: pytest.MonkeyPatch,
    public_fn: object,
) -> None:
    """Публичные функции поднимают KeystoreError при небезопасном backend.

    Ключевая проверка: set_password на mock'е НЕ вызывался ни разу.
    """
    plaintext = _PlaintextBackendMock()
    spy = MagicMock(wraps=plaintext)
    monkeypatch.setattr("keyring.get_keyring", lambda: spy)
    monkeypatch.delenv("PGW_KEYRING_BACKEND", raising=False)

    with pytest.raises(KeystoreError):
        public_fn()  # type: ignore[operator]

    spy.set_password.assert_not_called()


# ---------------------------------------------------------------------------
# test_missing_backend_raises — проверяем именно НАШ KeystoreError
# ---------------------------------------------------------------------------

def test_missing_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """KeystoreError должен исходить из нашего кода, а не из библиотеки.

    Прежняя версия была «зелёной» благодаря тому, что
    keyring.backends.fail.Keyring кидал собственное исключение, а не наш
    KeystoreError. Теперь валидация встроена в _get_backend(): любой backend
    вне allowlist (в т.ч. fail.Keyring) приводит к KeystoreError из нашего
    модуля до вызова каких-либо методов backend'а.
    """
    class _FailBackendMock:
        """Имитирует fail.Keyring, но методы никогда не вызываются."""

        __module__ = "keyring.backends.fail"
        __qualname__ = "Keyring"

        def get_password(self, service: str, username: str) -> str | None:  # pragma: no cover
            raise RuntimeError("should not be called")

        def set_password(self, service: str, username: str, password: str) -> None:  # pragma: no cover
            raise RuntimeError("should not be called")

        def delete_password(self, service: str, username: str) -> None:  # pragma: no cover
            raise RuntimeError("should not be called")

    monkeypatch.setattr("keyring.get_keyring", lambda: _FailBackendMock())
    monkeypatch.delenv("PGW_KEYRING_BACKEND", raising=False)

    # Убеждаемся, что поднимается именно наш KeystoreError
    with pytest.raises(KeystoreError) as exc_info:
        ks.get_key()
    assert "Unsafe or unavailable keyring backend" in str(exc_info.value)
    assert "keyring.backends.fail.Keyring" in str(exc_info.value)

    with pytest.raises(KeystoreError) as exc_info:
        ks.create_key()
    assert "Unsafe or unavailable keyring backend" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Нет ключевого материала на диске
# ---------------------------------------------------------------------------

def test_no_key_material_on_disk() -> None:
    import subprocess
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        text=True,
        cwd=".",
    )
    tracked_files = result.stdout.split("\0")
    suspicious = [
        f for f in tracked_files
        if any(kw in f.lower() for kw in ["fernet", ".key", "secret.json", "keyring"])
        and f.endswith((".key", ".pem", ".der"))
    ]
    assert suspicious == [], f"Suspicious key files tracked by git: {suspicious}"
