"""Тесты слоя хранения ключа (Э4).

Все тесты используют mock keyring backend — реальный системный keyring
не задействуется. Ключевой материал на диск не пишется.
"""

from __future__ import annotations

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

    storage: dict[tuple[str, str], str] = {}

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
    monkeypatch.setattr("keyring.get_password", backend.get_password)
    monkeypatch.setattr("keyring.set_password", backend.set_password)
    monkeypatch.setattr("keyring.delete_password", backend.delete_password)
    monkeypatch.delenv("PGW_KEYRING_BACKEND", raising=False)
    return backend


def test_key_stored_and_retrieved(safe_backend: _SafeBackendMock) -> None:
    key = ks.create_key()
    assert isinstance(key, bytes)
    assert len(key) > 0
    retrieved = ks.get_key()
    assert retrieved == key


def test_missing_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _UnsafeBackend:
        __module__ = "keyring.backends.fail"
        __qualname__ = "Keyring"

    monkeypatch.setattr("keyring.get_keyring", lambda: _UnsafeBackend())
    monkeypatch.delenv("PGW_KEYRING_BACKEND", raising=False)
    with pytest.raises(KeystoreError):
        ks.get_key()
    with pytest.raises(KeystoreError):
        ks.create_key()


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


def test_key_not_found_raises(safe_backend: _SafeBackendMock) -> None:
    with pytest.raises(KeyNotFoundError):
        ks.get_key()


def test_delete_key(safe_backend: _SafeBackendMock) -> None:
    ks.create_key()
    ks.delete_key()
    with pytest.raises(KeyNotFoundError):
        ks.get_key()
