"""Regression matrix единой overwrite-policy prepare — #48.

Все 7 непустых комбинаций существующих целевых артефактов
(`prompt.txt`, `route.json`, `manifest.json`) должны блокировать
prepare при ``overwrite=False`` до первой операции записи:

- контракт отказа сохранён: ``PipelineResult`` со статусом BLOCKED
  и агрегированным сообщением (исключение не вводится);
- ``save_manifest`` и ``_write_atomic`` не вызываются;
- байты существующих артефактов не меняются;
- отсутствующие артефакты не создаются;
- временные файлы (`.manifest-*.tmp` и прочие) не остаются.

При ``overwrite=True`` поведение и порядок публикации #45 сохраняются:
``manifest.json`` → ``manifest_sha256`` → ``prompt.txt`` → ``route.json``.

Основная матрица работает с обычными файлами в ``tmp_path`` и
исполняется на Linux и Windows без skip.

Синтетика: user@example.com, 192.0.2.10.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

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

TARGET_NAMES = ("prompt.txt", "route.json", "manifest.json")

SENTINELS: dict[str, bytes] = {
    "prompt.txt": b"SENTINEL-PROMPT-48\n",
    "route.json": b'{"sentinel": "route-48"}',
    "manifest.json": b"[]",
}

# Все непустые комбинации (prompt, route, manifest).
COMBINATIONS = [
    (True, False, False),
    (False, True, False),
    (False, False, True),
    (True, True, False),
    (True, False, True),
    (False, True, True),
    (True, True, True),
]
COMBINATION_IDS = [
    "prompt",
    "route",
    "manifest",
    "prompt+route",
    "prompt+manifest",
    "route+manifest",
    "prompt+route+manifest",
]


def _run(
    out_dir: Path, key: bytes, overwrite: bool = False
) -> PipelineResult:
    return prepare_pipeline(
        text=SYNTH_TEXT,
        source_ref="test_prepare_overwrite_collision_matrix.txt",
        routing_cfg=load_routing_config(None),
        key=key,
        out_dir=out_dir,
        overwrite=overwrite,
    )


def _seed(out_dir: Path, combo: tuple[bool, bool, bool]) -> list[str]:
    """Создать существующие артефакты по комбинации *combo*."""
    out_dir.mkdir(parents=True, exist_ok=True)
    seeded: list[str] = []
    for name, present in zip(TARGET_NAMES, combo, strict=True):
        if present:
            (out_dir / name).write_bytes(SENTINELS[name])
            seeded.append(name)
    return seeded


def _forbid_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Любой вызов writer в этом тесте — ошибка контракта preflight."""

    def forbidden_save(entries: list[ManifestEntry], path: Path) -> None:
        raise AssertionError(f"save_manifest вызван для {path.name}")

    def forbidden_write(
        path: Path, content: str | bytes, mode: int | None = None
    ) -> None:
        raise AssertionError(f"_write_atomic вызван для {path.name}")

    monkeypatch.setattr(pipeline_mod, "save_manifest", forbidden_save)
    monkeypatch.setattr(pipeline_mod, "_write_atomic", forbidden_write)


def _unexpected(out_dir: Path, expected: list[str]) -> list[str]:
    """Имена файлов каталога сверх ожидаемого набора."""
    return sorted(p.name for p in out_dir.iterdir() if p.name not in expected)


# ---------------------------------------------------------------------------
# overwrite=False — полная матрица коллизий
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("combo", COMBINATIONS, ids=COMBINATION_IDS)
def test_any_existing_target_blocks_prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    combo: tuple[bool, bool, bool],
) -> None:
    """Любая непустая комбинация артефактов блокирует overwrite=False."""
    out_dir = tmp_path / "out"
    seeded = _seed(out_dir, combo)
    _forbid_writes(monkeypatch)

    result = _run(out_dir, generate_key())

    assert result.status == ProcessingStatus.BLOCKED, result.message
    assert result.prompt_path is None
    assert result.route_path is None
    assert result.manifest_path is None
    for name in seeded:
        assert name in result.message


