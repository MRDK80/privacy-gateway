"""RED-доказательство TOCTOU публикации prepare — #64, ADR-64.

Preflight #63 (ADR-48) проверяет имена через ``os.path.lexists()`` до
первого writer-вызова. Между этой проверкой и публикацией остаётся окно:
конкурентный процесс создаёт объект по целевому имени, а writer завершает
публикацию заменой (``os.replace`` в ``manifest.save_manifest``,
``Path.replace`` в ``pipeline._write_atomic``) и молча уничтожает чужой
объект, несмотря на ``overwrite=False``.

Гонка воспроизводится детерминированно: foreign-объект создаётся внутри
спая на writer boundary, то есть строго после preflight и строго до
публикации. Ни ``sleep``, ни потоки, ни планировщик не используются.

Ожидаемое поведение после ADR-64 (сейчас часть тестов КРАСНЫЕ):
    overwrite=False:
        POSIX   temp -> close -> os.link(temp, target) -> unlink(temp)
        Windows temp -> close -> os.rename(temp, target)
    overwrite=True: без изменений, os.replace / Path.replace.

Границы: ``FileExistsError`` -> BLOCKED + exit code 3; ``EPERM`` и
``PermissionError`` в BLOCKED НЕ мапятся. Ранее опубликованные свои
артефакты не откатываются, ``route.json`` при незавершённом наборе не
публикуется (ADR-64, раздел 5).

Синтетика: user@example.com, 192.0.2.10.
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from privacy_gateway import pipeline as pipeline_mod
from privacy_gateway.crypto import generate_key
from privacy_gateway.manifest import save_manifest as real_save_manifest
from privacy_gateway.models import ManifestEntry, ProcessingStatus
from privacy_gateway.pipeline import PipelineResult, prepare_pipeline
from privacy_gateway.routing import load_routing_config

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"  # pragma: allowlist secret
SYNTH_TEXT = f"Письмо на {SYNTH_EMAIL} с сервера {SYNTH_IP}\n"

FOREIGN_BYTES = b"FOREIGN-OBJECT-DO-NOT-REPLACE\n"

WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-специфичный path entry"
)
POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX symlink semantics"
)


def _run(out_dir: Path, key: bytes, overwrite: bool = False) -> PipelineResult:
    return prepare_pipeline(
        text=SYNTH_TEXT,
        source_ref="test_prepare_no_clobber_race.txt",
        routing_cfg=load_routing_config(None),
        key=key,
        out_dir=out_dir,
        overwrite=overwrite,
    )


def _inject_before_manifest(
    monkeypatch: pytest.MonkeyPatch, make_foreign: Any
) -> None:
    """Создать foreign-объект прямо перед публикацией manifest.json."""

    def spy_save(
        entries: list[ManifestEntry],
        path: Path,
        *,
        overwrite: bool = True,
    ) -> None:
        make_foreign(path)
        real_save_manifest(entries, path, overwrite=overwrite)

    monkeypatch.setattr(pipeline_mod, "save_manifest", spy_save)


def _inject_before_write(
    monkeypatch: pytest.MonkeyPatch, target_name: str, make_foreign: Any
) -> None:
    """Создать foreign-объект прямо перед публикацией *target_name*."""
    real_write = pipeline_mod._write_atomic

    def spy_write(
        path: Path,
        content: str | bytes,
        mode: int | None = None,
        *,
        overwrite: bool = True,
    ) -> None:
        if path.name == target_name:
            make_foreign(path)
        real_write(path, content, mode, overwrite=overwrite)

    monkeypatch.setattr(pipeline_mod, "_write_atomic", spy_write)


def _write_foreign_file(path: Path) -> None:
    path.write_bytes(FOREIGN_BYTES)


def _leftover_temps(out_dir: Path) -> list[str]:
    """Любой файл, кроме трёх артефактов, считается остаточным.

    Временные файлы prompt/route наследуют суффикс цели
    (``.txt`` и ``.json``), поэтому фильтр по ``.tmp`` их не
    поймал бы.
    """
    artifacts = {"manifest.json", "prompt.txt", "route.json"}
    return sorted(
        p.name
        for p in out_dir.iterdir()
        if p.name not in artifacts
    )


def test_foreign_manifest_created_after_preflight_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED: чужой manifest.json не должен заменяться при overwrite=False."""
    out_dir = tmp_path / "out"
    _inject_before_manifest(monkeypatch, _write_foreign_file)

    result = _run(out_dir, generate_key())
    monkeypatch.undo()

    manifest_path = out_dir / "manifest.json"
    assert manifest_path.read_bytes() == FOREIGN_BYTES, (
        "чужой manifest.json заменён публикацией при overwrite=False"
    )
    assert result.status == ProcessingStatus.BLOCKED, result.message
    assert not (out_dir / "prompt.txt").exists()
    assert not (out_dir / "route.json").exists()
    assert _leftover_temps(out_dir) == []


