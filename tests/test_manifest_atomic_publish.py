"""Failure-path тесты атомарной публикации manifest.json — #45.

Проверяют политику atomic visibility only (ADR-45):
частичный manifest не наблюдается, при отказах write/close/replace
существующий файл сохраняется побайтово, временные файлы очищаются,
первичная ошибка не подменяется ошибкой cleanup.

Все пути — tmp_path, содержимое синтетическое. Тесты исполняются
на Linux и Windows без skip: временный файл всегда закрыт до unlink.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import IO, Any

import pytest

from privacy_gateway import manifest as manifest_mod
from privacy_gateway.crypto import generate_key
from privacy_gateway.manifest import build_manifest, save_manifest
from privacy_gateway.models import EntityType, ManifestEntry, TokenRecord

_SYNTHETIC_EMAIL = "user@example.com"  # pragma: allowlist secret
_SYNTHETIC_IP = "192.0.2.10"  # pragma: allowlist secret


def _records(suffix: str) -> list[TokenRecord]:
    return [
        TokenRecord(
            token=f"[EMAIL_{suffix}]",
            entity_type=EntityType.EMAIL,
            fingerprint=f"fp_email_{suffix}",
        ),
        TokenRecord(
            token=f"[HOST_{suffix}]",
            entity_type=EntityType.HOST,
            fingerprint=f"fp_ip_{suffix}",
        ),
    ]


def _entries(suffix: str = "1") -> list[ManifestEntry]:
    return build_manifest(
        _records(suffix), [_SYNTHETIC_EMAIL, _SYNTHETIC_IP], generate_key()
    )


def _leftovers(directory: Path) -> list[Path]:
    """Все файлы каталога, кроме опубликованного манифеста."""
    return [p for p in directory.iterdir() if p.name != "manifest.json"]


class _PartialWriteStream:
    """Пишет часть данных, затем поднимает заданную ошибку."""

    def __init__(self, stream: IO[str], exc: BaseException) -> None:
        self._stream = stream
        self._exc = exc

    def write(self, data: str) -> int:
        half = max(1, len(data) // 2)
        self._stream.write(data[:half])
        self._stream.flush()
        raise self._exc

    def __enter__(self) -> _PartialWriteStream:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._stream.close()


class _CloseFailStream:
    """Пишет данные полностью, но поднимает ошибку при закрытии."""

    def __init__(self, stream: IO[str], exc: BaseException) -> None:
        self._stream = stream
        self._exc = exc

    def write(self, data: str) -> int:
        return self._stream.write(data)

    def __enter__(self) -> _CloseFailStream:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._stream.close()
        raise self._exc


def _patch_stream(
    monkeypatch: pytest.MonkeyPatch, wrapper: type, exc: BaseException
) -> None:
    """Подменить os.fdopen оболочкой *wrapper* над реальным потоком."""
    real_fdopen = os.fdopen

    def fake_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        return wrapper(real_fdopen(fd, *args, **kwargs), exc)

    monkeypatch.setattr(os, "fdopen", fake_fdopen)


# ---------------------------------------------------------------------------
# Успешная публикация и format contract
# ---------------------------------------------------------------------------


def test_successful_publication_leaves_valid_json(tmp_path: Path) -> None:
    """После публикации манифест — полный валидный JSON без temp-файлов."""
    entries = _entries()
    dest = tmp_path / "manifest.json"
    save_manifest(entries, dest)
    decoded = json.loads(dest.read_text(encoding="utf-8"))
    assert isinstance(decoded, list)
    assert len(decoded) == len(entries)
    assert _leftovers(tmp_path) == []


def test_format_contract_unchanged(tmp_path: Path) -> None:
    """Формат файла совпадает с прежним json.dumps-контрактом."""
    entries = _entries()
    dest = tmp_path / "manifest.json"
    save_manifest(entries, dest)
    expected = json.dumps(
        [entry.to_dict() for entry in entries],
        ensure_ascii=False,
        indent=2,
    )
    assert dest.read_text(encoding="utf-8") == expected
    assert not expected.endswith("\\n")


def test_existing_manifest_replaced_entirely(tmp_path: Path) -> None:
    """Существующий манифест заменяется целиком, без остатков старого."""
    dest = tmp_path / "manifest.json"
    save_manifest(_entries("1"), dest)
    first = dest.read_bytes()
    save_manifest(_entries("2"), dest)
    second = dest.read_bytes()
    assert second != first
    assert json.loads(second.decode("utf-8"))[0]["token"] == "[EMAIL_2]"
    assert _leftovers(tmp_path) == []


def test_atomic_visibility_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """До replace виден старый манифест, temp содержит новый документ."""
    dest = tmp_path / "manifest.json"
    save_manifest(_entries("1"), dest)
    old_bytes = dest.read_bytes()
    observed: dict[str, bytes] = {}
    real_replace = os.replace

    def spy_replace(src: Any, dst: Any, **kwargs: Any) -> None:
        observed["dest"] = Path(dst).read_bytes()
        observed["tmp"] = Path(src).read_bytes()
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", spy_replace)
    save_manifest(_entries("2"), dest)
    monkeypatch.undo()

    assert observed["dest"] == old_bytes
    assert json.loads(observed["tmp"].decode("utf-8"))[0]["token"] == (
        "[EMAIL_2]"
    )
    assert dest.read_bytes() == observed["tmp"]
    assert _leftovers(tmp_path) == []


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_partial_write_failure_preserves_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ записи после частичной записи не повреждает манифест."""
    dest = tmp_path / "manifest.json"
    save_manifest(_entries("1"), dest)
    old_bytes = dest.read_bytes()
    _patch_stream(monkeypatch, _PartialWriteStream, OSError("write failed"))
    with pytest.raises(OSError, match="write failed"):
        save_manifest(_entries("2"), dest)
    monkeypatch.undo()
    assert dest.read_bytes() == old_bytes
    assert _leftovers(tmp_path) == []


