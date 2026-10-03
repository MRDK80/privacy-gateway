#!/usr/bin/env python3
"""Build a read-only coordinator discovery snapshot and plan preview."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, cast
from urllib.parse import quote

SCHEMA_VERSION = "1.0"
EXIT_CODES = {
    "OK": 0,
    "USAGE_ERROR": 2,
    "GITHUB_ERROR": 20,
    "REPOSITORY_MISMATCH": 21,
    "AMBIGUOUS_EPIC": 22,
    "EPIC_INVALID": 23,
    "ROADMAP_MISSING": 24,
    "AMBIGUOUS_ROADMAP": 25,
    "STALE_ROADMAP": 26,
    "AMBIGUOUS_TASK": 27,
    "TASK_NOT_FOUND": 28,
    "TASK_CLOSED": 29,
    "PARENT_MISMATCH": 30,
    "TASK_BLOCKED": 31,
    "CONFLICTING_PR": 32,
    "HEAD_EXISTS": 33,
    "NO_READY_TASK": 34,
}


class GitHubError(Exception):
    """Controlled GitHub transport or response error."""


class GitHubClient(Protocol):
    def repository(self, repository: str) -> dict[str, Any]: ...

    def open_epics(self, repository: str) -> list[dict[str, Any]]: ...

    def issue(self, repository: str, number: int) -> dict[str, Any]: ...

    def open_pull_requests(self, repository: str) -> list[dict[str, Any]]: ...

    def branches(self, repository: str) -> dict[str, str]: ...

    def compare(self, repository: str, base: str, head: str) -> str: ...


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class GhClient:
    """Minimal read-only adapter around authenticated GitHub CLI commands."""

    def __init__(self, *, run: RunCommand = subprocess.run) -> None:
        self._run = run

    def _json(self, command: Sequence[str]) -> Any:
        try:
            completed = self._run(
                list(command), capture_output=True, text=True, check=False
            )
        except OSError as error:
            raise GitHubError from error
        if completed.returncode != 0:
            raise GitHubError
        try:
            return json.loads(completed.stdout)
        except (TypeError, ValueError) as error:
            raise GitHubError from error

    def repository(self, repository: str) -> dict[str, Any]:
        value = self._json(
            (
                "gh",
                "repo",
                "view",
                repository,
                "--json",
                "nameWithOwner,defaultBranchRef",
            )
        )
        if not isinstance(value, dict):
            raise GitHubError
        return cast(dict[str, Any], value)

    def open_epics(self, repository: str) -> list[dict[str, Any]]:
        value = self._json(
            (
                "gh",
                "issue",
                "list",
                "--repo",
                repository,
                "--state",
                "open",
                "--label",
                "EPIC",
                "--limit",
                "100",
                "--json",
                "number,title,state,url,labels,parent",
            )
        )
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise GitHubError
        return cast(list[dict[str, Any]], value)

    def issue(self, repository: str, number: int) -> dict[str, Any]:
        value = self._json(
            (
                "gh",
                "issue",
                "view",
                str(number),
                "--repo",
                repository,
                "--json",
                "number,title,state,url,labels,parent,subIssues,blockedBy",
            )
        )
        if not isinstance(value, dict):
            raise GitHubError
        return cast(dict[str, Any], value)

    def open_pull_requests(self, repository: str) -> list[dict[str, Any]]:
        value = self._json(
            (
                "gh",
                "pr",
                "list",
                "--repo",
                repository,
                "--state",
                "open",
                "--limit",
                "100",
                "--json",
                (
                    "number,url,baseRefName,headRefName,headRefOid,"
                    "closingIssuesReferences"
                ),
            )
        )
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise GitHubError
        return cast(list[dict[str, Any]], value)

    def branches(self, repository: str) -> dict[str, str]:
        value = self._json(
            (
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/branches?per_page=100",
            )
        )
        if not isinstance(value, list):
            raise GitHubError
        result: dict[str, str] = {}
        for page in value:
            if not isinstance(page, list):
                raise GitHubError
            for branch in page:
                if not isinstance(branch, dict):
                    raise GitHubError
                name = branch.get("name")
                commit = branch.get("commit")
                sha = commit.get("sha") if isinstance(commit, dict) else None
                if not isinstance(name, str) or not isinstance(sha, str):
                    raise GitHubError
                result[name] = sha
        return result

    def compare(self, repository: str, base: str, head: str) -> str:
        comparison = f"{quote(base, safe='')}...{quote(head, safe='')}"
        value = self._json(("gh", "api", f"repos/{repository}/compare/{comparison}"))
        if not isinstance(value, dict) or not isinstance(value.get("status"), str):
            raise GitHubError
        return cast(str, value["status"]).lower()


@dataclass(frozen=True)
class Options:
    repository: str
    epic: int | None
    task: int | None


@dataclass(frozen=True)
class IssueIdentity:
    number: int
    title: str
    state: str
    url: str


@dataclass
class Result:
    schema_version: str
    state: str
    machine_code: str
    exit_code: int
    repository: str
    epic: IssueIdentity | None = None
    task: IssueIdentity | None = None
    candidates: list[IssueIdentity] = field(default_factory=list)
    default_branch: str | None = None
    main_sha: str | None = None
    base_ref: str | None = None
    base_sha: str | None = None
    head_ref: str | None = None
    provenance: list[str] = field(default_factory=list)


def _result(options: Options, code: str, **values: Any) -> Result:
    if code == "OK":
        state = "PLAN_APPROVAL"
    elif code in {"AMBIGUOUS_EPIC", "AMBIGUOUS_TASK"}:
        state = "NEEDS_DECISION"
    else:
        state = "BLOCKED"
    return Result(
        schema_version=SCHEMA_VERSION,
        state=state,
        machine_code=code,
        exit_code=EXIT_CODES[code],
        repository=options.repository,
        **values,
    )


def _identity(value: dict[str, Any]) -> IssueIdentity:
    number = value.get("number")
    title = value.get("title")
    state = value.get("state")
    url = value.get("url")
    if (
        not isinstance(number, int)
        or not isinstance(title, str)
        or not isinstance(state, str)
        or not isinstance(url, str)
    ):
        raise GitHubError
    return IssueIdentity(number, title[:500], state.upper(), url[:1000])


def _labels(value: dict[str, Any]) -> set[str]:
    labels = value.get("labels")
    if not isinstance(labels, list):
        raise GitHubError
    result: set[str] = set()
    for label in labels:
        if not isinstance(label, dict) or not isinstance(label.get("name"), str):
            raise GitHubError
        result.add(cast(str, label["name"]))
    return result


def _nodes(value: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    connection = value.get(field_name)
    if not isinstance(connection, dict) or not isinstance(
        connection.get("nodes"), list
    ):
        raise GitHubError
    nodes = connection["nodes"]
    if not all(isinstance(node, dict) for node in nodes):
        raise GitHubError
    return cast(list[dict[str, Any]], nodes)


def _parent_number(value: dict[str, Any]) -> int | None:
    parent = value.get("parent")
    if parent is None:
        return None
    if not isinstance(parent, dict) or not isinstance(parent.get("number"), int):
        raise GitHubError
    return cast(int, parent["number"])


def _has_open_blockers(value: dict[str, Any]) -> bool:
    for node in _nodes(value, "blockedBy"):
        if not isinstance(node.get("number"), int) or not isinstance(
            node.get("url"), str
        ):
            raise GitHubError
        if str(node.get("state", "")).upper() == "OPEN":
            return True
    return False


def _linked_prs(prs: list[dict[str, Any]], task: int) -> list[dict[str, Any]]:
    linked: list[dict[str, Any]] = []
    for pr in prs:
        references = pr.get("closingIssuesReferences")
        if not isinstance(references, list):
            raise GitHubError
        for reference in references:
            if not isinstance(reference, dict):
                raise GitHubError
            if reference.get("number") == task:
                linked.append(pr)
                break
    return linked


def _branch_type(title: str) -> str:
    prefix = title.split(":", 1)[0].strip().lower()
    return (
        prefix
        if prefix in {"build", "chore", "ci", "docs", "feat", "fix", "test"}
        else "feat"
    )


def _head_ref(issue: IssueIdentity) -> str:
    raw = issue.title.split(":", 1)[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:48].rstrip("-")
    if not slug:
        slug = "task"
    return f"{_branch_type(issue.title)}/{issue.number}-{slug}"


def _select_epic(
    options: Options, client: GitHubClient
) -> tuple[dict[str, Any] | None, Result | None]:
    epics = client.open_epics(options.repository)
    candidates = [_identity(value) for value in epics]
    if options.epic is None:
        if len(epics) != 1:
            return None, _result(
                options,
                "AMBIGUOUS_EPIC",
                candidates=sorted(candidates, key=lambda i: i.number),
            )
        selected_number = candidates[0].number
    else:
        selected_number = options.epic
        if selected_number not in {item.number for item in candidates}:
            return None, _result(options, "EPIC_INVALID", candidates=candidates)
    epic = client.issue(options.repository, selected_number)
    identity = _identity(epic)
    if (
        identity.state != "OPEN"
        or "EPIC" not in _labels(epic)
        or _parent_number(epic) is not None
    ):
        return None, _result(options, "EPIC_INVALID", epic=identity)
    return epic, None


def discover(options: Options, client: GitHubClient) -> Result:
    """Return a deterministic, read-only plan preview or explicit stop state."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", options.repository):
        return _result(options, "USAGE_ERROR")
    if (options.epic is not None and options.epic < 1) or (
        options.task is not None and options.task < 1
    ):
        return _result(options, "USAGE_ERROR")
    try:
        repository = client.repository(options.repository)
        identity = repository.get("nameWithOwner")
        default = repository.get("defaultBranchRef")
        default_branch = default.get("name") if isinstance(default, dict) else None
        if (
            not isinstance(identity, str)
            or identity.lower() != options.repository.lower()
        ):
            return _result(options, "REPOSITORY_MISMATCH")
        if not isinstance(default_branch, str) or not default_branch:
            raise GitHubError

        epic, stopped = _select_epic(options, client)
        if stopped is not None:
            return stopped
        assert epic is not None
        epic_identity = _identity(epic)

        branches = client.branches(options.repository)
        main_sha = branches.get(default_branch)
        roadmap_prefix = f"roadmap/{epic_identity.number}-"
        roadmap_refs = sorted(
            name for name in branches if name.startswith(roadmap_prefix)
        )
        common = {
            "epic": epic_identity,
            "default_branch": default_branch,
            "main_sha": main_sha,
        }
        if not isinstance(main_sha, str):
            raise GitHubError
        if not roadmap_refs:
            return _result(options, "ROADMAP_MISSING", **common)
        if len(roadmap_refs) != 1:
            return _result(options, "AMBIGUOUS_ROADMAP", **common)
        base_ref = roadmap_refs[0]
        base_sha = branches[base_ref]
        ref_values = {"base_ref": base_ref, "base_sha": base_sha, **common}
        if client.compare(options.repository, default_branch, base_ref) not in {
            "ahead",
            "identical",
        }:
            return _result(options, "STALE_ROADMAP", **ref_values)

        child_nodes = _nodes(epic, "subIssues")
        child_numbers = {
            node.get("number")
            for node in child_nodes
            if isinstance(node.get("number"), int)
        }
        if len(child_numbers) != len(child_nodes):
            raise GitHubError
        if options.task is not None and options.task not in child_numbers:
            return _result(options, "TASK_NOT_FOUND", **ref_values)

        prs = client.open_pull_requests(options.repository)
        ready: list[tuple[dict[str, Any], IssueIdentity]] = []
        for number in sorted(cast(set[int], child_numbers)):
            task_value = client.issue(options.repository, number)
            task_identity = _identity(task_value)
            if _parent_number(task_value) != epic_identity.number:
                return _result(
                    options, "PARENT_MISMATCH", task=task_identity, **ref_values
                )
            if task_identity.state != "OPEN":
                if options.task == number:
                    return _result(
                        options, "TASK_CLOSED", task=task_identity, **ref_values
                    )
                continue
            if _has_open_blockers(task_value):
                if options.task == number:
                    return _result(
                        options, "TASK_BLOCKED", task=task_identity, **ref_values
                    )
                continue
            if _linked_prs(prs, number):
                if options.task == number:
                    return _result(
                        options, "CONFLICTING_PR", task=task_identity, **ref_values
                    )
                continue
            ready.append((task_value, task_identity))

        if options.task is not None:
            chosen = next(
                (item for item in ready if item[1].number == options.task), None
            )
            if chosen is None:
                return _result(options, "NO_READY_TASK", **ref_values)
        else:
            if not ready:
                return _result(options, "NO_READY_TASK", **ref_values)
            if len(ready) != 1:
                return _result(
                    options,
                    "AMBIGUOUS_TASK",
                    candidates=[item[1] for item in ready],
                    **ref_values,
                )
            chosen = ready[0]

        task_identity = chosen[1]
        head_ref = _head_ref(task_identity)
        if head_ref in branches or any(pr.get("headRefName") == head_ref for pr in prs):
            return _result(
                options,
                "HEAD_EXISTS",
                task=task_identity,
                head_ref=head_ref,
                **ref_values,
            )
        return _result(
            options,
            "OK",
            task=task_identity,
            head_ref=head_ref,
            provenance=[
                f"https://github.com/{options.repository}/issues/{epic_identity.number}",
                task_identity.url,
                f"refs/heads/{default_branch}@{main_sha}",
                f"refs/heads/{base_ref}@{base_sha}",
            ],
            **ref_values,
        )
    except (GitHubError, KeyError, TypeError, ValueError):
        return _result(options, "GITHUB_ERROR")


def render_json(result: Result) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="OWNER/REPO identity")
    parser.add_argument("--epic", type=int)
    parser.add_argument("--task", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = discover(
        Options(repository=args.repository, epic=args.epic, task=args.task), GhClient()
    )
    print(render_json(result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
