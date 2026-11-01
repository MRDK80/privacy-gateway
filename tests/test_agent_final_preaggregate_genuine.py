"""New fresh original two-task FINAL preaggregate fixture, private INITIAL only."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from tools import agent_epic_runtime_cli as guards
from tools.agent_learning_planning_catalog import catalog_digest
from tools.agent_orchestrate import StateStore

from tests import learning_session_factory_fixture as factory
from tests.learning_session_native_fixture import NATIVE
from tests.learning_session_primary_fixture import Primary
from tests.test_agent_learning import profile
from tests.test_agent_learning_integration import roles
from tests.test_agent_learning_planning_catalog import sample
from tests.test_agent_learning_workflow_binding import initial_config8

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX original ownership")


def prepared_two_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    source = tmp_path / "source"
    shutil.copytree(
        Path.cwd() / "tools",
        source / "tools",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(Path.cwd() / "docs/schemas", source / "docs/schemas")
    shutil.copytree(Path.cwd() / ".github/workflows", source / ".github/workflows")
    catalog = sample()
    first = catalog["rules"][0]
    first.update(
        rule_id="planning-criteria", task_class="feature", problem_class="criteria"
    )
    first["environment"]["python_minor"] = (
        f"{sys.version_info.major}.{sys.version_info.minor}"
    )
    second = copy.deepcopy(first)
    second.update(
        rule_id="planning-review",
        role="controller",
        problem_class="review",
        validator="REVIEW_V1",
        failure_code="PLANNING_REVIEW_INVALID",
        symptom_code="PLANNING_REVIEW_INVALID",
    )
    catalog["rules"].append(second)
    empty = {"schema_version": "1.0", "rules": []}
    (source / "docs/schemas/agent-learning-catalog.json").write_text(json.dumps(empty))
    (source / "docs/schemas/agent-learning-planning-catalog.json").write_text(
        json.dumps(catalog)
    )
    monkeypatch.setattr(factory, "SOURCE", source)
    value = factory.build(
        tmp_path / "fixture",
        extra_tasks=(303,),
        delivery_enabled=True,
        with_demo=True,
        with_planning_policy=True,
        with_local_gate_sut=True,
        initial_frame_seconds=600,
    )
    state = Path(value["state"])
    store = StateStore(state)
    config = store.load("epic-runtime")
    adapter = store.load("runtime-adapter")
    launch = store.load("launch")
    mandate = store.load("mandate")
    plans = store.load("approved-plans")
    assert (
        config is not None
        and adapter is not None
        and launch is not None
        and mandate is not None
        and plans is not None
    )
    learning = profile()
    learning.update(
        policy_sha=value["sha"],
        mandate_digest=config["approved_mandate_digest"],
        catalog_digest=catalog_digest(empty, catalog),
    )
    store.save("learning-profile", learning)
    template = plans["tasks"]["301"]["demo_plan"]
    planning = {
        "schema_version": "1.0",
        **{
            key: mandate[key]
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        },
        "mandate_digest": config["approved_mandate_digest"],
        "tasks": {
            str(task): {
                "criteria_sections": ["Acceptance"],
                "task_class": "feature",
                "demo_template": {**copy.deepcopy(template), "task": task},
            }
            for task in (301, 303)
        },
        "limits": {
            "max_planner_attempts": 2,
            "max_review_attempts": 2,
            "max_input_chars": 200000,
            "max_output_chars": 20000,
            "max_paths": 4,
        },
    }
    primary = Primary(
        Path(value["root"]),
        tmp_path / "remote.git",
        policy_sha=value["sha"],
        tasks=(301, 303),
    )
    root = Path(value["root"])
    from tools import agent_gate

    original = (root / "code.py").read_bytes()
    previews = {}
    initial_ref = primary.git("branch", "--show-current")
    previous = value["sha"]
    try:
        for task, changed in ((301, 2), (303, 3)):
            if task == 303:
                primary.git("switch", "--detach", previous)
            (root / "code.py").write_text(f"VALUE = {changed}\n")
            with agent_gate.trusted_snapshot_session(
                root=root, base_sha=previous, allowed_paths=("code.py",)
            ) as snapshot:
                # Local object import only, independent of the remote transport.
                import subprocess

                subprocess.run(
                    [
                        "git",
                        "fetch",
                        "--quiet",
                        str(snapshot.checkout.parent / "mirror.git"),
                        snapshot.snapshot_commit,
                    ],
                    cwd=root,
                    check=True,
                    timeout=15,
                )
                merge = primary.git(
                    "commit-tree",
                    snapshot.tree_hash,
                    "-p",
                    previous,
                    "-p",
                    snapshot.snapshot_commit,
                    "-m",
                    "synthetic primary merge",
                )
                previews[str(task)] = {"head": snapshot.snapshot_commit, "merge": merge}
            (root / "code.py").write_bytes(original if task == 301 else b"VALUE = 2\n")
            previous = merge
    finally:
        primary.git("switch", initial_ref)
        (root / "code.py").write_bytes(original)
    assert primary.git("rev-parse", "HEAD") == value["sha"]
    assert not primary.git("status", "--porcelain")
    for task in (303,):
        planning["tasks"][str(task)]["demo_template"]["roadmap_sha"] = previews["301"][
            "merge"
        ]
    (state / "session-preview.json").write_text(json.dumps(previews))
    value["remote"] = str(primary.remote)
    store.save("planning-profile", planning)
    assert not (state / "invocations").exists()
    assert adapter["timeout_seconds"] == 600
    wait_profile = store.load("wait-profile")
    assert wait_profile is not None
    assert wait_profile["limits"] == {
        "max_phase_seconds": 600,
        "max_read_attempts": 2,
        "max_read_commands": 512,
        "max_ci_polls": 8,
    }
    adapter["timeout_seconds"] = 600
    store.save("runtime-adapter", adapter)
    config.update(
        schema_version="7.0",
        approved_learning_digest=guards.canonical_digest(learning),
        approved_planning_digest=guards.canonical_digest(planning),
    )
    launch["runtime_config_digest"] = guards.launch_config_digest(config, adapter)
    config["approved_launch_digest"] = guards.canonical_digest(launch)
    store.save("epic-runtime", config)
    store.save("launch", launch)
    control = store.load("launch-control")
    assert control is not None
    control["launch_digest"] = config["approved_launch_digest"]
    store.save("launch-control", control)
    binary = state / "planning-native-bin"
    binary.mkdir(mode=0o700)
    command = binary / "codex"
    command.write_text("#!" + sys.executable + "\n" + NATIVE)
    command.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    return value


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
api=Primary(root,Path(REMOTE),policy_sha=reader.owner['policy_sha'],tasks=(301,303),initialize=False)
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

from tools.agent_final_learning_task_rows import read_preaggregate_task_rows_owned
from tools.agent_epic_session import Session
from tools.agent_learning import LearningError
from dataclasses import asdict
native_calls=[]
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
        role='executor' if command==wrapped.executor_command else 'controller'
        native_calls.append((payload['contract']['issue'],role))
        target=(synthetic.executor_command if role=='executor'
            else synthetic.controller_command)
        return synthetic._call(target,payload)
    wrapped.delegate._call=native
    engine.transport.command=api.command
    return engine
session.task_adapter=adapter
try:
    session.resume()
except loop.PhaseBlocked as error:
    assert error.machine_code=='FINAL_DEMO_UNCONFIRMED'
    assert session.config['approved_final_digest'] is None
else:
    raise AssertionError('expected original null-final boundary')
cursor=cli.private_json(state/'session-control/cursor.json')
seed=session.store.load()
assert seed is not None
Session._validate_cursor(cursor,seed)
assert cursor['mode']=='FINAL' and cursor['completed_tasks']==[301,303]
assert cursor['pending_task'] is None and cursor['current_task']==303
assert session.config['approved_order']==[301,303]
assert set(reader.mandate['task_grants'])=={'301','303'}
assert set(reader._json(state/'planning-profile.json')['tasks'])=={'301','303'}
assert not (state/'planning/301').exists()
assert native_calls==[(301,'executor'),(301,'controller'),
    (303,'executor'),(303,'controller')]
planning_calls=[json.loads(line) for line in
    (state/'native-planning-observations.jsonl').read_text().splitlines()]
assert [(x['task'],x['role']) for x in planning_calls]==[(303,'planner'),(303,'review')]
assert not any(x['hinted'] for x in planning_calls)
for task in (301,303):
    assert api.pulls[task].merge_sha==preview[str(task)]['merge']
    fact=reader.workflow(task)
    assert fact.state['status']=='PASS'
    assert fact.state['executor_calls']==fact.state['controller_calls']==1
rows=None
faults=[]
with loop.RunnerLock(state/'session-control'), loop.RunnerLock(state):
    current=cli.final_preparation_original_sources(state,root)['current']
    assert (current['calls_consumed']==0
        and current['pending_reservation_digest'] is None)
    assert not (state/'final-preparation/role-settings').exists()
    original_current=copy.deepcopy(current)
    with session._stage('FINAL:FINAL_GATE'):
        rows=read_preaggregate_task_rows_owned(session)
        assert [x.task for x in rows]==[301,303]
        for row in rows:
            original=reader.workflow(row.task)
            assert row.workflow_run_id==original.state['run_id']
            assert row.workflow_calls==(original.state['executor_calls']
                +original.state['controller_calls'])
        admission=reader._json(state/'planning/303/admission.json')
        assert rows[0].planning_attempts==0
        assert rows[1].planning_attempts==sum(admission['attempts'].values())==2
        # Fault1: one exact original binding is absent; no fabricated replacement.
        binding_path=next(iter(sorted((state/'tasks/303/workflow-learning-bindings').glob('*.json'))))
        binding_bytes=binding_path.read_bytes()
        try:
            binding_path.unlink()
            try:
                read_preaggregate_task_rows_owned(session)
            except LearningError as error:
                assert error.machine_code=='LEARNING_SOURCE_MISMATCH'
                faults.append('missing-binding')
            else:
                raise AssertionError('missing original binding admitted')
        finally:
            binding_path.write_bytes(binding_bytes)
            binding_path.chmod(0o600)
        assert binding_path.read_bytes()==binding_bytes
        # Fault2: an extant no-admission container cannot be interpreted as noP.
        container=state/'planning/301'
        assert not container.exists() and not container.is_symlink()
        try:
            container.mkdir(mode=0o700)
            try:
                read_preaggregate_task_rows_owned(session)
            except LearningError as error:
                assert error.machine_code=='LEARNING_SOURCE_MISSING'
                faults.append('partial-planning-container')
            else:
                raise AssertionError('partial planning source admitted')
        finally:
            container.rmdir()
    # Fault3: original owned lock is insufficient without live FINAL stage.
    assert session.wait_scope.active_stage is None
    try:
        read_preaggregate_task_rows_owned(session)
    except loop.PhaseBlocked as error:
        assert error.machine_code=='WAIT_FRAME_REQUIRED'
        faults.append('outside-final-stage')
    else:
        raise AssertionError('source read outside original stage admitted')
assert faults==['missing-binding','partial-planning-container','outside-final-stage']
assert rows is not None
with loop.RunnerLock(state/'session-control'), loop.RunnerLock(state):
    assert (cli.final_preparation_original_sources(state,root)['current']
        ==original_current)
assert not (state/'final-preparation/role-settings').exists()
assert len(list((state/'role-settings').glob('receipt-*.json')))==4
print(json.dumps({'tasks':[asdict(x) for x in rows], 'workflow_calls':native_calls,
    'planning_calls':[(x['task'],x['role']) for x in planning_calls],
    'faults':faults,'aggregate_calls':0}))
"""


