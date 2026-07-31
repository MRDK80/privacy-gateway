"""Функциональные тесты восстановления — Этап Э7 / Э8.

Покрывают API restore_text() и сквозной маршрут prepare → restore.

Синтетика: user@example.com, 192.0.2.10, 2001:db8::1, +7 900 000-00-00.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway.crypto import generate_key
from privacy_gateway.models import ConfigurationError, RestoreStrictError
from privacy_gateway.restore import RestoreError, restore_text

# ---------------------------------------------------------------------------
# Synthetic test data (not real PII)
# ---------------------------------------------------------------------------

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"
SYNTH_IP6 = "2001:db8::1"
SYNTH_PHONE = "+7 900 000-00-00"
SYNTH_TEXT = (
    f"Связь: {SYNTH_EMAIL}, {SYNTH_IP}, {SYNTH_PHONE}\n"
)
SYNTH_UNICODE = (
    "Получатель: Иван Иванов <user@example.com>,"  # pragma: allowlist secret
    " адрес: 192.0.2.10\n"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fernet_key() -> bytes:
    return generate_key()


@pytest.fixture()
def mock_keyring(fernet_key: bytes):
    """Подменяет get_all_keys в pipeline и restore без обращения к реальному keyring."""
    with patch("privacy_gateway.pipeline.get_key", return_value=fernet_key):
        with patch("privacy_gateway.restore.get_all_keys", return_value=[fernet_key]):
            yield fernet_key


def _run_prepare(tmp_path: Path, key: bytes, text: str = SYNTH_TEXT) -> Path:
    """Запустить prepare_pipeline и вернуть out_dir. Проверяет статус OK."""
    from privacy_gateway.models import ProcessingStatus
    from privacy_gateway.pipeline import prepare_pipeline
    from privacy_gateway.routing import load_routing_config

    out_dir = tmp_path / "out"
    cfg = load_routing_config(None)
    result = prepare_pipeline(
        text=text,
        source_ref="test_restore.txt",
        routing_cfg=cfg,
        key=key,
        out_dir=out_dir,
    )
    assert result.status == ProcessingStatus.OK, f"prepare failed: {result.message}"
    return out_dir


# ---------------------------------------------------------------------------
# 1. Сквозной round-trip — побайтовое совпадение
# ---------------------------------------------------------------------------


def test_roundtrip_exact(tmp_path: Path, mock_keyring: bytes) -> None:
    """prepare → restore без изменений возвращает исходный текст побайтово."""
    key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    prompt = (out_dir / "prompt.txt").read_text(encoding="utf-8")
    route_path = out_dir / "route.json"

    result = restore_text(prompt, route_path)

    assert result.restored_text is not None
    assert result.restored_text == SYNTH_TEXT
    assert result.tokens_found_count == result.tokens_expected_count
    assert not result.tokens_missing
    assert not result.tokens_unknown
    assert not result.tokens_malformed


# ---------------------------------------------------------------------------
# 2. Unicode / кириллица
# ---------------------------------------------------------------------------


def test_roundtrip_unicode(tmp_path: Path, mock_keyring: bytes) -> None:
    """Round-trip корректно работает с кириллицей и Unicode."""
    key = mock_keyring
    out_dir = _run_prepare(tmp_path, key, text=SYNTH_UNICODE)
    prompt = (out_dir / "prompt.txt").read_text(encoding="utf-8")
    route_path = out_dir / "route.json"

    result = restore_text(prompt, route_path)

    assert result.restored_text is not None
    assert result.restored_text == SYNTH_UNICODE
    assert SYNTH_EMAIL not in " ".join(result.warnings)


# ---------------------------------------------------------------------------
# 3. Неизвестный токен — строгий режим → RestoreStrictError (ADR-21)
# ---------------------------------------------------------------------------


def test_unknown_token_strict_fails(tmp_path: Path, mock_keyring: bytes) -> None:
    """Корректный формат, но отсутствующий токен → RestoreStrictError."""
    key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    prompt = (out_dir / "prompt.txt").read_text(encoding="utf-8")
    llm_reply = prompt + " [EMAIL_99]"
    route_path = out_dir / "route.json"

    with pytest.raises(RestoreStrictError) as exc_info:
        restore_text(llm_reply, route_path, strict=True)

    msg = str(exc_info.value)
    assert "EMAIL_99" in msg
    assert SYNTH_EMAIL not in msg
    assert SYNTH_IP not in msg
    assert SYNTH_PHONE not in msg


# ---------------------------------------------------------------------------
# 4. Искажённые кандидаты фиксируются → RestoreStrictError
# ---------------------------------------------------------------------------


def test_malformed_token_detected(tmp_path: Path, mock_keyring: bytes) -> None:
    """Кандидаты с неверным форматом попадают в tokens_malformed."""
    key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    route_path = out_dir / "route.json"

    malformed_candidates = ["EMAIL", "EMAIL 1", "email_1"]
    llm_reply = " ".join(f"[{c}]" for c in malformed_candidates)

    with pytest.raises(RestoreStrictError):
        restore_text(llm_reply, route_path, strict=True)

    result = restore_text(llm_reply, route_path, strict=False)
    assert len(result.tokens_malformed) == len(malformed_candidates)
    for candidate in malformed_candidates:
        assert candidate in result.tokens_malformed


# ---------------------------------------------------------------------------
# 5. Пропавший токен — предупреждение, не ошибка
# ---------------------------------------------------------------------------


def test_missing_token_reported(tmp_path: Path, mock_keyring: bytes) -> None:
    """Известный токен, удалённый из ответа, попадает в tokens_missing."""
    import re

    key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    route_path = out_dir / "route.json"
    prompt = (out_dir / "prompt.txt").read_text(encoding="utf-8")

    tokens_in_prompt = re.findall(r"\[[A-Z][A-Z0-9]*_[1-9][0-9]*\]", prompt)
    assert tokens_in_prompt, "В prompt.txt не нашли токенов — тест некорректен"

    first_token = tokens_in_prompt[0]
    llm_reply = prompt.replace(first_token, "", 1)

    result = restore_text(llm_reply, route_path, strict=True)

    assert result.restored_text is not None
    assert first_token.strip("[]") in result.tokens_missing
    assert any(first_token.strip("[]") in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 6. Дублированный токен — все вхождения заменяются
# ---------------------------------------------------------------------------


def test_duplicated_token_behaviour(tmp_path: Path, mock_keyring: bytes) -> None:
    """Дубль известного токена — все вхождения заменяются, токен в tokens_duplicated."""
    import re

    key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    route_path = out_dir / "route.json"
    prompt = (out_dir / "prompt.txt").read_text(encoding="utf-8")

    tokens_in_prompt = re.findall(r"\[[A-Z][A-Z0-9]*_[1-9][0-9]*\]", prompt)
    assert tokens_in_prompt

    first_token = tokens_in_prompt[0]
    llm_reply = prompt + f" {first_token}"

    result = restore_text(llm_reply, route_path, strict=True)

    assert result.restored_text is not None
    token_key = first_token.strip("[]")
    assert token_key in result.tokens_duplicated
    assert first_token not in result.restored_text


# ---------------------------------------------------------------------------
# 7. Мягкий режим требует явного флага
# ---------------------------------------------------------------------------


def test_lenient_mode_requires_flag(tmp_path: Path, mock_keyring: bytes) -> None:
    """Строгий режим по умолчанию; мягкий допускает неизвестные токены."""
    key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    route_path = out_dir / "route.json"
    llm_reply = "Результат: [EMAIL_99] готово"

    # Строгий — RestoreStrictError (ADR-21)
    with pytest.raises(RestoreStrictError):
        restore_text(llm_reply, route_path)  # strict=True по умолчанию

    # Мягкий — успех, токен оставлен как есть
    result = restore_text(llm_reply, route_path, strict=False)
    assert result.restored_text is not None
    assert "[EMAIL_99]" in result.restored_text
    assert "EMAIL_99" in result.tokens_unknown


# ---------------------------------------------------------------------------
# 8. Формат route.json 1.1 принимается
# ---------------------------------------------------------------------------


def test_accepts_format_version_1_1(tmp_path: Path, mock_keyring: bytes) -> None:
    """Стандартный путь: format_version 1.1 с корректным manifest_sha256."""
    key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)

    route_data = json.loads((out_dir / "route.json").read_text(encoding="utf-8"))
    assert route_data["format_version"] == "1.1"
    assert "manifest_sha256" in route_data

    prompt = (out_dir / "prompt.txt").read_text(encoding="utf-8")
    result = restore_text(prompt, out_dir / "route.json")
    assert result.restored_text is not None


# ---------------------------------------------------------------------------
# 9. Формат route.json 1.0 — обратная совместимость
# ---------------------------------------------------------------------------


def test_accepts_format_version_1_0(tmp_path: Path, fernet_key: bytes) -> None:
    """format_version 1.0 не требует manifest_sha256; restore работает."""
    from privacy_gateway.manifest import build_manifest, save_manifest
    from privacy_gateway.models import EntityType, TokenRecord

    key = fernet_key
    records = [
        TokenRecord(
            token="[EMAIL_1]",
            entity_type=EntityType.EMAIL,
            fingerprint="fp1",
        )
    ]
    values = [SYNTH_EMAIL]
    entries = build_manifest(records, values, key)

    out_dir = tmp_path / "out10"
    out_dir.mkdir()
    manifest_path = out_dir / "manifest.json"
    save_manifest(entries, manifest_path)

    route_data = {
        "format_version": "1.0",
        "status": "OK",
        "manifest_path": "manifest.json",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    route_path = out_dir / "route.json"
    route_path.write_text(json.dumps(route_data), encoding="utf-8")

    with patch("privacy_gateway.restore.get_all_keys", return_value=[key]):
        result = restore_text("Contact: [EMAIL_1]", route_path)

    assert result.restored_text is not None
    assert SYNTH_EMAIL in result.restored_text


# ---------------------------------------------------------------------------
# 10. Неизвестная версия формата — отказ до расшифровки
# ---------------------------------------------------------------------------


def test_rejects_unknown_format_version(tmp_path: Path) -> None:
    """format_version 2.0 → ConfigurationError до любой работы с ключом."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"[]")

    route_data = {
        "format_version": "2.0",
        "status": "OK",
        "manifest_path": "manifest.json",
    }
    route_path = tmp_path / "route.json"
    route_path.write_text(json.dumps(route_data), encoding="utf-8")

    decrypt_called = []
    with patch(
        "privacy_gateway.restore.load_manifest",
        side_effect=lambda *a, **kw: decrypt_called.append(True),
    ):
        with pytest.raises(
            ConfigurationError, match="Unknown route.json format_version"
        ):
            restore_text("любой текст", route_path)

    assert not decrypt_called, (
        "load_manifest не должен вызываться при неизвестной версии"
    )