@pytest.mark.parametrize("combo", COMBINATIONS, ids=COMBINATION_IDS)
def test_preflight_failure_touches_no_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    combo: tuple[bool, bool, bool],
) -> None:
    """Отказ preflight не меняет и не создаёт ни одного файла."""
    out_dir = tmp_path / "out"
    seeded = _seed(out_dir, combo)
    _forbid_writes(monkeypatch)

    _run(out_dir, generate_key())

    for name in seeded:
        assert (out_dir / name).read_bytes() == SENTINELS[name]
    for name in TARGET_NAMES:
        if name not in seeded:
            assert not (out_dir / name).exists()
    assert _unexpected(out_dir, seeded) == []


@pytest.mark.parametrize("combo", COMBINATIONS, ids=COMBINATION_IDS)
def test_collision_message_leaks_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    combo: tuple[bool, bool, bool],
) -> None:
    """Сообщение отказа не раскрывает plaintext и содержимое файлов."""
    out_dir = tmp_path / "out"
    _seed(out_dir, combo)
    _forbid_writes(monkeypatch)

    result = _run(out_dir, generate_key())

    assert SYNTH_EMAIL not in result.message
    assert SYNTH_IP not in result.message
    assert "SENTINEL" not in result.message


def test_manifest_only_collision_blocks_before_save_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Исходный дефект #48: одиночный manifest.json обязан блокировать."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_bytes(SENTINELS["manifest.json"])
    calls: list[str] = []

    def spy_save(entries: list[ManifestEntry], path: Path) -> None:
        calls.append(path.name)

    monkeypatch.setattr(pipeline_mod, "save_manifest", spy_save)
    result = _run(out_dir, generate_key())

    assert result.status == ProcessingStatus.BLOCKED, result.message
    assert calls == []
    assert manifest_path.read_bytes() == SENTINELS["manifest.json"]
    assert not (out_dir / "prompt.txt").exists()
    assert not (out_dir / "route.json").exists()


def test_directory_on_target_name_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Каталог по целевому имени — занятый path entry, prepare не начат."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "manifest.json").mkdir()
    _forbid_writes(monkeypatch)

    result = _run(out_dir, generate_key())

    assert result.status == ProcessingStatus.BLOCKED, result.message
    assert (out_dir / "manifest.json").is_dir()


def _make_entry(out_dir: Path, name: str, kind: str) -> Path:
    """Занять целевое имя *name* записью типа *kind* внутри tmp_path."""
    target = out_dir / name
    if kind == "file":
        target.write_bytes(SENTINELS[name])
    elif kind == "dir":
        target.mkdir()
    elif kind == "live_symlink":
        real = out_dir / f"real-{name}"
        real.write_bytes(SENTINELS[name])
        target.symlink_to(real)
    elif kind == "broken_symlink":
        target.symlink_to(out_dir / f"missing-{name}")
    else:  # pragma: no cover - защита от опечатки в параметризации
        raise AssertionError(f"неизвестный тип записи: {kind}")
    return target


ENTRY_KINDS = ("file", "dir", "live_symlink", "broken_symlink")


