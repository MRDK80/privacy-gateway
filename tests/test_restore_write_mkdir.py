"""Тесты границы OSError в write_restored: подготовка каталога (#36, ADR-33).

Проверяется, что отказ создания родительского каталога — ожидаемый отказ
записи (ConfigurationError), а не-OSError по-прежнему проходит наружу.
Реальные права не меняются: patch адресный, тесты стабильны на Windows.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from privacy_gateway.models import ConfigurationError
from privacy_gateway.restore import write_restored

PLAINTEXT = "Иванов Иван, ivan@example.com"


def _fail_mkdir_for(
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    exc: BaseException,
) -> None:
    """Подменить Path.mkdir только для target, остальные пути не трогать."""
    real_mkdir = Path.mkdir

    def fake_mkdir(self: Path, *args: Any, **kwargs: Any) -> None:
        if self == target:
            raise exc
        real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)


def _spy_mkstemp(monkeypatch: pytest.MonkeyPatch, calls: list[object]) -> None:
    real_mkstemp = tempfile.mkstemp

    def fake_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
        calls.append(kwargs)
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(tempfile, "mkstemp", fake_mkstemp)


def test_mkdir_oserror_becomes_configuration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_path = tmp_path / "nested" / "restored.txt"
    _fail_mkdir_for(monkeypatch, out_path.parent, PermissionError("mkdir denied"))
    calls: list[object] = []
    _spy_mkstemp(monkeypatch, calls)

    with pytest.raises(ConfigurationError) as excinfo:
        write_restored(PLAINTEXT, out_path)

    assert str(excinfo.value) == (
        f"Не удалось записать результат в {out_path}: mkdir denied"
    )
    assert isinstance(excinfo.value.__cause__, PermissionError)
    assert calls == []
    assert not out_path.parent.exists()
    assert list(tmp_path.iterdir()) == []
    assert PLAINTEXT not in str(excinfo.value)


def test_mkdir_non_oserror_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_path = tmp_path / "nested" / "restored.txt"
    _fail_mkdir_for(monkeypatch, out_path.parent, RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        write_restored(PLAINTEXT, out_path)

    assert not out_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_missing_parent_directories_are_still_created(tmp_path: Path) -> None:
    out_path = tmp_path / "deep" / "nested" / "restored.txt"

    write_restored(PLAINTEXT, out_path)

    assert out_path.read_text(encoding="utf-8") == PLAINTEXT
    assert list(out_path.parent.glob(".pgw_restore_*")) == []
