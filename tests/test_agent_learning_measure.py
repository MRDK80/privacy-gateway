"""Rollback uses actual frozen source runs and all original applied selections."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import pytest
from tools import agent_coordinator_handover as handover
from tools import agent_epic_runtime_cli as guards
from tools.agent_orchestrate import StateStore

from tests import test_agent_continuation_factory as factory
from tests.test_agent_learning_evidence import child
from tests.test_agent_learning_integration import DELIVERY, SETUP, multi, roles

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX frozen sources")


def fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, cap: int = 100, repairs: int = 1
) -> dict[str, str]:
    original_build = factory.build

    def measurement_build(
        base: Path,
        *,
        extra_tasks: tuple[int, ...] = (),
        delivery_enabled: bool = False,
        with_demo: bool = False,
        with_planning_policy: bool = False,
    ) -> dict[str, str]:
        assert extra_tasks == (302, 303)
        return original_build(
            base,
            extra_tasks=(302, 303, 304),
            delivery_enabled=delivery_enabled,
            with_demo=with_demo,
            with_planning_policy=with_planning_policy,
        )

    with monkeypatch.context() as patch:
        patch.setattr(factory, "build", measurement_build)
        value = multi(tmp_path, monkeypatch, controller_rule=True)
    state = Path(value["state"])
    store = StateStore(state)
    config, adapter, launch, owner, plan, plans = (
        store.load(name)
        for name in (
            "epic-runtime",
            "runtime-adapter",
            "launch",
            "learning-profile",
            "handover",
            "approved-plans",
        )
    )
    assert config is not None and adapter is not None and launch is not None
    assert owner is not None and plan is not None and plans is not None
    owner["limits"]["max_records"] = cap
    plan["budgets"]["max_repair_iterations"] = repairs
    approved = handover.handover_digest(plan)
    plan["approval"]["plan_digest"] = approved
    plans["initial_handover_digest"] = approved
    plans["tasks"]["301"]["plan"] = plan
    prior = deepcopy(plan)
    prior.update(task=302, head_ref="codex/302-prior")
    prior["criteria_source"]["issue"] = 302
    prior["approval"]["plan_digest"] = handover.handover_digest(prior)
    demo = deepcopy(plans["tasks"]["301"]["demo_plan"])
    demo["task"] = 302
    plans["tasks"]["302"] = {"plan": prior, "demo_plan": demo}
    baseline = deepcopy(plans["tasks"]["303"])
    baseline["plan"].update(task=304, head_ref="codex/304-baseline")
    baseline["plan"]["criteria_source"]["issue"] = 304
    baseline["plan"]["approval"]["plan_digest"] = handover.handover_digest(
        baseline["plan"]
    )
    baseline["demo_plan"]["task"] = 304
    plans["tasks"]["304"] = baseline
    continuation = store.load("continuation-profile")
    assert continuation is not None
    continuation["limits"]["max_handoffs"] = max(
        continuation["limits"]["max_handoffs"], repairs + 1
    )
    store.save("continuation-profile", continuation)
    config["approved_continuation_digest"] = guards.canonical_digest(continuation)
    config["approved_plans_digest"] = guards.canonical_digest(plans)
    config["approved_learning_digest"] = guards.canonical_digest(owner)
    launch["runtime_config_digest"] = guards.launch_config_digest(config, adapter)
    config["approved_launch_digest"] = guards.canonical_digest(launch)
    for name, item in (
        ("learning-profile", owner),
        ("handover", plan),
        ("approved-plans", plans),
        ("epic-runtime", config),
        ("launch", launch),
    ):
        store.save(name, item)
    control = store.load("launch-control")
    assert control is not None
    control["launch_digest"] = config["approved_launch_digest"]
    store.save("launch-control", control)
    roles(value)
    script = state / "synthetic-roles.py"
    script.write_text(
        script.read_text().replace(
            "else: output=_verdict(payload,'PASS')",
            "else: output=_verdict(payload,'FAIL_ESCALATE' "
            "if payload['contract']['issue']==303 else 'PASS')",
        )
    )
    return value


COMMON = (
    SETUP
    + DELIVERY.replace("assert len(promoted)==1", "assert len(promoted)==2")
    + """
