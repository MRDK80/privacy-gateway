"""Prospective config8 association uses real originals and bounded fake roles."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tools import agent_epic_runtime_cli as guards
from tools import agent_final_preparation_records as final_records
from tools.agent_orchestrate import StateStore

from tests.test_agent_learning_evidence import child
from tests.test_agent_learning_integration import (
    DELIVERY,
    NEXT_CALL,
    SETUP,
    multi,
    roles,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX original ownership")


def initial_config8(value: dict[str, str]) -> None:
    """Synthetic original owner setup BEFORE any frame/role history; no backfill."""
    seed = Path(value["state"])
    assert not (seed / "invocations").exists()
    store = StateStore(seed)
    config, launch, adapter, control, mandate = (
        store.load(name)
        for name in (
            "epic-runtime",
            "launch",
            "runtime-adapter",
            "launch-control",
            "mandate",
        )
    )
    assert all(item is not None for item in (config, launch, adapter, control, mandate))
    assert config is not None and launch is not None and adapter is not None
    assert control is not None and mandate is not None
    assert config["schema_version"] == "7.0"
    profile = final_records.seal(
        {
            "schema_version": "1.0",
            "repository": mandate["repository"],
            "epic": mandate["epic"],
            "roadmap_ref": mandate["roadmap_ref"],
            "policy_sha": mandate["policy_sha"],
            "owner_identity": mandate["owner_identity"],
            "mandate_digest": config["approved_mandate_digest"],
            "max_report_chars": 20000,
            "max_artifact_bytes": 262144,
            "max_final_model_calls": 1,
        }
    )
    config.update(
        schema_version="8.0",
        approved_final_digest=None,
        approved_final_preparation_digest=guards.canonical_digest(profile),
    )
    launch["runtime_config_digest"] = guards.launch_config_digest(config, adapter)
    config["approved_launch_digest"] = guards.canonical_digest(launch)
    control["launch_digest"] = config["approved_launch_digest"]
    for name, item in (
        ("epic-runtime", config),
        ("launch", launch),
        ("launch-control", control),
        ("final-preparation-profile", profile),
    ):
        store.save(name, item)
    identity = {
        "repository": mandate["repository"],
        "epic": mandate["epic"],
        "mandate_digest": config["approved_mandate_digest"],
        "launch_digest": config["approved_launch_digest"],
    }
    zero = final_records.seal(
        {
            "schema_version": "1.0",
            **identity,
            "calls_consumed": 0,
            "pending_reservation_digest": None,
        }
    )
    shared = {**identity, "owner_identity": mandate["owner_identity"]}
    references = {
        "preparation_digest": config["approved_final_preparation_digest"],
        "counter_zero_digest": guards.canonical_digest(zero).removeprefix("sha256:"),
    }
    command = final_records.seal(
        {
            "schema_version": "1.0",
            **shared,
            **references,
            "kind": "OWNER_ORIGINAL_FINAL_COUNTER_PROVISIONING",
            "procedure": "ADR303_ORIGINAL_COUNTER_PROVISIONING_V1",
            "policy_sha": mandate["policy_sha"],
        }
    )
    approval = final_records.seal(
        {
            "schema_version": "1.0",
            **shared,
            **references,
            "owner_command_digest": guards.canonical_digest(command).removeprefix(
                "sha256:"
            ),
            "approved_at": int(config["started_at"]),
        },
        self_digest="approved_digest",
    )
    receipt = final_records.seal(
        {
            "schema_version": "1.0",
            **shared,
            **references,
            "kind": "OWNER_FINAL_COUNTER_INITIALIZATION",
            "approved_at": int(config["started_at"]),
            "owner_command_digest": guards.canonical_digest(command).removeprefix(
                "sha256:"
            ),
            "initialization_digest": guards.canonical_digest(approval).removeprefix(
                "sha256:"
            ),
        }
    )
    pin = final_records.seal(
        {
            "schema_version": "1.0",
            **shared,
            "initialization_digest": guards.canonical_digest(approval).removeprefix(
                "sha256:"
            ),
            "installation_receipt_digest": guards.canonical_digest(
                receipt
            ).removeprefix("sha256:"),
        }
    )
    for directory in (seed / "final-initialization", seed / "final-preparation"):
        directory.mkdir(mode=0o700)
    for name, item in (
        ("command-source", command),
        ("approval", approval),
        ("install-receipt", receipt),
        ("approval-pin", pin),
    ):
        path = seed / "final-initialization" / (name + ".json")
        path.write_text(json.dumps(item))
        path.chmod(0o600)
    path = seed / "final-preparation/role-state.json"
    path.write_text(json.dumps(zero))
    path.chmod(0o600)
    guards.final_preseed_guard(seed, Path(value["root"]))


OBSERVE = r"""
from tools.agent_learning_workflow_binding import Binding
from tools.agent_learning_binding_records import binding
from tools.agent_learning_store import Store
from tools.agent_learning_invocation import Runtime
native_count=[]
original_configure=configure
def configure(engine):
    reviewed=original_configure(engine)
    transport=reviewed.adapter.delegate._call
    def observed(command,payload):
        native_count.append((payload['contract']['issue'],payload['repair_iteration']))
        return transport(command,payload)
    reviewed.adapter.delegate._call=observed
    return reviewed
