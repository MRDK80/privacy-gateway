"""Характеризующие тесты CLI — задача #25.

Цель: зафиксировать текущее наблюдаемое поведение CLI до рефакторинга
трансляции исключений (#18) и выделения нейтрального прикладного слоя (#27).

Все тесты вызывают CLI через ``sys.argv`` + ``privacy_gateway.cli.main()``.
Внутренние ``_cmd_*`` напрямую не вызываются.

Сомнительное поведение фиксируется как есть, без исправлений:
  - argparse завершает ошибку разбора кодом 2, совпадающим с PENDING (#26);
  - ConfigurationError из ``write_restored`` даёт код 1, а не 3 (#28);
  - ``pgw key`` без подкоманды завершается кодом 0 (argparse ``--help``),
    хотя в описании #25 ожидался код 1 — расхождение зафиксировано тестом.

Реальный keyring не используется. Все данные синтетические, файлы — под
``tmp_path``.
"""

from __future__ import annotations

import io
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from privacy_gateway.crypto import generate_key
from privacy_gateway.keystore import KeyNotFoundError, KeystoreError
from privacy_gateway.models import (
    ConfigurationError,
    ProcessingStatus,
    RestoreStrictError,
)
from privacy_gateway.pipeline import PipelineResult
from privacy_gateway.restore import RestoreError, RestoreResult

# ---------------------------------------------------------------------------
# Синтетические данные (не реальные ПДн)
# ---------------------------------------------------------------------------

SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_TEXT = f"Контакт: {SYNTH_EMAIL}\n"
SYNTH_LLM_REPLY = "Ответ модели: [EMAIL_1]"

# Fernet-ключ в base64url: 43 символа + '='
_KEY_MATERIAL_RE = re.compile(r"[A-Za-z0-9_\-]{43}=")


# ---------------------------------------------------------------------------
# Общий helper и фикстуры
# ---------------------------------------------------------------------------


def _exit_code(exc: SystemExit) -> int:
    """Нормализовать SystemExit.code к int (семантика после #17)."""
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return int(code)


