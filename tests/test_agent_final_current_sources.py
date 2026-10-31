"""Actual canonical source preflight over synthetic primary delivery history."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tools import agent_epic_runtime_cli as guards
from tools.agent_orchestrate import StateStore

from tests.learning_session_native_fixture import prepared
from tests.test_agent_learning_integration import roles
from tests.test_agent_learning_session_native import BODY

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX canonical source chain"
)

PREFLIGHT = r"""
from tools.agent_final_preparation_runtime import (
    structural_child_preflight, _structural_children_current_owned,
    read_current_final_source_digests_owned, _CurrentFinalSources,
)
original_inventory = (301,303,305)
api.tasks = original_inventory
original_issues = copy.deepcopy(api.issues)
original_events = list(api.events)
role_bytes = (state/'native-planning-observations.jsonl').read_bytes()
# Preserve actual original authority, counters and wait budgets. Only deliberately
# damaged source bytes below are restored; no fixture namespace is copied/resealed.
source_paths = [
    path for directory in (state,state/'tasks/303',state/'tasks/305')
    for path in [directory/'epic-loop-checkpoint.json',directory/'delivery-ledger.json']
]
source_bytes = {path:path.read_bytes() for path in source_paths}
original_issue = api._issue

def primary_issue(number):
    value = original_issue(number)
    if number == 294:
        connection = value['subIssues']
        connection.update(totalCount=len(connection['nodes']))
        if attack == 'missing-count': del connection['totalCount']
        if attack == 'bool-count': connection['totalCount'] = True
        if attack == 'count-mismatch': connection['totalCount'] += 1
        if attack == 'page-has-next': connection['pageInfo'] = {'hasNextPage': True}
        if attack == 'page-flag-missing': connection['pageInfo'] = {}
        if attack == 'page-malformed': connection['pageInfo'] = []
    return value

# Synthetic provider JSON only; successful proofs keep the original readers.
api._issue = primary_issue
metadata_attacks = {
    'missing-count','bool-count','count-mismatch',
    'page-has-next','page-flag-missing','page-malformed',
}
original_task_directory = SourceReader.task_directory
per_child_reads = []

class PerChildProofRead(AssertionError):
    pass

def reject_per_child_read(self, number):
    per_child_reads.append(number)
    raise PerChildProofRead(('PER_CHILD_PROOF_READ_REACHED',attack,number))

outcomes = {}
for attack in ('missing-count','bool-count','count-mismatch',
               'page-has-next','page-flag-missing','page-malformed',
               'external','duplicate','foreign-parent','open',
               'missing-directory','missing-ledger','wrong-merge',
               'missing-demo-original','none'):
    restore = {}
    moved = None
    per_child_reads.clear()
    if attack in metadata_attacks:
        SourceReader.task_directory = reject_per_child_read
    try:
        # Keep the existing closed external child; avoid proving valid prefixes.
        if attack == 'external': api.tasks = (302,)
        if attack == 'duplicate': api.tasks = (301,303,303,305)
        if attack == 'foreign-parent': api.issues[303]['parent'] = {'number':999}
        if attack == 'open': api.issues[303]['state'] = 'OPEN'
        if attack == 'missing-directory':
            # This actual nonseed original remains the deliberately damaged target.
            api.tasks = (303,)
            moved = state/'tasks/303-unavailable'
            (state/'tasks/303').rename(moved)
        if attack in {'missing-ledger','wrong-merge','missing-demo-original'}:
            path = (state/'delivery-ledger.json' if attack == 'missing-ledger'
                    else state/'epic-loop-checkpoint.json' if attack == 'wrong-merge'
                    else state/'learning/records.jsonl')
            restore[path] = path.read_bytes()
            if attack == 'wrong-merge':
                value=json.loads(restore[path]);value['merge_sha']=api.policy_sha
                path.write_text(json.dumps(value))
            else: path.unlink()
        try:
            with (loop.RunnerLock.supervisor(state/'session-control'),
                  loop.RunnerLock(state)):
                with session._stage('FINAL:FINAL_GATE'):
                    result=structural_child_preflight(session)
        except PerChildProofRead:
            raise  # Rejecting instrumentation can never become a valid refusal.
        except Exception as error:
            assert attack != 'none', (type(error).__name__,str(error))
            if attack in metadata_attacks:
                assert str(error) == 'ISSUE_MISMATCH', (
                    attack,type(error).__name__,str(error))
            assert str(error) in {
                'ISSUE_MISMATCH','OPEN_REQUIRED_TASKS','LEARNING_SOURCE_MISSING',
                'LEARNING_TASK_UNAPPROVED','LEARNING_DELIVERY_SOURCE_INVALID',
                'LEARNING_DEMO_SOURCE_INVALID',
            }, (attack,type(error).__name__,str(error))
            outcomes[attack]='BLOCKED'
        else:
            assert attack == 'none'
            assert tuple(row['task'] for row in result)==original_inventory
            for row in result:
                assert len(row['pr_ci']['checks'])==5
                assert len(row['post_merge_ci']['checks'])==5
                assert row['delivery']['merge_sha']==api.pulls[row['task']].merge_sha
            outcomes[attack]='VERIFIED'
    finally:
        SourceReader.task_directory = original_task_directory
        for path,raw in restore.items():
            path.write_bytes(raw);path.chmod(0o600)
        if moved is not None: moved.rename(state/'tasks/303')
        api.tasks=original_inventory
        api.issues=copy.deepcopy(original_issues)
    assert not per_child_reads
    assert {path:path.read_bytes() for path in source_bytes}==source_bytes
    assert api.events==original_events
    assert (state/'native-planning-observations.jsonl').read_bytes()==role_bytes
    assert not (state/'final-preparation').exists()
