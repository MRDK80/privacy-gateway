"""Conveyor prepare — Этап Э6.

Публичный контракт:
    prepare_pipeline(text, source_ref, routing_cfg, key, out_dir, overwrite)
        -> PipelineResult

Порядок конвейера:
    ввод → детектор → жёсткая блокировка секретов → фильтрация по routing_cfg
    → токенизатор → манифест → валидатор → запись артефактов

Безопасность (fail closed):
    - Сущности с secret_kind != None блокируют обработку БЕЗУСЛОВНО,
      независимо от routing_cfg.block_unconditionally.
      Конфиг не может снять эту защиту (ADR-11).
    - prompt.txt не содержит исходных значений
    - route.json содержит только метаданные и счётчики
    - манифест зашифрован ключом из keyring

Атомарность:
    Артефакты записываются ТОЛЬКО при статусе OK.
    При BLOCKED или PENDING ни одного файла не создаётся.
    Используется запись во временный файл с последующим rename
    для обеспечения атомарности в пределах одной файловой системы.

Отклонение от хендовера (pipeline.py вместо routing.py):
    ROUTE_FORMAT_VERSION и формирование route.json фактически
    находятся здесь, а не в routing.py. Поэтому изменение версии и
    порядок записи manifest_sha256 требуют правки здесь.
    Изменения минимальны: только версия, порядок записи артефактов
    и добавление поля manifest_sha256 в route_data.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from privacy_gateway.detector import (
    DetectorConfig,
    detect_entities,
    load_config,
)
from privacy_gateway.keystore import (
    get_key as get_key,  # noqa: F401 — top-level import required for patch()
)
from privacy_gateway.manifest import build_manifest, save_manifest
from privacy_gateway.models import (
    ConfigurationError,
    ProcessingStatus,
)
from privacy_gateway.routing import RoutingConfig
from privacy_gateway.tokenizer import tokenize
from privacy_gateway.validator import validate

# Версия формата route.json — Э7 читает этот файл, версия обязательна
# Поднята до 1.1: добавлено поле manifest_sha256 (связывание route.json и manifest.json)
ROUTE_FORMAT_VERSION = "1.1"

# Права доступа к манифесту: только владелец (rw-------)
_MANIFEST_MODE = stat.S_IRUSR | stat.S_IWUSR


@dataclass
class PipelineResult:
    """Результат выполнения конвейера prepare.

    status:        ProcessingStatus (OK / BLOCKED / PENDING)
    prompt_path:   Путь к prompt.txt (только при OK)
    route_path:    Путь к route.json (только при OK)
    manifest_path: Путь к manifest.json (только при OK)
    message:       Читаемое сообщение (без исходных значений)
    findings_summary: Краткая сводка находок (типы и позиции, без значений)
    token_count:   Число созданных токенов (0 для не-OK)
    """

    status: ProcessingStatus
    message: str
    prompt_path: Path | None = None
    route_path: Path | None = None
    manifest_path: Path | None = None
    findings_summary: list[dict[str, Any]] = field(default_factory=list)
    token_count: int = 0


def _safe_findings_summary(findings: list[Any]) -> list[dict[str, Any]]:
    """Сформировать безопасную сводку находок (без значений)."""
    return [
        {
            "kind": f.kind,
            "rule": f.rule,
            "start": f.start,
            "length": f.length,
        }
        for f in findings
    ]


def _write_atomic(
    path: Path, content: str | bytes, mode: int | None = None
) -> None:
    """Записать файл атомарно через временный файл в той же директории."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix or ".tmp"
    fd, tmp_path_str = tempfile.mkstemp(dir=parent, suffix=suffix)
    tmp_path = Path(tmp_path_str)
    try:
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content
        os.write(fd, content_bytes)
        os.close(fd)
        fd = -1
        if mode is not None:
            os.chmod(tmp_path, mode)
        tmp_path.replace(path)
    except Exception:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        tmp_path.unlink(missing_ok=True)
        raise


