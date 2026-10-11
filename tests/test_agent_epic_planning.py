"""Synthetic admission failures and stable consumed role reservations (#298)."""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_handover as handover
from tools import agent_epic_loop as loop
from tools import agent_epic_planning as planning
from tools.agent_epic_runtime_cli import canonical_digest

BODY = (
    "## Scope\nSmall change.\n\n## Acceptance\n- First criterion\n- Second criterion\n"
)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def facts(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Synthetic")
    git(root, "remote", "add", "origin", "https://github.com/OWNER/repo.git")
    (root / "src").mkdir()
    (root / "src" / "example.py").write_text("VALUE=1\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "synthetic base")
    sha = git(root, "rev-parse", "HEAD")
    git(root, "branch", "roadmap/294-test")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    mandate: dict[str, Any] = {
        "schema_version": "3.0",
        "repository": "OWNER/repo",
        "epic": 294,
        "roadmap_ref": "roadmap/294-test",
        "policy_sha": sha,
        "issued_at": 100,
        "expires_at": 10000,
        "owner_identity": "OWNER",
        "limits": {
            "max_duration_seconds": 9000,
            "max_task_iterations": 5,
            "max_follow_up_issues": 1,
        },
        "revoked": False,
        "operations": ["create_local_task_branch", "build_task_handover"],
        "task_grants": {
            "298": {
                "causal_scope": ["small change"],
                "allowed_paths": ["src/example.py", "src/new.py"],
            }
        },
        "approval": {
            "mandate_digest": "pending",
            "approved_by": "OWNER",
            "approved_at": 100,
        },
    }
    mandate["approval"]["mandate_digest"] = handover.mandate_digest(mandate)
    template = {
        "schema_version": "1.0",
        "repository": "OWNER/repo",
        "epic": 294,
        "task": 298,
        "baseline_ref": "main",
        "baseline_sha": sha,
        "roadmap_ref": "roadmap/294-test",
        "roadmap_sha": sha,
        "consumer_visible": False,
        "not_applicable_reason": "Synthetic internal fixture",
        "scenario": None,
        "parameters": [],
        "old_argv": [],
        "new_argv": [],
    }
    owner = {
        "schema_version": "1.0",
        "repository": "OWNER/repo",
        "epic": 294,
        "roadmap_ref": "roadmap/294-test",
        "policy_sha": sha,
        "owner_identity": "OWNER",
        "mandate_digest": handover.mandate_digest(mandate),
        "tasks": {
            "298": {
                "criteria_sections": ["Acceptance"],
                "task_class": "internal",
                "demo_template": template,
            }
        },
        "limits": {
            "max_planner_attempts": 2,
            "max_review_attempts": 2,
            "max_input_chars": 100000,
            "max_output_chars": 20000,
            "max_paths": 4,
        },
    }
    plan = {
        "schema_version": "2.0",
        "repository": "OWNER/repo",
        "epic": 294,
        "task": 298,
        "policy_sha": sha,
        "criteria_source": {
            "kind": "github_issue",
            "issue": 298,
            "state": "OPEN",
            "body_sha256": planning.body_digest(BODY),
        },
        "acceptance_criteria": ["- First criterion", "- Second criterion"],
        "delivery_criterion_indices": [],
        "default_ref": "main",
        "default_sha": sha,
        "base_ref": "roadmap/294-test",
        "base_sha": sha,
        "head_ref": "codex/298-synthetic",
        "causal_scope": ["small change"],
        "allowed_paths": ["src/example.py"],
        "changes_policy_or_architecture": False,
        "task_class": "internal",
        "budgets": {
            "max_minutes": 5,
            "max_repair_iterations": 2,
            "max_report_chars": 10000,
        },
    }
    proposal = {
        "schema_version": "1.0",
        "plan": plan,
        "demo_plan": template,
        "criteria_mapping": [
            {
                "criterion_index": i,
                "allowed_paths": ["src/example.py"],
                "reason": "Minimal implementation",
            }
            for i in range(2)
        ],
    }
    return {
        "root": root,
        "state": state,
        "sha": sha,
        "mandate": mandate,
        "owner": owner,
        "proposal": proposal,
    }


def arguments(facts: dict[str, Any], body: str = BODY) -> dict[str, Any]:
    return {
        "owner": facts["owner"],
        "mandate": facts["mandate"],
        "context": handover.MandateContext(200, "OWNER", 150, 0, 0),
        "task": 298,
        "body": body,
        "base": facts["sha"],
        "default": facts["sha"],
    }


def roles(facts: dict[str, Any], calls: list[str]) -> Any:
    def invoke(role: str, request: dict[str, Any]) -> dict[str, Any]:
        calls.append(role)
        if role == "planner":
            proposal = copy.deepcopy(facts["proposal"])
            proposal["plan"]["criteria_source"]["body_sha256"] = planning.body_digest(
                request["issue_body"]
            )
            return dict(proposal)
        return {
            "schema_version": "1.0",
            "plan_digest": canonical_digest(request["proposal"]),
            "verdict": "PASS",
            "findings": [],
        }

    return invoke


@pytest.mark.skipif(os.name == "nt", reason="POSIX installed runtime storage")
def test_admission_runs_independent_review_and_reuses_bound_receipt(
    facts: dict[str, Any],
) -> None:
    calls: list[str] = []
    admission = planning.Admission(facts["state"], facts["root"])
    for _ in range(2):
        result = admission.admit(
            **arguments(facts), invoke=roles(facts, calls), guard=lambda: None
        )
        assert result["plan"]["acceptance_criteria"] == [
            "- First criterion",
            "- Second criterion",
        ]
    assert calls == ["planner", "review"]
    record = json.loads((facts["state"] / "planning/298/admission.json").read_text())
    assert record["attempts"] == {"planner": 1, "review": 1}
    assert record["status"] == "ADMITTED" and record["pending"] is None


@pytest.mark.parametrize(
    "body",
    [
        "## Acceptance\n- A\n## Acceptance\n- B",
        "## Acceptance\n- A\n  - nested",
        "## Acceptance\n```\nA\n```",
        "## Acceptance\n### Embedded\nA",
        "## Acceptance\n",
    ],
)
def test_ambiguous_or_unsupported_criteria_fail_closed(body: str) -> None:
    with pytest.raises(loop.PhaseBlocked):
        planning.extract_criteria(body, ["Acceptance"])


def test_criteria_preserve_source_order_and_continuations() -> None:
    assert planning.extract_criteria(
        "## Second\n- B\n  continuation\n\n## First\nA\n", ["First", "Second"]
    ) == ["- B\n  continuation", "A"]


@pytest.mark.parametrize("change", ["omitted", "permission", "scope", "self_review"])
def test_untrusted_proposal_cannot_omit_or_expand(
    facts: dict[str, Any], change: str
) -> None:
    proposal = copy.deepcopy(facts["proposal"])
    if change == "omitted":
        proposal["plan"]["acceptance_criteria"].pop()
    elif change == "permission":
        proposal["plan"]["permissions"] = {"merge": True}
    elif change == "scope":
        proposal["plan"]["allowed_paths"] = ["AGENTS.md"]
    else:
        proposal["review"] = {"verdict": "PASS"}
    with pytest.raises(loop.PhaseBlocked):
        planning.validate_proposal(proposal, **arguments(facts), root=facts["root"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX installed runtime storage")
def test_drift_consumes_attempts_without_refill(facts: dict[str, Any]) -> None:
    calls: list[str] = []
    admission = planning.Admission(facts["state"], facts["root"])
    for suffix in ("", "\n## Other\nOther source text"):
        admission.admit(
            **arguments(facts, BODY + suffix),
            invoke=roles(facts, calls),
            guard=lambda: None,
        )
    with pytest.raises(loop.PhaseBlocked, match="PLANNING_ATTEMPTS_EXHAUSTED"):
        admission.admit(
            **arguments(facts, BODY + "\n## Other\nThird drift"),
            invoke=roles(facts, calls),
            guard=lambda: None,
        )
    assert calls == ["planner", "review", "planner", "review"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX installed runtime storage")
def test_crashed_role_is_not_reinvoked_on_restart(facts: dict[str, Any]) -> None:
    admission = planning.Admission(facts["state"], facts["root"])

    def crash(*_args: Any) -> Any:
        raise TimeoutError("synthetic")

    with pytest.raises(TimeoutError):
        admission.admit(**arguments(facts), invoke=crash, guard=lambda: None)
    calls: list[str] = []
    with pytest.raises(loop.PhaseBlocked, match="PLANNING_PENDING_OUTCOME"):
        admission.admit(
            **arguments(facts), invoke=roles(facts, calls), guard=lambda: None
        )
    assert calls == []
    record = json.loads((facts["state"] / "planning/298/admission.json").read_text())
    assert record["attempts"]["planner"] == 1 and record["pending"]["role"] == "planner"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink/permissions fixture")
def test_symlinked_admission_directory_has_no_external_effect(
    facts: dict[str, Any], tmp_path: Path
) -> None:
    external = tmp_path / "external"
    external.mkdir(mode=0o755)
    (facts["state"] / "planning").symlink_to(external, target_is_directory=True)
    calls: list[str] = []
    with pytest.raises((loop.PhaseBlocked, loop.LoopError)):
        planning.Admission(facts["state"], facts["root"]).admit(
            **arguments(facts), invoke=roles(facts, calls), guard=lambda: None
        )
    assert list(external.iterdir()) == [] and calls == []
    assert external.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink/permissions fixture")
@pytest.mark.parametrize("operation", ["admit", "recipe"])
@pytest.mark.parametrize("name", ["admission.json", "epic-loop.lock"])
@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "permissions"])
def test_unsafe_admission_files_have_no_effect(
    facts: dict[str, Any], tmp_path: Path, name: str, unsafe: str, operation: str
) -> None:
    directory = facts["state"] / "planning" / "298"
    directory.mkdir(parents=True, mode=0o700)
    directory.parent.chmod(0o700)
    external = tmp_path / "external-file"
    external.write_text("{}")
    external.chmod(0o600)
    target = directory / name
    if unsafe == "symlink":
        target.symlink_to(external)
    elif unsafe == "hardlink":
        os.link(external, target)
    else:
        target.write_text("{}")
        target.chmod(0o644)
    calls: list[str] = []
    with pytest.raises((loop.PhaseBlocked, loop.LoopError)):
        admission = planning.Admission(facts["state"], facts["root"])
        if operation == "recipe":
            admission.recipe(298, canonical_digest(facts["owner"]))
        else:
            admission.admit(
                **arguments(facts), invoke=roles(facts, calls), guard=lambda: None
            )
    assert external.read_text() == "{}" and calls == []
    if unsafe == "permissions":
        assert target.stat().st_mode & 0o777 == 0o644
    assert sorted(path.name for path in directory.iterdir()) == [name]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions fixture")
def test_unsafe_existing_directory_is_not_chmodded(facts: dict[str, Any]) -> None:
    directory = facts["state"] / "planning"
    directory.mkdir(mode=0o755)
    calls: list[str] = []
    with pytest.raises((loop.PhaseBlocked, loop.LoopError)):
        planning.Admission(facts["state"], facts["root"]).admit(
            **arguments(facts), invoke=roles(facts, calls), guard=lambda: None
        )
    assert list(directory.iterdir()) == [] and calls == []
    assert directory.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name == "nt", reason="POSIX installed runtime storage")
def test_admission_lock_excludes_concurrent_invocation(facts: dict[str, Any]) -> None:
    admission = planning.Admission(facts["state"], facts["root"])
    directory = admission._storage(298, create=True)
    calls: list[str] = []
    with admission._lock(directory):
        with pytest.raises(loop.LoopError, match="RUNNER_LOCKED"):
            planning.Admission(facts["state"], facts["root"]).admit(
                **arguments(facts), invoke=roles(facts, calls), guard=lambda: None
            )
    assert calls == [] and not (directory / "admission.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows runtime is unsupported")
def test_windows_admission_refuses_storage_before_effects(
    facts: dict[str, Any],
) -> None:
    calls: list[str] = []
    with pytest.raises(loop.PhaseBlocked, match="UNSAFE_RUNTIME_CONFIG"):
        planning.Admission(facts["state"], facts["root"]).admit(
            **arguments(facts), invoke=roles(facts, calls), guard=lambda: None
        )
    assert calls == [] and list(facts["state"].iterdir()) == []