from tools import agent_learning as learning
from tools import agent_learning_measure as measure
from tools import agent_learning_observe as observe
from tools import agent_learning_records as records
from tools.agent_learning_store import Store
original_capture,original_terminal=measure.capture,observe.terminal
measure.capture=lambda *args: {}
observe.terminal=lambda *args: {}
def consumer(task):
    plan=copy.deepcopy(session.plans['tasks'][str(task)]['plan'])
    assert plan['head_sha']==plan['base_sha']==api.merge
    directory=state/'tasks'/str(task);directory.mkdir(mode=0o700)
    workflow.StateStore(directory).save('handover',plan)
    git('switch','-c',plan['head_ref'],api.merge)
    checkpoint=copy.deepcopy(seed)
    checkpoint.update(task=task,head_ref=plan['head_ref'],head_sha=api.merge,base_sha=api.merge)
    store=loop.CheckpointStore(directory,root);store.save(checkpoint)
    engine=session.task_adapter(store,0)
    return directory,configure(engine)
source_gate=agent_gate.run_repository_full
consumer_gates=0
def consumer_gate(**kwargs):
    global consumer_gates,force_gate_failure
    consumer_gates+=1
    force_gate_failure=consumer_gates==1
    return source_gate(**kwargs)
"""
)

RUNS = (
    COMMON
    + """
baseline_dir,baseline_reviewed=consumer(304)
force_gate_failure=False
with loop.RunnerLock(baseline_dir),session.wait_scope.context('TASK:304:RUN_TASK'):
    baseline_reviewed.run()
task_state,reviewed=consumer(303)
agent_gate.run_repository_full=consumer_gate
try:
    with loop.RunnerLock(task_state),session.wait_scope.context('TASK:303:RUN_TASK'):
        reviewed.run()
except loop.PhaseBlocked as error:
    assert error.machine_code=='WORKFLOW_REVIEW_NOT_VERIFIED'
measure.capture,observe.terminal=original_capture,original_terminal
baseline,_=measure.observed(reader,session,304)
hinted,metadata=measure.observed(reader,session,303)
assert baseline['outcome']=='PASS' and baseline['hint_set_digest'] is None
assert hinted['outcome']=='FAIL_ESCALATE' and hinted['hint_set_digest'] is not None
assert baseline['calls']=={'planner':0,'planning_review':0,'executor':1,'controller':1}
assert hinted['calls']=={'planner':0,'planning_review':0,'executor':2,'controller':1}
assert baseline['applicability_digest']==hinted['applicability_digest']
assert all(item['status']=='COMPLETED' for item in metadata)
first=next(item for item in metadata
    if item['role']=='executor' and item['ordinal']==1)
assert first['lesson_ids']==[]
applied=[item for item in metadata if item['lesson_ids']]
assert {(item['role'],item['ordinal']) for item in applied}=={
    ('executor',2),('controller',1)}
lessons=sorted({lesson for item in applied for lesson in item['lesson_ids']})
assert len(lessons)==2
assert set(lessons)=={item['lesson_id'] for item in promoted}
evaluation=learning.compare(baseline,hinted)
assert evaluation['comparison']['status']=='ROLLBACK_REQUIRED'
observe.terminal(reader,304)
with reader.ownership(baseline_dir), Store(baseline_dir/'learning',
        root,baseline_dir,reader.owner,
        validators=records.VALIDATORS) as store:
    store.append('measurement',baseline,'measurement_id')
"""
)


BIND_PRIMARY = """from tools import agent_epic_runtime_cli as cli
from tools.agent_learning_delivery import DeliverySources
sys.path.insert(1,str(source))
from tests.learning_delivery_fixture import PrimaryAPI
from tools.agent_epic_runtime import TaskPhaseRuntime
# Reconstruct only the already completed original synthetic301 primary identity.
# SourceReader keeps its original policy/root/wait/ownership and source guards.
reader.guard()
checkpoint=reader._json(state/'epic-loop-checkpoint.json')
ledger_rows=reader._json(state/'delivery-ledger.json')
merged=[row for row in ledger_rows.values()
    if row['operation']=='merge_task_pr']
assert len(merged)==1
merge_row=merged[0];request=merge_row['request']
assert merge_row['status']=='APPLIED' and request['task']==301
assert request['repository']=='OWNER/repo' and request['pr']==7
assert request['head_ref']=='codex/301-test'
assert request['base_ref']=='roadmap/294-autonomy'
assert checkpoint['task']==301 and checkpoint['phase']=='TASK_DONE'
assert checkpoint['pending_phase'] is None
assert checkpoint['head_sha']==request['head_sha']
assert checkpoint['base_sha']==request['base_sha']
assert checkpoint['merge_sha']==merge_row['receipt']['merge_sha']
assert checkpoint['pr']==request['pr']
primary=PrimaryAPI(request['base_sha'],request['head_sha'])
primary.merge=merge_row['receipt']['merge_sha'];primary.merged=True
original_head=reader._git('rev-parse','refs/heads/codex/301-test')
assert original_head.decode().strip()==primary.head
original_merge=reader._git('rev-parse','refs/heads/roadmap/294-autonomy')
assert original_merge.decode().strip()==primary.merge
def primary_read(argv):
    suffix=argv[-1].removeprefix('repos/OWNER/repo/')
    if suffix.startswith('commits/'):
        assert suffix.split('/')[1] in {primary.head,primary.merge}
    return primary(argv)
