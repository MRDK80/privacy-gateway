"""Тесты примера ``examples/04_untrusted_response.py`` (#100).

Пример запускается так же, как его запускает пользователь, но в
изолированном окружении: без сети, без реального системного keyring и без
домашнего каталога пользователя. Ключи подменяются в двух точках:
``prepare`` берёт активный ключ через ``privacy_gateway.keystore.get_key``,
а ``restore`` — через ``get_all_keys``, импортированный в модуль
``privacy_gateway.restore``. Сравнения с синтетическими значениями
выполняются через булев флаг, чтобы значение не попало в отчёт pytest.
"""

from __future__ import annotations

import runpy
import socket
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import patch

import pytest

from privacy_gateway import GatewayConfig, PrivacyGateway, StrictTokenError
from privacy_gateway.crypto import generate_key

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "examples" / "04_untrusted_response.py"
ENTITIES_CONFIG = REPO_ROOT / "config.example" / "entities.yaml"

FORBIDDEN_FRAGMENTS = (
    "manifest",
    "route.json",
    "prompt.txt",
    "pgw-untrusted-",
    ".pgw-owner",
    ".pgw-quarantine-",
)

EXPECTED_TAIL = (
    "provider_calls=10",
    "protected_leak_free=True",
    "workspace_clean=True",
    "all_variants_expected=True",
)

CASES = [
    ("preserved", "strict", "restored", 0),
    ("removed", "strict", "restored", 1),
    ("duplicated", "strict", "restored", 0),
    ("malformed", "strict", "StrictTokenError", None),
    ("fabricated", "strict", "StrictTokenError", None),
    ("preserved", "lenient", "restored", 0),
    ("removed", "lenient", "restored", 1),
    ("duplicated", "lenient", "restored", 0),
    ("malformed", "lenient", "restored", 1),
    ("fabricated", "lenient", "restored", 0),
]


def _no_network(*args: object, **kwargs: object) -> NoReturn:
    """Запретить любые сетевые сокеты во время примера."""
    raise AssertionError("Пример не должен обращаться к сети.")


@contextmanager
def _isolated_keys(key: bytes) -> Iterator[None]:
    """Подменить ключи и в ``prepare``, и в ``restore``.

    ``restore_text`` вызывает ``get_all_keys`` из пространства имён
    ``privacy_gateway.restore``, поэтому патч только ``keystore.get_key``
    оставил бы обращение к системному keyring.
    """
    with (
        patch("privacy_gateway.keystore.get_key", return_value=key),
        patch("privacy_gateway.restore.get_all_keys", return_value=[key]),
    ):
        yield


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
    return runpy.run_path(str(EXAMPLE_PATH), run_name="example_100")


def test_example_file_exists() -> None:
    """Пример лежит на документированном пути."""
    assert EXAMPLE_PATH.is_file()


@pytest.mark.parametrize(("variant", "mode", "expected", "missing"), CASES)
def test_variant_matches_public_contract(
    temp_root: Path,
    variant: str,
    mode: str,
    expected: str,
    missing: int | None,
) -> None:
    """Каждый вариант воспроизводит фактическое поведение публичного API."""
    namespace = _load_example()
    workspace = temp_root / f"{variant}-{mode}"
    workspace.mkdir()
    provider = namespace["RecordingProvider"]()

    with _isolated_keys(generate_key()):
        outcome = namespace["run_variant"](variant, mode, provider, workspace)

    assert outcome.outcome == expected
    assert outcome.workspace_clean is True
    assert outcome.fabricated_token_unresolved is True
    assert provider.calls == 1
    if missing is None:
        assert outcome.tokens_restored is None
        assert outcome.tokens_missing is None
    else:
        assert outcome.tokens_missing == missing
        assert outcome.tokens_restored is not None
    assert list(workspace.iterdir()) == []


def test_example_reports_expected_summary(
    temp_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Пример завершается кодом 0 и печатает ожидаемые итоги."""
    with _isolated_keys(generate_key()), pytest.raises(SystemExit) as excinfo:
        _run_example()

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    for line in EXPECTED_TAIL:
        assert line in captured.out
    assert captured.out.count("as_expected=True") == len(CASES)
    assert captured.err == ""
    assert "Traceback" not in captured.out


def test_output_hides_values_and_artifacts(
    temp_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Вывод не раскрывает значения, токены и внутренние артефакты."""
    namespace = _load_example()
    capsys.readouterr()

    with _isolated_keys(generate_key()), pytest.raises(SystemExit):
        _run_example()

    captured = capsys.readouterr()
    combined = captured.out + captured.err

    request_leaked = namespace["SYNTHETIC_REQUEST"] in combined
    assert request_leaked is False
    for value in namespace["SYNTHETIC_VALUES"]:
        value_leaked = value in combined
        assert value_leaked is False
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in combined
    assert "[" not in combined
    assert list(temp_root.iterdir()) == []


def test_provider_receives_only_protected_text(temp_root: Path) -> None:
    """Обработчик получает только защищённый текст на всех вариантах."""
    namespace = _load_example()
    provider = namespace["RecordingProvider"]()

    with _isolated_keys(generate_key()):
        assert namespace["run_untrusted_response_demo"](provider) == 0

    assert provider.calls == len(CASES)
    for received in provider.received:
        for value in namespace["SYNTHETIC_VALUES"]:
            value_leaked = value in received
            assert value_leaked is False
    assert list(temp_root.iterdir()) == []


def test_strict_error_hides_values_and_cleans_workspace(
    temp_root: Path,
) -> None:
    """Строгий отказ не возвращает текст и не раскрывает значения."""
    namespace = _load_example()
    workspace = temp_root / "public-strict"
    workspace.mkdir()
    gateway = PrivacyGateway(
        GatewayConfig(
            entities_config_path=ENTITIES_CONFIG,
            workspace_dir=workspace,
            strict=True,
        )
    )

    with _isolated_keys(generate_key()):
        prepared = gateway.prepare(
            namespace["SYNTHETIC_REQUEST"], correlation_id="strict-message"
        )
        try:
            with pytest.raises(StrictTokenError) as excinfo:
                gateway.restore(
                    f"{prepared.text}Уточнение по [PROJECT_90].\n",
                    context=prepared.context,
                )
        finally:
            gateway.discard(prepared.context)

    message = str(excinfo.value)
    for value in namespace["SYNTHETIC_VALUES"]:
        value_leaked = value in message
        assert value_leaked is False
    assert list(workspace.iterdir()) == []


def test_example_output_is_deterministic(
    temp_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Повторный запуск примера даёт идентичный вывод."""
    key = generate_key()
    outputs: list[str] = []
    for _ in range(2):
        with _isolated_keys(key), pytest.raises(SystemExit) as excinfo:
            _run_example()
        assert excinfo.value.code == 0
        outputs.append(capsys.readouterr().out)

    assert outputs[0] == outputs[1]
    assert list(temp_root.iterdir()) == []
