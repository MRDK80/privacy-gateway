"""One genuine aggregate history; original poll accounting and negative sources."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from tools import agent_epic_runtime_cli as guards

from tests import test_agent_final_preparation_runtime as native_fixture
from tests.test_agent_epic_loop import (
    active_runtime_namespace as active_runtime_namespace,
)
from tests.test_agent_final_preparation_ci import sources
from tests.test_agent_final_preparation_runtime import (
    NATIVE_DRIVER,
    native_factory_fixture,
    run_native_driver,
)
from tests.test_agent_final_preseed_guard import write

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX original poll")

PROBE = r"""
import errno, copy, subprocess
from tools.agent_epic_session_runtime import SessionRuntime
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_epic_wait_frame import FrameError
from tools.agent_final_preparation_ci import PrimaryCIError
from tools import agent_final_preparation_ci_source as collector
from tools import agent_epic_runtime_cli as guards
session = SessionRuntime(state=seed,root=root,
    config=guards.private_json(seed/'epic-runtime.json'),wait_scope=scope)
def forbidden(*args,**kwargs):
    raise AssertionError('collector created effect/model/gate')
storage.Store.write=forbidden
native_store.Store.save=forbidden
runtime.RoleCounter.reserve=forbidden
runtime._ReservedDelegate._call=forbidden
agent_gate.run_profile=forbidden
aggregate_bytes={p:p.read_bytes() for p in (seed/'final-preparation').rglob('*')
                 if p.is_file()}
operations=[]
collector_git_calls=[]
original_collector_git=collector._PollSources.git
def count_collector_git(self,*args):
    collector_git_calls.append(args)
    return original_collector_git(self,*args)
collector._PollSources.git=count_collector_git
def count_operation(event,args):
    if event=='subprocess.Popen':
        argv=args[1]
        if isinstance(argv,(list,tuple)) and argv:
            name=Path(argv[0]).name
            if name in {'gh','git'}: operations.append(name)
sys.addaudithook(count_operation)
def counts():
    return (operations.count('gh'),len(collector_git_calls))
def physical_counts():
    return (operations.count('gh'),operations.count('git'))
def delta(before):
    after=counts()
    return tuple(a-b for a,b in zip(after,before))
def wait_inventory():
    base=seed/'read-waits'
    paths=(base,*base.rglob('*')) if base.exists() else ()
    return {p:(p.lstat().st_mode,p.read_bytes() if p.is_file() else None)
            for p in paths}
control=seed/'primary-fault.json'
def transport(mode):
    control.write_text(json.dumps({'mode':mode,'check_reads':0}))
def refuse(call,kind,args=None):
    try: call()
    except kind as error:
        assert type(error) is kind
        if kind is FileNotFoundError:
            assert error.errno==errno.ENOENT
        else:
            assert error.args==args
        return {'exception':kind.__name__,'code':None if args is None else args[0]}
    else: raise AssertionError('expected exact refusal absent')
journal=[]
def begin(name):
    (seed/'ci-source-probe-progress.json').write_text(json.dumps({
        'probe':name,'collector_counts':counts(),'physical_counts':physical_counts(),
        'completed_probes':journal,
    }))
