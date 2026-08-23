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


def test_broken_symlink_semantics_are_characterized(tmp_path: Path) -> None:
    """Характеризация остаточной semantics: Path.exists() и broken symlink.

    ADR-48: unify path-entry semantics (lexists/is_symlink) в scope #48 не
    вводится — фиксируется как остаточное поведение, единое для всех трёх
    артефактов.
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    link = out_dir / "manifest.json"
    try:
        link.symlink_to(out_dir / "missing-target.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlink недоступен без привилегий на этой платформе")

    assert link.is_symlink()
    assert not link.exists()


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

    def spy_save(entries: list[ManifestEntry], path: Path) -> None:
        calls.append(path.name)
        real_save_manifest(entries, path)

    def spy_write(
        path: Path, content: str | bytes, mode: int | None = None
    ) -> None:
        calls.append(path.name)
        real_write(path, content, mode)

    monkeypatch.setattr(pipeline_mod, "save_manifest", spy_save)
    monkeypatch.setattr(pipeline_mod, "_write_atomic", spy_write)
    result = _run(out_dir, generate_key(), overwrite=True)

    assert result.status == ProcessingStatus.OK, result.message
    assert calls == ["manifest.json", "prompt.txt", "route.json"]
