#!/usr/bin/env python3
"""Build one narrow coordinator handover from an approved epic mandate."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from tools import agent_coordinator_branch as branch
from tools import agent_coordinator_handover as coordinator_handover

SCHEMA_VERSION = "1.0"
MANDATE_FIELDS = {
    "schema_version",
    "repository",
    "epic",
    "roadmap_ref",
    "policy_sha",
    "operations",
    "task_grants",
    "approval",
}
PLAN_FIELDS = {
    "schema_version",
    "repository",
    "epic",
    "task",
    "criteria_source",
    "acceptance_criteria",
    "delivery_criterion_indices",
    "default_ref",
    "default_sha",
    "base_ref",
    "base_sha",
    "head_ref",
    "causal_scope",
    "allowed_paths",
    "changes_policy_or_architecture",
    "budgets",
    "task_class",
}
REQUIRED_OPERATIONS = {"create_local_task_branch", "build_task_handover"}


@dataclass(frozen=True)
class Result:
    state: str
    machine_code: str
    mandate_digest: str
    handover: dict[str, Any] | None = None


class PlanningError(Exception):
    """Fail-closed planning result safe to expose as a machine code."""

    def __init__(self, machine_code: str) -> None:
        super().__init__(machine_code)
        self.machine_code = machine_code


def _canonical_digest(value: Mapping[str, Any], excluded: str) -> str:
    payload = {key: item for key, item in value.items() if key != excluded}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def mandate_digest(value: Mapping[str, Any]) -> str:
    """Bind approval to every authority-bearing mandate field."""
    return _canonical_digest(value, "approval")


def _exact_mapping(value: Any, fields: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise PlanningError("PLAN_INVALID")
    return cast(Mapping[str, Any], value)


def _strings(value: Any) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(
            isinstance(item, str) and item and item.strip() == item for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise PlanningError("PLAN_INVALID")
    return tuple(value)


def _safe_path(root: Path, raw: str) -> None:
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PlanningError("PATH_ESCAPE")
    candidate = root.joinpath(*path.parts)
    current = root
    for part in path.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise PlanningError("PATH_ESCAPE")
        if not current.exists() or not current.is_dir():
            raise PlanningError("UNCONFIRMED_DIRECTORY")
    if candidate.is_symlink():
        raise PlanningError("PATH_ESCAPE")
    try:
        common = os.path.commonpath((root.resolve(), candidate.resolve(strict=False)))
    except (OSError, ValueError) as error:
        raise PlanningError("PATH_ESCAPE") from error
    if Path(common) != root.resolve():
        raise PlanningError("PATH_ESCAPE")
    if candidate.exists() and not candidate.is_file():
        raise PlanningError("PLAN_INVALID")


def _validate_inputs(
    mandate_value: Any,
    plan_value: Any,
    *,
    root: Path,
    approved_mandate_digest: str,
    issue_body: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    mandate = _exact_mapping(mandate_value, MANDATE_FIELDS)
    plan = _exact_mapping(plan_value, PLAN_FIELDS)
    digest = mandate_digest(mandate)
    approval = _exact_mapping(mandate["approval"], {"mandate_digest"})
    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", approved_mandate_digest) is None
        or approval["mandate_digest"] != digest
        or approved_mandate_digest != digest
    ):
        raise PlanningError("MANDATE_APPROVAL_MISMATCH")

    repository, epic, task = plan["repository"], plan["epic"], plan["task"]
    if (
        mandate["schema_version"] != SCHEMA_VERSION
        or plan["schema_version"] != SCHEMA_VERSION
        or repository != mandate["repository"]
        or epic != mandate["epic"]
        or plan["base_ref"] != mandate["roadmap_ref"]
        or plan["base_sha"] != mandate["policy_sha"]
        or not isinstance(task, int)
        or isinstance(task, bool)
    ):
        raise PlanningError("MANDATE_IDENTITY_MISMATCH")
    operations = set(_strings(mandate["operations"]))
    if not REQUIRED_OPERATIONS.issubset(operations):
        raise PlanningError("MANDATE_AUTHORITY_MISSING")
    grants = mandate["task_grants"]
    if not isinstance(grants, Mapping) or str(task) not in grants:
        raise PlanningError("TASK_GRANT_MISSING")
    grant = _exact_mapping(grants[str(task)], {"causal_scope", "allowed_paths"})
    causal_scope = _strings(plan["causal_scope"])
    allowed_paths = _strings(plan["allowed_paths"])
    if not set(causal_scope).issubset(_strings(grant["causal_scope"])) or not set(
        allowed_paths
    ).issubset(_strings(grant["allowed_paths"])):
        raise PlanningError("PLAN_SCOPE_EXPANSION")
    if plan["changes_policy_or_architecture"] is not False:
        raise PlanningError("POLICY_DECISION_REQUIRED")
    protected = {"AGENTS.md", "CONTRIBUTING.md", "SECURITY.md", "docs/SECURITY.md"}
    if any(path in protected or path.startswith("docs/ADR-") for path in allowed_paths):
        raise PlanningError("POLICY_DECISION_REQUIRED")
    for path in allowed_paths:
        _safe_path(root, path)

    source = _exact_mapping(
        plan["criteria_source"], {"kind", "issue", "state", "body_sha256"}
    )
    expected_body_digest = "sha256:" + hashlib.sha256(issue_body.encode()).hexdigest()
    criteria = _strings(plan["acceptance_criteria"])
    if (
        source.get("kind") != "github_issue"
        or source.get("issue") != task
        or source.get("state") != "OPEN"
        or source.get("body_sha256") != expected_body_digest
        or any(item not in issue_body for item in criteria)
    ):
        raise PlanningError("CRITERIA_PROVENANCE_MISMATCH")
    return mandate, plan, digest


def prepare_handover(
    mandate_value: Any,
    plan_value: Any,
    *,
    root: Path,
    approved_mandate_digest: str,
    github: branch.GitHubClient,
    issue_body: str,
) -> Result:
    """Validate plan authority, create its branch ref, and return handover v1.0."""
    try:
        mandate, plan, digest = _validate_inputs(
            mandate_value,
            plan_value,
            root=root,
            approved_mandate_digest=approved_mandate_digest,
            issue_body=issue_body,
        )
        options = branch.Options(
            repository=cast(str, plan["repository"]),
            epic=cast(int, plan["epic"]),
            task=cast(int, plan["task"]),
            default_ref=cast(str, plan["default_ref"]),
            default_sha=cast(str, plan["default_sha"]),
            base_ref=cast(str, plan["base_ref"]),
            base_sha=cast(str, plan["base_sha"]),
            head_ref=cast(str, plan["head_ref"]),
            remote="origin",
            root=root,
            approved=True,
        )
        preflight = branch.prepare_branch(options, github)
        if preflight.machine_code != "OK":
            raise PlanningError(preflight.machine_code)

        permissions = {name: False for name in coordinator_handover.DENIED_ACTIONS}
        handover: dict[str, Any] = {
            "schema_version": coordinator_handover.SCHEMA_VERSION,
            "repository": plan["repository"],
            "epic": plan["epic"],
            "task": plan["task"],
            "criteria_source": plan["criteria_source"],
            "acceptance_criteria": plan["acceptance_criteria"],
            "delivery_criterion_indices": plan["delivery_criterion_indices"],
            "base_ref": plan["base_ref"],
            "base_sha": plan["base_sha"],
            "head_ref": plan["head_ref"],
            "head_sha": plan["base_sha"],
            "causal_scope": plan["causal_scope"],
            "allowed_paths": plan["allowed_paths"],
            "forbidden_actions": list(coordinator_handover.DENIED_ACTIONS),
            "permissions": permissions,
            "budgets": plan["budgets"],
            "required_gate": {"profile": "repository-full", "version": "1"},
            "trusted_policy": {"source": "base_sha", "base_sha": mandate["policy_sha"]},
            "task_class": plan["task_class"],
            "mandate_provenance": {
                "schema_version": mandate["schema_version"],
                "digest": digest,
                "policy_sha": mandate["policy_sha"],
            },
            "approval": {"plan_digest": "pending"},
        }
        handover["approval"]["plan_digest"] = coordinator_handover.handover_digest(
            handover
        )
        return Result("HANDOVER", "OK", digest, handover)
    except PlanningError as error:
        return Result("NEEDS_DECISION", error.machine_code, "", None)


def render_json(result: Result) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True) + "\n"
