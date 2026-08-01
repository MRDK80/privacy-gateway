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
from privacy_gateway.models import ConfigurationError, ManifestEntry, RestoreStrictError
from privacy_gateway.routing import verify_manifest_integrity

# Регулярное выражение для поиска кандидатов на токены (включая искажённые).
# Ищет что-либо похожее на [WORD...] — для последующей классификации.
_TOKEN_CANDIDATE_RE = re.compile(r"\[([^\[\]\n]+)\]")

# Паттерн корректного токена: [TYPE_N], где TYPE — заглавные буквы/цифры,
# N — целое число >= 1 (ADR-19: регистрозависимо).
_VALID_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9]*_[1-9][0-9]*$")


@dataclass
class RestoreResult:
    """Результат восстановления — только счётчики и токены, без значений.

    Поля:
        restored_text:      Восстановленный текст (None при ошибке строгого режима).
        tokens_expected:    Множество токенов из манифеста.
        tokens_found:       Токены, успешно подставленные.
        tokens_missing:     Токены из манифеста, не найденные в ответе.
        tokens_unknown:     Токены в ответе, не из манифеста.
        tokens_malformed:   Кандидаты, не прошедшие валидацию формата.
        tokens_duplicated:  Токены, встречающиеся в ответе более одного раза.
        warnings:           Предупреждения (мягкий режим).
        strict:             Применялся ли строгий режим.
    """

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
    """Разрешить путь к manifest.json (ADR-15).

    Если передан явный override — использовать его.
    Иначе путь из route.json разрешается относительно каталога route.json,
    а не рабочего каталога.

    Args:
        route_data:             Словарь из route.json.
        route_path:             Путь к route.json.
        manifest_path_override: Явный путь (из --manifest флага) или None.

    Returns:
        Абсолютный Path к manifest.json.

    Raises:
        ConfigurationError: Поле manifest_path отсутствует в route.json.
    """
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
    # Разрешаем относительно каталога route.json (ADR-15)
    return (route_path.parent / candidate).resolve()


def _classify_candidates(
    text: str,
    manifest_tokens: set[str],
) -> tuple[
    dict[str, list[tuple[int, int]]],  # known: token -> [(start, end), ...]
    set[str],                           # unknown valid tokens
    list[str],                          # malformed candidates
]:
    """Найти и классифицировать все кандидаты на токены в тексте.

    Регистрозависимо (ADR-19): [email_1] — искажённый, [EMAIL_1] — известный.

    Returns:
        known:     dict token -> list of (start, end) span positions.
        unknown:   set valid-format tokens not in manifest.
        malformed: list of raw candidate strings (внутри скобок) с неверным форматом.
    """
    known: dict[str, list[tuple[int, int]]] = {}
    unknown: set[str] = set()
    malformed: list[str] = []
    seen_malformed: set[str] = set()

    for m in _TOKEN_CANDIDATE_RE.finditer(text):
        inner = m.group(1)  # содержимое без скобок
        start, end = m.start(), m.end()

        if _VALID_TOKEN_RE.match(inner):
            # Формат корректен
            if inner in manifest_tokens:
                known.setdefault(inner, []).append((start, end))
            else:
                unknown.add(inner)
        else:
            # Искажённый формат; строгий отказ определяется ADR-16
            if inner not in seen_malformed:
                malformed.append(inner)
                seen_malformed.add(inner)

    return known, unknown, malformed


def _substitute(
    text: str,
    known: dict[str, list[tuple[int, int]]],
    value_map: dict[str, str],
) -> str:
    """Подставить значения для всех известных токенов (ADR-18).

    Обрабатывает дубли — все вхождения заменяются.
    Работает справа налево, чтобы не сдвигать позиции.
    """
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

    load_manifest из manifest.py принимает один ключ и бросает DecryptionError
    при несовпадении. Здесь реализован перебор ключей в порядке get_all_keys()
    ([active, retired, ...]), что обеспечивает чтение манифестов, созданных
    до ротации, без ручных действий.

    Args:
        manifest_path: Путь к manifest.json.
        keys:          Список ключей в порядке приоритета.

    Returns:
        Список ManifestEntry.

    Raises:
        ConfigurationError: Ни один ключ не подошёл или файл повреждён.
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
        f"Возможно, манифест зашифрован другим ключом или повреждён. "
        f"Детали: {last_exc}"
    ) from last_exc