reader.read=primary_read
TaskPhaseRuntime._github_read=lambda self,argv:primary_read(argv)
# Original production ledger validator checks all six exact requests/receipts,
# original contract/checkpoint/operation identities and reconstructed PR7 source.
original_delivery=DeliverySources(reader).ledger(301)
assert original_delivery['head_sha']==primary.head
assert original_delivery['merge_sha']==primary.merge
assert original_delivery['pr']==7
"""


SELECT = (
    BIND_PRIMARY
    + """from tools import agent_epic_runtime_cli as cli
from tools.agent_learning_verify import Verifier
session=cli.build_session(state,root,wake=True)
try:
    Verifier(reader,session).selection('feature','executor','typing')
except Exception as error:
    print(json.dumps({'code':str(error)}))
else:
    print(json.dumps({'code':'USABLE'}))"""
)


@pytest.mark.parametrize("failure", ["full", "append_io", "tombstone_io", "latch_io"])
def test_actual_regression_blocks_fresh_selection_after_any_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    value = fixture(tmp_path, monkeypatch)
    body = (
        RUNS
        + """import os,stat
failure="""
        + repr(failure)
        + """
original_write=os.write
original_append=Store.append
def write(fd,data):
    latch=task_state/'learning/learning-blocked.json'
    is_latch=latch.exists() and os.fstat(fd).st_ino==latch.stat().st_ino
    # The injected failure targets storage files; read-only Git stdin pipes
    # are transport, and must not masquerade as a failed learning append.
    if stat.S_ISREG(os.fstat(fd).st_mode) and (failure=='latch_io')==is_latch:
        raise OSError('synthetic write failure')
    return original_write(fd,data)
def append(self,kind,record,key):
    if failure=='tombstone_io' and kind=='tombstone':
        raise learning.LearningError('LEARNING_WRITE_FAILED')
    if failure=='full' and self.task==task_state:
        # Narrow append quota fault: actual workflow/gate source remains under
        # its original unchanged owner profile and consumed role budgets.
        original_cap=self.owner['limits']['max_records']
        self.owner['limits']['max_records']=len(self.records())
        try:
            return original_append(self,kind,record,key)
        finally:
            self.owner['limits']['max_records']=original_cap
    return original_append(self,kind,record,key)
if failure in {'append_io','latch_io'}: os.write=write
if failure in {'tombstone_io','full'}: Store.append=append
try:
    measure.capture(reader,session,303)
except Exception as error:
    code=str(error)
else:
    code='UNEXPECTED_SUCCESS'
os.write=original_write
Store.append=original_append
assert (task_state/'learning/learning-blocked.json').exists()
try:
    Verifier(reader,session).selection('feature','executor','typing')
except learning.LearningError as error:
    assert error.machine_code=='LEARNING_ROLLBACK_PENDING'
else:
    raise AssertionError('fresh verifier selected after regression')
print(json.dumps({'code':code,'lessons':len(lessons)}))"""
    )
    result = child(value, body, timeout_seconds=180)
    assert result["code"] == (
        "LEARNING_STORAGE_EXHAUSTED" if failure == "full" else "LEARNING_WRITE_FAILED"
    )
    assert result["lessons"] == 2
    assert child(value, SELECT) == {"code": "LEARNING_ROLLBACK_PENDING"}


def test_actual_run_tombstones_every_role_selection_before_clearing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = fixture(tmp_path, monkeypatch)
    result = child(
        value,
        RUNS
        + """observe.terminal(reader,303)
def source_bytes():
    return [path.read_bytes() for path in
        sorted((task_state/'learning-invocations').glob('*.json'))]
before=source_bytes()
assert measure.capture(reader,session,303)==hinted
assert not (task_state/'learning/learning-blocked.json').exists()
assert source_bytes()==before
with reader.ownership(task_state), Store(
        task_state/'learning',root,task_state,reader.owner,
        validators=records.VALIDATORS) as store:
    store.ensure_usable()
    retained=store.records()
removed=sorted(item['record']['lesson_id'] for item in retained
    if item['kind']=='tombstone')