with RunnerLock.supervisor(seed/'session-control'),RunnerLock(seed):
    with session._stage('FINAL:PR_CI'):
        reader=scope._active_reader()
        assert reader.limits['max_ci_polls']==2
        path=reader.directory/'wait.json'
        original_read=session._github_read
        original_run=reader.run
        original_subprocess_run=subprocess.run
        before=counts(); physical_before=physical_counts();saved=wait_inventory()
        begin('absent-poll')
        ci_probe_refusal=refuse(lambda:collector.collect_roadmap_sources_owned(session),
                      PrimaryCIError,('FINAL_PRIMARY_CI_INVALID',))
        assert delta(before)==(0,0) and wait_inventory()==saved
        assert reader._active_poll is False and reader._depth==0
        journal.append(dict(name='absent-poll',at_fault=before,after=counts(),
                            physical_at_fault=physical_before,
                            physical_after=physical_counts(),**ci_probe_refusal))

        def state():
            return json.loads(path.read_text())
        def save_fault(value):
            path.write_text(json.dumps(value))
        def primary_success():
            before=counts(); depth=reader._depth
            value=collector.collect_roadmap_sources_owned(session)
            assert delta(before)==(24,9)
            assert type(reader._depth) is int and reader._depth==depth
            assert not hasattr(value,'ready') and not hasattr(value,'candidate_digest')
            assert value.primary.sha==value.comparison.sha
            return value

        def first_assessment():
            initial=state()
            assert initial['ci']['attempts']==1 and initial['ci']['status']=='RESERVED'
            assert reader._active_poll is True and type(reader._depth) is int
            assert reader._depth>0
            begin('first-original-positive')
            positive=primary_success()

            # Each fault is injected only AFTER a genuine command has durably
            # completed; restore precisely the deliberately attacked field/bytes.
            # No request/accounting entries from that command are refunded.
            for name in ('active','depth-bool','depth-zero','depth-changed','reader',
                         'guard','deadline','ci-digest','ci-attempt','unknown',
                         'missing-wait','config','unicode-transport',
                         'unicode-guard-priority'):
                begin(name)
                source=collector._PollSources(session)
                before=counts(); fault_at=[]; physical_at=[];restorers=[]
                def inject(argv):
                    text=original_read(argv)
                    fault_at.append(counts())
                    physical_at.append(physical_counts())
                    if name=='active':
                        old=reader._active_poll;reader._active_poll=False
                        restorers.append(lambda:setattr(reader,'_active_poll',old))
                    elif name.startswith('depth-'):
                        old=reader._depth
                        reader._depth=(True if name=='depth-bool' else
                                       0 if name=='depth-zero' else old+1)
                        restorers.append(lambda:setattr(reader,'_depth',old))
                    elif name=='reader':
                        old=scope._readers['FINAL:PR_CI']
                        scope._readers['FINAL:PR_CI']=object()
                        restorers.append(lambda:scope._readers.__setitem__('FINAL:PR_CI',old))
                    elif name=='guard':
                        old=reader.guard;reader.guard=lambda:None
                        restorers.append(lambda:setattr(reader,'guard',old))
                    elif name=='deadline':
                        old=reader.current_deadline;reader.current_deadline=old+1
                        restorers.append(lambda:setattr(reader,'current_deadline',old))
                    elif name in {'ci-digest','ci-attempt','unknown'}:
                        old=path.read_bytes(); damaged=state()
                        if name=='ci-digest':
                            damaged['ci']['identity_digest']='sha256:'+('e'*64)
                            assert (damaged['ci']['identity_digest']!=
                                    initial['ci']['identity_digest'])
                        elif name=='ci-attempt':
                            assert damaged['ci']['attempts']==1
                            damaged['ci']['attempts']=2
                            assert (damaged['ci']['attempts']<=
                                    reader.limits['max_ci_polls'])
                        else:
                            assert not any(x['status']=='RESERVED'
                                           for x in damaged['requests'].values())
                            damaged['requests'][str(len(damaged['requests'])+1)]={
                                'command_digest':'sha256:'+('f'*64),'attempts':1,
                                'status':'RESERVED','category':None,'next_check_at':None}
                        save_fault(damaged)
                        restorers.append(lambda:path.write_bytes(old))
                    elif name=='missing-wait':
                        moved=path.with_name('deliberately-hidden-wait.json')
                        assert not moved.exists();path.rename(moved)
                        restorers.append(lambda:moved.rename(path))
                    elif name in {'config','unicode-guard-priority'}:
                        target=seed/'epic-runtime.json';old=target.read_bytes()
                        damaged=json.loads(old)
                        damaged['follow_up_issues']+=1
                        target.write_text(json.dumps(damaged))
                        restorers.append(lambda:target.write_bytes(old))
                    if name in {'unicode-transport','unicode-guard-priority'}:
                        raise UnicodeDecodeError('utf-8',b'\xff',0,1,
                                                 'synthetic malformed transport')
                    return text
                session._github_read=inject
                expected=(FileNotFoundError if name=='missing-wait' else
                          FrameError if name in {'config','unicode-guard-priority'} else
                          PhaseBlocked if name in {'reader','guard','deadline'} else
                          PrimaryCIError)
                args=(None if expected is FileNotFoundError else
                      ('WAIT_FRAME_BINDING_CHANGED',) if expected is FrameError else
                      ('SESSION_SCOPE_CHANGED',) if expected is PhaseBlocked else
                      ('FINAL_PRIMARY_CI_INVALID',))
                try:
                    result=refuse(lambda:source.api(''),expected,args)
                    assert len(fault_at)==1 and counts()==fault_at[0]
                    assert delta(before)==(1,0)
                    journal.append(dict(name=name,at_fault=fault_at[0],after=counts(),
                                        physical_at_fault=physical_at[0],
                                        physical_after=physical_counts(),**result))
                finally:
                    session._github_read=original_read
                    for restore in reversed(restorers):restore()
                assert reader._active_poll is True and reader._depth==source.depth
                assert state()['ci']==initial['ci']
                assert all(x['status']=='READY' for x in state()['requests'].values())

            # Actual malformed gh JSON; no verifier/normalizer success is mocked.
            for name in ('name-list','name-dict','run-long','job-long',
                         'run-range','job-range'):
                begin(name)
                source=collector._PollSources(session);before=counts();transport(name)
                try:
                    result=refuse(lambda:source.graph(positive.comparison),
                                  PrimaryCIError,('FINAL_PRIMARY_CI_INVALID',))
                    assert delta(before)==(1,0)
                    journal.append(dict(name=name,at_fault=counts(),after=counts(),
                                        physical_after=physical_counts(),**result))
                finally:transport('none')

            for name in ('surrogate','invalid-json','recursive-json','non-string'):
                begin(name)
                source=collector._PollSources(session);before=counts()
                def raw_run(*args,**kwargs):
                    value=original_run(*args,**kwargs)
                    replacement=('\ud800' if name=='surrogate' else
                                 '[' if name=='invalid-json' else '['*2000+']'*2000)
                    return subprocess.CompletedProcess(value.args,value.returncode,
                                                       replacement,value.stderr)
                def non_string(argv):
                    original_read(argv)
                    return b'not-text'
                if name=='non-string':session._github_read=non_string
                else:reader.run=raw_run
                try:
                    result=refuse(lambda:source.api(''),PrimaryCIError,
                                  ('FINAL_PRIMARY_CI_INVALID',))
                    assert delta(before)==(1,0)
                    journal.append(dict(name=name,at_fault=counts(),after=counts(),
                                        physical_after=physical_counts(),**result))
                finally:
                    reader.run=original_run;session._github_read=original_read

            begin('origin-utf8');before=counts()
            def origin_bytes(argv,**kwargs):
                value=original_subprocess_run(argv,**kwargs)
                if argv==['git','remote','get-url','origin']:
                    return subprocess.CompletedProcess(value.args,value.returncode,
                                                       b'\xff',value.stderr)
                return value
            subprocess.run=origin_bytes
            try:
                result=refuse(lambda:collector.collect_roadmap_sources_owned(session),
                              PrimaryCIError,('FINAL_PRIMARY_CI_INVALID',))
                assert delta(before)==(0,1)
                journal.append(dict(name='origin-utf8',at_fault=counts(),after=counts(),
                                    physical_after=physical_counts(),**result))
            finally:subprocess.run=original_subprocess_run

            begin('graph-drift');transport('graph-drift');before=counts()
            original_normalize=collector.normalize;normalized=[]
            def observe_normalize(**kwargs):
                value=original_normalize(**kwargs)
                normalized.append(value)
                return value
            collector.normalize=observe_normalize
            try:
                result=refuse(lambda:collector.collect_roadmap_sources_owned(session),
                              PrimaryCIError,('FINAL_PRIMARY_CI_INVALID',))
                assert len(normalized)==2 and normalized[0]!=normalized[1]
                assert normalized[0].sha==normalized[1].sha==positive.comparison.sha
                assert delta(before)==(21,7)
                journal.append(dict(name='graph-drift',at_fault=counts(),after=counts(),
                                    physical_after=physical_counts(),**result))
            finally:
                collector.normalize=original_normalize
                transport('none')
            assert state()['ci']==initial['ci']
            assert all(x['status']=='READY' for x in state()['requests'].values())
            assert counts()==(69,17)
            return {'status':'PASS','machine_code':'OK'}

        identity={'synthetic_original_test_assessment':1}
        reader.poll_ci(identity,first_assessment)
        first=state()
        assert first['ci']['status']=='READY' and first['ci']['attempts']==1
        assert reader._active_poll is False and reader._depth==0
        completed_at_cap=[]
        def last_assessment():
            value=state()
            assert value['ci']['status']=='RESERVED'
            assert value['ci']['attempts']==reader.limits['max_ci_polls']==2
            begin('last-cap-original-positive')
            primary_success()
            completed_at_cap.append(value['ci']['attempts'])
            assert state()['ci']['attempts']==2
            # A REAL text-mode decoding failure leaves the original request
            # RESERVED. It is deliberately terminal and can never be cleared
            # merely to continue this test. The original outer poll marks FAILED.
            begin('real-transport-utf8-terminal')
            transport('transport-utf8');source=collector._PollSources(session)
            before=counts()
            try:source.api('')
            finally:
                assert delta(before)==(1,0)
                transport('none')
            raise AssertionError('malformed UTF-8 admitted')
        ci_probe_refusal=refuse(lambda:reader.poll_ci(identity,last_assessment),
                      PrimaryCIError,('FINAL_PRIMARY_CI_INVALID',))
        journal.append(dict(name='real-transport-utf8-terminal',at_fault=counts(),
                            after=counts(),physical_after=physical_counts(),**ci_probe_refusal))
        final=state()
        assert completed_at_cap==[2]
        assert final['ci']['attempts']==2 and final['ci']['status']=='FAILED'
        assert len(final['requests'])==94
        assert [k for k,v in final['requests'].items()
                if v['status']=='RESERVED']==['94']
        assert all(v['status']=='READY' for k,v in final['requests'].items() if k!='94')
        assert reader._active_poll is False and reader._depth==0
        assert counts()==(94,26)
        assert physical_counts()[1]<=5000
        assert {p:p.read_bytes() for p in aggregate_bytes}==aggregate_bytes
        (seed/'ci-source-probe-receipts.json').write_text(json.dumps({
            'probes':journal,'gh':94,'git':26,'ci_attempts':2,
            'terminal_ci_status':'FAILED','terminal_unknown_requests':['94'],
            'cap_positive_attempts':completed_at_cap,'native_role_calls':len(native_started),
            'physical_gh':physical_counts()[0],'physical_git':physical_counts()[1],
        }))
        assert len(native_started)==1
