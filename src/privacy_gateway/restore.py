"""Модуль восстановления — Этап Э7 / Э8.

Публичный контракт:
    restore_text(
        llm_response: str,
        route_path: Path,
        manifest_path_override: Path | None,
        strict: bool,
    ) -> RestoreResult

Порядок проверок (обязателен; ADR-14, ADR-15):
    1. Прочитать и разобрать route.json, проверить поддерживаемую версию.
    2. Разрешить manifest_path относительно каталога route.json (ADR-15).
    3. Вызвать verify_manifest_integrity — до загрузки, расшифровки, обработки.
    4. Загрузить манифест, получить ключи из keyring, расшифровать записи.
    5. Классифицировать токены в ответе LLM, применить подстановку.
    6. Атомарно записать результат (ADR-12).

Классификация токенов:
    - Известный       — подставить значение во все вхождения.
    - Неизвестный     — строгий режим: RestoreStrictError (5); мягкий: предупреждение.
    - Искажённый      — строгий режим: RestoreStrictError (5); мягкий: предупреждение.
    - Пропавший       — всегда предупреждение, не ошибка (ADR-17).
    - Дублированный   — подстановка во все вхождения, счётчик в отчёт (ADR-18).

Отчёт (RestoreResult) содержит только счётчики и токены — без значений.

ADR-15: manifest_path разрешается относительно каталога route.json.
ADR-16: Строгий режим по умолчанию; мягкий — только по явному флагу --lenient.
ADR-17: Пропавший токен — предупреждение, не ошибка.
ADR-18: Дублированный токен — подстановка во все вхождения.
ADR-19: Чувствительность к регистру — токены регистрозависимы ([EMAIL_1] ≠ [email_1]).
ADR-21: Разведение кодов 3 и 5 — строгий токенный отказ → RestoreStrictError (5).
ADR-23: MultiFernet обеспечивает чтение манифестов, созданных до ротации.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from privacy_gateway.crypto import DecryptionError, decrypt_multi
from privacy_gateway.keystore import KeyNotFoundError, KeystoreError, get_all_keys
from privacy_gateway.manifest import load_manifest
from privacy_gateway.models import (
    ConfigurationError,
    ManifestEntry,
    RestoreStrictError,
)
from privacy_gateway.routing import verify_manifest_integrity

_TOKEN_CANDIDATE_RE = re.compile(r"\[([^\[\]\n]+)\]")
_VALID_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9]*_[1-9][0-9]*$")


@dataclass
class RestoreResult:
    """Результат восстановления — только счётчики и токены, без значений."""

    restored_text: str | None = None
    tokens_expected: set[str] = field(default_factory=set)
    tokens_found: set[str] = field(default_factory=set)
    tokens_missing: set[str] = field(default_factory=set)
    tokens_unknown: set[str] = field(default_factory=set)
    tokens_malformed: list[str] = field(default_factory=list)
    tokens_duplicated: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    strict: bool = True

    @property
    def tokens_expected_count(self) -> int:
        return len(self.tokens_expected)

    @property
    def tokens_found_count(self) -> int:
        return len(self.tokens_found)

    @property
    def tokens_missing_count(self) -> int:
        return len(self.tokens_missing)

    @property
    def tokens_unknown_count(self) -> int:
        return len(self.tokens_unknown)

    @property
    def tokens_malformed_count(self) -> int:
        return len(self.tokens_malformed)


class RestoreError(Exception):
    """Ошибка восстановления: ошибка конфигурации / целостности → код 3 (ADR-21)."""


def _resolve_manifest_path(
    route_data: dict,  # type: ignore[type-arg]
    route_path: Path,
    manifest_path_override: Path | None,
) -> Path:
    """Разрешить путь к manifest.json (ADR-15)."""
    if manifest_path_override is not None:
        return manifest_path_override.resolve()

    raw = route_data.get("manifest_path")
    if not raw:
        raise ConfigurationError(
            "route.json is missing required field 'manifest_path'."
        )

    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return (route_path.parent / candidate).resolve()


def _classify_candidates(
    text: str,
    manifest_tokens: set[str],
) -> tuple[
    dict[str, list[tuple[int, int]]],
    set[str],
    list[str],
]:
    """Найти и классифицировать все кандидаты на токены в тексте."""
    known: dict[str, list[tuple[int, int]]] = {}
    unknown: set[str] = set()
    malformed: list[str] = []
    seen_malformed: set[str] = set()

    for m in _TOKEN_CANDIDATE_RE.finditer(text):
        inner = m.group(1)
        start, end = m.start(), m.end()

        if _VALID_TOKEN_RE.match(inner):
            if inner in manifest_tokens:
                known.setdefault(inner, []).append((start, end))
            else:
                unknown.add(inner)
        else:
            if inner not in seen_malformed:
                malformed.append(inner)
                seen_malformed.add(inner)

    return known, unknown, malformed


def _substitute(
    text: str,
    known: dict[str, list[tuple[int, int]]],
    value_map: dict[str, str],
) -> str:
    """Подставить значения для всех известных токенов (ADR-18)."""
    spans: list[tuple[int, int, str]] = []
    for token, positions in known.items():
        for start, end in positions:
            spans.append((start, end, token))
    spans.sort(key=lambda x: x[0], reverse=True)

    result = text
    for start, end, token in spans:
        result = result[:start] + value_map[token] + result[end:]
    return result


def _load_manifest_multi_key(
    manifest_path: Path,
    keys: list[bytes],
) -> list[ManifestEntry]:
    """Загрузить манифест, перебирая ключи до первого успешного (ADR-23).

    Обеспечивает чтение манифестов, созданных до ротации, без ручных действий.
    """
    last_exc: DecryptionError | None = None
    for key in keys:
        try:
            return load_manifest(manifest_path, key)
        except DecryptionError as exc:
            last_exc = exc
            continue
    raise ConfigurationError(
        f"Не удалось загрузить манифест {manifest_path}: "
        f"ни один из {len(keys)} ключей не подошёл. "
        f"Детали: {last_exc}"
    ) from last_exc


def restore_text(
    llm_response: str,
    route_path: Path,
    manifest_path_override: Path | None = None,
    strict: bool = True,
) -> RestoreResult:
    """Восстановить исходный текст, подставив значения токенов.

    Порядок проверок строго соблюдается (ADR-14, ADR-15).
    Расшифровка через MultiFernet (ADR-23): манифесты, созданные до
    ротации, остаются читаемы без ручных действий.
    """
    result = RestoreResult(strict=strict)

    # --- 1. Прочитать и разобрать route.json ---
    try:
        route_raw = route_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"Cannot read route.json at {route_path}: {exc}"
        ) from exc

    try:
        route_data: dict = json.loads(route_raw)  # type: ignore[type-arg]
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"route.json is not valid JSON at {route_path}: {exc}"
        ) from exc

    # --- 2. Разрешить manifest_path (ADR-15) ---
    manifest_path = _resolve_manifest_path(
        route_data, route_path, manifest_path_override
    )

    # --- 3. verify_manifest_integrity — ДО любой загрузки/расшифровки ---
    verify_manifest_integrity(route_data, manifest_path)

    # --- 4. Получить все ключи, загрузить манифест через MultiFernet (ADR-23) ---
    try:
        keys = get_all_keys()
    except KeyNotFoundError as exc:
        raise KeystoreError(
            f"Ключ Fernet не найден в keyring. "
            f"Запустите 'pgw key create' для создания ключа. "
            f"Детали: {exc}"
        ) from exc

    # Перебираем все ключи до первого успешного (ADR-23).
    entries: list[ManifestEntry] = _load_manifest_multi_key(
        manifest_path, keys
    )

    # Строим словарь token -> plaintext через decrypt_multi (ADR-23)
    value_map: dict[str, str] = {}
    for entry in entries:
        token_key = entry.token.strip("[]")
        try:
            value_map[token_key] = decrypt_multi(entry.encrypted_value, keys)
        except DecryptionError as exc:
            raise ConfigurationError(
                f"Не удалось расшифровать запись манифеста "
                f"для токена {entry.token!r}. Детали: {exc}"
            ) from exc

    manifest_tokens = set(value_map.keys())
    result.tokens_expected = set(manifest_tokens)

    # --- 5. Классифицировать токены и применить подстановку ---
    known, unknown, malformed = _classify_candidates(llm_response, manifest_tokens)

    for token, positions in known.items():
        if len(positions) > 1:
            result.tokens_duplicated.add(token)

    result.tokens_found = set(known.keys())
    result.tokens_missing = manifest_tokens - result.tokens_found
    result.tokens_unknown = unknown
    result.tokens_malformed = malformed

    for token in sorted(result.tokens_missing):
        result.warnings.append(f"Токен отсутствует в ответе LLM: {token}")

    if strict and (unknown or malformed):
        issues: list[str] = []
        if unknown:
            issues.append(f"Неизвестные токены: {sorted(unknown)}")
        if malformed:
            issues.append(f"Искажённые кандидаты на токены: {malformed}")
        raise RestoreStrictError(
            "Строгий режим: в ответе LLM обнаружены недопустимые токены. "
            + "; ".join(issues)
        )

    if not strict:
        for token in sorted(unknown):
            result.warnings.append(
                f"Неизвестный токен (оставлен как есть): [{token}]"
            )
        for candidate in malformed:
            result.warnings.append(
                f"Искажённый кандидат (оставлен как есть): [{candidate}]"
            )

    restored = _substitute(llm_response, known, value_map)
    result.restored_text = restored
    return result


def write_restored(
    text: str,
    out_path: Path,
    overwrite: bool = False,
) -> None:
    """Атомарно записать восстановленный текст в файл (ADR-12)."""
    if out_path.exists() and not overwrite:
        raise FileExistsError(
            f"Файл уже существует: {out_path}. "
            f"Используйте флаг --overwrite для перезаписи."
        )

    # Подготовка каталога входит в ту же границу OSError, что и атомарная
    # запись (#36, ADR-33): отказ файловой системы на любом шаге даёт
    # ConfigurationError и код 3, не-OSError по-прежнему проходит наружу.
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=out_path.parent, suffix=".tmp", prefix=".pgw_restore_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        os.replace(tmp_name, out_path)
    except OSError as exc:
        raise ConfigurationError(
            f"Не удалось записать результат в {out_path}: {exc}"
        ) from exc