# ---------------------------------------------------------------------------
# 11. Целостность проверяется до keyring и расшифровки
# ---------------------------------------------------------------------------


def test_integrity_checked_before_decrypt(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """При ошибке целостности get_all_keys и load_manifest не вызываются."""
    key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)

    manifest_path = out_dir / "manifest.json"
    with manifest_path.open("ab") as f:
        f.write(b" ")

    route_path = out_dir / "route.json"

    get_all_keys_called = []
    load_manifest_called = []

    with patch(
        "privacy_gateway.restore.get_all_keys",
        side_effect=lambda: get_all_keys_called.append(True) or [key],
    ):
        with patch(
            "privacy_gateway.restore.load_manifest",
            side_effect=lambda *a, **kw: load_manifest_called.append(True),
        ):
            with pytest.raises(ConfigurationError):
                restore_text("любой текст", route_path)

    assert not get_all_keys_called, (
        "get_all_keys не должен вызываться при ошибке целостности"
    )
    assert not load_manifest_called, (
        "load_manifest не должен вызываться при ошибке целостности"
    )


# ---------------------------------------------------------------------------
# 12. Подменённый манифест отклоняется
# ---------------------------------------------------------------------------


def test_swapped_manifest_rejected(tmp_path: Path, mock_keyring: bytes) -> None:
    """Манифест от второго запуска с route.json первого → ConfigurationError."""
    key = mock_keyring
    out_dir1 = _run_prepare(tmp_path / "run1", key)
    out_dir2 = _run_prepare(tmp_path / "run2", key)

    prompt1 = (out_dir1 / "prompt.txt").read_text(encoding="utf-8")
    route_path1 = out_dir1 / "route.json"
    foreign_manifest = out_dir2 / "manifest.json"

    with pytest.raises(ConfigurationError, match="integrity check failed"):
        restore_text(
            prompt1,
            route_path1,
            manifest_path_override=foreign_manifest,
        )


