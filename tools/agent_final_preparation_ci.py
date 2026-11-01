"""Pure successful primary-CI graph normalization, never source admission.

Callers supply originals and an explicit comparison representation. This module
reads nothing, creates no candidate or persisted record and grants no authority.
Pending classification and actual Session source binding are intentionally absent.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

import yaml

from tools import agent_learning as learning
from tools.agent_continuation import digest
from tools.agent_coordinator_delivery import REQUIRED_CHECKS
from tools.agent_learning_delivery import normalize_checks

MAX_SOURCE_BYTES = 1_000_000


class PrimaryCIError(ValueError):
    """Successful primary provenance unavailable; never a wait classification."""


def _require(condition: bool) -> None:
    if not condition:
        raise PrimaryCIError("FINAL_PRIMARY_CI_INVALID")


def _bounded(value: Any) -> None:
    try:
        size = 0
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(
            value
        ):
            _require(len(chunk) <= MAX_SOURCE_BYTES)
            size += len(chunk.encode())
            _require(size <= MAX_SOURCE_BYTES)
    except (TypeError, ValueError, RecursionError):
        raise PrimaryCIError("FINAL_PRIMARY_CI_INVALID") from None


@dataclass(frozen=True, repr=False)
class Comparison:
    repository: str
    sha: str
    policy_sha: str


@dataclass(frozen=True, repr=False)
class PrimaryCheck:
    name: str
    head_sha: str
    check_id: int
    suite_id: int
    app_id: int
    run_id: int
    job_id: int
    conclusion: str
    attempt: int


@dataclass(frozen=True, repr=False)
class PrimaryCI:
    repository: str
    sha: str
    policy_sha: str
    event: str
    checks: tuple[PrimaryCheck, ...]
    source_digest: str
    comparison_digest: str


def _workflow(definition: bytes, original: Any, filename: str) -> dict[str, Any]:
    _require(type(definition) is bytes and len(definition) <= MAX_SOURCE_BYTES)
    _bounded(original)
    try:
        pinned = yaml.safe_load(definition.decode("utf-8", "strict"))
    except (UnicodeError, yaml.YAMLError, RecursionError):
        raise PrimaryCIError("FINAL_PRIMARY_CI_INVALID") from None
    _require(isinstance(pinned, dict))
    name = pinned.get("name")
    _require(isinstance(name, str) and bool(name))
    path = ".github/workflows/" + filename
    _require(
        isinstance(original, dict)
        and original.get("path") == path
        and original.get("name") == name
        and original.get("state") == "active"
    )
    workflow_id = learning.integer(original.get("id"), 2**63 - 1, 1)
    return {
        "id": workflow_id,
        "name": name,
        "path": path,
        "definition_digest": hashlib.sha256(definition).hexdigest(),
    }


def normalize(
    *,
    comparison: Comparison,
    check_runs: Any,
    suites: dict[int, Any],
    runs: dict[int, Any],
    jobs: dict[int, Any],
    definitions: dict[str, bytes],
    workflows: dict[str, Any],
) -> PrimaryCI:
    """Validate supplied success originals; labels do not prove their origin.

    Event is fixed pull_request. There is no run.pull_requests-number assertion,
    no pending fallback, no source callback and no success/readiness argument.
    """
    try:
        return _normalize(
            comparison, check_runs, suites, runs, jobs, definitions, workflows
        )
    except PrimaryCIError:
        raise
    except (
        learning.LearningError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        RecursionError,
    ):
        raise PrimaryCIError("FINAL_PRIMARY_CI_INVALID") from None


def _normalize(
    comparison: Comparison,
    check_runs: Any,
    suites: dict[int, Any],
    runs: dict[int, Any],
    jobs: dict[int, Any],
    definitions: dict[str, bytes],
    workflows: dict[str, Any],
) -> PrimaryCI:
    _require(type(comparison) is Comparison)
    repo, sha = comparison.repository, comparison.sha
    _require(isinstance(repo, str) and len(repo) <= 201)
    _require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is not None)
    learning.hash_value(sha, size=40)
    learning.hash_value(comparison.policy_sha, size=40)
    for value in (suites, runs, jobs):
        _require(isinstance(value, dict) and len(value) <= 5)
        _require(all(type(key) is int and key > 0 for key in value))
        for original in value.values():
            _bounded(original)
    expected_files = {"tests.yml", "pre-commit.yml"}
    _require(set(definitions) == set(workflows) == expected_files)
    _bounded(check_runs)
    _require(
        isinstance(check_runs, dict) and isinstance(check_runs.get("check_runs"), list)
    )
    learning.integer(check_runs.get("total_count"), 100)
    for original_check in check_runs["check_runs"]:
        if (
            isinstance(original_check, dict)
            and original_check.get("name") in REQUIRED_CHECKS
        ):
            _require(isinstance(original_check.get("app"), dict))
            _require(isinstance(original_check.get("check_suite"), dict))
            learning.integer(original_check.get("id"), 2**63 - 1, 1)
            learning.integer(original_check["app"].get("id"), 2**63 - 1, 1)
            learning.integer(original_check["check_suite"].get("id"), 2**63 - 1, 1)
    checks = normalize_checks(check_runs, repo, sha)
    normalized_workflows = {
        "pre-commit" if filename == "pre-commit.yml" else "tests": _workflow(
            definitions[filename], workflows[filename], filename
        )
        for filename in sorted(expected_files)
    }
    bound_runs: dict[int, dict[str, Any]] = {}
    bound_suites: dict[int, dict[str, Any]] = {}
    for check in checks:
        suite_id, run_id = check["suite_id"], check["run_id"]
        suite, run, original_jobs = suites[suite_id], runs[run_id], jobs[run_id]
        expected = normalized_workflows[
            "pre-commit" if check["name"] == "pre-commit" else "tests"
        ]
        _require(
            isinstance(suite, dict)
            and learning.integer(suite.get("id"), 2**63 - 1, 1) == suite_id
            and suite.get("head_sha") == sha
            and isinstance(suite.get("app"), dict)
            and learning.integer(suite["app"].get("id"), 2**63 - 1, 1)
            == check["app_id"]
            and isinstance(suite.get("repository"), dict)
            and suite["repository"].get("full_name") == repo
        )
        _require(
            isinstance(run, dict)
            and learning.integer(run.get("id"), 2**63 - 1, 1) == run_id
            and run.get("head_sha") == sha
            and run.get("event") == "pull_request"
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and isinstance(run.get("repository"), dict)
            and run["repository"].get("full_name") == repo
            and learning.integer(run.get("workflow_id"), 2**63 - 1, 1) == expected["id"]
            and run.get("path") == expected["path"]
            and run.get("name") == expected["name"]
            and learning.integer(run.get("check_suite_id"), 2**63 - 1, 1) == suite_id
        )
        attempt = learning.integer(run.get("run_attempt"), 1000, 1)
        _require(
            isinstance(original_jobs, dict)
            and isinstance(original_jobs.get("jobs"), list)
            and len(original_jobs["jobs"]) <= 100
            and learning.integer(original_jobs.get("total_count"), 100)
            == len(original_jobs["jobs"])
        )
        matched = [
            job
            for job in original_jobs["jobs"]
            if isinstance(job, dict)
            and job.get("name") == check["name"]
            and learning.integer(job.get("id"), 2**63 - 1, 1) == check["job_id"]
            and learning.integer(job.get("run_id"), 2**63 - 1, 1) == run_id
            and job.get("head_sha") == sha
            and learning.integer(job.get("run_attempt"), 1000, 1) == attempt
            and job.get("status") == "completed"
            and job.get("conclusion") == "success"
        ]
        _require(len(matched) == 1)
        check["attempt"] = attempt
        bound_suites[suite_id] = suite
        bound_runs[run_id] = {"run": run, "jobs": original_jobs, "attempt": attempt}
    _require(set(suites) == set(bound_suites))
    _require(set(runs) == set(jobs) == set(bound_runs))
    source_digest = digest(
        {"runs": bound_runs, "suites": bound_suites, "workflows": normalized_workflows}
    )
    facts = tuple(PrimaryCheck(**check) for check in checks)
    return PrimaryCI(
        repo,
        sha,
        comparison.policy_sha,
        "pull_request",
        facts,
        source_digest,
        digest(
            {
                "repository": repo,
                "sha": sha,
                "policy_sha": comparison.policy_sha,
                "source_digest": source_digest,
                "checks": checks,
            }
        ),
    )