assert removed==lessons
assert all(item['record']['evaluation_digest']==digest(evaluation)
    for item in retained if item['kind']=='tombstone')
print(json.dumps({'removed':len(removed),'calls':hinted['calls']}))""",
        timeout_seconds=180,
    )
    assert result == {
        "removed": 2,
        "calls": {"planner": 0, "planning_review": 0, "executor": 2, "controller": 1},
    }
    assert child(value, SELECT) == {"code": "USABLE"}


def test_source_drift_after_tombstones_keeps_negative_latch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = fixture(tmp_path, monkeypatch)
    result = child(
        value,
        RUNS
        + """observe.terminal(reader,303)
original=Store.append
def append(self,kind,record,key):
    original(self,kind,record,key)
    if kind=='tombstone' and record['lesson_id']==lessons[-1]:
        item=metadata[0]
        workflow.StateStore(task_state/'learning-invocations').save(item['invocation_key'],
            learning.seal({**item,'status':'UNKNOWN'}))
Store.append=append
try:
    measure.capture(reader,session,303)
except learning.LearningError as error:
    code=error.machine_code
else:
    code='UNEXPECTED_SUCCESS'
assert (task_state/'learning/learning-blocked.json').exists()
print(json.dumps({'code':code}))""",
        timeout_seconds=180,
    )
    assert result == {"code": "LEARNING_MEASUREMENT_SOURCE_INVALID"}
    assert child(value, SELECT) == {"code": "LEARNING_ROLLBACK_PENDING"}


def test_failed_first_latch_creation_blocks_source_based_selection_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = fixture(tmp_path, monkeypatch)
    result = child(
        value,
        RUNS
        + """import os
original=os.open
attempts=[]
def open_file(path,flags,*args,**kwargs):
    if Path(path)==task_state/'learning/learning-blocked.json' and flags & os.O_CREAT:
        attempts.append(str(path))
        raise OSError('synthetic first latch creation failure')
    return original(path,flags,*args,**kwargs)
os.open=open_file
try:
    measure.capture(reader,session,303)
except learning.LearningError as error:
    code=error.machine_code
else:
    code='UNEXPECTED_SUCCESS'
os.open=original
assert len(attempts)==1
assert not (task_state/'learning/learning-blocked.json').exists()
print(json.dumps({'code':code,'lessons':len(lessons)}))""",
        timeout_seconds=180,
    )
    assert result == {"code": "LEARNING_WRITE_FAILED", "lessons": 2}
    assert child(value, SELECT) == {"code": "LEARNING_ROLLBACK_PENDING"}


@pytest.mark.parametrize("change", ["missing", "unknown", "forged_measurement"])
def test_unfinalized_original_hinted_sources_never_authorize_new_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    value = fixture(tmp_path, monkeypatch)
    body = (
        RUNS
        + "change="
        + repr(change)
        + """
if change=='missing':
    path=task_state/'learning-invocations'/(metadata[0]['invocation_key']+'.json')
    path.unlink()
elif change=='unknown':
    item=metadata[0]
    workflow.StateStore(task_state/'learning-invocations').save(item['invocation_key'],
        learning.seal({**item,'status':'UNKNOWN'}))
else:
    fake={key:item for key,item in hinted.items()
        if key not in {'measurement_id','record_digest'}}
    fake['outcome']='PASS'
    fake['measurement_id']=digest(fake)
    fake=learning.seal(fake)
    with reader.ownership(task_state), Store(
            task_state/'learning',root,task_state,reader.owner,
            validators=records.VALIDATORS) as store:
        for item in metadata: store.append('invocation',item,'invocation_key')
        store.append('measurement',fake,'measurement_id')
        fake_eval=learning.compare(baseline,fake)
        store.append('evaluation',fake_eval,'evaluation_id')
print(json.dumps({'pending':True}))"""
    )
    assert child(value, body, timeout_seconds=180) == {"pending": True}
    result = child(value, SELECT)
    if change == "missing":
        assert result["code"] == "LEARNING_SOURCE_MISSING"
    else:
        assert result["code"] in {
            "LEARNING_MEASUREMENT_SOURCE_INVALID",
            "LEARNING_ROLLBACK_PENDING",
        }


def test_capture_finalizes_own_source_pending_evaluation_without_new_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = fixture(tmp_path, monkeypatch)
    assert child(
        value, RUNS + "print(json.dumps({'pending':True}))", timeout_seconds=180
    ) == {"pending": True}
    assert child(value, SELECT) == {"code": "LEARNING_ROLLBACK_PENDING"}
    result = child(
        value,
        BIND_PRIMARY
        + """from tools import agent_epic_runtime_cli as cli
