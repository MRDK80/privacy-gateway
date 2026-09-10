"""Точка входа CLI Privacy Gateway — Этапы Э1–Э8.

Публичный контракт:
    python -m privacy_gateway <command> [options]

Команды:
    detect      Диагностика сущностей (без шифрования)
    prepare     Подготовка текста: детекция, токенизация, шифрование (Э6)
    restore     Восстановление исходного текста из ответа LLM (Э7)
    key create  Создать Fernet-ключ в keyring (Э8)
    key status  Проверить наличие ключа (Э8)
    key rotate  Ротация ключа через MultiFernet (Э8)

Коды завершения (ADR-20, ADR-21, ADR-29, ADR-30):
    0  OK — артефакты созданы / текст восстановлен / ключ создан
    1  Непредвиденная ошибка
    2  PENDING — требуется ручное одобрение
    3  BLOCKED / ошибка входных данных / ошибка конфигурации / ошибка целостности
    4  Ошибка keystore (ключ не найден или небезопасный backend)
    5  Строгий отказ restore: LLM вернула неизвестный или искажённый токен

Адрес keystore не печатается. Ключ не выводится нигде в CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from privacy_gateway.input_parser import read_input
from privacy_gateway.keystore import KeystoreError, get_key
from privacy_gateway.models import (
    ConfigurationError,
    InputError,
    ProcessingStatus,
    RestoreStrictError,
)
from privacy_gateway.pipeline import PipelineResult, prepare_pipeline
from privacy_gateway.routing import load_routing_config

_DEFAULT_ENTITIES_CONFIG = Path("config.example") / "entities.yaml"
_JSON_SCHEMA_VERSION = "1.0"
_JSON_COMMANDS = frozenset(
    {"prepare", "restore", "key create", "key status", "key rotate"}
)


def _emit_json_success(command: str, result: dict[str, Any]) -> None:
    payload = {
        "schema_version": _JSON_SCHEMA_VERSION,
        "ok": True,
        "command": command,
        "result": result,
    }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _emit_json_error(
    command: str, code: str, error_type: str, message: str
) -> None:
    payload = {
        "schema_version": _JSON_SCHEMA_VERSION,
        "ok": False,
        "command": command,
        "error": {"code": code, "type": error_type, "message": message},
    }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _json_command_identifier(argv: list[str]) -> str:
    if not argv:
        return "cli"
    if argv[0] == "key":
        if len(argv) > 1:
            candidate = f"key {argv[1]}"
            return candidate if candidate in _JSON_COMMANDS else "key"
        return "key"
    return argv[0] if argv[0] in _JSON_COMMANDS else "cli"


def _json_error_details(
    command: str, exit_code: int, stderr: str
) -> tuple[str, str, str]:
    """Map stable human error categories to safe machine-facing details."""
    if exit_code == 1:
        return "internal_error", "internal_error", "Внутренняя ошибка."
    if exit_code == 2:
        return "pending", "processing_state", "Требуется ручное подтверждение."
    if exit_code == 5:
        return (
            "strict_restore_error",
            "restore_error",
            "Строгое восстановление отклонено.",
        )
    if command == "key create" and stderr.startswith("Ключ уже существует."):
        return "key_exists", "keystore_error", "Ключ уже существует."
    if command == "key status" and exit_code == 3:
        return "key_not_found", "keystore_error", "Ключ не найден."
    if stderr.startswith("Ключ не найден."):
        return "key_not_found", "keystore_error", "Ключ не найден."
    if exit_code == 4:
        return (
            "keystore_error",
            "keystore_error",
            "Операция с хранилищем ключей не выполнена.",
        )
    stderr_lines = stderr.splitlines()
    if any(line.startswith("Ошибка чтения") for line in stderr_lines):
        return "input_error", "input_error", "Не удалось прочитать входные данные."
    if any(line.startswith("Ошибка конфигурации") for line in stderr_lines):
        return (
            "configuration_error",
            "configuration_error",
            "Недопустимая конфигурация.",
        )
    if any(
        line.startswith("Ошибка записи") or line.startswith("Ошибка:")
        for line in stderr_lines
    ):
        return "output_error", "output_error", "Не удалось записать результат."
    if any(line.startswith("Ошибка восстановления") for line in stderr_lines):
        return "restore_error", "restore_error", "Восстановление не выполнено."
    if any(line.startswith("BLOCKED:") for line in stderr_lines):
        return "blocked", "processing_state", "Обработка заблокирована политикой."
    return "command_error", "command_error", "Команда не выполнена."



# ---------------------------------------------------------------------------
# Трансляция исключений в коды завершения (ADR-21)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ExceptionRule:
    """Тип исключения → префикс сообщения stderr и код завершения."""

    exception_type: type[Exception]
    exit_code: int
    prefix: str


def _report_exception(
    exc: Exception,
    rules: tuple[_ExceptionRule, ...],
    *,
    default_prefix: str = "Непредвиденная ошибка",
    default_code: int = 1,
) -> int:
    """Напечатать `{prefix}: {exc}` в stderr и вернуть код завершения.

    Правила проверяются по порядку через isinstance, поэтому подклассы
    должны идти раньше базовых классов. Неизвестное исключение получает
    код 1 (ADR-21).

    Ветки с фиксированным безопасным текстом без `str(exc)` сюда не
    передаются: они остаются явными в командных функциях.
    """
    for rule in rules:
        if isinstance(exc, rule.exception_type):
            print(f"{rule.prefix}: {exc}", file=sys.stderr)
            return rule.exit_code
    print(f"{default_prefix}: {exc}", file=sys.stderr)
    return default_code


# Правила для типов, доступных на уровне модуля. Правила для типов,
# импортируемых лениво (restore, keystore), строятся в самих командах.
_INPUT_RULES: tuple[_ExceptionRule, ...] = (
    _ExceptionRule(InputError, 3, "Ошибка чтения"),
)
_LLM_INPUT_RULES: tuple[_ExceptionRule, ...] = (
    _ExceptionRule(InputError, 3, "Ошибка чтения ответа LLM"),
)
_CONFIG_RULES: tuple[_ExceptionRule, ...] = (
    _ExceptionRule(ConfigurationError, 3, "Ошибка конфигурации"),
)
_KEYSTORE_RULES: tuple[_ExceptionRule, ...] = (
    _ExceptionRule(KeystoreError, 4, "Ошибка keystore"),
)
# Отказ записи результата restore — ожидаемый операционный отказ
# окружения и даёт код 3 (#28, ADR-31). Правила не объединяются:
# prefixes stderr различаются и сохраняются побайтово.
_RESTORE_WRITE_RULES: tuple[_ExceptionRule, ...] = (
    _ExceptionRule(FileExistsError, 3, "Ошибка"),
    _ExceptionRule(ConfigurationError, 3, "Ошибка записи"),
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pgw",
        description="Privacy Gateway — безопасная подготовка текста для LLM.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- detect ---
    detect_parser = sub.add_parser(
        "detect", help="Диагностика: найти сущности без шифрования."
    )
    detect_parser.add_argument(
        "file",
        metavar="ФАЙЛ",
        help="Путь к файлу или '-' для stdin.",
    )
    detect_parser.add_argument(
        "--encoding",
        metavar="ENC",
        default=None,
        help="Кодировка входного файла (utf-8, cp1251 и др.).",
    )
    detect_parser.add_argument(
        "--config",
        metavar="ENTITIES_CONFIG",
        default=None,
        help="Путь к entities.yaml детектора.",
    )

    # --- prepare ---
    prepare_parser = sub.add_parser(
        "prepare",
        help="Подготовка: токенизация, шифрование, запись артефактов.",
    )
    prepare_parser.add_argument(
        "file",
        metavar="ФАЙЛ",
        help="Путь к файлу или '-' для stdin.",
    )
    prepare_parser.add_argument(
        "--out",
        metavar="КАТАЛОГ",
        default=None,
        help="Каталог для артефактов (prompt.txt, route.json, manifest.json).",
    )
    prepare_parser.add_argument(
        "--routing",
        metavar="ROUTING_YAML",
        default=None,
        help="Путь к YAML-конфигу маршрутизации.",
    )
    prepare_parser.add_argument(
        "--config",
        metavar="ENTITIES_CONFIG",
        default=None,
        help=(
            "Путь к entities.yaml детектора. "
            "По умолчанию: config.example/entities.yaml."
        ),
    )
    prepare_parser.add_argument(
        "--encoding",
        metavar="ENC",
        default=None,
        help="Кодировка входного файла.",
    )
    prepare_parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Перезаписывать существующие артефакты.",
    )

    # --- restore ---
    restore_parser = sub.add_parser(
        "restore",
        help="Восстановление исходных значений из ответа LLM.",
    )
    restore_parser.add_argument(
        "file",
        metavar="ФАЙЛ",
        help="Путь к файлу с ответом LLM или '-' для stdin.",
    )
    restore_parser.add_argument(
        "--route",
        metavar="ROUTE_JSON",
        required=True,
        help="Путь к route.json от соответствующего запуска prepare.",
    )
    restore_parser.add_argument(
        "--out",
        metavar="ФАЙЛ",
        default=None,
        help="Путь к файлу результата. Если не указан — вывод в stdout.",
    )
    restore_parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Перезаписывать существующий файл результата.",
    )
    restore_parser.add_argument(
        "--manifest",
        metavar="MANIFEST_JSON",
        default=None,
        help=(
            "Явный путь к manifest.json. По умолчанию разрешается "
            "относительно каталога route.json (ADR-15)."
        ),
    )
    restore_parser.add_argument(
        "--lenient",
        action="store_true",
        default=False,
        help=(
            "Мягкий режим: неизвестные и искажённые токены дают предупреждение, "
            "а не ошибку. По умолчанию применяется строгий режим (ADR-16)."
        ),
    )

    # --- key ---
    key_parser = sub.add_parser(
        "key",
        help="Управление Fernet-ключом в keyring.",
    )
    key_sub = key_parser.add_subparsers(dest="key_command", required=True)

    # key create
    key_create = key_sub.add_parser(
        "create",
        help="Создать новый Fernet-ключ в keyring.",
    )
    key_create.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            "Перезаписать существующий ключ. "
            "ВНИМАНИЕ: все ранее созданные манифесты станут нечитаемыми. "
            "Для безопасной ротации используйте 'pgw key rotate'."
        ),
    )

    # key status
    key_sub.add_parser(
        "status",
        help="Проверить наличие ключа без вывода его значения.",
    )

    # key rotate
    key_sub.add_parser(
        "rotate",
        help=(
            "Ротация ключа: новый становится активным, "
            "старый остаётся для чтения манифестов."
        ),
    )

    return parser


def _entity_to_cli_dict(entity: Any) -> dict[str, Any]:
    """Сериализовать DetectedEntity в CLI-формат.

    CLI-контракт использует ключ "type" (а не "entity_type" из to_dict()).
    """
    d: dict[str, Any] = entity.to_dict()
    d["type"] = d.pop("entity_type")
    return d


def _cmd_detect(args: argparse.Namespace) -> int:
    """Обработка команды detect."""
    from privacy_gateway.detector import detect_entities, load_config

    try:
        input_text = (
            read_input(args.file, encoding=args.encoding)
            if args.encoding
            else read_input(args.file)
        )
    except InputError as exc:
        return _report_exception(exc, _INPUT_RULES)

    config_path = Path(args.config) if args.config else _DEFAULT_ENTITIES_CONFIG
    try:
        cfg = load_config(config_path)
    except ConfigurationError as exc:
        return _report_exception(exc, _CONFIG_RULES)

    entities = detect_entities(input_text.text, cfg)

    result: dict[str, Any] = {
        "source": input_text.source.value,
        "encoding": input_text.encoding,
        "entity_count": len(entities),
        "entities": [_entity_to_cli_dict(e) for e in entities],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_prepare(args: argparse.Namespace) -> int:
    """Обработка команды prepare."""
    try:
        input_text = (
            read_input(args.file, encoding=args.encoding)
            if args.encoding
            else read_input(args.file)
        )
    except InputError as exc:
        return _report_exception(exc, _INPUT_RULES)

    routing_path = Path(args.routing) if args.routing else None
    try:
        routing_cfg = load_routing_config(routing_path)
    except ConfigurationError as exc:
        return _report_exception(exc, _CONFIG_RULES)

    if args.out:
        routing_cfg.output_dir = args.out
    out_dir = Path(routing_cfg.output_dir)
    overwrite: bool = args.overwrite or routing_cfg.overwrite

    entities_config_path = Path(args.config) if args.config else None

    try:
        key = get_key()
    except KeystoreError as exc:
        return _report_exception(exc, _KEYSTORE_RULES)

    source_ref: str = input_text.path.name if input_text.path else "stdin"

    try:
        result: PipelineResult = prepare_pipeline(
            text=input_text.text,
            source_ref=source_ref,
            routing_cfg=routing_cfg,
            key=key,
            out_dir=out_dir,
            overwrite=overwrite,
            entities_config_path=entities_config_path,
        )
    except Exception as exc:  # noqa: BLE001
        return _report_exception(exc, _CONFIG_RULES)

    if result.status == ProcessingStatus.OK:
        print(
            f"OK: {result.prompt_path} / "
            f"{result.route_path} / {result.manifest_path}"
        )
        return 0
    elif result.status == ProcessingStatus.PENDING:
        print(f"PENDING: {result.message}", file=sys.stderr)
        return 2
    else:  # BLOCKED
        print(f"BLOCKED: {result.message}", file=sys.stderr)
        return 3


def _cmd_restore(args: argparse.Namespace) -> int:
    """Обработка команды restore."""
    from privacy_gateway.restore import RestoreError, restore_text, write_restored

    try:
        input_text = read_input(args.file)
    except InputError as exc:
        return _report_exception(exc, _LLM_INPUT_RULES)

    route_path = Path(args.route)
    manifest_override = Path(args.manifest) if args.manifest else None
    strict = not args.lenient

    # Порядок правил повторяет прежнюю лестницу except: строгий отказ по
    # токенам (5) проверяется раньше общей ошибки восстановления (3).
    restore_rules: tuple[_ExceptionRule, ...] = (
        _ExceptionRule(ConfigurationError, 3, "Ошибка конфигурации"),
        _ExceptionRule(KeystoreError, 4, "Ошибка keystore"),
        _ExceptionRule(RestoreStrictError, 5, "Строгий отказ по токенам"),
        _ExceptionRule(RestoreError, 3, "Ошибка восстановления"),
    )

    try:
        result = restore_text(
            llm_response=input_text.text,
            route_path=route_path,
            manifest_path_override=manifest_override,
            strict=strict,
        )
    except Exception as exc:  # noqa: BLE001
        return _report_exception(exc, restore_rules)

    for warning in result.warnings:
        print(f"ПРЕДУПРЕЖДЕНИЕ: {warning}", file=sys.stderr)

    report_lines = [
        (
            f"Восстановлено: {result.tokens_found_count}/"
            f"{result.tokens_expected_count} токенов"
        ),
    ]
    if result.tokens_missing_count:
        report_lines.append(
            f"  Не найдено в ответе: {result.tokens_missing_count} "
            f"({sorted(result.tokens_missing)})"
        )
    if result.tokens_unknown_count:
        report_lines.append(
            f"  Неизвестных токенов: {result.tokens_unknown_count} "
            f"({sorted(result.tokens_unknown)})"
        )
    if result.tokens_malformed_count:
        report_lines.append(
            f"  Искажённых кандидатов: {result.tokens_malformed_count} "
            f"({result.tokens_malformed})"
        )
    if result.tokens_duplicated:
        report_lines.append(
            f"  Дублированных токенов: {len(result.tokens_duplicated)} "
            f"({sorted(result.tokens_duplicated)})"
        )
    for line in report_lines:
        print(line, file=sys.stderr)

    assert result.restored_text is not None
    if args.out:
        out_path = Path(args.out)
        try:
            write_restored(result.restored_text, out_path, overwrite=args.overwrite)
        except (FileExistsError, ConfigurationError) as exc:
            return _report_exception(exc, _RESTORE_WRITE_RULES)
        print(f"OK: {out_path}")
    else:
        print(result.restored_text, end="")

    return 0


def _cmd_key_create(args: argparse.Namespace) -> int:
    """Обработка команды key create."""
    from privacy_gateway.keystore import KeyExistsError, KeystoreError, create_key

    rules: tuple[_ExceptionRule, ...] = (
        _ExceptionRule(KeystoreError, 4, "Ошибка keystore"),
    )

    try:
        create_key(force=args.force)
    except KeyExistsError:
        # Фиксированное безопасное сообщение без str(exc).
        print(
            "Ключ уже существует. Используйте --force для перезаписи "
            "(внимание: все существующие манифесты станут нечитаемыми). "
            "Для безопасной ротации используйте 'pgw key rotate'.",
            file=sys.stderr,
        )
        return 3
    except Exception as exc:  # noqa: BLE001
        return _report_exception(exc, rules)

    print("Ключ успешно создан.")
    return 0


def _cmd_key_status(_args: argparse.Namespace) -> int:
    """Обработка команды key status."""
    from privacy_gateway.keystore import KeystoreError, key_exists

    rules: tuple[_ExceptionRule, ...] = (
        _ExceptionRule(KeystoreError, 4, "Ошибка keystore"),
    )

    try:
        exists = key_exists()
    except Exception as exc:  # noqa: BLE001
        return _report_exception(exc, rules)

    if exists:
        print("Ключ присутствует в keyring.")
    else:
        print("Ключ не найден. Запустите 'pgw key create'.", file=sys.stderr)
        return 3
    return 0


def _cmd_key_rotate(_args: argparse.Namespace) -> int:
    """Обработка команды key rotate."""
    from privacy_gateway.keystore import KeyNotFoundError, KeystoreError, rotate_key

    # KeyNotFoundError — подкласс KeystoreError, поэтому идёт первым.
    rules: tuple[_ExceptionRule, ...] = (
        _ExceptionRule(
            KeyNotFoundError,
            4,
            "Ключ не найден. Запустите 'pgw key create' сначала. Детали",
        ),
        _ExceptionRule(KeystoreError, 4, "Ошибка keystore"),
    )

    try:
        rotate_key()
    except Exception as exc:  # noqa: BLE001
        return _report_exception(exc, rules)

    print(
        "Ротация выполнена. Новый ключ активен. "
        "Старый ключ сохранён для чтения манифестов, созданных до ротации."
    )
    return 0

def _parse_args(parser: argparse.ArgumentParser) -> argparse.Namespace:
    """Разобрать argv; JSON-флаг допустим только перед командой."""
    argv = sys.argv[1:]
    json_mode = bool(argv and argv[0] == "--json")
    parse_argv = argv[1:] if json_mode else argv
    try:
        if json_mode:
            with redirect_stderr(StringIO()):
                args = parser.parse_args(parse_argv)
        else:
            args = parser.parse_args(parse_argv)
    except SystemExit as exc:
        if exc.code == 2:
            if json_mode:
                _emit_json_error(
                    _json_command_identifier(parse_argv),
                    "invalid_arguments",
                    "usage_error",
                    "Недопустимые аргументы командной строки.",
                )
            raise SystemExit(3) from None
        raise
    args.json = json_mode
    return args


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "detect":
        return _cmd_detect(args)
    if args.command == "prepare":
        return _cmd_prepare(args)
    if args.command == "restore":
        return _cmd_restore(args)
    if args.command == "key":
        if args.key_command == "create":
            return _cmd_key_create(args)
        if args.key_command == "status":
            return _cmd_key_status(args)
        if args.key_command == "rotate":
            return _cmd_key_rotate(args)
        raise AssertionError(f"нераспознанная key-подкоманда: {args.key_command!r}")
    raise AssertionError(f"нераспознанная команда: {args.command!r}")


def _json_success_result(command: str, args: argparse.Namespace) -> dict[str, Any]:
    if command == "prepare":
        return {"status": "ok"}
    if command == "restore":
        return {"status": "ok", "output_path": str(args.out)}
    if command == "key create":
        return {"created": True}
    if command == "key status":
        return {"present": True}
    if command == "key rotate":
        return {"rotated": True}
    raise AssertionError(f"JSON не поддерживается для {command!r}")


def _run_json_command(args: argparse.Namespace) -> int:
    command = (
        f"key {args.key_command}" if args.command == "key" else str(args.command)
    )
    if command not in _JSON_COMMANDS:
        _emit_json_error(
            "cli",
            "unsupported_command",
            "usage_error",
            "Команда не поддерживает JSON-режим.",
        )
        return 3
    if command == "restore" and not args.out:
        _emit_json_error(
            command,
            "restore_output_required",
            "usage_error",
            "JSON-режим restore требует --out.",
        )
        return 3

    captured_stdout = StringIO()
    captured_stderr = StringIO()
    try:
        with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
            exit_code = _dispatch(args)
    except Exception:  # noqa: BLE001
        _emit_json_error(
            command,
            "internal_error",
            "internal_error",
            "Внутренняя ошибка.",
        )
        return 1

    if exit_code == 0:
        _emit_json_success(command, _json_success_result(command, args))
        return 0

    code, error_type, message = _json_error_details(
        command, exit_code, captured_stderr.getvalue()
    )
    _emit_json_error(command, code, error_type, message)
    return exit_code


def main() -> None:
    """Точка входа CLI с явным opt-in JSON-режимом."""
    parser = _build_parser()
    args = _parse_args(parser)
    if args.json:
        sys.exit(_run_json_command(args))
    sys.exit(_dispatch(args))
