"""Тесты слоя криптографии (Э4)."""

from __future__ import annotations

import pytest

from privacy_gateway.crypto import DecryptionError, decrypt, encrypt, generate_key
from privacy_gateway.models import ConfigurationError


def test_encrypt_decrypt_roundtrip() -> None:
    key = generate_key()
    for value in ["hello", "Привет мир", "test@example.com", "😀🔑"]:
        assert decrypt(encrypt(value, key), key) == value


def test_wrong_key_cannot_decrypt() -> None:
    key1 = generate_key()
    key2 = generate_key()
    ciphertext = encrypt("secret", key1)
    with pytest.raises(DecryptionError):
        decrypt(ciphertext, key2)


def test_tampered_ciphertext_detected() -> None:
    key = generate_key()
    ciphertext = bytearray(encrypt("value", key))
    ciphertext[-1] ^= 0xFF
    with pytest.raises(DecryptionError):
        decrypt(bytes(ciphertext), key)


def test_ciphertext_differs_for_same_plaintext() -> None:
    key = generate_key()
    ct1 = encrypt("same", key)
    ct2 = encrypt("same", key)
    assert ct1 != ct2


def test_key_not_in_repr() -> None:
    key = generate_key()
    ciphertext = encrypt("check", key)
    key_str = key.decode("latin-1")
    assert key_str not in repr(ciphertext)
    assert key_str not in str(ciphertext)


def test_invalid_key_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        encrypt("x", b"not-a-valid-fernet-key")

    with pytest.raises(ConfigurationError):
        decrypt(b"garbage", b"not-a-valid-fernet-key")