from tools import agent_learning_measure as measure
from tools import agent_learning_observe as observe
from tools.agent_learning_verify import Verifier
session=cli.build_session(state,root,wake=True)
task_state=state/'tasks/303'
original,metadata=measure.observed(reader,session,303)
paths=sorted((task_state/'learning-invocations').glob('*.json'))
before=[path.read_bytes() for path in paths]
observe.terminal(reader,303)
assert measure.capture(reader,session,303)==original
assert measure.capture(reader,session,303)==original
assert [path.read_bytes() for path in paths]==before
assert not (task_state/'learning/learning-blocked.json').exists()
Verifier(reader,session).selection('feature','executor','typing')
print(json.dumps({'calls':original['calls']}))""",
        timeout_seconds=180,
    )
    assert result == {
        "calls": {"planner": 0, "planning_review": 0, "executor": 2, "controller": 1},
    }
    assert child(value, SELECT) == {"code": "USABLE"}


@pytest.mark.parametrize(
    "change",
    [
        "settings",
        "partial_workflow",
        "measurement",
        "pending_workflow",
        "unknown_workflow",
        "missing_workflow",
        "missing_metadata_and_workflow",
    ],
)
def test_finalized_records_cannot_replace_changed_original_completion_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    value = fixture(tmp_path, monkeypatch)
    body = (
        RUNS
        + "observe.terminal(reader,303)\nmeasure.capture(reader,session,303)\nchange="
        + repr(change)
        + """
if change=='settings':
    original=reader.controller(303)
    path=state/'role-settings'/('receipt-'+original['invocation_digest']+'.json')
    path.unlink()
elif change=='partial_workflow':
    path=task_state/'bounded-workflow'/(workflow._run_key(reader.contract(303))+'.json')
    path.write_text('{')
elif change in {'missing_workflow','missing_metadata_and_workflow'}:
    path=task_state/'bounded-workflow'/(workflow._run_key(reader.contract(303))+'.json')
    path.unlink()
    if change=='missing_metadata_and_workflow':
        for path in (task_state/'learning-invocations').glob('*.json'): path.unlink()
elif change in {'pending_workflow','unknown_workflow'}:
    key=workflow._run_key(reader.contract(303))
    original=reader._json(task_state/'bounded-workflow'/(key+'.json'))
    original.update(terminal=False,
        status='running' if change=='pending_workflow' else 'UNKNOWN',
        role_pending='controller' if change=='pending_workflow' else None)
    workflow.StateStore(task_state/'bounded-workflow').save(key,original)
else:
    with reader.ownership(task_state), Store(
            task_state/'learning',root,task_state,reader.owner,
            validators=records.VALIDATORS) as store:
        retained=store.records()
        # A resealed, self-consistent stored measurement is not source proof.
        for item in retained:
            if item['kind']=='measurement':
                item['record']=learning.seal({**item['record'],'outcome':'PASS'})
        store.path.write_text(''.join(json.dumps(item,sort_keys=True)+'\\n'
            for item in retained))
print(json.dumps({'changed':True}))"""
    )
    assert child(value, body, timeout_seconds=180) == {"changed": True}
    result = child(value, SELECT)
    assert result["code"] != "USABLE"


ACTIVE_RUN = (
    COMMON
    + """
task_state,reviewed=consumer(303)
force_gate_failure=False
agent_gate.run_repository_full=source_gate
class SyntheticPause(BaseException): pass
original_gate=agent_gate.run_repository_full
def active_gate(**kwargs):
    key=workflow._run_key(reader.contract(303))
    canonical=reader._json(task_state/'bounded-workflow'/(key+'.json'))
    if canonical['executor_calls']==pause_at:
        raise SyntheticPause()
    return original_gate(**kwargs)
agent_gate.run_repository_full=active_gate
try:
    with loop.RunnerLock(task_state), session.wait_scope.context('TASK:303:RUN_TASK'):
        reviewed.run()
except SyntheticPause:
    pass
else:
    raise AssertionError('actual active executor boundary was not reached')
key=workflow._run_key(reader.contract(303))
canonical=reader._json(task_state/'bounded-workflow'/(key+'.json'))
assert canonical['terminal'] is False and canonical['status']=='running'
assert canonical['role_pending'] is None
assert canonical['executor_calls']==pause_at
assert canonical['controller_calls']==pause_at-1
metadata=measure._metadata(reader,task_state/'learning-invocations',task=303,
    run_id=canonical['run_id'])
