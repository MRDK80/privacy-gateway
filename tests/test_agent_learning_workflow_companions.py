"""Actual native role calls see immutable applicability before transport."""

import os
from pathlib import Path

import pytest

from tests.test_agent_learning_evidence import child
from tests.test_agent_learning_integration import (
    DELIVERY,
    NEXT_CALL,
    SETUP,
    multi,
    roles,
)

BEFORE_TRANSPORT = """
from tools import agent_learning_planning_records as problem_records
task=payload['contract']['issue']
seed_task=private_json(state/'epic-loop-checkpoint.json')['task']
where=state if task==seed_task else state/'tasks'/str(task)
rows=[]
for file in (where/'learning-invocations').glob('*.json'):
    if file.name.endswith(('.problem.json','.relevance.json')): continue
    row=private_json(file)
    if row['role']==role and row['status']=='RESERVED': rows.append(row)
assert len(rows)==1,rows
row=rows[0]
companion=problem_records.problem_binding(private_json(where/'learning-invocations'/(row['invocation_key']+'.problem.json')))
assert companion['invocation_key']==row['invocation_key'] and companion['role']==role
hinted=bool(payload['untrusted_verified_hints']['hints'])
assert (companion['source_kind']=='SAME_RUN_GATE')==hinted
native=private_json(where/'continuation'/('reservation-'+digest(payload)+'.json'))
assert native['status']=='RESERVED' and native['snapshot_after'] is None
assert native['outcome_digest'] is None
assert not (state/'role-settings'/('receipt-'+digest(payload)+'.json')).exists()
"""


def checked_roles(value: dict[str, str]) -> None:
    roles(value)
    path = Path(value["state"]) / "synthetic-roles.py"
    original = path.read_text()
    marker = "payload=json.load(sys.stdin)\n"
    assert original.count(marker) == 1
    path.write_text(original.replace(marker, marker + BEFORE_TRANSPORT))


@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_actual_executor_controller_receive_problem_before_native_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = multi(tmp_path, monkeypatch, controller_rule=True)
    checked_roles(value)
    delivery = DELIVERY.replace("assert len(promoted)==1", "assert len(promoted)==2")
    next_call = NEXT_CALL.replace(
        "assert len(applied)==1 and applied[0]['role']=='executor'",
        "assert len(applied)==2\n"
        "assert {item['role'] for item in applied}=={'executor','controller'}\n"
        "applied=[item for item in applied if item['role']=='executor']",
    ).replace(
        "print(json.dumps({'completed_hinted_calls':len(applied),'executor_calls':2}))",
        "assert next_reviewed.adapter.learning_runtime.reader."
        "_current_pending_native is None\n"
        "print(json.dumps({'completed_hinted_calls':2,'executor_calls':2}))",
    )
    assert child(value, SETUP + delivery + next_call, timeout_seconds=120) == {
        "completed_hinted_calls": 2,
        "executor_calls": 2,
    }
