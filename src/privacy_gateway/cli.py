"""CLI для Privacy Gateway — Этапы Э1 и Э2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from privacy_gateway.models import (
    ConfigurationError,
    EncodingError,
    InputError,
    UnsupportedInputError,
)

_DESCRIPTION = (
    "Privacy Gateway — локальное обезличивание текста "
    "для безопасной работы с LLM."
)
_E1_MESSAGE = "Команда недоступна на этапе Э1."

# Путь к конфигу по умолчанию (относительно CWD)
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
            "  prepare  [будущая] Токенизация текста (Э3+)\n"
            "  restore  [будущая] Восстановление оригиналов (Э3+)\n"
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
            "Результат команды НЕ является разрешением передавать исходный текст модели."
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

    # --- prepare (заглушка) ---
    prepare_parser = subparsers.add_parser(
        "prepare",
        help="[будущая] Обнаружить и токенизировать чувствительные данные в тексте.",
        description=(
            "[Этап Э3+] Недоступно в текущей версии."
        ),
    )
    prepare_parser.add_argument(
        "input",
        nargs="?",
        metavar="ФАЙЛ",
        help="Входной файл .txt (будущее использование).",
    )

    # --- restore (заглушка) ---
    restore_parser = subparsers.add_parser(
        "restore",
        help="[будущая] Восстановить исходные значения из ответа модели.",
        description=(
            "[Этап Э3+] Недоступно в текущей версии."
        ),
    )
    restore_parser.add_argument(
        "input",
        nargs="?",
        metavar="ФАЙЛ",
        help="Файл с ответом модели (будущее использование).",
    )

    return parser


def _cmd_detect(args: argparse.Namespace) -> int:
    """Выполнить команду detect. Возвращает код завершения."""
    from privacy_gateway.detector import DetectorConfig, detect_entities, load_config
    from privacy_gateway.input_parser import read_input

    # Загрузка конфига
    config_path = Path(args.config) if args.config else _DEFAULT_ENTITIES_CONFIG
    try:
        config: DetectorConfig = load_config(config_path)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 3

    # Чтение входа
    try:
        input_text = read_input(args.input, encoding=args.encoding)
    except (InputError, UnsupportedInputError, EncodingError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 3

    # Обнаружение
    try:
        entities = detect_entities(input_text.text, config)
    except Exception as exc:  # noqa: BLE001
        print("Unexpected error during detection.", file=sys.stderr)
        return 1

    # Безопасный JSON-вывод (без исходных значений)
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


def main() -> None:
    """Основная точка входа CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command in ("prepare", "restore"):
        print(_E1_MESSAGE, file=sys.stderr)
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

    if args.command is None:
        parser.print_help()
        sys.exit(0)
