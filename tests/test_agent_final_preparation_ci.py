"""Pure synthetic primary provenance; never installed-source acceptance."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from tools.agent_coordinator_delivery import REQUIRED_CHECKS
from tools.agent_final_preparation_ci import Comparison, PrimaryCIError, normalize


def sources() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    jobs: dict[int, Any] = {}
    runs: dict[int, Any] = {}
    suites: dict[int, Any] = {}
    workflows: dict[str, Any] = {}
    definitions: dict[str, bytes] = {}
    for filename, run_id, suite_id, workflow_id, label in (
        ("tests.yml", 90, 50, 102, "Tests"),
        ("pre-commit.yml", 91, 51, 101, "Pre-commit"),
    ):
        path = ".github/workflows/" + filename
        definitions[filename] = f"name: {label}\non: pull_request\n".encode()
        workflows[filename] = {
            "id": workflow_id,
            "path": path,
            "name": label,
            "state": "active",
        }
        suites[suite_id] = {
            "id": suite_id,
            "head_sha": "a" * 40,
            "app": {"id": 15368},
            "repository": {"full_name": "synthetic/repo"},
        }
        runs[run_id] = {
            "id": run_id,
            "head_sha": "a" * 40,
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 2,
            "repository": {"full_name": "synthetic/repo"},
            "workflow_id": workflow_id,
            "path": path,
            "name": label,
            "check_suite_id": suite_id,
        }
        jobs[run_id] = {"total_count": 0, "jobs": []}
    for check_id, name in enumerate(sorted(REQUIRED_CHECKS), 201):
        run_id = 91 if name == "pre-commit" else 90
        checks.append(
            {
                "id": check_id,
                "name": name,
                "head_sha": "a" * 40,
                "status": "completed",
                "conclusion": "success",
                "app": {"id": 15368, "slug": "github-actions"},
                "check_suite": {"id": 51 if run_id == 91 else 50},
                "details_url": f"https://github.com/synthetic/repo/actions/runs/{run_id}/job/{check_id}",
            }
        )
        jobs[run_id]["jobs"].append(
            {
                "id": check_id,
                "name": name,
                "run_id": run_id,
                "head_sha": "a" * 40,
                "run_attempt": 2,
                "status": "completed",
                "conclusion": "success",
            }
        )
        jobs[run_id]["total_count"] += 1
    return {
        "comparison": Comparison("synthetic/repo", "a" * 40, "b" * 40),
        "check_runs": {"total_count": 5, "check_runs": checks},
        "suites": suites,
        "runs": runs,
        "jobs": jobs,
        "definitions": definitions,
        "workflows": workflows,
    }


def test_success_is_immutable_neutral_facts_without_mutating_originals() -> None:
    original = sources()
    before = copy.deepcopy(original)
    facts = normalize(**original)
    assert original == before
    assert {check.name for check in facts.checks} == REQUIRED_CHECKS
    assert all(check.attempt == 2 for check in facts.checks)
    assert facts.event == "pull_request"
    assert not hasattr(facts, "ready") and not hasattr(facts, "candidate_digest")
    assert "synthetic/repo" not in repr(facts)
    assert facts == normalize(**original)
    with pytest.raises(FrozenInstanceError):
        setattr(facts, "sha", "c" * 40)


@pytest.mark.parametrize(
    "fault",
    [
        "check-sha",
        "check-app",
        "check-slug",
        "check-duplicate",
        "check-missing",
        "check-url",
        "check-id-bool",
        "check-total",
        "check-overflow",
        "suite-head",
        "suite-repo",
        "suite-app",
        "suite-id",
        "suite-missing",
        "run-head",
        "run-repo",
        "run-event",
        "run-status",
        "run-conclusion",
        "run-id",
        "run-attempt",
        "run-workflow",
        "run-path",
        "run-name",
        "run-suite",
        "job-head",
        "job-name",
        "job-id",
        "job-run",
        "job-attempt",
        "job-status",
        "job-conclusion",
        "job-duplicate",
        "job-total",
        "job-overflow",
        "workflow-path",
        "workflow-name",
        "workflow-state",
        "workflow-id",
        "definition-name",
        "definition-utf8",
        "definition-overflow",
        "source-overflow",
    ],
)
def test_every_broken_primary_edge_is_rejected_not_classified_as_wait(
    fault: str,
) -> None:
    value = sources()
    check = value["check_runs"]["check_runs"][1]
    suite, run = value["suites"][50], value["runs"][90]
    job = value["jobs"][90]["jobs"][0]
    workflow = value["workflows"]["tests.yml"]
    if fault.startswith("check-"):
        key = fault[6:]
        if key == "sha":
            check["head_sha"] = "c" * 40
        elif key == "app":
            check["app"]["id"] = 1
        elif key == "slug":
            check["app"]["slug"] = "foreign"
        elif key == "duplicate":
            value["check_runs"]["check_runs"][0] = copy.deepcopy(check)
        elif key == "missing":
            value["check_runs"]["check_runs"].pop()
            value["check_runs"]["total_count"] = 4
        elif key == "url":
            check["details_url"] = check["details_url"].replace(
                "synthetic/repo", "foreign/repo"
            )
        elif key == "id-bool":
            check["id"] = True
        elif key == "total":
            value["check_runs"]["total_count"] = 6
        elif key == "overflow":
            value["check_runs"]["check_runs"] *= 21
    elif fault.startswith("suite-"):
        key = fault[6:]
        if key == "head":
            suite["head_sha"] = "c" * 40
        elif key == "repo":
            suite["repository"]["full_name"] = "foreign/repo"
        elif key == "app":
            suite["app"]["id"] = 1
        elif key == "id":
            suite["id"] = 500
        elif key == "missing":
            del value["suites"][50]
    elif fault.startswith("run-"):
        key = fault[4:]
        field = {
            "head": "head_sha",
            "repo": "repository",
            "attempt": "run_attempt",
            "workflow": "workflow_id",
            "suite": "check_suite_id",
        }.get(key, key)
        run[field] = {
            "repo": {"full_name": "foreign/repo"},
            "attempt": 1001,
            "workflow": 999,
            "suite": 999,
            "id": 999,
        }.get(key, "foreign")
    elif fault.startswith("job-"):
        key = fault[4:]
        if key == "duplicate":
            value["jobs"][90]["jobs"].append(copy.deepcopy(job))
            value["jobs"][90]["total_count"] += 1
        elif key == "total":
            value["jobs"][90]["total_count"] += 1
        elif key == "overflow":
            value["jobs"][90]["jobs"] *= 26
        else:
            job[
                {"head": "head_sha", "run": "run_id", "attempt": "run_attempt"}.get(
                    key, key
                )
            ] = "foreign"
    elif fault.startswith("workflow-"):
        workflow[fault[9:]] = "foreign"
    elif fault.startswith("definition-"):
        value["definitions"]["tests.yml"] = {
            "name": b"name: Other\n",
            "utf8": b"\xff",
            "overflow": b"x" * 1_000_001,
        }[fault[11:]]
    else:
        run["unused"] = "x" * 1_000_001
    with pytest.raises(PrimaryCIError):
        normalize(**value)


def test_no_pr_number_link_or_source_origin_claim() -> None:
    value = sources()
    value["runs"][90]["pull_requests"] = []
    assert normalize(**value).sha == "a" * 40
    # A stronger PR-number or actual source-reader binding belongs to the driver.


def test_raw_source_changes_affect_digest_even_for_unrelated_fields() -> None:
    value = sources()
    first = normalize(**value)
    value["runs"][90]["unrelated"] = "changed"
    assert normalize(**value).source_digest != first.source_digest


@pytest.mark.parametrize(
    "status,conclusion",
    [
        ("queued", None),
        ("in_progress", None),
        ("completed", "failure"),
        ("completed", "cancelled"),
        ("completed", "timed_out"),
    ],
)
def test_non_success_is_rejected_without_any_wait_result(
    status: str, conclusion: str | None
) -> None:
    value = sources()
    value["check_runs"]["check_runs"][0].update(status=status, conclusion=conclusion)
    with pytest.raises(PrimaryCIError):
        normalize(**value)


@pytest.mark.parametrize(
    "comparison",
    [
        Comparison("foreign/repo", "a" * 40, "b" * 40),
        Comparison("synthetic/repo", "c" * 40, "b" * 40),
        Comparison("synthetic/repo", "a" * 40, "invalid"),
    ],
)
def test_invalid_or_mismatched_comparison_cannot_normalize(
    comparison: Comparison,
) -> None:
    value = sources()
    value["comparison"] = comparison
    with pytest.raises(PrimaryCIError):
        normalize(**value)


def test_policy_label_is_only_an_explicit_comparison_not_source_authentication() -> (
    None
):
    value = sources()
    original = normalize(**value)
    value["comparison"] = Comparison("synthetic/repo", "a" * 40, "c" * 40)
    changed = normalize(**value)
    assert changed.source_digest == original.source_digest
    assert changed.comparison_digest != original.comparison_digest


def numeric_slots(value: dict[str, Any]) -> dict[str, tuple[dict[str, Any], str]]:
    check = next(
        item
        for item in value["check_runs"]["check_runs"]
        if item["name"] != "pre-commit"
    )
    return {
        "check-id": (check, "id"),
        "check-app-id": (check["app"], "id"),
        "check-suite-id": (check["check_suite"], "id"),
        "check-total": (value["check_runs"], "total_count"),
        "suite-id": (value["suites"][50], "id"),
        "suite-app-id": (value["suites"][50]["app"], "id"),
        "run-id": (value["runs"][90], "id"),
        "run-workflow-id": (value["runs"][90], "workflow_id"),
        "run-suite-id": (value["runs"][90], "check_suite_id"),
        "run-attempt": (value["runs"][90], "run_attempt"),
        "job-id": (value["jobs"][90]["jobs"][0], "id"),
        "job-run-id": (value["jobs"][90]["jobs"][0], "run_id"),
        "job-attempt": (value["jobs"][90]["jobs"][0], "run_attempt"),
        "jobs-total": (value["jobs"][91], "total_count"),
        "workflow-id": (value["workflows"]["tests.yml"], "id"),
    }


@pytest.mark.parametrize(
    "edge",
    [
        "check-id",
        "check-app-id",
        "check-suite-id",
        "check-total",
        "suite-id",
        "suite-app-id",
        "run-id",
        "run-workflow-id",
        "run-suite-id",
        "run-attempt",
        "job-id",
        "job-run-id",
        "job-attempt",
        "jobs-total",
        "workflow-id",
    ],
)
@pytest.mark.parametrize("fractional", [False, True])
def test_original_numeric_fields_reject_json_floats(
    edge: str, fractional: bool
) -> None:
    value = sources()
    original, key = numeric_slots(value)[edge]
    original[key] = float(original[key]) + (0.5 if fractional else 0.0)
    with pytest.raises(PrimaryCIError, match="^FINAL_PRIMARY_CI_INVALID$"):
        normalize(**value)


def ones_sources() -> dict[str, Any]:
    """Coherent valid ordinary IDs/attempts equal one, not approving flags."""
    value = sources()
    value["suites"][1] = value["suites"].pop(50)
    value["suites"][1]["id"] = 1
    value["runs"][1] = value["runs"].pop(90)
    value["runs"][1].update(id=1, workflow_id=1, check_suite_id=1, run_attempt=1)
    value["workflows"]["tests.yml"]["id"] = 1
    value["jobs"][1] = value["jobs"].pop(90)
    for job in value["jobs"][1]["jobs"]:
        job.update(run_id=1, run_attempt=1)
    first_job = value["jobs"][1]["jobs"][0]
    old_id = first_job["id"]
    first_job["id"] = 1
    for check in value["check_runs"]["check_runs"]:
        if check["name"] != "pre-commit":
            check["check_suite"]["id"] = 1
            check["details_url"] = check["details_url"].replace("/runs/90/", "/runs/1/")
            if check["id"] == old_id:
                check["id"] = 1
                check["details_url"] = (
                    check["details_url"].rsplit("/job/", 1)[0] + "/job/1"
                )
    return value


def test_integer_one_sources_remain_valid() -> None:
    assert len(normalize(**ones_sources()).checks) == 5


@pytest.mark.parametrize(
    "edge",
    [
        "check-id",
        "check-suite-id",
        "suite-id",
        "run-id",
        "run-workflow-id",
        "run-suite-id",
        "run-attempt",
        "job-id",
        "job-run-id",
        "job-attempt",
        "jobs-total",
        "workflow-id",
    ],
)
def test_boolean_true_cannot_impersonate_original_integer_one(edge: str) -> None:
    value = ones_sources()
    check = next(
        item
        for item in value["check_runs"]["check_runs"]
        if item["name"] != "pre-commit"
    )
    slots = {
        "check-id": (check, "id"),
        "check-suite-id": (check["check_suite"], "id"),
        "suite-id": (value["suites"][1], "id"),
        "run-id": (value["runs"][1], "id"),
        "run-workflow-id": (value["runs"][1], "workflow_id"),
        "run-suite-id": (value["runs"][1], "check_suite_id"),
        "run-attempt": (value["runs"][1], "run_attempt"),
        "job-id": (value["jobs"][1]["jobs"][0], "id"),
        "job-run-id": (value["jobs"][1]["jobs"][0], "run_id"),
        "job-attempt": (value["jobs"][1]["jobs"][0], "run_attempt"),
        "jobs-total": (value["jobs"][91], "total_count"),
        "workflow-id": (value["workflows"]["tests.yml"], "id"),
    }
    original, key = slots[edge]
    assert original[key] == 1 and type(original[key]) is int
    original[key] = True
    with pytest.raises(PrimaryCIError, match="^FINAL_PRIMARY_CI_INVALID$"):
        normalize(**value)


def test_invalid_pinned_yaml_timestamp_has_only_closed_error() -> None:
    value = sources()
    value["definitions"]["tests.yml"] = b"name: Tests\nunused: 2020-99-99\n"
    with pytest.raises(PrimaryCIError, match="^FINAL_PRIMARY_CI_INVALID$") as found:
        normalize(**value)
    assert found.value.__cause__ is None


def test_excessive_url_decimal_has_only_closed_error() -> None:
    value = sources()
    check = value["check_runs"]["check_runs"][0]
    check["details_url"] = (
        "https://github.com/synthetic/repo/actions/runs/"
        + "9" * 5000
        + "/job/"
        + str(check["id"])
    )
    with pytest.raises(PrimaryCIError, match="^FINAL_PRIMARY_CI_INVALID$") as found:
        normalize(**value)
    assert found.value.__cause__ is None