# ---------------------------------------------------------------------------
# 13. Относительный manifest_path разрешается от каталога route.json
# ---------------------------------------------------------------------------


def test_manifest_path_resolution(tmp_path: Path, fernet_key: bytes) -> None:
    """Относительный manifest_path вычисляется от каталога route.json, не от cwd."""
    from privacy_gateway.manifest import build_manifest, save_manifest
    from privacy_gateway.models import EntityType, TokenRecord

    key = fernet_key
    records = [
        TokenRecord(
            token="[EMAIL_1]",
            entity_type=EntityType.EMAIL,
            fingerprint="fp1",
        )
    ]
    values = [SYNTH_EMAIL]
    entries = build_manifest(records, values, key)

    sub = tmp_path / "subdir"
    sub.mkdir()
    manifest_path = sub / "manifest.json"
    save_manifest(entries, manifest_path)

    sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    route_data = {
        "format_version": "1.1",
        "status": "OK",
        "manifest_path": "manifest.json",
        "manifest_sha256": sha,
        "timestamp": "2026-01-01T00:00:00Z",
    }
    route_path = sub / "route.json"
    route_path.write_text(json.dumps(route_data), encoding="utf-8")

    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)  # cwd ≠ sub
        with patch("privacy_gateway.restore.get_all_keys", return_value=[key]):
            result = restore_text("[EMAIL_1]", route_path)
    finally:
        os.chdir(original_cwd)

    assert result.restored_text is not None
    assert SYNTH_EMAIL in result.restored_text


