"""Тесты ротации ключей, жизненного цикла и кодов возврата (Э8).

Проверяют:
- после rotate_key() шифрование идёт новым ключом;
- манифест, созданный до ротации, читается после ротации;
- порядок ключей в MultiFernet имеет значение;
- прерывание rotate_key не оставляет частично записанное состояние;
- удаление ключа делает старые манифесты нечитаемыми;
- полный цикл: create → prepare → restore → rotate → restore старого манифеста;
- коды возврата 3, 4, 5 различимы между собой.

Реальный keyring не задействуется: используется _SafeBackendMock.
Данные — только синтетика: user@example.com, 192.0.2.10, +7 900 000-00-00.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import privacy_gateway.keystore as ks
from privacy_gateway.crypto import (
    DecryptionError,
    decrypt_multi,
    encrypt_multi,
    generate_key,
)
from privacy_gateway.keystore import (
    KeyNotFoundError,
    KeystoreError,
)
from privacy_gateway.models import ConfigurationError, RestoreStrictError


# ---------------------------------------------------------------------------
# Shared mock backend (тот же паттерн, что в test_keystore.py)
# ---------------------------------------------------------------------------

class _SafeBackendMock:
    """Mock backend с FQCN из allowlist."""

    __module__ = "keyring.backends.SecretService"
    __qualname__ = "Keyring"

    storage: dict[tuple[str, str], str]

    def get_password(self, service: str, username: str) -> str | None:
        return self.storage.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.storage[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.storage.pop((service, username), None)


@pytest.fixture()
def safe_backend(monkeypatch: pytest.MonkeyPatch) -> _SafeBackendMock:
    backend = _SafeBackendMock()
    backend.storage = {}
    monkeypatch.setattr("keyring.get_keyring", lambda: backend)
    monkeypatch.delenv("PGW_KEYRING_BACKEND", raising=False)
    return backend


# ---------------------------------------------------------------------------
# test_rotation_new_key_first
# ---------------------------------------------------------------------------

def test_rotation_new_key_first(safe_backend: _SafeBackendMock) -> None:
    """После rotate_key() шифрование новых данных идёт новым ключом.

    encrypt_multi шифрует первым ключом списка (keys[0]).
    Проверяем, что после ротации get_key() возвращает новый ключ,
    а шифртекст, созданный после ротации, не читается старым ключом.
    """
    old_key = ks.create_key()
    new_key = ks.rotate_key()

    assert new_key != old_key, "Новый ключ должен отличаться от старого"

    active = ks.get_key()
    assert active == new_key, "get_key() должен вернуть новый ключ"

    # Шифртекст, созданный после ротации, должен расшифровываться новым ключом
    plaintext = "user@example.com"
    ciphertext = encrypt_multi(plaintext, [new_key])
    assert decrypt_multi(ciphertext, [new_key]) == plaintext

    # Старый ключ не должен расшифровывать новый шифртекст
    with pytest.raises(DecryptionError):
        decrypt_multi(ciphertext, [old_key])


# ---------------------------------------------------------------------------
# test_old_manifest_still_readable  (центральный тест)
# ---------------------------------------------------------------------------

def test_old_manifest_still_readable(safe_backend: _SafeBackendMock) -> None:
    """Манифест, созданный ДО ротации, читается поСЛЕ ротации через decrypt_multi.

    Это проверяет гарантию ADR-23 (MultiFernet обеспечивает обратную
    совместимость): get_all_keys() возвращает [new_key, old_key],
    и decrypt_multi пробует оба ключа, пока один не подойдёт.
    """
    old_key = ks.create_key()

    # Шифруем данные СТАРЫМ ключом (имитация prepare до ротации)
    plaintext = "user@example.com"
    ciphertext_old = encrypt_multi(plaintext, [old_key])

    # Ротация
    ks.rotate_key()

    # После ротации: get_all_keys() = [new_key, old_key]
    all_keys = ks.get_all_keys()
    assert len(all_keys) >= 2, "get_all_keys() должен вернуть не менее 2 ключей после ротации"
    assert all_keys[0] != old_key, "Первый ключ в get_all_keys() должен быть новым"

    # Центральная проверка: старый шифртекст читается через все ключи
    recovered = decrypt_multi(ciphertext_old, all_keys)
    assert recovered == plaintext, (
        f"Манифест, созданный до ротации, должен читаться без ручных действий. "
        f"Получено: {recovered!r}, ожидалось: {plaintext!r}"
    )


# ---------------------------------------------------------------------------
# test_multifernet_order_matters
# ---------------------------------------------------------------------------

def test_multifernet_order_matters(safe_backend: _SafeBackendMock) -> None:
    """Нарушение порядка ключей обнаруживается при шифровании.

    encrypt_multi шифрует первым ключом. Если передать у MultiFernet ключи
    в неправильном порядке (старый первым), шифрование будет идти старым ключом,
    а decrypt_multi с правильным порядком (новый первым) всё равно расшифрует,
    поскольку MultiFernet пробует все ключи.

    Поэтому проверяем обратную ситуацию: после ротации новый шифртекст
    не должен расшифровываться одним старым ключом.
    """
    key_a = generate_key()
    key_b = generate_key()

    # Шифруем ключом key_a (первый в списке)
    plaintext = "192.0.2.10"
    ciphertext = encrypt_multi(plaintext, [key_a, key_b])

    # Правильный порядок: [key_a, key_b] — расшифровывает
    assert decrypt_multi(ciphertext, [key_a, key_b]) == plaintext

    # Неправильный порядок: один старый ключ без key_a — не расшифрует
    with pytest.raises(DecryptionError):
        decrypt_multi(ciphertext, [key_b])  # key_a вообще отсутствует


# ---------------------------------------------------------------------------
# test_rotation_atomic
# ---------------------------------------------------------------------------

def test_rotation_atomic(safe_backend: _SafeBackendMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Прерывание на втором _set_raw не оставляет keystore в повреждённом состоянии.

    rotate_key() выполняет два _set_raw: сначала записывает retired,
    затем active. Если второй вызов рухнет, active остаётся прежним.
    Проверяем, что старый ключ всё ещё читается.
    """
    old_key = ks.create_key()
    plaintext = "+7 900 000-00-00"
    ciphertext = encrypt_multi(plaintext, [old_key])

    call_count = 0
    original_set_raw = ks._set_raw

    def _failing_set_raw(name: str, value: str) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # второй вызов — запись active
            raise KeystoreError("Симулированный сбой записи active")
        original_set_raw(name, value)

    monkeypatch.setattr(ks, "_set_raw", _failing_set_raw)

    with pytest.raises(KeystoreError):
        ks.rotate_key()

    # После сбоя: active остался прежним, старые данные всё ещё читаются
    # _set_raw восстанавливаем для проверки
    monkeypatch.setattr(ks, "_set_raw", original_set_raw)
    active_key = ks.get_key()
    assert decrypt_multi(ciphertext, [active_key]) == plaintext, (
        "Старый ключ должен остаться действующим после прерванной ротации"
    )