def preaggregate_at_driver_preflight(
    body: str, *, collector: bool = False,
) -> str:
    """Place zero-state reader assertions in the original Driver preflight.

    Only test-script lexical scaffolds change. Actual Session, structural
    preflight, capture, locks, readers, calls and failure predicates stay real.
    """
    assert type(body) is str and type(collector) is bool
    old_resume = (
        "try:\n    session.resume()\n"
        "except loop.PhaseBlocked as error:\n"
        "    assert error.machine_code=='FINAL_DEMO_UNCONFIRMED'\n"
        "    assert session.config['approved_final_digest'] is None\n"
        "else:\n    raise AssertionError('expected original null-final boundary')\n"
    )
    assert body.count(old_resume) == 1
    before, after = body.split(old_resume, 1)
    lock = "with loop.RunnerLock(state/'session-control'), loop.RunnerLock(state):\n"
    stage = "    with session._stage('FINAL:FINAL_GATE'):\n"
    outside = "    assert session.wait_scope.active_stage is None\n"
    if collector:
        assert after.count(lock) == after.count(stage) == 1
        prefix, owned = after.split(lock, 1)
        before_stage, positive_tail = owned.split(stage, 1)
        assert before_stage.count(outside) == 1
        owned_prefix, negative = before_stage.split(outside, 1)
        before_sources = '    before=source_files()\n'
        assert negative.endswith(before_sources)
        negative = negative[:-len(before_sources)]
        owned_prefix += before_sources
        end = "assert not (state/'final-preparation/role-settings').exists()\n"
        assert positive_tail.count(end) == 1
        positive, after_unwind = positive_tail.split(end, 1)
        final = (
            "    assert cli.final_preparation_original_sources(state,root)"
            "['current']==original_current\n"
        )
        assert positive.endswith(final)
        positive = positive[:-len(final)]
        group = (
            prefix + textwrap.dedent(owned_prefix)
            + textwrap.dedent(positive) + textwrap.dedent(final)
        )
        after_unwind = lock + outside + negative + end + after_unwind
        old_order = "assert faults==['outside-final-stage','missing-binding']"
        new_order = "assert faults==['missing-binding','outside-final-stage']"
        assert after_unwind.count(old_order) == 1
        after_unwind = after_unwind.replace(old_order, new_order, 1)
    else:
        marker = 'rows=None\nfaults=[]\n'
        assert after.count(marker) == 1
        prefix, tail = after.split(marker, 1)
        assert tail.startswith(lock) and tail.count(lock) == 2
        tail = tail[len(lock):]
        negative_marker = '    # Fault3: original owned lock is insufficient'
        assert tail.count(negative_marker) == 1
        positive, negative_tail = tail.split(negative_marker, 1)
        assert positive.count(stage) == 1
        owned_prefix, positive = positive.split(stage, 1)
        end = (
            "assert faults==['missing-binding','partial-planning-container',"
            "'outside-final-stage']\n"
        )
        assert negative_tail.count(end) == 1
        negative, after_unwind = negative_tail.split(end, 1)
        group = (
            prefix + marker + textwrap.dedent(owned_prefix)
            + textwrap.dedent(positive)
        )
        after_unwind = lock + negative_marker + negative + end + after_unwind
    setup = (
        '\nfrom tools import agent_final_preparation_runtime as preaggregate_runtime\n'
        'class PreaggregateBoundaryStop(BaseException):\n    pass\n'
        'original_preaggregate_preflight='
        'preaggregate_runtime.structural_child_preflight\n'
        'def preaggregate_preflight(actual_session):\n'
        '    assert actual_session is session\n'
        "    assert session.wait_scope.active_stage=='FINAL:FINAL_GATE'\n"
        "    loop.RunnerLock.current_owned(state/'session-control')\n"
        '    loop.RunnerLock.current_owned(state)\n'
        '    original_preaggregate_preflight(actual_session)\n'
        "    assert session.config['approved_final_digest'] is None\n"
        '    exec(' + repr(group) + ',globals())\n'
        '    raise PreaggregateBoundaryStop()\n'
        'preaggregate_runtime.structural_child_preflight=preaggregate_preflight\n'
        'try:\n    session.resume()\n'
        'except PreaggregateBoundaryStop:\n    pass\n'
        "else:\n    raise AssertionError('expected original preflight boundary')\n"
        'finally:\n    preaggregate_runtime.structural_child_preflight='
        'original_preaggregate_preflight\n'
    )
    return before + setup + after_unwind


