"""Failure-path тесты cleanup временного plaintext-файла write_restored (#44).

Аудит #42 показал: при отказе ``os.replace`` после успешной записи временный
файл ``.pgw_restore_*.tmp`` оставался на диске и содержал восстановленный
plaintext. Тесты фиксируют, что временный файл удаляется при любом неуспешном
исходе (mkstemp, fdopen, write, close, replace), существующий destination не
повреждается, успешная запись остаётся атомарной, а действующий exception
contract (``OSError`` → ``ConfigurationError``, не-``OSError`` наружу) не
изменяется.

Файлы создаются только в ``tmp_path``; plaintext синтетический (ADR-25).
Реальные права файловой системы не меняются — сценарии стабильны и на Windows,
где unlink открытого файла невозможен: дескриптор закрывается до cleanup.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from privacy_gateway.models import ConfigurationError
from privacy_gateway.restore import write_restored

PLAINTEXT = "Иванов Иван, user@example.com, 192.0.2.10"
EXISTING = "ранее записанный результат"
TMP_GLOB = ".pgw_restore_*"


class _StreamProxy:
    """Обёртка вокруг реального текстового потока с управляемым отказом."""

    def __init__(
        self,
        stream: Any,
        *,
        write_exc: BaseException | None = None,
        close_exc: BaseException | None = None,
        partial: str = "",
    ) -> None:
        self._stream = stream
        self._write_exc = write_exc
        self._close_exc = close_exc
        self._partial = partial

    def __enter__(self) -> _StreamProxy:
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        self.close()
        return False

    def write(self, data: str) -> int:
        if self._write_exc is not None:
            self._stream.write(self._partial)
            self._stream.flush()
            raise self._write_exc
        return int(self._stream.write(data))

    def close(self) -> None:
        self._stream.close()
        if self._close_exc is not None:
            raise self._close_exc


def _leftovers(directory: Path) -> list[Path]:
    return sorted(directory.glob(TMP_GLOB))


def _files_with_plaintext(directory: Path) -> list[Path]:
    hits: list[Path] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        if PLAINTEXT[:20] in content:
            hits.append(path)
    return hits


def _patch_replace(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    def fake_replace(src: Any, dst: Any, **kwargs: Any) -> None:
        raise exc

    monkeypatch.setattr(os, "replace", fake_replace)


def _patch_fdopen(monkeypatch: pytest.MonkeyPatch, **proxy_kwargs: Any) -> None:
    real_fdopen = os.fdopen

    def fake_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        return _StreamProxy(real_fdopen(fd, *args, **kwargs), **proxy_kwargs)

    monkeypatch.setattr(os, "fdopen", fake_fdopen)


def test_replace_failure_removes_plaintext_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Основной finding #42: отказ os.replace после записи plaintext."""
    out_path = tmp_path / "restored.txt"
    out_path.write_text(EXISTING, encoding="utf-8")
    _patch_replace(monkeypatch, OSError("replace denied"))

    with pytest.raises(ConfigurationError) as excinfo:
        write_restored(PLAINTEXT, out_path, overwrite=True)

    assert str(excinfo.value) == (
        f"Не удалось записать результат в {out_path}: replace denied"
    )
    assert isinstance(excinfo.value.__cause__, OSError)
    assert PLAINTEXT not in str(excinfo.value)
    assert out_path.read_text(encoding="utf-8") == EXISTING
    assert _leftovers(tmp_path) == []
    assert _files_with_plaintext(tmp_path) == []


def test_replace_failure_without_existing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup не зависит от overwrite и от наличия destination."""
    out_path = tmp_path / "nested" / "restored.txt"
    _patch_replace(monkeypatch, OSError("replace denied"))

    with pytest.raises(ConfigurationError):
        write_restored(PLAINTEXT, out_path, overwrite=False)

    assert not out_path.exists()
    assert _leftovers(out_path.parent) == []
    assert _files_with_plaintext(tmp_path) == []


def test_write_failure_removes_partial_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ записи после частичной записи: temp удалён, destination цел."""
    out_path = tmp_path / "restored.txt"
    out_path.write_text(EXISTING, encoding="utf-8")
    _patch_fdopen(
        monkeypatch,
        write_exc=OSError("no space left on device"),
        partial=PLAINTEXT[:12],
    )

    with pytest.raises(ConfigurationError) as excinfo:
        write_restored(PLAINTEXT, out_path, overwrite=True)

    assert "no space left on device" in str(excinfo.value)
    assert out_path.read_text(encoding="utf-8") == EXISTING
    assert _leftovers(tmp_path) == []


