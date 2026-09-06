"""Smoke- и regression-тесты примера ``examples/02_full_round_trip.py`` (#98).

Пример запускается так же, как его запускает пользователь, но в
изолированном окружении: реальный системный keyring, сеть и домашний
каталог пользователя не используются, временные файлы уходят в каталог
теста.
"""

from __future__ import annotations

import inspect
import runpy
import socket
import tempfile
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import patch

import pytest

from privacy_gateway.crypto import generate_key

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "examples" / "02_full_round_trip.py"

FORBIDDEN_FRAGMENTS = (
    "manifest",
    "route.json",
    "pgw-round-trip-",
    ".pgw-owner",
    ".pgw-quarantine-",
)


def _no_network(*args: object, **kwargs: object) -> NoReturn:
    """Запретить любые сетевые сокеты во время примера."""
    raise AssertionError("Пример не должен обращаться к сети.")


@pytest.fixture()
def temp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Изолировать домашний каталог, временные файлы и сеть."""
    home = tmp_path / "home"
    temp = tmp_path / "temp"
    home.mkdir()
    temp.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.chdir(REPO_ROOT)
    return temp


def _run_example() -> None:
    """Выполнить файл примера как самостоятельный сценарий."""
    runpy.run_path(str(EXAMPLE_PATH), run_name="__main__")


def _load_example() -> dict[str, Any]:
    """Загрузить пространство имён примера без запуска ``main``."""
    return runpy.run_path(str(EXAMPLE_PATH), run_name="example_98")


def _tokens(output: str, key: str) -> int:
    """Извлечь целочисленное значение строки вида ``ключ=значение``."""
    for line in output.splitlines():
        name, _, value = line.partition("=")
        if name == key:
            return int(value)
    raise AssertionError(f"В выводе примера нет строки {key}.")


def test_example_file_exists() -> None:
    """Пример лежит на документированном пути."""
    assert EXAMPLE_PATH.is_file()


def test_round_trip_reports_expected_invariants(
    temp_root: Path, capsys: pytest.CaptureFixture[str]
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
    assert "unsupported_document_visible=True" in out
    assert "roundtrip_exact=True" in out
    assert "workspace_clean=True" in out
    assert "tokens_missing=0" in out
    assert _tokens(out, "tokens_prepared") == _tokens(out, "tokens_restored")
    assert _tokens(out, "tokens_prepared") > 0


def test_output_hides_internal_artifacts(
    temp_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Вывод примера не раскрывает артефакты, пути и ключевой материал."""
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
    assert list(temp_root.iterdir()) == []


def test_provider_boundary_accepts_only_protected_text() -> None:
    """Обработчик принимает один строковый аргумент и детерминирован."""
    namespace = _load_example()
    provider = namespace["fake_provider"]
    signature = inspect.signature(provider)
    assert list(signature.parameters) == ["protected_text"]
    parameter = signature.parameters["protected_text"]
    assert parameter.annotation == "str"
    assert parameter.default is inspect.Parameter.empty

    probe = "Обращение [EMAIL_1] по [PROJECT_1].\n"
    expected = (
        namespace["PROVIDER_HEADER"] + probe + namespace["PROVIDER_FOOTER"]
    )
    assert provider(probe) == expected
    assert provider(probe) == provider(probe)


def test_protected_text_hides_supported_values(temp_root: Path) -> None:
    """Обработчик не видит исходных значений поддерживаемых сущностей."""
    namespace = _load_example()
    seen: list[str] = []

    def recording_provider(protected_text: str) -> str:
        seen.append(protected_text)
        return namespace["fake_provider"](protected_text)

    key = generate_key()
    with (
        patch("privacy_gateway.keystore.get_key", return_value=key),
        patch("privacy_gateway.restore.get_all_keys", return_value=[key]),
    ):
        assert namespace["run_round_trip"](recording_provider) == 0

    assert len(seen) == 1
    protected = seen[0]
    for value in namespace["SUPPORTED_VALUES"]:
        assert value not in protected
    assert namespace["UNSUPPORTED_DOCUMENT"] in protected


def test_discard_runs_when_provider_fails(temp_root: Path) -> None:
    """При исключении обработчика рабочий каталог всё равно освобождается."""
    namespace = _load_example()

    def failing_provider(protected_text: str) -> str:
        raise RuntimeError("сбой обработчика")

    key = generate_key()
    with (
        patch("privacy_gateway.keystore.get_key", return_value=key),
        pytest.raises(RuntimeError),
    ):
        namespace["run_round_trip"](failing_provider)

    leftovers = [
        child for base in temp_root.iterdir() for child in base.iterdir()
    ]
    assert leftovers == []


def test_discard_runs_when_restore_fails(temp_root: Path) -> None:
    """При исключении restore рабочий каталог всё равно освобождается."""
    key = generate_key()
    with (
        patch("privacy_gateway.keystore.get_key", return_value=key),
        patch(
            "privacy_gateway.facade.PrivacyGateway.restore",
            side_effect=RuntimeError("сбой восстановления"),
        ),
        pytest.raises(RuntimeError),
    ):
        _run_example()

    leftovers = [
        child for base in temp_root.iterdir() for child in base.iterdir()
    ]
    assert leftovers == []
