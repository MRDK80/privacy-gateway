"""Тесты bounded retention policy для retired keys (ADR-46, issue #46).

Проверяют принятый контракт «active + один retired» (вариант A1):

- после 1/2/3 ротаций get_all_keys() отдаёт не более двух ключей
  в детерминированном порядке [active, retired] без дублей;
- retired-entry содержит ровно один ключ после успешной ротации;
- decryptability-матрица K0-K3: читаются только два последних поколения;
- отказ на каждом шаге ротации не теряет данные и допускает retry;
- отказ verification не сообщает success и не раскрывает key material;
- legacy-состояние с глубокой историей ограничивается при чтении
  и сокращается при первой явной ротации;
- чтение никогда не удаляет key material.

Реальный системный keyring не задействуется: используется _SafeBackendMock.
Ключи синтетические (ADR-25), на диск не пишутся.
"""

from __future__ import annotations

import json

import pytest

import privacy_gateway.keystore as ks
from privacy_gateway.crypto import (
    DecryptionError,
    decrypt_multi,
    encrypt_multi,
    generate_key,
)
from privacy_gateway.keystore import KeystoreError

_PLAINTEXT = "user@example.com"


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


def _retired_entries(backend: _SafeBackendMock) -> list[str]:
    """Вернуть сырой список retired-ключей из keyring-entry."""
    raw = backend.storage.get((ks._SERVICE, ks._RETIRED_KEY))
    if raw is None:
        return []
    data = json.loads(raw)
    return [data] if isinstance(data, str) else list(data)


# ---------------------------------------------------------------------------
# Bounded state и ordering
# ---------------------------------------------------------------------------


def test_bounded_state_after_three_rotations(
    safe_backend: _SafeBackendMock,
) -> None:
    """После каждой ротации доступны ровно active и один retired."""
    generations = [ks.create_key()]

    assert ks.get_all_keys() == [generations[0]]
    assert _retired_entries(safe_backend) == []

    for _ in range(3):
        previous = generations[-1]
        new_key = ks.rotate_key()
        generations.append(new_key)

        keys = ks.get_all_keys()
        assert keys == [new_key, previous], (
            "get_all_keys() должен вернуть [active, retired] по ADR-46"
        )
        assert len(_retired_entries(safe_backend)) == 1, (
            "retired-entry должен содержать ровно один ключ"
        )

    assert len(generations) == 4
    assert len(ks.get_all_keys()) == 2


def test_get_all_keys_has_no_duplicates(safe_backend: _SafeBackendMock) -> None:
    """Дубликаты не возвращаются даже при совпадении active и retired."""
    key = ks.create_key()
    ks._set_raw(ks._RETIRED_KEY, ks._encode_keys([key]))

    assert ks.get_all_keys() == [key]


# ---------------------------------------------------------------------------
# Decryptability матрица K0-K3
# ---------------------------------------------------------------------------


def test_decryptability_matrix_k0_to_k3(safe_backend: _SafeBackendMock) -> None:
    """Читаются только текущее и непосредственно предыдущее поколения."""
    tokens: list[bytes] = []

    ks.create_key()
    tokens.append(encrypt_multi(_PLAINTEXT, ks.get_all_keys()))
    for _ in range(3):
        ks.rotate_key()
        tokens.append(encrypt_multi(_PLAINTEXT, ks.get_all_keys()))

    keys = ks.get_all_keys()
    assert decrypt_multi(tokens[3], keys) == _PLAINTEXT
    assert decrypt_multi(tokens[2], keys) == _PLAINTEXT

    for stale in (tokens[1], tokens[0]):
        with pytest.raises(DecryptionError):
            decrypt_multi(stale, keys)


