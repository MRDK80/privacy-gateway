"""Read-only current relevance origin bound to original Admission and fresh guards."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools import agent_epic_planning as admission
from tools import agent_learning as core
from tools import agent_learning_planning_predicates as predicates
from tools import agent_orchestrate as workflow
from tools.agent_epic_handover import MandateContext
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_runtime_cli import canonical_digest
from tools.agent_learning_evidence import SourceReader


def current_facts(
    reader: SourceReader,
    role: str,
    request: dict[str, Any],
    context: MandateContext,
    *,
    guard: Callable[[], None],
) -> dict[str, Any]:
    guard()
    reader.guard()
    RunnerLock.current_owned(reader.seed)
    if role not in {"planner", "planning_review"}:
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    owner = admission.profile(
        reader._json(reader.seed / "planning-profile.json"),
        approved=reader.config["approved_planning_digest"],
        mandate=reader.mandate,
    )
    identity = request["identity"]
    task = core.integer(identity["task"], 2147483647, 1)
    expected_identity = {
        "schema_version": "1.0",
        "repository": reader.owner["repository"],
        "epic": reader.owner["epic"],
        "task": task,
        "planning_digest": canonical_digest(owner),
        "mandate_digest": reader.owner["mandate_digest"],
        "policy_sha": reader.owner["policy_sha"],
    }
    if (
        identity != expected_identity
        or task not in reader.config["approved_order"]
        or request["task_profile"] != owner["tasks"][str(task)]
        or request["grant"] != reader.mandate["task_grants"][str(task)]
        or request["limits"] != owner["limits"]
        or context.owner_identity != reader.owner["owner_identity"]
    ):
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    evidence = request["evidence"]
    if evidence["issue_body_digest"] != admission.body_digest(request["issue_body"]):
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    logical = "planner" if role == "planner" else "review"
    limit_key = (
        "max_planner_attempts" if logical == "planner" else "max_review_attempts"
    )
    source = admission.Admission(reader.seed, reader.root)._storage(task)
    # Before its first reservation Admission has not persisted admission.json.
    # Selection never creates it or invents consumed attempts. Existing state
    # must still be read and checked; symlinks/errors cannot mean first call.
    path = source / "admission.json"
    if path.exists() or path.is_symlink():
        saved = reader._json(path)
        admission.exact(saved, admission.RECORD_KEYS)
        if (
            saved["pending"] is not None
            or saved["status"] != "RESERVED"
            or any(saved[key] != item for key, item in identity.items())
            or any(saved[key] != item for key, item in evidence.items())
        ):
            raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
        admission.exact(saved["attempts"], {"planner", "review"})
        for name, key in [
            ("planner", "max_planner_attempts"),
            ("review", "max_review_attempts"),
        ]:
            core.integer(saved["attempts"][name], owner["limits"][key])
        if saved["attempts"][logical] >= owner["limits"][limit_key]:
            raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
        if role == "planning_review" and saved["proposal"] != {
            "digest": canonical_digest(request["proposal"]),
            "response": request["proposal"],
        }:
            raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    elif role == "planning_review":
        raise core.LearningError("LEARNING_SOURCE_MISSING")
    if (
        context.started_at > context.now
        or context.task_iterations > reader.mandate["limits"]["max_task_iterations"]
        or context.follow_up_issues > reader.mandate["limits"]["max_follow_up_issues"]
    ):
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    remaining = min(
        reader.mandate["expires_at"] - context.now,
        reader.mandate["limits"]["max_duration_seconds"]
        - (context.now - context.started_at),
    )
    if remaining <= 0 or request["remaining_seconds"] != remaining:
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    if workflow._git(reader.root, "status", "--porcelain=v1", "-z"):
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    head = workflow._git(reader.root, "rev-parse", "HEAD")
    tree = workflow._git(reader.root, "rev-parse", head + "^{tree}")
    if role == "planning_review":
        admission.validate_proposal(
            request["proposal"],
            owner=owner,
            mandate=reader.mandate,
            context=context,
            root=reader.root,
            task=task,
            body=request["issue_body"],
            base=request["evidence"]["base_sha"],
            default=request["evidence"]["default_sha"],
        )
    guard()
    reader.guard()
    return predicates.facts(request, head_sha=head, tree_hash=tree)
