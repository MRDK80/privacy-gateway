"""Pinned empty catalog and forged flags never issue a role hint."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tools import agent_learning as c
from tools import agent_learning_records as records
from tools.agent_continuation import digest
from tools.agent_learning_verify import Verifier

from tests.test_agent_learning import environment, profile, rule
from tests.test_agent_learning_evidence import child, prepared
from tests.test_agent_learning_invocation import current_gate
from tests.test_agent_learning_records import observation


@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_empty_pinned_catalog_selects_no_hints(tmp_path: Path) -> None:
    value = prepared(tmp_path)
    body = """from tools.agent_epic_runtime_cli import build_session
from tools.agent_learning_verify import Verifier
session=build_session(state,root,wake=True)
value=Verifier(reader,session).selection('runtime','executor')
print(json.dumps({'hints':len(value['hints']),'role':value['role']}))"""
    assert child(value, body) == {"hints": 0, "role": "executor"}


@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_empty_catalog_never_promotes_supplied_pass_flags(tmp_path: Path) -> None:
    value = prepared(tmp_path)
    state = Path(value["state"])
    (state / "candidate-flags.json").write_text(
        json.dumps({"verified": True, "gate": "PASS"})
    )
    body = """from tools.agent_epic_runtime_cli import build_session
from tools.agent_learning_verify import Verifier
session=build_session(state,root,wake=True)
print(json.dumps({'promoted':len(Verifier(reader,session).candidates(301))}))"""
    assert child(value, body) == {"promoted": 0}


def paired_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Verifier, dict[str, Any], dict[str, Any], dict[str, Any]]:
    pinned = rule()
    before, after = observation(), observation(delivery=True)
    before["identity"]["task"] = 301
    before["record_id"] = records.observation_id(before)
    before = c.seal(before)
    gate = current_gate(failed="none")
    controller = {"status": "PASS", "gate": gate, "source_digest": "1" * 64}
    delivery = {
        "head_sha": after["identity"]["head_sha"],
        "merge_sha": after["identity"]["merge_sha"],
        "source_digest": "2" * 64,
    }
    facts = {}
    for observed in (before, after):
        task = observed["identity"]["task"]
        state = {
            **observed["metrics"],
            "run_id": observed["identity"]["run_id"],
            "status": "PASS" if task == 302 else "FAIL_ESCALATE",
            "failures": [] if task == 302 else ["GATE_FAILED"],
        }
        if task == 302:
            state["run_id"] = "f" * 32
            observed["identity"]["run_id"] = state["run_id"]
        memory = {
            "head_sha": observed["identity"]["head_sha"],
            "duration_seconds": observed["metrics"]["duration_seconds"],
        }
        facts[task] = SimpleNamespace(
            contract=SimpleNamespace(issue=task, task_class="runtime"),
            state=state,
            memory=memory,
        )
        observed["source"]["workflow_digest"] = digest(
            {"state": state, "memory": memory}
        )
        observed["record_id"] = records.observation_id(observed)
    after["source"].update(
        gate_digest=digest(gate),
        controller_digest=controller["source_digest"],
        delivery_digest=delivery["source_digest"],
        pr_ci_digest=digest({}),
        post_ci_digest=digest({}),
        demo_digest=digest({}),
    )
    before, after = c.seal(before), c.seal(after)
    identity = pinned["regression_checks"][0]
    rows = {
        301: [{"checks": [{**identity, "outcome": "FAIL"}]}],
        302: [
            {
                "checks": [{**identity, "outcome": "PASS"}],
                "snapshot_digest": digest(gate["snapshot"]),
            }
        ],
    }
    value = Verifier.__new__(Verifier)
    value.reader = SimpleNamespace(
        owner={**profile(), "policy_sha": before["identity"]["policy_sha"]},
        workflow=lambda task: facts[task],
        controller=lambda task: controller,
        delivery=lambda task: delivery,
        ci=lambda *a: {},
        demo=lambda *a: {},
        gate=lambda task: rows[task],
        catalog={"rules": [pinned]},
    )  # type: ignore[assignment]
    value.session = SimpleNamespace()  # type: ignore[assignment]
    value.applicability = lambda *a: "3" * 64  # type: ignore[method-assign]
    monkeypatch.setattr(
        "tools.agent_learning_verify.environment", lambda *a, **kw: environment()
    )
    assert value.pair(before, after, pinned) is not None
    return value, before, after, pinned


@pytest.mark.parametrize(
    "change",
    [
        "canonical-pass",
        "failure-code",
        "repository",
        "epic",
        "policy_sha",
        "head_sha",
        "findings",
        "repair_iterations",
        "executor_calls",
        "controller_calls",
        "duration_seconds",
        "after-repository",
        "after-policy",
        "after-metric",
        "after-status",
        "after-codes",
    ],
)
def test_pair_rejects_resealed_facts_that_disagree_with_original_sources(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    value, before, after, pinned = paired_verifier(monkeypatch)
    if change == "canonical-pass":
        fact = value.reader.workflow(301)
        fact.state.update(status="PASS", failures=[])
        before["source"]["workflow_digest"] = digest(
            {"state": fact.state, "memory": fact.memory}
        )
    elif change == "failure-code":
        fact = value.reader.workflow(301)
        fact.state["failures"] = ["OTHER_FAILURE"]
        before["source"]["workflow_digest"] = digest(
            {"state": fact.state, "memory": fact.memory}
        )
    elif change in {"repository", "epic", "policy_sha", "head_sha"}:
        before["identity"][change] = {
            "repository": "OTHER/repo",
            "epic": 293,
            "policy_sha": "0" * 40,
            "head_sha": "0" * 40,
        }[change]
    elif change.startswith("after-"):
        field = change.removeprefix("after-")
        if field == "repository":
            after["identity"]["repository"] = "OTHER/repo"
        elif field == "policy":
            after["identity"]["policy_sha"] = "0" * 40
        elif field == "metric":
            after["metrics"]["findings"] += 1
        elif field == "status":
            fact = value.reader.workflow(302)
            fact.state["status"] = "FAIL_ESCALATE"
            after["source"]["workflow_digest"] = digest(
                {"state": fact.state, "memory": fact.memory}
            )
        else:
            after["outcome"]["machine_codes"] = ["GATE_FAILED"]
    else:
        before["metrics"][change] += 1
    for observed in (before, after):
        observed["record_id"] = records.observation_id(observed)
    assert value.pair(c.seal(before), c.seal(after), pinned) is None


@pytest.mark.parametrize("problem", [None, "unrelated", "typing"])
def test_selection_requires_exact_current_problem_class(
    monkeypatch: pytest.MonkeyPatch, problem: str | None
) -> None:
    value, before, after, pinned = paired_verifier(monkeypatch)
    proof = value.pair(before, after, pinned)
    assert proof is not None
    candidate = {"rule_id": pinned["rule_id"]}
    verified = {
        "lesson_id": "4" * 64,
        "catalog_digest": value.reader.owner["catalog_digest"],
        "candidate_digest": digest(candidate),
        "observation_digest": digest(after),
        "record_digest": "5" * 64,
        "evidence_digest": proof["evidence_digest"],
        "applicability_digest": proof["applicability_digest"],
    }
    retained = [
        {"kind": "observation", "record": before},
        {"kind": "observation", "record": after},
        {"kind": "candidate", "record": {**candidate, "record_digest": "6" * 64}},
        {"kind": "verification", "record": verified},
    ]
    verified["candidate_digest"] = digest(retained[2]["record"])
    value.retained = lambda: retained  # type: ignore[method-assign]
    result = value.selection("runtime", "executor", problem)
    assert len(result["hints"]) == (1 if problem == "typing" else 0)


@pytest.mark.parametrize("reverse", [False, True])
def test_selection_orders_by_lesson_id_before_applying_hint_cap(
    monkeypatch: pytest.MonkeyPatch, reverse: bool
) -> None:
    value, before, after, pinned = paired_verifier(monkeypatch)
    rules = [{**pinned, "rule_id": f"typing-failure-{index}"} for index in range(8)]
    value.reader.catalog = c.catalog({"schema_version": "1.0", "rules": rules})
    value.reader.owner["catalog_digest"] = digest(value.reader.catalog)
    retained = [
        {"kind": "observation", "record": records.observation(before)},
        {"kind": "observation", "record": records.observation(after)},
    ]
    verifications = []
    for selected_rule in rules:
        # Exercise the real source-pair checks with the synthetic source reader.
        proof = value.pair(before, after, selected_rule)
        assert proof is not None
        candidate = {
            "schema_version": "1.0",
            "rule_id": selected_rule["rule_id"],
            "role": selected_rule["role"],
            "observation_digest": proof["after_digest"],
            "applicability_digest": proof["applicability_digest"],
            "evidence_digest": proof["evidence_digest"],
        }
        candidate["candidate_id"] = digest(candidate)
        candidate = records.candidate(c.seal(candidate))
        verified = {
            "schema_version": "1.0",
            "candidate_digest": digest(candidate),
            "catalog_digest": value.reader.owner["catalog_digest"],
            "observation_digest": proof["after_digest"],
            "evidence_digest": proof["evidence_digest"],
            "applicability_digest": proof["applicability_digest"],
        }
        verified["lesson_id"] = digest(verified)
        verified = records.verification(c.seal(verified))
        verifications.append(verified)
        retained.extend(
            [
                {"kind": "candidate", "record": candidate},
                {"kind": "verification", "record": verified},
            ]
        )
    cap = value.reader.owner["limits"]["max_hints_per_role"]
    expected_ids = sorted(item["lesson_id"] for item in verifications)[:cap]
    assert len(verifications) > cap == 3
    assert len({item["lesson_id"] for item in verifications}) == len(verifications)
    # Keep the fixture sensitive to choosing the wrong subset, not just order.
    digest_order = sorted(verifications, key=lambda item: item["record_digest"])
    assert {item["lesson_id"] for item in digest_order[:cap]} != set(expected_ids)
    if reverse:
        retained.reverse()
    value.retained = lambda: retained  # type: ignore[method-assign]
    expected_hints = [
        {
            "lesson_id": lesson_id,
            "trust": "UNTRUSTED_VERIFIED_HINT",
            "problem_class": pinned["problem_class"],
            "symptom_code": pinned["symptom_code"],
            "diagnostic_steps": pinned["diagnostic_steps"],
            "minimal_fix": pinned["minimal_fix"],
            "applicability": pinned["applicability"],
            "permissions": None,
        }
        for lesson_id in expected_ids
    ]
    result = value.selection("runtime", "executor", "typing")
    assert result["hints"] == expected_hints
    assert result["selection_digest"] == digest(expected_hints)
