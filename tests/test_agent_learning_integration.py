"""Synthetic frozen multi-task workflow sources, never production activation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tools import agent_continuation_backend as backend
from tools import agent_epic_runtime_cli as guards
from tools.agent_continuation import digest
from tools.agent_learning_planning_catalog import catalog_digest
from tools.agent_orchestrate import StateStore

from tests import test_agent_continuation_factory as factory
from tests.test_agent_learning import profile, rule
from tests.test_agent_learning_evidence import child

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX frozen sources")


def multi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    controller_rule: bool = False,
    initial_adapter_timeout: int = 120,
) -> dict[str, str]:
    source = tmp_path / "source"
    shutil.copytree(
        Path.cwd() / "tools",
        source / "tools",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(Path.cwd() / "docs/schemas", source / "docs/schemas")
    shutil.copytree(Path.cwd() / ".github/workflows", source / ".github/workflows")
    base = tmp_path / "fixture"
    native = (
        "#!" + sys.executable + "\nimport sys\n"
        'if "--version" in sys.argv:\n print("codex-cli 0.157.1")\n'
        "else:\n raise SystemExit(1)\n"
    )
    from tests.test_agent_continuation_backend import document

    owner_backend = document()
    owner_backend["source"].update(
        executable=str(base / "state/native-fixture"),
        executable_sha256=hashlib.sha256(native.encode()).hexdigest(),
        version_digest=hashlib.sha256(b"codex-cli 0.157.1").hexdigest(),
    )
    pinned = rule()
    pinned["environment"]["python_minor"] = (
        f"{sys.version_info.major}.{sys.version_info.minor}"
    )
    pinned.update(task_class="feature")
    pinned["environment"]["backend_digest"] = digest(backend.document(owner_backend))
    pinned["regression_checks"][0]["argv_digest"] = digest(["mypy", "."])
    rules = [pinned]
    if controller_rule:
        controller = copy.deepcopy(pinned)
        controller.update(rule_id="controller-typing", role="controller")
        rules.append(controller)
    catalog = {"schema_version": "1.0", "rules": rules}
    (source / "docs/schemas/agent-learning-catalog.json").write_text(
        json.dumps(catalog)
    )
    monkeypatch.setattr(factory, "SOURCE", source)
    value = factory.build(
        base,
        extra_tasks=(302, 303),
        delivery_enabled=True,
        with_demo=True,
        initial_adapter_timeout=initial_adapter_timeout,
    )
    store = StateStore(Path(value["state"]))
    plans = store.load("approved-plans")
    config_seed = store.load("epic-runtime")
    assert plans is not None and config_seed is not None
    from tools.agent_coordinator_handover import handover_digest

    recipe = copy.deepcopy(plans["tasks"]["301"])
    plan = recipe["plan"]
    from tools import agent_gate

    root = Path(value["root"])
    original = (root / "code.py").read_bytes()
    (root / "code.py").write_text("VALUE = 2\n")
    try:
        with agent_gate.trusted_snapshot_session(
            root=root, base_sha=value["sha"], allowed_paths=("code.py",)
        ) as preview:
            subprocess.run(
                [
                    "git",
                    "fetch",
                    "--quiet",
                    str(preview.checkout.parent / "mirror.git"),
                    preview.snapshot_commit,
                ],
                cwd=root,
                check=True,
            )
            future_merge = subprocess.check_output(
                [
                    "git",
                    "commit-tree",
                    preview.tree_hash,
                    "-p",
                    value["sha"],
                    "-p",
                    preview.snapshot_commit,
                    "-m",
                    "synthetic primary merge",
                ],
                cwd=root,
                text=True,
                env={
                    **os.environ,
                    "GIT_AUTHOR_DATE": "1970-01-01T00:00:00Z",
                    "GIT_COMMITTER_DATE": "1970-01-01T00:00:00Z",
                },
            ).strip()
    finally:
        (root / "code.py").write_bytes(original)
    plan.update(
        task=303,
        head_ref="codex/303-next",
        base_sha=future_merge,
        head_sha=future_merge,
    )
    plan["criteria_source"]["issue"] = 303
    plan["approval"]["plan_digest"] = handover_digest(plan)
    recipe["demo_plan"]["task"] = 303
    plans["tasks"]["303"] = recipe
    config_seed["approved_plans_digest"] = guards.canonical_digest(plans)
    store.save("approved-plans", plans)
    store.save("epic-runtime", config_seed)
    config, adapter, launch = (
        store.load(name) for name in ("epic-runtime", "runtime-adapter", "launch")
    )
    assert config is not None and adapter is not None and launch is not None
    owner = profile()
    owner.update(
        policy_sha=value["sha"],
        mandate_digest=config["approved_mandate_digest"],
        catalog_digest=catalog_digest(catalog, {"schema_version": "1.0", "rules": []}),
    )
    store.save("learning-profile", owner)
    config.update(
        schema_version="7.0", approved_learning_digest=guards.canonical_digest(owner)
    )
    launch["runtime_config_digest"] = guards.launch_config_digest(config, adapter)
    config["approved_launch_digest"] = guards.canonical_digest(launch)
    store.save("epic-runtime", config)
    store.save("launch", launch)
    control = store.load("launch-control")
    assert control is not None
    control["launch_digest"] = config["approved_launch_digest"]
    store.save("launch-control", control)
    return value


def roles(value: dict[str, str]) -> None:
    (Path(value["state"]) / "synthetic-roles.py").write_text("""import sys,json
