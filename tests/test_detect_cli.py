"""Тесты CLI-команды pgw detect — Этап Э2."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Вызвать CLI как отдельный процесс."""
    return subprocess.run(
        [sys.executable, "-m", "privacy_gateway", *args],
        capture_output=True,
        text=True,
    )


def test_detect_help_exits_zero() -> None:
    """pgw detect --help завершается с кодом 0."""
    result = _run("detect", "--help")
    assert result.returncode == 0, result.stderr


def test_detect_returns_json() -> None:
    """pgw detect возвращает валидный JSON."""
    result = _run("detect", str(_FIXTURES / "utf8_sample.txt"))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert "entity_count" in data
    assert "entities" in data
    assert isinstance(data["entities"], list)


def test_detect_json_has_required_fields() -> None:
    """JSON-вывод содержит обязательные поля."""
    result = _run("detect", str(_FIXTURES / "utf8_sample.txt"))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert "source" in data
    assert "encoding" in data
    assert data["source"] == "file"
    for entity in data["entities"]:
        assert "type" in entity
        assert "start" in entity
        assert "end" in entity
        assert "confidence" in entity
        assert "source" in entity
        assert "fingerprint" in entity


def test_detect_json_no_raw_values() -> None:
    """JSON-вывод не содержит исходных значений обнаруженных сущностей."""
    result = _run("detect", str(_FIXTURES / "utf8_sample.txt"))
    assert result.returncode == 0, result.stderr
    output = result.stdout
    # Синтетические значения из фикстуры
    # не должны появляться в выводе
    assert "synth-user@example-test.local" not in output
    assert "192.168.100.200" not in output
    assert "+7 (900) 123-45-67" not in output


def test_detect_unsupported_file_returns_code_3() -> None:
    """Неподдерживаемый тип файла возвращает код 3."""
    result = _run("detect", str(_FIXTURES / "not_a_txt.csv"))
    assert result.returncode == 3


def test_detect_nonexistent_file_returns_code_3() -> None:
    """Несуществующий файл возвращает код 3."""
    result = _run("detect", "nonexistent_synthetic_file_xyz.txt")
    assert result.returncode == 3


def test_detect_encoding_utf8_explicit() -> None:
    """pgw detect --encoding utf-8 работает корректно."""
    result = _run("detect", str(_FIXTURES / "utf8_sample.txt"), "--encoding", "utf-8")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["encoding"] in ("utf-8", "utf-8-sig")


def test_detect_bom_file() -> None:
    """pgw detect корректно обрабатывает файл с UTF-8 BOM."""
    result = _run("detect", str(_FIXTURES / "utf8bom_sample.txt"))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["entity_count"] >= 0


def test_detect_entities_found_in_sample() -> None:
    """pgw detect находит хотя бы одну сущность в синтетической фикстуре."""
    result = _run("detect", str(_FIXTURES / "utf8_sample.txt"))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["entity_count"] > 0