@pytest.mark.parametrize("name", TARGET_NAMES)
@pytest.mark.parametrize("kind", ENTRY_KINDS)
def test_occupied_path_entry_blocks_prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    kind: str,
) -> None:
    """#63: занятый path entry блокирует overwrite=False до первой записи.

    Преемственность: заменяет характеризационный
    ``test_broken_symlink_semantics_are_characterized`` (#48, ADR-48), который
    фиксировал остаточную target-oriented semantics ``Path.exists()``.
    Остаточное поведение переведено в regression contract, а не удалено.
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    try:
        entry = _make_entry(out_dir, name, kind)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"{kind} недоступен в этом окружении: {exc!r}")
    link_target = os.readlink(entry) if kind.endswith("symlink") else None
    expected = sorted(p.name for p in out_dir.iterdir())
    _forbid_writes(monkeypatch)

    result = _run(out_dir, generate_key())

    assert result.status == ProcessingStatus.BLOCKED, (
        f"target={name} kind={kind}: {result.message}"
    )
    assert name in result.message, f"target={name} kind={kind}"
    assert result.prompt_path is None
    assert result.route_path is None
    assert result.manifest_path is None
    assert os.path.lexists(entry), f"запись удалена: target={name} kind={kind}"
    if kind == "dir":
        assert entry.is_dir()
    elif kind == "file":
        assert entry.read_bytes() == SENTINELS[name]
    else:
        assert entry.is_symlink(), f"symlink заменён: target={name} kind={kind}"
        assert os.readlink(entry) == link_target
    if kind == "broken_symlink":
        assert not entry.exists(), "broken symlink должен остаться broken"
    assert sorted(p.name for p in out_dir.iterdir()) == expected


@pytest.mark.skipif(sys.platform != "win32", reason="junction только Windows")
@pytest.mark.parametrize("name", TARGET_NAMES)
def test_live_junction_on_target_name_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """#63: Windows junction по целевому имени — занятый path entry."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    real = out_dir / f"real-{name}"
    real.mkdir()
    link = out_dir / name
    proc = subprocess.run(  # noqa: S603 - фиксированные аргументы внутри tmp_path
        ["cmd", "/c", "mklink", "/J", str(link), str(real)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not os.path.lexists(link):
        pytest.skip(
            f"junction не создан: rc={proc.returncode} {proc.stderr.strip()}"
        )
    _forbid_writes(monkeypatch)

    result = _run(out_dir, generate_key())

    assert result.status == ProcessingStatus.BLOCKED, result.message
    assert os.path.lexists(link)


# ---------------------------------------------------------------------------
# overwrite=True — поведение и порядок публикации #45 сохранены
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("combo", COMBINATIONS, ids=COMBINATION_IDS)
def test_overwrite_true_publishes_consistent_set(
    tmp_path: Path, combo: tuple[bool, bool, bool]
) -> None:
    """overwrite=True публикует новый согласованный набор артефактов."""
    out_dir = tmp_path / "out"
    seeded = _seed(out_dir, combo)

    result = _run(out_dir, generate_key(), overwrite=True)

    assert result.status == ProcessingStatus.OK, result.message
    assert result.manifest_path is not None
    assert result.route_path is not None
    assert result.prompt_path is not None
    for name in seeded:
        assert (out_dir / name).read_bytes() != SENTINELS[name]
    decoded = json.loads(result.route_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(result.manifest_path.read_bytes()).hexdigest()
    assert decoded["manifest_sha256"] == digest
    assert _unexpected(out_dir, list(TARGET_NAMES)) == []


def test_overwrite_true_keeps_publication_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порядок #45 не изменён единым preflight: manifest → prompt → route."""
    out_dir = tmp_path / "out"
    _seed(out_dir, (True, True, True))
    calls: list[str] = []
    real_write = pipeline_mod._write_atomic

    def spy_save(
        entries: list[ManifestEntry],
        path: Path,
        *,
        overwrite: bool = True,
    ) -> None:
        calls.append(path.name)
        real_save_manifest(entries, path, overwrite=overwrite)

    def spy_write(
        path: Path,
        content: str | bytes,
        mode: int | None = None,
        *,
        overwrite: bool = True,
    ) -> None:
        calls.append(path.name)
        real_write(path, content, mode, overwrite=overwrite)

    monkeypatch.setattr(pipeline_mod, "save_manifest", spy_save)
    monkeypatch.setattr(pipeline_mod, "_write_atomic", spy_write)
    result = _run(out_dir, generate_key(), overwrite=True)

    assert result.status == ProcessingStatus.OK, result.message
    assert calls == ["manifest.json", "prompt.txt", "route.json"]