def test_foreign_prompt_created_after_preflight_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED: чужой prompt.txt не должен заменяться при overwrite=False."""
    out_dir = tmp_path / "out"
    _inject_before_write(monkeypatch, "prompt.txt", _write_foreign_file)

    result = _run(out_dir, generate_key())
    monkeypatch.undo()

    assert (out_dir / "prompt.txt").read_bytes() == FOREIGN_BYTES, (
        "чужой prompt.txt заменён публикацией при overwrite=False"
    )
    assert result.status == ProcessingStatus.BLOCKED, result.message
    assert _leftover_temps(out_dir) == []


def test_foreign_route_created_after_preflight_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED: чужой route.json не должен заменяться при overwrite=False."""
    out_dir = tmp_path / "out"
    _inject_before_write(monkeypatch, "route.json", _write_foreign_file)

    result = _run(out_dir, generate_key())
    monkeypatch.undo()

    assert (out_dir / "route.json").read_bytes() == FOREIGN_BYTES, (
        "чужой route.json заменён публикацией при overwrite=False"
    )
    assert result.status == ProcessingStatus.BLOCKED, result.message
    assert _leftover_temps(out_dir) == []


def test_late_race_on_prompt_keeps_manifest_and_omits_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED: поздняя коллизия — набор незавершён, route.json не создан.

    ADR-64 раздел 5: чужой объект не трогаем, ранее опубликованный
    manifest.json НЕ откатываем (ownership недоказуем без транзакционной
    подсистемы, вне scope), route.json не публикуется.
    """
    out_dir = tmp_path / "out"
    _inject_before_write(monkeypatch, "prompt.txt", _write_foreign_file)

    result = _run(out_dir, generate_key())
    monkeypatch.undo()

    assert (out_dir / "prompt.txt").read_bytes() == FOREIGN_BYTES
    assert (out_dir / "manifest.json").exists(), (
        "ранее опубликованный manifest.json не должен удаляться"
    )
    assert not (out_dir / "route.json").exists(), (
        "route.json не публикуется при незавершённом наборе"
    )
    assert result.status == ProcessingStatus.BLOCKED, result.message
    assert _leftover_temps(out_dir) == []


@POSIX_ONLY
def test_broken_symlink_prompt_target_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED: publication не должна заменять broken symlink по имени target.

    POSIX ``link()`` возвращает EEXIST, если path2 является symbolic link,
    поэтому запись «сквозь» ссылку и замена самой ссылки недопустимы.
    """
    out_dir = tmp_path / "out"

    def make_broken_symlink(path: Path) -> None:
        path.symlink_to(path.parent / "nonexistent-target")

    _inject_before_write(monkeypatch, "prompt.txt", make_broken_symlink)

    result = _run(out_dir, generate_key())
    monkeypatch.undo()

    prompt_path = out_dir / "prompt.txt"
    assert os.path.islink(prompt_path), "broken symlink заменён публикацией"
    assert not prompt_path.exists(), "ссылка не должна стать обычным файлом"
    assert result.status == ProcessingStatus.BLOCKED, result.message
    assert _leftover_temps(out_dir) == []


