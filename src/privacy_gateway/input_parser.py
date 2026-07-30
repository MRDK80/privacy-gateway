"""Безопасное чтение текстового входа — Этап Э2.

Поддерживаются .txt-файлы и stdin ("-").
Автоугадывание кодировок не применяется.
"""

from __future__ import annotations

import sys
from pathlib import Path

from privacy_gateway.models import (
    EncodingError,
    InputError,
    InputSource,
    InputText,
    UnsupportedInputError,
)

# Консервативный лимит по умолчанию — 512 КБ.
# Значение также задаётся в config.example/safety.yaml (max_input_bytes).
_DEFAULT_MAX_BYTES: int = 524_288


def read_input(
    source: str | Path,
    *,
    encoding: str = "utf-8",
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> InputText:
    """Прочитать текстовый вход из файла или stdin.

    Args:
        source: Путь к .txt-файлу или "-" для чтения из stdin.
        encoding: Кодировка. Разрешены utf-8 (default), utf-8-sig, cp1251.
            cp1251 применяется ТОЛЬКО при явной передаче параметра.
        max_bytes: Максимальный размер входа в байтах.

    Returns:
        InputText с текстом, источником и использованной кодировкой.

    Raises:
        UnsupportedInputError: Файл не .txt или кодировка не поддерживается.
        EncodingError: Ошибка декодирования или пустой stdin.
        InputError: Превышен лимит размера или файл недоступен.
    """
    _validate_encoding(encoding)

    if source == "-":
        return _read_stdin(encoding=encoding, max_bytes=max_bytes)

    return _read_file(Path(source), encoding=encoding, max_bytes=max_bytes)


_ALLOWED_ENCODINGS = frozenset({"utf-8", "utf-8-sig", "cp1251", "windows-1251"})


def _validate_encoding(encoding: str) -> None:
    enc = encoding.lower().replace("_", "-")
    if enc not in _ALLOWED_ENCODINGS:
        raise UnsupportedInputError(
            f"Unsupported encoding: {encoding!r}. "
            f"Allowed: utf-8, utf-8-sig, cp1251."
        )


def _read_file(path: Path, *, encoding: str, max_bytes: int) -> InputText:
    if path.suffix.lower() != ".txt":
        raise UnsupportedInputError(
            f"Only .txt files are supported (got suffix {path.suffix!r})."
        )

    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise InputError(f"Cannot access file: {path.name!r}.") from exc

    if file_size > max_bytes:
        raise InputError(
            f"File {path.name!r} exceeds size limit "
            f"({file_size} > {max_bytes} bytes)."
        )

    # Нормализуем UTF-8 BOM: если кодировка utf-8 и файл начинается с BOM,
    # переключаемся на utf-8-sig для прозрачной обработки.
    effective_encoding = encoding
    if effective_encoding.lower() in ("utf-8", "utf_8"):
        try:
            with path.open("rb") as fb:
                bom = fb.read(3)
            if bom == b"\xef\xbb\xbf":
                effective_encoding = "utf-8-sig"
        except OSError as exc:
            raise InputError(f"Cannot read file: {path.name!r}.") from exc

    try:
        text = path.read_text(encoding=effective_encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise EncodingError(
            f"Failed to decode {path.name!r} as {effective_encoding!r}."
        ) from exc
    except OSError as exc:
        raise InputError(f"Cannot read file: {path.name!r}.") from exc

    return InputText(
        text=text,
        source=InputSource.FILE,
        encoding=effective_encoding,
        path=path,
    )


def _read_stdin(*, encoding: str, max_bytes: int) -> InputText:
    try:
        raw = sys.stdin.buffer.read(max_bytes + 1)
    except OSError as exc:
        raise InputError("Cannot read from stdin.") from exc

    if not raw:
        raise EncodingError("stdin is empty or not available.")

    if len(raw) > max_bytes:
        raise InputError(
            f"stdin input exceeds size limit ({max_bytes} bytes)."
        )

    effective_encoding = encoding
    if effective_encoding.lower() in ("utf-8", "utf_8") and raw.startswith(
        b"\xef\xbb\xbf"
    ):
        effective_encoding = "utf-8-sig"

    try:
        text = raw.decode(effective_encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise EncodingError(
            f"Failed to decode stdin as {effective_encoding!r}."
        ) from exc

    return InputText(
        text=text,
        source=InputSource.STDIN,
        encoding=effective_encoding,
        path=None,
    )
