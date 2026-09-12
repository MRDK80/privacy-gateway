#!/usr/bin/env python3
"""Verify an agent pull request and its GitHub checks at an exact head SHA."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, cast

SCHEMA_VERSION = "1.0"
SUCCESS_STATES = {"SUCCESS"}
PENDING_STATES = {
    "EXPECTED",
    "IN_PROGRESS",
    "PENDING",
    "QUEUED",
    "REQUESTED",
    "WAITING",
}
PROTECTED_PATTERNS = (
    "AGENTS.md",
    ".agents/",
    ".codex/",
    ".github/CODEOWNERS",
    ".github/hooks/",
    ".github/workflows/",
    "docs/agent-contracts.md",
    "docs/schemas/",
    "tools/agent_orchestrate.py",
    "tools/agent_pr_checks.py",
)
PRIVATE_PATTERNS = (
    ".agent-private/",
    ".agent-logs/",
    "agent-memory/",
    "raw-retrospectives/",
    "controller-notes/",
    "pending-lessons/",
    "known-pitfalls/",
    "usage-metrics/",
    "config.local/",
)
EXIT_CODES = {
    "OK": 0,
    "USAGE_ERROR": 2,
    "GITHUB_ERROR": 20,
    "WRONG_DIRECTION": 21,
    "STALE_HEAD": 22,
    "PR_CHANGED": 23,
    "NO_CHECKS": 24,
    "CHECKS_PENDING": 25,
    "CHECKS_FAILED": 26,
    "HUMAN_REVIEW_REQUIRED": 27,
    "PRIVATE_ARTIFACT": 28,
}


class GitHubError(Exception):
    """A controlled GitHub query failure without raw command output."""


class GitHubClient(Protocol):
    def pull_request(self, number: int) -> dict[str, Any]: ...

    def pull_request_checks(self, number: int) -> list[dict[str, str]]: ...


class GhClient:
    """Minimal read-only adapter around the authenticated GitHub CLI."""

    @staticmethod
    def _json(command: Sequence[str]) -> Any:
        try:
            run = subprocess.run(
                list(command), capture_output=True, text=True, check=False
            )
        except OSError as error:
            raise GitHubError from error
        if run.returncode != 0:
            raise GitHubError
        try:
            return json.loads(run.stdout)
        except (TypeError, ValueError) as error:
            raise GitHubError from error

    def pull_request(self, number: int) -> dict[str, Any]:
        value = self._json(
            (
                "gh",
                "pr",
                "view",
                str(number),
                "--json",
                "number,url,baseRefName,headRefName,headRefOid,files,reviews",
            )
        )
        if not isinstance(value, dict):
            raise GitHubError
        return cast(dict[str, Any], value)

    def pull_request_checks(self, number: int) -> list[dict[str, str]]:
        value = self._json(
            ("gh", "pr", "checks", str(number), "--json", "name,state,link")
        )
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise GitHubError
        return cast(list[dict[str, str]], value)


@dataclass(frozen=True)
class Options:
    pr: int
    expected_base: str
    expected_head: str
    expected_sha: str
    timeout_seconds: float
    poll_seconds: float


@dataclass(frozen=True)
class CheckEvidence:
    name: str
    state: str
    link: str


@dataclass
class Result:
    schema_version: str
    status: str
    machine_code: str
    exit_code: int
    pr: int
    url: str | None = None
    base: str | None = None
    head: str | None = None
    head_sha: str | None = None
    protected_paths: list[str] = field(default_factory=list)
    checks: list[CheckEvidence] = field(default_factory=list)


def _result(options: Options, machine_code: str, **values: Any) -> Result:
    return Result(
        schema_version=SCHEMA_VERSION,
        status="green" if machine_code == "OK" else "failed",
        machine_code=machine_code,
        exit_code=EXIT_CODES[machine_code],
        pr=options.pr,
        **values,
    )


def _matches(path: str, patterns: Sequence[str]) -> bool:
    return any(path == pattern or path.startswith(pattern) for pattern in patterns)


def _direction_allowed(base: str, head: str) -> bool:
    task = base.startswith("roadmap/") and not head.startswith("roadmap/")
    roadmap = base == "main" and head.startswith("roadmap/")
    return task or roadmap


def _paths(pr: dict[str, Any]) -> list[str]:
    files = pr.get("files")
    if not isinstance(files, list):
        raise GitHubError
    paths: list[str] = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise GitHubError
        paths.append(item["path"])
    return paths


def _approved(pr: dict[str, Any]) -> bool:
    reviews = pr.get("reviews")
    if not isinstance(reviews, list):
        raise GitHubError
    latest: dict[str, str] = {}
    for review in reviews:
        if not isinstance(review, dict):
            raise GitHubError
        author = review.get("author")
        state = review.get("state")
        if (
            isinstance(author, dict)
            and isinstance(author.get("login"), str)
            and isinstance(state, str)
        ):
            latest[author["login"]] = state.upper()
    return "APPROVED" in latest.values()


def _identity(pr: dict[str, Any]) -> tuple[str, str, str, str]:
    values = tuple(
        pr.get(key) for key in ("url", "baseRefName", "headRefName", "headRefOid")
    )
    if not all(isinstance(value, str) and value for value in values):
        raise GitHubError
    return cast(tuple[str, str, str, str], values)


def verify(
    options: Options,
    client: GitHubClient,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Result:
    """Wait for checks and prove their result belongs to the expected PR SHA."""
    if options.pr < 1 or options.timeout_seconds < 0 or options.poll_seconds < 0:
        return _result(options, "USAGE_ERROR")
    try:
        initial = client.pull_request(options.pr)
        url, base, head, sha = _identity(initial)
        common = {"url": url, "base": base, "head": head, "head_sha": sha}
        if (
            base != options.expected_base
            or head != options.expected_head
            or not _direction_allowed(base, head)
        ):
            return _result(options, "WRONG_DIRECTION", **common)
        if sha != options.expected_sha:
            return _result(options, "STALE_HEAD", **common)

        paths = _paths(initial)
        protected = sorted(path for path in paths if _matches(path, PROTECTED_PATTERNS))
        if any(_matches(path, PRIVATE_PATTERNS) for path in paths):
            return _result(
                options, "PRIVATE_ARTIFACT", protected_paths=protected, **common
            )
        if protected and not _approved(initial):
            return _result(
                options, "HUMAN_REVIEW_REQUIRED", protected_paths=protected, **common
            )

        started = monotonic()
        evidence: list[CheckEvidence] = []
        while True:
            raw_checks = client.pull_request_checks(options.pr)
            evidence = [
                CheckEvidence(
                    name=str(item.get("name", ""))[:200],
                    state=str(item.get("state", "UNKNOWN")).upper()[:40],
                    link=str(item.get("link", ""))[:1000],
                )
                for item in raw_checks
            ]
            if not evidence:
                return _result(
                    options,
                    "NO_CHECKS",
                    protected_paths=protected,
                    checks=evidence,
                    **common,
                )
            if any(
                item.state not in SUCCESS_STATES | PENDING_STATES for item in evidence
            ):
                return _result(
                    options,
                    "CHECKS_FAILED",
                    protected_paths=protected,
                    checks=evidence,
                    **common,
                )
            if all(item.state in SUCCESS_STATES for item in evidence):
                break
            if monotonic() - started >= options.timeout_seconds:
                return _result(
                    options,
                    "CHECKS_PENDING",
                    protected_paths=protected,
                    checks=evidence,
                    **common,
                )
            sleep(options.poll_seconds)

        final = client.pull_request(options.pr)
        if _identity(final) != (url, base, head, sha) or _paths(final) != paths:
            return _result(
                options,
                "PR_CHANGED",
                protected_paths=protected,
                checks=evidence,
                **common,
            )
        if protected and not _approved(final):
            return _result(
                options,
                "HUMAN_REVIEW_REQUIRED",
                protected_paths=protected,
                checks=evidence,
                **common,
            )
        return _result(
            options, "OK", protected_paths=protected, checks=evidence, **common
        )
    except GitHubError:
        return _result(options, "GITHUB_ERROR")


def render_json(result: Result) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--expected-base", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=20)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = verify(
        Options(
            pr=args.pr,
            expected_base=args.expected_base,
            expected_head=args.expected_head,
            expected_sha=args.expected_sha,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        ),
        GhClient(),
    )
    print(render_json(result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
