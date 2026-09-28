#!/usr/bin/env python3
"""Prepare a local two-revision consumer demo without reading its real input."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, cast

SCHEMA_VERSION = "1.0"
SHA_RE = re.compile(r"[0-9a-f]{40}")
NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
PARAM_RE = re.compile(r"\{param:([a-z][a-z0-9_]{0,31})\}")
PLAN_KEYS = {
    "schema_version",
    "repository",
    "epic",
    "task",
    "baseline_ref",
    "baseline_sha",
    "roadmap_ref",
    "roadmap_sha",
    "consumer_visible",
    "not_applicable_reason",
    "scenario",
    "parameters",
    "old_argv",
    "new_argv",
}
PARAMETER_KEYS = {"name", "prompt", "secret"}
BUILTIN_PLACEHOLDERS = {"{python}", "{checkout}", "{input}"}


class DemoError(Exception):
    """Fail-closed demo error safe to expose as a machine code."""


@dataclass(frozen=True)
class Assessment:
    status: str
    baseline_sha: str
    roadmap_sha: str
    reason: str | None


def _invalid() -> DemoError:
    return DemoError("DEMO_PLAN_INVALID")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value


def _validate_parameters(value: Any) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise _invalid()
    checked: list[dict[str, object]] = []
    names: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != PARAMETER_KEYS:
            raise _invalid()
        name = raw["name"]
        prompt = raw["prompt"]
        secret = raw["secret"]
        if (
            not isinstance(name, str)
            or NAME_RE.fullmatch(name) is None
            or name in {"input", "python", "checkout"}
            or name in names
            or not _text(prompt)
            or "\n" in cast(str, prompt)
            or not isinstance(secret, bool)
        ):
            raise _invalid()
        names.add(name)
        checked.append({"name": name, "prompt": prompt, "secret": secret})
    return checked


def _validate_argv(value: Any, parameter_names: set[str]) -> list[str]:
    if not isinstance(value, list) or not value or value[0] != "{python}":
        raise _invalid()
    checked: list[str] = []
    used: set[str] = set()
    for raw in value:
        if not _text(raw) or "\n" in cast(str, raw):
            raise _invalid()
        token = cast(str, raw)
        if (Path(token).is_absolute() or PureWindowsPath(token).is_absolute()) and (
            token not in BUILTIN_PLACEHOLDERS
        ):
            raise _invalid()
        remaining = token
        for builtin in BUILTIN_PLACEHOLDERS:
            remaining = remaining.replace(builtin, "")
        for match in PARAM_RE.finditer(remaining):
            used.add(match.group(1))
        remaining = PARAM_RE.sub("", remaining)
        if "{" in remaining or "}" in remaining:
            raise _invalid()
        checked.append(token)
    if checked.count("{input}") != 1 or not used.issubset(parameter_names):
        raise _invalid()
    return checked


def validate_plan(value: Any) -> dict[str, Any]:
    """Validate a closed demo plan without inspecting any consumer input."""
    if not isinstance(value, Mapping) or set(value) != PLAN_KEYS:
        raise _invalid()
    item = dict(value)
    visible = item["consumer_visible"]
    if (
        item["schema_version"] != SCHEMA_VERSION
        or not _text(item["repository"])
        or cast(str, item["repository"]).count("/") != 1
        or not isinstance(item["epic"], int)
        or isinstance(item["epic"], bool)
        or item["epic"] <= 0
        or not isinstance(item["task"], int)
        or isinstance(item["task"], bool)
        or item["task"] <= 0
        or item["task"] == item["epic"]
        or item["baseline_ref"] != "main"
        or not isinstance(item["baseline_sha"], str)
        or SHA_RE.fullmatch(item["baseline_sha"]) is None
        or not isinstance(item["roadmap_ref"], str)
        or not item["roadmap_ref"].startswith(f"roadmap/{item['epic']}-")
        or not isinstance(item["roadmap_sha"], str)
        or SHA_RE.fullmatch(item["roadmap_sha"]) is None
        or not isinstance(visible, bool)
    ):
        raise _invalid()
    parameters = _validate_parameters(item["parameters"])
    if not visible:
        if (
            not _text(item["not_applicable_reason"])
            or item["scenario"] is not None
            or parameters
            or item["old_argv"] != []
            or item["new_argv"] != []
        ):
            raise _invalid()
        item["parameters"] = parameters
        return item
    if item["not_applicable_reason"] is not None or not _text(item["scenario"]):
        raise _invalid()
    names = {cast(str, parameter["name"]) for parameter in parameters}
    old_argv = _validate_argv(item["old_argv"], names)
    new_argv = _validate_argv(item["new_argv"], names)
    referenced = {
        match.group(1)
        for token in [*old_argv, *new_argv]
        for match in PARAM_RE.finditer(token)
    }
    if referenced != names:
        raise _invalid()
    item["parameters"] = parameters
    item["old_argv"] = old_argv
    item["new_argv"] = new_argv
    return item


def assess(value: Any) -> Assessment:
    """Return bounded readiness metadata, never a path, parameter, or payload."""
    item = validate_plan(value)
    visible = cast(bool, item["consumer_visible"])
    return Assessment(
        "CONSUMER_DEMO_READY" if visible else "DEMO_NOT_APPLICABLE",
        cast(str, item["baseline_sha"]),
        cast(str, item["roadmap_sha"]),
        cast(str | None, item["not_applicable_reason"]),
    )


def _runtime_source(item: Mapping[str, Any]) -> str:
    constants = {
        "BASELINE_REF": item["baseline_ref"],
        "BASELINE_SHA": item["baseline_sha"],
        "ROADMAP_REF": item["roadmap_ref"],
        "ROADMAP_SHA": item["roadmap_sha"],
        "PARAMETERS": item["parameters"],
        "OLD_ARGV": item["old_argv"],
        "NEW_ARGV": item["new_argv"],
    }
    encoded = json.dumps(constants, ensure_ascii=False, sort_keys=True)
    return f'''import getpass
import json
import pathlib
import subprocess
import sys
import tempfile
import venv

CONFIG = json.loads({encoded!r})


def ask(label, secret=False):
    value = (getpass.getpass(label + ": ") if secret else input(label + ": ")).strip()
    if not value:
        raise SystemExit("Обязательное значение не введено")
    return value


repository = pathlib.Path(
    ask("Путь к локальному Git repository")
).expanduser().resolve()
input_path = pathlib.Path(
    ask("Путь к реальному локальному входу")
).expanduser().resolve()
if not (repository / ".git").exists() or not input_path.is_file():
    raise SystemExit("Repository или локальный вход не найден")
parameters = {{
    item["name"]: ask(item["prompt"], item["secret"])
    for item in CONFIG["PARAMETERS"]
}}


def git(*args, capture=False):
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, text=True,
        capture_output=capture,
    )


def resolve(revision):
    completed = git(
        "rev-parse", "--verify", revision + "^{{commit}}", capture=True
    )
    return completed.stdout.strip()


if resolve(CONFIG["BASELINE_SHA"]) != CONFIG["BASELINE_SHA"]:
    raise SystemExit("Закреплённый baseline SHA недоступен")
if resolve(CONFIG["ROADMAP_SHA"]) != CONFIG["ROADMAP_SHA"]:
    raise SystemExit("Закреплённый roadmap SHA недоступен")
if resolve(CONFIG["ROADMAP_REF"]) != CONFIG["ROADMAP_SHA"]:
    raise SystemExit("Roadmap ref изменился; нужен новый проверенный plan")


def expand(argv, checkout, python):
    values = {{
        "{{python}}": str(python),
        "{{checkout}}": str(checkout),
        "{{input}}": str(input_path),
        **{{"{{param:" + key + "}}": value for key, value in parameters.items()}},
    }}
    return [replace_all(token, values) for token in argv]


def replace_all(token, values):
    for marker, value in values.items():
        token = token.replace(marker, value)
    return token


with tempfile.TemporaryDirectory(prefix="pgw-demo-") as temporary:
    root = pathlib.Path(temporary)
    old_checkout = root / "old"
    new_checkout = root / "new"
    attached = []
    try:
        for checkout, revision in (
            (old_checkout, CONFIG["BASELINE_SHA"]),
            (new_checkout, CONFIG["ROADMAP_SHA"]),
        ):
            git("worktree", "add", "--quiet", "--detach", str(checkout), revision)
            attached.append(checkout)
        old_env = root / "old-venv"
        new_env = root / "new-venv"
        venv.EnvBuilder(with_pip=True).create(old_env)
        venv.EnvBuilder(with_pip=True).create(new_env)
        executable = "python.exe" if sys.platform == "win32" else "python"
        scripts = "Scripts" if sys.platform == "win32" else "bin"
        old_python = old_env / scripts / executable
        new_python = new_env / scripts / executable
        print("СТАРЫЙ СПОСОБ", flush=True)
        subprocess.run(
            expand(CONFIG["OLD_ARGV"], old_checkout, old_python),
            cwd=old_checkout,
            check=True,
        )
        print("НОВЫЙ СПОСОБ", flush=True)
        subprocess.run(
            expand(CONFIG["NEW_ARGV"], new_checkout, new_python),
            cwd=new_checkout,
            check=True,
        )
    finally:
        for checkout in reversed(attached):
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(checkout)],
                cwd=repository,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )'''


def generate_command(value: Any) -> str:
    """Generate one copy-paste command; real values are requested only at runtime."""
    item = validate_plan(value)
    if not item["consumer_visible"]:
        raise DemoError("DEMO_NOT_APPLICABLE")
    return "python - << 'PYEOF'\n" + _runtime_source(item) + "\nPYEOF"


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DemoError("DEMO_PLAN_READ_FAILED") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("assess", "generate"):
        child = subparsers.add_parser(name)
        child.add_argument("plan", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        value = _load(args.plan)
        if args.command == "generate":
            print(generate_command(value))
        else:
            print(json.dumps(asdict(assess(value)), sort_keys=True))
        return 0
    except DemoError as error:
        print(json.dumps({"status": "ESCALATE", "machine_code": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