# A genuine alternate current FINAL stage reuses the already-entered reader.
with loop.RunnerLock.supervisor(state/'session-control'), loop.RunnerLock(state):
    with session._stage('FINAL:PR_CI'):
        alternate = _structural_children_current_owned(session)
        assert tuple(row['task'] for row in alternate) == original_inventory
        try:
            structural_child_preflight(session)
        except Exception as error:
            assert str(error) == 'WAIT_FRAME_REQUIRED'
        else:
            raise AssertionError('strict wrapper broadened')
        scope = session.wait_scope
        active = scope._active_reader()
        pin = _CurrentFinalSources(session)
        for attack in ('guard', 'deadline', 'reader'):
            saved_guard, saved_deadline = active.guard, active.current_deadline
            try:
                if attack == 'guard': active.guard = lambda: None
                if attack == 'deadline': active.current_deadline += 1
                if attack == 'reader': scope._readers['FINAL:PR_CI'] = object()
                try: pin.check()
                except Exception as error:
                    assert str(error) == 'SESSION_SCOPE_CHANGED'
                else: raise AssertionError('pinned source changed:'+attack)
            finally:
                active.guard, active.current_deadline = saved_guard, saved_deadline
                scope._readers['FINAL:PR_CI'] = active
        original_route = session._read_stage
        session._read_stage = 'FINAL:FINAL_GATE'
        try:
            _structural_children_current_owned(session)
        except Exception as error:
            assert str(error) == 'SESSION_SCOPE_CHANGED'
        else:
            raise AssertionError('mismatched provider route admitted')
        finally:
            session._read_stage = original_route
        removed = scope._readers.pop('FINAL:PR_CI')
        try:
            _structural_children_current_owned(session)
        except Exception as error:
            assert str(error) == 'WAIT_FRAME_REQUIRED'
        else:
            raise AssertionError('missing reader provisioned')
        finally:
            scope._readers['FINAL:PR_CI'] = removed
        assert scope._active_reader() is active
        # This real task history intentionally has no completed aggregate/environment.
        # It cannot become a five-family source result, even though T is genuine.
        try:
            read_current_final_source_digests_owned(session)
        except Exception:
            pass
        else:
            raise AssertionError('absent aggregate originals admitted')
assert not (state/'final-preparation').exists()
assert api.events == original_events
print(json.dumps(outcomes))
"""


def test_all_structural_children_require_actual_original_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = prepared(tmp_path, monkeypatch)
    store = StateStore(Path(value["state"]))
    wait, config, launch, adapter, control = (
        store.load(name)
        for name in (
            "wait-profile",
            "epic-runtime",
            "launch",
            "runtime-adapter",
            "launch-control",
        )
    )
    assert wait is not None and config is not None and launch is not None
    assert adapter is not None and control is not None
    assert not (Path(value["state"]) / "invocations").exists()
    assert wait["limits"]["max_phase_seconds"] == 300
    assert adapter["timeout_seconds"] == 300
    wait["limits"]["max_phase_seconds"] = 600
    adapter["timeout_seconds"] = 600
    store.save("wait-profile", wait)
    store.save("runtime-adapter", adapter)
    config["approved_wait_digest"] = guards.canonical_digest(wait)
    launch["runtime_config_digest"] = guards.launch_config_digest(config, adapter)
    config["approved_launch_digest"] = guards.canonical_digest(launch)
    store.save("launch", launch)
    store.save("epic-runtime", config)
    control["launch_digest"] = config["approved_launch_digest"]
    store.save("launch-control", control)
    roles(value)
    script = Path(value["state"]) / "synthetic-roles.py"
    script.write_text(
        script.read_text().replace(
            "if fields['issue']==301: (root/'code.py').write_text('VALUE = 2\\n')",
            "(root/'code.py').write_text('VALUE = '+"
            "str({301:2,303:3,305:4}[fields['issue']])+'\\n')",
        )
    )
    # Reuse actual original Session deliveries/observations and scoped primary I/O.
    # Only its terminal print is replaced; no SourceReader proof method is mocked.
    history = BODY.rsplit("print(json.dumps(", 1)[0]
    body = "REMOTE=" + repr(value["remote"]) + "\n" + history
    body += "\n" + PREFLIGHT
    state = Path(value["state"])
    harness = state / "children-preflight.py"
    harness.write_text(
        "import sys,json\nfrom pathlib import Path\n"
        + "sys.path.insert(0,"
        + repr(str(Path(value["installed"]) / "policy"))
        + ")\n"
        + "from tools.agent_learning_evidence import SourceReader\n"
        + "state=Path("
        + repr(str(state))
        + ")\n"
        + "root=Path("
        + repr(value["root"])
        + ")\n"
        + "source=Path("
        + repr(str(Path.cwd()))
        + ")\n"
        + "reader=SourceReader(state,root)\n"
        + body
    )
    result = subprocess.run(
        [sys.executable, "-B", str(harness)],
        capture_output=True,
        text=True,
        timeout=660,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "none": "VERIFIED",
        **dict.fromkeys(
            (
                "external",
                "duplicate",
                "foreign-parent",
                "open",
                "missing-directory",
                "missing-ledger",
                "wrong-merge",
                "missing-demo-original",
                "missing-count",
                "bool-count",
                "count-mismatch",
                "page-has-next",
                "page-flag-missing",
                "page-malformed",
            ),
            "BLOCKED",
        ),
    }