# ---------------------------------------------------------------------------
# 14. Чужой ключ — читаемая ошибка без plaintext
# ---------------------------------------------------------------------------


def test_wrong_key_readable_error(tmp_path: Path, mock_keyring: bytes) -> None:
    """Расшифровка чужим ключом → ConfigurationError без утечки шифротекста."""
    key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    route_path = out_dir / "route.json"
    prompt = (out_dir / "prompt.txt").read_text(encoding="utf-8")

    wrong_key = generate_key()

    with patch("privacy_gateway.restore.get_all_keys", return_value=[wrong_key]):
        with pytest.raises(ConfigurationError) as exc_info:
            restore_text(prompt, route_path)

    msg = str(exc_info.value)
    assert msg.strip()
    assert SYNTH_EMAIL not in msg
    assert SYNTH_IP not in msg
    assert SYNTH_PHONE not in msg
    manifest_raw = (out_dir / "manifest.json").read_text(encoding="utf-8")
    for fragment in json.loads(manifest_raw):
        assert fragment.get("encrypted_value", "")[:20] not in msg


# ---------------------------------------------------------------------------
# 15. Повреждённый манифест — отказ целостности
# ---------------------------------------------------------------------------


def test_tampered_manifest_detected(tmp_path: Path, mock_keyring: bytes) -> None:
    """Изменение байта в manifest.json → ConfigurationError (integrity check)."""
    key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    manifest_path = out_dir / "manifest.json"

    with manifest_path.open("ab") as f:
        f.write(b"X")

    with pytest.raises(ConfigurationError, match="integrity check failed"):
        restore_text(
            "любой текст",
            out_dir / "route.json",
        )
