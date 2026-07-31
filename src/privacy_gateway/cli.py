"""Точка входа CLI Privacy Gateway — Этапы Э1–Э6.

Публичный контракт:
    python -m privacy_gateway <command> [options]

Команды:
    detect   Диагностика сущностей (без шифрования)
    prepare  Подготовка текста: детекция, токенизация, шифрование (Э6)
    restore  Восстановление ответа (заглушка, реализация в Э7)

Коды завершения (prepare):
    0  OK — артефакты созданы
    2  PENDING — требуется ручное одобрение
    3  BLOCKED или ошибка входа/конфигурации
    4  Ошибка keystore (ключ не найден или небезопасный backend)
    1  Непредвиденная ошибка
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from privacy_gateway.input_parser import InputError, read_input
from privacy_gateway.keystore import KeystoreError, get_key
from privacy_gateway.models import (
    ConfigurationError,
    ProcessingStatus,
)
from privacy_gateway.pipeline import PipelineResult, prepare_pipeline
from privacy_gateway.routing import load_routing_config


def _build_parser():
    import argparse

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
    sub.add_parser(
        "restore",
        help="Восстановление исходных значений (заглушка, реализация в Э7).",
    )

    return parser


def _cmd_detect(args) -> int:
    """Обработька команды detect."""
    from privacy_gateway.detector import detect_entities, load_config

    try:
        input_text = read_input(
            args.file, encoding=args.encoding
        )
    except InputError as exc:
        print(f"Ошибка чтения: {exc}", file=sys.stderr)
        return 3

    config_path = Path(args.config) if args.config else None
    try:
        cfg = load_config(config_path)
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 3

    entities = detect_entities(input_text.text, cfg)

    result = {
        "source": input_text.source.value,
        "encoding": input_text.encoding,
        "entity_count": len(entities),
        "entities": [e.to_dict() for e in entities],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_prepare(args) -> int:
    """Обработька команды prepare."""
    # --- Чтение входа ---
    try:
        input_text = read_input(args.file, encoding=args.encoding)
    except InputError as exc:
        print(f"Ошибка чтения: {exc}", file=sys.stderr)
        return 3

    # --- Загрузка routing конфига ---
    routing_path = Path(args.routing) if args.routing else None
    try:
        routing_cfg = load_routing_config(routing_path)
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 3

    # --out переопределяет output_dir из конфига
    if args.out:
        routing_cfg.output_dir = args.out
    out_dir = Path(routing_cfg.output_dir)
    overwrite = args.overwrite or routing_cfg.overwrite

    entities_config_path = Path(args.config) if args.config else None

    # --- Получение ключа ---
    try:
        key = get_key()
    except KeystoreError as exc:
        print(f"Ошибка keystore: {exc}", file=sys.stderr)
        return 4

    source_ref = (
        input_text.path.name if input_text.path else "stdin"
    )

    # --- Запуск конвейера ---
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

    # --- Вывод результата ---
    if result.status == ProcessingStatus.OK:
        print(
            f"OK: {result.prompt_path} / {result.route_path} / {result.manifest_path}"
        )
        return 0
    elif result.status == ProcessingStatus.PENDING:
        print(f"PENDING: {result.message}", file=sys.stderr)
        return 2
    else:  # BLOCKED
        print(f"BLOCKED: {result.message}", file=sys.stderr)
        return 3


def main() -> None:
    """Точка входа CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "detect":
        sys.exit(_cmd_detect(args))
    elif args.command == "prepare":
        sys.exit(_cmd_prepare(args))
    elif args.command == "restore":
        print(
            "Команда restore недоступна на текущем этапе (Э7+).",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)