"""


BOUND_POSITIVE = r"""
# Observe the real orchestrator-provided writer context; never manufacture it.
from tools.codex_adapter import _prompt
# Diagnostic only: inspect the still-active exception at original terminal save.
import sys
original_diagnostic_terminal_save=workflow.StateStore.save
def diagnostic_terminal_save(self,run_key,value):
    active=sys.exception()
    diagnostic=None
    try:
        if (type(value) is dict and
                self.directory==next_reviewed.directory/'bounded-workflow' and
                value.get('terminal') is True and
                value.get('machine_code')=='INTERNAL_ERROR'):
            frames=[]
            trace=active.__traceback__ if active is not None else None
            while trace is not None and len(frames)<32:
                code=trace.tb_frame.f_code
                module=code.co_filename.rsplit('/',1)[-1].rsplit('\\',1)[-1]
                function=code.co_name
                safe_module=(module.endswith('.py') and
                             module[:-3].isascii() and module[:-3].isidentifier())
                safe_function=(function.isascii() and function.isidentifier())
                frames.append({'module':module if safe_module else 'OTHER',
                               'function':function if safe_function else 'OTHER',
                               'line':trace.tb_lineno})
                trace=trace.tb_next
            name=type(active).__name__ if active is not None else None
            kind=(name if name is not None and name.isascii() and
                  name.isidentifier() else 'OTHER') if active is not None else None
            diagnostic={'active_exception':active is not None,
                        'exception_class':kind,'frames':frames,
                        'truncated':trace is not None}
    except BaseException:
        # Metadata preparation must not replace or skip the original save.
        pass
    result=original_diagnostic_terminal_save(self,run_key,value)
    if diagnostic is not None:
        try:
            print('PGW303_TERMINAL_SAVE_DIAGNOSTIC '+json.dumps(diagnostic),
                  file=sys.stderr,flush=True)
        except BaseException:
            # Save already succeeded; output failure must not replace its result.
            pass
    return result
workflow.StateStore.save=diagnostic_terminal_save
original_positive_augment=Runtime.augment
positive_augmentations=[]
positive_hinted=[]
def observe_positive_augment(self,role,payload,**options):
    assert self is next_reviewed.adapter.learning_runtime
    context=self._workflow_binding
    assert context is not None and context.context is not None
    assert context.context.directory==reader.task_directory(303)
    original_contract=copy.deepcopy(payload['contract'])
    augmented=original_positive_augment(self,role,payload,**options)
    assert augmented['contract']==original_contract
    positive_augmentations.append(role)
    if role=='executor' and augmented['untrusted_verified_hints']['hints']:
        assert augmented['untrusted_verified_hints']==hints
        # Exact original CommandAdapter._call serialization projection.
        safe={key:value for key,value in augmented.items() if key!='contract_object'}
        assert hints['hints'][0]['minimal_fix'] in _prompt('executor',safe)
        positive_hinted.append(augmented['untrusted_verified_hints'])
    return augmented
Runtime.augment=observe_positive_augment
"""

POSITIVE = r"""
assert positive_augmentations==['executor','executor','controller']
assert positive_hinted==[hints]
metadata=[];associations=[]
for task in (302,301,303):
    directory=reader.task_directory(task)
    metadata.extend(json.loads(p.read_text()) for p in
                    (directory/'learning-invocations').glob('*.json')
                    if not p.name.endswith('.problem.json'))
    for path in (directory/'workflow-learning-bindings').glob('*.json'):
        value=binding(json.loads(path.read_text()),
                      max_repairs=reader.contract(task).max_repair_iterations,
                      max_hints=reader.owner['limits']['max_hints_per_role'])
        assert value['invocation_key']==path.stem and value['status']=='COMPLETED'
        assert value['completion']['settings_receipt_digest']
        associations.append(value)
assert len(native_count)==len(associations)==len(metadata)==6
assert {x['role'] for x in associations}=={'executor','controller'}
assert any(x['ordered_lessons'] for x in associations)
assert any(not x['ordered_lessons'] and x['selection_digest']==digest([])
           for x in associations)
for value in associations:
    match=[item for item in metadata if item['invocation_key']==value['invocation_key']]
    assert len(match)==1 and match[0]['status']=='COMPLETED'
    assert value['completion']['completed_metadata_digest']==digest(match[0])
print(json.dumps({'bindings':6,'native_calls':6,'nonempty':True,'zero':True}))
"""

FAULT = r"""
from tools import agent_learning as learning
from tools import agent_learning_companions as companions
original_save=Runtime._save
original_companion=companions.save
original_binding=Binding._write
original_append=Store.append
def fail_metadata(self,directory,value):
    if value['identity']['task']==302:
        raise learning.LearningError('LEARNING_WRITE_FAILED')
    return original_save(self,directory,value)
def fail_companion(reader,directory,name,value,**options):
    if directory.parent.name=='302':
        raise learning.LearningError('LEARNING_WRITE_FAILED')
    return original_companion(reader,directory,name,value,**options)
def fail_binding(self,value,previous):
    result=original_binding(self,value,previous)
    if value['identity']['task']==302 and value['status']=='RESERVED':
        raise learning.LearningError('LEARNING_WRITE_FAILED')
    return result
def fail_append(self,kind,record,key_field):
    if kind=='invocation' and record['identity']['task']==302:
        raise learning.LearningError('LEARNING_WRITE_FAILED')
    return original_append(self,kind,record,key_field)
if FAULT_KIND=='metadata':Runtime._save=fail_metadata
elif FAULT_KIND=='companion':companions.save=fail_companion
elif FAULT_KIND=='binding-reread':Binding._write=fail_binding
else:Store.append=fail_append
try:
    with loop.RunnerLock(where), session.wait_scope.context('TASK:302:RUN_TASK'):
        reviewed.run()
