"""CLI для Privacy Gateway — каркас Этапа Э1."""

from __future__ import annotations

import argparse
import sys

_DESCRIPTION = "Privacy Gateway — локальное обезличивание текста для безопасной работы с LLM."
_E1_MESSAGE = "Команда недоступна на этапе Э1."


def _build_parser() -> argparse.ArgumentParser:
    """Собрать и вернуть парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        prog="pgw",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Этап Э1: только каркас.\n"
            "Команды 'prepare' и 'restore' будут реализованы в следующих этапах."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="КОМАНДА")

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="[будущая] Обнаружить и токенизировать чувствительные данные в тексте.",
        description=(
            "[Этап Э2+] Читает файл .txt или stdin, обнаруживает чувствительные сущности,\n"
            "заменяет их нейтральными токенами и выводит обезличенный prompt.\n"
            "Недоступно на этапе Э1."
        ),
    )
    prepare_parser.add_argument(
        "input",
        nargs="?",
        metavar="ФАЙЛ",
        help="Входной файл .txt (будущее использование).",
    )

    restore_parser = subparsers.add_parser(
        "restore",
        help="[будущая] Восстановить исходные значения из ответа модели.",
        description=(
            "[Этап Э2+] Принимает ответ модели с известными токенами и заменяет\n"
            "их исходными значениями из зашифрованной карты кейса.\n"
            "Недоступно на этапе Э1."
        ),
    )
    restore_parser.add_argument(
        "input",
        nargs="?",
        metavar="ФАЙЛ",
        help="Файл с ответом модели (будущее использование).",
    )

    return parser


def main() -> None:
    """Основная точка входа CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command in ("prepare", "restore"):
        print(_E1_MESSAGE, file=sys.stderr)
        sys.exit(1)

    if args.command is None:
        parser.print_help()
        sys.exit(0)
