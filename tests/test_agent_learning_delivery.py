"""Exact CI source identities cannot be replaced by success flags."""

from __future__ import annotations

from typing import Any

import pytest
from tools import agent_learning as c


def checks(sha: str) -> dict[str, Any]:
    from tools.agent_coordinator_delivery import REQUIRED_CHECKS

    return {
        "check_runs": [
            {
                "id": i + 1,
                "name": name,
                "head_sha": sha,
                "status": "completed",
                "conclusion": "success",
                "app": {"id": 15368, "slug": "github-actions"},
                "details_url": (
                    "https://github.com/synthetic/repo/actions/runs/"
                    f"{100 if name == 'pre-commit' else 99}/job/{i + 1}"
                ),
                "check_suite": {"id": 51 if name == "pre-commit" else 50},
            }
            for i, name in enumerate(sorted(REQUIRED_CHECKS))
        ],
        "total_count": 5,
    }


def test_ci_rejects_wrong_sha_and_duplicate_required_job() -> None:
    from tools.agent_learning_delivery import normalize_checks

    sha = "a" * 40
    with pytest.raises(c.LearningError):
        normalize_checks(checks("b" * 40), "synthetic/repo", sha)
    value = checks(sha)
    value["check_runs"].append(value["check_runs"][0])
    with pytest.raises(c.LearningError):
        normalize_checks(value, "synthetic/repo", sha)


def test_ci_requires_actual_github_actions_identity() -> None:
    from tools.agent_learning_delivery import normalize_checks

    sha = "a" * 40
    value = checks(sha)
    value["check_runs"][0]["app"]["id"] = 1
    with pytest.raises(c.LearningError):
        normalize_checks(value, "synthetic/repo", sha)
    actual = normalize_checks(checks(sha), "synthetic/repo", sha)
    assert len(actual) == 5
    assert {check["run_id"] for check in actual} == {99, 100}


def ci_reader(*, change: str | None = None) -> tuple[Any, list[str]]:
    import json
    from pathlib import Path
    from types import SimpleNamespace

    sha = "a" * 40
    policy = "b" * 40
    seen: list[str] = []

    def git(*argv: str) -> bytes:
        assert argv[0] == "show"
        revision, path = argv[1].split(":", 1)
        assert revision == policy
        seen.append("policy:" + path)
        if change == "policy-name":
            return b"name: wrong-name\njobs: {}\n"
        return Path(path).read_bytes()

    def read(argv: tuple[str, ...]) -> str:
        suffix = argv[-1].removeprefix("repos/synthetic/repo/")
        seen.append(suffix)
        if suffix.startswith("commits/"):
            if suffix.endswith("/check-suites?per_page=100"):
                return json.dumps({
                    "total_count": 2,
                    "check_suites": [
                        {
                            "id": identifier, "head_sha": sha,
                            "app": {"id": 15368},
                            "repository": {"full_name": "synthetic/repo"},
                        }
                        for identifier in (50, 51)
                    ],
                })
            if suffix.endswith((
                "/check-runs?per_page=100",
                "/check-runs?filter=all&per_page=100",
            )):
                return json.dumps(checks(sha))
            raise AssertionError("unsupported synthetic commit endpoint")
        if suffix.startswith("check-suites/"):
            suite = int(suffix.split("/")[1])
            return json.dumps(
                {
                    "id": suite,
                    "head_sha": sha,
                    "app": {"id": 15368},
                    "repository": {"full_name": "synthetic/repo"},
                }
            )
        if suffix.startswith("actions/workflows/"):
            filename = suffix.removeprefix("actions/workflows/")
            pre = filename == "pre-commit.yml"
            workflow = {
                "id": 101 if pre else 102,
                "name": "pre-commit" if pre else "CI",
                "path": ".github/workflows/" + filename,
                "state": "active",
            }
            if change == "workflow-path":
                workflow["path"] = ".github/workflows/untrusted.yml"
            elif change == "workflow-name":
                workflow["name"] = "untrusted"
            elif change == "workflow-id":
                workflow["id"] = 999
            elif change == "workflow-inactive":
                workflow["state"] = "disabled_manually"
            return json.dumps(workflow)
        parts = suffix.split("/")
        run_id = int(parts[2])
        pre = run_id == 100
        if len(parts) == 3:
            run = {
                "id": run_id,
                "head_sha": sha,
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "run_attempt": 2,
                "repository": {"full_name": "synthetic/repo"},
                "name": "pre-commit" if pre else "CI",
                "path": ".github/workflows/"
                + ("pre-commit.yml" if pre else "tests.yml"),
                "workflow_id": 101 if pre else 102,
                "check_suite_id": 51 if pre else 50,
            }
            if change == "run-name":
                run["name"] = "untrusted"
            elif change == "run-path":
                run["path"] = ".github/workflows/untrusted.yml"
            elif change == "run-workflow-id":
                run["workflow_id"] = 999
            elif change == "run-suite-id":
                run["check_suite_id"] = 999
            elif change == "missing-workflow-id":
                del run["workflow_id"]
            return json.dumps(run)
        assert suffix == f"actions/runs/{run_id}/attempts/2/jobs?per_page=100"
        rows = [
            x for x in checks(sha)["check_runs"] if (x["name"] == "pre-commit") == pre
        ]
        return json.dumps(
            {
                "total_count": len(rows),
                "jobs": [
                    {
                        "id": x["id"],
                        "name": x["name"],
                        "run_id": run_id,
                        "head_sha": sha,
                        "run_attempt": 2,
                        "status": "completed",
                        "conclusion": "success",
                    }
                    for x in rows
                ],
            }
        )

    reader = SimpleNamespace(
        config={"schema_version": "7.0"},
        guard=lambda: None,
        read=read,
        _git=git,
        owner={"repository": "synthetic/repo", "policy_sha": policy},
    )
    return reader, seen


def test_exact_ci_rereads_suite_run_attempt_and_jobs() -> None:
    from tools.agent_learning_delivery import DeliverySources

    reader, seen = ci_reader()
    facts = DeliverySources(reader).ci("a" * 40, event="push")
    assert {check["run_id"] for check in facts["checks"]} == {99, 100}
    assert all(check["attempt"] == 2 for check in facts["checks"])
    assert "actions/runs/99/attempts/2/jobs?per_page=100" in seen
    assert "actions/runs/100/attempts/2/jobs?per_page=100" in seen
    with pytest.raises(c.LearningError):
        DeliverySources(reader).ci("a" * 40, event="pull_request")


@pytest.mark.parametrize(
    "change",
    [
        "run-name",
        "run-path",
        "run-workflow-id",
        "run-suite-id",
        "missing-workflow-id",
        "workflow-path",
        "workflow-name",
        "workflow-id",
        "workflow-inactive",
        "policy-name",
    ],
)
def test_exact_ci_rejects_success_from_other_workflow(change: str) -> None:
    from tools.agent_learning_delivery import DeliverySources

    reader, _ = ci_reader(change=change)
    expected = (
        "LEARNING_INVALID"
        if change == "missing-workflow-id"
        else "LEARNING_CI_SOURCE_INVALID"
    )
    with pytest.raises(c.LearningError, match=expected):
        DeliverySources(reader).ci("a" * 40, event="push")
