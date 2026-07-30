"""CLI for Privacy Gateway — Stage E1 skeleton."""

from __future__ import annotations

import argparse
import sys

_DESCRIPTION = "Privacy Gateway — local text sanitiser for safe LLM interaction."
_E1_MESSAGE = "Command is not available in stage E1."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pgw",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Stage E1: skeleton only.\n"
            "Commands 'prepare' and 'restore' will be implemented in future stages."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="[future] Detect and tokenise sensitive data in a text.",
        description=(
            "[Stage E2+] Reads a .txt file or stdin, detects sensitive entities,\n"
            "replaces them with neutral tokens, and outputs a sanitised prompt.\n"
            "Not available in stage E1."
        ),
    )
    prepare_parser.add_argument(
        "input",
        nargs="?",
        metavar="FILE",
        help="Input .txt file (future use).",
    )

    restore_parser = subparsers.add_parser(
        "restore",
        help="[future] Restore original values from a model response.",
        description=(
            "[Stage E2+] Accepts a model response containing known tokens and\n"
            "replaces them with original values from the encrypted case map.\n"
            "Not available in stage E1."
        ),
    )
    restore_parser.add_argument(
        "input",
        nargs="?",
        metavar="FILE",
        help="Model response file (future use).",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command in ("prepare", "restore"):
        print(_E1_MESSAGE, file=sys.stderr)
        sys.exit(1)

    if args.command is None:
        parser.print_help()
        sys.exit(0)