def prepare_pipeline(
    text: str,
    source_ref: str,
    routing_cfg: RoutingConfig,
    key: bytes,
    out_dir: Path,
    overwrite: bool = False,
    entities_config_path: Path | None = None,
) -> PipelineResult:
    """Выполнить полный конвейер подготовки текста.

    Порядок: детектор → жёсткая блокировка секретов → фильтрация по routing_cfg
    → токенизатор → манифест → валидатор → запись артефактов.

    Запись артефактов при OK (в порядке):
    1. manifest.json — атомарно через save_manifest + chmod
    2. SHA-256 читается из финального пути manifest_path
    3. prompt.txt — атомарно
    4. route.json с manifest_sha256 — атомарно

    Args:
        text:                 Входной текст.
        source_ref:           Строка-ссылка на источник.
        routing_cfg:          Конфигурация маршрутизации.
        key:                  Fernet-ключ.
        out_dir:              Каталог для артефактов.
        overwrite:            Разрешить перезапись.
        entities_config_path: Путь к конфигу детектора.

    Returns:
        PipelineResult с итоговым статусом.

    Raises:
        ConfigurationError: Ошибка конфигурации.
        OSError:            Ошибка записи.
    """
    # --- Пустой ввод ---
    if not text.strip():
        return PipelineResult(
            status=ProcessingStatus.BLOCKED,
            message="Input text is empty or contains only whitespace.",
        )

    # --- Проверка существующих артефактов ---
    prompt_path = out_dir / "prompt.txt"
    route_path = out_dir / "route.json"
    manifest_path = out_dir / "manifest.json"

    if not overwrite:
        existing = [p for p in (prompt_path, route_path) if p.exists()]
        if existing:
            names = ", ".join(p.name for p in existing)
            return PipelineResult(
                status=ProcessingStatus.BLOCKED,
                message=(
                    f"Output file(s) already exist: {names}. "
                    "Use --overwrite to allow replacement."
                ),
            )

    # --- Загрузка конфига детектора ---
    _default_config_path = Path("config.example") / "entities.yaml"
    config_path = entities_config_path or _default_config_path
    try:
        detector_cfg: DetectorConfig = load_config(config_path)
    except ConfigurationError:
        raise

    # --- Детектор ---
    entities = detect_entities(text, detector_cfg)

    # --- Жёсткая блокировка секретов (fail closed, ADR-11) ---
    secret_entities = [e for e in entities if e.secret_kind is not None]
    if secret_entities:
        summary = [
            {
                "type": e.entity_type.value,
                "secret_kind": e.secret_kind,
                "start": e.start,
            }
            for e in secret_entities
        ]
        return PipelineResult(
            status=ProcessingStatus.BLOCKED,
            message=(
                f"Found {len(secret_entities)} secret(s) in input. "
                "Processing aborted (fail closed)."
            ),
            findings_summary=summary,
        )

    # --- Блокировка по routing_cfg.block_unconditionally ---
    blocked_types = frozenset(routing_cfg.block_unconditionally)
    allowed_types = frozenset(routing_cfg.tokenize_types)

    found_blocked = [e for e in entities if e.entity_type.value in blocked_types]
    if found_blocked:
        summary = [
            {"type": e.entity_type.value, "start": e.start}
            for e in found_blocked
        ]
        return PipelineResult(
            status=ProcessingStatus.BLOCKED,
            message=(
                f"Found {len(found_blocked)} entity(ies) of unconditionally "
                "blocked type(s). Processing aborted."
            ),
            findings_summary=summary,
        )

    # --- Фильтрация: только разрешённые типы ---
    entities_to_tokenize = [
        e for e in entities if e.entity_type.value in allowed_types
    ]

    original_values = [text[e.start:e.end] for e in entities_to_tokenize]

    # --- Токенизатор ---
    tokenized_text, token_records = tokenize(
        text, entities_to_tokenize, original_values
    )

    # --- Манифест ---
    fp_to_value: dict[str, str] = {
        e.fingerprint: text[e.start:e.end] for e in entities_to_tokenize
    }
    manifest_values = [fp_to_value.get(r.fingerprint, "") for r in token_records]
    manifest_entries = build_manifest(token_records, manifest_values, key)

    # --- Валидатор ---
    validation = validate(tokenized_text)

    token_counts: dict[str, int] = {}
    for r in token_records:
        token_counts[r.entity_type.value] = (
            token_counts.get(r.entity_type.value, 0) + 1
        )

    if validation.status != ProcessingStatus.OK:
        findings_summary = _safe_findings_summary(validation.findings)
        status_word = validation.status.value
        if validation.findings:
            rules_hit = sorted({f.rule for f in validation.findings})
            msg = (
                f"Validation {status_word}: rules triggered: {rules_hit}. "
                "Check type and position in findings_summary "
                "(no original values disclosed)."
            )
        else:
            msg = f"Validation {status_word}."
        return PipelineResult(
            status=validation.status,
            message=msg,
            findings_summary=findings_summary,
        )

    # --- Запись артефактов (только при OK) ---
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(tz=UTC).isoformat()

    # Шаг 1: атомарная запись manifest.json (через save_manifest, ADR-12)
    save_manifest(manifest_entries, manifest_path)
    try:
        os.chmod(manifest_path, _MANIFEST_MODE)
    except OSError:
        pass  # Windows не поддерживает chmod

    # Шаг 2: читаем SHA-256 из финального файла (после rename)
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    # Шаг 3: prompt.txt
    _write_atomic(prompt_path, tokenized_text)

    # Шаг 4: route.json с manifest_sha256 (атомарно)
    token_count = len(token_records)
    route_data: dict[str, Any] = {
        "format_version": ROUTE_FORMAT_VERSION,
        "status": ProcessingStatus.OK.value,
        "timestamp": timestamp,
        "source_ref": source_ref,
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "token_count": token_count,
        "token_counts_by_type": token_counts,
        "entity_count_detected": len(entities),
        "entity_count_tokenized": len(token_records),
    }
    route_json = json.dumps(route_data, ensure_ascii=False, indent=2)
    _write_atomic(route_path, route_json)

    return PipelineResult(
        status=ProcessingStatus.OK,
        message="OK: artifacts created.",
        prompt_path=prompt_path,
        route_path=route_path,
        manifest_path=manifest_path,
        token_count=token_count,
    )