def restore_text(
    llm_response: str,
    route_path: Path,
    manifest_path_override: Path | None = None,
    strict: bool = True,
) -> RestoreResult:
    """Восстановить исходный текст, подставив значения токенов.

    Порядок проверок строго соблюдается (ADR-14, ADR-15):
    verify_manifest_integrity вызывается до любой работы с манифестом.

    Расшифровка через MultiFernet (ADR-23): манифесты, созданные до
    ротации ключа, остаются читаемы без ручных действий.

    Args:
        llm_response:           Текст ответа LLM.
        route_path:             Путь к route.json.
        manifest_path_override: Явный путь к манифесту (переопределяет route.json).
        strict:                 True = строгий режим (по умолчанию, ADR-16).

    Returns:
        RestoreResult. При строгом отказе restored_text is None.

    Raises:
        ConfigurationError:  Ошибка формата route.json или нарушение целостности.
        KeystoreError:       Ключ не найден или backend небезопасен.
        RestoreError:        Ошибка конфигурации/целостности → код 3.
        RestoreStrictError:  Строгий отказ по неизвестному/искажённому токену → код 5.
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
            f"Запустите 'pgw key create' для создания ключа. Детали: {exc}"
        ) from exc

    # Перебираем все ключи до первого успешного (ADR-23: обратная совместимость ротации).
    entries: list[ManifestEntry] = _load_manifest_multi_key(manifest_path, keys)

    # Построить словарь token -> plaintext через decrypt_multi (ADR-23)
    value_map: dict[str, str] = {}
    for entry in entries:
        token_key = entry.token.strip("[]")
        try:
            value_map[token_key] = decrypt_multi(entry.encrypted_value, keys)
        except DecryptionError as exc:
            raise ConfigurationError(
                f"Не удалось расшифровать запись манифеста для токена {entry.token!r}. "
                f"Детали: {exc}"
            ) from exc

    manifest_tokens = set(value_map.keys())
    result.tokens_expected = set(manifest_tokens)

    # --- 5. Классифицировать токены и применить подстановку ---
    known, unknown, malformed = _classify_candidates(llm_response, manifest_tokens)

    # Дублированные токены — более одного вхождения (ADR-18)
    for token, positions in known.items():
        if len(positions) > 1:
            result.tokens_duplicated.add(token)

    result.tokens_found = set(known.keys())
    result.tokens_missing = manifest_tokens - result.tokens_found
    result.tokens_unknown = unknown
    result.tokens_malformed = malformed

    # Предупреждения о пропавших токенах (ADR-17 — не ошибка, но обязательно в отчёт)
    for token in sorted(result.tokens_missing):
        result.warnings.append(f"Токен отсутствует в ответе LLM: {token}")

    # Строгий режим: неизвестные и искажённые — RestoreStrictError (код 5, ADR-21)
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

    # Мягкий режим: предупреждения вместо ошибки
    if not strict:
        for token in sorted(unknown):
            result.warnings.append(
                f"Неизвестный токен (оставлен как есть): [{token}]"
            )
        for candidate in malformed:
            result.warnings.append(
                f"Искажённый кандидат (оставлен как есть): [{candidate}]"
            )

    # Подстановка
    restored = _substitute(llm_response, known, value_map)
    result.restored_text = restored
    return result


def write_restored(
    text: str,
    out_path: Path,
    overwrite: bool = False,
) -> None:
    """Атомарно записать восстановленный текст в файл (ADR-12).

    Args:
        text:      Восстановленный текст.
        out_path:  Путь к результирующему файлу.
        overwrite: Перезаписывать ли существующий файл.

    Raises:
        FileExistsError:    Файл уже существует и overwrite=False.
        ConfigurationError: Ошибка записи.
    """
    if out_path.exists() and not overwrite:
        raise FileExistsError(
            f"Файл уже существует: {out_path}. "
            f"Используйте флаг --overwrite для перезаписи."
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Атомарная запись через временный файл в том же каталоге (ADR-12)
    try:
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