def test_partial_write_failure_creates_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """При отсутствующем адресате частичный manifest.json не появляется."""
    dest = tmp_path / "manifest.json"
    _patch_stream(monkeypatch, _PartialWriteStream, OSError("write failed"))
    with pytest.raises(OSError, match="write failed"):
        save_manifest(_entries(), dest)
    monkeypatch.undo()
    assert not dest.exists()
    assert _leftovers(tmp_path) == []


def test_close_failure_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ закрытия/flush очищает temp и не меняет адресат."""
    dest = tmp_path / "manifest.json"
    save_manifest(_entries("1"), dest)
    old_bytes = dest.read_bytes()
    _patch_stream(monkeypatch, _CloseFailStream, OSError("close failed"))
    with pytest.raises(OSError, match="close failed"):
        save_manifest(_entries("2"), dest)
    monkeypatch.undo()
    assert dest.read_bytes() == old_bytes
    assert _leftovers(tmp_path) == []


def test_replace_failure_preserves_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ os.replace сохраняет старый манифест побайтово."""
    dest = tmp_path / "manifest.json"
    save_manifest(_entries("1"), dest)
    old_bytes = dest.read_bytes()

    def failing_replace(src: Any, dst: Any, **kwargs: Any) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="replace failed"):
        save_manifest(_entries("2"), dest)
    monkeypatch.undo()
    assert dest.read_bytes() == old_bytes
    assert _leftovers(tmp_path) == []


def test_replace_failure_creates_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ replace при отсутствующем адресате не создаёт манифест."""
    dest = tmp_path / "manifest.json"

    def failing_replace(src: Any, dst: Any, **kwargs: Any) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="replace failed"):
        save_manifest(_entries(), dest)
    monkeypatch.undo()
    assert not dest.exists()
    assert _leftovers(tmp_path) == []


def test_fdopen_failure_closes_raw_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ os.fdopen закрывает сырой descriptor и удаляет temp."""
    dest = tmp_path / "manifest.json"
    captured: list[int] = []

    def failing_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        captured.append(fd)
        raise OSError("fdopen failed")

    monkeypatch.setattr(os, "fdopen", failing_fdopen)
    with pytest.raises(OSError, match="fdopen failed"):
        save_manifest(_entries(), dest)
    monkeypatch.undo()
    assert captured, "os.fdopen не был вызван"
    with pytest.raises(OSError):
        os.fstat(captured[0])
    assert not dest.exists()
    assert _leftovers(tmp_path) == []