except loop.PhaseBlocked as error:
    assert error.machine_code=='WORKFLOW_REVIEW_NOT_VERIFIED'
else: raise AssertionError('expected original workflow refusal')
canonical=reader._json(where/'bounded-workflow'/(workflow._run_key(reader.contract(302))+'.json'))
assert canonical['executor_calls']==1 and canonical['controller_calls']==0
natives=[reader._json(path) for path in
         (where/'continuation').glob('reservation-*.json')]
assert len(natives)==1
assert natives[0]['status']=='UNKNOWN'
assert canonical['status']=='FAIL_ESCALATE'
assert canonical['machine_code']=='LEARNING_WRITE_FAILED'
assert canonical.get('role_pending') is None
assert (natives[0]['outcome_digest'] is not None)==(FAULT_KIND=='store')
checkpoint=reader._json(where/'continuation'/('checkpoint-'+natives[0]['invocation_digest']+'.json'))
assert checkpoint['executor_calls']==1 and checkpoint['controller_calls']==0
assert natives[0]['checkpoint_digest']==digest(checkpoint)
metas=[json.loads(p.read_text()) for p in (where/'learning-invocations').glob('*.json')
       if not p.name.endswith('.problem.json')]
assert len(metas)==(0 if FAULT_KIND=='metadata' else 1)
if metas:
    assert metas[0]['status']==('COMPLETED' if FAULT_KIND=='store' else 'RESERVED')
if FAULT_KIND=='store':
    from tools import agent_learning_records as original_records
    rows=[json.loads(line) for line in
          (where/'learning/records.jsonl').read_text().splitlines()]
    assert len(rows)==1
    learning.exact(rows[0],{'kind','record'})
    assert rows[0]['kind']=='observation'  # No invocation or unrelated row.
    negative=original_records.observation(rows[0]['record'])
    original_fact=reader.workflow(302)
    assert original_fact.directory==where and original_fact.state==canonical
    assert negative['kind']=='TERMINAL_WORKFLOW'
    assert negative['identity']=={
        'repository':reader.owner['repository'],'epic':reader.owner['epic'],
        'task':302,'run_id':canonical['run_id'],'policy_sha':reader.owner['policy_sha'],
        'head_sha':original_fact.memory['head_sha'],'merge_sha':None}
    assert negative['outcome']=={
        'status':'FAIL_ESCALATE','machine_codes':['LEARNING_WRITE_FAILED']}
    assert sorted(set(canonical['failures']))==['LEARNING_WRITE_FAILED']
    assert negative['metrics']=={
        'findings':canonical['findings'],'repair_iterations':canonical['repair_iterations'],
        'executor_calls':1,'controller_calls':0,
        'duration_seconds':original_fact.memory['duration_seconds']}
    assert negative['source']=={
        field:(digest({'state':canonical,'memory':original_fact.memory})
               if field=='workflow_digest' else None)
        for field in original_records.SOURCE_FIELDS}
assert len(native_count)==(1 if FAULT_KIND=='store' else 0)
paths=list((where/'workflow-learning-bindings').glob('*.json'))
if FAULT_KIND in {'store','binding-reread'}:
    assert len(paths)==1 and json.loads(paths[0].read_text())['status']=='RESERVED'
else: assert paths==[]
assert reviewed.adapter.learning_runtime._workflow_binding.capability is None
# New process-local object cannot recover a completion capability from disk.
before={p:p.read_bytes() for p in where.rglob('*') if p.is_file()}
fresh=Runtime(reader,session)
try: Binding(fresh).complete('executor',{})
except learning.LearningError as error:
    assert error.machine_code=='LEARNING_SOURCE_MISMATCH'
