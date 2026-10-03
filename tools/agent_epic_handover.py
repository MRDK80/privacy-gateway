#!/usr/bin/env python3
"""Build one narrow coordinator handover from an approved epic mandate."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from tools import agent_coordinator_branch as branch
from tools import agent_coordinator_handover as coordinator_handover
from tools import agent_epic_policy_binding as binding

SCHEMA_VERSION = "1.0"
MANDATE_SCHEMA_VERSION = "2.0"
PINNED_MANDATE_SCHEMA_VERSION = "3.0"
MANDATE_FIELDS = {
    "schema_version",
    "repository",
    "epic",
    "roadmap_ref",
    "policy_sha",
    "issued_at",
    "expires_at",
    "owner_identity",
    "limits",
    "revoked",
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


@dataclass(frozen=True)
class MandateContext:
    now: int
    owner_identity: str
    started_at: int
    task_iterations: int
    follow_up_issues: int


def mandate_lifecycle_code(
    mandate: Mapping[str, Any], context: MandateContext, *, at_task_start: bool = True
) -> str | None:
    """Return the fail-closed lifecycle reason for one imminent side effect."""
    if mandate.get("schema_version") not in {
        MANDATE_SCHEMA_VERSION,
        PINNED_MANDATE_SCHEMA_VERSION,
    }:
        return "MANDATE_SCHEMA_UNSUPPORTED"
    limits = mandate.get("limits")
    approval = mandate.get("approval")
    if not isinstance(limits, Mapping) or set(limits) != {
        "max_duration_seconds",
        "max_task_iterations",
        "max_follow_up_issues",
    }:
        return "MANDATE_INVALID"
    if not isinstance(approval, Mapping) or set(approval) != {
        "mandate_digest",
        "approved_by",
        "approved_at",
    }:
        return "MANDATE_INVALID"
    integer_values = (
        mandate.get("issued_at"),
        mandate.get("expires_at"),
        approval.get("approved_at"),
        limits.get("max_duration_seconds"),
        limits.get("max_task_iterations"),
        limits.get("max_follow_up_issues"),
        context.now,
        context.started_at,
        context.task_iterations,
        context.follow_up_issues,
    )
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in integer_values
    ):
        return "MANDATE_INVALID"
    issued_at = cast(int, mandate["issued_at"])
    expires_at = cast(int, mandate["expires_at"])
    approved_at = cast(int, approval["approved_at"])
    duration = cast(int, limits["max_duration_seconds"])
    task_limit = cast(int, limits["max_task_iterations"])
    follow_up_limit = cast(int, limits["max_follow_up_issues"])
    if (
        issued_at < 0
        or expires_at <= issued_at
        or approved_at < issued_at
        or approved_at > expires_at
        or duration <= 0
        or task_limit <= 0
        or follow_up_limit < 0
        or context.started_at < issued_at
        or context.task_iterations < 0
        or context.follow_up_issues < 0
        or not isinstance(mandate.get("owner_identity"), str)
        or not mandate["owner_identity"]
        or mandate.get("revoked") not in {True, False}
    ):
        return "MANDATE_INVALID"
    if mandate["owner_identity"] != context.owner_identity:
        return "MANDATE_OWNER_MISMATCH"
    if approval["approved_by"] != context.owner_identity:
        return "MANDATE_APPROVAL_IDENTITY_MISMATCH"
    if mandate["revoked"] is True:
        return "MANDATE_REVOKED"
    if context.now < issued_at or context.now < approved_at:
        return "MANDATE_NOT_YET_VALID"
    if context.now >= expires_at:
        return "MANDATE_EXPIRED"
    if context.now - context.started_at >= duration:
        return "MANDATE_DURATION_LIMIT"
    if context.task_iterations > task_limit or (
        context.task_iterations == task_limit
        and (
            at_task_start or mandate["schema_version"] != PINNED_MANDATE_SCHEMA_VERSION
        )
    ):
        return "MANDATE_TASK_LIMIT"
    if context.follow_up_issues > follow_up_limit:
        return "MANDATE_FOLLOW_UP_LIMIT"
    return None


def _canonical_digest(value: Mapping[str, Any], excluded: str) -> str:
    payload = {key: item for key, item in value.items() if key != excluded}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def mandate_digest(value: Mapping[str, Any]) -> str:
    """Bind approval to every authority-bearing mandate field."""
    payload = dict(value)
    approval = payload.get("approval")
    if isinstance(approval, Mapping):
        payload["approval"] = {
            key: item for key, item in approval.items() if key != "mandate_digest"
        }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def migrate_pinned_mandate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Pure v2 -> v3 candidate; deliberately invalidate old owner approval."""
    mandate = _exact_mapping(value, MANDATE_FIELDS)
    if mandate["schema_version"] != MANDATE_SCHEMA_VERSION:
        raise PlanningError("MANDATE_SCHEMA_UNSUPPORTED")
    approval = _exact_mapping(
        mandate["approval"], {"mandate_digest", "approved_by", "approved_at"}
    )
    if approval["mandate_digest"] != mandate_digest(mandate):
        raise PlanningError("MANDATE_APPROVAL_MISMATCH")
    candidate = copy.deepcopy(dict(mandate))
    candidate["schema_version"] = PINNED_MANDATE_SCHEMA_VERSION
    candidate["approval"] = {
        "mandate_digest": "pending",
        "approved_by": "",
        "approved_at": 0,
    }
    return candidate


