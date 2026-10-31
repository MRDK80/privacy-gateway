"""Original planning history remains readable after rollback, never selectable."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_handover as planner
from tools import agent_epic_runtime_cli as guards
from tools.agent_orchestrate import StateStore

from tests import learning_session_factory_fixture as factory
from tests import learning_session_native_fixture as native
from tests.test_agent_learning_integration import roles
from tests.test_agent_learning_session_native import BODY

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX original planning sources"
)


def original_history_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    original_build = factory.build

    def build(base: Path, **options: Any) -> dict[str, str]:
        assert options["extra_tasks"] == (302, 304, 303, 305)
        options["extra_tasks"] = (*options["extra_tasks"], 306)
        return dict(original_build(base, **options))

    # Extra baseline grant exists BEFORE any role invocation/source generation.
    with monkeypatch.context() as setup:
        setup.setattr(factory, "build", build)
        value = native.prepared(tmp_path, monkeypatch)
    state = Path(value["state"])
    store = StateStore(state)
    plans, config, launch, adapter, control = (
        store.load(name)
        for name in (
            "approved-plans",
            "epic-runtime",
            "launch",
            "runtime-adapter",
            "launch-control",
        )
    )
    assert plans is not None and config is not None and launch is not None
    assert adapter is not None and control is not None
    preview = json.loads((state / "session-preview.json").read_text())
    recipe = copy.deepcopy(plans["tasks"]["301"])
    initial = recipe["plan"]
    plan = {
        key: value
        for key, value in initial.items()
        if key in planner.PLAN_FIELDS
    }
    plan.update(
        task=306,
        base_sha=preview["303"]["merge"],
        head_ref="codex/306-baseline",
        task_class="feature",
        acceptance_criteria=["- synthetic criterion"],
        default_ref="main",
        default_sha=initial["trusted_policy"]["policy_sha"],
        policy_sha=initial["trusted_policy"]["policy_sha"],
        changes_policy_or_architecture=False,
    )
    assert set(plan) == planner.PLAN_FIELDS | {"policy_sha"}
    recipe["plan"] = plan
    body = "## Acceptance\n- synthetic criterion\n"
    plan["criteria_source"].update(
        issue=306, body_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest()
    )
    recipe["demo_plan"].update(task=306, roadmap_sha=preview["303"]["merge"])
    plans["tasks"]["306"] = recipe
    store.save("approved-plans", plans)
    wait = store.load("wait-profile")
    assert wait is not None and wait["limits"]["max_phase_seconds"] == 300
    wait["limits"]["max_phase_seconds"] = 1800
    store.save("wait-profile", wait)
    assert not (state / "invocations").exists()
    assert adapter["timeout_seconds"] == 300
    adapter["timeout_seconds"] = 1800
    store.save("runtime-adapter", adapter)
    config["approved_wait_digest"] = guards.canonical_digest(wait)
    config["approved_plans_digest"] = guards.canonical_digest(plans)
    launch["runtime_config_digest"] = guards.launch_config_digest(config, adapter)
    config["approved_launch_digest"] = guards.canonical_digest(launch)
    store.save("launch", launch)
    store.save("epic-runtime", config)
    control["launch_digest"] = config["approved_launch_digest"]
    store.save("launch-control", control)
    roles(value)
    script = state / "synthetic-roles.py"
    role_source = script.read_text()
    target = "if fields['issue']==301: (root/'code.py').write_text('VALUE = 2\\n')"
    assert role_source.count(target) == 1
    script.write_text(
        role_source.replace(
            "if fields['issue']==301: (root/'code.py').write_text('VALUE = 2\\n')",
            "(root/'code.py').write_text('VALUE = '+"
            "str({301:2,303:3,305:4,306:3}[fields['issue']])+'\\n')",
        )
    )
    return value


BASELINE = r"""
from tools import agent_learning_measure as measure
original_capture=measure.capture
# Only defer the baseline measurement append. Its actual terminal observation,
# native calls/settings/snapshots remain original; no result/metrics are replaced.
def defer_baseline(reader,session,task):
    return {} if task==306 else original_capture(reader,session,task)
measure.capture=defer_baseline
api.tasks=(*api.tasks,306)
api.issues[306]=copy.deepcopy(api.issues[303])
api.issues[306].update(number=306,title='Synthetic unhinted baseline')

