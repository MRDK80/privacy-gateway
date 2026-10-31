"""One actual completed-learning producer, three original read-only groups.

Test-only acquisition uses canonical synthetic Primary. Production sources and
capture selectors are not replaced. This module is imported lazily by one
session FixtureDef; it never starts a producer at module import.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest


def completed_reader_case(tmp_path: Path) -> dict[str, str]:
    from tests import test_agent_final_learning_lifecycle_genuine as canonical
    from tests.test_agent_learning_integration import roles
    from tests.test_agent_learning_workflow_binding import initial_config8

    with pytest.MonkeyPatch.context() as patch:
        def initial_resources(full_owner: bool = False) -> tuple[int, int, int]:
            assert type(full_owner) is bool and not full_owner
            return 3600, 7200, 7190

        patch.setattr(canonical, '_initial_fixture_resources', initial_resources)
        patch.setenv(
            'PATH', str(canonical._canonical_bin()) + os.pathsep + os.environ['PATH'],
        )
        # Canonical no-install copy/relocation; the caller's admitted warm cache
        # remains the source. No install fallback is introduced here.
        original_cache, cache_before = canonical._cache(tmp_path, patch)
        value = canonical._prepared_two_tasks(tmp_path, patch, full_owner=False)
        initial_config8(value)
        state = Path(value['state'])
        assert not (state / 'invocations').exists()
        roles(value)
        script = state / 'synthetic-roles.py'
        text = script.read_text()
        old = "if fields['issue']==301: (root/'code.py').write_text('VALUE = 2\\n')"
        new = (
            "(root/'code.py').write_text('VALUE = '+"
            "str({301:2,303:3}[fields['issue']])+'\\n')"
        )
        assert text.count(old) == 1
        script.write_text(text.replace(old, new))
        marker = 'session.task_adapter=adapter\n'
        assert canonical.HISTORY.count(marker) == 1
        # Runtime final HISTORY includes every canonical string replacement and
        # the fixed top-level diagnostic insertion loop, not its initial literal.
        prefix, after = canonical.HISTORY.split(marker, 1)
        cutoff = 'try:\n    outcome=session.resume()\n'
        assert after.count(cutoff) == 1
        before_resume = marker + after.split(cutoff, 1)[0]
        guarded = before_resume + 'session.resume()\n'
        history = prefix + SUPPORT + '\ntry:\n'
        history += ''.join('    ' + line if line.strip() else line
                           for line in guarded.splitlines(True))
        history += 'except CompletedReaderStop:\n    pass\n'
        history += 'else:\n    raise AssertionError("EXPECTED_COMPLETED_READER_STOP")\n'
        history += RESTORE_AND_NEGATIVES
        header = (
            'import sys,json,hashlib,time,os\nfrom pathlib import Path\n'
            + 'sys.path.insert(0,' + repr(str(Path(value['installed']) / 'policy'))
            + ')\nfrom tools.agent_learning_evidence import SourceReader\n'
            + 'state=Path(' + repr(str(state)) + ')\nseed=state\n'
            + 'root=Path(' + repr(value['root']) + ')\n'
            + 'source=Path(' + repr(str(Path.cwd())) + ')\n'
            + 'reader=SourceReader(state,root)\nREMOTE=' + repr(value['remote'])
            + '\nOBSERVATION=' + repr(canonical.OBSERVATION) + '\n'
        )
        harness = state / 'completed-reader-originals.py'
        diagnostic_fd, diagnostic_path, diagnostic_identity = (
            canonical._malformed_sink_file(state)
        )
        try:
            harness.write_text(canonical._malformed_harness(
                header + history, diagnostic_fd, Path(value['installed']) / 'policy',
            ))
            completed = subprocess.run(
                [sys.executable, '-B', str(harness)], capture_output=True,
                text=True, timeout=3600, pass_fds=(diagnostic_fd,),
            )
        finally:
            try:
                os.close(diagnostic_fd)
            except Exception:
                pass
        diagnostic_report = canonical._malformed_readback(
            diagnostic_path, diagnostic_identity, failed=completed.returncode != 0,
        )
        # Private synthetic raw capture retained on failure; no external export.
        for name, stream in (
            ('stdout', completed.stdout), ('stderr', completed.stderr),
        ):
            assert len(stream) <= 200_000
            raw = stream.encode('utf-8')
            assert len(raw) <= 200_000
            path = state / ('completed-reader-' + name)
            path.write_bytes(raw)
            path.chmod(0o600)
        assert completed.returncode == 0, completed.stderr[-8000:] + diagnostic_report
        lines = completed.stdout.splitlines()
        assert len(lines) == 1
        result = json.loads(lines[0])
        assert result == {
            'projection': 'PASS', 'read_only': 'PASS', 'environment': 'PASS',
            'missing_ownership': 'PASS', 'outside_pr_ci': 'PASS',
        }
        assert hashlib.sha256((original_cache / 'db.db').read_bytes()).hexdigest() == (
            cache_before['db.db']['sha256']
        )
        for name in cache_before:
            if name != 'db.db':
                assert canonical._tree_map(original_cache / name) == (
                    cache_before[name]['map']
                )
        return cast(dict[str, str], result)


SUPPORT = (
    "COMPLETED_ENVIRONMENT = (\n    '\\nfrom tools.agent_epic_"
    "session_runtime import SessionRu'\n    'ntime\\nfrom tool"
    "s import agent_epic_runtime_cli as guard'\n    's\\nfrom "
    "tools import agent_final_preparation_records as '\n    '"
    'records\\nfrom tools import agent_final_preparation_revi'
    "e'\n    'w as review_module\\n\\noriginal = runtime.read_c"
    "ompleted_e'\n    'nvironment_owned(session)\\nassert (\\n "
    '   original["launch\'\n    \'er_python_version"]\\n    == r'
    "eview_module.platform.pytho'\n    'n_version()\\n)\\ncount"
    "er = runtime.RoleCounter(\\n    runtim'\n    'e._ReadOnly"
    "PreparationStore(\\n        seed,\\n        root'\n    ',\\"
    "n        runtime._OwnedCompletedSources(session).profi'"
    "\n    'le,\\n        runtime._OwnedCompletedSources(sessi"
    'on).che\'\n    \'ck,\\n    ),\\n    session.config["approved'
    '_launch_digest"]\'\n    \',\\n)\\ntarget = (\\n    seed\\n    '
    '/ "final-preparation/source\'\n    \'s"\\n    / ("environme'
    'nt-" + runtime.digest(counter.ident\'\n    \'ity) + ".json'
    '")\\n)\\nsaved = target.read_bytes()\\nreservati\'\n    \'on '
    '= next((seed / "final-preparation").glob("reservatio\'\n '
    '   \'n-*.json"))\\nbefore = reservation.read_bytes()\\nrev'
    'iew_mo\'\n    \'dule.platform.python_version = lambda: "99'
    '9.998.997"\\nas\'\n    \'sert runtime.read_completed_enviro'
    "nment_owned(session) '\n    '== original\\nassert runtime"
    ".read_completed_aggregate_own'\n    'ed(session) == resu"
    "lt\\n\\ndef forbidden(*args, **kwargs):\\n'\n    '    raise"
    ' AssertionError("replacement call/publication"\'\n    \')\\'
    'n\\nruntime.RoleCounter.reserve = forbidden\\nruntime._Re'
    "s'\n    'ervedDelegate._call = forbidden\\nagent_gate.run"
    "_profile '\n    '= forbidden\\nstorage.Store.write = forb"
    'idden\\nfor attack \'\n    \'in (\\n    "missing",\\n    "ver'
    'sion",\\n    "hash",\\n    "ide\'\n    \'ntity",\\n    "input'
    '",\\n    "cache",\\n    "extra",\\n    "dup\'\n    \'licate",'
    '\\n    "symlink",\\n    "hardlink",\\n    "oversize",\'\n   '
    ' \'\\n    "fifo",\\n    "directory",\\n    "mode",\\n    "in'
    'teger"\'\n    \',\\n    "kind",\\n    "digest",\\n    "schema'
    '",\\n    "missing-\'\n    \'field",\\n    "repository",\\n   '
    ' "launch",\\n    "mandate",\\n\'\n    \'):\\n    alias = targ'
    'et.with_suffix(".alias")\\n    try:\\n  \'\n    \'      valu'
    'e = json.loads(saved)\\n        if attack == "m\'\n    \'is'
    'sing":\\n            target.unlink()\\n        elif attac'
    '\'\n    \'k == "symlink":\\n            target.rename(alias'
    ")\\n      '\n    '      target.symlink_to(alias)\\n       "
    ' elif attack == "\'\n    \'hardlink":\\n            import '
    "os\\n\\n            os.link(t'\n    'arget, alias)\\n      "
    '  elif attack == "fifo":\\n          \'\n    \'  target.unl'
    "ink()\\n            import os\\n\\n            os'\n    '.m"
    'kfifo(target, 0o600)\\n        elif attack == "director\''
    '\n    \'y":\\n            target.unlink()\\n            tar'
    'get.mkdi\'\n    \'r(mode=0o700)\\n        elif attack == "m'
    'ode":\\n          \'\n    \'  target.chmod(0o644)\\n        '
    'elif attack == "integer":\'\n    \'\\n            value["ep'
    'ic"] = True\\n            target.wr\'\n    \'ite_text(json.'
    "dumps(records.seal(value)))\\n        elif '\n    'attack"
    ' == "kind":\\n            value["kind"] = "CALLER_E\'\n   '
    ' \'NVIRONMENT"\\n            target.write_text(json.dumps'
    '(re\'\n    \'cords.seal(value)))\\n        elif attack == "'
    'schema":\\n  \'\n    \'          value["schema_version"] = '
    '"2.0"\\n            t\'\n    \'arget.write_text(json.dumps('
    'records.seal(value)))\\n     \'\n    \'   elif attack == "m'
    'issing-field":\\n            del valu\'\n    \'e["launcher_'
    'executable_sha256"]\\n            target.writ\'\n    \'e_te'
    "xt(json.dumps(records.seal(value)))\\n        elif at'\n "
    '   \'tack in {"repository", "launch", "mandate"}:\\n     '
    '     \'\n    \'  key = {\\n                "repository": "r'
    'epository",\\n \'\n    \'               "launch": "launch_d'
    'igest",\\n             \'\n    \'   "mandate": "mandate_dig'
    'est",\\n            }[attack]\\n \'\n    \'           value['
    'key] = (\\n                "synthetic/ot\'\n    \'her"\\n   '
    '             if attack == "repository"\\n        \'\n    \''
    '        else "sha256:" + "0" * 64\\n            )\\n     '
    "  '\n    '     target.write_text(json.dumps(records.seal"
    '(value)))\'\n    \'\\n        elif attack == "digest":\\n   '
    '         value["re\'\n    \'cord_digest"] = "0" * 64\\n    '
    "        target.write_text('\n    'json.dumps(value))\\n  "
    '      elif attack == "duplicate":\\n\'\n    \'            t'
    "arget.write_text(\\n                saved.de'\n    'code("
    ')[:-1] + \\\',"kind":"FINAL_AGGREGATE_ENVIRONMENT"}\\\'\\n\'\n'
    '    \'            )\\n        elif attack == "oversize":\\'
    "n      '\n    '      target.write_bytes(\\n              "
    '  b" " * (count\'\n    \'er.store.owner["max_artifact_byte'
    's"] + 1)\\n            )\'\n    \'\\n        else:\\n        '
    '    key = {\\n                "ver\'\n    \'sion": "launche'
    'r_python_version",\\n                "hash\'\n    \'": "lau'
    'ncher_executable_sha256",\\n                "ident\'\n    '
    '\'ity": "preparation_identity_digest",\\n                '
    '"i\'\n    \'nput": "input_digest",\\n                "cache'
    '": "review\'\n    \'_source_digest",\\n                "ext'
    'ra": "unexpected",\'\n    \'\\n            }[attack]\\n     '
    '       value[key] = (\\n     \'\n    \'           "bad" if '
    'attack in ("version", "hash", "extr\'\n    \'a") else "0" '
    "* 64\\n            )\\n            target.writ'\n    'e_te"
    "xt(json.dumps(records.seal(value)))\\n        # Both '\n "
    "   'fixed admission and owned reconstruction must fail "
    "clos'\n    'ed.\\n        for read in (\\n            lamb"
    "da: guards.va'\n    'lidate_final_preseed(seed, root),\\n"
    "            lambda: r'\n    'untime.read_completed_envir"
    "onment_owned(session),\\n     '\n    '       lambda: runt"
    "ime.read_completed_aggregate_owned(s'\n    'ession),\\n  "
    "      ):\\n            try:\\n                re'\n    'ad"
    '()\\n            except Exception as error:\\n           '
    "'\n    '     assert type(error).__name__ in {\\n         "
    '        \'\n    \'   "DeploymentError",\\n                 '
    '   "PhaseBlocked\'\n    \'",\\n                    "LoopErr'
    'or",\\n                   \'\n    \' "ContinuationError",\\n'
    "                }, (attack, type('\n    'error).__name__"
    ")\\n            else:\\n                rais'\n    'e Asse"
    'rtionError("accepted invalid original:" + attack)\'\n    '
    "'\\n        assert reservation.read_bytes() == before\\n "
    "   '\n    'finally:\\n        if target.is_symlink():\\n  "
    "          ta'\n    'rget.unlink()\\n        if alias.exis"
    "ts():\\n            al'\n    'ias.unlink()\\n        if ta"
    "rget.is_dir():\\n            ta'\n    'rget.rmdir()\\n    "
    "    elif target.exists():\\n            '\n    'target.un"
    "link()\\n        target.write_bytes(saved)\\n     '\n    '"
    '   target.chmod(0o600)\\nassert runtime.read_completed_e'
    "n'\n    'vironment_owned(session) == original\\nassert gu"
    'ards.vali\'\n    \'date_final_preseed(seed, root)["calls_c'
    'onsumed"] == 1\\n#\'\n    \' The real native command has al'
    "ready run exactly once.\\n'\n    'assert len(native_start"
    "ed) == 1\\nassert len(publication_'\n    'seen) == 1\\ndea"
    'dline = json.loads(before)["deadline_at"]\'\n    \'\\ntime.'
    "sleep(max(0, deadline - time.time()) + 0.05)\\nasse'\n   "
    " 'rt time.time() > deadline\\nassert runtime.read_comple"
    "ted'\n    '_environment_owned(session) == original\\nasse"
    "rt runtime.'\n    'read_completed_aggregate_owned(sessio"
    "n) == result\\nasser'\n    't reservation.read_bytes() =="
    " before\\n'\n)\n\nCOMPLETED_READ_ONLY = (\n    '# These are "
    "only effect-boundary spies AFTER genuine na'\n    'tive "
    "completion, never\\n# original source readers or own'\n  "
    "  'ership/authority guards.\\ndef forbidden(*args,**kwar"
    'gs):\'\n    "\\n    raise AssertionError(\'completed-only r'
    'eader attemp"\n    "ted write/factory/gate/native\')\\nset'
    'tings_backend.Settin"\n    \'gsAdapter.__init__=forbidden'
    "\\nsettings_backend.SettingsS'\n    'tore.__init__=forbid"
    "den\\nruntime.codex_role_adapter=forb'\n    'idden\\nStore"
    ".write=forbidden\\nsource_store.Store.save=for'\n    'bid"
    "den\\nruntime.RoleCounter.reserve=forbidden\\nagent_gate'"
    "\n    '.run_profile=forbidden\\nruntime._ReservedDelegate"
    '._call=\'\n    "forbidden\\ncanonical=[seed/\'final-prepara'
    'tion/role-state"\n    ".json\']\\ncanonical+=list((seed/\'f'
    'inal-preparation\').glob"\n    "(\'reservation-*.json\'))\\n'
    'canonical+=list((seed/\'final-pr"\n    "eparation/sources'
    '\').glob(\'*.json\'))\\ncanonical+=list((se"\n    "ed/\'final'
    '-preparation/role-settings\').glob(\'*.json\'))\\no"\n    \'r'
    'iginal={path:path.read_bytes() for path in canonical}\\n'
    "'\n    'first=runtime.read_completed_aggregate_owned(ses"
    'sion)\\na\'\n    "ssert first==result\\nreservation_path=ne'
    'xt((seed/\'final-"\n    "preparation\').glob(\'reservation-'
    '*.json\'))\\nreservation=g"\n    \'uards.private_json(reser'
    'vation_path)\\nassert reservation\'\n    "[\'deadline_at\']-'
    'time.time()<16\\ntime.sleep(max(0,reserva"\n    "tion[\'de'
    'adline_at\']-time.time())+0.05)\\nassert time.time"\n    "'
    "()>reservation['deadline_at']\\nassert runtime.read_comp"
    'l"\n    \'eted_aggregate_owned(session)==result\\nassert {'
    "path:path'\n    '.read_bytes() for path in original}==or"
    'iginal\\nfor attac\'\n    "k in (\\n    \'zero\',\'reservation'
    '\',\'unknown\',\'cache\',\'sett"\n    "ings\',\'cursor\',\'checkpo'
    'int\',\\n):\\n    target=(seed/\'final"\n    "-preparation/r'
    'ole-state.json\' if attack==\'zero\' else\\n  "\n    "      '
    '    seed/\'epic-loop-checkpoint.json\' if attack==\'"\n    '
    '"checkpoint\' else\\n            next((seed/\'final-prepar'
    'at"\n    "ion\').glob(\'reservation-*.json\'))\\n           '
    ' if attack"\n    " in {\'reservation\',\'unknown\'} else\\n  '
    '          next((se"\n    "ed/\'final-preparation/sources\''
    ').glob(\'*.json\'))\\n       "\n    "     if attack==\'cache'
    '\' else\\n            next((seed/\'fi"\n    "nal-preparatio'
    'n/role-settings\').glob(\'*.json\'))\\n       "\n    "     i'
    'f attack==\'settings\' else seed/\'session-control/c"\n    '
    '"ursor.json\')\\n    saved=target.read_bytes()\\n    try:\\'
    'n   "\n    "     if attack in {\'cache\',\'settings\'}:\\n   '
    '         tar"\n    \'get.unlink()\\n        else:\\n       '
    '     value=json.loads\'\n    "(saved)\\n            if att'
    'ack==\'cursor\': value[\'mode\']="\n    "\'DONE\'\\n           '
    ' elif attack==\'checkpoint\': value[\'st"\n    "atus\']=\'BLO'
    'CKED\'\\n            elif attack==\'zero\':\\n     "\n    \'  '
    "         from tools.agent_final_preparation_records i'\n"
    '    "mport seal\\n                value=seal({**value,\'c'
    'alls_c"\n    "onsumed\':0,\'pending_reservation_digest\':No'
    'ne})\\n        "\n    \'    else:\\n                from to'
    'ols.agent_final_prepar\'\n    "ation_records import seal\\'
    'n                status=\'UNKNO"\n    "WN\' if attack==\'un'
    'known\' else \'RESERVED\'\\n              "\n    "  value=se'
    'al({**value,\'status\':status,\\n                "\n    "  '
    '          \'settings_digest\':None,\'output_digest\':None"\n'
    "    '})\\n            target.write_text(json.dumps(value"
    "))\\n   '\n    '     try: runtime.read_completed_aggregat"
    "e_owned(sessio'\n    'n)\\n        except Exception as er"
    'ror:\\n            asser\'\n    "t type(error).__name__ in'
    ' {\\n                \'Deployment"\n    "Error\',\'PhaseBloc'
    'ked\',\'LoopError\',\\n            }, (atta"\n    \'ck,type(e'
    'rror).__name__,str(error))\\n        else: raise\'\n    " '
    "AssertionError('invalid source accepted:'+attack)\\n    "
    '"\n    \'finally:\\n        target.write_bytes(saved);targ'
    "et.chmod'\n    '(0o600)\\n    assert {path:path.read_byte"
    "s() for path in '\n    'original}==original\\nassert runt"
    "ime.read_completed_aggre'\n    'gate_owned(session)==res"
    "ult\\n'\n)\n\nCOMPLETED_PROJECTION = (\n    'def forbidden(*"
    'args, **kwargs):\\n    raise AssertionErro\'\n    "r(\'read'
    'er attempted production/gate/model effect\')\\nrunt"\n    '
    "'ime.RoleCounter.reserve = forbidden\\nruntime._Reserved"
    "De'\n    'legate._call = forbidden\\nagent_gate.run_profi"
    "le = forbi'\n    'dden\\nstorage.Store.write = forbidden\\"
    "nnative_store.Store'\n    '.save = forbidden\\noriginals "
    '= {p: p.read_bytes() for p \'\n    "in (seed/\'final-prepa'
    'ration\').rglob(\'*\')\\n             i"\n    \'f p.is_file()'
    "}\\na = runtime.read_completed_aggregate_own'\n    'ed(se"
    "ssion)\\ne = runtime.read_completed_environment_owne'\n  "
    "  'd(session)\\nassert a == result\\nactual = runtime.rea"
    'd_cur\'\n    "rent_final_source_digests_owned(session)\\ng'
    'ate = a[\'requ"\n    "est\'][\'gate_evidence\']\\nassert actu'
    'al == expected_projec"\n    \'tion\\nassert {p: p.read_byt'
    "es() for p in originals} == o'\n    'riginals\\n# Delete "
    "the real environment original: it mus'\n    't never bec"
    'ome a default.\\nenvironment_path = next(\\n    \'\n    "(s'
    'eed/\'final-preparation/sources\').glob(\'environment-*."\n'
    '    "json\'))\\nsaved = environment_path.read_bytes()\\ntr'
    'y:\\n    "\n    \'environment_path.unlink()\\n    try: runt'
    "ime.read_current'\n    '_final_source_digests_owned(sess"
    'ion)\\n    except Excepti\'\n    "on as error:\\n        as'
    'sert type(error).__name__ in {\'D"\n    "eploymentError\','
    ' \'PhaseBlocked\'}\\n    else: raise Assert"\n    "ionError'
    '(\'absent original environment admitted\')\\nfinall"\n    \''
    'y:\\n    environment_path.write_bytes(saved)\\n    enviro'
    "nm'\n    'ent_path.chmod(0o600)\\nassert {p: p.read_bytes"
    "() for p i'\n    'n originals} == originals\\n\\n# A legac"
    "y request shape can'\n    'not stand in for the real sch"
    'ema8 producer.\\nreview_path\'\n    " = next((seed / \'fina'
    'l-preparation/sources\').glob(\'revi"\n    "ew-*.json\'))\\n'
    'review_saved = review_path.read_bytes()\\nre"\n    \'view_'
    "mode = review_path.stat().st_mode & 0o777\\ntry:\\n   '\n "
    '   " legacy = json.loads(review_saved)\\n    assert \'lea'
    'rning"\n    "_source_digest\' in legacy[\'request\']\\n    d'
    'el legacy[\'re"\n    "quest\'][\'learning_source_digest\']\\n'
    '    review_path.write"\n    \'_text(json.dumps(legacy))\\n'
    "    try:\\n        runtime.read'\n    '_completed_aggrega"
    'te_owned(session)\\n    except Exceptio\'\n    "n as error'
    ':\\n        assert type(error).__name__ in {\'De"\n    "pl'
    "oymentError', 'PhaseBlocked'}\\n    else:\\n        raise"
    '"\n    " AssertionError(\'legacy request admitted as sche'
    'ma8 pro"\n    "ducer\')\\nfinally:\\n    review_path.write_'
    'bytes(review_sav"\n    \'ed)\\n    review_path.chmod(revie'
    "w_mode)\\nassert {p: p.rea'\n    'd_bytes() for p in orig"
    "inals} == originals\\n'\n)\n\nfrom tools import agent_final"
    '_preparation_runtime as runtime\nfrom tools import agent'
    '_final_preparation_driver as preparation_driver\nfrom to'
    'ols import agent_final_preparation_storage as storage\nf'
    'rom tools import agent_continuation_store as native_sto'
    're\n'
    'from tools import agent_continuation_store as source_st'
    'ore\nfrom tools.agent_final_preparation_storage import S'
    'tore\n'
    'from tools import agent_continuation_backend as sett'
    'ings_backend\nfrom tools import agent_epic_runtime_cli a'
    's guards\nfrom tools.agent_epic_loop import PhaseBlocked'
    ', RunnerLock\nfrom tools.agent_epic_wait_scope import Sc'
    'ope\nfrom tools.agent_final_preparation_runtime import ('
    '\n    RoleCounter, _LearningAggregateReview, _OriginalSo'
    'urces, _PreparationStore,\n    _completed_learning_revie'
    'w, _configured_aggregate,\n)\nfrom typing import Any\nfrom'
    ' tools import agent_final_preparation_review as review_'
    'module\nseed = state\n\ndef review_learning_after_preparat'
    'ion(\n    session: Any, *, started_at: int, deadline_at:'
    ' int,\n) -> dict[str, Any]:\n    """Collect before factor'
    'ies; close continuous zero reader before reserve."""\n  '
    '  from tools.agent_epic_session_runtime import SessionR'
    'untime\n    from tools.agent_final_learning_collector im'
    'port _collect\n    from tools.agent_final_learning_domai'
    'n import _PreAggregatePlanningReader\n\n    if type(sessi'
    'on) is not SessionRuntime:\n        raise PhaseBlocked("'
    'SESSION_SCOPE_CHANGED")\n    if session.config.get("sche'
    'ma_version") != "8.0":\n        raise PhaseBlocked("SESS'
    'ION_SCOPE_CHANGED")\n    scope = session.wait_scope\n    '
    'if type(scope) is not Scope or scope.active_stage != "F'
    'INAL:FINAL_GATE":\n        raise PhaseBlocked("WAIT_FRAM'
    'E_REQUIRED")\n    seed, root = session.state, session.ro'
    'ot\n    seed = guards.secure_directory(seed, root)\n    s'
    'upervisor = RunnerLock.current_owned(seed / "session-co'
    'ntrol")\n    with RunnerLock(seed) as ownership:\n       '
    ' guards.validate_final_preseed(seed, root)\n        sour'
    'ces = _OriginalSources(seed, root, supervisor, ownershi'
    'p, scope)\n        if session.config != sources.config:\n'
    '            raise PhaseBlocked("SESSION_SCOPE_CHANGED")'
    '\n        from tools.agent_final_preparation_sources imp'
    'ort _aggregate_capture_segment\n\n        with _aggregate'
    '_capture_segment(session):\n            historical = _Pr'
    'eAggregatePlanningReader(session)\n            try:\n    '
    '            packet = _collect(historical, session)\n    '
    '            store = _PreparationStore(seed, root, sourc'
    'es.profile, sources.check)\n                counter = Ro'
    'leCounter(store, sources.config["approved_launch_digest'
    '"])\n                state, _ = counter.read()\n         '
    '       if state is not None and state["calls_consumed"]'
    ':\n                    raise PhaseBlocked("FINAL_ROLE_CA'
    'LL_SPENT")\n                original = _configured_aggre'
    'gate(\n                    seed, root, scope, sources, s'
    'tore, counter\n                )\n                aggrega'
    'te = _LearningAggregateReview(counter, original.adapter'
    ')\n                # Both objects share original counter'
    '/adapter; no Settings factory,\n                # source'
    ' constructor or transport is repeated here.\n           '
    '     aggregate.adapter.delegate.aggregate = aggregate\n '
    '               aggregate._learning_source_digest = pack'
    'et["source_digest"]\n                identity = aggregat'
    'e._identity()\n                prepared = aggregate._pre'
    'pare_new_review(identity)\n                historical._g'
    'uard()\n                sources.check()\n            fina'
    'lly:\n                historical.reader.close()\n        '
    '    # No preaggregate guard after this boundary: reserv'
    'e changes zero state.\n            # Test-owned initial '
    'role reservation starts only after real prep.\n         '
    '   started_at = int(time.time())\n            mandate = '
    'guards.private_json(seed / "mandate.json")\n            '
    'deadline_at = int(min(\n                started_at + 15,'
    '\n                scope.frame["deadline_at"],\n          '
    '      scope._active_reader().current_deadline,\n        '
    '        mandate["expires_at"],\n                session.'
    'config["started_at"]\n                + mandate["limits"'
    ']["max_duration_seconds"],\n            ))\n            i'
    'f not started_at < deadline_at:\n                raise P'
    'haseBlocked("WAIT_INVOCATION_EXPIRED")\n            sour'
    'ce = aggregate._dispatch_review(\n                identi'
    'ty, prepared, started_at=started_at, deadline_at=deadli'
    'ne_at\n            )\n            authenticated, fresh = '
    '_completed_learning_review(session)\n            if auth'
    'enticated != source or fresh != packet:\n               '
    ' raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")\n      '
    '      sources.check()\n            return source\n\nclass '
    'CompletedReaderStop(BaseException):\n    pass\n\n_complete'
    'd_receipts = {}\n_completed_poison = False\n_completed_ro'
    'ws = None\n_completed_result = None\n_completed_environme'
    'nt = None\n_completed_dispatch_active = False\nnative_sta'
    'rted = []\npublication_seen = []\n_completed_original_pre'
    'flight = runtime.structural_child_preflight\n_completed_'
    'original_review = runtime.review_learning_owned\n_comple'
    'ted_original_constructor = preparation_driver.CurrentPr'
    'eparation\n_completed_original_write = storage.Store.wri'
    'te\n_completed_original_delegate = runtime._ReservedDele'
    'gate._call\n\n\ndef completed_preflight(session):\n    glob'
    'al _completed_rows\n    value = _completed_original_pref'
    'light(session)\n    assert _completed_rows is None\n    a'
    "ssert [row['task'] for row in value] == [301, 303]\n    "
    "for row in value:\n        original = api.pulls[row['tas"
    "k']]\n        assert row['delivery']['pr'] == original.n"
    "umber\n        assert row['delivery']['head_sha'] == ori"
    "ginal.head_sha\n        assert row['delivery']['merge_sh"
    "a'] == original.merge_sha\n    _completed_rows = json.lo"
    'ads(json.dumps(value))\n    return value\n\n\ndef completed'
    '_write(self, key, value):\n    global _completed_environ'
    'ment, _completed_poison\n    observer_error = None\n    t'
    "ry:\n        if key.startswith('reservation-') and value"
    ".get('status') == 'COMPLETED':\n            from tools i"
    'mport agent_final_preparation_records as records\n      '
    "      counter = runtime.RoleCounter(self, value['launch"
    "_digest'])\n            sources = native_store.Store(sel"
    "f.directory / 'sources', self.root)\n            environ"
    'ment = records.aggregate_environment(sources.load(\n    '
    "            'environment-' + runtime.digest(counter.ide"
    "ntity),\n                cap=self.owner['max_artifact_by"
    "tes'],\n            ))\n            source = sources.load"
    "(\n                'review-' + runtime.digest(counter.id"
    "entity),\n                cap=self.owner['max_artifact_b"
    "ytes'],\n            )\n            assert set(source) =="
    " {\n                'identity', 'request', 'policy', 'se"
    "ssion_id', 'output',\n                'settings_source',"
    "\n            }\n            assert environment['review_s"
    "ource_digest'] == runtime.digest(source)\n            as"
    "sert environment['launcher_executable_sha256'] == hashl"
    'ib.sha256(\n                Path(sys.executable).resolve'
    '(strict=True).read_bytes()\n            ).hexdigest()\n  '
    "          assert environment['input_digest'] == value['"
    "input_digest']\n            assert environment['preparat"
    "ion_identity_digest'] == (\n                value['prepa"
    "ration_identity_digest']\n            )\n            asse"
    'rt _completed_environment is None\n            _complete'
    'd_environment = json.loads(json.dumps(environment))\n   '
    "         publication_seen.append(value['record_digest']"
    ')\n    except BaseException as error:\n        _completed'
    '_poison = True\n        observer_error = error\n    # Alw'
    'ays forward the original writer once; its own failure s'
    'tays primary.\n    result = _completed_original_write(se'
    'lf, key, value)\n    if observer_error is not None:\n    '
    '    raise observer_error\n    return result\n\n\ndef comple'
    'ted_native_audit(event, args):\n    if _completed_dispat'
    "ch_active and event == 'subprocess.Popen':\n        argv"
    ' = args[1]\n        if isinstance(argv, (list, tuple)) a'
    "nd '--role' in argv:\n            assert 'controller' in"
    ' argv\n            native_started.append(time.monotonic('
    '))\n\n\nsys.addaudithook(completed_native_audit)\n\n\ndef com'
    'pleted_delegate(self, command, payload):\n    global _co'
    'mpleted_dispatch_active\n    assert not _completed_dispa'
    'tch_active\n    _completed_dispatch_active = True\n    tr'
    'y:\n        return _completed_original_delegate(self, co'
    'mmand, payload)\n    finally:\n        _completed_dispatc'
    'h_active = False\n\n\ndef completed_review(session, *, sta'
    'rted_at, deadline_at):\n    global _completed_result\n   '
    ' assert _completed_result is None\n    value = review_le'
    'arning_after_preparation(\n        session, started_at=s'
    'tarted_at, deadline_at=deadline_at,\n    )\n    _complete'
    'd_result = json.loads(json.dumps(value))\n    return val'
    "ue\n\n\ndef completed_fingerprint():\n    paths = [seed / '"
    "epic-loop-checkpoint.json',\n             seed / 'sessio"
    "n-control/cursor.json']\n    paths += [p for p in (seed "
    "/ 'final-preparation').rglob('*') if p.is_file()]\n    r"
    'eturn {str(p): (p.read_bytes(), p.stat().st_mode) for p'
    ' in paths}\n\n\ndef completed_group(name, code, namespace)'
    ':\n    global _completed_poison\n    targets = [\n        '
    "(settings_backend.SettingsAdapter, '__init__'),\n       "
    " (settings_backend.SettingsStore, '__init__'),\n        "
    "(runtime, 'codex_role_adapter'),\n        (runtime.RoleC"
    "ounter, 'reserve'),\n        (runtime._ReservedDelegate,"
    " '_call'),\n        (agent_gate, 'run_profile'),\n       "
    " (storage.Store, 'write'),\n        (native_store.Store,"
    " 'save'),\n        (review_module.platform, 'python_vers"
    "ion'),\n    ]\n    saved = [(obj, attr, getattr(obj, attr"
    ')) for obj, attr in targets]\n    before = completed_fin'
    'gerprint()\n    primary = None\n    try:\n        assert n'
    "ot _completed_poison\n        exec(compile(code, '<compl"
    "eted-reader-' + name + '>', 'exec'), namespace)\n    exc"
    'ept BaseException as error:\n        primary = error\n   '
    '     raise\n    finally:\n        secondary = None\n      '
    '  for obj, attr, value in saved:\n            try:\n     '
    '           setattr(obj, attr, value)\n            except'
    ' BaseException as error:\n                _completed_poi'
    'son = True\n                if secondary is None:\n      '
    '              secondary = error\n        try:\n          '
    '  assert completed_fingerprint() == before\n        exce'
    'pt BaseException as error:\n            _completed_poiso'
    'n = True\n            if secondary is None:\n            '
    '    secondary = error\n        if secondary is not None '
    'and primary is None:\n            raise secondary\n    as'
    'sert not _completed_poison\n    _completed_receipts[name'
    "] = 'PASS'\n\n\ndef completed_probe(actual_session):\n    a"
    'ssert actual_session is session\n    scope = actual_sess'
    "ion.wait_scope\n    assert scope.active_stage == 'FINAL:"
    "FINAL_GATE'\n    RunnerLock.current_owned(seed / 'sessio"
    "n-control')\n    RunnerLock.current_owned(seed)\n    asse"
    'rt _completed_result is not None\n    assert _completed_'
    'environment is not None\n    assert _completed_rows is n'
    'ot None\n    assert len(native_started) == 1\n    assert '
    'len(publication_seen) == 1\n    # No observer replaces t'
    'he tested original reader or its source guards.\n    run'
    'time.structural_child_preflight = _completed_original_p'
    'reflight\n    runtime.review_learning_owned = _completed'
    '_original_review\n    storage.Store.write = _completed_o'
    'riginal_write\n    runtime._ReservedDelegate._call = _co'
    'mpleted_original_delegate\n    from tools import agent_f'
    'inal_preparation_review as review_module\n    gate = _co'
    "mpleted_result['request']['gate_evidence']\n    expected"
    "_projection = {\n        'snapshot_digest': runtime.dige"
    "st(gate['snapshot']),\n        'local_gate_digest': runt"
    "ime.digest({\n            'gate_evidence': gate, 'enviro"
    "nment': _completed_environment,\n        }),\n        're"
    "view_digest': runtime.digest(_completed_result),\n      "
    "  'children_digest': runtime.digest({\n            'repo"
    "sitory': scope.mandate['repository'],\n            'epic"
    "': scope.mandate['epic'], 'children': [301, 303],\n     "
    "   }),\n        'task_delivery_digest': runtime.digest(_"
    'completed_rows),\n    }\n    namespace = dict(globals())\n'
    '    namespace.update(\n        session=actual_session, s'
    'cope=scope, result=_completed_result,\n        expected_'
    'projection=expected_projection, review_module=review_mo'
    "dule,\n    )\n    for name, code in (\n        ('projectio"
    "n', COMPLETED_PROJECTION),\n        ('read_only', COMPLE"
    "TED_READ_ONLY),\n        ('environment', COMPLETED_ENVIR"
    'ONMENT),\n    ):\n        completed_group(name, code, nam'
    'espace)\n    assert not _completed_poison\n    raise Comp'
    'letedReaderStop()\n\n\nruntime.structural_child_preflight '
    '= completed_preflight\nruntime.review_learning_owned = c'
    'ompleted_review\nstorage.Store.write = completed_write\nr'
    'untime._ReservedDelegate._call = completed_delegate\npre'
    'paration_driver.CurrentPreparation = completed_probe\n'
)