def migrate_pinned_plan(value: Mapping[str, Any], *, policy_sha: str) -> dict[str, Any]:
    """Pure candidate; migration does not infer a different legacy policy."""
    plan = _exact_mapping(value, PLAN_FIELDS)
    if plan["schema_version"] != SCHEMA_VERSION:
        raise PlanningError("PLAN_INVALID")
    if (
        plan["base_sha"] != policy_sha
        or re.fullmatch(r"[0-9a-f]{40}", policy_sha) is None
    ):
        raise PlanningError("MIGRATION_POLICY_MISMATCH")
    return copy.deepcopy(dict(plan)) | {
        "schema_version": "2.0",
        "policy_sha": policy_sha,
    }


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
    mandate_context: MandateContext,
) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    if not isinstance(mandate_value, Mapping) or mandate_value.get(
        "schema_version"
    ) not in {MANDATE_SCHEMA_VERSION, PINNED_MANDATE_SCHEMA_VERSION}:
        raise PlanningError("MANDATE_SCHEMA_UNSUPPORTED")
    mandate = _exact_mapping(mandate_value, MANDATE_FIELDS)
    pinned = mandate["schema_version"] == PINNED_MANDATE_SCHEMA_VERSION
    plan = _exact_mapping(
        plan_value, PLAN_FIELDS | ({"policy_sha"} if pinned else set())
    )
    digest = mandate_digest(mandate)
    lifecycle = mandate_lifecycle_code(mandate, mandate_context)
    if lifecycle is not None:
        raise PlanningError(lifecycle)
    approval = _exact_mapping(
        mandate["approval"], {"mandate_digest", "approved_by", "approved_at"}
    )
    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", approved_mandate_digest) is None
        or approval["mandate_digest"] != digest
        or approved_mandate_digest != digest
    ):
        raise PlanningError("MANDATE_APPROVAL_MISMATCH")

    repository, epic, task = plan["repository"], plan["epic"], plan["task"]
    if (
        plan["schema_version"] != ("2.0" if pinned else SCHEMA_VERSION)
        or repository != mandate["repository"]
        or epic != mandate["epic"]
        or plan["base_ref"] != mandate["roadmap_ref"]
        or (plan["policy_sha"] if pinned else plan["base_sha"]) != mandate["policy_sha"]
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
    mandate_context: MandateContext,
    before_create: Callable[[], None] | None = None,
) -> Result:
    """Validate authority, create its branch ref and return versioned handover."""
    try:
        mandate, plan, digest = _validate_inputs(
            mandate_value,
            plan_value,
            root=root,
            approved_mandate_digest=approved_mandate_digest,
            issue_body=issue_body,
            mandate_context=mandate_context,
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
        if mandate["schema_version"] == PINNED_MANDATE_SCHEMA_VERSION:
            try:
                binding.validate_live_binding(
                    {
                        "schema_version": binding.SCHEMA_VERSION,
                        "repository": plan["repository"],
                        "epic": plan["epic"],
                        "roadmap_ref": plan["base_ref"],
                        "base_sha": plan["base_sha"],
                        "policy_sha": mandate["policy_sha"],
                    },
                    repository_root=root,
                    repository=cast(str, plan["repository"]),
                    epic=cast(int, plan["epic"]),
                    roadmap_ref=cast(str, plan["base_ref"]),
                    policy_sha=cast(str, mandate["policy_sha"]),
                    branches=github.branches,
                )
            except binding.BindingError as error:
                raise PlanningError(str(error)) from None
        preflight = (
            branch.prepare_branch(options, github, before_create=before_create)
            if before_create is not None
            else branch.prepare_branch(options, github)
        )
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
        if mandate["schema_version"] == PINNED_MANDATE_SCHEMA_VERSION:
            handover["schema_version"] = "2.0"
            handover["trusted_policy"] = {
                "source": "pinned_policy_sha",
                "policy_sha": mandate["policy_sha"],
            }
        handover["approval"]["plan_digest"] = coordinator_handover.handover_digest(
            handover
        )
        return Result("HANDOVER", "OK", digest, handover)
    except PlanningError as error:
        return Result("NEEDS_DECISION", error.machine_code, "", None)


def render_json(result: Result) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True) + "\n"