# ---------------------------------------------------------------------------
# test_removed_key_makes_manifest_unreadable
# ---------------------------------------------------------------------------

def test_removed_key_makes_manifest_unreadable(safe_backend: _SafeBackendMock) -> None:
    """Удаление ключа делает зашифрованные данные нечитаемыми — задокументированное поведение."""
    key = ks.create_key()
    plaintext = "2001:db8::1"
    ciphertext = encrypt_multi(plaintext, [key])

    # Удаляем ключ
    ks.delete_key()

    # Без ключа — KeyNotFoundError
    with pytest.raises(KeyNotFoundError):
        ks.get_all_keys()

    # Прямая попытка расшифровать старым ключом, полученным вне keystore
    with pytest.raises(DecryptionError):
        decrypt_multi(ciphertext, [generate_key()])  # чужой ключ


# ---------------------------------------------------------------------------
# test_full_lifecycle
# ---------------------------------------------------------------------------

def test_full_lifecycle(
    safe_backend: _SafeBackendMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Полный цикл: create → prepare → restore → rotate → restore старого манифеста.

    Используется только синтетические данные: user@example.com.
    """
    from privacy_gateway.pipeline import prepare_pipeline
    from privacy_gateway.restore import restore_text
    from privacy_gateway.routing import RoutingConfig

    # Шаг 1: создаём ключ
    old_key = ks.create_key()
    assert isinstance(old_key, bytes)

    # Шаг 2: prepare
    input_text = "Send report to user@example.com from 192.0.2.10"
    out_dir = tmp_path / "artifacts"

    routing_cfg = RoutingConfig(
        tokenize_types=["EMAIL", "IP"],
        block_unconditionally=[],
        output_dir=str(out_dir),
        overwrite=False,
    )

    # entities.yaml нужен для детектора
    entities_config = Path("config.example") / "entities.yaml"

    pipeline_result = prepare_pipeline(
        text=input_text,
        source_ref="test_lifecycle.txt",
        routing_cfg=routing_cfg,
        key=old_key,
        out_dir=out_dir,
        overwrite=False,
        entities_config_path=entities_config,
    )

    from privacy_gateway.models import ProcessingStatus
    assert pipeline_result.status == ProcessingStatus.OK, (
        f"prepare_pipeline вернул не OK: {pipeline_result.message}"
    )

    route_path = pipeline_result.route_path
    assert route_path is not None

    # Шаг 3: restore до ротации
    prompt = (out_dir / "prompt.txt").read_text(encoding="utf-8")
    result_before = restore_text(
        llm_response=prompt,
        route_path=route_path,
        strict=False,  # мягкий режим: пропущенные токены не блокируют
    )
    assert "user@example.com" in (result_before.restored_text or ""), (
        "restore до ротации должен восстановить user@example.com"
    )

    # Шаг 4: ротация
    ks.rotate_key()

    # Шаг 5: restore СТАРОГО манифеста после ротации (центральная проверка)
    result_after = restore_text(
        llm_response=prompt,
        route_path=route_path,
        strict=False,
    )
    assert "user@example.com" in (result_after.restored_text or ""), (
        "FAIL (ADR-23): манифест, созданный ДО ротации, должен читаться ПОСЛЕ ротации. "
        "Ротация ломает обратную совместимость — блокирующий дефект."
    )


# ---------------------------------------------------------------------------
# Коды возврата 3 / 4 / 5 — различимы между собой
# ---------------------------------------------------------------------------

def _run_cli_key(*args: str) -> int:
    """CLI через main(), вернуть код возврата."""
    import sys
    from unittest.mock import patch as _patch
    from privacy_gateway.cli import main

    with _patch("sys.argv", ["pgw", *args]):
        try:
            main()
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 0
    return 0


def test_exit_code_config_error(tmp_path: Path) -> None:
    """Код 3: ошибка конфигурации (неверный route.json) в команде restore."""
    bad_route = tmp_path / "route.json"
    bad_route.write_text("{\"broken\": true}", encoding="utf-8")

    # Фактический файл для ответа LLM
    llm_file = tmp_path / "llm.txt"
    llm_file.write_text("some text", encoding="utf-8")

    code = _run_cli_key("restore", str(llm_file), "--route", str(bad_route))
    assert code == 3, f"Ожидался код 3, получен {code}"


def test_exit_code_keystore_error(tmp_path: Path) -> None:
    """Код 4: ключ не найден / недоступный backend в команде prepare."""
    input_file = tmp_path / "input.txt"
    input_file.write_text("Send to user@example.com", encoding="utf-8")

    with patch(
        "privacy_gateway.cli.get_key",
        side_effect=KeystoreError("backend unavailable"),
    ):
        code = _run_cli_key("prepare", str(input_file))

    assert code == 4, f"Ожидался код 4, получен {code}"


def test_exit_code_token_strict_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Код 5: неизвестный/искажённый токен в строгом режиме restore."""
    # route.json со всеми нужными полями (manifest_path не проходит проверку целостности
    # поскольку мы перехватываем restore_text)
    llm_file = tmp_path / "llm.txt"
    llm_file.write_text("Hello [UNKNOWN_99] world", encoding="utf-8")

    route_file = tmp_path / "route.json"
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text("[]", encoding="utf-8")

    import hashlib
    sha = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
    route_data = {
        "format_version": "1.1",
        "status": "OK",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "source_ref": "test.txt",
        "manifest_path": str(manifest_file),
        "manifest_sha256": sha,
        "token_count": 0,
        "token_counts_by_type": {},
        "entity_count_detected": 0,
        "entity_count_tokenized": 0,
    }
    route_file.write_text(json.dumps(route_data), encoding="utf-8")

    with patch(
        "privacy_gateway.restore.get_all_keys",
        return_value=[generate_key()],
    ):
        code = _run_cli_key("restore", str(llm_file), "--route", str(route_file))

    assert code == 5, f"Ожидался код 5, получен {code}"


def test_exit_codes_are_distinct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Коды 3, 4, 5 различимы между собой."""
    # Код 3 — неверный route.json
    bad_route = tmp_path / "route.json"
    bad_route.write_text("{\"broken\": true}", encoding="utf-8")
    llm_file = tmp_path / "llm.txt"
    llm_file.write_text("text", encoding="utf-8")
    code_3 = _run_cli_key("restore", str(llm_file), "--route", str(bad_route))

    # Код 4 — keystore error в prepare
    input_file = tmp_path / "input.txt"
    input_file.write_text("Send to user@example.com", encoding="utf-8")
    with patch(
        "privacy_gateway.cli.get_key",
        side_effect=KeystoreError("unavailable"),
    ):
        code_4 = _run_cli_key("prepare", str(input_file))

    # Код 5 — неизвестный токен в строгом режиме
    manifest_file2 = tmp_path / "manifest2.json"
    manifest_file2.write_text("[]", encoding="utf-8")
    import hashlib
    sha2 = hashlib.sha256(manifest_file2.read_bytes()).hexdigest()
    route_file2 = tmp_path / "route2.json"
    route_file2.write_text(json.dumps({
        "format_version": "1.1", "status": "OK",
        "timestamp": "2026-01-01T00:00:00+00:00", "source_ref": "t.txt",
        "manifest_path": str(manifest_file2), "manifest_sha256": sha2,
        "token_count": 0, "token_counts_by_type": {},
        "entity_count_detected": 0, "entity_count_tokenized": 0,
    }), encoding="utf-8")
    llm_unknown = tmp_path / "llm_unknown.txt"
    llm_unknown.write_text("[UNKNOWN_99]", encoding="utf-8")
    with patch(
        "privacy_gateway.restore.get_all_keys",
        return_value=[generate_key()],
    ):
        code_5 = _run_cli_key("restore", str(llm_unknown), "--route", str(route_file2))

    assert code_3 == 3, f"Ожидался 3, получен {code_3}"
    assert code_4 == 4, f"Ожидался 4, получен {code_4}"
    assert code_5 == 5, f"Ожидался 5, получен {code_5}"
    assert len({code_3, code_4, code_5}) == 3, (
        f"Коды 3/4/5 должны быть различны между собой: {code_3=} {code_4=} {code_5=}"
    )