RESTORE_AND_NEGATIVES = (
    'finally:\n    preparation_driver.CurrentPreparation = _c'
    'ompleted_original_constructor\n    runtime.structural_ch'
    'ild_preflight = _completed_original_preflight\n    runti'
    'me.review_learning_owned = _completed_original_review\n '
    '   storage.Store.write = _completed_original_write\n    '
    'runtime._ReservedDelegate._call = _completed_original_d'
    "elegate\nassert _completed_receipts == {\n    'projection"
    "': 'PASS', 'read_only': 'PASS', 'environment': 'PASS',\n"
    '}\nassert not _completed_poison\nscope = session.wait_sco'
    'pe\nseed = state\nresult = _completed_result\ntry:\n    run'
    'time.read_completed_aggregate_owned(session)\nexcept Exc'
    "eption as error:\n    assert type(error).__name__ in {'P"
    "haseBlocked', 'LoopError'}\nelse:\n    raise AssertionErr"
    "or('missing original ownership accepted')\n_completed_re"
    "ceipts['missing_ownership'] = 'PASS'\nwith RunnerLock.su"
    "pervisor(seed / 'session-control'), RunnerLock(seed):\n "
    "   try:\n        with session._stage('FINAL:PR_CI'):\n   "
    '         runtime.read_current_final_source_digests_owne'
    'd(session)\n    except Exception as error:\n        asser'
    "t type(error).__name__ in {'PhaseBlocked', 'LoopError'}"
    "\n    else:\n        raise AssertionError('outside-captur"
    "e PR_CI accepted')\n_completed_receipts['outside_pr_ci']"
    " = 'PASS'\nprint(json.dumps(_completed_receipts, sort_ke"
    'ys=True))\n'
)
