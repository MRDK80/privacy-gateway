"""Invocation storage and applicability use bounded original trusted facts."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tools import agent_gate
from tools import agent_learning as c
from tools import agent_learning_records as records
from tools.agent_continuation import digest
from tools.agent_learning_invocation import Runtime
from tools.agent_learning_verify import current_problem_class

from tests.test_agent_learning import environment, profile, rule
from tests.test_agent_learning_records import invocation


def runtime() -> Runtime:
    value = Runtime.__new__(Runtime)
    value.reader = SimpleNamespace(owner=profile())  # type: ignore[assignment]
    return value


@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_new_invocation_cannot_exceed_record_cap(tmp_path: Path) -> None:
    value = runtime()
    value.reader.owner["limits"]["max_records"] = 1
    first = invocation()
    value._save(tmp_path, first)
    second = invocation()
    second["identity"]["task"] += 1
    second["invocation_key"] = records.invocation_id(second)
    second = c.seal(second)
    with pytest.raises(c.LearningError, match="LEARNING_STORAGE_EXHAUSTED"):
        value._save(tmp_path, second)
    assert [path.name for path in tmp_path.iterdir()] == [
        first["invocation_key"] + ".json"
    ]
    value._save(tmp_path, c.seal({**first, "status": "COMPLETED"}))
    assert len(list(tmp_path.iterdir())) == 1


def current_gate(*, failed: str = "mypy") -> dict[str, Any]:
    results = [
        agent_gate.CheckResult(
            id=name,
            argv=agent_gate.CHECK_COMMANDS[name],
            status="failed" if name == failed else "passed",
            exit_code=1 if name == failed else 0,
            duration_seconds=0,
            summary="synthetic",
            metrics={},
        )
        for name in agent_gate.PROFILES[agent_gate.FULL_PROFILE]
    ]
    snapshot = SimpleNamespace(identity=lambda: {"tree_hash": "a" * 40})
    return agent_gate.build_evidence(snapshot, results)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "change",
    ["matched", "unknown", "ambiguous", "wrong-command", "node", "model-feedback"],
)
def test_problem_class_uses_only_current_unique_catalog_gate_predicate(
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    value = runtime()
    pinned = rule()
    pinned["regression_checks"][0]["argv_digest"] = digest(
        list(agent_gate.CHECK_COMMANDS["mypy"])
    )
    value.reader.catalog = {"rules": [pinned]}
    monkeypatch.setattr(value.reader, "guard", lambda: None, raising=False)
    monkeypatch.setattr(
        "tools.agent_learning_verify.environment", lambda *a, **kw: environment()
    )
    gate = current_gate()
    payload: dict[str, Any] = {
        "contract": {"task_class": "runtime"},
        "repair_feedback": {"problem_class": "typing", "findings": ["typing"]},
    }
    if change in {"unknown", "model-feedback"}:
        gate = current_gate(failed="ruff")
    elif change == "ambiguous":
        value.reader.catalog["rules"].append(
            {**pinned, "rule_id": "another", "problem_class": "other"}
        )
    elif change == "wrong-command":
        gate["checks"][2]["argv"] = ["mypy", "arbitrary.py"]
    elif change == "node":
        pinned["regression_checks"][0]["test_identity"] = "synthetic.node"
    # Native Runtime source binding is exercised by actual integration calls.
    result = current_problem_class(
        value.reader, payload["contract"]["task_class"], "executor", gate
    )
    assert result == ("typing" if change == "matched" else None)