from pathlib import Path
policy,source,state,root=map(Path,sys.argv[1:5])
role=sys.argv[5]
sys.path.insert(0,str(source));sys.path.insert(0,str(policy))
from tools.agent_continuation_backend import completed_receipt
from tools.agent_continuation import digest
from tools.agent_epic_runtime_cli import private_json
from tools.agent_orchestrate import TaskContract,ActionPermissions
from tests.test_agent_orchestrator import _executor_report,_verdict
payload=json.load(sys.stdin)
if role=='executor':
    fields=dict(payload['contract']);fields['permissions']=ActionPermissions(**fields['permissions'])
    for key in ('acceptance_criteria','allowed_paths','delivery_criterion_indices'):
        fields[key]=tuple(fields[key])
    if fields['issue']==301: (root/'code.py').write_text('VALUE = 2\\n')
    output=_executor_report(TaskContract(**fields),payload['head_sha'])
else: output=_verdict(payload,'PASS')
owner=private_json(state/'role-backend.json')
observed={'model':owner['model'],'reasoning':['low','medium','high'][payload['repair_iteration']],
    'thread_digest':'1'*64,'turn_digest':'2'*64}
receipt,terminal=completed_receipt(owner,observed,role=role,invocation=digest(payload),output=output)
print(json.dumps({'role_output':output,'settings_receipt':receipt,'terminal_fact':terminal}))
""")


SETUP = """import copy,subprocess,os
from tools import agent_epic_runtime_cli as cli
from tools import agent_coordinator_handover as handover
from tools import agent_orchestrate as workflow
from tools import agent_epic_loop as loop
from tools import agent_gate
from tools.agent_continuation import digest
handover.GitHubCLI.issue=lambda self,repo,number:{'number':number,'state':'OPEN',
    'parent':{'number':294},'body':'synthetic criterion\\n'}
session=cli.build_session(state,root,wake=True)
def git(*args):
    return subprocess.run(['git',*args],cwd=root,text=True,
        capture_output=True,check=True,env={**os.environ,
        'GIT_AUTHOR_DATE':'1970-01-01T00:00:00Z',
        'GIT_COMMITTER_DATE':'1970-01-01T00:00:00Z'}).stdout.strip()