else: raise AssertionError('fresh Runtime recovered completion')
assert {p:p.read_bytes() for p in before}==before
print(json.dumps({'consumed':1,'native_calls':len(native_count),'binding_reserved':bool(paths)}))
"""


def prefix_with_observer() -> str:
    marker = "initial=cli.private_json(state/'handover.json')"
    assert SETUP.count(marker) == 1
    return SETUP.replace(marker, OBSERVE + "\n" + marker)


def test_genuine_config8_same_call_zero_and_hint_associations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = multi(tmp_path, monkeypatch, controller_rule=True)
    fresh_seed = Path(value["state"])
    assert not any((fresh_seed / name).exists() for name in (
        "invocations", "role-settings", "bounded-workflow",
    ))
    assert not any(
        (fresh_seed / "tasks" / str(task) / "bounded-workflow").exists()
        for task in (302, 303)
    )
    initial_store = StateStore(fresh_seed)
    initial_adapter = initial_store.load("runtime-adapter")
    wait_profile = initial_store.load("wait-profile")
    assert initial_adapter is not None and wait_profile is not None
    assert initial_adapter["timeout_seconds"] == 120
    assert wait_profile["limits"] == {
        "max_phase_seconds": 300, "max_read_attempts": 2,
        "max_ci_polls": 2, "max_read_commands": 100,
    }
    initial_adapter["timeout_seconds"] = 300
    initial_store.save("runtime-adapter", initial_adapter)
    initial_config8(value)
    bound_config, bound_launch, bound_control = (
        initial_store.load(name)
        for name in ("epic-runtime", "launch", "launch-control")
    )
    assert bound_config is not None and bound_launch is not None
    assert bound_control is not None
    assert bound_launch["runtime_config_digest"] == guards.launch_config_digest(
        bound_config, initial_adapter
    )
    assert bound_config["approved_launch_digest"] == guards.canonical_digest(
        bound_launch
    )
    assert bound_control["launch_digest"] == bound_config["approved_launch_digest"]
    roles(value)
    # Existing source builders generate actual failures/delivery/current repairs.
    # Existing original3-task role history, no provider and no extra model calls.
    setup = prefix_with_observer()
    # DELIVERY's schema7 standalone augment has no admitted config8 context.
    # Keep its original delivery/selection checks, then exercise augmentation
    # only inside the original next303 orchestrator invocation below.
    unbound = "next_payload={'contract':asdict(failure.contract),'head_sha':head}"
    assert DELIVERY.count(unbound) == 1
    delivery, direct_augmentation = DELIVERY.split(unbound, 1)
    assert "Runtime(reader,session).augment" in direct_augmentation
    assert (
        "assert augmented['contract']==next_payload['contract']" in direct_augmentation
    )
    assert "assert augmented['untrusted_verified_hints']==hints" in direct_augmentation
    assert "minimal_fix" in direct_augmentation
    delivery = delivery.replace("assert len(promoted)==1", "assert len(promoted)==2")
    next_call = NEXT_CALL.replace(
        "print(json.dumps({'completed_hinted_calls':len(applied),'executor_calls':2}))",
        "",
    ).replace(
        "assert len(applied)==1 and applied[0]['role']=='executor'",
        "assert len(applied)==2\n"
        "assert {item['role'] for item in applied}=={'executor','controller'}\n"
        "applied=[item for item in applied if item['role']=='executor']",
    )
    assert child(
        value, setup + delivery + BOUND_POSITIVE + next_call + POSITIVE,
        timeout_seconds=300,
    ) == {
        "bindings": 6,
        "native_calls": 6,
        "nonempty": True,
        "zero": True,
    }


@pytest.mark.parametrize("fault", ["metadata", "companion", "binding-reread", "store"])
def test_consumed_partial_failures_never_dispatch_or_complete_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    value = multi(tmp_path, monkeypatch)
    initial_config8(value)
    roles(value)
    setup = prefix_with_observer()
    marker = (
        "try:\n    with loop.RunnerLock(where), "
        "session.wait_scope.context('TASK:302:RUN_TASK'):"
    )
    assert setup.count(marker) == 1
    body = setup.split(marker, 1)[0] + "\nFAULT_KIND=" + repr(fault) + "\n" + FAULT
    assert child(value, body, timeout_seconds=120) == {
        "consumed": 1,
        "native_calls": 1 if fault == "store" else 0,
        "binding_reserved": fault in {"binding-reread", "store"},
    }


PREPARE_INITIAL = r"""
# Original admitted initial301 producer BEFORE any302 capture/source generation.
assert git('branch','--show-current')=='codex/301-test'
assert native_count==[]
initial_before=session.store.load()
settings_before={p:p.read_bytes() for p in
                 (state/'role-settings').glob('receipt-*.json')}
invocations_before={p:p.read_bytes() for p in (state/'invocations').glob('*.json')}
handover_before=(state/'handover.json').read_bytes()
prepared_initial_engine=session.task_adapter(session.store,0)
prepared_initial_reviewed=configure(prepared_initial_engine)
assert prepared_initial_reviewed.adapter.learning_runtime._workflow_binding is None
assert reader.contract(301).issue==301
from tools.agent_learning_workflow_companions import canonical as original_canonical
assert original_canonical(reader,301) is None
assert session.store.load()==initial_before
assert {p:p.read_bytes() for p in
        (state/'role-settings').glob('receipt-*.json')}==settings_before
assert {p:p.read_bytes() for p in
        (state/'invocations').glob('*.json')}==invocations_before
assert (state/'handover.json').read_bytes()==handover_before
assert not (state/'learning').exists() and not (state/'learning').is_symlink()
assert not list((state/'bounded-workflow').glob('*.json'))
assert not list((state/'continuation').glob('reservation-*.json'))
assert not list((state/'learning-invocations').glob('*.json'))
assert not list((state/'workflow-learning-bindings').glob('*.json'))
assert native_count==[]
"""

PROJECTION = r"""
from dataclasses import asdict
from tools import agent_learning as learning
class ProjectionAccepted(BaseException): pass
class MissingControllerProjection(BaseException): pass
original_capture=Binding.capture
original_prefix=Binding._prefix
prefix_entries=[]
def source_projection(contract,repairs):
    result={
        'issue':contract.issue,'epic':contract.epic,
        'acceptance_criteria':list(contract.acceptance_criteria),
        'base_ref':contract.base_ref,'base_sha':contract.base_sha,
        'head_ref':contract.head_ref,'allowed_paths':list(contract.allowed_paths),
        'permissions':asdict(contract.permissions),
        'max_repair_iterations':contract.max_repair_iterations,
        'remaining_repair_iterations':contract.max_repair_iterations-repairs,
        'max_minutes':contract.max_minutes,'task_class':contract.task_class,
    }
    if contract.delivery_criterion_indices:
        result['delivery_criterion_indices']=list(contract.delivery_criterion_indices)
    if contract.policy_sha is not None: result['policy_sha']=contract.policy_sha
    return result
def prefix_enter(self,task,current,**options):
    result=original_prefix(self,task,current,**options)
    if task==301 and current.get('role_pending') is None:
        prefix_entries.append(result)
    return result
