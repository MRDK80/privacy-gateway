"""CLI для Privacy Gateway — Этапы Э1–Э6.

Точка входа: команда ``pgw``.

Реализованные команды:
    detect   Диагностическое обнаружение сущностей (Э2). Читает .txt-файл
             или stdin, применяет детектор и выводит JSON-метаданные.
             Исходные значения в вывод не включаются.
    prepare  Токенизация текста (Э6). Запускает полный конвейер:
             детектор → токенизатор → манифест → валидатор → артефакты.
             При OK создаёт prompt.txt и route.json.
             При BLOCKED/PENDING — ненулевой код возврата, файлы не создаются.

Заглушки (недоступны):
    restore  Восстановление оригиналов (Э7+) — не реализована.

Коды завершения:
    0  Успех (OK).
    1  Непредвиденная ошибка.
    2  PENDING — требуется ручное одобрение.
    3  BLOCKED или ошибка конфигурации/входных данных.
    4  Ошибка keystore (ключ не найден или небезопасный backend).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from privacy_gateway.models import (
    ConfigurationError,
    EncodingError,
    InputError,
    ProcessingStatus,
    UnsupportedInputError,
)

_DESCRIPTION = (
    "Privacy Gateway — локальное обезличивание текста "
    "для безопасной работы с LLM."
)

# Путь к конфигу детектора по умолчанию (относительно CWD)
_DEFAULT_ENTITIES_CONFIG = Path("config.example") / "entities.yaml"


def _build_parser() -> argparse.ArgumentParser:
    """Собрать и вернуть парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        prog="pgw",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Доступные команды:\n"
            "  detect   Диагностическое обнаружение сущностей (Э2)\n"
            "  prepare  Токенизация и подготовка текста (Э6)\n"
            "  restore  [будущая] Восстановление оригиналов (Э7+)\n"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="КОМАНДА")

    # --- detect ---
    detect_parser = subparsers.add_parser(
        "detect",
        help="Обнаружить потенциально чувствительные сущности (диагностика).",
        description=(
            "[Этап Э2] Читает .txt-файл или stdin, применяет regex и словарные\n"
            "детекторы и выводит JSON-метаданные обнаруженных сущностей.\n"
            "Исходный текст и значения сущностей в вывод НЕ включаются.\n"
            "Команда диагностическая и не гарантирует полноту обнаружения.\n"
            "Результат команды НЕ является разрешением\n"
            "передавать исходный текст модели."
        ),
    )
    detect_parser.add_argument(
        "input",
        metavar="ФАЙЛ",
        help="Входной .txt-файл или '-' для чтения из stdin.",
    )
    detect_parser.add_argument(
        "--encoding",
        default="utf-8",
        metavar="ENC",
        help="Кодировка входного файла (utf-8, cp1251). По умолчанию: utf-8.",
    )
    detect_parser.add_argument(
        "--config",
        default=None,
        metavar="CONFIG",
        help="Путь к entities.yaml. По умолчанию: config.example/entities.yaml.",
    )

    # --- prepare ---
    prepare_parser = subparsers.add_parser(
        "prepare",
        help="Обнаружить и токенизировать чувствительные данные в тексте.",
        description=(
            "[Этап Э6] Читает .txt-файл или stdin, применяет полный конвейер:\n"
            "  детектор → токенизатор → манифест → валидатор.\n"
            "При статусе OK создаёт в --out:\n"
            "  prompt.txt   — токенизированный текст без исходных значений\n"
            "  route.json   — метаданные запуска и счётчики токенов\n"
            "  manifest.json — зашифрованная карта token→value\n"
            "При BLOCKED или PENDING файлы НЕ создаются."
        ),
    )
    prepare_parser.add_argument(
        "input",
        metavar="ФАЙЛ",
        help="Входной .txt-файл или '-' для чтения из stdin.",
    )
    prepare_parser.add_argument(
        "--out",
        default=None,
        metavar="КАТАЛОГ",
        help=(
            "Каталог для артефактов (prompt.txt, route.json, manifest.json). "
            "По умолчанию: значение из routing.yaml или ./pgw_out."
        ),
    )
    prepare_parser.add_argument(
        "--routing",
        default=None,
        metavar="ROUTING_YAML",
        help="Путь к YAML-конфигу маршрутизации. По умолчанию: безопасные умолчания.",
    )
    prepare_parser.add_argument(
        "--config",
        default=None,
        metavar="ENTITIES_CONFIG",
        help="Путь к entities.yaml детектора. По умолчанию: config.example/entities.yaml.",
    )
    prepare_parser.add_argument(
        "--encoding",
        default="utf-8",
        metavar="ENC",
        help="Кодировка входного файла. По умолчанию: utf-8.",
    )
    prepare_parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Разрешить перезапись существующих prompt.txt / route.json.",
    )

    # --- restore (заглушка) ---
    restore_parser = subparsers.add_parser(
        "restore",
        help="[будущая] Восстановить исходные значения из ответа модели.",
        description="[Этап Э7+] Недоступно в текущей версии.",
    )
    restore_parser.add_argument(
        "input",
        nargs="?",
        metavar="ФАЙЛ",
        help="Файл с ответом модели (будущее использование).",
    )

    return parser


