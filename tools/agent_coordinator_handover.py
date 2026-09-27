#!/usr/bin/env python3
"""Validate an approved coordinator handover and run the existing workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, cast

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import agent_orchestrate as workflow  # noqa: E402
from tools.agent_memory import RetrospectiveStore  # noqa: E402
from tools.agent_memory import (
    default_directory as default_memory_directory,  # noqa: E402
)

SCHEMA_VERSION = "1.0"
SHA_RE = re.compile(r"[0-9a-f]{40}")
MAX_MINUTES = 240
MAX_REPAIRS = 2
MAX_REPORT_CHARS = 200_000
REQUIRED_TOP_LEVEL = {
    "schema_version",
    "repository",
    "epic",
    "task",
    "criteria_source",
    "acceptance_criteria",
    "delivery_criterion_indices",
    "base_ref",
    "base_sha",
    "head_ref",
    "head_sha",
    "causal_scope",
    "allowed_paths",
    "forbidden_actions",
    "permissions",
    "budgets",
    "required_gate",
    "trusted_policy",
    "task_class",
    "approval",
}
OPTIONAL_TOP_LEVEL = {"mandate_provenance"}
DENIED_ACTIONS = ("commit", "push", "create_pr", "comment", "merge")


class HandoverError(Exception):
    """Controlled validation failure safe to expose as a machine code."""

    def __init__(self, machine_code: str) -> None:
        super().__init__(machine_code)
        self.machine_code = machine_code


@dataclass(frozen=True)
class ValidatedHandover:
    repository: str
    digest: str
    contract: workflow.TaskContract


@dataclass(frozen=True)
class Result:
    status: str
    machine_code: str
    run_id: str
    repair_iterations: int
    handover_digest: str


WorkflowRunner = Callable[[workflow.TaskContract], workflow.RunResult]


class GitHubClient(Protocol):
    def issue(self, repository: str, number: int) -> Mapping[str, Any]: ...


class GitHubCLI:
    """Read only the task facts required to prove criteria provenance."""

    def issue(self, repository: str, number: int) -> Mapping[str, Any]:
        try:
            completed = subprocess.run(
                [
                    "gh",
                    "issue",
                    "view",
                    str(number),
                    "--repo",
                    repository,
                    "--json",
                    "number,state,parent,body",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise HandoverError("GITHUB_ERROR") from error
        if completed.returncode != 0:
            raise HandoverError("GITHUB_ERROR")
        try:
            value = json.loads(completed.stdout)
        except ValueError as error:
            raise HandoverError("GITHUB_ERROR") from error
        if not isinstance(value, Mapping):
            raise HandoverError("GITHUB_ERROR")
        return cast(Mapping[str, Any], value)


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise HandoverError("GIT_UNAVAILABLE") from error
    if completed.returncode != 0:
        raise HandoverError("GIT_STATE_INVALID")
    return completed.stdout.strip()


def _exact_mapping(value: Any, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise HandoverError("HANDOVER_INVALID")
    return cast(Mapping[str, Any], value)


def _string_list(value: Any, *, allow_empty: bool = False) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not value and not allow_empty)
        or not all(
            isinstance(item, str) and item.strip() == item and item for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise HandoverError("HANDOVER_INVALID")
    return tuple(value)


def handover_digest(value: Mapping[str, Any]) -> str:
    """Return the approval identity for every authority-bearing field."""
    payload = {key: item for key, item in value.items() if key != "approval"}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _repository_identity(root: Path) -> str:
    remote = _git(root, "remote", "get-url", "origin")
    match = re.search(r"(?:github\.com[/:])([^/]+/[^/]+?)(?:\.git)?$", remote)
    if match is None:
        raise HandoverError("REPOSITORY_MISMATCH")
    return match.group(1)


def validate_handover(
    value: Any,
    *,
    root: Path,
    approved_digest: str,
    github: GitHubClient,
) -> ValidatedHandover:
    """Validate authority, scope and current Git identity without side effects."""
    if not isinstance(value, Mapping) or not (
        set(value) == REQUIRED_TOP_LEVEL
        or set(value) == REQUIRED_TOP_LEVEL | OPTIONAL_TOP_LEVEL
    ):
        raise HandoverError("HANDOVER_INVALID")
    handover = cast(Mapping[str, Any], value)
    digest = handover_digest(handover)
    approval = _exact_mapping(handover["approval"], {"plan_digest"})
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", approved_digest)
        or approval["plan_digest"] != digest
        or approved_digest != digest
    ):
        raise HandoverError("APPROVAL_MISMATCH")

    repository = handover["repository"]
    epic = handover["epic"]
    task = handover["task"]
    base_ref = handover["base_ref"]
    base_sha = handover["base_sha"]
    head_ref = handover["head_ref"]
    head_sha = handover["head_sha"]
    if (
        handover["schema_version"] != SCHEMA_VERSION
        or not isinstance(repository, str)
        or repository.count("/") != 1
        or not isinstance(epic, int)
        or isinstance(epic, bool)
        or not isinstance(task, int)
        or isinstance(task, bool)
        or epic < 1
        or task < 1
        or epic == task
        or not isinstance(base_ref, str)
        or not base_ref.startswith(f"roadmap/{epic}-")
        or not isinstance(head_ref, str)
        or head_ref.startswith("roadmap/")
        or not isinstance(base_sha, str)
        or SHA_RE.fullmatch(base_sha) is None
        or not isinstance(head_sha, str)
        or SHA_RE.fullmatch(head_sha) is None
    ):
        raise HandoverError("HANDOVER_INVALID")

    criteria_source = _exact_mapping(
        handover["criteria_source"], {"kind", "issue", "state", "body_sha256"}
    )
    if (
        criteria_source.get("kind") != "github_issue"
        or criteria_source.get("issue") != task
        or criteria_source.get("state") != "OPEN"
        or not isinstance(criteria_source.get("body_sha256"), str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", criteria_source["body_sha256"])
        is None
    ):
        raise HandoverError("HANDOVER_INVALID")
    criteria = _string_list(handover["acceptance_criteria"])
    causal_scope = _string_list(handover["causal_scope"])
    del causal_scope
    allowed_paths = _string_list(handover["allowed_paths"])
    forbidden = _string_list(handover["forbidden_actions"])
    if not set(DENIED_ACTIONS).issubset(forbidden):
        raise HandoverError("PERMISSION_EXPANSION")
    permissions = _exact_mapping(handover["permissions"], set(DENIED_ACTIONS))
    if any(permissions[name] is not False for name in DENIED_ACTIONS):
        raise HandoverError("PERMISSION_EXPANSION")

    budgets = _exact_mapping(
        handover["budgets"],
        {"max_minutes", "max_repair_iterations", "max_report_chars"},
    )
    max_minutes = budgets["max_minutes"]
    max_repairs = budgets["max_repair_iterations"]
    max_report_chars = budgets["max_report_chars"]
    if (
        not isinstance(max_minutes, int)
        or isinstance(max_minutes, bool)
        or not 1 <= max_minutes <= MAX_MINUTES
        or not isinstance(max_repairs, int)
        or isinstance(max_repairs, bool)
        or not 0 <= max_repairs <= MAX_REPAIRS
        or not isinstance(max_report_chars, int)
        or isinstance(max_report_chars, bool)
        or not 1 <= max_report_chars <= MAX_REPORT_CHARS
    ):
        raise HandoverError("BUDGET_INVALID")
    gate = _exact_mapping(handover["required_gate"], {"profile", "version"})
    policy = _exact_mapping(handover["trusted_policy"], {"source", "base_sha"})
    if gate != {"profile": "repository-full", "version": "1"}:
        raise HandoverError("GATE_INVALID")
    if policy != {"source": "base_sha", "base_sha": base_sha}:
        raise HandoverError("POLICY_PROVENANCE_MISMATCH")
    if "mandate_provenance" in handover:
        provenance = _exact_mapping(
            handover["mandate_provenance"],
            {"schema_version", "digest", "policy_sha"},
        )
        if (
            provenance.get("schema_version") != "1.0"
            or not isinstance(provenance.get("digest"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", provenance["digest"]) is None
            or provenance.get("policy_sha") != base_sha
        ):
            raise HandoverError("POLICY_PROVENANCE_MISMATCH")
    indices = handover["delivery_criterion_indices"]
    if not isinstance(indices, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in indices
    ):
        raise HandoverError("HANDOVER_INVALID")
    task_class = handover["task_class"]
    if not isinstance(task_class, str) or not task_class:
        raise HandoverError("HANDOVER_INVALID")

    issue = github.issue(repository, task)
    parent = issue.get("parent")
    body = issue.get("body")
    if (
        issue.get("number") != task
        or str(issue.get("state")).upper() != "OPEN"
        or not isinstance(parent, Mapping)
        or parent.get("number") != epic
        or not isinstance(body, str)
        or "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
        != criteria_source["body_sha256"]
        or any(criterion not in body for criterion in criteria)
    ):
        raise HandoverError("CRITERIA_PROVENANCE_MISMATCH")

    top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root.resolve() or _repository_identity(root) != repository:
        raise HandoverError("REPOSITORY_MISMATCH")
    if _git(root, "status", "--porcelain=v1", "-z"):
        raise HandoverError("DIRTY_WORKTREE")
    if _git(root, "branch", "--show-current") != head_ref:
        raise HandoverError("WRONG_BRANCH")
    if _git(root, "rev-parse", "HEAD") != head_sha:
        raise HandoverError("STALE_HEAD")
    if _git(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}") != base_sha:
        raise HandoverError("STALE_BASE")
    _git(root, "merge-base", "--is-ancestor", base_sha, head_sha)

    try:
        contract = workflow.build_contract(
            issue=task,
            epic=epic,
            issue_text="",
            acceptance_criteria=criteria,
            delivery_criterion_indices=cast(Sequence[int], indices),
            base_ref=base_ref,
            head_ref=head_ref,
            root=root,
            allowed_paths=allowed_paths,
            task_class=task_class,
            max_minutes=max_minutes,
            max_repair_iterations=max_repairs,
            max_report_chars=max_report_chars,
            permissions=workflow.ActionPermissions(),
        )
    except workflow.OrchestrationError as error:
        raise HandoverError(error.machine_code) from error
    if contract.base_sha != base_sha:
        raise HandoverError("STALE_BASE")
    return ValidatedHandover(repository, digest, contract)


def run_handover(
    value: Any,
    *,
    root: Path,
    approved_digest: str,
    runner: WorkflowRunner,
    github: GitHubClient,
) -> Result:
    """Revalidate once, then hand the narrowed contract to the real workflow."""
    try:
        validated = validate_handover(
            value, root=root, approved_digest=approved_digest, github=github
        )
        run_result = runner(validated.contract)
        return Result(
            run_result.status,
            run_result.machine_code,
            run_result.run_id,
            run_result.repair_iterations,
            validated.digest,
        )
    except HandoverError as error:
        return Result("BLOCKED", error.machine_code, "not-started", 0, "")


def _json_command(value: str) -> list[str]:
    try:
        command = json.loads(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("command must be a JSON array") from error
    if not isinstance(command, list) or not command or not all(
        isinstance(item, str) and item for item in command
    ):
        raise argparse.ArgumentTypeError("command must be a non-empty JSON array")
    return command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handover", type=Path)
    parser.add_argument("--approve-plan-digest", required=True)
    parser.add_argument("--executor-command", type=_json_command, required=True)
    parser.add_argument("--controller-command", type=_json_command, required=True)
    parser.add_argument("--storage", type=Path)
    parser.add_argument("--memory-storage", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        value = json.loads(args.handover.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = Result("BLOCKED", "HANDOVER_INVALID", "not-started", 0, "")
    else:
        def invoke(contract: workflow.TaskContract) -> workflow.RunResult:
            adapter = workflow.CommandAdapter(
                args.executor_command,
                args.controller_command,
                root=args.root,
                timeout_seconds=contract.max_minutes * 60,
                output_limit=contract.max_report_chars,
            )
            return workflow.run(
                contract,
                root=args.root,
                storage=(
                    workflow.StateStore(args.storage)
                    if args.storage
                    else workflow.default_storage()
                ),
                adapter=adapter,
                memory=RetrospectiveStore(
                    args.memory_storage or default_memory_directory(),
                    args.root,
                ),
            )

        result = run_handover(
            value,
            root=args.root,
            approved_digest=args.approve_plan_digest,
            runner=invoke,
            github=GitHubCLI(),
        )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.status in {"PASS", "PASS_WITH_NOTES"} else 20


if __name__ == "__main__":
    raise SystemExit(main())