"""


def endpoints(
    root: Path, mandate: dict[str, Any], graph: dict[str, Any], head: str, main: str
) -> dict[str, Any]:
    """Synthetic provider originals; policy workflow bytes stay original Git."""
    repo, ref = mandate["repository"], mandate["roadmap_ref"]
    result: dict[str, Any] = {f"repos/{repo}": {"full_name": repo}}
    for name, sha in (("main", main), (ref, head)):
        result[f"repos/{repo}/git/ref/heads/{name}"] = {"object": {"sha": sha}}
    for item in graph["check_runs"]["check_runs"]:
        item["head_sha"] = head
        item["details_url"] = item["details_url"].replace("synthetic/repo", repo)
    result[f"repos/{repo}/commits/{head}/check-runs?per_page=100"] = graph[
        "check_runs"
    ]
    for suite_id, suite in graph["suites"].items():
        suite.update(head_sha=head, repository={"full_name": repo})
        result[f"repos/{repo}/check-suites/{suite_id}"] = suite
    for run_id, run in graph["runs"].items():
        run.update(head_sha=head, repository={"full_name": repo})
        filename = Path(run["path"]).name
        definition = subprocess.check_output(
            ["git", "show", f"{mandate['policy_sha']}:{run['path']}"], cwd=root
        )
        label = yaml.safe_load(definition)["name"]
        run["name"] = label
        graph["workflows"][filename]["name"] = label
        result[f"repos/{repo}/actions/runs/{run_id}"] = run
        for job in graph["jobs"][run_id]["jobs"]:
            job["head_sha"] = head
        result[f"repos/{repo}/actions/runs/{run_id}/attempts/2/jobs?per_page=100"] = (
            graph["jobs"][run_id]
        )
    for filename, workflow in graph["workflows"].items():
        result[f"repos/{repo}/actions/workflows/{filename}"] = workflow
    return result


def alternate_graph() -> dict[str, Any]:
    """Different complete same-SHA primary graph, individually normalizable."""
    graph = copy.deepcopy(sources())
    for item in graph["check_runs"]["check_runs"]:
        item["id"] += 1000
        item["check_suite"]["id"] += 1000
        before, job = item["details_url"].rsplit("/job/", 1)
        before, run = before.rsplit("/runs/", 1)
        item["details_url"] = f"{before}/runs/{int(run) + 1000}/job/{int(job) + 1000}"
    for suite in graph["suites"].values():
        suite["id"] += 1000
    graph["suites"] = {key + 1000: value for key, value in graph["suites"].items()}
    for run in graph["runs"].values():
        run["id"] += 1000
        run["check_suite_id"] += 1000
    graph["runs"] = {key + 1000: value for key, value in graph["runs"].items()}
    for rows in graph["jobs"].values():
        for job in rows["jobs"]:
            job["id"] += 1000
            job["run_id"] += 1000
    graph["jobs"] = {key + 1000: value for key, value in graph["jobs"].items()}
    return graph


GH = r"""import copy,json,os,sys
from pathlib import Path
seed=Path(__file__).resolve().parents[1]
control=seed/'primary-fault.json'
fault=json.loads(control.read_text())
mode=fault['mode']
assert len(sys.argv)==3 and sys.argv[1]=='api'
if mode=='transport-utf8':
    os.write(1,b'\xff');raise SystemExit(0)
