#!/usr/bin/env python3
"""Create one approved local task branch after a fail-closed preflight."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

SCHEMA_VERSION = "1.0"
EXIT_CODES = {
    "OK": 0,
    "USAGE_ERROR": 2,
    "APPROVAL_REQUIRED": 10,
    "GITHUB_ERROR": 20,
    "REPOSITORY_MISMATCH": 21,
    "ISSUE_MISMATCH": 22,
    "REMOTE_STATE_MISMATCH": 23,
    "GIT_ERROR": 30,
    "DIRTY_WORKTREE": 31,
    "WRONG_BASE": 32,
    "HEAD_CONFLICT": 33,
    "CREATE_FAILED": 34,
}


class GitHubError(Exception):
    """Controlled read-only GitHub transport or response error."""


class GitHubClient(Protocol):
    def repository(self, repository: str) -> dict[str, Any]: ...

    def issue(self, repository: str, number: int) -> dict[str, Any]: ...

    def branches(self, repository: str) -> dict[str, str]: ...

    def compare(self, repository: str, base: str, head: str) -> str: ...


@dataclass(frozen=True)
class Options:
    repository: str
    epic: int
    task: int
    default_ref: str
    default_sha: str
    base_ref: str
    base_sha: str
    head_ref: str
    remote: str
    root: Path
    approved: bool


@dataclass
class Result:
    schema_version: str
    status: str
    machine_code: str
    exit_code: int
    repository: str
    epic: int
    task: int
    default_ref: str
    default_sha: str
    base_ref: str
    base_sha: str
    head_ref: str
    before_sha: str | None = None
    after_sha: str | None = None
    current_branch_before: str | None = None
    current_branch_after: str | None = None
    upstream: str | None = None


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


def _gh_json(arguments: Sequence[str]) -> Any:
    run = _run(Path.cwd(), ("gh", *arguments))
    if run.returncode != 0:
        raise GitHubError("GitHub CLI request failed")
    try:
        return json.loads(run.stdout)
    except ValueError as error:
        raise GitHubError("GitHub CLI returned invalid JSON") from error


class GitHubCLI:
    """Read-only transport for facts required immediately before branch creation."""

    def __init__(self, *, read: Callable[[Sequence[str]], str] | None = None) -> None:
        self.read = read

    def repository(self, repository: str) -> dict[str, Any]:
        value = _gh_json(
            ("repo", "view", repository, "--json", "nameWithOwner,defaultBranchRef")
        )
        if not isinstance(value, dict):
            raise GitHubError("invalid repository response")
        return value

    def issue(self, repository: str, number: int) -> dict[str, Any]:
        value = _gh_json(
            (
                "issue",
                "view",
                str(number),
                "--repo",
                repository,
                "--json",
                "number,state,parent,subIssues",
            )
        )
        if not isinstance(value, dict):
            raise GitHubError("invalid issue response")
        return value

    def branches(self, repository: str) -> dict[str, str]:
        arguments = (
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/branches?per_page=100",
        )
        if self.read is None:
            values = _gh_json(arguments)
        else:
            try:
                values = json.loads(self.read(("gh", *arguments)))
            except ValueError as error:
                raise GitHubError("invalid branches response") from error
        if not isinstance(values, list):
            raise GitHubError("invalid branches response")
        result: dict[str, str] = {}
        for page in values:
            if not isinstance(page, list):
                raise GitHubError("invalid branches page")
            for branch in page:
                if not isinstance(branch, dict) or not isinstance(
                    branch.get("commit"), dict
                ):
                    raise GitHubError("invalid branch")
                name = branch.get("name")
                sha = branch["commit"].get("sha")
                if not isinstance(name, str) or not isinstance(sha, str):
                    raise GitHubError("invalid branch identity")
                result[name] = sha
        return result

    def compare(self, repository: str, base: str, head: str) -> str:
        comparison = f"{quote(base, safe='')}...{quote(head, safe='')}"
        value = _gh_json(("api", f"repos/{repository}/compare/{comparison}"))
        status = value.get("status") if isinstance(value, dict) else None
        if not isinstance(status, str):
            raise GitHubError("invalid compare response")
        return status


def _result(options: Options, code: str, *, status: str = "BLOCKED") -> Result:
    return Result(
        schema_version=SCHEMA_VERSION,
        status=status,
        machine_code=code,
        exit_code=EXIT_CODES[code],
        repository=options.repository,
        epic=options.epic,
        task=options.task,
        default_ref=options.default_ref,
        default_sha=options.default_sha,
        base_ref=options.base_ref,
        base_sha=options.base_sha,
        head_ref=options.head_ref,
    )


def _git(root: Path, *arguments: str) -> CommandResult:
    return _run(root, ("git", *arguments))


def _valid_ref(root: Path, value: str) -> bool:
    return (
        bool(value)
        and not value.startswith("-")
        and _git(root, "check-ref-format", "--branch", value).returncode == 0
    )


def _sha(root: Path, ref: str) -> str | None:
    run = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    return run.stdout.strip() if run.returncode == 0 else None


def _parent_number(issue: dict[str, Any]) -> int | None:
    parent = issue.get("parent")
    number = parent.get("number") if isinstance(parent, dict) else None
    return number if isinstance(number, int) else None


def _sub_issue_numbers(issue: dict[str, Any]) -> set[int]:
    connection = issue.get("subIssues")
    nodes = connection.get("nodes") if isinstance(connection, dict) else None
    if not isinstance(nodes, list):
        return set()
    return {
        number
        for item in nodes
        if isinstance(item, dict)
        for number in (item.get("number"),)
        if isinstance(number, int)
    }


def prepare_branch(
    options: Options,
    client: GitHubClient,
    *,
    before_create: Callable[[], None] | None = None,
) -> Result:
    """Revalidate an approved plan and create only its local branch ref."""
    if not options.approved:
        return _result(options, "APPROVAL_REQUIRED")
    if (
        options.epic < 1
        or options.task < 1
        or options.epic == options.task
        or not options.repository.count("/") == 1
        or not options.base_ref.startswith(f"roadmap/{options.epic}-")
        or options.head_ref.startswith("roadmap/")
        or not all(
            _valid_ref(options.root, value)
            for value in (options.default_ref, options.base_ref, options.head_ref)
        )
        or re.fullmatch(r"[0-9a-f]{40}", options.default_sha) is None
        or re.fullmatch(r"[0-9a-f]{40}", options.base_sha) is None
    ):
        return _result(options, "USAGE_ERROR")

    top = _git(options.root, "rev-parse", "--show-toplevel")
    if (
        top.returncode != 0
        or Path(top.stdout.strip()).resolve() != options.root.resolve()
    ):
        return _result(options, "GIT_ERROR")
    status = _git(options.root, "status", "--porcelain=v1", "-z")
    current = _git(options.root, "branch", "--show-current")
    if status.returncode != 0 or current.returncode != 0:
        return _result(options, "GIT_ERROR")
    if status.stdout:
        return _result(options, "DIRTY_WORKTREE")

    try:
        repository = client.repository(options.repository)
        epic = client.issue(options.repository, options.epic)
        task = client.issue(options.repository, options.task)
        branches = client.branches(options.repository)
        comparison = client.compare(
            options.repository, options.default_ref, options.base_ref
        )
    except GitHubError:
        return _result(options, "GITHUB_ERROR")

    default = repository.get("defaultBranchRef")
    if (
        repository.get("nameWithOwner") != options.repository
        or not isinstance(default, dict)
        or default.get("name") != options.default_ref
    ):
        return _result(options, "REPOSITORY_MISMATCH")
    if (
        epic.get("number") != options.epic
        or str(epic.get("state")).upper() != "OPEN"
        or task.get("number") != options.task
        or str(task.get("state")).upper() != "OPEN"
        or _parent_number(task) != options.epic
        or options.task not in _sub_issue_numbers(epic)
    ):
        return _result(options, "ISSUE_MISMATCH")
    if (
        branches.get(options.default_ref) != options.default_sha
        or branches.get(options.base_ref) != options.base_sha
        or options.head_ref in branches
        or comparison not in {"ahead", "identical"}
    ):
        return _result(options, "REMOTE_STATE_MISMATCH")

    remote_default = _sha(
        options.root, f"refs/remotes/{options.remote}/{options.default_ref}"
    )
    remote_base = _sha(
        options.root, f"refs/remotes/{options.remote}/{options.base_ref}"
    )
    local_base = _sha(options.root, options.base_ref)
    ancestry = _git(
        options.root,
        "merge-base",
        "--is-ancestor",
        options.default_sha,
        options.base_sha,
    )
    if (
        remote_default != options.default_sha
        or remote_base != options.base_sha
        or local_base != options.base_sha
        or ancestry.returncode != 0
    ):
        return _result(options, "WRONG_BASE")

    before = _sha(options.root, f"refs/heads/{options.head_ref}")
    if before is not None and before != options.base_sha:
        return _result(options, "HEAD_CONFLICT")

    result = _result(options, "OK", status="NO_OP" if before else "CREATED")
    result.before_sha = before
    result.current_branch_before = current.stdout.strip()
    if before is None:
        if before_create is not None:
            before_create()
        created = _git(
            options.root, "branch", "--no-track", options.head_ref, options.base_sha
        )
        if created.returncode != 0:
            return _result(options, "CREATE_FAILED")
    result.after_sha = _sha(options.root, f"refs/heads/{options.head_ref}")
    after_current = _git(options.root, "branch", "--show-current")
    result.current_branch_after = (
        after_current.stdout.strip() if after_current.returncode == 0 else None
    )
    upstream = _git(
        options.root,
        "for-each-ref",
        "--format=%(upstream:short)",
        f"refs/heads/{options.head_ref}",
    )
    result.upstream = upstream.stdout.strip() or None
    if (
        result.after_sha != options.base_sha
        or result.current_branch_after != result.current_branch_before
        or result.upstream is not None
    ):
        return _result(options, "CREATE_FAILED")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--epic", type=int, required=True)
    parser.add_argument("--task", type=int, required=True)
    parser.add_argument("--default-ref", required=True)
    parser.add_argument("--default-sha", required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--approve-plan", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    options = Options(
        repository=args.repository,
        epic=args.epic,
        task=args.task,
        default_ref=args.default_ref,
        default_sha=args.default_sha,
        base_ref=args.base_ref,
        base_sha=args.base_sha,
        head_ref=args.head_ref,
        remote=args.remote,
        root=args.root.resolve(),
        approved=args.approve_plan,
    )
    result = prepare_branch(options, GitHubCLI())
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
