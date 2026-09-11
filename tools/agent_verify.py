#!/usr/bin/env python3
"""Deterministic Git and local quality verification for coding-agent tasks."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = "1.0"
PHASES = ("pre-commit", "pre-push", "post-push")
QUALITY_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("pytest", "-q"),
    ("ruff", "check", "."),
    ("mypy", "."),
    ("pre-commit", "run", "--all-files"),
)

EXIT_CODES = {
    "OK": 0,
    "USAGE_ERROR": 2,
    "GIT_ERROR": 10,
    "WRONG_BRANCH": 11,
    "DIRTY_WORKTREE": 12,
    "ANCESTRY_MISSING": 13,
    "DIFF_ERROR": 14,
    "GATE_FAILED": 15,
    "REMOTE_MISMATCH": 16,
    "INTERNAL_ERROR": 17,
}


@dataclass(frozen=True)
class Options:
    phase: str
    base: str
    head: str
    remote: str
    output_format: str
    log_dir: Path | None
    root: Path


@dataclass(frozen=True)
class Check:
    name: str
    command: list[str]
    status: str
    exit_code: int


@dataclass
class Result:
    schema_version: str
    status: str
    phase: str
    machine_code: str
    exit_code: int
    base: str
    head: str
    head_sha: str | None = None
    remote_sha: str | None = None
    changed_files: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    log_directory: str | None = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _run(root: Path, command: Sequence[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command), cwd=root, capture_output=True, text=True, check=False
        )
    except OSError as error:
        return CommandResult(127, "", type(error).__name__)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _git(root: Path, *args: str) -> CommandResult:
    return _run(root, ("git", *args))


def _failure(options: Options, machine_code: str) -> Result:
    return Result(
        schema_version=SCHEMA_VERSION,
        status="failed",
        phase=options.phase,
        machine_code=machine_code,
        exit_code=EXIT_CODES[machine_code],
        base=options.base,
        head=options.head,
        log_directory=str(options.log_dir) if options.log_dir else None,
    )


def _safe_branch_name(value: str) -> bool:
    if not value or value.startswith("-"):
        return False
    checked = _run(Path.cwd(), ("git", "check-ref-format", "--branch", value))
    return checked.returncode == 0


def _write_log(
    directory: Path,
    index: int,
    command: Sequence[str],
    run: CommandResult,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = "-".join(command[:2]).replace(os.sep, "_")
    target = directory / f"{index:02d}-{safe_name}.log"
    target.write_text(
        f"command: {' '.join(command)}\nexit_code: {run.returncode}\n"
        f"\n[stdout]\n{run.stdout}\n[stderr]\n{run.stderr}",
        encoding="utf-8",
    )


def verify(
    options: Options,
    *,
    quality_commands: Sequence[Sequence[str]] = QUALITY_COMMANDS,
) -> Result:
    """Run phase checks without performing network or Git write operations."""
    if options.phase not in PHASES:
        return _failure(options, "USAGE_ERROR")
    if options.log_dir is not None:
        try:
            options.log_dir.resolve().relative_to(options.root.resolve())
        except ValueError:
            pass
        else:
            return _failure(options, "USAGE_ERROR")
    if not _safe_branch_name(options.base) or not _safe_branch_name(options.head):
        return _failure(options, "WRONG_BRANCH")
    if not options.base.startswith("roadmap/") or options.head.startswith("roadmap/"):
        return _failure(options, "WRONG_BRANCH")

    top = _git(options.root, "rev-parse", "--show-toplevel")
    root_matches = Path(top.stdout.strip()).resolve() == options.root.resolve()
    if top.returncode != 0 or not root_matches:
        return _failure(options, "GIT_ERROR")

    current = _git(options.root, "branch", "--show-current")
    if current.returncode != 0:
        return _failure(options, "GIT_ERROR")
    if current.stdout.strip() != options.head:
        return _failure(options, "WRONG_BRANCH")

    base_sha = _git(options.root, "rev-parse", "--verify", f"{options.base}^{{commit}}")
    head_sha_run = _git(
        options.root, "rev-parse", "--verify", f"{options.head}^{{commit}}"
    )
    if base_sha.returncode != 0 or head_sha_run.returncode != 0:
        return _failure(options, "GIT_ERROR")
    head_sha = head_sha_run.stdout.strip()

    ancestry = _git(
        options.root, "merge-base", "--is-ancestor", options.base, options.head
    )
    if ancestry.returncode == 1:
        result = _failure(options, "ANCESTRY_MISSING")
        result.head_sha = head_sha
        return result
    if ancestry.returncode != 0:
        return _failure(options, "GIT_ERROR")

    status = _git(options.root, "status", "--porcelain=v1", "-z")
    if status.returncode != 0:
        return _failure(options, "GIT_ERROR")
    dirty = bool(status.stdout)
    if options.phase != "pre-commit" and dirty:
        result = _failure(options, "DIRTY_WORKTREE")
        result.head_sha = head_sha
        return result

    names = _git(
        options.root, "diff", "--name-only", "-z", f"{options.base}...{options.head}"
    )
    if names.returncode != 0:
        return _failure(options, "GIT_ERROR")
    changed_names = {name for name in names.stdout.split("\0") if name}
    if options.phase == "pre-commit":
        working = _git(options.root, "diff", "--name-only", "-z")
        staged = _git(options.root, "diff", "--cached", "--name-only", "-z")
        untracked = _git(
            options.root, "ls-files", "--others", "--exclude-standard", "-z"
        )
        if any(run.returncode != 0 for run in (working, staged, untracked)):
            return _failure(options, "GIT_ERROR")
        for run in (working, staged, untracked):
            changed_names.update(name for name in run.stdout.split("\0") if name)
    changed_files = sorted(changed_names)

    diff_commands = [["git", "diff", "--check", f"{options.base}...{options.head}"]]
    if options.phase == "pre-commit":
        diff_commands.append(["git", "diff", "--check"])
    checks: list[Check] = []
    for command in diff_commands:
        run = _run(options.root, command)
        checks.append(
            Check(
                "diff",
                command,
                "passed" if run.returncode == 0 else "failed",
                run.returncode,
            )
        )
        if run.returncode != 0:
            result = _failure(options, "DIFF_ERROR")
            result.head_sha = head_sha
            result.changed_files = changed_files
            result.checks = checks
            return result

    if options.phase in ("pre-push", "post-push"):
        quality_failed = False
        for index, command_values in enumerate(quality_commands, start=1):
            command = list(command_values)
            run = _run(options.root, command)
            checks.append(
                Check(
                    "quality_gate",
                    command,
                    "passed" if run.returncode == 0 else "failed",
                    run.returncode,
                )
            )
            quality_failed = quality_failed or run.returncode != 0
            if options.log_dir is not None:
                _write_log(options.log_dir, index, command, run)

        after = _git(options.root, "status", "--porcelain=v1", "-z")
        if after.returncode != 0:
            return _failure(options, "GIT_ERROR")
        if after.stdout:
            result = _failure(options, "DIRTY_WORKTREE")
            result.head_sha = head_sha
            result.changed_files = changed_files
            result.checks = checks
            return result
        if quality_failed:
            result = _failure(options, "GATE_FAILED")
            result.head_sha = head_sha
            result.changed_files = changed_files
            result.checks = checks
            return result

    remote_sha: str | None = None
    if options.phase == "post-push":
        remote_ref = f"refs/remotes/{options.remote}/{options.head}"
        remote = _git(options.root, "rev-parse", "--verify", f"{remote_ref}^{{commit}}")
        if remote.returncode == 0:
            remote_sha = remote.stdout.strip()
        if remote_sha != head_sha:
            result = _failure(options, "REMOTE_MISMATCH")
            result.head_sha = head_sha
            result.remote_sha = remote_sha
            result.changed_files = changed_files
            result.checks = checks
            return result

    return Result(
        schema_version=SCHEMA_VERSION,
        status="passed",
        phase=options.phase,
        machine_code="OK",
        exit_code=EXIT_CODES["OK"],
        base=options.base,
        head=options.head,
        head_sha=head_sha,
        remote_sha=remote_sha,
        changed_files=changed_files,
        checks=checks,
        log_directory=str(options.log_dir) if options.log_dir else None,
    )


def render_json(result: Result) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True)


def render_text(result: Result) -> str:
    summary = (
        f"{result.status.upper()} [{result.machine_code}] phase={result.phase} "
        f"base={result.base} head={result.head}"
    )
    details = [
        f"changed_files={len(result.changed_files)} checks={len(result.checks)}"
    ]
    details.extend(
        f"{check.status}: {' '.join(check.command)} (exit {check.exit_code})"
        for check in result.checks
    )
    return "\n".join((summary, *details))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=PHASES)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    options = Options(
        phase=args.phase,
        base=args.base,
        head=args.head,
        remote=args.remote,
        output_format=args.format,
        log_dir=args.log_dir,
        root=args.root,
    )
    try:
        result = verify(options)
    except Exception:  # defensive CLI boundary; never disclose inputs or env
        result = _failure(options, "INTERNAL_ERROR")
    rendered = (
        render_json(result)
        if options.output_format == "json"
        else render_text(result)
    )
    print(rendered)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