def configure(engine):
    reviewed=engine.bootstrap.reviewed
    adapter=reviewed.adapter
    if 'api' in globals(): adapter.learning_runtime.reader.read=api
    prefix=[sys.executable,'-B',str(state/'synthetic-roles.py'),str(reader.policy),str(source),str(state),str(root)]
    synthetic=workflow.CommandAdapter(prefix+['executor'],prefix+['controller'],root=root,
        timeout_seconds=10,output_limit=65536)
    def transport(command,payload):
        adapter.delegate.guard()
        target=(synthetic.executor_command if command==adapter.executor_command
            else synthetic.controller_command)
        return synthetic._call(target,payload)
    adapter.delegate._call=transport
    return reviewed
observed_gates={}
def gate(*,root,base_sha,allowed_paths,observer_factory=None):
    with agent_gate.trusted_snapshot_session(root=root,
        base_sha=base_sha,allowed_paths=allowed_paths) as snap:
        observer=observer_factory(snap) if observer_factory is not None else None
        failing=(git('branch','--show-current')=='codex/302-prior'
            or globals().get('force_gate_failure',False))
        checks=[agent_gate.CheckResult(name,agent_gate.CHECK_COMMANDS[name],
            'failed' if failing and name=='mypy' else 'passed',
            1 if failing and name=='mypy' else 0,
            0.0,'failed' if failing and name=='mypy' else 'passed',{})
            for name in agent_gate.PROFILES[agent_gate.FULL_PROFILE]]
        for check in checks:
            if observer is not None: observer(check)
        evidence=agent_gate.build_evidence(snap,checks)
        observed_gates[git('branch','--show-current')]=evidence
        return evidence
agent_gate.run_repository_full=gate
initial=cli.private_json(state/'handover.json')
prior=copy.deepcopy(initial)
prior.update(task=302,head_ref='codex/302-prior')
prior['criteria_source']['issue']=302
prior['budgets']['max_repair_iterations']=0
approved=handover.handover_digest(prior);prior['approval']['plan_digest']=approved
(state/'tasks').mkdir(mode=0o700)
where=state/'tasks/302';where.mkdir(mode=0o700)
workflow.StateStore(where).save('handover',prior)
git('switch','-c','codex/302-prior')
seed=session.store.load()
checkpoint=copy.deepcopy(seed);checkpoint.update(task=302,head_ref='codex/302-prior')
store=loop.CheckpointStore(where,root);store.save(checkpoint)
engine=session.task_adapter(store,0);reviewed=configure(engine)
try:
    with loop.RunnerLock(where), session.wait_scope.context('TASK:302:RUN_TASK'):
        reviewed.run()
except loop.PhaseBlocked as error:
    assert error.machine_code=='WORKFLOW_REVIEW_NOT_VERIFIED'
failure=reader.workflow(302)
assert failure.state['status']=='FAIL_ESCALATE' and failure.state['executor_calls']==1
assert any(check['outcome']=='FAIL'
    for row in reader.gate(302) for check in row['checks'])
git('switch','codex/301-test')
engine=session.task_adapter(session.store,0);reviewed=configure(engine)
with session.wait_scope.context('TASK:301:RUN_TASK'): reviewed.run()
correction=reader.workflow(301)
assert correction.state['status']=='PASS'
assert correction.state['run_id']!=failure.state['run_id']
assert reader.controller(301)['status']=='PASS'
"""


def test_two_frozen_tasks_retain_distinct_failure_and_correction_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = multi(
        tmp_path,
        monkeypatch,
        initial_adapter_timeout=300,
    )
    roles(value)
    body = (
        SETUP
        + """print(json.dumps({'before':failure.state['status'],
    'after':correction.state['status'],
    'failed_checks':len(reader.gate(302)),'corrected_checks':len(reader.gate(301))}))"""
    )
    assert child(
        value,
        body,
        timeout_seconds=360,
    ) == {
        "before": "FAIL_ESCALATE",
        "after": "PASS",
        "failed_checks": 4,
        "corrected_checks": 4,
    }


DELIVERY = """from dataclasses import asdict
sys.path.insert(1,str(source))
from tests.learning_delivery_fixture import PrimaryAPI
from tools import agent_epic_delivery as delivery
from tools.agent_epic_snapshot import commit_reviewed_snapshot
artifact=reviewed.artifacts()
snapshot=artifact['gate']['snapshot']
head=snapshot['snapshot_commit'];base=initial['base_sha']
api=PrimaryAPI(base,head)
reader.read=api
ledger=delivery.Ledger(state,root,read=api)
mandate,approval,context=session.authority(0)
common={'schema_version':'3.0','policy_sha':reader.owner['policy_sha'],
    'repository':'OWNER/repo','epic':294,'task':301,'pr':None,
    'base_ref':'roadmap/294-autonomy','base_sha':base,'head_ref':'codex/301-test'}
