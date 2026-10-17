"""Actual Session delivery and planning through bounded synthetic native I/O."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.learning_session_native_fixture import prepared
from tests.test_agent_learning_integration import roles

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX native Session")

BODY = r"""
import copy, subprocess, os
sys.path.insert(1,str(source))
from tests.learning_session_primary_fixture import Primary
from tools import agent_epic_runtime_cli as cli
from tools import agent_epic_planning as planning
from tools import agent_epic_loop as loop
from tools import agent_gate
from tools import agent_orchestrate as workflow
from tools.agent_epic_planning_roles import PlanningRoles
from tools.agent_learning_planning import Planning
api=Primary(root,Path(REMOTE),policy_sha=reader.owner['policy_sha'],initialize=False)
preview=json.loads((state/'session-preview.json').read_text())
original_run=subprocess.run
def io(argv,**kwargs):
    argv=list(argv)
    if argv[0]=='gh':
        assert 0 < kwargs.get('timeout',15) <= 120
        return subprocess.CompletedProcess(argv,0,api.read(argv),'')
    if argv[0]=='git':
        exact='https://github.com/OWNER/repo.git'
        argv=[str(api.remote) if arg==exact else arg for arg in argv]
        assert all(
            not any(s in arg for s in ('https://','http://','ssh://','git@'))
            for arg in argv)
        if argv[1] in {'fetch','push','ls-remote'}:
            assert str(api.remote) in argv or any(
                Path(arg).is_absolute() and Path(arg).is_dir()
                and Path(arg).name in {'mirror.git','snapshot'}
                and Path(arg).parent.name.startswith('pgw-gate-')
                and str(Path(arg)).startswith('/tmp/')
                for arg in argv)
        kwargs['timeout']=min(kwargs.get('timeout') or 15,15)
    return original_run(argv,**kwargs)
subprocess.run=io
session=cli.build_session(state,root,wake=True)
# Retain Scope.reader itself, guard/frame state and command reservation logic.
original_reader=session.wait_scope.reader
def bounded_reader(stage):
    result=original_reader(stage)
    result.run=io
    return result
session.wait_scope.reader=bounded_reader
reader.read=session._github_read

def gate(*,root,base_sha,allowed_paths,observer_factory=None):
    with agent_gate.trusted_snapshot_session(root=root,base_sha=base_sha,
        allowed_paths=allowed_paths) as snap:
        observer=observer_factory(snap) if observer_factory else None
        checks=[]
        for name in agent_gate.PROFILES[agent_gate.FULL_PROFILE]:
            metrics={'passed':1,'skipped':0} if name=='pytest' else (
                {'errors':0} if name=='ruff' else (
                {'source_files':1} if name=='mypy' else {
                'hooks':[{'id':'synthetic','outcome':'passed'}]}))
            check=agent_gate.CheckResult(name,agent_gate.CHECK_COMMANDS[name],
                'passed',0,0.0,'synthetic passed',metrics)
            checks.append(check)
            if observer: observer(check)
        return agent_gate.build_evidence(snap,checks)
