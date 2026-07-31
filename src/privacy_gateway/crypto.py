"""Слой криптографии — Э4 / Э8.

Публичный контракт:
    encrypt(value, key)        -> bytes   (один ключ, совместимость Э4)
    decrypt(data, key)         -> str     (один ключ, совместимость Э4)
    encrypt_multi(value, keys) -> bytes   (MultiFernet, шифрует первым ключом)
    decrypt_multi(data, keys)  -> str     (MultiFernet, пробует все ключи)
    generate_key()             -> bytes

Алгоритм: Fernet (AES-128-CBC + HMAC-SHA256, аутентифицированное
симметричное шифрование). Ключ — 32 байта в base64url, генерируется
Fernet.generate_key().

MultiFernet (ADR-23):
    encrypt_multi шифрует первым ключом списка (keys[0]).
    decrypt_multi пробует каждый ключ по очереди — это даёт обратную
    совместимость: манифесты, созданные до ротации, остаются читаемы.

Ключ НИКОГДА не логируется, не помещается в repr/str, не пишется
в файлы репозитория.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from privacy_gateway.models import ConfigurationError


class DecryptionError(Exception):
    """Не удалось расшифровать данные: неверный ключ или повреждённый шифртекст."""


def generate_key() -> bytes:
    """Сгенерировать новый Fernet-ключ (32 random bytes в base64url)."""
    return Fernet.generate_key()


def _make_fernet(key: bytes) -> Fernet:
    """Вернуть Fernet-инстанцию, подняв ConfigurationError при невалидном ключе."""
    try:
        return Fernet(key)
    except (ValueError, Exception) as exc:
        raise ConfigurationError(f"Invalid Fernet key: {exc}") from exc


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
    return _make_fernet(key).encrypt(value.encode("utf-8"))


def decrypt(data: bytes, key: bytes) -> str:
    """Расшифровать шифртекст *data* ключом *key*.

    Args:
        data: Fernet-token (результат encrypt()).
        key:  Fernet-ключ.

    Returns:
        Исходная строка (UTF-8).

    Raises:
        DecryptionError:    Неверный ключ или повреждённый шифртекст.
        ConfigurationError: Если *key* не является валидным Fernet-ключом.
    """
    try:
        return _make_fernet(key).decrypt(data).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError(
            "Decryption failed: wrong key or corrupted ciphertext"
        ) from exc


def encrypt_multi(value: str, keys: list[bytes]) -> bytes:
    """Зашифровать строку через MultiFernet (активный ключ — первый).

    Args:
        value: Открытый текст (UTF-8).
        keys:  Список Fernet-ключей. Первый — активный (для шифрования),
               остальные — для расшифрования старых манифестов.

    Returns:
        Шифртекст bytes.

    Raises:
        ConfigurationError: Пустой список ключей или невалидный ключ.
    """
    if not keys:
        raise ConfigurationError("encrypt_multi requires at least one key")
    mf = MultiFernet([_make_fernet(k) for k in keys])
    return mf.encrypt(value.encode("utf-8"))


def decrypt_multi(data: bytes, keys: list[bytes]) -> str:
    """Расшифровать шифртекст через MultiFernet (пробует все ключи по очереди).

    Обеспечивает обратную совместимость: манифесты, зашифрованные
    любым ключом списка, будут успешно расшифрованы.

    Args:
        data: Fernet-token.
        keys: Список Fernet-ключей (не пустой).

    Returns:
        Исходная строка (UTF-8).

    Raises:
        DecryptionError:    Ни один ключ не подошёл.
        ConfigurationError: Пустой список ключей или невалидный ключ.
    """
    if not keys:
        raise ConfigurationError("decrypt_multi requires at least one key")
    mf = MultiFernet([_make_fernet(k) for k in keys])
    try:
        return mf.decrypt(data).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError(
            "Decryption failed: no key matched the ciphertext"
        ) from exc