def request(operation,sha,pr=None,merge=None):
    fields={**common,'head_sha':sha,'pr':pr,'merge_sha':merge,'operation':operation}
    return delivery.Request(operation_id=digest(fields),**fields)
commit=request('commit_task',base)
result=commit_reviewed_snapshot(commit,root=root,evidence=artifact['gate'],
    review_receipt=artifact['review_receipt'],mandate=mandate,
    approved_mandate_digest=approval,mandate_context=context,ledger=ledger,
    live_revalidate=lambda item:git('rev-parse','HEAD')==item.head_sha)
assert result.status=='APPLIED', result.machine_code
assert git('rev-parse','HEAD')==head
api.merge=git('commit-tree',snapshot['tree_hash'],'-p',base,'-p',head,
    '-m','synthetic primary merge')
def effect(item):
    if item.operation=='push_task': return {'head_sha':item.head_sha}
    if item.operation=='create_task_pr': return {'pr':7,'head_sha':item.head_sha}
    if item.operation=='merge_task_pr':
        api.merged=True
        git('update-ref','refs/heads/roadmap/294-autonomy',api.merge)
        return {'merge_sha':api.merge}
    if item.operation=='close_task': return {'task':301,'merge_sha':api.merge}
    return {'comment_id':8,'merge_sha':api.merge}
for operation in ('push_task','create_task_pr','merge_task_pr',
                  'close_task','update_epic'):
    item=request(operation,head,
        pr=7 if operation in {'merge_task_pr','close_task','update_epic'} else None,
        merge=api.merge if operation in {'close_task','update_epic'} else None)
    assessment={**asdict(item),'schema_version':'1.0','status':'pass','machine_code':'OK',
        'phase':'post-merge' if item.merge_sha else 'pr',
        'github_checks':[{'name':name,'state':'SUCCESS'}
                         for name in delivery.REQUIRED_CHECKS]}
    result=delivery.deliver(item,mandate=mandate,approved_mandate_digest=approval,
        assessment=assessment,ledger=ledger,mandate_context=context,
        revalidate=lambda item:git('rev-parse','HEAD')==item.head_sha,
        effect=effect)
    assert result.status=='APPLIED',result.machine_code
saved=session.store.load();saved.update(head_sha=head,pr=7,merge_sha=api.merge,
    phase='TASK_DONE',completed_phases=list(loop.PHASES[1:7]),pending_phase=None)
session.store.save(saved)
engine.github.ref_sha=lambda repo,ref:base if ref=='main' else api.merge
engine.learning_reader=reader
from tools.agent_learning_observe import delivered
with session.wait_scope.context('TASK:301:TASK_DONE'):
    observation=delivered(reader,301,engine)
assert observation['kind']=='TASK_DELIVERY'
from tools.agent_learning_verify import Verifier
verifier=Verifier(reader,session)
# Historical demo uses original TaskPhaseRuntime, same reviewed recipe/source refs.
from tools.agent_epic_runtime import TaskPhaseRuntime
TaskPhaseRuntime._github_read=lambda self,argv:api(argv)
with session.wait_scope.context('TASK:301:TASK_DONE'):
    promoted=verifier.candidates(301)
assert len(promoted)==1
with session.wait_scope.context('TASK:301:TASK_DONE'):
    hints=verifier.selection('feature','executor','typing')