def test_mkstemp_failure_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ mkstemp не создаёт temp и не меняет существующий манифест."""
    dest = tmp_path / "manifest.json"
    save_manifest(_entries("1"), dest)
    old_bytes = dest.read_bytes()

    def failing_mkstemp(**kwargs: Any) -> tuple[int, str]:
        raise OSError("mkstemp failed")

    monkeypatch.setattr(tempfile, "mkstemp", failing_mkstemp)
    with pytest.raises(OSError, match="mkstemp failed"):
        save_manifest(_entries("2"), dest)
    monkeypatch.undo()
    assert dest.read_bytes() == old_bytes
    assert _leftovers(tmp_path) == []


def test_non_oserror_propagates_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Не-OSError не маскируется, temp при этом очищается."""
    dest = tmp_path / "manifest.json"
    _patch_stream(
        monkeypatch, _PartialWriteStream, RuntimeError("synthetic failure")
    )
    with pytest.raises(RuntimeError, match="synthetic failure"):
        save_manifest(_entries(), dest)
    monkeypatch.undo()
    assert not dest.exists()
    assert _leftovers(tmp_path) == []


def test_serialization_failure_creates_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ошибка сериализации не создаёт temp и не трогает адресат."""
    dest = tmp_path / "manifest.json"
    save_manifest(_entries("1"), dest)
    old_bytes = dest.read_bytes()

    def failing_dumps(*args: Any, **kwargs: Any) -> str:
        raise TypeError("not serializable")

    monkeypatch.setattr(manifest_mod.json, "dumps", failing_dumps)
    with pytest.raises(TypeError, match="not serializable"):
        save_manifest(_entries("2"), dest)
    monkeypatch.undo()
    assert dest.read_bytes() == old_bytes
    assert _leftovers(tmp_path) == []


def test_cleanup_failure_does_not_mask_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ unlink не подменяет первичную ошибку replace."""
    dest = tmp_path / "manifest.json"
    save_manifest(_entries("1"), dest)
    old_bytes = dest.read_bytes()

    def failing_replace(src: Any, dst: Any, **kwargs: Any) -> None:
        raise OSError("primary replace failure")

    def failing_unlink(path: Any, **kwargs: Any) -> None:
        raise OSError("cleanup failure")

    monkeypatch.setattr(os, "replace", failing_replace)
    monkeypatch.setattr(os, "unlink", failing_unlink)
    with pytest.raises(OSError, match="primary replace failure"):
        save_manifest(_entries("2"), dest)
    monkeypatch.undo()

    assert dest.read_bytes() == old_bytes
    # temp остался из-за смоделированного отказа ОС: убираем вручную
    for leftover in _leftovers(tmp_path):
        leftover.unlink()
    assert _leftovers(tmp_path) == []


def test_temp_name_is_not_manifest_like(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Temp-имя уникально и не выглядит как опубликованный манифест."""
    dest = tmp_path / "manifest.json"
    seen: list[str] = []
    real_replace = os.replace

    def spy_replace(src: Any, dst: Any, **kwargs: Any) -> None:
        seen.append(Path(src).name)
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", spy_replace)
    save_manifest(_entries("1"), dest)
    save_manifest(_entries("2"), dest)
    monkeypatch.undo()

    assert len(seen) == 2
    assert seen[0] != seen[1]
    for name in seen:
        assert name != "manifest.json"
        assert not name.endswith(".json")
        assert name.startswith(".manifest-")


def test_temp_created_next_to_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Temp создаётся в каталоге назначения (без cross-filesystem)."""
    dest = tmp_path / "nested" / "manifest.json"
    dest.parent.mkdir()
    parents: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def spy_mkstemp(**kwargs: Any) -> tuple[int, str]:
        fd, name = real_mkstemp(**kwargs)
        parents.append(Path(name).parent)
        return fd, name

    monkeypatch.setattr(tempfile, "mkstemp", spy_mkstemp)
    save_manifest(_entries(), dest)
    monkeypatch.undo()
    assert parents == [dest.parent]
    assert _leftovers(dest.parent) == []
