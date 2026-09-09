"""Subprocess-тест сквозного CLI-сценария ``examples/06_cli_round_trip.md`` (#102).

Сценарий выполняется так же, как его выполняет пользователь — отдельными
процессами ``python -m privacy_gateway`` — но в изолированном окружении:
системный keyring, сеть и домашний каталог пользователя не используются.

Изоляция keystore выполняется через ``sitecustomize`` в отдельном
``PYTHONPATH``: модуль загружается до импорта пакета, поэтому CLI получает
детерминированный тестовый ключ вместо системного хранилища. Подмена
применяется и к ``pipeline``, и к ``restore``: эти модули связывают
доступ к ключу на своём уровне и импортируются раньше, чем sitecustomize
переопределяет ``keystore``. Путь к ``entities.yaml`` передаётся явно,
чтобы результат не зависел от cwd.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from privacy_gateway.crypto import generate_key

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "examples" / "06_cli_round_trip.md"
INDEX_PATH = REPO_ROOT / "examples" / "README.md"
ENTITIES_CONFIG = REPO_ROOT / "config.example" / "entities.yaml"

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_HOST = "192.0.2.10"
SYNTH_PHONE = "+7 900 000-00-00"
SYNTH_REQUEST = (
    f"Клиент {SYNTH_EMAIL} сообщил сбой на узле {SYNTH_HOST}.\n"
    f"Контактный телефон {SYNTH_PHONE}.\n"
)
SYNTH_VALUES = (SYNTH_EMAIL, SYNTH_HOST, SYNTH_PHONE)

UNKNOWN_TOKEN = "[EMAIL_99]"
STRICT_REFUSAL_EXIT = 5
BLOCKED_EXIT = 3

_SITECUSTOMIZE = """\
\"\"\"Изоляция keystore для subprocess-теста CLI round-trip.\"\"\"

import os

import privacy_gateway.keystore as keystore
import privacy_gateway.pipeline as pipeline
import privacy_gateway.restore as restore

_KEY = os.environ["PGW_TEST_KEY"].encode()

