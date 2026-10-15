"""Current planning relevance rejects unapproved request before transport."""

from pathlib import Path

import pytest

from tests.test_agent_learning_evidence import child
from tests.test_agent_learning_planning_sources import (
    PLANNING_SOURCE_CALL,
    planning_fixture,
)


@pytest.mark.parametrize("failure", [False, True])
def test_current_origin_before_original_reservation(
    tmp_path: Path, failure: bool
) -> None:
    value = planning_fixture(tmp_path)
    draft = "from tools.agent_learning_planning_current import current_facts"
    hook = "\n".join(
        [
            "namespace={}",
            "exec(" + repr(draft) + ", namespace)",
            "original_prepare=learning.prepare",
            "checked=[]",
            "def checked_prepare(role, request):",
            '    logical="planner" if role=="planner" else "planning_review"',
            '    facts=namespace["current_facts"](reader, logical, request, context,'
            "    guard=reader.guard)",
            '    assert facts["criterion_count"]==1',
            "    checked.append(logical)",
            "    return original_prepare(role,request)",
            "learning.prepare=checked_prepare",
        ]
    )
    body = PLANNING_SOURCE_CALL.replace("calls=[]", hook + "\ncalls=[]")
    result = child(value, f"failure={failure!r}\n" + body, timeout_seconds=60)
    assert result["calls"] == (["planner"] if failure else ["planner", "review"])
    assert result["pending"] is failure


@pytest.mark.parametrize(
    "mutation",
    [
        "request['grant']['allowed_paths']=['unapproved.py']",
        "request['identity']['policy_sha']='b'*40",
        "request['criteria']=['- changed criterion']",
        "request['task_profile']['task_class']='unapproved'",
    ],
)
def test_current_origin_rejects_unapproved_request_before_call(
    tmp_path: Path, mutation: str
) -> None:
    value = planning_fixture(tmp_path)
    draft = "from tools.agent_learning_planning_current import current_facts"
    hook = "\n".join(
        [
            "namespace={}",
            "exec(" + repr(draft) + ", namespace)",
            "original_prepare=learning.prepare",
            "def checked_prepare(role, request):",
            "    " + mutation,
            '    namespace["current_facts"](reader, "planner", request, context,'
            "    guard=reader.guard)",
            '    raise AssertionError("unapproved relevance must be blocked")',
            "learning.prepare=checked_prepare",
        ]
    )
    body = PLANNING_SOURCE_CALL.replace("calls=[]", hook + "\ncalls=[]")
    body = (
        body[: body.index('code="OK"')]
        + """try:
    planning.Admission(state,root).admit(owner=owner,mandate=mandate,context=context,
      task=301,body=body,base=base,default=base,dependencies=[],invoke=invoke,
      guard=reader.guard,learning=learning)
except Exception as error:
    code=getattr(error,"machine_code",None)
    assert code=="LEARNING_PLANNING_SOURCE_INVALID", repr(error)
    assert calls==[]
    print(json.dumps({"blocked":True,"calls":calls}))
else:
    raise AssertionError("unapproved request reached transport")
"""
    )
    result = child(value, "failure=False\n" + body, timeout_seconds=60)
    assert result == {"blocked": True, "calls": []}