assert len(metadata)==2*pause_at-1
assert all(item['status']=='COMPLETED' and item['lesson_ids']==[]
    for item in metadata)
from tools import agent_learning_planning_records as planning_records
for item in metadata:
    path=task_state/'learning-invocations'/(item['invocation_key']+'.problem.json')
    companion=planning_records.problem_binding(reader._json(path))
    assert companion['invocation_key']==item['invocation_key']
    assert companion['role']==item['role']
    assert companion['source_kind']=='NONE'
    assert companion['source_digest'] is None
    assert companion['problem_class'] is None
# Actual passing gates and FAIL_RETRY controllers consume C1/C2 before E2/E3.
# This active prefix proves original empty-selection completions, not hint usage.
reservations=[reader._json(path) for path in
    (task_state/'continuation').glob('reservation-*.json')]
assert {(item['role'],item['ordinal']) for item in reservations}=={
    (role,ordinal) for role,count in (
        ('executor',pause_at),('controller',pause_at-1))
    for ordinal in range(1,count+1)}
assert all(item['status']=='COMPLETED' for item in reservations)
for operation in (reader.workflow,reader.controller):
    try: operation(303)
    except learning.LearningError as error:
        assert error.machine_code=='LEARNING_INVALID'
    else: raise AssertionError('active prefix was treated as terminal source')
print(json.dumps({'calls':[pause_at,pause_at-1],'completed':len(metadata)}))
"""
)


def active_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, pause_at: int = 1
) -> dict[str, str]:
    value = fixture(tmp_path, monkeypatch, repairs=2)
    script = Path(value["state"]) / "synthetic-roles.py"
    script.write_text(script.read_text().replace("'FAIL_ESCALATE'", "'FAIL_RETRY'"))
    assert child(
        value,
        "pause_at=" + repr(pause_at) + "\n" + ACTIVE_RUN,
        timeout_seconds=180,
    ) == {"calls": [pause_at, pause_at - 1], "completed": 2 * pause_at - 1}
    return value


def test_active_completed_executor_requires_original_settings_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = active_run(tmp_path, monkeypatch)
    assert child(value, SELECT) == {"code": "USABLE"}
    assert child(
        value,
        """task_state=state/'tasks/303'
from tools import agent_learning_measure as measure
from tools import agent_orchestrate as workflow
key=workflow._run_key(reader.contract(303))
canonical=reader._json(task_state/'bounded-workflow'/(key+'.json'))
rows=[reader._json(path) for path in
    (task_state/'continuation').glob('reservation-*.json')]
assert len(rows)==1 and rows[0]['role']=='executor'
path=state/'role-settings'/('receipt-'+rows[0]['invocation_digest']+'.json')
assert path.exists()
path.unlink()
assert reader._json(task_state/'bounded-workflow'/(key+'.json'))==canonical
print(json.dumps({'deleted':True}))""",
    ) == {"deleted": True}
    assert child(value, SELECT)["code"] != "USABLE"


@pytest.mark.parametrize(
    ("role", "ordinal"),
    [
        ("executor", 1),
        ("executor", 2),
        ("executor", 3),
        ("controller", 1),
        ("controller", 2),
    ],
)
def test_active_selection_rereads_every_consumed_original_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str, ordinal: int
) -> None:
    value = active_run(tmp_path, monkeypatch, pause_at=3)
    assert child(value, SELECT) == {"code": "USABLE"}
    body = (
        "role="
        + repr(role)
        + "\nordinal="
        + repr(ordinal)
        + "\n"
        + BIND_PRIMARY
        + """
task_state=state/'tasks/303'
import hashlib,os,subprocess,zlib
from copy import deepcopy
from tools import agent_learning as learning
from tools import agent_learning_records as records
from tools import agent_orchestrate as workflow
from tools.agent_continuation import digest
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_verify import Verifier
from tools import agent_epic_runtime_cli as cli
session=cli.build_session(state,root,wake=True)
key=workflow._run_key(reader.contract(303))
canonical_path=task_state/'bounded-workflow'/(key+'.json')
canonical_bytes=canonical_path.read_bytes()
rows=[(path,reader._json(path)) for path in
    (task_state/'continuation').glob('reservation-*.json')]
reservation_path,row=next((path,item) for path,item in rows
    if item['role']==role and item['ordinal']==ordinal)
inv=row['invocation_digest']
checkpoint_path=task_state/'continuation'/('checkpoint-'+inv+'.json')
settings_path=state/'role-settings'/('receipt-'+inv+'.json')
metadata_path=next(path for path in (task_state/'learning-invocations').glob('*.json')
    if not path.stem.endswith('.problem') and
        (reader._json(path)['role'],reader._json(path)['ordinal'])==(role,ordinal))