agent_gate.run_repository_full=gate
writes=[]
failed_bytes={}
original_adapter=session.task_adapter
def adapter(store,iterations):
    engine=original_adapter(store,iterations)
    wrapped=engine.bootstrap.reviewed.adapter
    prefix=[sys.executable,'-B',str(state/'synthetic-roles.py'),str(reader.policy),
        str(source),str(state),str(root)]
    synthetic=workflow.CommandAdapter(prefix+['executor'],prefix+['controller'],
        root=root,timeout_seconds=10,output_limit=65536)
    def native(command,payload):
        wrapped.delegate.guard()
        assert command in (wrapped.executor_command,wrapped.controller_command)
        target=(synthetic.executor_command if command==wrapped.executor_command
            else synthetic.controller_command)
        return synthetic._call(target,payload)
    wrapped.delegate._call=native
    def primary_command(argv):
        try: return api.command(argv)
        except Exception:
            import traceback
            traceback.print_exc()
            raise
    engine.transport.command=primary_command
    before=engine.transport.before_write
    def before_write(request):
        try: before(request)
        except Exception:
            import traceback
            traceback.print_exc()
            raise
        writes.append(request.operation)
    engine.transport.before_write=before_write
    effect=engine.effect
    def observed_effect(phase):
        try:
            result=effect(phase)
        except Exception:
            import traceback
            traceback.print_exc()
            raise
        task=store.load()['task']
        if phase=='TASK_DONE' and task==301:
            assert api.pulls[301].head_sha==preview['301']['head']
            assert api.pulls[301].merge_sha==preview['301']['merge']
            mandate,approved,context=session._fresh_authority(iterations)
            owner=session._planning_owner(mandate)
            invoke=PlanningRoles(root=root,policy_root=reader.policy,
                policy_sha=reader.owner['policy_sha'],learning_state=state)
            for failed in (302,304):
                try:
                    planning.Admission(state,root).admit(owner=owner,mandate=mandate,
                        context=context,task=failed,body=api.issues[failed]['body'],
                        base=api.pulls[301].merge_sha,default=api.policy_sha,
                        dependencies=[],invoke=invoke,guard=reader.guard,
                        learning=Planning(reader,session))
                except loop.PhaseBlocked as error:
                    expected=('PLANNING_CRITERIA_INCOMPLETE' if failed==302
                        else 'PLANNING_REVIEW_INVALID')
                    assert error.machine_code==expected
                else: raise AssertionError('expected original planning failure')
                failed_bytes[failed]={str(p.relative_to(state)):p.read_bytes()
                    for p in (state/'planning'/str(failed)).rglob('*') if p.is_file()}
                api.owner_close_failed(failed)
        return result
    engine.effect=observed_effect
    return engine
session.task_adapter=adapter
try:
    session.resume()
except loop.PhaseBlocked as error:
    assert error.machine_code=='FINAL_DEMO_UNCONFIRMED'
    assert session.config['approved_final_digest'] is None
else:
    raise AssertionError('expected canonical owner final demo stop')
cursor=cli.private_json(state/'session-control/cursor.json')
assert cursor['completed_tasks']==[301,303,305]
assert cursor['mode']=='FINAL'
for failed,files in failed_bytes.items():
    assert files=={str(p.relative_to(state)):p.read_bytes()
        for p in (state/'planning'/str(failed)).rglob('*') if p.is_file()}
for task in (301,303):
    assert api.pulls[task].merge_sha==preview[str(task)]['merge']
observations=state/'native-planning-observations.jsonl'
observed=[json.loads(line) for line in observations.read_text().splitlines()]
assert [(r['task'],r['role']) for r in observed]==[(302,'planner'),(304,'planner'),
    (304,'review'),(303,'planner'),(303,'review'),(305,'planner'),(305,'review')]
assert [r['hinted'] for r in observed]==[False]*5+[True]*2
print(json.dumps({'completed_tasks':cursor['completed_tasks'],'native_planning_calls':len(observed)}))
"""


def test_original_native_session_plans_and_delivers_successors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = prepared(tmp_path, monkeypatch)
    roles(value)
    script = Path(value["state"]) / "synthetic-roles.py"
    text = script.read_text().replace(
        "if fields['issue']==301: (root/'code.py').write_text('VALUE = 2\\n')",
        "(root/'code.py').write_text('VALUE = '+"
        "str({301:2,303:3,305:4}[fields['issue']])+'\\n')",
    )
    script.write_text(text)
    body = "REMOTE=" + repr(value["remote"]) + "\n" + BODY
    state = Path(value["state"])
    harness = state / "native-session.py"
    harness.write_text(
        "import sys,json\nfrom pathlib import Path\n"
        + "sys.path.insert(0,"
        + repr(str(Path(value["installed"]) / "policy"))
        + ")\n"
        + "from tools.agent_learning_evidence import SourceReader\n"
        + "from tools.agent_epic_loop import RunnerLock\n"
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
        + "\n"
    )
    run = subprocess.run(
        [sys.executable, "-B", str(harness)],
        capture_output=True,
        text=True,
        timeout=360,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {
        "completed_tasks": [301, 303, 305],
        "native_planning_calls": 7,
    }
