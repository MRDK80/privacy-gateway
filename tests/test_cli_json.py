"""Contract tests for the explicit CLI JSON mode (issue #150)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

from privacy_gateway.keystore import KeyExistsError, KeyNotFoundError, KeystoreError
from privacy_gateway.models import (
    ConfigurationError,
    ProcessingStatus,
    RestoreStrictError,
)
from privacy_gateway.pipeline import PipelineResult
from privacy_gateway.restore import RestoreResult

_SECRET = "SYNTHETIC-PRIVATE-VALUE"  # pragma: allowlist secret
_KEY = b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # pragma: allowlist secret


def _run(*argv: str) -> int:
    from privacy_gateway.cli import main
    with patch.object(sys, "argv", ["pgw", *argv]), pytest.raises(SystemExit) as exc:
        main()
    assert isinstance(exc.value.code, int)
    return exc.value.code


def _payload(stdout: str) -> dict[str, Any]:
    assert stdout.endswith("\n")
    payload = json.loads(stdout)
    assert payload["schema_version"] == "1.0"
    assert isinstance(payload["ok"], bool)
    assert isinstance(payload["command"], str)
    assert ("result" in payload) != ("error" in payload)
    return cast(dict[str, Any], payload)


def _prepare(status: ProcessingStatus) -> PipelineResult:
    return PipelineResult(status=status, message=_SECRET)


def _prepare_context(result: PipelineResult) -> Any:
    return patch.multiple(
        "privacy_gateway.cli",
        read_input=lambda *_a, **_k: SimpleNamespace(text=_SECRET, path=Path("in.txt")),
        load_routing_config=lambda _p: SimpleNamespace(
            output_dir="out", overwrite=False
        ),
        get_key=lambda: _KEY,
        prepare_pipeline=lambda **_k: result,
    )


def test_prepare_success(capsys: pytest.CaptureFixture[str]) -> None:
    with _prepare_context(_prepare(ProcessingStatus.OK)):
        assert _run("--json", "prepare", "in.txt") == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert _payload(out.out) == {
        "schema_version": "1.0", "ok": True, "command": "prepare",
        "result": {"status": "ok"},
    }
    assert _SECRET not in out.out and _KEY.decode() not in out.out


@pytest.mark.parametrize("status,exit_code,code", [
    (ProcessingStatus.PENDING, 2, "pending"),
    (ProcessingStatus.BLOCKED, 3, "blocked"),
])
def test_prepare_states(status: ProcessingStatus, exit_code: int, code: str,
                        capsys: pytest.CaptureFixture[str]) -> None:
    with _prepare_context(_prepare(status)):
        assert _run("--json", "prepare", "in.txt") == exit_code
    out = capsys.readouterr()
    assert out.err == "" and _payload(out.out)["error"]["code"] == code
    assert _SECRET not in out.out


def test_prepare_configuration_error(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.multiple(
        "privacy_gateway.cli",
        read_input=lambda *_a, **_k: SimpleNamespace(text=_SECRET, path=Path("in.txt")),
        load_routing_config=lambda _p: (_ for _ in ()).throw(
            ConfigurationError(_SECRET)
        ),
    ):
        assert _run("--json", "prepare", "in.txt") == 3
    out = capsys.readouterr()
    assert out.err == ""
    assert _payload(out.out)["error"]["code"] == "configuration_error"
    assert _SECRET not in out.out


def test_restore_requires_out_before_reading(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("privacy_gateway.cli.read_input") as read_mock:
        assert _run("--json", "restore", "reply", "--route", "route") == 3
    out = capsys.readouterr()
    assert out.err == ""
    assert _payload(out.out)["error"]["code"] == "restore_output_required"
    read_mock.assert_not_called()


@pytest.mark.parametrize(
    "path", ["safe/result.txt", r"C:\safe\result.txt", "результат.txt"]
)
def test_restore_success_is_metadata_only(
    path: str, capsys: pytest.CaptureFixture[str]
) -> None:
    result = RestoreResult(restored_text=_SECRET, warnings=[_SECRET], strict=True)
    with (
        patch(
            "privacy_gateway.cli.read_input",
            return_value=SimpleNamespace(text="safe"),
        ),
        patch("privacy_gateway.restore.restore_text", return_value=result),
        patch("privacy_gateway.restore.write_restored") as write_mock,
    ):
        assert (
            _run("--json", "restore", "reply", "--route", "route", "--out", path)
            == 0
        )
    out = capsys.readouterr()
    assert out.err == ""
    assert _payload(out.out)["result"] == {"status": "ok", "output_path": path}
    assert _SECRET not in out.out
    write_mock.assert_called_once()


@pytest.mark.parametrize(
    "detail", ["unknown token " + _SECRET, "distorted token " + _SECRET]
)
def test_restore_strict_error_is_safe(
    detail: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch(
            "privacy_gateway.cli.read_input",
            return_value=SimpleNamespace(text=_SECRET),
        ),
        patch(
            "privacy_gateway.restore.restore_text",
            side_effect=RestoreStrictError(detail),
        ),
    ):
        assert (
            _run("--json", "restore", "reply", "--route", "route", "--out", "out")
            == 5
        )
    out = capsys.readouterr()
    assert out.err == ""
    assert _payload(out.out)["error"]["code"] == "strict_restore_error"
    assert _SECRET not in out.out and "Traceback" not in out.out


@pytest.mark.parametrize(
    "failure", [FileExistsError(_SECRET), ConfigurationError(_SECRET)]
)
def test_restore_write_error_is_safe(
    failure: Exception, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch(
            "privacy_gateway.cli.read_input",
            return_value=SimpleNamespace(text="safe"),
        ),
        patch(
            "privacy_gateway.restore.restore_text",
            return_value=RestoreResult(restored_text=_SECRET),
        ),
        patch("privacy_gateway.restore.write_restored", side_effect=failure),
    ):
        assert (
            _run("--json", "restore", "reply", "--route", "route", "--out", "out")
            == 3
        )
    out = capsys.readouterr()
    assert out.err == "" and _payload(out.out)["error"]["code"] == "output_error"
    assert _SECRET not in out.out


@pytest.mark.parametrize("argv,target,value,expected", [
    (("key", "create"), "privacy_gateway.keystore.create_key", _KEY, {"created": True}),
    (("key", "status"), "privacy_gateway.keystore.key_exists", True, {"present": True}),
    (("key", "rotate"), "privacy_gateway.keystore.rotate_key", _KEY, {"rotated": True}),
])
def test_key_success_has_no_material(
    argv: tuple[str, str], target: str, value: object,
    expected: dict[str, bool], capsys: pytest.CaptureFixture[str]
) -> None:
    with patch(target, return_value=value):
        assert _run("--json", *argv) == 0
    out = capsys.readouterr()
    assert out.err == "" and _payload(out.out)["result"] == expected
    assert _KEY.decode() not in out.out


@pytest.mark.parametrize("argv,target,value,side_effect,exit_code,code", [
    (
        ("key", "create"), "privacy_gateway.keystore.create_key", None,
        KeyExistsError(_SECRET), 3, "key_exists",
    ),
    (
        ("key", "status"), "privacy_gateway.keystore.key_exists", False,
        None, 3, "key_not_found",
    ),
    (
        ("key", "rotate"), "privacy_gateway.keystore.rotate_key", None,
        KeyNotFoundError(_SECRET), 4, "key_not_found",
    ),
    (
        ("key", "rotate"), "privacy_gateway.keystore.rotate_key", None,
        KeystoreError("verification " + _SECRET), 4, "keystore_error",
    ),
    (
        ("key", "rotate"), "privacy_gateway.keystore.rotate_key", None,
        KeystoreError("prune " + _SECRET), 4, "keystore_error",
    ),
])
def test_key_errors_are_stable_and_safe(
    argv: tuple[str, str], target: str, value: object, side_effect: Exception | None,
    exit_code: int, code: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch(target, return_value=value, side_effect=side_effect):
        assert _run("--json", *argv) == exit_code
    out = capsys.readouterr()
    assert out.err == "" and _payload(out.out)["error"]["code"] == code
    assert _SECRET not in out.out


@pytest.mark.parametrize("argv,command", [
    (("prepare",), "prepare"), (("restore", "reply"), "restore"),
    (("key",), "key"), (("prepare", "in", "--bad", _SECRET), "prepare"),
])
def test_argparse_errors_are_safe_json(
    argv: tuple[str, ...], command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run("--json", *argv) == 3
    out = capsys.readouterr()
    payload = _payload(out.out)
    assert out.err == "" and payload["command"] == command
    assert payload["error"]["code"] == "invalid_arguments" and _SECRET not in out.out


def test_json_flag_position_and_detect_scope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run("key", "status", "--json") == 3
    human = capsys.readouterr()
    assert human.out == "" and "unrecognized arguments: --json" in human.err
    assert _run("--json", "detect", "in") == 3
    machine = capsys.readouterr()
    assert machine.err == ""
    assert _payload(machine.out)["error"]["code"] == "unsupported_command"


def test_unexpected_error_is_safe(capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "privacy_gateway.keystore.key_exists", side_effect=RuntimeError(_SECRET)
    ):
        assert _run("--json", "key", "status") == 1
    out = capsys.readouterr()
    assert out.err == ""
    assert _payload(out.out)["error"]["code"] == "internal_error"
    assert _SECRET not in out.out and "Traceback" not in out.out


def test_contract_documentation_links() -> None:
    root = Path(__file__).resolve().parents[1]
    adr = root / "docs" / "ADR-150-cli-json-contract.md"
    assert adr.is_file() and "schema_version" in adr.read_text(encoding="utf-8")
    assert "docs/ADR-150-cli-json-contract.md" in (
        root / "AGENTS.md"
    ).read_text(encoding="utf-8")
    assert "ADR-150-cli-json-contract.md" in (
        root / "examples" / "06_cli_round_trip.md"
    ).read_text(encoding="utf-8")
