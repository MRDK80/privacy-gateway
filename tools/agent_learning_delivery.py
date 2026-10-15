"""Fixed original delivery and GitHub identities; no candidate-supplied flags."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import fields
from typing import Any

import yaml

from tools import agent_epic_delivery as delivery
from tools import agent_epic_loop as loop
from tools import agent_learning as c
from tools.agent_continuation import digest
from tools.agent_coordinator_delivery import REQUIRED_CHECKS
from tools.agent_learning_evidence import SourceReader


def normalize_checks(raw: Any, repository: str, sha: str) -> list[dict[str, Any]]:
    c.hash_value(sha, size=40)
    if not isinstance(raw, dict) or not isinstance(raw.get("check_runs"), list):
        raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
    items = raw["check_runs"]
    if len(items) > 100 or raw.get("total_count") != len(items):
        raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    ids: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
        name = item.get("name")
        if name not in REQUIRED_CHECKS:
            continue
        app, suite = item.get("app"), item.get("check_suite")
        match = re.fullmatch(
            re.escape(f"https://github.com/{repository}/actions/runs/")
            + r"([1-9][0-9]*)/job/([1-9][0-9]*)",
            str(item.get("details_url")),
        )
        if (
            name in seen
            or item.get("head_sha") != sha
            or item.get("status") != "completed"
            or item.get("conclusion") != "success"
            or not isinstance(app, dict)
            or app.get("id") != 15368
            or app.get("slug") != "github-actions"
            or not isinstance(suite, dict)
            or match is None
        ):
            raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
        check_id = c.integer(item.get("id"), 2**63 - 1, 1)
        suite_id = c.integer(suite.get("id"), 2**63 - 1, 1)
        if check_id in ids or int(match[2]) != check_id:
            raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
        seen.add(name)
        ids.add(check_id)
        result.append(
            {
                "name": name,
                "head_sha": sha,
                "check_id": check_id,
                "suite_id": suite_id,
                "app_id": app["id"],
                "run_id": int(match[1]),
                "job_id": int(match[2]),
                "conclusion": "success",
            }
        )
    if seen != REQUIRED_CHECKS:
        raise c.LearningError("LEARNING_CI_SOURCE_MISSING")
    return sorted(result, key=lambda item: item["name"])


class DeliverySources:
    def __init__(self, reader: SourceReader) -> None:
        self.reader = reader

    def api(self, suffix: str) -> Any:
        reader = self.reader
        reader.guard()
        if reader.read is None:
            raise c.LearningError("LEARNING_CI_SOURCE_MISSING")
        raw = reader.read(("gh", "api", f"repos/{reader.owner['repository']}/{suffix}"))
        if not isinstance(raw, str) or len(raw.encode()) > 1_000_000:
            raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
        try:
            return json.loads(raw)
        except ValueError:
            raise c.LearningError("LEARNING_CI_SOURCE_INVALID") from None

    def workflow(self, check_name: str) -> dict[str, Any]:
        """Bind a fixed primary workflow to the exact owner policy definition."""
        reader = self.reader
        reader.guard()
        filename = "pre-commit.yml" if check_name == "pre-commit" else "tests.yml"
        path = ".github/workflows/" + filename
        definition = reader._git("show", f"{reader.owner['policy_sha']}:{path}")
        if len(definition) > 1_000_000:
            raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
        try:
            pinned = yaml.safe_load(definition.decode("utf-8", "strict"))
        except (UnicodeError, yaml.YAMLError):
            raise c.LearningError("LEARNING_CI_SOURCE_INVALID") from None
        if (
            not isinstance(pinned, dict)
            or not isinstance(pinned.get("name"), str)
            or not pinned["name"]
        ):
            raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
        original = self.api(f"actions/workflows/{filename}")
        if (
            not isinstance(original, dict)
            or original.get("path") != path
            or original.get("name") != pinned["name"]
            or original.get("state") != "active"
        ):
            raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
        workflow_id = c.integer(original.get("id"), 2**63 - 1, 1)
        return {
            "id": workflow_id,
            "name": pinned["name"],
            "path": path,
            "definition_digest": hashlib.sha256(definition).hexdigest(),
        }

    def ci(self, sha: str, *, event: str) -> dict[str, Any]:
        c.hash_value(sha, size=40)
        if event not in {"pull_request", "push"}:
            raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
        repo = self.reader.owner["repository"]
        checks = normalize_checks(
            self.api(f"commits/{sha}/check-runs?per_page=100"), repo, sha
        )
        runs: dict[int, dict[str, Any]] = {}
        suites: dict[int, dict[str, Any]] = {}
        workflows: dict[str, dict[str, Any]] = {}
        for check in checks:
            workflow_key = "pre-commit" if check["name"] == "pre-commit" else "tests"
            if workflow_key not in workflows:
                workflows[workflow_key] = self.workflow(check["name"])
            expected_workflow = workflows[workflow_key]
            suite_id = check["suite_id"]
            if suite_id not in suites:
                suite = self.api(f"check-suites/{suite_id}")
                if (
                    not isinstance(suite, dict)
                    or suite.get("id") != suite_id
                    or suite.get("head_sha") != sha
                    or not isinstance(suite.get("app"), dict)
                    or suite["app"].get("id") != check["app_id"]
                    or not isinstance(suite.get("repository"), dict)
                    or suite["repository"].get("full_name") != repo
                ):
                    raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
                suites[suite_id] = suite
            run_id = check["run_id"]
            if run_id not in runs:
                run = self.api(f"actions/runs/{run_id}")
                if (
                    not isinstance(run, dict)
                    or run.get("id") != run_id
                    or run.get("head_sha") != sha
                    or run.get("event") != event
                    or run.get("status") != "completed"
                    or run.get("conclusion") != "success"
                    or not isinstance(run.get("repository"), dict)
                    or run["repository"].get("full_name") != repo
                ):
                    raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
                attempt = c.integer(run.get("run_attempt"), 1000, 1)
                jobs = self.api(
                    f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
                )
                if (
                    not isinstance(jobs, dict)
                    or not isinstance(jobs.get("jobs"), list)
                    or len(jobs["jobs"]) > 100
                    or jobs.get("total_count") != len(jobs["jobs"])
                ):
                    raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
                runs[run_id] = {"run": run, "jobs": jobs, "attempt": attempt}
            bound = runs[run_id]
            original_run = bound["run"]
            if (
                original_run.get("workflow_id") != expected_workflow["id"]
                or original_run.get("path") != expected_workflow["path"]
                or original_run.get("name") != expected_workflow["name"]
                or original_run.get("check_suite_id") != suite_id
            ):
                raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
            matched = [
                job
                for job in bound["jobs"]["jobs"]
                if isinstance(job, dict)
                and job.get("id") == check["job_id"]
                and job.get("name") == check["name"]
                and job.get("run_id") == run_id
                and job.get("head_sha") == sha
                and job.get("run_attempt") == bound["attempt"]
                and job.get("status") == "completed"
                and job.get("conclusion") == "success"
            ]
            if len(matched) != 1:
                raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
            check["attempt"] = bound["attempt"]
        return {
            "sha": sha,
            "event": event,
            "checks": checks,
            "source_digest": digest(
                {"runs": runs, "suites": suites, "workflows": workflows}
            ),
        }

    def ledger(self, task: int) -> dict[str, Any]:
        reader = self.reader
        reader.guard()
        contract = reader.contract(task)
        directory = reader.task_directory(task)
        with reader.ownership(directory):
            rows = reader._json(directory / "delivery-ledger.json")
            checkpoint = loop.validate_checkpoint(
                reader._json(directory / "epic-loop-checkpoint.json")
            )
        if len(rows) > reader.owner["limits"]["max_records"]:
            raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
        applied: dict[str, dict[str, Any]] = {}
        expected_fields = {field.name for field in fields(delivery.Request)}
        for key, row in rows.items():
            if not isinstance(row, dict) or set(row) != {
                "operation",
                "request",
                "status",
                "receipt",
            }:
                raise c.LearningError("LEARNING_DELIVERY_SOURCE_INVALID")
            request = row["request"]
            if not isinstance(request, dict) or set(request) != expected_fields:
                raise c.LearningError("LEARNING_DELIVERY_SOURCE_INVALID")
            try:
                original = delivery.Request(**request)
                valid = delivery._valid_request(original)
            except (TypeError, ValueError):
                valid = False
            if (
                not valid
                or request["operation_id"] != key
                or row["operation"] != request["operation"]
                or request["repository"] != reader.owner["repository"]
                or request["epic"] != contract.epic
                or request["task"] != task
                or request["policy_sha"] != reader.owner["policy_sha"]
                or request["base_ref"] != contract.base_ref
                or request["base_sha"] != contract.base_sha
                or request["head_ref"] != contract.head_ref
            ):
                raise c.LearningError("LEARNING_DELIVERY_SOURCE_INVALID")
            if row["status"] != "APPLIED":
                raise c.LearningError("LEARNING_DELIVERY_SOURCE_PENDING")
            if request["operation"] in applied:
                raise c.LearningError("LEARNING_DELIVERY_SOURCE_INVALID")
            applied[request["operation"]] = row
        required = {
            "commit_task",
            "push_task",
            "create_task_pr",
            "merge_task_pr",
            "close_task",
            "update_epic",
        }
        if set(applied) != required:
            raise c.LearningError("LEARNING_DELIVERY_SOURCE_MISSING")
        merge = applied["merge_task_pr"]
        receipt = c.exact(merge["receipt"], {"merge_sha"})
        c.hash_value(receipt["merge_sha"], size=40)
        head, pr = merge["request"]["head_sha"], merge["request"]["pr"]
        for operation in required:
            request = applied[operation]["request"]
            if (operation != "commit_task" and request["head_sha"] != head) or (
                request["pr"] is not None and request["pr"] != pr
            ):
                raise c.LearningError("LEARNING_DELIVERY_SOURCE_INVALID")
            if (
                operation in delivery.POST_MERGE_OPERATIONS
                and request["merge_sha"] != receipt["merge_sha"]
            ):
                raise c.LearningError("LEARNING_DELIVERY_SOURCE_INVALID")
        commit = c.exact(applied["commit_task"]["receipt"], {"head_sha", "tree_hash"})
        c.hash_value(commit["tree_hash"], size=40)
        expected = {
            "commit_task": commit,
            "push_task": {"head_sha": head},
            "create_task_pr": {"pr": pr, "head_sha": head},
            "merge_task_pr": {"merge_sha": receipt["merge_sha"]},
            "close_task": {"task": task, "merge_sha": receipt["merge_sha"]},
        }
        if commit["head_sha"] != head or any(
            applied[key]["receipt"] != value for key, value in expected.items()
        ):
            raise c.LearningError("LEARNING_DELIVERY_SOURCE_INVALID")
        comment = c.exact(
            applied["update_epic"]["receipt"], {"comment_id", "merge_sha"}
        )
        c.integer(comment["comment_id"], 2**63 - 1, 1)
        if comment["merge_sha"] != receipt["merge_sha"] or any(
            checkpoint[key] != value
            for key, value in {
                "task": task,
                "repository": reader.owner["repository"],
                "epic": reader.owner["epic"],
                "head_sha": head,
                "pr": pr,
                "merge_sha": receipt["merge_sha"],
                "policy_sha": reader.owner["policy_sha"],
            }.items()
        ):
            raise c.LearningError("LEARNING_DELIVERY_SOURCE_INVALID")
        actual = self.api(f"pulls/{pr}")
        if (
            not isinstance(actual, dict)
            or actual.get("number") != pr
            or actual.get("merged") is not True
            or actual.get("merge_commit_sha") != receipt["merge_sha"]
            or not isinstance(actual.get("head"), dict)
            or actual["head"].get("sha") != head
            or actual["head"].get("ref") != contract.head_ref
            or not isinstance(actual.get("base"), dict)
            or actual["base"].get("ref") != contract.base_ref
        ):
            raise c.LearningError("LEARNING_DELIVERY_SOURCE_INVALID")
        return {
            "head_sha": head,
            "merge_sha": receipt["merge_sha"],
            "pr": pr,
            "source_digest": digest(rows),
        }