def run_baseline():
    recipe=copy.deepcopy(session.plans['tasks']['306'])
    plan=recipe['plan']
    assert plan['base_sha']==api.pulls[303].merge_sha
    assert plan['acceptance_criteria']==['- synthetic criterion']
    previous_ref=api.git('branch','--show-current')
    previous=loop.CheckpointStore(state/'tasks/303',root).load()
    assert previous is not None and previous['task']==303
    assert previous['merge_sha']==plan['base_sha']
    directory=state/'tasks/306'
    store=loop.CheckpointStore(directory,root)
    cursor=cli.private_json(state/'session-control/cursor.json')
    iterations=session.config['task_iterations']+len(cursor['completed_tasks'])+1
    try:
        # Existing original writer validates approved plan/authority, imports
        # actual refs, fast-forwards local roadmap, creates branch/handover,
        # and writes its canonical PLAN/READY checkpoint. No positive is forged.
        session.prepare(previous,306,store,iterations)
        initial=store.load()
        assert initial is not None and initial['phase']=='PLAN'
        assert initial['status']=='READY' and initial['pr'] is None
        assert initial['completed_phases']==[] and initial['pending_phase'] is None
        assert api.git('rev-parse',plan['base_ref'])==plan['base_sha']
        assert api.git('branch','--show-current')==plan['head_ref']
        with session._stage('TASK:306:RUN_TASK'),loop.RunnerLock(directory):
            baseline_engine=session.task_adapter(store,iterations)
            baseline_engine.bootstrap.reviewed.run()
    except Exception:
        # These isolated synthetic paths/records are private test diagnostics.
        # The outer delivery loop otherwise reports only UNKNOWN disposition.
        import traceback
        traceback.print_exc()
        raise
    finally:
        api.git('switch',previous_ref)
    # Synthetic primary owner disposition, as for fixture failed baselines.
    # No final driver/readiness or delivered-child assertion is made for306.
    api.issues[306]['state']='CLOSED'
    api.events.append({'kind':'owner-close-baseline','task':306})