def _run(*argv: str) -> int:
    """Запустить CLI через sys.argv + main(); вернуть код завершения."""
    from privacy_gateway.cli import main

    with patch.object(sys, "argv", ["pgw", *argv]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    return _exit_code(exc_info.value)


class _FakeStdin:
    """Минимальная замена sys.stdin: read_input читает sys.stdin.buffer."""

    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


@pytest.fixture()
def fernet_key() -> bytes:
    """Синтетический Fernet-ключ, keyring не используется."""
    return generate_key()


@pytest.fixture()
def cli_key(fernet_key: bytes) -> Iterator[bytes]:
    """get_key импортирован в cli.py на уровне модуля — патчим там."""
    with patch("privacy_gateway.cli.get_key", return_value=fernet_key):
        yield fernet_key


def _input_file(tmp_path: Path, text: str = SYNTH_TEXT) -> Path:
    """Создать входной .txt под tmp_path."""
    src = tmp_path / "input.txt"
    src.write_text(text, encoding="utf-8")
    return src


def _artifact_paths(out_dir: Path) -> tuple[Path, Path, Path]:
    return (
        out_dir / "prompt.txt",
        out_dir / "route.json",
        out_dir / "manifest.json",
    )


def _expected_ok_line(out_dir: Path) -> str:
    prompt_path, route_path, manifest_path = _artifact_paths(out_dir)
    return f"OK: {prompt_path} / {route_path} / {manifest_path}\n"


def _empty_restore_result(text: str = "восстановлено") -> RestoreResult:
    return RestoreResult(restored_text=text, strict=True)


# ---------------------------------------------------------------------------
# prepare: успех, layout, отсутствие утечек
# ---------------------------------------------------------------------------


def test_prepare_success_exit_code_and_stdout(
    tmp_path: Path, cli_key: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """Успех prepare: код 0, точная строка stdout, пустой stderr."""
    src = _input_file(tmp_path)
    out_dir = tmp_path / "out"

    code = _run("prepare", str(src), "--out", str(out_dir))

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == _expected_ok_line(out_dir)
    assert captured.err == ""


def test_prepare_success_artifact_layout(tmp_path: Path, cli_key: bytes) -> None:
    """Артефакты лежат непосредственно в --out DIR, без подкаталогов."""
    src = _input_file(tmp_path)
    out_dir = tmp_path / "out"

    code = _run("prepare", str(src), "--out", str(out_dir))

    prompt_path, route_path, manifest_path = _artifact_paths(out_dir)
    assert code == 0
    assert prompt_path.is_file()
    assert route_path.is_file()
    assert manifest_path.is_file()
    subdirs = [p.name for p in out_dir.iterdir() if p.is_dir()]
    assert subdirs == []


def test_prepare_artifacts_have_no_plaintext(
    tmp_path: Path, cli_key: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """Исходные значения и ключ не попадают в артефакты и вывод."""
    src = _input_file(tmp_path)
    out_dir = tmp_path / "out"

    code = _run("prepare", str(src), "--out", str(out_dir))

    prompt_path, route_path, _ = _artifact_paths(out_dir)
    prompt = prompt_path.read_text(encoding="utf-8")
    route_raw = route_path.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert code == 0
    assert SYNTH_EMAIL not in prompt
    assert SYNTH_EMAIL not in route_raw
    assert SYNTH_EMAIL not in captured.out
    assert not _KEY_MATERIAL_RE.search(captured.out)


def test_prepare_source_ref_is_file_name(tmp_path: Path, cli_key: bytes) -> None:
    """source_ref в route.json равен имени входного файла."""
    src = _input_file(tmp_path)
    out_dir = tmp_path / "out"

    code = _run("prepare", str(src), "--out", str(out_dir))

    route_data = json.loads((out_dir / "route.json").read_text(encoding="utf-8"))
    assert code == 0
    assert route_data["source_ref"] == "input.txt"


def test_prepare_source_ref_is_stdin(tmp_path: Path, cli_key: bytes) -> None:
    """При вводе через '-' source_ref равен 'stdin'."""
    out_dir = tmp_path / "out"
    fake_stdin = _FakeStdin(SYNTH_TEXT.encode("utf-8"))

    with patch.object(sys, "stdin", fake_stdin):
        code = _run("prepare", "-", "--out", str(out_dir))

    route_data = json.loads((out_dir / "route.json").read_text(encoding="utf-8"))
    assert code == 0
    assert route_data["source_ref"] == "stdin"


# ---------------------------------------------------------------------------
# prepare: коды 1 / 2 / 3 / 4
# ---------------------------------------------------------------------------


def test_prepare_pending_maps_to_code_2(
    tmp_path: Path, cli_key: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """PENDING из конвейера → код 2 и точная строка stderr."""
    src = _input_file(tmp_path)
    out_dir = tmp_path / "out"
    pending = PipelineResult(
        status=ProcessingStatus.PENDING,
        message="Validation PENDING.",
    )

    with patch("privacy_gateway.cli.prepare_pipeline", return_value=pending):
        code = _run("prepare", str(src), "--out", str(out_dir))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == "PENDING: Validation PENDING.\n"
    assert captured.out == ""
    assert not out_dir.exists()


def test_prepare_blocked_maps_to_code_3(
    tmp_path: Path, cli_key: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """BLOCKED из конвейера → код 3 и точная строка stderr."""
    src = _input_file(tmp_path)
    out_dir = tmp_path / "out"
    blocked = PipelineResult(
        status=ProcessingStatus.BLOCKED,
        message="Validation BLOCKED.",
    )

    with patch("privacy_gateway.cli.prepare_pipeline", return_value=blocked):
        code = _run("prepare", str(src), "--out", str(out_dir))

    captured = capsys.readouterr()
    assert code == 3
    assert captured.err == "BLOCKED: Validation BLOCKED.\n"
    assert captured.out == ""


def test_prepare_unexpected_error_maps_to_code_1(
    tmp_path: Path, cli_key: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """Непредвиденная ошибка конвейера → код 1 и точная строка stderr."""
    src = _input_file(tmp_path)
    out_dir = tmp_path / "out"

    with patch(
        "privacy_gateway.cli.prepare_pipeline",
        side_effect=RuntimeError("сбой конвейера"),
    ):
        code = _run("prepare", str(src), "--out", str(out_dir))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == "Непредвиденная ошибка: сбой конвейера\n"
    assert captured.out == ""


def test_prepare_keystore_error_maps_to_code_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """KeystoreError от get_key() → код 4, ключ и значения не выводятся."""
    src = _input_file(tmp_path)
    out_dir = tmp_path / "out"

    with patch(
        "privacy_gateway.cli.get_key",
        side_effect=KeystoreError("небезопасный backend"),
    ):
        code = _run("prepare", str(src), "--out", str(out_dir))

    captured = capsys.readouterr()
    assert code == 4
    assert captured.err == "Ошибка keystore: небезопасный backend\n"
    assert captured.out == ""
    assert SYNTH_EMAIL not in captured.err
    assert not _KEY_MATERIAL_RE.search(captured.err)


# ---------------------------------------------------------------------------
# prepare: overwrite (CLI-флаг и routing YAML объединяются по or)
# ---------------------------------------------------------------------------


def test_prepare_existing_artifacts_blocked_without_overwrite(
    tmp_path: Path, cli_key: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """Без --overwrite существующие артефакты дают BLOCKED и код 3."""
    src = _input_file(tmp_path)
    out_dir = tmp_path / "out"

    assert _run("prepare", str(src), "--out", str(out_dir)) == 0
    capsys.readouterr()

    code = _run("prepare", str(src), "--out", str(out_dir))

    captured = capsys.readouterr()
    assert code == 3
    assert captured.err == (
        "BLOCKED: Output file(s) already exist: prompt.txt, route.json. "
        "Use --overwrite to allow replacement.\n"
    )


def test_prepare_overwrite_flag_allows_replacement(
    tmp_path: Path, cli_key: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-флаг --overwrite разрешает замену артефактов."""
    src = _input_file(tmp_path)
    out_dir = tmp_path / "out"

    assert _run("prepare", str(src), "--out", str(out_dir)) == 0
    capsys.readouterr()

    code = _run("prepare", str(src), "--out", str(out_dir), "--overwrite")

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == _expected_ok_line(out_dir)


def test_prepare_overwrite_from_routing_config_allows_replacement(
    tmp_path: Path, cli_key: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """overwrite: true из routing YAML разрешает замену без CLI-флага."""
    src = _input_file(tmp_path)
    out_dir = tmp_path / "out"
    routing = tmp_path / "routing.yaml"
    routing.write_text("overwrite: true\n", encoding="utf-8")

    assert _run("prepare", str(src), "--out", str(out_dir)) == 0
    capsys.readouterr()

    code = _run(
        "prepare",
        str(src),
        "--out",
        str(out_dir),
        "--routing",
        str(routing),
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == _expected_ok_line(out_dir)


# ---------------------------------------------------------------------------
# restore: отчёт, вывод, коды
# ---------------------------------------------------------------------------


def test_restore_report_lists_all_four_categories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Отчёт stderr: missing, unknown, malformed, duplicated — точные строки."""
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)
    route_path = tmp_path / "route.json"
    result = RestoreResult(
        restored_text="восстановлено",
        tokens_expected={"EMAIL_1", "EMAIL_2", "EMAIL_3"},
        tokens_found={"EMAIL_1"},
        tokens_missing={"EMAIL_2"},
        tokens_unknown={"HOST_9"},
        tokens_malformed=["email_1"],
        tokens_duplicated={"EMAIL_1"},
        warnings=["Токен отсутствует в ответе LLM: EMAIL_2"],
        strict=False,
    )

    with patch("privacy_gateway.restore.restore_text", return_value=result):
        code = _run(
            "restore",
            str(reply),
            "--route",
            str(route_path),
            "--lenient",
        )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == (
        "ПРЕДУПРЕЖДЕНИЕ: Токен отсутствует в ответе LLM: EMAIL_2\n"
        "Восстановлено: 1/3 токенов\n"
        "  Не найдено в ответе: 1 (['EMAIL_2'])\n"
        "  Неизвестных токенов: 1 (['HOST_9'])\n"
        "  Искажённых кандидатов: 1 (['email_1'])\n"
        "  Дублированных токенов: 1 (['EMAIL_1'])\n"
    )


def test_restore_manifest_override_is_passed_through(tmp_path: Path) -> None:
    """CLI передаёт выбранный manifest в restore_text(manifest_path_override=)."""
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)
    route_path = tmp_path / "route.json"
    manifest_path = tmp_path / "other-manifest.json"
    mock_restore: MagicMock = MagicMock(return_value=_empty_restore_result())

    with patch("privacy_gateway.restore.restore_text", mock_restore):
        code = _run(
            "restore",
            str(reply),
            "--route",
            str(route_path),
            "--manifest",
            str(manifest_path),
        )

    assert code == 0
    mock_restore.assert_called_once_with(
        llm_response=SYNTH_LLM_REPLY,
        route_path=route_path,
        manifest_path_override=manifest_path,
        strict=True,
    )


def test_restore_stdout_has_no_added_newline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Без --out результат печатается print(..., end='') без перевода строки."""
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)
    route_path = tmp_path / "route.json"
    result = _empty_restore_result("текст без перевода строки")

    with patch("privacy_gateway.restore.restore_text", return_value=result):
        code = _run("restore", str(reply), "--route", str(route_path))

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "текст без перевода строки"


def test_restore_stdout_preserves_own_trailing_newline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Собственный перевод строки в тексте не удваивается печатью."""
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)
    route_path = tmp_path / "route.json"
    result = _empty_restore_result("текст с переводом\n")

    with patch("privacy_gateway.restore.restore_text", return_value=result):
        code = _run("restore", str(reply), "--route", str(route_path))

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "текст с переводом\n"


def test_restore_out_prints_ok_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """С --out результат пишется в файл, stdout — точная строка OK."""
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)
    route_path = tmp_path / "route.json"
    out_path = tmp_path / "restored.txt"
    result = _empty_restore_result("итоговый текст")

    with patch("privacy_gateway.restore.restore_text", return_value=result):
        code = _run(
            "restore",
            str(reply),
            "--route",
            str(route_path),
            "--out",
            str(out_path),
        )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == f"OK: {out_path}\n"
    assert out_path.read_text(encoding="utf-8") == "итоговый текст"


def test_restore_existing_out_without_overwrite_maps_to_code_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Существующий --out без --overwrite → код 3, файл не изменён."""
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)
    route_path = tmp_path / "route.json"
    out_path = tmp_path / "restored.txt"
    out_path.write_text("исходное содержимое", encoding="utf-8")
    result = _empty_restore_result("новый текст")

    with patch("privacy_gateway.restore.restore_text", return_value=result):
        code = _run(
            "restore",
            str(reply),
            "--route",
            str(route_path),
            "--out",
            str(out_path),
        )

    captured = capsys.readouterr()
    assert code == 3
    assert captured.err == (
        "Восстановлено: 0/0 токенов\n"
        f"Ошибка: Файл уже существует: {out_path}. "
        "Используйте флаг --overwrite для перезаписи.\n"
    )
    assert captured.out == ""
    assert out_path.read_text(encoding="utf-8") == "исходное содержимое"
    assert "новый текст" not in captured.out
    assert "новый текст" not in captured.err


def test_restore_write_error_maps_to_code_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ConfigurationError из write_restored даёт код 1, а не 3 — см. #28."""
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)
    route_path = tmp_path / "route.json"
    out_path = tmp_path / "restored.txt"
    result = _empty_restore_result("секретный результат")

    with patch("privacy_gateway.restore.restore_text", return_value=result):
        with patch(
            "privacy_gateway.restore.write_restored",
            side_effect=ConfigurationError("нет доступа к каталогу"),
        ):
            code = _run(
                "restore",
                str(reply),
                "--route",
                str(route_path),
                "--out",
                str(out_path),
            )

    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == (
        "Восстановлено: 0/0 токенов\n"
        "Ошибка записи: нет доступа к каталогу\n"
    )
    assert captured.out == ""
    assert "секретный результат" not in captured.err


def test_restore_input_error_maps_to_code_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Недоступный входной файл → код 3 и точная строка stderr."""
    missing = tmp_path / "missing.txt"
    route_path = tmp_path / "route.json"

    code = _run("restore", str(missing), "--route", str(route_path))

    captured = capsys.readouterr()
    assert code == 3
    assert captured.err == (
        "Ошибка чтения ответа LLM: Cannot access file: 'missing.txt'.\n"
    )
    assert captured.out == ""


def test_restore_configuration_error_maps_to_code_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ConfigurationError из restore_text → код 3."""
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)
    route_path = tmp_path / "route.json"

    with patch(
        "privacy_gateway.restore.restore_text",
        side_effect=ConfigurationError("route.json повреждён"),
    ):
        code = _run("restore", str(reply), "--route", str(route_path))

    captured = capsys.readouterr()
    assert code == 3
    assert captured.err == "Ошибка конфигурации: route.json повреждён\n"
    assert captured.out == ""


def test_restore_restore_error_maps_to_code_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """RestoreError из restore_text → код 3."""
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)
    route_path = tmp_path / "route.json"

    with patch(
        "privacy_gateway.restore.restore_text",
        side_effect=RestoreError("route.json не найден"),
    ):
        code = _run("restore", str(reply), "--route", str(route_path))

    captured = capsys.readouterr()
    assert code == 3
    assert captured.err == "Ошибка восстановления: route.json не найден\n"
    assert captured.out == ""


def test_restore_keystore_error_maps_to_code_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """KeystoreError из restore_text → код 4, ключ не выводится."""
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)
    route_path = tmp_path / "route.json"

    with patch(
        "privacy_gateway.restore.restore_text",
        side_effect=KeystoreError("ключ не найден"),
    ):
        code = _run("restore", str(reply), "--route", str(route_path))

    captured = capsys.readouterr()
    assert code == 4
    assert captured.err == "Ошибка keystore: ключ не найден\n"
    assert not _KEY_MATERIAL_RE.search(captured.err)


