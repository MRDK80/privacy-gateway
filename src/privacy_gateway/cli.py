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

Коды завершения (ADR-20, ADR-21, ADR-29):
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
from dataclasses import dataclass
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
# Ошибка записи результата restore даёт код 1, а не 3 (#28): поведение
# зафиксировано тестами и в рамках #18 не исправляется.
_RESTORE_WRITE_RULES: tuple[_ExceptionRule, ...] = (
    _ExceptionRule(FileExistsError, 3, "Ошибка"),
    _ExceptionRule(ConfigurationError, 1, "Ошибка записи"),
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pgw",
        description="Privacy Gateway — безопасная подготовка текста для LLM.",
    )
    sub = parser.add_subparsers(dest="command")

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
    key_sub = key_parser.add_subparsers(dest="key_command")

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
    """Первичный разбор argv: usage error argparse → код 3 (ADR-29, #26).

    argparse к этому моменту уже напечатал usage/error в stderr — текст
    не изменяется. help (код 0) и любые другие коды пробрасываются как есть.
    """
    try:
        return parser.parse_args()
    except SystemExit as exc:
        if exc.code == 2:
            raise SystemExit(3) from None
        raise

def main() -> None:
    """Точка входа CLI."""
    parser = _build_parser()
    args = _parse_args(parser)

    if args.command == "detect":
        sys.exit(_cmd_detect(args))
    elif args.command == "prepare":
        sys.exit(_cmd_prepare(args))
    elif args.command == "restore":
        sys.exit(_cmd_restore(args))
    elif args.command == "key":
        if not hasattr(args, "key_command") or args.key_command is None:
            # pgw key без подкоманды
            parser.parse_args(["key", "--help"])
            sys.exit(1)
        if args.key_command == "create":
            sys.exit(_cmd_key_create(args))
        elif args.key_command == "status":
            sys.exit(_cmd_key_status(args))
        elif args.key_command == "rotate":
            sys.exit(_cmd_key_rotate(args))
        else:
            parser.parse_args(["key", "--help"])
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)
