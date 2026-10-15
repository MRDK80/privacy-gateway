"""The actual gate producer retains normalized facts, never its raw stdout."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.test_agent_learning_evidence import child, prepared

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX frozen gate source")


def test_actual_checker_callback_retains_failed_and_passing_facts(
    tmp_path: Path,
) -> None:
    value = prepared(tmp_path)
    body = """from tools import agent_gate
from tools.agent_learning_gate import GateJournal
from tools.agent_orchestrate import StateStore
session_args={'root':root,'base_sha':reader.mandate['policy_sha'],
              'allowed_paths':['code.py']}
store=StateStore(state/'bounded-workflow')
run='a'*32
store.save('b'*24,{'run_id':run,'executor_calls':1,'repair_iterations':0})
with agent_gate.trusted_snapshot_session(**session_args) as snapshot:
    journal=GateJournal(reader,301,run,1,0,snapshot)
    def runner(args,cwd,timeout):
        return agent_gate.CommandOutcome('failed',1,'Found 1 error')
    result=agent_gate.run_profile(snapshot,checks=['ruff'],runner=runner,
        on_check=journal)
paths=list((state/'learning-source/gate').glob('*.json'))
record=json.loads(paths[0].read_text())
print(json.dumps({'records':len(paths),'outcome':record['checks'][0]['outcome'],
    'name':record['checks'][0]['check_name'],'raw_stdout':'stdout' in record}))"""
    assert child(value, body) == {
        "records": 1,
        "outcome": "FAIL",
        "name": "ruff",
        "raw_stdout": False,
    }
    files = list((Path(value["state"]) / "learning-source/gate").glob("*.json"))
    assert json.loads(files[0].read_text())["checks"][0]["test_identity"] is None


def test_repository_gate_forwards_snapshot_bound_observer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import nullcontext

    from tools import agent_gate

    session = object()
    calls: list[object] = []

    def observer(result: agent_gate.CheckResult) -> None:
        pass

    monkeypatch.setattr(
        agent_gate, "trusted_snapshot_session", lambda **kw: nullcontext(session)
    )
    monkeypatch.setattr(agent_gate, "verify_environment", lambda *a, **kw: None)

    def profile(s: object, **kw: object) -> tuple[object, ...]:
        calls.append(kw.get("on_check"))
        return ()

    monkeypatch.setattr(agent_gate, "run_profile", profile)
    monkeypatch.setattr(agent_gate, "build_evidence", lambda *a: {})

    def factory(
        s: agent_gate.SnapshotSession,
    ) -> Callable[[agent_gate.CheckResult], None]:
        assert s is session
        return observer

    agent_gate.run_repository_full(
        root=Path("."),
        base_sha="a" * 40,
        allowed_paths=["code.py"],
        observer_factory=factory,
    )
    assert calls == [observer]