def test_restore_strict_error_maps_to_code_5(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """RestoreStrictError из restore_text → код 5 (ADR-21)."""
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)
    route_path = tmp_path / "route.json"

    with patch(
        "privacy_gateway.restore.restore_text",
        side_effect=RestoreStrictError("неизвестный токен"),
    ):
        code = _run("restore", str(reply), "--route", str(route_path))

    captured = capsys.readouterr()
    assert code == 5
    assert captured.err == "Строгий отказ по токенам: неизвестный токен\n"
    assert captured.out == ""


# ---------------------------------------------------------------------------
# key rotate и `pgw key` без подкоманды
# ---------------------------------------------------------------------------


def test_key_rotate_success(
    fernet_key: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """key rotate: код 0, точный stdout, ключевой материал не выводится."""
    with patch("privacy_gateway.keystore.rotate_key", return_value=fernet_key):
        code = _run("key", "rotate")

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == (
        "Ротация выполнена. Новый ключ активен. "
        "Старый ключ сохранён для чтения манифестов, "
        "созданных до ротации.\n"
    )
    assert captured.err == ""
    assert not _KEY_MATERIAL_RE.search(captured.out)


def test_key_rotate_key_not_found_maps_to_code_4(
    capsys: pytest.CaptureFixture[str]
) -> None:
    """key rotate без ключа → код 4 и точная строка stderr."""
    with patch(
        "privacy_gateway.keystore.rotate_key",
        side_effect=KeyNotFoundError("активный ключ отсутствует"),
    ):
        code = _run("key", "rotate")

    captured = capsys.readouterr()
    assert code == 4
    assert captured.err == (
        "Ключ не найден. Запустите 'pgw key create' сначала. "
        "Детали: активный ключ отсутствует\n"
    )
    assert captured.out == ""


def test_key_rotate_keystore_error_maps_to_code_4(
    capsys: pytest.CaptureFixture[str]
) -> None:
    """key rotate при ошибке keystore → код 4 и точная строка stderr."""
    with patch(
        "privacy_gateway.keystore.rotate_key",
        side_effect=KeystoreError("небезопасный backend"),
    ):
        code = _run("key", "rotate")

    captured = capsys.readouterr()
    assert code == 4
    assert captured.err == "Ошибка keystore: небезопасный backend\n"
    assert captured.out == ""
    assert not _KEY_MATERIAL_RE.search(captured.err)


def test_key_without_subcommand_prints_help(
    capsys: pytest.CaptureFixture[str]
) -> None:
    """`pgw key` без подкоманды: help и код 0.

    В описании #25 ожидался код 1, однако фактически ветка вызывает
    ``parser.parse_args(["key", "--help"])``, и argparse завершает процесс
    кодом 0 до строки ``sys.exit(1)``. Тест фиксирует фактическое поведение;
    расхождение с #25 — предмет отдельного решения, здесь не исправляется.
    """
    code = _run("key")

    captured = capsys.readouterr()
    assert code == 0
    assert "key" in captured.out
    assert captured.err == ""


# ---------------------------------------------------------------------------
# argparse: код 2 совпадает с PENDING (#26) — фиксируем, не исправляем
# ---------------------------------------------------------------------------


def test_argparse_error_exits_with_code_2(tmp_path: Path) -> None:
    """restore без --route: argparse завершает кодом 2, как PENDING (#26).

    Семантика различается, но код совпадает. Здесь фиксируется только код
    argparse; отдельного значения PENDING этот тест не проверяет.
    """
    reply = _input_file(tmp_path, SYNTH_LLM_REPLY)

    code = _run("restore", str(reply))

    assert code == 2
