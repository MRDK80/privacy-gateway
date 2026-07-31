"""Сквозные тесты команды pgw restore — Э7.

Проверяют поведение _cmd_restore через argparse Namespace,
без обращения к реальному keyring и реальным файлам LLM.

Синтетика: user@example.com, 192.0.2.10, +7 900 000-00-00.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway.crypto import generate_key

# Синтетические данные (не реальные PII)
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
def mock_keyring(fernet_key: bytes):
    """Подменяет get_key в pipeline и restore без обращения к реальному keyring."""
    with patch("privacy_gateway.pipeline.get_key", return_value=fernet_key):
        with patch("privacy_gateway.restore.get_key", return_value=fernet_key):
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


def _make_args(
    *,
    file: str,
    route: str,
    out: str | None = None,
    overwrite: bool = False,
    manifest: str | None = None,
    lenient: bool = False,
) -> argparse.Namespace:
    """Собрать argparse.Namespace для _cmd_restore."""
    return argparse.Namespace(
        file=file,
        route=route,
        out=out,
        overwrite=overwrite,
        manifest=manifest,
        lenient=lenient,
    )


# ---------------------------------------------------------------------------
# 1. Успешное восстановление → файл с исходным текстом, код 0
# ---------------------------------------------------------------------------


def test_restore_to_file_returns_zero(tmp_path: Path, mock_keyring: bytes) -> None:
    """pgw restore --route ... --out FILE записывает результат и возвращает 0."""
    from privacy_gateway.cli import _cmd_restore

    key = mock_keyring
    out_dir = _prepare_artifacts(tmp_path, key)

    prompt_path = out_dir / "prompt.txt"
    route_path = out_dir / "route.json"
    result_path = tmp_path / "restored.txt"

    args = _make_args(
        file=str(prompt_path),
        route=str(route_path),
        out=str(result_path),
    )
    code = _cmd_restore(args)

    assert code == 0
    assert result_path.exists()
    restored = result_path.read_text(encoding="utf-8")
    assert SYNTH_EMAIL in restored
    assert SYNTH_IP in restored
    assert SYNTH_PHONE in restored


# ---------------------------------------------------------------------------
# 2. Вывод в stdout (без --out) — код 0, текст содержит исходные значения
# ---------------------------------------------------------------------------


def test_restore_to_stdout_returns_zero(
    tmp_path: Path, mock_keyring: bytes, capsys: pytest.CaptureFixture
) -> None:
    """pgw restore без --out выводит восстановленный текст в stdout, код 0."""
    from privacy_gateway.cli import _cmd_restore

    key = mock_keyring
    out_dir = _prepare_artifacts(tmp_path, key)

    prompt_path = out_dir / "prompt.txt"
    route_path = out_dir / "route.json"

    args = _make_args(
        file=str(prompt_path),
        route=str(route_path),
    )
    code = _cmd_restore(args)

    assert code == 0
    captured = capsys.readouterr()
    assert SYNTH_EMAIL in captured.out
    assert SYNTH_IP in captured.out
    assert SYNTH_PHONE in captured.out


# ---------------------------------------------------------------------------
# 3. Строгий отказ при неизвестном токене — код 3, файл не создан
# ---------------------------------------------------------------------------


def test_restore_strict_unknown_token_returns_3(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Неизвестный токен в строгом режиме → код 3, выходной файл отсутствует."""
    from privacy_gateway.cli import _cmd_restore

    key = mock_keyring
    out_dir = _prepare_artifacts(tmp_path, key)
    route_path = out_dir / "route.json"

    # Ответ LLM с несуществующим токеном
    llm_reply_path = tmp_path / "llm_reply.txt"
    llm_reply_path.write_text(
        (out_dir / "prompt.txt").read_text(encoding="utf-8") + " [EMAIL_99]",
        encoding="utf-8",
    )
    result_path = tmp_path / "restored.txt"

    args = _make_args(
        file=str(llm_reply_path),
        route=str(route_path),
        out=str(result_path),
        lenient=False,  # строгий режим явно
    )
    code = _cmd_restore(args)

    assert code == 3
    assert not result_path.exists(), "При ошибке строгого режима файл не должен создаваться"


# ---------------------------------------------------------------------------
# 4. Защита существующего файла без --overwrite → код 3
# ---------------------------------------------------------------------------


def test_restore_no_overwrite_without_flag(
    tmp_path: Path, mock_keyring: bytes
) -> None:
    """Без --overwrite существующий файл результата не перезаписывается, код 3."""
    from privacy_gateway.cli import _cmd_restore

    key = mock_keyring
    out_dir = _prepare_artifacts(tmp_path, key)

    prompt_path = out_dir / "prompt.txt"
    route_path = out_dir / "route.json"
    result_path = tmp_path / "restored.txt"
    result_path.write_text("original content", encoding="utf-8")

    args = _make_args(
        file=str(prompt_path),
        route=str(route_path),
        out=str(result_path),
        overwrite=False,
    )
    code = _cmd_restore(args)

    assert code == 3
    assert result_path.read_text(encoding="utf-8") == "original content"
