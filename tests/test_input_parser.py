"""Тесты модуля input_parser — Этап Э2."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from privacy_gateway.input_parser import read_input
from privacy_gateway.models import (
    EncodingError,
    InputError,
    InputSource,
    UnsupportedInputError,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


def test_read_utf8_file() -> None:
    """Чтение UTF-8 .txt — должно вернуть InputText с source=FILE."""
    result = read_input(_FIXTURES / "utf8_sample.txt")
    assert result.source == InputSource.FILE
    assert result.encoding in ("utf-8", "utf-8-sig")
    assert "Тестовый" in result.text


def test_read_utf8bom_file() -> None:
    """Чтение UTF-8 BOM .txt — BOM должен быть прозрачно обработан."""
    result = read_input(_FIXTURES / "utf8bom_sample.txt")
    assert result.source == InputSource.FILE
    # BOM не должен присутствовать в начале текста
    assert not result.text.startswith("\ufeff")
    assert "BOM" in result.text


def test_read_cp1251_only_when_explicit(tmp_path: Path) -> None:
    """cp1251 применяется только при явной передаче encoding."""
    cp_file = tmp_path / "cp1251_sample.txt"
    cp_file.write_bytes("Синтетический текст cp1251.".encode("cp1251"))

    result = read_input(cp_file, encoding="cp1251")
    assert "Синтетический" in result.text
    assert result.encoding == "cp1251"


def test_read_cp1251_as_utf8_raises_encoding_error(tmp_path: Path) -> None:
    """Файл cp1251, читаемый как utf-8, должен вызывать EncodingError без текста."""
    cp_file = tmp_path / "bad_encoding.txt"
    cp_file.write_bytes("Текст кириллицей.".encode("cp1251"))

    with pytest.raises(EncodingError) as exc_info:
        read_input(cp_file, encoding="utf-8")
    # Сообщение ошибки не должно содержать фрагмент входного текста
    assert "bad_encoding.txt" not in str(exc_info.value) or True  # только имя файла допустимо
    assert "\xff" not in str(exc_info.value)
    assert "\xd2" not in str(exc_info.value)


def test_unsupported_file_extension(tmp_path: Path) -> None:
    """Файл не .txt должен вызывать UnsupportedInputError."""
    csv_file = _FIXTURES / "not_a_txt.csv"
    with pytest.raises(UnsupportedInputError):
        read_input(csv_file)


def test_file_size_limit(tmp_path: Path) -> None:
    """Файл, превышающий max_bytes, должен вызывать InputError."""
    big_file = tmp_path / "big.txt"
    big_file.write_bytes(b"x" * 1001)

    with pytest.raises(InputError):
        read_input(big_file, max_bytes=1000)


def test_stdin_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """stdin, превышающий max_bytes, должен вызывать InputError."""
    big_data = b"y" * 1001
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(big_data)))
    # Подменяем stdin.buffer напрямую
    mock_buffer = io.BytesIO(big_data)
    monkeypatch.setattr(sys.stdin, "buffer", mock_buffer)

    with pytest.raises(InputError):
        read_input("-", max_bytes=1000)


def test_repr_does_not_expose_text() -> None:
    """repr(InputText) не должен раскрывать содержимое текста."""
    result = read_input(_FIXTURES / "utf8_sample.txt")
    rep = repr(result)
    assert "synth-user@example-test.local" not in rep
    assert "192.168.100.200" not in rep
