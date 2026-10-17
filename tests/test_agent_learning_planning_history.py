"""Fresh native source-reader context and bounded planning provenance guards."""

import os
from pathlib import Path

import pytest

from tests.test_agent_learning_evidence import child, error_body
from tests.test_agent_learning_planning_sources import planning_fixture


@pytest.mark.parametrize("boundary", ["cycle", "depth"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_original_replay_rejects_recursive_or_exhausted_provenance(
    tmp_path: Path, boundary: str
) -> None:
    value = planning_fixture(tmp_path)
    setup = (
        "reader.planning_replay_stack.add((301,'planner',1))\n"
        if boundary == "cycle"
        else (
            "reader.planning_replay_stack.update((i,'planner',1) "
            "for i in range(reader.owner['limits']['max_records']))\n"
        )
    )
    result = child(
        value,
        "from tools.agent_learning_planning_sources import replay\n"
        + setup
        + error_body("replay(reader,301,'planner',1)"),
        timeout_seconds=60,
    )
    assert result["code"] == "LEARNING_SOURCE_MISMATCH"
    assert not (Path(value["state"]) / "planning/301/admission.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_hinted_origin_requires_actual_validated_session_context(
    tmp_path: Path,
) -> None:
    value = planning_fixture(tmp_path)
    result = child(
        value,
        "from tools.agent_learning_planning_history import verify\n"
        + error_body("verify(reader,'planner',{}, {}, {})"),
        timeout_seconds=60,
    )
    assert result["code"] == "LEARNING_SOURCE_INVALID"


@pytest.mark.skipif(os.name != "posix", reason="POSIX private learning runtime fixture")
def test_actual_verifier_binds_its_own_reader_for_original_proof(
    tmp_path: Path,
) -> None:
    value = planning_fixture(tmp_path)
    result = child(
        value,
        "from tools import agent_epic_runtime_cli as cli\n"
        "from tools.agent_learning_verify import Verifier\n"
        "session=cli.build_session(state,root,wake=True)\n"
        "verified=Verifier(reader,session)\n"
        "assert reader.planning_verifier is verified\n"
        "assert verified.reader is reader and verified.session is session\n"
        "print(json.dumps({'bound':True,'stack':len(reader.planning_replay_stack)}))\n",
        timeout_seconds=60,
    )
    assert result == {"bound": True, "stack": 0}
