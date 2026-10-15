"""Consumed planning prefix proof preserves original counters on source loss."""

from pathlib import Path

import pytest

from tests.test_agent_learning_evidence import child
from tests.test_agent_learning_planning_sources import (
    PLANNING_SOURCE_CALL,
    planning_fixture,
)


@pytest.mark.parametrize("failure", [False, True])
def test_verified_original_unhinted_prefix_preserves_attempts(
    tmp_path: Path, failure: bool
) -> None:
    value = planning_fixture(tmp_path)
    draft = "from tools.agent_learning_planning_barrier import planning_barrier"
    inserted = (
        "namespace={}\nexec("
        + repr(draft)
        + ',namespace)\nnamespace["planning_barrier"](learning.verifier,301)\n'
    )
    body = PLANNING_SOURCE_CALL.replace(
        "saved=reader._json", inserted + "saved=reader._json"
    )
    result = child(value, f"failure={failure!r}\n" + body, timeout_seconds=60)
    assert result["calls"] == (["planner"] if failure else ["planner", "review"])
    assert result["attempts"] == {"planner": 1, "review": 0 if failure else 1}
    assert result["pending"] is failure


@pytest.mark.parametrize("artifact", ["validation", "metadata"])
def test_missing_original_consumed_prefix_blocks_barrier(
    tmp_path: Path, artifact: str
) -> None:
    value = planning_fixture(tmp_path)
    draft = "from tools.agent_learning_planning_barrier import planning_barrier"
    body = PLANNING_SOURCE_CALL[: PLANNING_SOURCE_CALL.index("source=replay")]
    body += """
namespace={}
exec(DRAFT,namespace)
source=state/'planning/301'
if ARTIFACT=='validation':
    path=source/'learning-validation/planner-1-validation.json'
else:
    paths=list((source/'learning-invocations').glob('*.json'))
    path=next(p for p in paths if json.loads(p.read_text())['role']=='planner')
path.unlink()
before=reader._json(source/'admission.json')
try:
    namespace['planning_barrier'](learning.verifier,301)
except Exception as error:
    assert getattr(error,'machine_code',None)=='LEARNING_SOURCE_MISSING',repr(error)
else:
    raise AssertionError('missing original prefix was permitted')
assert reader._json(source/'admission.json')==before
print(json.dumps({'blocked':True,'calls':calls,'attempts':before['attempts']}))
"""
    result = child(
        value,
        "failure=False\nDRAFT="
        + repr(draft)
        + "\nARTIFACT="
        + repr(artifact)
        + "\n"
        + body,
        timeout_seconds=60,
    )
    assert result == {
        "blocked": True,
        "calls": ["planner", "review"],
        "attempts": {"planner": 1, "review": 1},
    }
