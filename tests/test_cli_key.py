"""Тесты CLI-команд key create / key status (Э8).

Проверяют:
- успешное создание ключа (код 0);
- отказ при существующем ключе без --force (код 3, ключ не изменён);
- перезапись с --force (код 0);
- отсутствие ключевого материала в stdout/stderr во всех сценариях;
- читаемое сообщение при недоступном backend (код 4);
- key status не выводит значение ключа (код 0).

Реальный системный keyring не задействуется.
"""

from __future__ import annotations

import re
import sys
from unittest.mock import patch

import pytest

from privacy_gateway.cli import main
from privacy_gateway.crypto import generate_key
from privacy_gateway.keystore import (
    KeyExistsError,
    KeystoreError,
)

# Паттерн Fernet-ключа: base64url, ровно 44 символа, заканчивается '='
_FERNET_KEY_RE = re.compile(r"[A-Za-z0-9_\-]{43}=")


def _run_cli(*args: str, capsys: pytest.CaptureFixture) -> int:  # type: ignore[type-arg]
    """Запустить main() с заданными аргументами, вернуть код возврата."""
    with patch("sys.argv", ["pgw", *args]):
        try:
            main()
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 0
    return 0


# ---------------------------------------------------------------------------
# test_key_create_success
# ---------------------------------------------------------------------------

def test_key_create_success(capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
    """pgw key create → код 0, stdout содержит подтверждение."""
    # create_key импортируется внутри _cmd_key_create, поэтому патчим
    # в модуле keystore, а не в cli.
    with patch("privacy_gateway.keystore.create_key", return_value=generate_key()) as mock_create:
        code = _run_cli("key", "create", capsys=capsys)

    assert code == 0
    out = capsys.readouterr().out
    assert "создан" in out.lower() or "ключ" in out.lower()
    mock_create.assert_called_once_with(force=False)


# ---------------------------------------------------------------------------
# test_key_create_refuses_existing
# ---------------------------------------------------------------------------

def test_key_create_refuses_existing(capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
    """pgw key create без --force при существующем ключе → код 3.

    Проверяется именно неизменность: mock не должен вызывать create_key
    успешно после первого KeyExistsError.
    """
    call_count = 0
    original_key = generate_key()

    def _refusing_create(*, force: bool = False) -> bytes:
        nonlocal call_count
        call_count += 1
        if not force:
            raise KeyExistsError("Ключ уже существует.")
        return original_key

    with patch("privacy_gateway.keystore.create_key", side_effect=_refusing_create):
        code = _run_cli("key", "create", capsys=capsys)

    assert code == 3
    captured = capsys.readouterr()
    # Сообщение об ошибке идёт в stderr
    assert captured.err  # не пустой
    # Ключевой материал не утёк
    assert not _FERNET_KEY_RE.search(captured.out)
    assert not _FERNET_KEY_RE.search(captured.err)
    # create_key вызван ровно один раз — без повторных попыток
    assert call_count == 1


# ---------------------------------------------------------------------------
# test_key_create_force_overwrites
# ---------------------------------------------------------------------------

def test_key_create_force_overwrites(capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
    """pgw key create --force → create_key(force=True), код 0."""
    with patch("privacy_gateway.keystore.create_key", return_value=generate_key()) as mock_create:
        code = _run_cli("key", "create", "--force", capsys=capsys)

    assert code == 0
    mock_create.assert_called_once_with(force=True)


# ---------------------------------------------------------------------------
# test_key_never_printed
# ---------------------------------------------------------------------------

def test_key_never_printed(capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
    """Ни stdout, ни stderr не содержат ключевого материала ни в каком сценарии.

    Сценарии: успех, ключ уже существует, недоступный backend.
    """
    scenarios: list[tuple[str, ...]] = [
        ("key", "create"),
        ("key", "create"),   # второй вызов — KeyExistsError
        ("key", "create", "--force"),
        ("key", "status"),
    ]
    side_effects = [
        generate_key(),            # успех
        KeyExistsError("exists"),  # отказ
        generate_key(),            # force-перезапись
    ]

    se_iter = iter(side_effects)

    def _side_effect(*, force: bool = False) -> bytes:
        val = next(se_iter)
        if isinstance(val, Exception):
            raise val
        return val  # type: ignore[return-value]

    with (
        patch("privacy_gateway.keystore.create_key", side_effect=_side_effect),
        patch("privacy_gateway.keystore.key_exists", return_value=True),
    ):
        for args in scenarios:
            _run_cli(*args, capsys=capsys)
            captured = capsys.readouterr()
            assert not _FERNET_KEY_RE.search(captured.out), (
                f"Ключевой материал найден в stdout при сценарии {args}: {captured.out!r}"
            )
            assert not _FERNET_KEY_RE.search(captured.err), (
                f"Ключевой материал найден в stderr при сценарии {args}: {captured.err!r}"
            )


# ---------------------------------------------------------------------------
# test_key_create_backend_unavailable
# ---------------------------------------------------------------------------

def test_key_create_backend_unavailable(capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
    """Недоступный backend → читаемое сообщение в stderr, код 4."""
    with patch(
        "privacy_gateway.keystore.create_key",
        side_effect=KeystoreError("Unsafe or unavailable keyring backend"),
    ):
        code = _run_cli("key", "create", capsys=capsys)

    assert code == 4
    captured = capsys.readouterr()
    # Сообщение должно быть читаемым (непустым)
    assert len(captured.err.strip()) > 0
    # Адрес keystore / ключевой материал не утёк
    assert not _FERNET_KEY_RE.search(captured.err)


# ---------------------------------------------------------------------------
# test_key_status_no_value
# ---------------------------------------------------------------------------

def test_key_status_no_value(capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
    """key status при наличии ключа → код 0, значение ключа не выводится."""
    with patch("privacy_gateway.keystore.key_exists", return_value=True):
        code = _run_cli("key", "status", capsys=capsys)

    assert code == 0
    captured = capsys.readouterr()
    assert not _FERNET_KEY_RE.search(captured.out), (
        f"Ключевой материал найден в stdout: {captured.out!r}"
    )
    assert not _FERNET_KEY_RE.search(captured.err), (
        f"Ключевой материал найден в stderr: {captured.err!r}"
    )
    # Подтверждение присутствия ключа должно быть в stdout
    assert captured.out.strip()
