"""Stdlib entry dispatch, including diagnostics of incomplete installations."""

from __future__ import annotations

import json
import sys


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "doctor":
        from privacy_gateway.doctor import main as doctor_main

        sys.exit(doctor_main(argv[1:]))
    if argv[:2] == ["--json", "doctor"]:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "ok": False,
                    "command": "cli",
                    "error": {
                        "code": "unsupported_command",
                        "type": "usage_error",
                        "message": "Команда не поддерживает JSON-режим.",
                    },
                },
                ensure_ascii=False,
            )
        )
        sys.exit(3)
    from privacy_gateway.cli import main as cli_main

    cli_main()