paths={'reservation':reservation_path,'checkpoint':checkpoint_path,
    'settings':settings_path,'metadata':metadata_path}
original={name:path.read_bytes() for name,path in paths.items()}
def snapshots():
    return {str(path.relative_to(state)):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (task_state/'continuation/snapshots').rglob('*') if path.is_file()}
original_snapshots=snapshots()
failures={}
def select():
    fresh=SourceReader(state,root,read=primary_read)
    try: Verifier(fresh,session).selection('feature','executor','typing')
    except Exception as error: return str(error)
    return 'USABLE'
for change in ((requested,) if requested!='missing_review' else ()):
    baseline=select()
    assert baseline=='USABLE', (change,baseline)
    try:
        if change.startswith('missing_') and change[8:] in paths:
            paths[change[8:]].unlink()
        elif change.startswith('partial_'):
            paths[change[8:]].write_text('{')
        elif change.endswith('_blob'):
            stage='before' if 'before' in change else 'after'
            snap=task_state/'continuation/snapshots'/digest({'invocation':inv,'stage':stage})
            env={**os.environ,'GIT_INDEX_FILE':str(snap/'index'),'GIT_OPTIONAL_LOCKS':'0'}
            indexed=subprocess.check_output(['git','--git-dir',str(snap/'objects.git'),
                'ls-files','--stage','--','code.py'],env=env)
            blob=indexed.split()[1].decode()
            blob_path=snap/'objects.git/objects'/blob[:2]/blob[2:]
            blob_bytes=blob_path.read_bytes()
            if change.startswith('missing_'): blob_path.unlink()
            else:
                raw=zlib.decompress(blob_bytes)
                changed=raw.replace(b'VALUE = 1',b'VALUE = 9')
                changed=changed.replace(b'VALUE = 2',b'VALUE = 9')
                assert changed!=raw
                blob_path.write_bytes(zlib.compress(changed))
            try: failures[change]=select()
            finally:
                blob_path.write_bytes(blob_bytes)
                blob_path.chmod(0o600)
            continue
        elif change in {'missing_before','missing_after','before_index','after_index'}:
            stage='before' if 'before' in change else 'after'
            snap=task_state/'continuation/snapshots'/digest({'invocation':inv,'stage':stage})
            index=snap/'index'
            index_bytes=index.read_bytes()
            if change.startswith('missing_'):
                saved=snap.with_name(snap.name+'.retained')
                snap.rename(saved)
            else:
                index.write_bytes(b'synthetic partial index')
            try: failures[change]=select()
            finally:
                if change.startswith('missing_'): saved.rename(snap)
                else: index.write_bytes(index_bytes)
            continue
        elif change.startswith('checkpoint_') and change!='checkpoint_digest':
            document=json.loads(original['checkpoint'])
            if change=='checkpoint_run': document['run_id']='f'*32
            elif change=='checkpoint_ordinal': document[role+'_calls']=ordinal+1
            else: document['repair_iterations']=2 if ordinal==1 else 0
            checkpoint_path.write_text(json.dumps(document))
        elif change in {'requested','effective','output_digest','observed_exit'}:
            document=json.loads(original['settings'])
            if change in {'requested','effective'}:
                document['receipt'][change]['reasoning']='high' if ordinal==1 else 'low'
            elif change=='output_digest':
                document['terminal']['validated_output_digest']='f'*64
            else: document['terminal']['adapter_exit_code']=1
            settings_path.write_text(json.dumps(document))
        elif change.startswith('metadata_'):
            document=json.loads(original['metadata'])
            if change=='metadata_ordinal':
                document['ordinal']=3 if ordinal==1 else 1
                if role=='executor': document['repair_iteration']=document['ordinal']-1
                document['invocation_key']=records.invocation_id(document)
            elif change=='metadata_catalog': document['catalog_digest']='f'*64
            else: document['status']='UNKNOWN'
            metadata_path.write_text(json.dumps(learning.seal(document)))
        else:
            document=deepcopy(row)
            if change=='unknown_reservation': document['status']='UNKNOWN'
            elif change=='identity': document['identity']['policy_sha']='f'*40
            elif change.startswith('snapshot_'):
                document[change.removesuffix('_digest')]='f'*64
            else: document[change]='f'*64
            reservation_path.write_text(json.dumps(document))
        failures[change]=select()
    finally:
        for name,path in paths.items():
            path.write_bytes(original[name])
            path.chmod(0o600)
    assert canonical_path.read_bytes()==canonical_bytes