def _cmd_detect(args: argparse.Namespace) -> int:
    """Выполнить команду detect."""
    from privacy_gateway.detector import DetectorConfig, detect_entities, load_config
    from privacy_gateway.input_parser import read_input

    config_path = Path(args.config) if args.config else _DEFAULT_ENTITIES_CONFIG
    try:
        config: DetectorConfig = load_config(config_path)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 3

    try:
        input_text = read_input(args.input, encoding=args.encoding)
    except (InputError, UnsupportedInputError, EncodingError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 3

    try:
        entities = detect_entities(input_text.text, config)
    except Exception:  # noqa: BLE001
        print("Unexpected error during detection.", file=sys.stderr)
        return 1

    output = {
        "source": input_text.source.value,
        "encoding": input_text.encoding,
        "entity_count": len(entities),
        "entities": [
            {
                "type": (
                    e.secret_kind
                    if e.secret_kind
                    else e.entity_type.value
                ),
                "start": e.start,
                "end": e.end,
                "confidence": e.confidence.value,
                "source": e.source,
                "fingerprint": e.fingerprint,
            }
            for e in entities
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _cmd_prepare(args: argparse.Namespace) -> int:  # noqa: C901
    """Выполнить команду prepare.

    Порядок конвейера: ввод → детектор → токенизатор → манифест → валидатор → артефакты.

    Коды возврата:
        0  OK — артефакты созданы.
        2  PENDING — найдены аномалии, нужно ручное одобрение.
        3  BLOCKED или ошибка конфигурации/входа.
        4  Ошибка keystore.
        1  Непредвиденная ошибка.
    """
    from privacy_gateway.input_parser import read_input
    from privacy_gateway.keystore import KeyNotFoundError, KeystoreError, get_key
    from privacy_gateway.pipeline import prepare_pipeline
    from privacy_gateway.routing import load_routing_config

    # Загрузка конфига маршрутизации
    routing_path = Path(args.routing) if args.routing else None
    try:
        routing_cfg = load_routing_config(routing_path)
    except ConfigurationError as exc:
        print(f"Routing config error: {exc}", file=sys.stderr)
        return 3

    # --out перекрывает output_dir из routing.yaml
    if args.out:
        routing_cfg.output_dir = args.out

    # --overwrite перекрывает значение из routing.yaml
    if args.overwrite:
        routing_cfg.overwrite = True

    out_dir = Path(routing_cfg.output_dir)

    # Получение ключа из keyring
    try:
        key = get_key()
    except KeyNotFoundError as exc:
        print(
            f"Keystore error: {exc}\n"
            "Hint: run 'pgw key create' to generate and store a new key.",
            file=sys.stderr,
        )
        return 4
    except KeystoreError as exc:
        print(f"Keystore error: {exc}", file=sys.stderr)
        return 4

    # Чтение входного текста
    try:
        input_text = read_input(args.input, encoding=args.encoding)
    except (InputError, UnsupportedInputError, EncodingError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 3

    source_ref = (
        str(input_text.path) if input_text.path else "stdin"
    )

    # Конфиг детектора
    entities_config_path = Path(args.config) if args.config else None

    # Запуск конвейера
    try:
        result = prepare_pipeline(
            text=input_text.text,
            source_ref=source_ref,
            routing_cfg=routing_cfg,
            key=key,
            out_dir=out_dir,
            overwrite=routing_cfg.overwrite,
            entities_config_path=entities_config_path,
        )
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"IO error writing artifacts: {exc}", file=sys.stderr)
        return 1

    # Вывод результата
    if result.status == ProcessingStatus.OK:
        print(f"OK: artifacts written to {out_dir}")
        print(f"  prompt.txt   → {result.prompt_path}")
        print(f"  route.json   → {result.route_path}")
        print(f"  manifest.json → {result.manifest_path}")
        return 0

    if result.status == ProcessingStatus.BLOCKED:
        print(f"BLOCKED: {result.message}", file=sys.stderr)
        if result.findings_summary:
            print(
                "Findings (type/position only, no original values):",
                file=sys.stderr,
            )
            for f in result.findings_summary[:10]:  # не выводить более 10
                print(f"  {f}", file=sys.stderr)
        return 3

    if result.status == ProcessingStatus.PENDING:
        print(f"PENDING: {result.message}", file=sys.stderr)
        print(
            "Manual review required before sending to LLM.",
            file=sys.stderr,
        )
        return 2

    # Неожиданный статус — fail closed
    print(f"Unexpected pipeline status: {result.status}", file=sys.stderr)
    return 1


def main() -> None:
    """Основная точка входа CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "restore":
        print("Команда restore недоступна на текущем этапе (Э7+).", file=sys.stderr)
        sys.exit(1)

    if args.command == "detect":
        try:
            code = _cmd_detect(args)
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001
            print("Unexpected error.", file=sys.stderr)
            sys.exit(1)
        sys.exit(code)

    if args.command == "prepare":
        try:
            code = _cmd_prepare(args)
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001
            print("Unexpected error during prepare.", file=sys.stderr)
            sys.exit(1)
        sys.exit(code)

    if args.command is None:
        parser.print_help()
        sys.exit(0)
