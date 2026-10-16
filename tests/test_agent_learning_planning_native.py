"""Original bounded planning calls through real synthetic native subprocesses."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.learning_planning_native_fixture import prepared
from tests.test_agent_learning_evidence import child

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX frozen native planning"
)

NATIVE_CALLS = r"""
from tools import agent_epic_planning as planning
from tools import agent_epic_runtime_cli as cli
from tools.agent_epic_planning_roles import PlanningRoles
from tools.agent_learning_planning import Planning
from tools.agent_learning_planning_sources import replay
session=cli.build_session(state,root,wake=True)
mandate,approved,context=session._fresh_authority(0)
owner=session._planning_owner(mandate)
invoke=PlanningRoles(root=root,policy_root=reader.policy,
    policy_sha=reader.owner['policy_sha'],learning_state=state)
body='## Acceptance\n- synthetic criterion\n'
results={}
for task in (302,304,303):
    learning=Planning(reader,session)
    code='OK'
    try:
        proposal=planning.Admission(state,root).admit(owner=owner,mandate=mandate,
            context=context,task=task,body=body,base=reader.owner['policy_sha'],
            default=reader.owner['policy_sha'],dependencies=[],invoke=invoke,
            guard=reader.guard,learning=learning)
    except planning.loop.PhaseBlocked as error:
        code=error.machine_code
    saved=reader._json(state/'planning'/str(task)/'admission.json')
    planner=replay(reader,task,'planner',1)
    result={'code':code,'planner':planner['validation']['outcome'],
        'attempts':saved['attempts'],'pending':saved['pending'] is not None}
    if saved['attempts']['review']:
        result['review']=replay(reader,task,'planning_review',1)['validation']['outcome']
    results[str(task)]=result
observed=[json.loads(line) for line in
    (state/'native-planning-observations.jsonl').read_text().splitlines()]
assert [(row['task'],row['role']) for row in observed]==[
    (302,'planner'),(304,'planner'),(304,'review'),(303,'planner'),(303,'review')]
assert all(not row['hinted'] for row in observed)
assert len({row['invocation_key'] for row in observed})==5
print(json.dumps(results))
"""


def test_native_validation_failure_and_correction_keep_original_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = prepared(tmp_path, monkeypatch)
    assert child(value, NATIVE_CALLS, timeout_seconds=120) == {
        "302": {
            "code": "PLANNING_CRITERIA_INCOMPLETE",
            "planner": "FAIL",
            "attempts": {"planner": 1, "review": 0},
            "pending": True,
        },
        "304": {
            "code": "PLANNING_REVIEW_INVALID",
            "planner": "PASS",
            "review": "FAIL",
            "attempts": {"planner": 1, "review": 1},
            "pending": True,
        },
        "303": {
            "code": "OK",
            "planner": "PASS",
            "review": "PASS",
            "attempts": {"planner": 1, "review": 1},
            "pending": False,
        },
    }