def controller_capture(self,role,payload,previous,references):
    if payload['contract']['issue']!=301 or role!='controller':
        return original_capture(self,role,payload,previous,references)
    contract=self.reader.contract(301)
    assert len(native_count)==2 and previous is not None
    assert previous==self._current(301) and previous['role_pending'] is None
    assert previous['executor_calls']==1 and previous['controller_calls']==0
    assert previous['completed_executor'] and previous['continuation_gate']
    repairs=previous['repair_iterations']
    assert type(repairs) is int and payload['repair_iteration']==repairs
    assert payload['contract']==source_projection(contract,repairs)
    assert payload['contract']!=workflow._contract_fields(contract)
    assert prefix_entries==[]
    # Real capture predicates must reject wrong fixed projections BEFORE prefix.
    # These are negative inputs, never replacement successful source verdicts.
    for fault in ('remaining','delivery','policy','unknown','repair-bool'):
        bad=copy.deepcopy(payload)
        if fault=='remaining': bad['contract']['remaining_repair_iterations']+=1
        elif fault=='delivery':
            if contract.delivery_criterion_indices:
                del bad['contract']['delivery_criterion_indices']
            else: bad['contract']['delivery_criterion_indices']=[]
        elif fault=='policy':
            if contract.policy_sha is None: bad['contract']['policy_sha']=None
            else:
                value=contract.policy_sha
                bad['contract']['policy_sha']=(value[:-1]+
                    ('0' if value[-1]!='0' else '1'))
        elif fault=='unknown': bad['contract']['unknown_projection_field']=None
        else: bad['repair_iteration']=bool(repairs)
        try: original_capture(self,role,bad,previous,references)
        except learning.LearningError as error:
            assert error.machine_code=='LEARNING_SOURCE_MISMATCH'
            assert prefix_entries==[]
        else: raise AssertionError('wrong role projection accepted:'+fault)
    try: original_capture(self,role,payload,previous,references)
    except learning.LearningError as error:
        frames=[];cursor=error.__traceback__
        while cursor is not None:
            frames.append(cursor.tb_frame)
            cursor=cursor.tb_next
        capture=[frame for frame in frames if frame.f_code is original_capture.__code__]
        assert len(capture)==1 and prefix_entries==[]
        assert error.machine_code=='LEARNING_SOURCE_MISMATCH'
        assert capture[0].f_locals['payload'] is payload
        # Exact original unconditional pre-prefix projection require is causal.
        import linecache
        line=linecache.getline(capture[0].f_code.co_filename,
                               capture[0].f_lineno).strip()
        assert line==('require(same(payload["contract"], '
                      'workflow._contract_fields(contract)))')
        raise MissingControllerProjection('WRITER_CONTROLLER_PROJECTION_BEFORE_PREFIX')
    assert len(prefix_entries)==1 and len(prefix_entries[0])==1
    assert prefix_entries[0][0]['role']=='executor'
    assert self.witness.role=='controller' and self.witness.lifecycle=='CREATED'
    assert self.witness.completed_prefix==prefix_entries[0]
    raise ProjectionAccepted()
Binding._prefix=prefix_enter
Binding.capture=controller_capture
try:
    with session.wait_scope.context('TASK:301:RUN_TASK'): reviewed.run()
except ProjectionAccepted: pass
else: raise AssertionError('genuine controller capture not reached')
assert len(native_count)==2
canonical=reader._json(state/'bounded-workflow'/
    (workflow._run_key(reader.contract(301))+'.json'))
assert canonical['executor_calls']==1 and canonical['controller_calls']==0
assert canonical['role_pending'] is None and not canonical['terminal']
assert reviewed.adapter.learning_runtime._workflow_binding.capability is None
assert reviewed.adapter.learning_runtime._workflow_binding.witness.lifecycle=='INVALID'
print(json.dumps({'controller_projection':True,'prefix_verified':True,
                  'negative_projections':5,'native_calls':2,'controller_consumed':0}))
"""

NONCURRENT_ABSENT = r"""
from tools import agent_learning as learning
from tools.agent_learning_verify import Verifier
class PendingCreated(BaseException): pass
class RefusalObserved(BaseException): pass
class MissingNoncurrentCompleteness(BaseException): pass
original_capture=Binding.capture
original_reserve=Binding.reserve
original_complete=Verifier._source_complete
original_origins=Binding._origins
pending_created=False
complete_checks=[];origin_events=[]
def checked_complete(self,task,items,retained):
    if pending_created and task==301:
        assert items==[] and not (state/'learning').exists()
        assert reader.contract(301).issue==301
        complete_checks.append('entered301')
        try: value=original_complete(self,task,items,retained)
        except learning.LearningError as error:
            assert error.machine_code=='LEARNING_MEASUREMENT_SOURCE_INVALID'
            complete_checks.append('refused301')
            raise
        complete_checks.append('returned301')
        return value
    return original_complete(self,task,items,retained)
def checked_origins(self):
    if pending_created and self.witness.task==302:
        origin_events.append('entered302')
        try: value=original_origins(self)
        except learning.LearningError:
            origin_events.append('refused302')
            raise
        origin_events.append('returned302')
        return value
    return original_origins(self)
