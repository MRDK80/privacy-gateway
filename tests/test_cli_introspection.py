"""Contract tests машиночитаемой интроспекции CLI (#151, ADR-151)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

import pytest

from privacy_gateway import cli


def _run_describe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.argv", ["pgw", *argv])
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    captured = capsys.readouterr()
    code = excinfo.value.code
    return (int(code) if isinstance(code, int) else 1, captured.out, captured.err)


def _catalog(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> dict[str, Any]:
    exit_code, out, err = _run_describe(monkeypatch, capsys, ["--describe"])
    assert exit_code == 0
    assert err == ""
    return cast(dict[str, Any], json.loads(out))


def _actions(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return cast(list[argparse.Action], getattr(parser, "_actions"))


def _walk_parser() -> dict[str, argparse.ArgumentParser]:
    """Независимый обход parser для проверки эквивалентности."""
    result: dict[str, argparse.ArgumentParser] = {}

    def visit(parser: argparse.ArgumentParser, prefix: str) -> None:
        found = False
        for action in _actions(parser):
            choices = getattr(action, "choices", None)
            if not isinstance(choices, dict):
                continue
            for name, sub in choices.items():
                if not isinstance(sub, argparse.ArgumentParser):
                    continue
                found = True
                visit(sub, f"{prefix} {name}".strip())
        if not found and prefix:
            result[prefix] = parser

    visit(cli._build_parser(), "")
    return result


def test_catalog_top_level_schema(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(monkeypatch, capsys)
    assert catalog["schema_version"] == "1.0"
    assert catalog["kind"] == "cli_catalog"
    assert catalog["program"] == "pgw"
    assert catalog["operational_json_schema_version"] == cli._JSON_SCHEMA_VERSION
    assert isinstance(catalog["commands"], list)
    assert catalog["side_effect_values"] == list(cli._SIDE_EFFECT_VALUES)


def test_catalog_is_byte_deterministic(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, first, _ = _run_describe(monkeypatch, capsys, ["--describe"])
    _, second, _ = _run_describe(monkeypatch, capsys, ["--describe"])
    assert first == second
    assert first.encode("utf-8") == second.encode("utf-8")


def test_catalog_commands_match_parser(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(monkeypatch, capsys)
    catalog_ids = [str(command["id"]) for command in catalog["commands"]]
    assert catalog_ids == sorted(catalog_ids)
    assert set(catalog_ids) == set(_walk_parser())
    assert set(catalog_ids) == {
        "detect",
        "key create",
        "key rotate",
        "key status",
        "prepare",
        "restore",
    }


def test_key_namespace_is_not_a_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(monkeypatch, capsys)
    ids = {str(command["id"]) for command in catalog["commands"]}
    assert "key" not in ids
    assert "key delete" not in ids
    for command in catalog["commands"]:
        if str(command["id"]).startswith("key "):
            assert command["path"][0] == "key"


def test_arguments_match_parser_actions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(monkeypatch, capsys)
    parsers = _walk_parser()
    for command in catalog["commands"]:
        parser = parsers[str(command["id"])]
        expected = [
            action
            for action in _actions(parser)
            if action.dest != "help"
            and not isinstance(getattr(action, "choices", None), dict)
        ]
        published = list(command["arguments"])
        assert [str(item["name"]) for item in published] == [
            str(action.dest) for action in expected
        ]
        for item, action in zip(published, expected, strict=True):
            assert item["option_strings"] == list(action.option_strings)
            positional = not action.option_strings
            assert item["required"] == (positional or bool(action.required))
            assert item["kind"] == ("positional" if positional else "option")
            assert item["cardinality"] == ("flag" if action.nargs == 0 else "one")
            if action.choices is None:
                assert item["choices"] is None
            else:
                assert item["choices"] == sorted(
                    str(choice) for choice in action.choices
                )
            assert item["summary"] == str(action.help or "")


def test_published_types_and_defaults_are_safe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(monkeypatch, capsys)
    for command in catalog["commands"]:
        for argument in command["arguments"]:
            assert argument["type"] in {"boolean", "enum", "integer", "path", "string"}
            default = argument["default"]
            assert default is None or isinstance(default, bool)


def test_unknown_parser_command_breaks_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cli._build_parser

    def patched() -> argparse.ArgumentParser:
        parser = original()
        for action in _actions(parser):
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                add_parser = getattr(action, "add_parser")
                add_parser("phantom", help="Искусственная команда.")
                break
        return parser

    monkeypatch.setattr(cli, "_build_parser", patched)
    with pytest.raises(KeyError):
        cli._build_catalog()


def test_new_parser_argument_breaks_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cli._build_parser

    def patched() -> argparse.ArgumentParser:
        parser = original()
        for action in _actions(parser):
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                detect = choices["detect"]
                detect.add_argument("--phantom", metavar="PHANTOM", default=None)
                break
        return parser

    monkeypatch.setattr(cli, "_build_parser", patched)
    with pytest.raises(AssertionError):
        cli._build_catalog()


def test_json_support_matches_registry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(monkeypatch, capsys)
    json_ids = {
        str(command["id"])
        for command in catalog["commands"]
        if "json" in command["output_formats"]
    }
    assert json_ids == set(cli._JSON_COMMANDS)
    by_id = {str(command["id"]): command for command in catalog["commands"]}
    assert by_id["detect"]["output_formats"] == ["human"]
    assert by_id["detect"]["machine_error_codes"] == []
    assert by_id["restore"]["json_mode_requires"] == ["--out"]
    assert by_id["prepare"]["json_mode_requires"] == []


def test_machine_error_codes_match_registry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(monkeypatch, capsys)
    assert catalog["machine_error_codes"] == sorted(cli._JSON_ERROR_CODES)
    union: set[str] = set(catalog["cli_level_machine_error_codes"])
    for command in catalog["commands"]:
        codes = {str(code) for code in command["machine_error_codes"]}
        assert codes <= set(cli._JSON_ERROR_CODES)
        assert sorted(codes) == list(command["machine_error_codes"])
        union |= codes
    assert union == set(cli._JSON_ERROR_CODES)


def test_exit_codes_match_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(monkeypatch, capsys)
    global_codes = [int(entry["code"]) for entry in catalog["exit_codes"]]
    assert global_codes == [0, 1, 2, 3, 4, 5]
    union: set[int] = set()
    for command in catalog["commands"]:
        codes = [int(code) for code in command["exit_codes"]]
        assert codes == sorted(codes)
        assert set(codes) <= set(global_codes)
        assert 0 in codes
        union |= set(codes)
    assert union == set(global_codes)


def test_side_effects_use_stable_enum(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(monkeypatch, capsys)
    allowed = set(cli._SIDE_EFFECT_VALUES)
    by_id = {str(command["id"]): command for command in catalog["commands"]}
    for command in catalog["commands"]:
        values = [str(value) for value in command["side_effects"]]
        assert values == sorted(values)
        assert set(values) <= allowed
    assert "writes_keyring" in by_id["key create"]["side_effects"]
    assert "writes_keyring" in by_id["key rotate"]["side_effects"]
    assert "writes_keyring" not in by_id["key status"]["side_effects"]
    assert "writes_artifacts" in by_id["prepare"]["side_effects"]
    assert "writes_output" in by_id["restore"]["side_effects"]


def test_describe_does_not_touch_keyring_or_handlers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    def explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("интроспекция не должна выполнять операции")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_dispatch", explode)
    monkeypatch.setattr(cli, "read_input", explode)
    monkeypatch.setattr(cli, "get_key", explode)
    monkeypatch.setattr(cli, "load_routing_config", explode)
    monkeypatch.setattr("privacy_gateway.keystore.key_exists", explode)
    monkeypatch.setattr("privacy_gateway.keystore.create_key", explode)
    monkeypatch.setattr("privacy_gateway.keystore.rotate_key", explode)
    monkeypatch.setattr("privacy_gateway.keystore.get_key", explode)
    exit_code, out, err = _run_describe(monkeypatch, capsys, ["--describe"])
    assert exit_code == 0
    assert err == ""
    assert json.loads(out)["kind"] == "cli_catalog"
    assert list(tmp_path.iterdir()) == []


def test_catalog_does_not_leak_local_or_internal_data(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = _run_describe(monkeypatch, capsys, ["--describe"])
    lowered = out.lower()
    for forbidden in (
        "/home/",
        "c:\\",
        "keyring.backends",
        "secretservice",
        "object at 0x",
        "<class",
        "<function",
        "_machineerror",
        "privacy_gateway.cli",
        str(Path.home()).lower(),
        str(Path.cwd()).lower(),
    ):
        assert forbidden not in lowered


def test_introspection_is_invisible_to_argparse() -> None:
    parser = cli._build_parser()
    options: set[str] = set()
    for action in _actions(parser):
        options.update(action.option_strings)
    assert "--describe" not in options
    assert "--json" not in options
    assert "describe" not in _walk_parser()


def test_malformed_introspection_invocations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code, out, err = _run_describe(
        monkeypatch, capsys, ["--describe", "extra"]
    )
    assert exit_code == 3
    assert out == ""
    assert "--describe" in err

    exit_code, out, err = _run_describe(monkeypatch, capsys, ["detect", "--describe"])
    assert exit_code == 3
    assert out == ""

    exit_code, out, err = _run_describe(monkeypatch, capsys, ["--json", "--describe"])
    assert exit_code == 3
    envelope = json.loads(out)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_arguments"
    assert err == ""