assert len(hints['hints'])==1
assert hints['hints'][0]['permissions'] is None
from tools.agent_learning_invocation import Runtime
from tools.codex_adapter import _prompt
next_payload={'contract':asdict(failure.contract),'head_sha':head}
with session.wait_scope.context('TASK:301:TASK_DONE'):
    augmented=Runtime(reader,session).augment('executor',next_payload,
        current_gate=observed_gates['codex/302-prior'])
assert augmented['contract']==next_payload['contract']
assert augmented['untrusted_verified_hints']==hints
assert hints['hints'][0]['minimal_fix'] in _prompt('executor',augmented)
"""


def test_original_failure_delivery_and_next_role_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = multi(
        tmp_path,
        monkeypatch,
        initial_adapter_timeout=300,
    )
    roles(value)
    body = (
        SETUP
        + DELIVERY
        + """print(json.dumps({'promoted':len(promoted),
    'selected':len(hints['hints']),'merge_differs':api.merge!=api.head}))"""
    )
    assert child(value, body, timeout_seconds=360) == {
        "promoted": 1,
        "selected": 1,
        "merge_differs": True,
    }


NEXT_CALL = """next_plan=copy.deepcopy(session.plans['tasks']['303']['plan'])
assert next_plan['head_sha']==api.merge
next_dir=state/'tasks/303';next_dir.mkdir(mode=0o700)
workflow.StateStore(next_dir).save('handover',next_plan)
git('switch','-c','codex/303-next',api.merge)
next_checkpoint=copy.deepcopy(seed)
next_checkpoint.update(task=303,head_ref='codex/303-next',
    head_sha=api.merge,base_sha=api.merge)
next_store=loop.CheckpointStore(next_dir,root);next_store.save(next_checkpoint)
next_engine=session.task_adapter(next_store,0)
next_reviewed=configure(next_engine)
original_gate=agent_gate.run_repository_full
count=0
def next_gate(**kwargs):
    global count,force_gate_failure
    count+=1
    force_gate_failure=(count==1)
    return original_gate(**kwargs)
agent_gate.run_repository_full=next_gate
try:
    with loop.RunnerLock(next_dir), session.wait_scope.context('TASK:303:RUN_TASK'):
        next_reviewed.run()
except loop.PhaseBlocked:
    facts=reader.workflow(303)
    raise AssertionError({'status':facts.state['status'],
        'codes':facts.state['failures'],'calls':facts.state['executor_calls']})

from tools.agent_learning_measure import observed
measured,invocations=observed(reader,session,303)
applied=[item for item in invocations if item['lesson_ids']]
assert len(applied)==1 and applied[0]['role']=='executor'
assert applied[0]['status']=='COMPLETED' and applied[0]['ordinal']==2
assert applied[0]['lesson_ids']==[hints['hints'][0]['lesson_id']]
assert measured['calls']['executor']==2
print(json.dumps({'completed_hinted_calls':len(applied),'executor_calls':2}))
"""


def test_verified_hint_reaches_actual_next_role_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = multi(
        tmp_path,
        monkeypatch,
        initial_adapter_timeout=300,
    )
    roles(value)
    assert child(value, SETUP + DELIVERY + NEXT_CALL, timeout_seconds=360) == {
        "completed_hinted_calls": 1,
        "executor_calls": 2,
    }


def test_controller_problem_uses_original_same_run_failure_and_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = multi(
        tmp_path,
        monkeypatch,
        controller_rule=True,
        initial_adapter_timeout=300,
    )
    roles(value)
    body = SETUP + DELIVERY.replace(
        "assert len(promoted)==1", "assert len(promoted)==2"
    )
    # Initial executor consumes the ordinary empty selection; only its repair
    # and the controller after correction have an original current failure.
    replacement = """assert len(applied)==2