def test_fresh_original_two_task_FINAL_preaggregate_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = prepared_two_tasks(tmp_path, monkeypatch)
    initial_config8(value)
    state = Path(value["state"])
    assert not (state / "invocations").exists()
    roles(value)
    script = state / "synthetic-roles.py"
    text = script.read_text()
    old = "if fields['issue']==301: (root/'code.py').write_text('VALUE = 2\\n')"
    new = ("(root/'code.py').write_text('VALUE = '+"
           "str({301:2,303:3}[fields['issue']])+'\\n')")
    assert text.count(old) == 1
    script.write_text(text.replace(old, new))
    harness = state / "fresh-final-preaggregate.py"
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
        + "REMOTE="
        + repr(value["remote"])
        + "\n"
        + preaggregate_at_driver_preflight(BODY)
        + "\n"
    )
    run = subprocess.run(
        [sys.executable, "-B", str(harness)],
        capture_output=True,
        text=True,
        timeout=660,
    )
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert [x["task"] for x in result["tasks"]] == [301, 303]
    assert [(x["workflow_calls"], x["planning_attempts"]) for x in result["tasks"]] == [
        (2, 0),
        (2, 2),
    ]
    assert result["workflow_calls"] == [
        [301, "executor"],
        [301, "controller"],
        [303, "executor"],
        [303, "controller"],
    ]
    assert result["planning_calls"] == [[303, "planner"], [303, "review"]]
    assert result["faults"] == [
        "missing-binding",
        "partial-planning-container",
        "outside-final-stage",
    ]
    assert result["aggregate_calls"] == 0
