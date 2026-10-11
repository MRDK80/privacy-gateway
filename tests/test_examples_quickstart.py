\
"""Smoke-тест quickstart-примера ``examples/01_quickstart.py`` (#97).

Пример запускается так же, как его запускает пользователь, но в
изолированном окружении: реальный системный keyring, сеть и домашний
каталог пользователя не используются, временные файлы уходят в каталог
теста.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway.crypto import generate_key

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "examples" / "01_quickstart.py"

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_HOST = "192.0.2.10"
SYNTH_PHONE = "+7 900 000-00-00"

FORBIDDEN_FRAGMENTS = (
    SYNTH_EMAIL,
    SYNTH_HOST,
    SYNTH_PHONE,
    "manifest",
    "route.json",
    "pgw-quickstart-",
)


def _run_example() -> None:
    """Выполнить файл примера как самостоятельный сценарий."""
    runpy.run_path(str(EXAMPLE_PATH), run_name="__main__")


def test_example_file_exists() -> None:
    """Пример лежит на документированном пути."""
    assert EXAMPLE_PATH.is_file()


def test_quickstart_reports_expected_invariants(
    sandbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Пример завершается кодом 0 и печатает ожидаемые инварианты."""
    key = generate_key()
    with (
        patch("privacy_gateway.keystore.get_key", return_value=key),
        patch("privacy_gateway.restore.get_all_keys", return_value=[key]),
        pytest.raises(SystemExit) as excinfo,
    ):
        _run_example()

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "protected_leak_free=True" in out
    assert "roundtrip_exact=True" in out
    assert "workspace_clean=True" in out
    assert "tokens_missing=0" in out


def test_quickstart_output_hides_sensitive_data(
    sandbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Вывод примера не раскрывает значения, пути и артефакты."""
    key = generate_key()
    with (
        patch("privacy_gateway.keystore.get_key", return_value=key),
        patch("privacy_gateway.restore.get_all_keys", return_value=[key]),
        pytest.raises(SystemExit),
    ):
        _run_example()

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in combined
    assert list(sandbox.iterdir()) == []


def test_discard_runs_when_processing_fails(sandbox: Path) -> None:
    """При исключении рабочий каталог всё равно освобождается."""
    key = generate_key()
    with (
        patch("privacy_gateway.keystore.get_key", return_value=key),
        patch(
            "privacy_gateway.facade.PrivacyGateway.restore",
            side_effect=RuntimeError("сбой обработчика"),
        ),
        pytest.raises(RuntimeError),
    ):
        _run_example()

    leftovers = [
        child for base in sandbox.iterdir() for child in base.iterdir()
    ]
    assert leftovers == []