Verifier._source_complete=checked_complete
Binding._origins=checked_origins
def create_pending_after_capture(self,role,payload,previous,references):
    global pending_created
    original_capture(self,role,payload,previous,references)
    if self.witness.task!=302: return
    assert role=='executor' and previous is None
    assert reader.contract(301).issue==301
    origins=[x for x in self.witness.retained_origins if x['task']==301]
    assert len(origins)==1 and origins[0]['records_digest']==digest(None)
    assert not (state/'learning').exists() and not (state/'learning').is_symlink()
    assert self._current(301) is None
    assert self.witness.lifecycle=='CREATED' and native_count==[]
    # Reuse the actual preconstructed original301 Runtime/configuration.
    other_reviewed=prepared_initial_reviewed
    branch=git('branch','--show-current')
    git('switch','codex/301-test')
    try:
        with session.wait_scope.context('TASK:301:RUN_TASK'):
            other_reviewed.run()
    except PendingCreated: pass
    else: raise AssertionError('original pending producer did not stop')
    finally: git('switch',branch)
    pending_created=True
    original=reader._json(state/'bounded-workflow'/
        (workflow._run_key(reader.contract(301))+'.json'))
    assert original['role_pending']=='executor'
    assert original['executor_calls']==1 and original['controller_calls']==0
    assert not (state/'learning').exists() and native_count==[]
    self._guard(302)
def reserve_then_stop(self,role,safe):
    if self.witness.task==301:
        original_reserve(self,role,safe)
        assert not (state/'learning').exists() and native_count==[]
        raise PendingCreated()
    assert self.witness.task==302 and pending_created
    assert role=='executor' and origin_events==[] and complete_checks==[]
    try: original_reserve(self,role,safe)
    except learning.LearningError as error:
        assert error.machine_code=='LEARNING_MEASUREMENT_SOURCE_INVALID'
        assert complete_checks==['entered301','refused301']
        assert origin_events==['entered302','refused302']
        raise RefusalObserved()
    assert origin_events==['entered302','returned302'] and complete_checks==[]
    assert self.witness.lifecycle=='CONSUMED' and self.capability is not None
    assert not (state/'learning').exists() and native_count==[]
    raise MissingNoncurrentCompleteness('WRITER_NONCURRENT_ABSENT_STORE_COMPLETENESS')
Binding.capture=create_pending_after_capture
Binding.reserve=reserve_then_stop
try:
    with loop.RunnerLock(where), session.wait_scope.context('TASK:302:RUN_TASK'):
        reviewed.run()
except RefusalObserved: pass
else: raise AssertionError('expected source completeness refusal')
assert pending_created and complete_checks==['entered301','refused301']
assert origin_events==['entered302','refused302'] and native_count==[]
for task,directory in ((301,state),(302,where)):
    canonical=reader._json(directory/'bounded-workflow'/
        (workflow._run_key(reader.contract(task))+'.json'))
    assert canonical['role_pending']=='executor'
    assert canonical['executor_calls']==1 and canonical['controller_calls']==0
    native=[reader._json(p) for p in
            (directory/'continuation').glob('reservation-*.json')]
    assert len(native)==1 and native[0]['status']=='RESERVED'
    metadata=[json.loads(p.read_text()) for p in
              (directory/'learning-invocations').glob('*.json')
              if not p.name.endswith('.problem.json')]
    assert len(metadata)==1 and metadata[0]['status']=='RESERVED'
    assert not (directory/'learning').exists()
    bindings=list((directory/'workflow-learning-bindings').glob('*.json'))
    assert len(bindings)==(1 if task==301 else 0)
    if bindings: assert json.loads(bindings[0].read_text())['status']=='RESERVED'
assert reviewed.adapter.learning_runtime._workflow_binding.capability is None
print(json.dumps({'noncurrent_absent_refused':True,'consumed':2,'native_calls':0,
                  'source_complete301_observed':True,'origins302_observed':True}))
"""

PREFIX_STORE_FAULT = r"""
from tools import agent_learning as learning
from tools import agent_learning_records as invocation_records
class RefusalObserved(BaseException): pass
class MissingPrefixStorePredicate(BaseException): pass
original_augment=Runtime.augment
original_reserve=Binding.reserve
original_prefix=Binding._prefix
original_store_join=getattr(Binding,'_prefix_store_equal',None)
original_current_store=getattr(Binding,'_current_store_equal',None)
fault_path=None;fault_bytes=None;protected=None;fault_key=None
prefix_entered=[];join_refused=[];events=[]
preflight=None;inner_store=None
def corrupt_prefix(self,store=None):
    global fault_path,fault_bytes,protected,fault_key
    directory=self.reader.task_directory(301)
    assert directory==state and len(native_count)==2
    canonical=self._current(301)
    assert canonical['executor_calls']==1
    assert canonical['controller_calls']==(0 if FAULT_TIMING=='capture' else 1)
    def owned_change(owned):
        global fault_path,fault_bytes,protected,fault_key
        owned._owned();owned.ensure_usable()
        rows=owned.records()
        originals=[x['record'] for x in rows if x['kind']=='invocation']
        assert len(originals)==1
        metadata=originals[0]
        assert metadata['role']=='executor' and metadata['ordinal']==1
        assert metadata['status']=='COMPLETED' and metadata['lesson_ids']==[]
        assert preflight[0]['metadata_digest']==digest(metadata)
        fault_key=metadata['invocation_key']
        assert reader._json(directory/'learning-invocations'/
                           (fault_key+'.json'))==metadata
        fault_path=directory/'learning/records.jsonl'
        fault_bytes=fault_path.read_bytes()
        protected={p:p.read_bytes() for base in (
            directory/'continuation',directory/'learning-invocations',
            state/'role-settings') for p in base.rglob('*.json')}
        if FAULT_KIND=='missing':
            rows=[x for x in rows if not (x['kind']=='invocation'
                and x['record']['invocation_key']==fault_key)]
        elif FAULT_KIND=='conflict':
            changed=dict(metadata,input_digest=('e'*64 if
                metadata['input_digest']!='e'*64 else 'd'*64))
            changed=invocation_records.invocation(learning.seal(changed))
            rows=[{'kind':'invocation','record':changed} if (
                x['kind']=='invocation' and x['record']['invocation_key']==fault_key)
                else x for x in rows]
        else:
            assert FAULT_KIND=='orphan' and FAULT_TIMING=='capture'
            orphan=dict(metadata,role='controller')
            orphan['invocation_key']=invocation_records.invocation_id(orphan)
            orphan=invocation_records.invocation(learning.seal(orphan))
            assert not (directory/'learning-invocations'/
                        (orphan['invocation_key']+'.json')).exists()
            rows=rows+[{'kind':'invocation','record':orphan}]
        if rows:
            fault_path.write_text(''.join(json.dumps(x,sort_keys=True)+'\n'
                                          for x in rows))
            fault_path.chmod(0o600)
        else: fault_path.unlink()
        assert owned.records()==rows
        assert {p:p.read_bytes() for p in protected}==protected
    if store is None:
        with Store(directory/'learning',reader.root,directory,reader.owner,
                   validators=invocation_records.VALIDATORS) as owned:
            owned_change(owned)
    else:
        assert type(store) is Store and store is inner_store
        owned_change(store)
    events.append('fault-'+FAULT_TIMING)
