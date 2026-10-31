"""Five projections from one genuine aggregate seed and synthetic primary I/O."""

from __future__ import annotations

import os

import pytest

from tests.test_agent_epic_loop import (
    active_runtime_namespace as active_runtime_namespace,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX managed sources")

PROJECTION = r"""
from tools.agent_epic_session_runtime import SessionRuntime
from tools import agent_epic_runtime_cli as guards
session = SessionRuntime(
    state=seed, root=root, config=guards.private_json(seed/'epic-runtime.json'),
    wait_scope=scope,
)
def forbidden(*args, **kwargs):
    raise AssertionError('reader attempted production/gate/model effect')
runtime.RoleCounter.reserve = forbidden
runtime._ReservedDelegate._call = forbidden
agent_gate.run_profile = forbidden
storage.Store.write = forbidden
native_store.Store.save = forbidden
originals = {p: p.read_bytes() for p in (seed/'final-preparation').rglob('*')
             if p.is_file()}
with RunnerLock.supervisor(seed/'session-control'), RunnerLock(seed):
    with session._stage('FINAL:PR_CI'):
        a = runtime.read_completed_aggregate_owned(session)
        e = runtime.read_completed_environment_owned(session)
        assert a == result
        actual = runtime.read_current_final_source_digests_owned(session)
        gate = a['request']['gate_evidence']
        assert actual == {
            'snapshot_digest': runtime.digest(gate['snapshot']),
            'local_gate_digest': runtime.digest(
                {'gate_evidence': gate, 'environment': e}),
            'review_digest': runtime.digest(a),
            'children_digest': runtime.digest({
                'repository': scope.mandate['repository'],
                'epic': scope.mandate['epic'], 'children': [],
            }),
            'task_delivery_digest': runtime.digest([]),
        }
        assert {p: p.read_bytes() for p in originals} == originals
        # Delete the real environment original: it must never become a default.
        environment_path = next(
            (seed/'final-preparation/sources').glob('environment-*.json'))
        saved = environment_path.read_bytes()
        try:
            environment_path.unlink()
            try: runtime.read_current_final_source_digests_owned(session)
            except Exception as error:
                assert type(error).__name__ in {'DeploymentError', 'PhaseBlocked'}
            else: raise AssertionError('absent original environment admitted')
        finally:
            environment_path.write_bytes(saved)
            environment_path.chmod(0o600)
        assert {p: p.read_bytes() for p in originals} == originals
"""


@pytest.mark.usefixtures("active_runtime_namespace")
def test_original_aggregate_five_projections_with_primary_empty_children(
    completed_learning_readers: dict[str, str],
) -> None:
    assert completed_learning_readers['projection'] == 'PASS'