@WINDOWS_ONLY
def test_directory_prompt_target_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED: каталог по имени prompt.txt не заменяется, ошибка ожидаема."""
    out_dir = tmp_path / "out"

    def make_directory(path: Path) -> None:
        path.mkdir()
        (path / "marker.txt").write_bytes(FOREIGN_BYTES)

    _inject_before_write(monkeypatch, "prompt.txt", make_directory)

    result = _run(out_dir, generate_key())
    monkeypatch.undo()

    prompt_path = out_dir / "prompt.txt"
    assert prompt_path.is_dir(), "каталог заменён публикацией"
    assert (prompt_path / "marker.txt").read_bytes() == FOREIGN_BYTES
    assert result.status == ProcessingStatus.BLOCKED, result.message


@WINDOWS_ONLY
def test_junction_prompt_target_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED: live junction по имени prompt.txt не заменяется."""
    out_dir = tmp_path / "out"
    junction_target = tmp_path / "junction-target"
    junction_target.mkdir()
    (junction_target / "marker.txt").write_bytes(FOREIGN_BYTES)

    def make_junction(path: Path) -> None:
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(path), str(junction_target)],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0 or not os.path.lexists(path):
            pytest.skip("mklink /J недоступен в этой среде")

    _inject_before_write(monkeypatch, "prompt.txt", make_junction)

    result = _run(out_dir, generate_key())
    monkeypatch.undo()

    prompt_path = out_dir / "prompt.txt"
    assert os.path.lexists(prompt_path), "junction удалён публикацией"
    assert (prompt_path / "marker.txt").read_bytes() == FOREIGN_BYTES
    assert result.status == ProcessingStatus.BLOCKED, result.message


def test_eperm_is_not_mapped_to_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard: EPERM (ФС без hard links) не превращается в BLOCKED.

    После ADR-64 отсутствие поддержки hard links должно давать явную
    ошибку окружения, а не коллизию имени. BLOCKED зарезервирован под
    конфликт имени; смешение маскировало бы отказ механизма.
    """
    out_dir = tmp_path / "out"

    def failing_save(
        entries: list[ManifestEntry],
        path: Path,
        *,
        overwrite: bool = True,
    ) -> None:
        raise OSError(
            errno.EPERM, "hard links are not supported by the filesystem"
        )

    monkeypatch.setattr(pipeline_mod, "save_manifest", failing_save)

    with pytest.raises(OSError) as excinfo:
        _run(out_dir, generate_key())
    monkeypatch.undo()

    assert excinfo.value.errno == errno.EPERM
    assert not (out_dir / "manifest.json").exists()
    assert not (out_dir / "prompt.txt").exists()
    assert not (out_dir / "route.json").exists()


def test_permission_error_is_not_mapped_to_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard: sharing/antivirus PermissionError не превращается в BLOCKED."""
    out_dir = tmp_path / "out"

    def failing_write(
        path: Path,
        content: str | bytes,
        mode: int | None = None,
        *,
        overwrite: bool = True,
    ) -> None:
        raise PermissionError(
            errno.EACCES, "temp file is locked by another process"
        )

    monkeypatch.setattr(pipeline_mod, "_write_atomic", failing_write)

    with pytest.raises(PermissionError) as excinfo:
        _run(out_dir, generate_key())
    monkeypatch.undo()

    assert excinfo.value.errno == errno.EACCES
    assert (out_dir / "manifest.json").exists(), (
        "manifest.json опубликован до отказа и не откатывается"
    )
    assert not (out_dir / "route.json").exists()


def test_overwrite_true_still_replaces_all_artifacts(tmp_path: Path) -> None:
    """Regression: overwrite=True сохраняет replace semantics и порядок."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    for name in ("manifest.json", "prompt.txt", "route.json"):
        (out_dir / name).write_bytes(FOREIGN_BYTES)

    result = _run(out_dir, generate_key(), overwrite=True)

    assert result.status == ProcessingStatus.OK, result.message
    for name in ("manifest.json", "prompt.txt", "route.json"):
        assert (out_dir / name).read_bytes() != FOREIGN_BYTES
    assert _leftover_temps(out_dir) == []


def test_successful_run_leaves_no_temp_files(tmp_path: Path) -> None:
    """Regression: успешный прогон не оставляет temp-файлов."""
    out_dir = tmp_path / "out"
    result = _run(out_dir, generate_key())

    assert result.status == ProcessingStatus.OK, result.message
    assert _leftover_temps(out_dir) == []
