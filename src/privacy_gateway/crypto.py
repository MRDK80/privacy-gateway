"""Слой криптографии — Э4.

Публичный контракт:
    encrypt(value, key) -> bytes
    decrypt(data, key)  -> str
    generate_key()      -> bytes

Алгоритм: Fernet (AES-128-CBC + HMAC-SHA256, аутентифицированное
симметричное шифрование). Ключ — 32 байта в base64url, генерируется
Fernet.generate_key().

Ключ НИКОГДА не логируется, не помещается в repr/str, не пишется
в файлы репозитория.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from privacy_gateway.models import ConfigurationError


class DecryptionError(Exception):
    """Не удалось расшифровать данные: неверный ключ или повреждённый шифртекст."""


def generate_key() -> bytes:
    """Сгенерировать новый Fernet-ключ (32 random bytes в base64url)."""
    return Fernet.generate_key()


def encrypt(value: str, key: bytes) -> bytes:
    """Зашифровать строку *value* ключом *key*.

    Args:
        value: Открытый текст (UTF-8).
        key:   Fernet-ключ (результат generate_key()).

    Returns:
        Шифртекст в виде bytes (Fernet-token).

    Raises:
        ConfigurationError: Если *key* не является валидным Fernet-ключом.
    """
    try:
        f = Fernet(key)
    except (ValueError, Exception) as exc:
        raise ConfigurationError(f"Invalid Fernet key: {exc}") from exc
    return f.encrypt(value.encode("utf-8"))


def decrypt(data: bytes, key: bytes) -> str:
    """Расшифровать шифртекст *data* ключом *key*.

    Args:
        data: Fernet-token (результат encrypt()).
        key:  Fernet-ключ.

    Returns:
        Исходная строка (UTF-8).

    Raises:
        DecryptionError: Неверный ключ или повреждённый шифртекст.
        ConfigurationError: Если *key* не является валидным Fernet-ключом.
    """
    try:
        f = Fernet(key)
    except (ValueError, Exception) as exc:
        raise ConfigurationError(f"Invalid Fernet key: {exc}") from exc
    try:
        return f.decrypt(data).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError(
            "Decryption failed: wrong key or corrupted ciphertext"
        ) from exc
