"""Точка входа CLI Privacy Gateway — Этапы Э1–Э7.

Публичный контракт:
    python -m privacy_gateway <command> [options]

Команды:
    detect   Диагностика сущностей (без шифрования)
    prepare  Подготовка текста: детекция, токенизация, шифрование (Э6)
    restore  Восстановление исходного текста из ответа LLM (Э7)

Коды завершения (prepare / restore):
    0  OK — артефакты созданы / текст восстановлен
    2  PENDING — требуется ручное одобрение
    3  BLOCKED или ошибка входа/конфигурации
    4  Ошибка keystore (ключ не найден или небезопасный backend)
    1  Непредвиденная ошибка
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from privacy_gateway.input_parser import read_input
from privacy_gateway.keystore import KeystoreError, get_key
from privacy_gateway.models import (
    ConfigurationError,
    InputError,
    ProcessingStatus,
)
from privacy_gateway.pipeline import PipelineResult, prepare_pipeline
from privacy_gateway.routing import load_routing_config

_DEFAULT_ENTITIES_CONFIG = Path("config.example") / "entities.yaml"


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
        print(f"Ошибка чтения: {exc}", file=sys.stderr)
        return 3

    config_path = Path(args.config) if args.config else _DEFAULT_ENTITIES_CONFIG
    try:
        cfg = load_config(config_path)
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 3

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
        print(f"Ошибка чтения: {exc}", file=sys.stderr)
        return 3

    routing_path = Path(args.routing) if args.routing else None
    try:
        routing_cfg = load_routing_config(routing_path)
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 3

    if args.out:
        routing_cfg.output_dir = args.out
    out_dir = Path(routing_cfg.output_dir)
    overwrite: bool = args.overwrite or routing_cfg.overwrite

    entities_config_path = Path(args.config) if args.config else None

    try:
        key = get_key()
    except KeystoreError as exc:
        print(f"Ошибка keystore: {exc}", file=sys.stderr)
        return 4

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
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"Непредвиденная ошибка: {exc}", file=sys.stderr)
        return 1

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

    # Прочитать ответ LLM из файла или stdin
    try:
        input_text = read_input(args.file)
    except InputError as exc:
        print(f"Ошибка чтения ответа LLM: {exc}", file=sys.stderr)
        return 3

    route_path = Path(args.route)
    manifest_override = Path(args.manifest) if args.manifest else None
    strict = not args.lenient

    try:
        result = restore_text(
            llm_response=input_text.text,
            route_path=route_path,
            manifest_path_override=manifest_override,
            strict=strict,
        )
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 3
    except KeystoreError as exc:
        print(f"Ошибка keystore: {exc}", file=sys.stderr)
        return 4
    except RestoreError as exc:
        print(f"Ошибка восстановления: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"Непредвиденная ошибка: {exc}", file=sys.stderr)
        return 1

    # Вывести предупреждения (мягкий режим, пропавшие токены)
    for warning in result.warnings:
        print(f"ПРЕДУПРЕЖДЕНИЕ: {warning}", file=sys.stderr)

    # Отчёт о восстановлении (только счётчики, без значений)
    report_lines = [
        f"Восстановлено: {result.tokens_found_count}/{result.tokens_expected_count} токенов",
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

    # Записать или вывести результат
    assert result.restored_text is not None  # строгий режим уже поднял бы исключение
    if args.out:
        out_path = Path(args.out)
        try:
            write_restored(result.restored_text, out_path, overwrite=args.overwrite)
        except FileExistsError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 3
        except ConfigurationError as exc:
            print(f"Ошибка записи: {exc}", file=sys.stderr)
            return 1
        print(f"OK: {out_path}")
    else:
        print(result.restored_text, end="")

    return 0


def main() -> None:
    """Точка входа CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "detect":
        sys.exit(_cmd_detect(args))
    elif args.command == "prepare":
        sys.exit(_cmd_prepare(args))
    elif args.command == "restore":
        sys.exit(_cmd_restore(args))
    else:
        parser.print_help()
        sys.exit(1)
