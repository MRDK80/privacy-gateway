"""Слой зашифрованного манифеста — Э4.

Публичный контракт:
    build_manifest(records, original_values, key) -> list[ManifestEntry]
    save_manifest(entries, path)                  -> None
    load_manifest(path, key)                      -> list[ManifestEntry]

Манифест хранится в JSON (UTF-8). encrypted_value сериализуется как hex.
Исходные значения в открытом виде в файле отсутствуют.
Ключ в манифест не записывается ни в каком виде.

При чтении манифеста, зашифрованного другим ключом, поднимается
DecryptionError (из crypto.py).

Ротация ключа:
    Манифесты зашифрованы конкретным ключом. При замене ключа старые манифесты
    становятся нечитаемы (load_manifest поднимет DecryptionError). Для ротации
    необходимо: 1) загрузить со старым ключом, 2) пересохранить с новым.
"""

from __future__ import annotations

import json
from pathlib import Path

from privacy_gateway.crypto import decrypt, encrypt
from privacy_gateway.models import ManifestEntry, TokenRecord


def build_manifest(
    records: list[TokenRecord],
    original_values: list[str],
    key: bytes,
) -> list[ManifestEntry]:
    """Собрать манифест из списка TokenRecord и исходных значений.

    Args:
        records:         Список TokenRecord (результат tokenize()).
        original_values: Исходные значения параллельно records.
        key:             Fernet-ключ шифрования.

    Returns:
        Список ManifestEntry с заполненным encrypted_value.

    Raises:
        ValueError: Длины records и original_values не совпадают.
        ConfigurationError: Невалидный ключ.
    """
    if len(records) != len(original_values):
        raise ValueError(
            f"records ({len(records)}) and original_values "
            f"({len(original_values)}) must have the same length"
        )
    entries: list[ManifestEntry] = []
    for record, value in zip(records, original_values):
        ciphertext = encrypt(value, key)
        entries.append(
            ManifestEntry(
                token=record.token,
                entity_type=record.entity_type,
                fingerprint=record.fingerprint,
                encrypted_value=ciphertext,
                secret_kind=record.secret_kind,
            )
        )
    return entries


def save_manifest(entries: list[ManifestEntry], path: Path) -> None:
    """Записать манифест в JSON-файл (UTF-8).

    Исходные значения в файле отсутствуют; encrypted_value хранится как hex.

    Args:
        entries: Список ManifestEntry.
        path:    Путь к файлу (создаётся или перезаписывается).
    """
    data = [entry.to_dict() for entry in entries]
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_manifest(path: Path, key: bytes) -> list[ManifestEntry]:
    """Прочитать манифест из JSON-файла и проверить расшифровку.

    Проверяет каждую запись: если хотя бы одна не расшифровывается —
    поднимает DecryptionError.

    Args:
        path: Путь к файлу манифеста.
        key:  Fernet-ключ.

    Returns:
        Список ManifestEntry.

    Raises:
        DecryptionError:    Неверный ключ или повреждённые данные.
        ConfigurationError: Невалидный ключ.
        json.JSONDecodeError: Файл не является валидным JSON.
    """
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    entries: list[ManifestEntry] = []
    for item in data:
        entry = ManifestEntry.from_dict(item)
        decrypt(entry.encrypted_value, key)
        entries.append(entry)
    return entries


def decrypt_manifest_entry(entry: ManifestEntry, key: bytes) -> str:
    """Расшифровать значение одной записи манифеста.

    Args:
        entry: ManifestEntry.
        key:   Fernet-ключ.

    Returns:
        Исходное значение.

    Raises:
        DecryptionError: Неверный ключ или повреждённый шифртекст.
    """
    return decrypt(entry.encrypted_value, key)