keystore.get_key = lambda: _KEY
keystore.get_all_keys = lambda: [_KEY]
keystore.key_exists = lambda: True
pipeline.get_key = keystore.get_key
restore.get_all_keys = keystore.get_all_keys
"""


class Workspace:
    """Изолированное окружение запуска CLI."""

    def __init__(self, root: Path, env: dict[str, str]) -> None:
        self.root = root
        self.env = env
        self.home = root / "home"
        self.demo = root / "pgw_demo"
        self.work = self.demo / "work"
        self.request = self.demo / "request.txt"

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Запустить CLI отдельным процессом."""
        return subprocess.run(
            [sys.executable, "-m", "privacy_gateway", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env,
            cwd=self.root,
        )

    def prepare(self) -> subprocess.CompletedProcess[str]:
        """Выполнить шаг подготовки запроса."""
        return self.run(
            "prepare",
            str(self.request),
            "--out",
            str(self.work),
            "--config",
            str(ENTITIES_CONFIG),
        )

    def reply(self, name: str, suffix: str) -> Path:
        """Собрать имитацию ответа провайдера из защищённого текста."""
        prompt = (self.work / "prompt.txt").read_text(encoding="utf-8")
        path = self.demo / name
        path.write_text(prompt + suffix, encoding="utf-8")
        return path


@pytest.fixture()
def workspace(tmp_path: Path) -> Iterator[Workspace]:
    """Подготовить изолированный keystore, домашний каталог и входной файл."""
    key = generate_key()
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    demo = tmp_path / "pgw_demo"
    demo.mkdir()
    (demo / "request.txt").write_text(SYNTH_REQUEST, encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(shim)
    env["PGW_TEST_KEY"] = key.decode()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    yield Workspace(tmp_path, env)


def test_example_file_exists() -> None:
    """Документ сценария лежит на документированном пути."""
    assert EXAMPLE_PATH.is_file()


def test_example_is_linked_from_index() -> None:
    """Индекс примеров ссылается на документ сценария."""
    assert "06_cli_round_trip.md" in INDEX_PATH.read_text(encoding="utf-8")


def test_example_does_not_promise_absent_commands() -> None:
    """Документ не обещает отсутствующую команду удаления ключа."""
    assert "pgw key delete" not in EXAMPLE_PATH.read_text(encoding="utf-8")


def test_prepare_creates_three_artifacts(workspace: Workspace) -> None:
    """Подготовка даёт код 0, строку OK и ровно три артефакта."""
    result = workspace.prepare()

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("OK: ")
    assert (workspace.work / "prompt.txt").is_file()
    assert (workspace.work / "route.json").is_file()
    assert (workspace.work / "manifest.json").is_file()
    assert [p.name for p in workspace.work.iterdir() if p.is_dir()] == []


def test_prepare_artifacts_have_no_original_values(workspace: Workspace) -> None:
    """Защищённый текст и маршрут не содержат исходных значений."""
    result = workspace.prepare()
    assert result.returncode == 0, result.stderr

    prompt = (workspace.work / "prompt.txt").read_text(encoding="utf-8")
    route = (workspace.work / "route.json").read_text(encoding="utf-8")
    for value in SYNTH_VALUES:
        assert value not in prompt, value
        assert value not in route, value
        assert value not in result.stdout, value


def test_repeated_prepare_without_overwrite_is_blocked(
    workspace: Workspace,
) -> None:
    """Повторная подготовка без --overwrite отклоняется кодом 3."""
    assert workspace.prepare().returncode == 0

    result = workspace.prepare()

    assert result.returncode == BLOCKED_EXIT
    assert result.stderr.startswith("BLOCKED: ")
    assert result.stdout == ""


def test_round_trip_restores_original_values(workspace: Workspace) -> None:
    """Восстановление возвращает исходные значения и не печатает их в отчёте."""
    assert workspace.prepare().returncode == 0
    reply = workspace.reply("reply.txt", "\nСтатус: заявка принята.\n")
    restored = workspace.demo / "restored.txt"

    result = workspace.run(
        "restore",
        str(reply),
        "--route",
        str(workspace.work / "route.json"),
        "--out",
        str(restored),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"OK: {restored}\n"
    assert "Восстановлено: " in result.stderr

    text = restored.read_text(encoding="utf-8")
    for value in SYNTH_VALUES:
        assert value in text, value
        assert value not in result.stderr, value
        assert value not in result.stdout, value


def test_strict_refusal_leaves_no_output(workspace: Workspace) -> None:
    """Неизвестный токен даёт код 5 и не публикует частичный результат."""
    assert workspace.prepare().returncode == 0
    reply = workspace.reply("bad_reply.txt", f"\nСвяжитесь с {UNKNOWN_TOKEN}.\n")
    restored = workspace.demo / "bad_restored.txt"

    result = workspace.run(
        "restore",
        str(reply),
        "--route",
        str(workspace.work / "route.json"),
        "--out",
        str(restored),
    )

    assert result.returncode == STRICT_REFUSAL_EXIT
    assert result.stdout == ""
    assert not restored.exists()
    for value in SYNTH_VALUES:
        assert value not in result.stderr, value


def test_cleanup_removes_workspace(workspace: Workspace) -> None:
    """Документированная очистка удаляет каталог вместе с манифестом."""
    assert workspace.prepare().returncode == 0
    assert (workspace.work / "manifest.json").is_file()

    shutil.rmtree(workspace.demo)

    assert not workspace.demo.exists()


def test_scenario_does_not_write_to_home(workspace: Workspace) -> None:
    """Сценарий не создаёт файлов в домашнем каталоге пользователя."""
    assert workspace.prepare().returncode == 0

    assert list(workspace.home.iterdir()) == []
