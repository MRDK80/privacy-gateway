"""Характеризация порядка публикации артефактов prepare — #45, #48.

Фиксируют фактический порядок записи (подтверждён кодом, не только
документацией): manifest.json публикуется первым, затем prompt.txt,
затем route.json с manifest_sha256. Порядок в #45 и #48 не меняется —
тесты защищают его от регрессии вместе с атомарной публикацией.

Единая preflight overwrite-policy трёх артефактов закрыта в #48
(ADR-48): наличие только manifest.json при overwrite=False блокирует
prepare до первой записи. Прежний характеризационный тест обратного
поведения инвертирован и оставлен здесь как regression contract на
границе с #45: атомарная замена одного файла и preflight-коллизия —
разные гарантии.

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


def _run(out_dir: Path, key: bytes, overwrite: bool = False) -> PipelineResult:
    return prepare_pipeline(
        text=SYNTH_TEXT,
        source_ref="test_pipeline_publication_order.txt",
        routing_cfg=load_routing_config(None),
        key=key,
        out_dir=out_dir,
        overwrite=overwrite,
    )


def test_manifest_published_before_prompt_and_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Фактический порядок: manifest.json → prompt.txt → route.json."""
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
    result = _run(tmp_path / "out", generate_key())
    monkeypatch.undo()

    assert result.status == ProcessingStatus.OK, result.message
    assert calls == ["manifest.json", "prompt.txt", "route.json"]


def test_route_json_binds_published_manifest(tmp_path: Path) -> None:
    """route.json содержит sha256 именно опубликованного манифеста."""
    out_dir = tmp_path / "out"
    result = _run(out_dir, generate_key())
    assert result.status == ProcessingStatus.OK, result.message
    assert result.manifest_path is not None
    assert result.route_path is not None
    decoded = json.loads(result.route_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(result.manifest_path.read_bytes()).hexdigest()
    assert decoded["manifest_sha256"] == digest


def test_manifest_failure_leaves_prompt_and_route_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ публикации манифеста: prompt/route не создаются."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text("[]", encoding="utf-8")
    old_bytes = manifest_path.read_bytes()

    def failing_save(
        entries: list[ManifestEntry],
        path: Path,
        *,
        overwrite: bool = True,
    ) -> None:
        raise OSError("manifest publication failed")

    monkeypatch.setattr(pipeline_mod, "save_manifest", failing_save)
    with pytest.raises(OSError, match="manifest publication failed"):
        _run(out_dir, generate_key(), overwrite=True)
    monkeypatch.undo()

    assert manifest_path.read_bytes() == old_bytes
    assert not (out_dir / "prompt.txt").exists()
    assert not (out_dir / "route.json").exists()


def test_existing_manifest_alone_blocks_prepare(tmp_path: Path) -> None:
    """#48: одиночный manifest.json блокирует prepare при overwrite=False."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text("[]", encoding="utf-8")
    old_bytes = manifest_path.read_bytes()

    result = _run(out_dir, generate_key())

    assert result.status == ProcessingStatus.BLOCKED, result.message
    assert "manifest.json" in result.message
    assert manifest_path.read_bytes() == old_bytes
    assert not (out_dir / "prompt.txt").exists()
    assert not (out_dir / "route.json").exists()