if role=='controller' and requested=='missing_review':
    baseline=select()
    assert baseline=='USABLE', ('missing_review',baseline)
    review_path=task_state/'continuation'/('review-'+inv+'.json')
    review_bytes=review_path.read_bytes()
    review_path.unlink()
    try: failures['missing_review']=select()
    finally:
        review_path.write_bytes(review_bytes)
        review_path.chmod(0o600)
restored=select()
assert restored=='USABLE', (requested,restored)
assert canonical_path.read_bytes()==canonical_bytes
assert snapshots()==original_snapshots
print(json.dumps({'failures':failures,'calls':[3,2]}))"""
    )
    cases: tuple[str, ...] = (
        "missing_reservation",
        "partial_reservation",
        "unknown_reservation",
        "contract_digest",
        "consumed_digest",
        "checkpoint_digest",
        "invocation_digest",
        "identity",
        "missing_checkpoint",
        "checkpoint_run",
        "checkpoint_ordinal",
        "checkpoint_repair",
        "missing_settings",
        "partial_settings",
        "requested",
        "effective",
        "output_digest",
        "observed_exit",
        "metadata_ordinal",
        "metadata_catalog",
        "metadata_unknown",
        "snapshot_before_digest",
        "snapshot_after_digest",
        "missing_before",
        "missing_after",
        "before_index",
        "after_index",
        "missing_before_blob",
        "missing_after_blob",
        "corrupt_before_blob",
        "corrupt_after_blob",
    )
    if role == "controller":
        cases += ("missing_review",)
    for requested in cases:
        # Each independent fault probe starts a new bounded read invocation;
        # original task state, consumed role calls and all source guards persist.
        result = child(
            value,
            "requested=" + repr(requested) + "\n" + body,
            timeout_seconds=600,
        )
        assert result["calls"] == [3, 2]
        assert set(result["failures"]) == {requested}
        assert all(
            code
            not in {"USABLE", "LEARNING_CI_SOURCE_MISSING", "WAIT_INVOCATION_EXPIRED"}
            for code in result["failures"].values()
        ), (requested, result)


@pytest.mark.parametrize("stage", ["before", "after"])
@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_active_snapshot_digest_cannot_replace_missing_original_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, fault: str
) -> None:
    value = active_run(tmp_path, monkeypatch)
    assert child(value, SELECT) == {"code": "USABLE"}
    result = child(
        value,
        "stage="
        + repr(stage)
        + "\nfault="
        + repr(fault)
        + "\n"
        + BIND_PRIMARY
        + """
task_state=state/'tasks/303'
import os,subprocess,zlib
from tools import agent_epic_runtime_cli as cli
from tools.agent_continuation import digest
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_verify import Verifier
session=cli.build_session(state,root,wake=True)
row=reader._json(next((task_state/'continuation').glob('reservation-*.json')))
snap=task_state/'continuation/snapshots'/digest({'invocation':row['invocation_digest'],'stage':stage})
env={**os.environ,'GIT_INDEX_FILE':str(snap/'index'),'GIT_OPTIONAL_LOCKS':'0'}
original=subprocess.check_output(['git','--git-dir',str(snap/'objects.git'),
    'ls-files','--stage','--','code.py'],env=env)
blob=original.split()[1].decode()
path=snap/'objects.git/objects'/blob[:2]/blob[2:]
assert path.exists()
blob_bytes=path.read_bytes()
Verifier(SourceReader(state,root,read=primary_read),session).selection(
    'feature','executor','typing')
if fault=='missing': path.unlink()
else:
    raw=zlib.decompress(blob_bytes)
    changed=raw.replace(b'VALUE = 1',b'VALUE = 9')
    changed=changed.replace(b'VALUE = 2',b'VALUE = 9')
    assert changed!=raw
    path.write_bytes(zlib.compress(changed))
try:
    fresh=SourceReader(state,root,read=primary_read)
    try: Verifier(fresh,session).selection('feature','executor','typing')
    except Exception as error: code=str(error)
    else: code='USABLE'
finally:
    path.write_bytes(blob_bytes)
    path.chmod(0o600)
assert subprocess.check_output(['git','--git-dir',str(snap/'objects.git'),
    'ls-files','--stage','--','code.py'],env=env)==original
print(json.dumps({'code':code}))""",
        timeout_seconds=180,
    )
    assert result["code"] not in {"USABLE", "LEARNING_CI_SOURCE_MISSING"}
    assert child(value, SELECT) == {"code": "USABLE"}