def observed_prefix(self,task,current,**options):
    if task==301 and fault_path is not None:
        prefix_entered.append('entered')
    return original_prefix(self,task,current,**options)
def observed_join(self,task,current,prefix,store):
    if (task==301 and FAULT_TIMING=='inner' and inner_store is not None
            and store is inner_store and fault_path is None):
        assert current['role_pending']=='controller'
        assert prefix==self.witness.completed_prefix==preflight
        # Exact existing Store is already owned; do not acquire/open another Store.
        corrupt_prefix(self,store)
    try: return original_store_join(self,task,current,prefix,store)
    except learning.LearningError as error:
        if task!=301 or fault_path is None: raise
        assert error.machine_code=='LEARNING_SOURCE_MISMATCH'
        frames=[];cursor=error.__traceback__
        while cursor is not None:
            if cursor.tb_frame.f_code is original_store_join.__code__:
                frames.append(cursor.tb_frame)
            cursor=cursor.tb_next
        assert len(frames)==1
        local=frames[0].f_locals
        assert local['task']==301 and local['store'] is store
        assert prefix==preflight and len(prefix)==1 and prefix[0]['role']=='executor'
        if FAULT_KIND in {'missing','conflict'}:
            assert local['key']==fault_key
            matching=local['matching'];metadata=local['metadata']
            assert (len(matching)==0 if FAULT_KIND=='missing'
                    else len(matching)==1 and matching[0]!=metadata)
        else:
            assert FAULT_KIND=='orphan'
            assert local['current_records']!=local['expected_records']
            assert set(local['current_records'])>set(local['expected_records'])
        join_refused.append('store-join-'+FAULT_TIMING+'-'+FAULT_KIND)
        raise
def current_store(self,store):
    global inner_store
    target=self.witness.task==301 and self.witness.role=='controller'
    if not target: return original_current_store(self,store)
    assert store is not None and inner_store is None
    store._owned();inner_store=store
    try: return original_current_store(self,store)
    finally: inner_store=None
def fault_before_selection(self,role,payload,**options):
    global preflight
    if payload['contract']['issue']!=301 or role!='controller':
        return original_augment(self,role,payload,**options)
    binding=self._workflow_binding
    canonical=binding._current(301)
    assert canonical['role_pending'] is None and canonical['controller_calls']==0
    # Genuine healthy original native/settings/metadata/Store prefix first.
    preflight=original_prefix(binding,301,canonical)
    assert len(preflight)==1 and preflight[0]['role']=='executor'
    assert preflight[0]['metadata_digest'] and len(native_count)==2
    events.append('healthy-prefix-verified')
    if FAULT_TIMING=='capture': corrupt_prefix(binding)
    try: value=original_augment(self,role,payload,**options)
    except learning.LearningError as error:
        assert FAULT_TIMING=='capture'
        assert error.machine_code=='LEARNING_SOURCE_MISMATCH'
        assert prefix_entered==['entered']
        assert join_refused==['store-join-capture-'+FAULT_KIND]
        events.append('capture-target-refused')
        raise RefusalObserved()
    if FAULT_TIMING=='capture':
        assert prefix_entered==['entered'] and join_refused==[]
        assert binding.witness.lifecycle=='CREATED'
        assert binding.witness.completed_prefix==preflight
        assert binding.witness.role=='controller' and len(native_count)==2
        assert binding._current(301)['controller_calls']==0
        raise MissingPrefixStorePredicate('WRITER_PREFIX_STORE_CAPTURE_'+FAULT_KIND)
    assert fault_path is None and binding.witness.completed_prefix==preflight
    return value
def no_dispatch_after_bad_prefix(self,role,safe):
    if self.witness.task!=301 or role!='controller':
        return original_reserve(self,role,safe)
    assert FAULT_TIMING=='inner'
    try: original_reserve(self,role,safe)
    except learning.LearningError as error:
        assert error.machine_code=='LEARNING_SOURCE_MISMATCH'
        assert join_refused==['store-join-inner-'+FAULT_KIND]
        events.append('inner-target-refused')
        raise RefusalObserved()
    raise AssertionError('inner original Store reread failed to refuse')