"""

HISTORICAL = r"""
from tools import agent_learning as learning
from tools import agent_learning_measure as measure
from tools import agent_learning_records as records
from tools.agent_learning_store import Store
from tools.agent_learning_verify import Verifier
from tools import agent_learning_planning_history as active_history
from tools import agent_learning_planning_sources as active_sources
from tools import agent_learning_planning_selection as active_selection
from tools.agent_final_preparation_runtime import HistoricalPlanningReader
from tools.agent_continuation import digest
measure.capture=original_capture
with loop.RunnerLock.supervisor(state/'session-control'),loop.RunnerLock(state):
    with session._stage('FINAL:FINAL_GATE'):
        baseline,_=measure.observed(reader,session,306)
        hinted,metadata=measure.observed(reader,session,305)
        assert baseline['hint_set_digest'] is None
        assert hinted['hint_set_digest'] is not None
        assert baseline['applicability_digest']==hinted['applicability_digest']
        assert baseline['calls']['planner']==baseline['calls']['planning_review']==0
        assert hinted['calls']['planner']==hinted['calls']['planning_review']==1
        evaluation=learning.compare(baseline,hinted)
        assert evaluation['comparison']['status']=='ROLLBACK_REQUIRED'
        original_clear=Store.clear_block
        original_latch={}
        def capture_latch(self):
            if self.task==state/'tasks/305':
                original_latch['raw']=self.block_path.read_bytes()
            return original_clear(self)
        Store.clear_block=capture_latch
        try:
            measure.capture(reader,session,306)
            assert measure.capture(reader,session,305)==hinted
        finally: Store.clear_block=original_clear
        assert original_latch['raw']
        retained=Verifier(reader,session).retained()
        consumed={lesson for item in metadata for lesson in item['lesson_ids']}
        removed={item['record']['lesson_id'] for item in retained
                 if item['kind']=='tombstone'}
        assert consumed and consumed.issubset(removed)
        assert not (state/'tasks/305/learning/learning-blocked.json').exists()
        history=HistoricalPlanningReader(session)
        # Observe actual delegated proofs; never replace a source verdict.
        original_inventory=history._namespace_inventory
        original_source=active_sources._original_with_call_fact
        observed={'inventory_calls':0,'inventory':{},'used':{}}
        inventory_active=[False]
        # Test-only neutral observations of genuine source/pair results.
        requested_proofs=[]
        pair_proofs=[]
        from tools import agent_learning_planning_promotion as observed_promotion
        original_pair_complete=observed_promotion._pair_complete
        def observe_pair_complete(*args,**kwargs):
            value=original_pair_complete(*args,**kwargs)
            if value is not None: pair_proofs.append(json.loads(json.dumps(value)))
            return value
        def count_inventory(*args):
            observed['inventory_calls']+=1
            assert not inventory_active[0]
            inventory_active[0]=True
            try: return original_inventory(*args)
            finally: inventory_active[0]=False
        def count_source(source_reader,task,role,ordinal):
            value=original_source(source_reader,task,role,ordinal)
            if source_reader is history.reader:
                kind='inventory' if inventory_active[0] else 'used'
                key=(task,role,ordinal)
                observed[kind][key]=observed[kind].get(key,0)+1
            if (source_reader is history.reader and not inventory_active[0]
                    and (task,role,ordinal)==(305,'planner',1)):
                requested_proofs.append(value)
            return value
        try:
            observed_promotion._pair_complete=observe_pair_complete
            history._namespace_inventory=count_inventory
            active_sources._original_with_call_fact=count_source
            use=history.read(305,'planner',1)
        finally:
            history._namespace_inventory=original_inventory
            active_sources._original_with_call_fact=original_source
            observed_promotion._pair_complete=original_pair_complete
        assert observed['inventory_calls']==2, 'B1_PUBLIC_BOUNDARY_INVENTORY_COUNT'
        assert observed['inventory'] and all(
            count==2 for count in observed['inventory'].values())
        assert observed['used'][(305,'planner',1)]>=1
        assert any(count>1 for count in observed['used'].values())
        assert history._read_scope is None
        assert tuple(sorted(consumed))==tuple(sorted({
            *use.facts()[8],*history.read(305,'planning_review',1).facts()[8],
        }))
        assert use.facts()[9] and not hasattr(use,'hints')
        # These oracles run ONLY after successful public read and final guards.
        assert requested_proofs
        requested_five,requested_fact=requested_proofs[0]
        assert all(value==requested_proofs[0] for value in requested_proofs)
        carrier=use.call_fact()
        assert carrier==requested_fact
        assert (carrier.task,carrier.role,carrier.ordinal)==(305,'planner',1)
        original_result,envelope,relevance,normalized,original_metadata=requested_five
        assert original_result['facts']==normalized
        key=original_metadata['invocation_key']
        assert carrier.invocation_key==key
        original_directory=state/'planning/305'
        artifact_directory=original_directory/'learning-validation'
        originals={name:json.loads((artifact_directory/('planner-1-'+name+'.json')).read_text())
                   for name in (
                       'request','response','context','workspace','validation')}
        original_meta=json.loads((original_directory/'learning-invocations'/(key+'.json')).read_text())
        original_problem=json.loads((original_directory/'learning-invocations'/(key+'.problem.json')).read_text())
        original_relevance=json.loads((original_directory/'learning-invocations'/(key+'.relevance.json')).read_text())
        assert original_metadata==original_meta
        assert relevance==original_relevance and envelope['hints']
        assert original_result['request']==originals['request']['payload']
        assert original_result['response']==originals['response']['payload']
        for name in ('context','workspace','validation'):
            assert original_result[name]==originals[name]
        expected_commitments={'family':'PLANNING','task':305,'role':'planner','ordinal':1,
            'metadata_digest':digest(original_meta),'problem_digest':digest(original_problem),
            'relevance_digest':digest(original_relevance),
            **{name+'_digest':digest(value) for name,value in originals.items()}}
        for name,value in expected_commitments.items():
            if name!='family': assert getattr(carrier,name)==value
        assert carrier.source_digest==digest(expected_commitments)
        # Original retained rows were source-validated by the completed public read.
        # Raw TEST-only reads below compare full preimages, never grant authority.
        planning_rows=[json.loads(line) for line in
            (state/'planning-learning/records.jsonl').read_text().splitlines()]
        references=use.lesson_references()
        assert len(references)==1
        expected_references=[]
        for lesson in original_meta['lesson_ids']:
            verified=[row['record'] for row in planning_rows
                      if row['kind']=='planning_verification'
                      and row['record']['lesson_id']==lesson]
            assert len(verified)==1
            candidate=[row['record'] for row in planning_rows
                       if row['kind']=='planning_candidate'
                       and digest(row['record'])==verified[0]['candidate_digest']]
            assert len(candidate)==1
            failure=[row['record'] for row in planning_rows
                     if row['kind']=='planning_observation'
                     and (row['record']['validation_digest']
                          ==original_relevance['failure_validation_digest'])]
            corrected=[row['record'] for row in planning_rows
                       if row['kind']=='planning_observation'
                       and (row['record']['validation_digest']
                            ==original_relevance['corrected_validation_digest'])]
            assert len(failure)==len(corrected)==1
            paired=[value for value in pair_proofs
                    if value['before_digest']==digest(failure[0])
                    and value['after_digest']==digest(corrected[0])]
            assert paired and all(value==paired[0] for value in paired)
            expected_references.append((lesson,digest(verified[0]),digest(candidate[0]),
                digest(failure[0]),digest(corrected[0]),digest(paired[0])))
        assert references==tuple(expected_references)
        assert tuple(row[0] for row in references)==tuple(original_meta['lesson_ids'])
        expected_negatives=tuple(sorted(digest(row['record'])
            for row in retained+planning_rows
            if row['kind'] in {'evaluation','tombstone'}))
        assert expected_negatives
        assert use.facts()==(305,'planner',1,digest(original_result),
            digest(originals['request']['payload']),digest(originals['validation']),
            digest(original_meta),references[0][5],tuple(original_meta['lesson_ids']),
            expected_negatives)
        assert len(use.facts())==10 and use.facts()[9]
        assert not hasattr(use,'hints') and history._read_scope is None
        # Actual failed admissions remain RESERVED/pending and source-complete;
        # they are historical failures, never successful admission or a refund.
        for failed in (302,304):
            admission_path=state/'planning'/str(failed)/'admission.json'
            saved_admission=admission_path.read_bytes()
            actual=json.loads(saved_admission)
            assert actual['status']=='RESERVED' and actual['pending'] is not None
            short=actual['pending']['role']
            logical='planner' if short=='planner' else 'planning_review'
            ordinal=actual['pending']['attempt']
            assert ordinal==actual['attempts'][short]
            # A genuinely missing returned response/validation is unavailable;
            # do not manufacture a positive UNKNOWN/completion source graph.
            for suffix in ('response','validation'):
                path=admission_path.parent/'learning-validation'/(
                    f'{logical}-{ordinal}-{suffix}.json')
                raw=path.read_bytes()
                try:
                    path.unlink()
                    try: history.read(305,'planner',1)
                    except (learning.LearningError,loop.PhaseBlocked): pass
                    else: raise AssertionError('partial failed original accepted')
                finally: path.write_bytes(raw);path.chmod(0o600)
            for fault in ('shape','status','digest','digest-tag','role',
                          'ordinal-zero','ordinal-bool','ordinal-count'):
                changed=copy.deepcopy(actual)
                pending=changed['pending']
                if fault=='shape': pending['extra']=None
                elif fault=='status': changed['status']='ADMITTED'
                elif fault=='digest':
                    old=pending['request_digest']
                    pending['request_digest']=old[:-1]+('0' if old[-1]!='0' else '1')
                elif fault=='digest-tag':
                    pending['request_digest']=pending['request_digest'][7:]
                elif fault=='role':
                    pending['role']='review' if short=='planner' else 'planner'
                elif fault=='ordinal-zero': pending['attempt']=0
                elif fault=='ordinal-bool': pending['attempt']=True
                else: pending['attempt']=actual['attempts'][short]+1
                try:
                    admission_path.write_text(json.dumps(changed))
                    admission_path.chmod(0o600)
                    try: history.read(305,'planner',1)
                    except (learning.LearningError,loop.PhaseBlocked): pass
                    else: raise AssertionError('invalid failed pending accepted:'+fault)
                finally:
                    admission_path.write_bytes(saved_admission)
                    admission_path.chmod(0o600)
        # Point a faulted pending at an ACTUAL original PASS request. Its intact
        # response/metadata must not satisfy the new fixed FAIL-only predicate.
        passed_path=state/'planning/303/admission.json'
        passed_raw=passed_path.read_bytes()
        passed=json.loads(passed_raw)
        request=json.loads((passed_path.parent/'learning-validation'/
            'planner-1-request.json').read_text())['payload']
        passed['status']='RESERVED'
        passed['pending']={'role':'planner','attempt':1,
                           'request_digest':cli.canonical_digest(request)}
        try:
            passed_path.write_text(json.dumps(passed));passed_path.chmod(0o600)
            try: history.read(305,'planner',1)
            except (learning.LearningError,loop.PhaseBlocked): pass
            else: raise AssertionError('pending to original PASS accepted')
        finally: passed_path.write_bytes(passed_raw);passed_path.chmod(0o600)
        for failed,files in failed_bytes.items():
            assert files=={str(path.relative_to(state)):path.read_bytes()
                for path in (state/'planning'/str(failed)).rglob('*') if path.is_file()}
        try: use._values=()
        except AttributeError: pass
        else: raise AssertionError('historical result mutable')
        # Actual CURRENT call route refuses tombstones; no current envelope emitted.
        try: active_sources.replay(reader,305,'planner',1)
        except learning.LearningError as error:
            assert error.machine_code=='LEARNING_VERIFICATION_STALE'
        else: raise AssertionError('current replay re-enabled tombstoned hint')
        # Same original five-source proof supplies facts for the independent
        # active-selection tombstone assertion, without entering private recursion.
        full=active_sources._original(history.reader,305,'planner',1)[0]
        try: history._read_original(305,'planner',1)
        except learning.LearningError as error:
            assert error.machine_code=='LEARNING_SOURCE_MISMATCH'
        else: raise AssertionError('private historical read escaped public scope')
        assert history._read_scope is None
        selected,_=active_selection.select(
            Verifier(reader,session),'planner',full['facts'],
        )
        assert selected['hints']==[]
        # Mutate only deliberately faulted synthetic bytes AFTER full entry.
        # Counts/keys remain equal; the whole admission snapshot must still refuse.
        admission_path=state/'planning/305/admission.json'
        saved_admission=admission_path.read_bytes()
        changed=json.loads(saved_admission)
        assert changed['pending'] is None
        changed['status']='RESERVED' if changed['status']!='RESERVED' else 'ADMITTED'
        entry_calls=[0]
        def admission_after_entry(*args):
            value=original_inventory(*args)
            entry_calls[0]+=1
            if entry_calls[0]==1:
                admission_path.write_text(json.dumps(changed))
                admission_path.chmod(0o600)
            return value
        try:
            history._namespace_inventory=admission_after_entry
            try: history.read(305,'planner',1)
            except learning.LearningError as error:
                assert error.machine_code=='LEARNING_SOURCE_MISMATCH'
            else: raise AssertionError('within-read admission drift accepted')
            assert entry_calls[0]==1
        finally:
            history._namespace_inventory=original_inventory
            admission_path.write_bytes(saved_admission);admission_path.chmod(0o600)
        assert history._read_scope is None
        orphan=state/'planning/305/learning-validation/after-entry-orphan.json'
        boundary_calls=[0];used_calls=[0];entry_finished=[False]
        def orphan_after_entry(*args):
            boundary_calls[0]+=1
            value=original_inventory(*args)
            if boundary_calls[0]==1:
                orphan.write_text('{}');orphan.chmod(0o600)
                entry_finished[0]=True
            return value
        def used_before_exit(source_reader,task,role,ordinal):
            value=original_source(source_reader,task,role,ordinal)
            if (boundary_calls[0]==1 and entry_finished[0]
                    and source_reader is history.reader):
                used_calls[0]+=1
            return value
        try:
            history._namespace_inventory=orphan_after_entry
            active_sources._original_with_call_fact=used_before_exit
            try: history.read(305,'planner',1)
            except learning.LearningError: pass
            else: raise AssertionError('persistent after-entry orphan accepted')
            assert boundary_calls[0]==2 and used_calls[0]>0
        finally:
            history._namespace_inventory=original_inventory
            active_sources._original_with_call_fact=original_source
            orphan.unlink(missing_ok=True)
        assert history._read_scope is None
        # Original input path exercises history.verify before private revalidate;
        # no claim that private revalidate alone sees all workflow-family tombstones.
        before={
            path:path.read_bytes() for path in (state/'planning/305').rglob('*.json')
        }
        targets=[
            state/'planning/305/learning-validation/planner-1-request.json',
            state/'planning/305/learning-validation/planner-1-response.json',
            state/'planning/305/learning-validation/planner-1-context.json',
            state/'planning/305/learning-validation/planner-1-workspace.json',
            next((state/'planning/305/learning-invocations').glob('*.problem.json')),
            next((state/'planning/305/learning-invocations').glob('*.relevance.json')),
            next((state/'tasks/305/continuation').glob('reservation-*.json')),
        ]
        for target in targets:
            raw=target.read_bytes()
            try:
                target.unlink()
                try: history.read(305,'planner',1)
                except (learning.LearningError,loop.PhaseBlocked): pass
                else: raise AssertionError('missing original accepted:'+target.name)
            finally: target.write_bytes(raw);target.chmod(0o600)
        # Same genuine originals; namespace-only faults never reseal source facts.
        namespaces=[state/'planning/305'/name for name in
                    ('learning-validation','learning-invocations')]
        for directory in namespaces:
            extra=directory/('planner-99-request.json' if
                directory.name=='learning-validation' else 'orphan.json')
            for shape in ('file','directory','symlink'):
                try:
                    if shape=='file': extra.write_text('{}');extra.chmod(0o600)
                    elif shape=='directory': extra.mkdir(mode=0o700)
                    else: extra.symlink_to(directory/'planner-1-request.json')
                    try: history.read(305,'planner',1)
                    except (learning.LearningError,loop.PhaseBlocked): pass
                    else: raise AssertionError('orphan namespace accepted:'+shape)
                finally:
                    if extra.is_dir() and not extra.is_symlink(): extra.rmdir()
                    else: extra.unlink(missing_ok=True)
        # Empty expected-set primitive: no fabricated admission/source graph.
        # Whole-domain inclusion of actual zero-counter task directories is
        # separately reviewed in domain(); this tests its namespace operation.
        empty=state/'empty-namespace-probe';empty.mkdir(mode=0o700)
        try:
            history._namespace_names(empty,set(),lock=False)
            orphan=empty/'planner-1-request.json'
            orphan.write_text('{}');orphan.chmod(0o600)
            try: history._namespace_names(empty,set(),lock=False)
            except learning.LearningError: pass
            else: raise AssertionError('zero-attempt namespace orphan accepted')
            orphan.unlink()
        finally: empty.rmdir()
        validation=namespaces[0]
        expected={path.name for path in validation.iterdir()}
        extras=[validation/f'orphan-{index}.json' for index in range(3)]
        original_iterdir=Path.iterdir
        original_guard=history._guard
        yielded=[];guard_calls=[]
        def guarded():
            guard_calls.append(True)
            return original_guard()
        def ordered(path):
            if path==validation:
                for name in sorted(expected):
                    yielded.append(name);yield path/name
                for extra in extras:
                    yielded.append(extra.name);yield extra
            else: yield from original_iterdir(path)
        try:
            for extra in extras: extra.write_text('{}');extra.chmod(0o600)
            Path.iterdir=ordered;history._guard=guarded
            try: history._namespace_names(validation,expected,lock=False)
            except learning.LearningError as error:
                assert error.machine_code=='LEARNING_STORAGE_EXHAUSTED'
            else: raise AssertionError('bounded prefix overflow accepted')
            assert len(yielded)==len(expected)+1
            assert len(guard_calls)>=2*len(expected)+1
        finally:
            Path.iterdir=original_iterdir;history._guard=original_guard
            for extra in extras: extra.unlink()
        records_path=state/'tasks/305/learning/records.jsonl'
        saved=records_path.read_bytes()
        latch_path=state/'tasks/305/learning/learning-blocked.json'
        for fault in ('missing-tombstone','original-latch'):
            try:
                if fault=='missing-tombstone':
                    rows=saved.splitlines(keepends=True)
                    index=next(i for i,line in enumerate(rows)
                               if json.loads(line)['kind']=='tombstone')
                    records_path.write_bytes(b''.join(rows[:index]+rows[index+1:]))
                else:
                    latch_path.write_bytes(original_latch['raw'])
                    latch_path.chmod(0o600)
                try: history.read(305,'planner',1)
                except learning.LearningError as error:
                    assert error.machine_code=='LEARNING_ROLLBACK_PENDING'
                else: raise AssertionError('negative barrier bypassed')
            finally:
                records_path.write_bytes(saved);records_path.chmod(0o600)
                if latch_path.exists(): latch_path.unlink()
        assert {path:path.read_bytes() for path in before}==before
        nested=[False]
        def nested_public(source_reader,task,role,ordinal):
            value=original_source(source_reader,task,role,ordinal)
            if source_reader is history.reader and not nested[0]:
                nested[0]=True
                try: history.read(305,'planner',1)
                except learning.LearningError as error:
                    assert error.machine_code=='LEARNING_SOURCE_MISMATCH'
                else: raise AssertionError('nested public scope accepted')
            return value
        try:
            active_sources._original_with_call_fact=nested_public
            assert history.read(305,'planner',1).facts()==use.facts()
        finally: active_sources._original_with_call_fact=original_source
        assert nested[0] and history._read_scope is None
        def no_inventory(*args):
            raise AssertionError('invalid type performed an inventory')
        try:
            history._namespace_inventory=no_inventory
            for task,ordinal in ((True,1),(305,True),('305',1),(305,'1')):
                try: history.read(task,'planner',ordinal)
                except learning.LearningError as error:
                    assert error.machine_code=='LEARNING_SOURCE_MISMATCH'
                else: raise AssertionError('invalid public type accepted')
        finally: history._namespace_inventory=original_inventory
        assert history._read_scope is None
        assert reader.planning_replay_stack==set()
        assert history.reader.planning_replay_stack==set()
        for failed,files in failed_bytes.items():
            assert files=={str(path.relative_to(state)):path.read_bytes()
                for path in (state/'planning'/str(failed)).rglob('*') if path.is_file()}