originals=json.loads((seed/'primary-originals.json').read_text())
endpoint=sys.argv[2]
check='/commits/' in endpoint and endpoint.endswith('/check-runs?per_page=100')
if mode=='graph-drift' and check:
    fault['check_reads']+=1;control.write_text(json.dumps(fault))
table=originals['alternate' if mode=='graph-drift'
                and fault['check_reads']>=2 else 'base']
value=copy.deepcopy(table[endpoint])
if check:
    item=value['check_runs'][0]
    if mode=='name-list':item['name']=['untrusted-name']
    if mode=='name-dict':item['name']={'untrusted':'name'}
    if mode in {'run-long','job-long','run-range','job-range'}:
        before,job=item['details_url'].rsplit('/job/',1)
        before,run=before.rsplit('/runs/',1)
        invalid='9'*4401 if mode.endswith('long') else '9223372036854775808'
        if mode.startswith('run-'):run=invalid
        else:job=invalid
        item['details_url']=before+'/runs/'+run+'/job/'+job
print(json.dumps(value))
"""


@pytest.mark.usefixtures("active_runtime_namespace")
def test_original_reserved_poll_collects_neutral_primary_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_setup = native_fixture.original_sources

    def setup_with_original_workflows(*args: Any, **kwargs: Any) -> Any:
        root, seed = original_setup(*args, **kwargs)
        # Explicit NEW initial profile choice, before original factory policy
        # commit/install/launch/preseed sealing or any Scope/frame/native call.
        assert not (seed / "invocations").exists()
        wait = guards.private_json(seed / "wait-profile.json")
        assert wait["limits"]["max_ci_polls"] == 11
        wait["limits"]["max_ci_polls"] = 2
        write(seed / "wait-profile.json", wait)
        directory = root / ".github/workflows"
        directory.mkdir(parents=True, exist_ok=True)
        for filename, definition in sources()["definitions"].items():
            (directory / filename).write_bytes(definition)
        return root, seed

    monkeypatch.setattr(
        native_fixture, "original_sources", setup_with_original_workflows
    )
    root, seed, policy, driver = native_factory_fixture(tmp_path, monkeypatch)
    mandate = json.loads((seed / "mandate.json").read_text())
    head = subprocess.check_output(
        ["git", "rev-parse", mandate["roadmap_ref"]], cwd=root
    ).decode().strip()
    main = (
        subprocess.check_output(["git", "rev-parse", "main"], cwd=root).decode().strip()
    )
    (seed / "primary-originals.json").write_text(
        json.dumps(
            {
                "base": endpoints(root, mandate, sources(), head, main),
                "alternate": endpoints(root, mandate, alternate_graph(), head, main),
            }
        )
    )
    (seed / "primary-fault.json").write_text(
        json.dumps({"mode": "none", "check_reads": 0})
    )
    binary = seed / "primary-bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text("#!" + sys.executable + "\n" + GH)
    gh.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    marker = (
        '    print(json.dumps({"status": "COMPLETED", '
        '"verdict": result["output"]["verdict"]}))'
    )
    assert NATIVE_DRIVER.count(marker) == 1
    driver.write_text(
        NATIVE_DRIVER.replace(
            marker,
            "\n".join("    " + line for line in PROBE.splitlines()) + "\n" + marker,
        )
    )
    assert run_native_driver(root, seed, policy, driver, "short-run") == {
        "status": "COMPLETED",
        "verdict": "PASS",
    }
    receipt = json.loads((seed / "ci-source-probe-receipts.json").read_text())
    assert len(receipt["probes"]) == 28
    assert receipt["gh"] == 94 and receipt["git"] == 26
    assert receipt["ci_attempts"] == 2 and receipt["cap_positive_attempts"] == [2]
    assert receipt["terminal_ci_status"] == "FAILED"
    assert receipt["terminal_unknown_requests"] == ["94"]
    assert receipt["native_role_calls"] == 1