Runtime.augment=fault_before_selection
Binding._prefix=observed_prefix
Binding.reserve=no_dispatch_after_bad_prefix
if original_store_join is not None: Binding._prefix_store_equal=observed_join
if original_current_store is not None: Binding._current_store_equal=current_store
try:
    try:
        with session.wait_scope.context('TASK:301:RUN_TASK'): reviewed.run()
    except RefusalObserved: pass
    else: raise AssertionError('expected exact prefix Store target refusal')
finally:
    if fault_path is not None:
        assert fault_bytes is not None
        fault_path.write_bytes(fault_bytes);fault_path.chmod(0o600)
assert events==['healthy-prefix-verified','fault-'+FAULT_TIMING,
               ('capture-target-refused' if FAULT_TIMING=='capture'
                else 'inner-target-refused')]
assert len(native_count)==2 and protected is not None
# Restore only the deliberate Store bytes, never original reservations/counters.
assert {p:p.read_bytes() for p in protected}==protected
canonical=reader._json(state/'bounded-workflow'/
    (workflow._run_key(reader.contract(301))+'.json'))
assert canonical['executor_calls']==1
assert canonical['controller_calls']==(0 if FAULT_TIMING=='capture' else 1)
natives=[reader._json(p) for p in (state/'continuation').glob('reservation-*.json')]
assert len(natives)==(1 if FAULT_TIMING=='capture' else 2)
assert sum(x['status']=='COMPLETED' for x in natives)==1
assert sum(x['status']=='RESERVED' for x in natives)==(
    0 if FAULT_TIMING=='capture' else 1)
associations=[json.loads(p.read_text()) for p in
              (state/'workflow-learning-bindings').glob('*.json')]
assert len(associations)==1 and associations[0]['role']=='executor'
assert associations[0]['status']=='COMPLETED'
assert reviewed.adapter.learning_runtime._workflow_binding.capability is None
assert reviewed.adapter.learning_runtime._workflow_binding.witness.lifecycle=='INVALID'
print(json.dumps({'prefix_store_refused':True,'timing':FAULT_TIMING,
                  'fault':FAULT_KIND,'native_calls':2,'healthy_prefix_verified':True,
                  'exact_store_join_observed':True,
                  'controller_consumed':0 if FAULT_TIMING=='capture' else 1}))
"""


def prepared_target_setup() -> str:
    """Original301 sources exist before302 capture; preserve original writers."""
    setup = prefix_with_observer()
    marker = "initial=cli.private_json(state/'handover.json')"
    assert setup.count(marker) == 1
    setup = setup.replace(marker, PREPARE_INITIAL + "\n" + marker)
    marker = "engine=session.task_adapter(session.store,0);reviewed=configure(engine)"
    assert setup.count(marker) == 1
    return setup.replace(
        marker,
        "engine=prepared_initial_engine;reviewed=prepared_initial_reviewed",
    )


def test_genuine_controller_contract_projection_reaches_original_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = multi(tmp_path, monkeypatch)
    initial_config8(value)
    roles(value)
    setup = prepared_target_setup()
    marker = "with session.wait_scope.context('TASK:301:RUN_TASK'): reviewed.run()"
    assert setup.count(marker) == 1
    body = setup.split(marker, 1)[0] + PROJECTION
    assert child(value, body, timeout_seconds=120) == {
        "controller_projection": True,
        "prefix_verified": True,
        "negative_projections": 5,
        "native_calls": 2,
        "controller_consumed": 0,
    }


def test_noncurrent_absent_store_pending_is_not_waived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = multi(tmp_path, monkeypatch)
    initial_config8(value)
    roles(value)
    setup = prepared_target_setup()
    marker = (
        "try:\n    with loop.RunnerLock(where), "
        "session.wait_scope.context('TASK:302:RUN_TASK'):"
    )
    assert setup.count(marker) == 1
    body = setup.split(marker, 1)[0] + NONCURRENT_ABSENT
    assert child(value, body, timeout_seconds=120) == {
        "noncurrent_absent_refused": True,
        "consumed": 2,
        "native_calls": 0,
        "source_complete301_observed": True,
        "origins302_observed": True,
    }


@pytest.mark.parametrize(
    ("timing", "fault"),
    [
        ("capture", "missing"),
        ("capture", "conflict"),
        ("capture", "orphan"),
        ("inner", "missing"),
        ("inner", "conflict"),
    ],
)
def test_completed_prefix_requires_exact_original_store_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    timing: str,
) -> None:
    value = multi(tmp_path, monkeypatch)
    initial_config8(value)
    roles(value)
    setup = prepared_target_setup()
    marker = "with session.wait_scope.context('TASK:301:RUN_TASK'): reviewed.run()"
    assert setup.count(marker) == 1
    body = (
        setup.split(marker, 1)[0]
        + "\nFAULT_KIND="
        + repr(fault)
        + "\nFAULT_TIMING="
        + repr(timing)
        + "\n"
        + PREFIX_STORE_FAULT
    )
    assert child(value, body, timeout_seconds=120) == {
        "prefix_store_refused": True,
        "timing": timing,
        "fault": fault,
        "native_calls": 2,
        "controller_consumed": 0 if timing == "capture" else 1,
        "healthy_prefix_verified": True,
        "exact_store_join_observed": True,
    }