print(json.dumps({'historical':'VERIFIED_APPLIED','comparison':'UNMEASURED',
                  'neutral':True,'planning_role_calls':7,'baseline_role_calls':2}))
"""


def test_original_planning_use_is_neutral_after_genuine_complete_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = original_history_fixture(tmp_path, monkeypatch)
    body = BODY.rsplit("print(json.dumps(", 1)[0]
    hook = "        return result\n    engine.effect=observed_effect"
    assert body.count(hook) == 1
    body = body.replace(
        hook,
        "        if phase=='TASK_DONE' and task==303:\n            run_baseline()\n"
        + hook,
    )
    marker = "session.task_adapter=adapter\n"
    assert body.count(marker) == 1
    body = body.replace(marker, marker + BASELINE)
    body = "REMOTE=" + repr(value["remote"]) + "\n" + body + HISTORICAL
    state = Path(value["state"])
    harness = state / "historical-planning.py"
    harness.write_text(
        "import sys,json\nfrom pathlib import Path\n"
        + "sys.path.insert(0,"
        + repr(str(Path(value["installed"]) / "policy"))
        + ")\n"
        + "from tools.agent_learning_evidence import SourceReader\n"
        + "state=Path("
        + repr(str(state))
        + ")\nroot=Path("
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
        timeout=1860,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "historical": "VERIFIED_APPLIED",
        "comparison": "UNMEASURED",
        "neutral": True,
        "planning_role_calls": 7,
        "baseline_role_calls": 2,
    }


def test_historical_original_commitment_covers_all_five_objects() -> None:
    from tools.agent_final_preparation_runtime import HistoricalPlanningReader

    values: list[Any] = [
        {"request": {"synthetic": "request"}, "validation": {"outcome": "FAIL"}},
        {"hints": []},
        None,
        {"task": 305},
        {"invocation_key": "synthetic"},
    ]
    commitment = HistoricalPlanningReader._original_commitment(*values)
    assert len(commitment) == 64
    for index in range(5):
        changed = copy.deepcopy(values)
        changed[index] = {"synthetic_changed": index}
        assert HistoricalPlanningReader._original_commitment(*changed) != commitment
