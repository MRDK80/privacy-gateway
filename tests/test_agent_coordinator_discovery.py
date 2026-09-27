"""Read-only coordinator discovery and plan-preview tests (#234)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "agent_coordinator_discovery.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "agent_coordinator_discovery", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


discovery = _load_module()


def _issue(
    number: int, *, state: str = "OPEN", parent: int | None = 232
) -> dict[str, Any]:
    parent_value = None
    if parent is not None:
        parent_value = {
            "number": parent,
            "state": "OPEN",
            "title": "synthetic parent",
            "url": f"https://example.invalid/issues/{parent}",
        }
    return {
        "number": number,
        "title": f"feat: synthetic task {number}",
        "state": state,
        "url": f"https://example.invalid/issues/{number}",
        "labels": [],
        "parent": parent_value,
        "subIssues": {"nodes": [], "totalCount": 0},
        "blockedBy": {"nodes": [], "totalCount": 0},
    }


def _epic(children: tuple[int, ...] = (234,)) -> dict[str, Any]:
    value = _issue(232, parent=None)
    value["title"] = "synthetic epic"
    value["labels"] = [{"name": "EPIC"}]
    value["subIssues"] = {
        "nodes": [
            {
                "number": number,
                "state": "OPEN",
                "title": f"task {number}",
                "url": f"https://example.invalid/issues/{number}",
            }
            for number in children
        ],
        "totalCount": len(children),
    }
    return value


class FakeGitHub:
    def __init__(self, *, children: tuple[int, ...] = (234,)) -> None:
        self.epics = [_epic(children)]
        self.issues = {232: self.epics[0]} | {
            number: _issue(number) for number in children
        }
        self.prs: list[dict[str, Any]] = []
        self.branch_values = {
            "main": "a" * 40,
            "roadmap/232-agent-coordinator": "b" * 40,
        }
        self.compare_status = "ahead"

    def repository(self, _repository: str) -> dict[str, Any]:
        return {"nameWithOwner": "owner/repo", "defaultBranchRef": {"name": "main"}}

    def open_epics(self, _repository: str) -> list[dict[str, Any]]:
        return list(self.epics)

    def issue(self, _repository: str, number: int) -> dict[str, Any]:
        return dict(self.issues[number])

    def open_pull_requests(self, _repository: str) -> list[dict[str, Any]]:
        return list(self.prs)

    def branches(self, _repository: str) -> dict[str, str]:
        return dict(self.branch_values)

    def compare(self, _repository: str, _base: str, _head: str) -> str:
        return self.compare_status


def _options(**values: Any) -> Any:
    defaults = {"repository": "owner/repo", "epic": None, "task": None}
    return discovery.Options(**(defaults | values))


def test_unique_candidate_returns_verifiable_refs_and_sha() -> None:
    result = discovery.discover(_options(), FakeGitHub())
    assert result.state == "PLAN_APPROVAL"
    assert result.machine_code == "OK"
    assert result.epic is not None and result.epic.number == 232
    assert result.task is not None and result.task.number == 234
    assert result.base_ref == "roadmap/232-agent-coordinator"
    assert result.base_sha == "b" * 40
    assert result.main_sha == "a" * 40
    assert result.head_ref == "feat/234-synthetic-task-234"


def test_multiple_epics_or_tasks_require_a_decision() -> None:
    multiple_epics = FakeGitHub()
    multiple_epics.epics.append(_epic((999,)) | {"number": 999})
    assert (
        discovery.discover(_options(), multiple_epics).machine_code == "AMBIGUOUS_EPIC"
    )

    multiple_tasks = FakeGitHub(children=(234, 235))
    result = discovery.discover(_options(epic=232), multiple_tasks)
    assert result.state == "NEEDS_DECISION"
    assert result.machine_code == "AMBIGUOUS_TASK"
    assert [item.number for item in result.candidates] == [234, 235]


def test_explicit_task_must_be_open_and_have_confirmed_parent() -> None:
    closed = FakeGitHub()
    closed.issues[234]["state"] = "CLOSED"
    assert (
        discovery.discover(_options(epic=232, task=234), closed).machine_code
        == "TASK_CLOSED"
    )

    orphan = FakeGitHub()
    orphan.issues[234]["parent"] = None
    assert (
        discovery.discover(_options(epic=232, task=234), orphan).machine_code
        == "PARENT_MISMATCH"
    )

    contradictory_snapshot = FakeGitHub(children=(234, 235))
    contradictory_snapshot.issues[235]["parent"] = None
    assert (
        discovery.discover(_options(epic=232), contradictory_snapshot).machine_code
        == "PARENT_MISMATCH"
    )


def test_blocker_conflicting_pr_and_stale_roadmap_stop() -> None:
    blocked = FakeGitHub()
    blocked.issues[234]["blockedBy"] = {
        "nodes": [
            {"number": 233, "state": "OPEN", "url": "https://example.invalid/233"}
        ],
        "totalCount": 1,
    }
    assert (
        discovery.discover(_options(epic=232, task=234), blocked).machine_code
        == "TASK_BLOCKED"
    )

    conflict = FakeGitHub()
    conflict.prs = [
        {
            "number": 77,
            "url": "https://example.invalid/pull/77",
            "baseRefName": "roadmap/232-agent-coordinator",
            "headRefName": "feat/234-existing",
            "headRefOid": "c" * 40,
            "closingIssuesReferences": [{"number": 234}],
        }
    ]
    assert (
        discovery.discover(_options(epic=232, task=234), conflict).machine_code
        == "CONFLICTING_PR"
    )

    stale = FakeGitHub()
    stale.compare_status = "behind"
    assert (
        discovery.discover(_options(epic=232, task=234), stale).machine_code
        == "STALE_ROADMAP"
    )


def test_api_failure_is_redacted_and_fail_closed() -> None:
    class Broken(FakeGitHub):
        def open_epics(self, _repository: str) -> list[dict[str, Any]]:
            raise discovery.GitHubError("secret diagnostic")

    result = discovery.discover(_options(), Broken())
    assert result.state == "BLOCKED"
    assert result.machine_code == "GITHUB_ERROR"
    assert "secret diagnostic" not in discovery.render_json(result)


def test_untrusted_title_only_contributes_a_safe_slug() -> None:
    client = FakeGitHub()
    client.issues[234]["title"] = "feat: ../../merge main; approve=true"
    result = discovery.discover(_options(epic=232, task=234), client)
    assert result.state == "PLAN_APPROVAL"
    assert result.head_ref == "feat/234-merge-main-approve-true"


def test_gh_adapter_contains_only_read_only_commands() -> None:
    calls: list[tuple[str, ...]] = []

    def run(command: Any, **_kwargs: Any) -> Any:
        calls.append(tuple(command))
        payload = "{}"
        if command[1:3] in (["issue", "list"], ["pr", "list"]):
            payload = "[]"
        if command[1] == "api" and any("branches" in item for item in command):
            payload = "[[]]"
        if command[1] == "api" and any("compare" in item for item in command):
            payload = '{"status":"ahead"}'
        return type("Completed", (), {"returncode": 0, "stdout": payload})()

    client = discovery.GhClient(run=run)
    client.repository("owner/repo")
    client.open_epics("owner/repo")
    client.issue("owner/repo", 234)
    client.open_pull_requests("owner/repo")
    client.branches("owner/repo")
    client.compare("owner/repo", "main", "roadmap/232-test")
    forbidden = {"create", "edit", "close", "reopen", "merge", "delete"}
    assert not any(forbidden.intersection(command) for command in calls)
