#!/usr/bin/env python3
"""Assess task PR and post-merge delivery evidence without GitHub writes."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from tools import agent_gate

SCHEMA_VERSION = "1.0"
SHA_RE = re.compile(r"[0-9a-f]{40}")
REQUIRED_CHECKS = frozenset(
    {
        "test (ubuntu-latest, 3.11)",
        "test (ubuntu-latest, 3.12)",
        "test (windows-latest, 3.11)",
        "test (windows-latest, 3.12)",
        "pre-commit",
    }
)
SUCCESS = "SUCCESS"
EXIT_CODES = {
    "OK": 0,
    "USAGE_ERROR": 2,
    "GITHUB_ERROR": 20,
    "WRONG_DIRECTION": 21,
    "STALE_IDENTITY": 22,
    "SCOPE_ESCAPE": 23,
    "LOCAL_GATE_INVALID": 24,
    "CI_MISSING": 25,
    "CI_PENDING": 26,
    "CI_FAILED": 27,
    "MERGE_UNCONFIRMED": 28,
}


class GitHubError(Exception):
    """A controlled GitHub read failure."""


class GitHubClient(Protocol):
    def pull_request(self, repository: str, number: int) -> Mapping[str, Any]: ...

    def pr_checks(
        self, repository: str, number: int
    ) -> Sequence[Mapping[str, Any]]: ...

    def commit_checks(
        self, repository: str, sha: str
    ) -> Sequence[Mapping[str, Any]]: ...

    def ref_sha(self, repository: str, ref: str) -> str: ...


class GhClient:
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
        except ValueError as error:
            raise GitHubError from error

    def pull_request(self, repository: str, number: int) -> Mapping[str, Any]:
        value = self._json(
            (
                "gh",
                "pr",
                "view",
                str(number),
                "--repo",
                repository,
                "--json",
                "number,url,state,baseRefName,baseRefOid,headRefName,headRefOid,files,mergeCommit,isCrossRepository",
            )
        )
        if not isinstance(value, Mapping):
            raise GitHubError
        return cast(Mapping[str, Any], value)

    def pr_checks(self, repository: str, number: int) -> Sequence[Mapping[str, Any]]:
        value = self._json(
            (
                "gh",
                "pr",
                "checks",
                str(number),
                "--repo",
                repository,
                "--json",
                "name,state,link",
            )
        )
        if not isinstance(value, list):
            raise GitHubError
        return cast(list[Mapping[str, Any]], value)

    def commit_checks(self, repository: str, sha: str) -> Sequence[Mapping[str, Any]]:
        value = self._json(
            ("gh", "api", f"repos/{repository}/commits/{sha}/check-runs", "--paginate")
        )
        if not isinstance(value, Mapping) or not isinstance(
            value.get("check_runs"), list
        ):
            raise GitHubError
        result: list[Mapping[str, Any]] = []
        for item in value["check_runs"]:
            if not isinstance(item, Mapping):
                raise GitHubError
            result.append(
                {
                    "name": item.get("name"),
                    "state": item.get("conclusion") or item.get("status"),
                    "link": item.get("html_url"),
                }
            )
        return result

    def ref_sha(self, repository: str, ref: str) -> str:
        value = self._json(("gh", "api", f"repos/{repository}/git/ref/heads/{ref}"))
        if not isinstance(value, Mapping) or not isinstance(
            value.get("object"), Mapping
        ):
            raise GitHubError
        sha = value["object"].get("sha")
        if not isinstance(sha, str):
            raise GitHubError
        return sha


@dataclass(frozen=True)
class Options:
    phase: str
    repository: str
    epic: int
    task: int
    pr: int
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    allowed_paths: tuple[str, ...]
    merge_sha: str | None = None


@dataclass(frozen=True)
class Check:
    name: str
    state: str
    link: str


@dataclass
class Result:
    schema_version: str
    status: str
    machine_code: str
    exit_code: int
    phase: str
    task_status: str | None
    repository: str
    epic: int
    task: int
    pr: int
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    merge_sha: str | None = None
    changed_files: list[str] = field(default_factory=list)
    local_checks: list[dict[str, Any]] = field(default_factory=list)
    github_checks: list[Check] = field(default_factory=list)


def _result(options: Options, code: str, **values: Any) -> Result:
    return Result(
        schema_version=SCHEMA_VERSION,
        status="pass" if code == "OK" else "blocked",
        machine_code=code,
        exit_code=EXIT_CODES[code],
        phase=options.phase,
        task_status=("TASK READY FOR REVIEW" if options.phase == "pr" else "TASK DONE")
        if code == "OK"
        else None,
        repository=options.repository,
        epic=options.epic,
        task=options.task,
        pr=options.pr,
        base_ref=options.base_ref,
        base_sha=options.base_sha,
        head_ref=options.head_ref,
        head_sha=options.head_sha,
        merge_sha=options.merge_sha,
        **values,
    )


def _identity(pr: Mapping[str, Any]) -> tuple[Any, ...]:
    files = pr.get("files")
    if not isinstance(files, list) or not all(
        isinstance(item, Mapping) and isinstance(item.get("path"), str)
        for item in files
    ):
        raise GitHubError
    merge = pr.get("mergeCommit")
    merge_sha = merge.get("oid") if isinstance(merge, Mapping) else None
    return (
        pr.get("state"),
        pr.get("baseRefName"),
        pr.get("baseRefOid"),
        pr.get("headRefName"),
        pr.get("headRefOid"),
        merge_sha,
        tuple(item["path"] for item in files),
    )


def _local_checks(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks = evidence.get("checks")
    if not isinstance(checks, list):
        return []
    return [
        {
            "id": item.get("id"),
            "cwd": item.get("cwd"),
            "status": item.get("status"),
            "exit_code": item.get("exit_code"),
            "metrics": item.get("metrics"),
        }
        for item in checks
        if isinstance(item, Mapping)
    ]


def _checks(raw: Sequence[Mapping[str, Any]]) -> list[Check]:
    return [
        Check(
            str(item.get("name", ""))[:200],
            str(item.get("state", "UNKNOWN")).upper()[:40],
            str(item.get("link", ""))[:1000],
        )
        for item in raw
    ]


def _ci_code(checks: Sequence[Check]) -> str:
    by_name: dict[str, Check] = {}
    for check in checks:
        if check.name in by_name:
            return "CI_FAILED"
        by_name[check.name] = check
    required = [by_name.get(name) for name in REQUIRED_CHECKS]
    if any(item is None for item in required):
        return "CI_MISSING"
    states = {cast(Check, item).state for item in required}
    if states == {SUCCESS}:
        return "OK"
    if states & {
        "EXPECTED",
        "IN_PROGRESS",
        "PENDING",
        "QUEUED",
        "REQUESTED",
        "WAITING",
    }:
        return "CI_PENDING"
    return "CI_FAILED"


def assess(
    options: Options, gate_evidence: Mapping[str, Any], github: GitHubClient
) -> Result:
    """Return a fail-closed assessment for one exact delivery phase."""
    if (
        options.phase not in {"pr", "post-merge"}
        or options.repository.count("/") != 1
        or options.epic < 1
        or options.task < 1
        or options.pr < 1
        or not options.base_ref.startswith(f"roadmap/{options.epic}-")
        or options.head_ref.startswith("roadmap/")
        or SHA_RE.fullmatch(options.base_sha) is None
        or SHA_RE.fullmatch(options.head_sha) is None
        or not options.allowed_paths
        or (options.phase == "post-merge") != (options.merge_sha is not None)
        or (
            options.merge_sha is not None
            and SHA_RE.fullmatch(options.merge_sha) is None
        )
    ):
        return _result(options, "USAGE_ERROR")
    local = _local_checks(gate_evidence)
    snapshot = gate_evidence.get("snapshot")
    if (
        not agent_gate.gate_evidence_is_passing(gate_evidence)
        or not isinstance(snapshot, Mapping)
        or snapshot.get("base_sha") != options.base_sha
        or snapshot.get("snapshot_commit") != options.head_sha
    ):
        return _result(options, "LOCAL_GATE_INVALID", local_checks=local)
    try:
        initial = github.pull_request(options.repository, options.pr)
        identity = _identity(initial)
        state, base, base_sha, head, head_sha, merge_sha, file_values = identity
        common = {"changed_files": sorted(file_values), "local_checks": local}
        if base != options.base_ref or head != options.head_ref:
            return _result(options, "WRONG_DIRECTION", **common)
        if base_sha != options.base_sha or head_sha != options.head_sha:
            return _result(options, "STALE_IDENTITY", **common)
        if not set(file_values).issubset(options.allowed_paths):
            return _result(options, "SCOPE_ESCAPE", **common)
        if options.phase == "pr":
            raw = github.pr_checks(options.repository, options.pr)
        else:
            if state != "MERGED" or merge_sha != options.merge_sha:
                return _result(options, "MERGE_UNCONFIRMED", **common)
            if (
                github.ref_sha(options.repository, options.base_ref)
                != options.merge_sha
            ):
                return _result(options, "STALE_IDENTITY", **common)
            raw = github.commit_checks(options.repository, options.merge_sha)
        checks = _checks(raw)
        code = _ci_code(checks)
        if code != "OK":
            return _result(options, code, github_checks=checks, **common)
        final = github.pull_request(options.repository, options.pr)
        if _identity(final) != identity:
            return _result(options, "STALE_IDENTITY", github_checks=checks, **common)
        return _result(options, "OK", github_checks=checks, **common)
    except GitHubError:
        return _result(options, "GITHUB_ERROR", local_checks=local)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("pr", "post-merge"), required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--epic", type=int, required=True)
    parser.add_argument("--task", type=int, required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--allowed-path", action="append", required=True)
    parser.add_argument("--gate-evidence", type=Path, required=True)
    parser.add_argument("--merge-sha")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        evidence = json.loads(args.gate_evidence.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        evidence = {}
    result = assess(
        Options(
            args.phase,
            args.repository,
            args.epic,
            args.task,
            args.pr,
            args.base_ref,
            args.base_sha,
            args.head_ref,
            args.head_sha,
            tuple(args.allowed_path),
            args.merge_sha,
        ),
        evidence if isinstance(evidence, Mapping) else {},
        GhClient(),
    )
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
