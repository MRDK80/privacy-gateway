"""Planning source-family promotion; delivery proof stays exact and independent."""

from __future__ import annotations

from typing import Any

from tools import agent_gate
from tools import agent_learning as core
from tools import agent_learning_planning_catalog as catalog
from tools import agent_learning_planning_records as records
from tools import agent_learning_records as workflow_records
from tools.agent_continuation import digest
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_session_runtime import SessionRuntime
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_planning_sources import replay
from tools.agent_learning_sources import environment
from tools.agent_learning_store import Store

VALIDATORS = {
    "planning_observation": records.observation,
    "planning_candidate": records.candidate,
    "planning_verification": records.verification,
    "tombstone": workflow_records.tombstone,
}


def store(reader: SourceReader) -> Store:
    reader.guard()
    RunnerLock.current_owned(reader.seed)
    return Store(
        reader.seed / "planning-learning",
        reader.root,
        reader.seed,
        reader.owner,
        validators=VALIDATORS,
    )


def observation(source: dict[str, Any]) -> dict[str, Any]:
    value = source["validation"]
    return records.observation(
        core.seal(
            {
                "schema_version": "1.0",
                "kind": "PLANNING_VALIDATION",
                **{
                    key: value[key]
                    for key in (
                        "identity",
                        "role",
                        "ordinal",
                        "outcome",
                        "machine_code",
                        "request_digest",
                        "response_digest",
                        "applicability_digest",
                    )
                },
                "validation_digest": digest(value),
            }
        )
    )


def observe(reader: SourceReader, task: int, role: str, ordinal: int) -> dict[str, Any]:
    value = observation(replay(reader, task, role, ordinal))
    with store(reader) as retained:
        retained.append("planning_observation", value, "record_digest")
    return value


def retained(reader: SourceReader) -> list[dict[str, Any]]:
    directory = reader.seed / "planning-learning"
    if not directory.exists() and not directory.is_symlink():
        return []
    with store(reader) as current:
        current.ensure_usable()
        return current.records()


def pair(
    reader: SourceReader,
    session: SessionRuntime,
    before: dict[str, Any],
    after: dict[str, Any],
    rule: dict[str, Any],
) -> dict[str, Any] | None:
    original = _pair_inputs(reader, session, before, after, rule)
    if original is None:
        return None
    before, after, role = original
    first = replay(reader, before["identity"]["task"], role, before["ordinal"])
    last = replay(reader, after["identity"]["task"], role, after["ordinal"])
    return _pair_complete(reader, session, before, after, rule, first, last)


def _pair_inputs(
    reader: SourceReader,
    session: SessionRuntime,
    before: dict[str, Any],
    after: dict[str, Any],
    rule: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    reader.guard()
    if (
        not isinstance(session, SessionRuntime)
        or session.state != reader.seed
        or session.root.resolve() != reader.root
        or dict(session.config) != reader.config
        or rule not in reader.planning_catalog["rules"]
    ):
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    before, after = records.observation(before), records.observation(after)
    catalog.catalog({"schema_version": "1.0", "rules": [rule]})
    role = "planner" if rule["role"] == "planner" else "planning_review"
    if (
        before["outcome"] != "FAIL"
        or after["outcome"] != "PASS"
        or before["machine_code"] != rule["failure_code"]
        or before["role"] != role
        or after["role"] != role
        or before["identity"]["task"] == after["identity"]["task"]
        or rule["environment"] != environment(reader, planning=True)
    ):
        return None
    return before, after, role


def _pair_complete(
    reader: SourceReader,
    session: SessionRuntime,
    before: dict[str, Any],
    after: dict[str, Any],
    rule: dict[str, Any],
    first: dict[str, Any],
    last: dict[str, Any],
) -> dict[str, Any] | None:
    role = "planner" if rule["role"] == "planner" else "planning_review"
    if observation(first) != before or observation(last) != after:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")
    if (
        first["facts"] != last["facts"]
        or first["validation"]["check_identity"] != last["validation"]["check_identity"]
        or last["request"]["task_profile"]["task_class"] != rule["task_class"]
    ):
        return None
    task = after["identity"]["task"]
    controller = reader.controller(task)
    if controller[
        "status"
    ] not in core.SUCCESS or not agent_gate.gate_evidence_is_passing(
        controller["gate"]
    ):
        return None
    delivery = reader.delivery(task)
    pr_ci, post_ci = reader.ci(task, "pr"), reader.ci(task, "post-merge")
    demo = reader.demo(task, session)
    workflow = reader.workflow(task)
    # Successful delivery belongs to the exact corrected original plan/criteria.
    proposed = (
        last["request"]["proposal"] if role == "planning_review" else last["response"]
    )
    plan = proposed["plan"]
    if (
        list(workflow.contract.acceptance_criteria) != last["request"]["criteria"]
        or workflow.contract.base_sha != plan["base_sha"]
        or workflow.memory["head_sha"] != plan["base_sha"]
        or list(workflow.contract.allowed_paths) != plan["allowed_paths"]
        or workflow.contract.task_class != rule["task_class"]
    ):
        return None
    return {
        "before_digest": digest(before),
        "after_digest": digest(after),
        "applicability_digest": last["validation"]["applicability_digest"],
        "check_identity_digest": last["validation"]["check_identity"],
        "evidence_digest": digest(
            {
                "first": first,
                "last": last,
                "workflow": {"state": workflow.state, "memory": workflow.memory},
                "controller": controller,
                "delivery": delivery,
                "pr_ci": pr_ci,
                "post_ci": post_ci,
                "demo": demo,
            }
        ),
    }


def promote(
    reader: SourceReader, session: SessionRuntime, task: int
) -> list[dict[str, Any]]:
    facts = [
        item["record"]
        for item in retained(reader)
        if item["kind"] == "planning_observation"
    ]
    results: list[dict[str, Any]] = []
    for rule in reader.planning_catalog["rules"]:
        for after in facts:
            if after["identity"]["task"] != task:
                continue
            for before in facts:
                proof = pair(reader, session, before, after, rule)
                if proof is None:
                    continue
                candidate = {
                    "schema_version": "1.0",
                    "rule_id": rule["rule_id"],
                    "role": rule["role"],
                    "source_kind": "PLANNING_VALIDATION",
                    "validator": rule["validator"],
                    "observation_digest": proof["after_digest"],
                    **{
                        key: proof[key]
                        for key in (
                            "applicability_digest",
                            "evidence_digest",
                            "check_identity_digest",
                        )
                    },
                }
                candidate["candidate_id"] = digest(candidate)
                candidate = records.candidate(core.seal(candidate))
                verified = {
                    "schema_version": "1.0",
                    "candidate_digest": digest(candidate),
                    "catalog_digest": reader.owner["catalog_digest"],
                    **{
                        key: candidate[key]
                        for key in (
                            "source_kind",
                            "validator",
                            "observation_digest",
                            "applicability_digest",
                            "evidence_digest",
                            "check_identity_digest",
                        )
                    },
                }
                verified["lesson_id"] = digest(verified)
                verified = records.verification(core.seal(verified))
                with store(reader) as current:
                    current.append("planning_candidate", candidate, "candidate_id")
                    current.append("planning_verification", verified, "lesson_id")
                results.append(verified)
    return results
