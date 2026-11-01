"""Genuine completed native aggregate reread under real existing ownership."""

from __future__ import annotations

import os

import pytest

from tests.test_agent_epic_loop import (
    active_runtime_namespace as active_runtime_namespace,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX managed source locks")

READ_ONLY = r"""
from tools.agent_epic_session_runtime import SessionRuntime
from tools import agent_epic_runtime_cli as guards
from tools import agent_continuation_backend as settings_backend
from tools import agent_continuation_store as source_store
from tools import agent_gate
from tools.agent_final_preparation_storage import Store
session=SessionRuntime(
    state=seed,root=root,config=guards.private_json(seed/'epic-runtime.json'),
    wait_scope=scope,
)
# These are only effect-boundary spies AFTER genuine native completion, never
# original source readers or ownership/authority guards.
def forbidden(*args,**kwargs):
    raise AssertionError('completed-only reader attempted write/factory/gate/native')
settings_backend.SettingsAdapter.__init__=forbidden
settings_backend.SettingsStore.__init__=forbidden
runtime.codex_role_adapter=forbidden
Store.write=forbidden
source_store.Store.save=forbidden
runtime.RoleCounter.reserve=forbidden
agent_gate.run_profile=forbidden
runtime._ReservedDelegate._call=forbidden
canonical=[seed/'final-preparation/role-state.json']
canonical+=list((seed/'final-preparation').glob('reservation-*.json'))
canonical+=list((seed/'final-preparation/sources').glob('*.json'))
canonical+=list((seed/'final-preparation/role-settings').glob('*.json'))
original={path:path.read_bytes() for path in canonical}
with RunnerLock.supervisor(seed/'session-control'),RunnerLock(seed):
    with scope.context('FINAL:FINAL_GATE'):
        first=runtime.read_completed_aggregate_owned(session)
        assert first==result
        reservation_path=next((seed/'final-preparation').glob('reservation-*.json'))
        reservation=guards.private_json(reservation_path)
        assert reservation['deadline_at']-time.time()<16
        time.sleep(max(0,reservation['deadline_at']-time.time())+0.05)
        assert time.time()>reservation['deadline_at']
        assert runtime.read_completed_aggregate_owned(session)==result
        assert {path:path.read_bytes() for path in original}==original
        for attack in (
            'zero','reservation','unknown','cache','settings','cursor','checkpoint',
        ):
            target=(seed/'final-preparation/role-state.json' if attack=='zero' else
                    seed/'epic-loop-checkpoint.json' if attack=='checkpoint' else
                    next((seed/'final-preparation').glob('reservation-*.json'))
                    if attack in {'reservation','unknown'} else
                    next((seed/'final-preparation/sources').glob('*.json'))
                    if attack=='cache' else
                    next((seed/'final-preparation/role-settings').glob('*.json'))
                    if attack=='settings' else seed/'session-control/cursor.json')
            saved=target.read_bytes()
            try:
                if attack in {'cache','settings'}:
                    target.unlink()
                else:
                    value=json.loads(saved)
                    if attack=='cursor': value['mode']='DONE'
                    elif attack=='checkpoint': value['status']='BLOCKED'
                    elif attack=='zero':
                        from tools.agent_final_preparation_records import seal
                        value=seal({**value,'calls_consumed':0,'pending_reservation_digest':None})
                    else:
                        from tools.agent_final_preparation_records import seal
                        status='UNKNOWN' if attack=='unknown' else 'RESERVED'
                        value=seal({**value,'status':status,
                                    'settings_digest':None,'output_digest':None})
                    target.write_text(json.dumps(value))
                try: runtime.read_completed_aggregate_owned(session)
                except Exception as error:
                    assert type(error).__name__ in {
                        'DeploymentError','PhaseBlocked','LoopError',
                    }, (attack,type(error).__name__,str(error))
                else: raise AssertionError('invalid source accepted:'+attack)
            finally:
                target.write_bytes(saved);target.chmod(0o600)
            assert {path:path.read_bytes() for path in original}==original
        assert runtime.read_completed_aggregate_owned(session)==result
# Both locks have actually exited; no borrowed ownership may survive.
try: runtime.read_completed_aggregate_owned(session)
except Exception as error:
    assert type(error).__name__ in {'PhaseBlocked','LoopError'}
else: raise AssertionError('missing original ownership accepted')
"""


@pytest.mark.usefixtures("active_runtime_namespace")
def test_completed_only_original_cache_without_factory_gate_call_or_publish(
    completed_learning_readers: dict[str, str],
) -> None:
    assert completed_learning_readers['read_only'] == 'PASS'