assert {item['role'] for item in applied}=={'executor','controller'}
applied=[item for item in applied if item['role']=='executor']"""
    next_call = NEXT_CALL.replace(
        "assert len(applied)==1 and applied[0]['role']=='executor'",
        replacement,
    ).replace(
        "print(json.dumps({'completed_hinted_calls':len(applied),'executor_calls':2}))",
        "",
    )
    body += next_call
    body += """
from tools.agent_learning_gate_problem import controller_problem
fact=reader.workflow(303)
problem,proof=controller_problem(reader,303,fact.state)
assert problem=='typing' and proof is not None
assert proof['failed']['executor_ordinal']==1
assert proof['corrected']['executor_ordinal']==2
assert proof['failed']['gate']['status']=='failed'
assert proof['corrected']['gate']['status']=='passed'
print(json.dumps({'problem':problem,'ordinals':[1,2]}))
"""
    assert child(value, body, timeout_seconds=360) == {
        "problem": "typing",
        "ordinals": [1, 2],
    }


@pytest.mark.parametrize("missing", ["prior", "current", "directory"])
def test_controller_problem_rejects_missing_consumed_gate_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    value = multi(tmp_path, monkeypatch, controller_rule=True)
    roles(value)
    correction_start = (
        "engine=session.task_adapter(session.store,0);reviewed=configure(engine)"
    )
    repair_gate = """original_gate=agent_gate.run_repository_full
gate_count=0
def fail_then_pass(**kwargs):
    global gate_count,force_gate_failure
    gate_count+=1
    force_gate_failure=(gate_count==1)
    return original_gate(**kwargs)
agent_gate.run_repository_full=fail_then_pass
"""
    body = SETUP.replace(correction_start, repair_gate + correction_start)
    body += """
import shutil
from tools.agent_learning import LearningError
from tools.agent_learning_gate_problem import controller_problem
canonical=correction.state
assert canonical['executor_calls']==2
problem,proof=controller_problem(reader,301,canonical)
assert problem=='typing' and proof is not None
assert proof['failed']['executor_ordinal']==1
assert proof['corrected']['executor_ordinal']==2
assert proof['failed']['gate']['status']=='failed'
assert proof['corrected']['gate']['status']=='passed'
directory=reader.task_directory(301)/'learning-source/full-gates'
assert len(list(directory.glob('*.json')))==2
"""
    body += f"missing={json.dumps(missing)}\n"
    body += """if missing=='directory':
    shutil.rmtree(directory)
else:
    ordinal=1 if missing=='prior' else 2
    (directory/(canonical['run_id']+'-'+str(ordinal)+'.json')).unlink()
try:
    controller_problem(reader,301,canonical)
except LearningError as error:
    assert error.machine_code=='LEARNING_SOURCE_MISSING',error.machine_code
else:
    raise AssertionError('missing consumed gate source silently ignored')
print(json.dumps({'missing':missing,'executor_calls':canonical['executor_calls']}))
"""
    assert child(value, body, timeout_seconds=120) == {
        "missing": missing,
        "executor_calls": 2,
    }


def test_controller_problem_zero_calls_and_all_success_have_no_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = multi(tmp_path, monkeypatch, controller_rule=True)
    roles(value)
    correction_start = (
        "engine=session.task_adapter(session.store,0);reviewed=configure(engine)"
    )
    zero_calls = """from tools.agent_learning_gate_problem import controller_problem
directory=reader.task_directory(301)/'learning-source/full-gates'
assert not directory.exists()
assert controller_problem(reader,301,{'executor_calls':0})==(None,None)
"""
    body = SETUP.replace(correction_start, correction_start + "\n" + zero_calls)
    body += """
canonical=correction.state
assert canonical['executor_calls']==1
assert directory.is_dir()
assert (directory/(canonical['run_id']+'-1.json')).is_file()
assert controller_problem(reader,301,canonical)==(None,None)
print(json.dumps({'zero_calls_empty':True,'verified_all_success_empty':True}))
"""
    assert child(value, body, timeout_seconds=120) == {
        "zero_calls_empty": True,
        "verified_all_success_empty": True,
    }
