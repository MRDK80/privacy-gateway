"""Approved English text and unchanged machine/privacy contracts for #122."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import run_cli

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/cli_english"


def _machine_only(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _machine_only(item)
            for key, item in value.items()
            if key not in {"summary", "description"}
        }
    if isinstance(value, list):
        return [_machine_only(item) for item in value]
    return value


def test_catalog_machine_fields_preserve_pretranslation_baseline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_cli("--describe") == 0
    actual = json.loads(capsys.readouterr().out)
    baseline = json.loads((FIXTURES / "machine_catalog.json").read_text("utf-8"))
    assert _machine_only(actual) == baseline
    assert not re.search("[А-Яа-яЁё]", json.dumps(actual, ensure_ascii=False))


@pytest.mark.parametrize(
    "args",
    [
        ("--help",),
        ("detect", "--help"),
        ("prepare", "--help"),
        ("restore", "--help"),
        ("key", "--help"),
        ("key", "create", "--help"),
        ("key", "status", "--help"),
        ("key", "rotate", "--help"),
    ],
)
def test_all_parser_help_is_english(
    args: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli(*args) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "usage:" in output.out
    assert not re.search("[А-Яа-яЁё]", output.out)


@pytest.mark.parametrize(
    ("args", "message", "code"),
    [
        (
            ("--json", "detect", "unused.txt"),
            "This command does not support JSON output.",
            "unsupported_command",
        ),
        (
            ("--json", "restore", "unused.txt", "--route", "unused.json"),
            "JSON restore requires --out.",
            "restore_output_required",
        ),
    ],
)
def test_english_json_errors_keep_machine_codes_and_no_input_reads(
    args: tuple[str, ...],
    message: str,
    code: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_cli(*args) == 3
    output = capsys.readouterr()
    body = json.loads(output.out)
    assert body["schema_version"] == "1.0"
    assert body["ok"] is False
    assert body["error"]["code"] == code
    assert body["error"]["message"] == message
    assert output.err == ""


def test_bootstrap_json_doctor_keeps_machine_contract() -> None:
    result = subprocess.run(
        [sys.executable, "-B", "-m", "privacy_gateway", "--json", "doctor"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "schema_version": "1.0",
        "ok": False,
        "command": "cli",
        "error": {
            "code": "unsupported_command",
            "type": "usage_error",
            "message": "This command does not support JSON output.",
        },
    }