def test_close_failure_removes_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ закрытия потока: дескриптор закрыт, temp удалён."""
    out_path = tmp_path / "restored.txt"
    _patch_fdopen(monkeypatch, close_exc=OSError("close failed"))

    with pytest.raises(ConfigurationError) as excinfo:
        write_restored(PLAINTEXT, out_path)

    assert "close failed" in str(excinfo.value)
    assert not out_path.exists()
    assert _leftovers(tmp_path) == []
    assert _files_with_plaintext(tmp_path) == []


def test_mkstemp_failure_creates_no_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ mkstemp: temp не создан, destination не изменён."""
    out_path = tmp_path / "restored.txt"
    out_path.write_text(EXISTING, encoding="utf-8")

    def fake_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
        raise OSError("mkstemp denied")

    monkeypatch.setattr(tempfile, "mkstemp", fake_mkstemp)

    with pytest.raises(ConfigurationError) as excinfo:
        write_restored(PLAINTEXT, out_path, overwrite=True)

    assert "mkstemp denied" in str(excinfo.value)
    assert out_path.read_text(encoding="utf-8") == EXISTING
    assert _leftovers(tmp_path) == []


def test_fdopen_failure_closes_descriptor_and_removes_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ fdopen: сырой дескриптор закрыт явно, temp удалён."""
    out_path = tmp_path / "restored.txt"
    created: list[int] = []
    closed: list[int] = []
    real_mkstemp = tempfile.mkstemp
    real_close = os.close

    def spy_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
        fd, name = real_mkstemp(*args, **kwargs)
        created.append(fd)
        return fd, name

    def spy_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    def fake_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        raise OSError("fdopen failed")

    monkeypatch.setattr(tempfile, "mkstemp", spy_mkstemp)
    monkeypatch.setattr(os, "close", spy_close)
    monkeypatch.setattr(os, "fdopen", fake_fdopen)

    with pytest.raises(ConfigurationError) as excinfo:
        write_restored(PLAINTEXT, out_path)

    assert "fdopen failed" in str(excinfo.value)
    assert len(created) == 1
    assert created[0] in closed, "сырой дескриптор не закрыт при отказе fdopen"
    assert _leftovers(tmp_path) == []
    assert not out_path.exists()


def test_non_oserror_is_not_masked_but_temp_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не-OSError остаётся непредвиденной ошибкой; cleanup всё равно выполнен."""
    out_path = tmp_path / "restored.txt"
    out_path.write_text(EXISTING, encoding="utf-8")
    _patch_replace(monkeypatch, RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        write_restored(PLAINTEXT, out_path, overwrite=True)

    assert out_path.read_text(encoding="utf-8") == EXISTING
    assert _leftovers(tmp_path) == []
    assert _files_with_plaintext(tmp_path) == []


def test_cleanup_failure_does_not_mask_primary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ самого unlink не подменяет первичную ошибку записи."""
    out_path = tmp_path / "restored.txt"
    _patch_replace(monkeypatch, OSError("replace denied"))
    real_unlink = os.unlink

    def failing_unlink(path: Any, **kwargs: Any) -> None:
        raise PermissionError("unlink denied")

    monkeypatch.setattr(os, "unlink", failing_unlink)

    with pytest.raises(ConfigurationError) as excinfo:
        write_restored(PLAINTEXT, out_path)

    assert "replace denied" in str(excinfo.value)
    assert "unlink denied" not in str(excinfo.value)

    monkeypatch.setattr(os, "unlink", real_unlink)
    for leftover in _leftovers(tmp_path):
        leftover.unlink()


def test_successful_write_is_atomic_and_leaves_no_temp(tmp_path: Path) -> None:
    """Успешная запись публикует destination и не оставляет temp-файлов."""
    out_path = tmp_path / "nested" / "restored.txt"

    write_restored(PLAINTEXT, out_path)

    assert out_path.read_text(encoding="utf-8") == PLAINTEXT
    assert _leftovers(out_path.parent) == []


def test_overwrite_contract_unchanged(tmp_path: Path) -> None:
    """overwrite=False сохраняет FileExistsError и не создаёт temp-файлов."""
    out_path = tmp_path / "restored.txt"
    out_path.write_text(EXISTING, encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_restored(PLAINTEXT, out_path)

    assert out_path.read_text(encoding="utf-8") == EXISTING
    assert _leftovers(tmp_path) == []

    write_restored(PLAINTEXT, out_path, overwrite=True)

    assert out_path.read_text(encoding="utf-8") == PLAINTEXT
    assert _leftovers(tmp_path) == []
