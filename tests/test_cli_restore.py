"""Сквозные CLI-тесты pgw restore — Э7 (fix/e7-gaps).

Все четыре теста запускают реальную точку входа CLI через sys.argv + main(),
без прямого вызова _cmd_restore() или pipeline-функций.

Синтетические данные (не реальные PII):
    SYNTH_EMAIL  = user@example.com
    SYNTH_IP     = 192.0.2.10
    SYNTH_PHONE  = +7 900 000-00-00
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway.crypto import generate_key

# ---------------------------------------------------------------------------
# Синтетические тестовые данные (не реальные PII)
# ---------------------------------------------------------------------------

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"
SYNTH_PHONE = "+7 900 000-00-00"
SYNTH_TEXT = f"Связь: {SYNTH_EMAIL}, {SYNTH_IP}, {SYNTH_PHONE}\n"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fernet_key() -> bytes:
    return generate_key()


@pytest.fixture()
def mock_keyring(fernet_key: bytes) -> Iterator[bytes]:
    """Подменяет get_all_keys в pipeline и restore без обращения к реальному keyring."""
    with patch("privacy_gateway.pipeline.get_key", return_value=fernet_key):
        with patch("privacy_gateway.restore.get_all_keys", return_value=[fernet_key]):
            yield fernet_key


def _prepare_artifacts(tmp_path: Path, key: bytes, text: str = SYNTH_TEXT) -> Path:
    """Запустить prepare_pipeline и вернуть out_dir со статусом OK."""
    from privacy_gateway.models import ProcessingStatus
    from privacy_gateway.pipeline import prepare_pipeline
    from privacy_gateway.routing import load_routing_config

    out_dir = tmp_path / "out"
    cfg = load_routing_config(None)
    result = prepare_pipeline(
        text=text,
        source_ref="test.txt",
        routing_cfg=cfg,
        key=key,
        out_dir=out_dir,
    )
    assert result.status == ProcessingStatus.OK, f"prepare failed: {result.message}"
    return out_dir


def _exit_code(exc: SystemExit) -> int:
    """Нормализовать SystemExit.code к int; поведение не меняется."""
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return int(code)


def _run_cli(*args: str) -> int:
    """Запустить pgw CLI через sys.argv + main(); вернуть код завершения."""
    from privacy_gateway.cli import main

    with patch.object(sys, "argv", ["pgw", *args]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    return _exit_code(exc_info.value)


# ---------------------------------------------------------------------------
# Тест 1: test_no_output_on_failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure_mode",
    [
        "unknown_token",
        "tampered_manifest",
        "missing_key",
    ],
)
def test_no_output_on_failure(
    tmp_path: Path, mock_keyring: bytes, failure_mode: str
) -> None:
    """При отказе выходной файл и временные файлы отсутствуют."""
    from privacy_gateway.keystore import KeyNotFoundError

    key = mock_keyring
    out_dir = _prepare_artifacts(tmp_path, key)
    route_path = out_dir / "route.json"
    result_path = tmp_path / "restored.txt"
    result_dir = tmp_path

    if failure_mode == "unknown_token":
        llm_reply_path = tmp_path / "llm_reply.txt"
        original_prompt = (out_dir / "prompt.txt").read_text(encoding="utf-8")
        llm_reply_path.write_text(
            original_prompt + " [EMAIL_99]",
            encoding="utf-8",
        )
        code = _run_cli(
            "restore",
            str(llm_reply_path),
            "--route", str(route_path),
            "--out", str(result_path),
        )

    elif failure_mode == "tampered_manifest":
        out_dir2 = _prepare_artifacts(tmp_path / "out2", key)
        (out_dir / "manifest.json").write_bytes(
            (out_dir2 / "manifest.json").read_bytes()
        )
        llm_reply_path = out_dir / "prompt.txt"
        code = _run_cli(
            "restore",
            str(llm_reply_path),
            "--route", str(route_path),
            "--out", str(result_path),
        )

    else:  # missing_key
        llm_reply_path = out_dir / "prompt.txt"
        with patch(
            "privacy_gateway.restore.get_all_keys",
            side_effect=KeyNotFoundError("no key"),
        ):
            code = _run_cli(
                "restore",
                str(llm_reply_path),
                "--route", str(route_path),
                "--out", str(result_path),
            )

    assert code != 0, f"[{failure_mode}] ожидался ненулевой код, получен {code}"

    assert not result_path.exists(), (
        f"[{failure_mode}] выходной файл не должен существовать: {result_path}"
    )

    tmp_leftovers = list(result_dir.glob(".pgw_restore_*"))
    assert not tmp_leftovers, (
        f"[{failure_mode}] найдены временные файлы: {tmp_leftovers}"
    )


# ---------------------------------------------------------------------------
# Тест 2: test_report_does_not_leak_values
# ---------------------------------------------------------------------------


def test_report_does_not_leak_values(
    tmp_path: Path, mock_keyring: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """Отчёт pgw restore не содержит расшифрованных значений при успешном пути."""
    key = mock_keyring
    out_dir = _prepare_artifacts(tmp_path, key)
    route_path = out_dir / "route.json"
    result_path = tmp_path / "restored.txt"

    code = _run_cli(
        "restore",
        str(out_dir / "prompt.txt"),
        "--route", str(route_path),
        "--out", str(result_path),
    )

    assert code == 0

    restored = result_path.read_text(encoding="utf-8")
    assert SYNTH_EMAIL in restored
    assert SYNTH_IP in restored
    assert SYNTH_PHONE in restored

    captured = capsys.readouterr()
    report_output = captured.out + captured.err
    assert SYNTH_EMAIL not in report_output, (
        f"Отчёт содержит {SYNTH_EMAIL!r} — утечка значения"
    )
    assert SYNTH_IP not in report_output, (
        f"Отчёт содержит {SYNTH_IP!r} — утечка значения"
    )
    assert SYNTH_PHONE not in report_output, (
        f"Отчёт содержит {SYNTH_PHONE!r} — утечка значения"
    )


# ---------------------------------------------------------------------------
# Тест 3: CLI успешного restore
# ---------------------------------------------------------------------------


def test_cli_restore_success(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """pgw restore через CLI: код 0, файл создан, содержимое == исходный текст."""
    key = mock_keyring
    out_dir = _prepare_artifacts(tmp_path, key)
    route_path = out_dir / "route.json"
    result_path = tmp_path / "restored.txt"

    code = _run_cli(
        "restore",
        str(out_dir / "prompt.txt"),
        "--route", str(route_path),
        "--out", str(result_path),
    )

    assert code == 0
    assert result_path.exists()
    restored = result_path.read_text(encoding="utf-8")
    assert SYNTH_EMAIL in restored
    assert SYNTH_IP in restored
    assert SYNTH_PHONE in restored
    assert restored == SYNTH_TEXT


# ---------------------------------------------------------------------------
# Тест 4: CLI строгого отказа → код 5 (ADR-21)
# ---------------------------------------------------------------------------


def test_cli_restore_strict_failure(
    tmp_path: Path, mock_keyring: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    pgw restore: неизвестный токен → код 5 (RestoreStrictError, ADR-21),
    файл отсутствует, значения не утекают.
    """
    key = mock_keyring
    out_dir = _prepare_artifacts(tmp_path, key)
    route_path = out_dir / "route.json"
    result_path = tmp_path / "restored.txt"

    llm_reply_path = tmp_path / "bad_reply.txt"
    original_prompt = (out_dir / "prompt.txt").read_text(encoding="utf-8")
    llm_reply_path.write_text(
        original_prompt + " [EMAIL_99]",
        encoding="utf-8",
    )

    code = _run_cli(
        "restore",
        str(llm_reply_path),
        "--route", str(route_path),
        "--out", str(result_path),
    )

    # Код 5 — строгий отказ по токену (ADR-21)
    assert code == 5, f"Ожидался код 5, получен {code}"
    assert not result_path.exists(), "При строгом отказе файл не должен создаваться"

    captured = capsys.readouterr()
    error_output = captured.out + captured.err
    assert (
        "EMAIL_99" in error_output
        or "токен" in error_output.lower()
        or "token" in error_output.lower()
    )
    assert SYNTH_EMAIL not in error_output
    assert SYNTH_IP not in error_output
    assert SYNTH_PHONE not in error_output
