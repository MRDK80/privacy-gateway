"""Original planning validation replay; never invokes a provider or clears pending."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import fields
from pathlib import Path
from typing import Any

from tools import agent_epic_handover as handover
from tools import agent_epic_loop as loop
from tools import agent_epic_planning as admission
from tools import agent_learning as core
from tools import agent_learning_hints as hints
from tools import agent_learning_planning_predicates as predicates
from tools import agent_learning_planning_records as records
from tools import agent_learning_records as invocation_records
from tools import agent_orchestrate as workflow
from tools.agent_continuation import digest
from tools.agent_epic_runtime_cli import canonical_digest
from tools.agent_learning_evidence import SourceReader


def workspace(raw: Any) -> dict[str, Any]:
    value = core.sealed(raw, {"identity", "role", "ordinal", "head_sha", "tree_hash"})
    records.identity(value["identity"])
    if (
        not isinstance(value["role"], str)
        or value["role"] not in records.PLANNING_ROLES
    ):
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    core.integer(value["ordinal"], 3, 1)
    core.hash_value(value["head_sha"], size=40)
    core.hash_value(value["tree_hash"], size=40)
    return value


def capture_workspace(
    reader: SourceReader, identity: dict[str, Any], role: str, ordinal: int
) -> dict[str, Any]:
    if workflow._git(reader.root, "status", "--porcelain=v1", "-z"):
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    head = workflow._git(reader.root, "rev-parse", "HEAD")
    tree = workflow._git(reader.root, "rev-parse", head + "^{tree}")
    return workspace(
        core.seal(
            {
                "schema_version": "1.0",
                "identity": identity,
                "role": role,
                "ordinal": ordinal,
                "head_sha": head,
                "tree_hash": tree,
            }
        )
    )


def _git(reader: SourceReader, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=reader.root,
            capture_output=True,
            timeout=30,
            check=False,
            env=workflow._snapshot_environment(GIT_OPTIONAL_LOCKS="0"),
        )
    except (OSError, subprocess.SubprocessError):
        raise core.LearningError("LEARNING_SOURCE_INVALID") from None
    if (
        completed.returncode
        or len(completed.stdout) > reader.owner["limits"]["max_store_bytes"]
    ):
        raise core.LearningError("LEARNING_SOURCE_INVALID")
    return completed.stdout


def _object(reader: SourceReader, oid: str, kind: str) -> bytes:
    core.hash_value(oid, size=40)
    raw = _git(reader, "cat-file", kind, oid)
    header = f"{kind} {len(raw)}\0".encode()
    if hashlib.sha1(header + raw).hexdigest() != oid:
        raise core.LearningError("LEARNING_SOURCE_INVALID")
    return raw


def verify_workspace(reader: SourceReader, value: dict[str, Any]) -> None:
    value = workspace(value)
    commit = _object(reader, value["head_sha"], "commit")
    if not commit.startswith(("tree " + value["tree_hash"] + "\n").encode()):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    # Each reachable tree and blob is bounded and byte-verified before checkout.
    pending: list[tuple[str, str]] = [(value["tree_hash"], "tree")]
    seen: set[str] = set()
    size = 0
    limits = reader.owner["limits"]
    while pending:
        oid, kind = pending.pop()
        if oid in seen:
            continue
        if len(seen) >= limits["max_records"]:
            raise core.LearningError("LEARNING_SOURCE_INVALID")
        seen.add(oid)
        raw = _object(reader, oid, kind)
        size += len(raw)
        if size > limits["max_store_bytes"]:
            raise core.LearningError("LEARNING_SOURCE_INVALID")
        if kind == "tree":
            offset = 0
            while offset < len(raw):
                marker = raw.find(b"\0", offset)
                if marker < 0 or marker + 21 > len(raw):
                    raise core.LearningError("LEARNING_SOURCE_INVALID")
                entry = raw[offset:marker]
                mode, _, name = entry.partition(b" ")
                if not name or b"/" in name or name in {b".", b".."}:
                    raise core.LearningError("LEARNING_SOURCE_INVALID")
                if mode not in {b"40000", b"100644", b"100755", b"120000"}:
                    raise core.LearningError("LEARNING_SOURCE_INVALID")
                child = raw[marker + 1 : marker + 21].hex()
                pending.append((child, "tree" if mode == b"40000" else "blob"))
                offset = marker + 21
                if len(pending) + len(seen) > limits["max_records"]:
                    raise core.LearningError("LEARNING_SOURCE_INVALID")


def replay(reader: SourceReader, task: int, role: str, ordinal: int) -> dict[str, Any]:
    """Bound recursive provenance by canonical identity and existing record limits."""
    reader.guard()
    identity = (task, role, ordinal)
    stack = reader.planning_replay_stack
    if identity in stack or len(stack) >= reader.owner["limits"]["max_records"]:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    stack.add(identity)
    try:
        return _replay(reader, task, role, ordinal)
    finally:
        stack.remove(identity)


def _replay(reader: SourceReader, task: int, role: str, ordinal: int) -> dict[str, Any]:
    reader.guard()
    if (
        role not in records.PLANNING_ROLES
        or task not in reader.config["approved_order"]
    ):
        raise core.LearningError("LEARNING_PLANNING_SOURCE_INVALID")
    core.integer(ordinal, 3, 1)
    source = admission.Admission(reader.seed, reader.root)._storage(task)
    directory = source / "learning-validation"
    prefix = f"{role}-{ordinal}-"
    request_record = records.artifact(
        reader._json(directory / (prefix + "request.json"))
    )
    response_record = records.artifact(
        reader._json(directory / (prefix + "response.json"))
    )
    context_record = records.context(
        reader._json(directory / (prefix + "context.json"))
    )
    validation = records.validation(
        reader._json(directory / (prefix + "validation.json"))
    )
    original_workspace = workspace(
        reader._json(directory / (prefix + "workspace.json"))
    )
    identity = validation["identity"]
    for record in (request_record, response_record, context_record, original_workspace):
        if any(
            record[key] != validation[key] for key in ("identity", "role", "ordinal")
        ):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    if request_record["kind"] != "REQUEST" or response_record["kind"] != "RESPONSE":
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    request, response = request_record["payload"], response_record["payload"]
    if (
        digest(request) != validation["request_digest"]
        or digest(response) != validation["response_digest"]
        or digest(request) != context_record["request_digest"]
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    owner = admission.profile(
        reader._json(reader.seed / "planning-profile.json"),
        approved=reader.config["approved_planning_digest"],
        mandate=reader.mandate,
    )
    expected_identity = {
        "repository": reader.owner["repository"],
        "epic": reader.owner["epic"],
        "task": task,
        "planning_digest": canonical_digest(owner),
        "mandate_digest": reader.owner["mandate_digest"],
        "policy_sha": reader.owner["policy_sha"],
    }
    if identity != expected_identity or request["identity"] != {
        "schema_version": "1.0",
        **identity,
    }:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    envelope = hints.envelope(
        request["untrusted_verified_hints"], reader.owner, role=role
    )
    metadata_identity = {
        "repository": reader.owner["repository"],
        "epic": reader.owner["epic"],
        "task": task,
        "policy_sha": reader.owner["policy_sha"],
        "owner_profile_digest": digest(reader.owner),
        "original_run_id": digest(request["identity"]),
    }
    invocation_key = invocation_records.invocation_id(
        {
            "identity": metadata_identity,
            "role": role,
            "ordinal": ordinal,
            "repair_iteration": 0,
        }
    )
    metadata = invocation_records.invocation(
        reader._json(source / "learning-invocations" / (invocation_key + ".json"))
    )
    original_request = {
        key: item for key, item in request.items() if key != "untrusted_verified_hints"
    }
    from tools.agent_learning_planning import prompt

    expected_metadata = {
        "identity": metadata_identity,
        "role": role,
        "ordinal": ordinal,
        "repair_iteration": 0,
        "catalog_digest": reader.owner["catalog_digest"],
        "selection_digest": envelope["selection_digest"],
        "lesson_ids": sorted(hint["lesson_id"] for hint in envelope["hints"]),
        "input_digest": digest(original_request),
        "prompt_digest": hashlib.sha256(
            prompt(reader.root, identity["policy_sha"], request).encode()
        ).hexdigest(),
    }
    if any(
        metadata[key] != item for key, item in expected_metadata.items()
    ) or metadata["status"] != (
        "COMPLETED" if validation["outcome"] == "PASS" else "RESERVED"
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    problem = records.problem_binding(
        reader._json(
            source / "learning-invocations" / (invocation_key + ".problem.json")
        )
    )
    if problem["invocation_key"] != invocation_key or problem["role"] != role:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    relevance = None
    if not envelope["hints"]:
        if problem["source_kind"] != "NONE":
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    else:
        if (
            len(envelope["hints"]) != 1
            or problem["source_kind"] != "PLANNING_VALIDATION"
            or problem["problem_class"] != envelope["hints"][0]["problem_class"]
        ):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
        relevance = records.relevance(
            reader._json(
                source / "learning-invocations" / (invocation_key + ".relevance.json")
            )
        )
        if problem["source_digest"] != digest(relevance) or relevance[
            "current_request_digest"
        ] != digest(request):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    original = reader._json(source / "admission.json")
    admission.exact(original, admission.RECORD_KEYS)
    short_role = "planner" if role == "planner" else "review"
    limit = owner["limits"][
        "max_planner_attempts" if role == "planner" else "max_review_attempts"
    ]
    count = core.integer(original["attempts"][short_role], limit, ordinal)
    if count < ordinal or any(
        original[key] != item for key, item in request["identity"].items()
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    if validation["outcome"] == "FAIL":
        if original["pending"] != {
            "role": short_role,
            "attempt": ordinal,
            "request_digest": canonical_digest(request),
        }:
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    elif (
        admission.Admission._response(
            original["proposal" if role == "planner" else "review"]
        )
        != response
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    if (
        role == "planning_review"
        and admission.Admission._response(original["proposal"]) != request["proposal"]
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    if (
        len(json.dumps(request, ensure_ascii=False))
        > owner["limits"]["max_input_chars"]
        or len(json.dumps(response, ensure_ascii=False))
        > owner["limits"]["max_output_chars"]
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    evidence = request["evidence"]
    if (
        evidence
        != {
            key: original[key]
            for key in ("issue_body_digest", "base_sha", "default_sha")
        }
        or evidence["issue_body_digest"] != admission.body_digest(request["issue_body"])
        or request["task_profile"] != owner["tasks"][str(task)]
        or request["grant"] != reader.mandate["task_grants"][str(task)]
        or request["limits"] != owner["limits"]
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    context = handover.MandateContext(
        **{
            field.name: context_record[field.name]
            for field in fields(handover.MandateContext)
        }
    )
    if (
        context.started_at != reader.config["started_at"]
        or context.follow_up_issues != reader.config["follow_up_issues"]
        or handover.mandate_lifecycle_code(reader.mandate, context) is not None
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    remaining = min(
        reader.mandate["expires_at"] - context.now,
        reader.mandate["limits"]["max_duration_seconds"]
        - (context.now - context.started_at),
    )
    if (
        type(request["remaining_seconds"]) is not int
        or request["remaining_seconds"] != remaining
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    normalized = predicates.facts(
        request,
        head_sha=original_workspace["head_sha"],
        tree_hash=original_workspace["tree_hash"],
    )
    if relevance is not None:
        from tools.agent_learning_sources import environment

        expected_relevance = {
            "criteria_digest": normalized["criteria_digest"],
            "grant_digest": normalized["grant_digest"],
            "profile_digest": normalized["profile_digest"].removeprefix("sha256:"),
            "environment_digest": digest(environment(reader, planning=True)),
            "policy_sha": normalized["policy_sha"],
        }
        if any(relevance[key] != item for key, item in expected_relevance.items()):
            raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    check = predicates.check_identity(role, normalized)
    if (
        validation["applicability_digest"] != digest(normalized)
        or validation["check_identity"] != check
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    verify_workspace(reader, original_workspace)
    # The original path checks see their original immutable tree, never live files.
    with tempfile.TemporaryDirectory(prefix="pgw-learning-planning-replay-") as scratch:
        clone = Path(scratch) / "checkout"
        _git(
            reader,
            "clone",
            "--quiet",
            "--no-hardlinks",
            "--no-checkout",
            str(reader.root),
            str(clone),
        )
        try:
            completed = subprocess.run(
                [
                    "git",
                    "checkout",
                    "--quiet",
                    "--detach",
                    original_workspace["head_sha"],
                ],
                cwd=clone,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise core.LearningError("LEARNING_SOURCE_INVALID") from None
        if completed.returncode:
            raise core.LearningError("LEARNING_SOURCE_INVALID")
        code = "OK"
        try:
            if role == "planner":
                admission.validate_proposal(
                    response,
                    owner=owner,
                    mandate=reader.mandate,
                    context=context,
                    root=clone,
                    task=task,
                    body=request["issue_body"],
                    base=evidence["base_sha"],
                    default=evidence["default_sha"],
                )
            else:
                proposal = admission.validate_proposal(
                    request["proposal"],
                    owner=owner,
                    mandate=reader.mandate,
                    context=context,
                    root=clone,
                    task=task,
                    body=request["issue_body"],
                    base=evidence["base_sha"],
                    default=evidence["default_sha"],
                )
                admission.validate_review(response, proposal, len(request["criteria"]))
        except loop.PhaseBlocked as error:
            code = error.machine_code
    if code != validation["machine_code"]:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    if relevance is not None:
        from tools.agent_learning_planning_history import verify

        verify(reader, role, envelope, relevance, normalized)
    return {
        "validation": validation,
        "request": request,
        "response": response,
        "context": context_record,
        "workspace": original_workspace,
        "facts": normalized,
    }
