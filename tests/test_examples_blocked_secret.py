"""Smoke- и regression-тесты примера ``examples/03_blocked_secret.py`` (#99).

Пример запускается так же, как его запускает пользователь, но в
изолированном окружении: реальный системный keyring, сеть и домашний
каталог пользователя не используются, временные файлы уходят в каталог
теста. Сравнения с секретом выполняются через булев флаг, чтобы значение
не попало в сообщение об ошибке теста.
"""

from __future__ import annotations

import runpy
import socket
import tempfile
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import patch

import pytest

from privacy_gateway import DetectionError, GatewayConfig, PrivacyGateway
from privacy_gateway.crypto import generate_key

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "examples" / "03_blocked_secret.py"
ENTITIES_CONFIG = REPO_ROOT / "config.example" / "entities.yaml"

FORBIDDEN_FRAGMENTS = (
    "manifest",
    "route.json",
    "prompt.txt",
    "pgw-blocked-",
    ".pgw-owner",
    ".pgw-quarantine-",
)

EXPECTED_LINES = (
    "secret_blocked=True",
    "failure_class=DetectionError",
    "failure_status=BLOCKED",
    "provider_calls=0",
    "protected_payload_created=False",
    "pseudonymizable_entity_present=True",
    "workspace_clean=True",
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
    return runpy.run_path(str(EXAMPLE_PATH), run_name="example_99")


def _gateway(workspace: Path) -> PrivacyGateway:
    """Создать шлюз с изолированным рабочим каталогом."""
    return PrivacyGateway(
        GatewayConfig(
            entities_config_path=ENTITIES_CONFIG,
            workspace_dir=workspace,
            strict=True,
        )
    )


def test_example_file_exists() -> None:
    """Пример лежит на документированном пути."""
    assert EXAMPLE_PATH.is_file()


def test_blocked_secret_reports_expected_invariants(
    temp_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Пример завершается кодом 0 и печатает ожидаемые инварианты."""
    key = generate_key()
    with (
        patch("privacy_gateway.keystore.get_key", return_value=key),
        pytest.raises(SystemExit) as excinfo,
    ):
        _run_example()

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    for line in EXPECTED_LINES:
        assert line in captured.out
    assert captured.err == ""
    assert "Traceback" not in captured.out


def test_output_hides_secret_and_artifacts(
    temp_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Вывод не раскрывает секрет, исходный текст и внутренние артефакты."""
    namespace = _load_example()
    capsys.readouterr()

    key = generate_key()
    with (
        patch("privacy_gateway.keystore.get_key", return_value=key),
        pytest.raises(SystemExit),
    ):
        _run_example()

    captured = capsys.readouterr()
    combined = captured.out + captured.err

    secret_leaked = namespace["SYNTHETIC_SECRET_VALUE"] in combined
    assert secret_leaked is False
    secret_line_leaked = namespace["SYNTHETIC_SECRET_LINE"] in combined
    assert secret_line_leaked is False
    request_leaked = namespace["SYNTHETIC_REQUEST"] in combined
    assert request_leaked is False
    for value in namespace["PSEUDONYMIZABLE_VALUES"]:
        assert value not in combined
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in combined
    assert list(temp_root.iterdir()) == []


def test_provider_is_never_called(temp_root: Path) -> None:
    """Обработчик-шпион не получает ни одного вызова при блокировке."""
    namespace = _load_example()
    spy = namespace["SpyProvider"]()

    key = generate_key()
    with patch("privacy_gateway.keystore.get_key", return_value=key):
        assert namespace["run_blocked_secret_demo"](spy) == 0

    assert spy.calls == 0


def test_spy_provider_fails_loudly_when_called() -> None:
    """Шпион действительно упал бы при вызове и считает вызовы."""
    namespace = _load_example()
    spy = namespace["SpyProvider"]()

    with pytest.raises(AssertionError):
        spy("Обращение [EMAIL_1] по [PROJECT_1].\n")

    assert spy.calls == 1


def test_prepare_raises_blocked_detection_error(temp_root: Path) -> None:
    """Публичный ``prepare`` поднимает ``DetectionError`` со статусом BLOCKED."""
    namespace = _load_example()
    workspace = temp_root / "blocked"
    workspace.mkdir()
    gateway = _gateway(workspace)

    key = generate_key()
    with (
        patch("privacy_gateway.keystore.get_key", return_value=key),
        pytest.raises(DetectionError) as excinfo,
    ):
        gateway.prepare(namespace["SYNTHETIC_REQUEST"], correlation_id="blocked")

    assert excinfo.value.status == "BLOCKED"
    message_leaked = namespace["SYNTHETIC_SECRET_VALUE"] in str(excinfo.value)
    assert message_leaked is False
    assert list(workspace.iterdir()) == []


def test_regular_entity_alone_does_not_block(temp_root: Path) -> None:
    """Обычная сущность без секрета блокировку не вызывает и токенизируется."""
    namespace = _load_example()
    workspace = temp_root / "allowed"
    workspace.mkdir()
    gateway = _gateway(workspace)

    key = generate_key()
    with patch("privacy_gateway.keystore.get_key", return_value=key):
        prepared = gateway.prepare(
            namespace["SYNTHETIC_REQUEST_WITHOUT_SECRET"],
            correlation_id="allowed",
        )
        try:
            assert prepared.token_count > 0
            for value in namespace["PSEUDONYMIZABLE_VALUES"]:
                assert value not in prepared.text
        finally:
            gateway.discard(prepared.context)

    assert list(workspace.iterdir()) == []


def test_example_output_is_deterministic(
    temp_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Повторный запуск примера даёт идентичный вывод."""
    key = generate_key()
    outputs: list[str] = []
    for _ in range(2):
        with (
            patch("privacy_gateway.keystore.get_key", return_value=key),
            pytest.raises(SystemExit) as excinfo,
        ):
            _run_example()
        assert excinfo.value.code == 0
        outputs.append(capsys.readouterr().out)

    assert outputs[0] == outputs[1]
    assert list(temp_root.iterdir()) == []
