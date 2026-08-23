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

Публикация manifest.json (#45, ADR-45):
    save_manifest() сериализует документ целиком, пишет его во временный
    файл в каталоге назначения и публикует одним os.replace().

    Политика — atomic visibility only: пустой или частично записанный
    manifest.json не наблюдается, при отказе write/close/replace ранее
    существовавший файл остаётся неизменным, временный файл удаляется
    best-effort и его удаление не подменяет первичную ошибку.

    fsync файла и каталога НЕ выполняется: crash durability не обещается.
    Атомарна публикация одного файла, а не набора артефактов prepare.
    Порядок публикации в pipeline не меняется (manifest.json → prompt.txt
    → route.json; route.json содержит manifest_sha256). Коллизия
    «существует только manifest.json» относится к #48 и здесь не
    изменяется.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from privacy_gateway.crypto import decrypt, encrypt
from privacy_gateway.models import ManifestEntry, TokenRecord

# Префикс/суффикс временного файла публикации манифеста.
# Имя не совпадает с manifest.json и не воспринимается как манифест.
_TMP_PREFIX = ".manifest-"
_TMP_SUFFIX = ".tmp"


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


def _publish_atomically(path: Path, serialized: str) -> None:
    """Опубликовать *serialized* по пути *path* атомарной заменой.

    Временный файл создаётся в каталоге назначения (одна файловая
    система), закрывается ровно один раз и публикуется os.replace().
    До успешного replace содержимое *path* не меняется. Cleanup
    временного файла best-effort и не маскирует первичную ошибку.

    Args:
        path:       Итоговый путь публикации.
        serialized: Полностью сформированное содержимое файла.

    Raises:
        OSError: Отказ создания, записи, закрытия или замены файла.
    """
    tmp_name: str | None = None
    published = False
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=_TMP_PREFIX,
            suffix=_TMP_SUFFIX,
        )
        try:
            # newline по умолчанию — как у Path.write_text(),
            # чтобы формат файла не изменился ни на одной платформе.
            stream = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        with stream:
            stream.write(serialized)
        os.replace(tmp_name, path)
        published = True
    finally:
        if tmp_name is not None and not published:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def save_manifest(entries: list[ManifestEntry], path: Path) -> None:
    """Записать манифест в JSON-файл (UTF-8) атомарной публикацией.

    Исходные значения в файле отсутствуют; encrypted_value хранится как hex.
    Документ сериализуется полностью до создания временного файла, поэтому
    ошибка сериализации не создаёт временных файлов и не меняет
    существующий манифест.

    Args:
        entries: Список ManifestEntry.
        path:    Путь к файлу (создаётся или заменяется целиком).

    Raises:
        OSError: Отказ создания, записи, закрытия или замены файла.
    """
    data = [entry.to_dict() for entry in entries]
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    _publish_atomically(path, serialized)


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