# ---------------------------------------------------------------------------
# Failure / retry матрица
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fail_on_call", [1, 2, 3])
def test_rotation_failure_preserves_data_and_allows_retry(
    safe_backend: _SafeBackendMock,
    monkeypatch: pytest.MonkeyPatch,
    fail_on_call: int,
) -> None:
    """Отказ любой записи не теряет данные и допускает retry.

    Шаги записи по ADR-46: 1 — carry retired, 2 — новый active, 3 — prune.
    При отказе на шагах 1 и 2 ротация не состоялась и active остаётся
    прежним. При отказе на шаге 3 смена active уже выполнена и
    подтверждена чтением, недостигнутым остаётся только bounded-
    состояние retired-entry, о чём операция обязана сообщить ошибкой,
    а не полным success.
    """
    ks.create_key()
    ks.rotate_key()
    active_before = ks.get_key()
    current_token = encrypt_multi(_PLAINTEXT, [active_before])

    calls = 0
    original_set_raw = ks._set_raw

    def _failing_set_raw(name: str, value: str) -> None:
        nonlocal calls
        calls += 1
        if calls == fail_on_call:
            raise KeystoreError("Симулированный сбой записи keyring")
        original_set_raw(name, value)

    monkeypatch.setattr(ks, "_set_raw", _failing_set_raw)
    with pytest.raises(KeystoreError):
        ks.rotate_key()
    monkeypatch.setattr(ks, "_set_raw", original_set_raw)

    active_after_failure = ks.get_key()
    if fail_on_call in (1, 2):
        assert active_after_failure == active_before, (
            "Ротация не состоялась: активный ключ должен остаться прежним"
        )
    else:
        assert active_after_failure != active_before, (
            "Отказ prune происходит после подтверждённой смены active"
        )

    keys_after_failure = ks.get_all_keys()
    assert len(keys_after_failure) <= 2
    assert decrypt_multi(current_token, keys_after_failure) == _PLAINTEXT

    expected_entries = 1 if fail_on_call == 1 else 2
    assert len(_retired_entries(safe_backend)) == expected_entries, (
        "Незавершённый prune оставляет key material в keyring"
    )

    new_key = ks.rotate_key()
    assert ks.get_all_keys() == [new_key, active_after_failure]
    assert len(_retired_entries(safe_backend)) == 1


def test_rotation_verification_failure_is_not_silent_success(
    safe_backend: _SafeBackendMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Неподтверждённая смена active даёт KeystoreError без key material."""
    ks.create_key()
    active_before = ks.get_key()
    original_get_raw = ks._get_raw
    stale_active = original_get_raw(ks._ACTIVE_KEY)

    def _stale_get_raw(name: str) -> str | None:
        if name == ks._ACTIVE_KEY:
            return stale_active
        return original_get_raw(name)

    monkeypatch.setattr(ks, "_get_raw", _stale_get_raw)
    with pytest.raises(KeystoreError) as exc_info:
        ks.rotate_key()
    monkeypatch.setattr(ks, "_get_raw", original_get_raw)

    message = str(exc_info.value)
    assert active_before.decode() not in message
    for entry in _retired_entries(safe_backend):
        assert entry not in message


# ---------------------------------------------------------------------------
# Migration legacy-состояния
# ---------------------------------------------------------------------------


def test_legacy_deep_history_bounded_on_read_pruned_on_rotate(
    safe_backend: _SafeBackendMock,
) -> None:
    """Глубокая история ограничивается при чтении и сокращается при ротации."""
    active = ks.create_key()
    legacy = [generate_key() for _ in range(3)]
    ks._set_raw(ks._RETIRED_KEY, ks._encode_keys(legacy))

    keys = ks.get_all_keys()
    assert keys == [active, legacy[0]], (
        "Чтение должно быть ограничено двумя ключами по ADR-46"
    )
    assert len(_retired_entries(safe_backend)) == 3, (
        "Чтение не должно удалять key material"
    )

    new_key = ks.rotate_key()
    assert ks.get_all_keys() == [new_key, active]
    assert len(_retired_entries(safe_backend)) == 1


def test_legacy_single_string_value_still_readable(
    safe_backend: _SafeBackendMock,
) -> None:
    """Legacy-форма значения (одиночная JSON-строка) продолжает читаться."""
    active = ks.create_key()
    retired = generate_key()
    safe_backend.storage[(ks._SERVICE, ks._RETIRED_KEY)] = json.dumps(
        retired.decode()
    )

    assert ks.get_all_keys() == [active, retired]


def test_read_operations_never_delete_key_material(
    safe_backend: _SafeBackendMock,
) -> None:
    """key_exists/get_key/get_all_keys не изменяют keyring."""
    ks.create_key()
    legacy = [generate_key() for _ in range(2)]
    ks._set_raw(ks._RETIRED_KEY, ks._encode_keys(legacy))
    snapshot = dict(safe_backend.storage)

    for _ in range(3):
        assert ks.key_exists() is True
        ks.get_key()
        ks.get_all_keys()

    assert safe_backend.storage == snapshot
