"""UNAPPLIED INITIAL lifecycle fixture. No execution evidence or READY claim."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_epic_entry as entry
from tools import agent_epic_install as install
from tools import agent_epic_runtime_cli as guards
from tools import agent_orchestrate as workflow
from tools.agent_learning_planning_catalog import catalog_digest
from tools.agent_orchestrate import StateStore

from tests.learning_session_native_fixture import NATIVE
from tests.learning_session_primary_fixture import Primary
from tests.test_agent_continuation import owner
from tests.test_agent_continuation_backend import document
from tests.test_agent_coordinator_handover import _value
from tests.test_agent_epic_handover import _mandate
from tests.test_agent_learning import profile
from tests.test_agent_learning_integration import roles
from tests.test_agent_learning_planning_catalog import sample
from tests.test_agent_learning_workflow_binding import initial_config8

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX original sources")

CONFTEST = (
    'import sys\nfrom pathlib import Path\n\nsys.path.inse'
    'rt(0, str(Path(__file__).resolve().parents[1] / "s'
    'rc"))\n'
)
SMOKE = (
    'import hashlib\nfrom dataclasses import FrozenInsta'
    'nceError\nfrom pathlib import Path\n\nimport pytest\n\n'
    '\ndef test_actual_snapshot_binding_and_frozen_confi'
    'guration() -> None:\n    import privacy_gateway\n   '
    ' from privacy_gateway import GatewayConfig, facade'
    '\n\n    source = Path(__file__).resolve().parents[1]'
    ' / "src"\n    assert Path(privacy_gateway.__file__)'
    '.resolve().is_relative_to(source)\n    assert Path('
    'facade.__file__).resolve().is_relative_to(source)\n'
    '    assert privacy_gateway.__version__ == "0.5.0"\n'
    '    assert hashlib.sha256(\n        Path(privacy_ga'
    'teway.__file__).read_bytes()\n    ).hexdigest() == '
    "''.join((\n        'bc892aa4', 'df8ef310', '1aa5852"
    "6', '5cdd60c8',\n        'c79ae8c0', '42429a08', '1"
    "ec0b908', 'bb70f2ac',\n    ))\n    assert GatewayCon"
    'fig is facade.GatewayConfig\n    assert "GatewayCon'
    'fig" in privacy_gateway.__all__\n    value = Gatewa'
    'yConfig()\n    assert value.strict is True\n    with'
    ' pytest.raises(FrozenInstanceError):\n        setat'
    'tr(value, "strict", False)\n'
)
APP_SERVER = (
    '#!PYTHON\nimport json,sys\nimport os\nfrom pa'
    "thlib import Path\nif '--version' in sys.ar"
    "gv:\n    print('codex-cli 0.157.1'); raise "
    'SystemExit(0)\n_COUNTER_RECORD = b\'{"role":'
    ' "controller"}\\n\'\n\n\ndef _counter_directory'
    '(path: Path) -> int:\n    import stat as co'
    'unter_stat\n\n    if (not path.is_absolute()'
    ' or len(str(path)) > 4096\n            or l'
    'en(path.parts) > 64 or any(p in (".", ".."'
    ') for p in path.parts)):\n        raise Run'
    'timeError("SYNTHETIC_COUNTER_DIRECTORY_STO'
    'P")\n    flags = os.O_RDONLY | os.O_DIRECTO'
    'RY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_'
    'CLOEXEC\n    descriptor = os.open("/", flag'
    's)\n    try:\n        for component in path.'
    'parts[1:]:\n            following = os.open'
    '(component, flags, dir_fd=descriptor)\n    '
    '        os.close(descriptor)\n            d'
    'escriptor = following\n        info = os.fs'
    'tat(descriptor)\n        if (not counter_st'
    'at.S_ISDIR(info.st_mode)\n                o'
    'r info.st_uid != getattr(os, "getuid")()\n '
    '               or counter_stat.S_IMODE(inf'
    'o.st_mode) != 0o700):\n            raise Ru'
    'ntimeError("SYNTHETIC_COUNTER_DIRECTORY_ST'
    'OP")\n        result = descriptor\n        d'
    'escriptor = -1\n        return result\n    f'
    'inally:\n        if descriptor != -1:\n     '
    '       os.close(descriptor)\n\n\ndef _counter'
    '_write_record(directory: int, data: bytes)'
    ' -> None:\n    if type(data) is not bytes o'
    'r len(data) > 64 or data != _COUNTER_RECOR'
    'D:\n        raise RuntimeError("SYNTHETIC_C'
    'OUNTER_RECORD_STOP")\n    flags = os.O_WRON'
    'LY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLO'
    'W\n    flags |= os.O_NONBLOCK | os.O_CLOEXE'
    'C\n    descriptor = os.open("aggregate-tran'
    'sport-count.jsonl", flags, 0o600,\n        '
    '                 dir_fd=directory)\n    try'
    ':\n        if os.write(descriptor, data) !='
    ' len(data):\n            raise RuntimeError'
    '("SYNTHETIC_COUNTER_WRITE_STOP")\n    final'
    'ly:\n        os.close(descriptor)\n\n\ndef _wr'
    'ite_synthetic_counter_in_existing_codex_ho'
    'me() -> None:\n    directory = _counter_dir'
    'ectory(Path(os.environ["CODEX_HOME"]))\n   '
    ' try:\n        _counter_write_record(direct'
    'ory, _COUNTER_RECORD)\n    finally:\n       '
    ' os.close(directory)\n\n\ndef read(method):\n '
    '   value=json.loads(sys.stdin.readline());'
    " assert value['method']==method; return va"
    'lue\ndef emit(value):\n    print(json.dumps('
    "value),flush=True)\nread('initialize'); emi"
    "t({'id':1,'result':{}}); read('initialized"
    "')\ns=read('thread/start')\nassert s['params"
    "']['ephemeral'] is True\nassert s['params']"
    "['approvalPolicy']=='never'\nassert s['para"
    "ms']['config']['project_doc_max_bytes']==0"
    "\nemit({'id':2,'result':{'thread':{'id':'sy"
    "nthetic-thread'},'approvalPolicy':'never',"
    "\n'cwd':s['params']['cwd'],'instructionSour"
    "ces':[],'model':'synthetic-model',\n'reason"
    "ingEffort':'low','sandbox':{'type':'readOn"
    "ly'}}})\nread('turn/start')\n_write_syntheti"
    "c_counter_in_existing_codex_home()\nemit({'"
    "id':3,'result':{'turn':{'id':'synthetic-tu"
    "rn'}}})\nverdict={'verdict':'PASS','repair_"
    "iteration':0,'escalation_reason':None,\n'bl"
    "ocking_findings':[],'notes':[]}\nemit({'met"
    "hod':'item/completed','params':{'threadId'"
    ":'synthetic-thread',\n 'turnId':'synthetic-"
    "turn','item':{'type':'agentMessage','text'"
    ":json.dumps(verdict)}}})\nemit({'method':'t"
    "urn/completed','params':{'threadId':'synth"
    "etic-thread',\n 'turn':{'id':'synthetic-tur"
    "n','status':'completed'}}})\n"
)

def _appserver_bytes() -> bytes:
    return APP_SERVER.replace("#!PYTHON", "#!" + sys.executable, 1).encode()


def _tree_map(base: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base).as_posix()
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            result[relative] = {"kind": "link", "target": os.readlink(path)}
        elif path.is_file():
            result[relative] = {
                "kind": "file", "mode": path.stat().st_mode & 0o777,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        elif path.is_dir():
            result[relative] = {"kind": "directory"}
    return result


def _canonical_bin() -> Path:
    """Use the active developer environment, never an account-specific path."""
    directory = Path(sys.executable).parent
    assert directory.is_absolute()
    for name in ("pytest", "ruff", "mypy", "pre-commit"):
        path = directory / name
        assert path.is_file() and os.access(path, os.X_OK)
    return directory


def _cache_sources() -> tuple[Path, dict[tuple[str, str], str]]:
    """Admit an explicitly provisioned private cache, without Store creation."""
    from pre_commit.store import Store  # type: ignore[import-untyped]

    selected = os.environ.get("PRE_COMMIT_HOME")
    assert selected
    old = Path(selected)
    assert old.is_absolute() and old.resolve(strict=True) == old
    assert str(old) == Store.get_default_directory()
    owner = getattr(os, "geteuid")()
    root = old.lstat()
    assert old.is_dir() and not old.is_symlink()
    assert root.st_uid == owner and root.st_mode & 0o777 == 0o700
    database = old / "db.db"
    info = database.lstat()
    assert database.is_file() and not database.is_symlink()
    assert info.st_uid == owner and info.st_nlink == 1
    expected = {
        ("https://github.com/Yelp/detect-secrets", "v1.5.0"),
        ("https://github.com/pre-commit/pre-commit-hooks", "v6.0.0"),
    }
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    with sqlite3.connect(
        database.as_uri() + "?mode=ro&immutable=1", uri=True,
    ) as db:
        rows = list(db.execute("SELECT repo,ref,path FROM repos"))
    result: dict[tuple[str, str], str] = {}
    for repo, rev in expected:
        matches = [path for raw_repo, raw_rev, path in rows
                   if (raw_repo, raw_rev) == (repo, rev)]
        assert len(matches) == 1 and isinstance(matches[0], str)
        path = Path(matches[0])
        assert path.is_absolute() and path.parent == old
        assert path.resolve(strict=True) == path
        assert path.is_dir() and not path.is_symlink()
        assert path.lstat().st_uid == owner
        result[(repo, rev)] = path.name
    assert len(set(result.values())) == len(expected)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert old.lstat() == root
    return old, result


def _cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, dict[str, Any]]]:
    """Exact private toolchain copy; original cache is read-only input."""
    old, expected = _cache_sources()
    target = tmp_path / "hook-cache"
    target.mkdir(mode=0o700)
    source_maps = {name: _tree_map(old / name) for name in expected.values()}
    db_before = hashlib.sha256((old / "db.db").read_bytes()).hexdigest()
    with sqlite3.connect(f"file:{old / 'db.db'}?mode=ro&immutable=1", uri=True) as db:
        actual = {(repo, rev): path for repo, rev, path in db.execute(
            "SELECT repo,ref,path FROM repos")}
    relocations: dict[str, dict[str, str]] = {}
    for identity, name in expected.items():
        assert actual[identity] == str(old / name)
        shutil.copytree(
            old / name, target / name, symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        assert _tree_map(target / name) == source_maps[name]
        from pre_commit.languages.python import get_default_version  # type: ignore[import-untyped]

        version = get_default_version()
        for version in (version,):
            previous = old / name / ("py_env-" + version)
            current = target / name / ("py_env-" + version)
            assert current.is_dir()
            for path in (current / "bin").iterdir():
                if path.is_symlink() or not path.is_file():
                    continue
                data = path.read_bytes()
                line, separator, rest = data.partition(b"\n")
                if line == ("#!" + str(previous / "bin/python")).encode():
                    shebang = ("#!" + str(current / "bin/python")).encode()
                    path.write_bytes(shebang + separator + rest)
                    relocations[str(path.relative_to(target))] = {
                        "before": hashlib.sha256(data).hexdigest(),
                        "after": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
            cfg = current / "pyvenv.cfg"
            data = cfg.read_bytes()
            lines = []
            for cfg_line in data.decode().splitlines():
                if cfg_line.startswith("command = "):
                    cfg_line = cfg_line.replace(str(previous), str(current))
                lines.append(cfg_line)
            cfg.write_text("\n".join(lines) + "\n")
            if cfg.read_bytes() != data:
                relocations[str(cfg.relative_to(target))] = {
                    "before": hashlib.sha256(data).hexdigest(),
                    "after": hashlib.sha256(cfg.read_bytes()).hexdigest(),
                }
        copied = _tree_map(target / name)
        for key, value in copied.items():
            relative = name + "/" + key
            if relative not in relocations:
                assert value == source_maps[name][key]
        assert set(copied) == set(source_maps[name])
    # Reject unenumerated executable/runtime old-prefix dependencies BEFORE
    # importing pre_commit or invoking any tool. Metadata exceptions never run.
    prefix_exceptions: list[str] = []
    for path in target.rglob("*"):
        if path.is_symlink():
            assert not os.readlink(path).startswith(str(old))
            continue
        if not path.is_file() or str(old).encode() not in path.read_bytes():
            continue
        relative_path = path.relative_to(target)
        metadata = (
            ".git" in relative_path.parts
            or (path.parent.name == "bin" and path.name.startswith("activate"))
            or (path.parent.name.endswith(".dist-info")
                and path.name in {"RECORD", "direct_url.json"})
        )
        assert metadata, f"unmapped runtime prefix: {relative_path}"
        prefix_exceptions.append(str(relative_path))
    with sqlite3.connect(target / "db.db") as db:
        db.execute(
            "CREATE TABLE repos (repo TEXT NOT NULL,ref TEXT NOT NULL,"
            "path TEXT NOT NULL,PRIMARY KEY(repo,ref))"
        )
        db.execute("CREATE TABLE configs (path TEXT NOT NULL,PRIMARY KEY(path))")
        db.executemany("INSERT INTO repos VALUES (?,?,?)", [
            (repo, rev, str(target / name))
            for (repo, rev), name in expected.items()
        ])
        assert set(db.execute("SELECT repo,ref,path FROM repos")) == {
            (repo, rev, str(target / name))
            for (repo, rev), name in expected.items()
        }
    monkeypatch.setenv("PRE_COMMIT_HOME", str(target))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    # Do NOT call all_hooks/Store.clone: those can fall back to network.
    # Only original health predicates and exact state data are inspected.
    script = """import json,sys
from pathlib import Path
from pre_commit.languages.python import health_check, get_default_version
from pre_commit.prefix import Prefix
version=get_default_version()
for raw in sys.argv[1:]:
    repo=Path(raw)
    env=repo/('py_env-'+version)
    v2=env/'.install_state_v2'
    v1=env/'.install_state_v1'
    assert v2.is_file() or json.loads(v1.read_text())=={'additional_dependencies':[]}
    assert health_check(Prefix(str(repo)), version) is None
"""
    run = subprocess.run(
        [sys.executable, "-B", "-c", script,
         *(str(target / name) for name in expected.values())],
        capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert hashlib.sha256((old / "db.db").read_bytes()).hexdigest() == db_before
    assert all(_tree_map(old / name) == value for name, value in source_maps.items())
    (tmp_path / "cache-preparation.json").write_text(json.dumps({
        "source_maps": source_maps, "relocations": relocations,
        "prefix_metadata_exceptions": prefix_exceptions,
        "prepared_map": _tree_map(target), "health_exit": run.returncode,
        "original_db_sha256": db_before,
    }, sort_keys=True))
    return old, {"db.db": {"sha256": db_before}, **{
        name: {"map": value} for name, value in source_maps.items()
    }}

ADAPTER_DIAGNOSTIC = (
    '\n# Explicit aggregate-bound synthetic diag'
    'nostics; original routines retained.\n_orig'
    'inal_diagnostic_adapt = adapt\n_original_di'
    'agnostic_validate = _validate_controller_r'
    "eview\n_DIAGNOSTIC_CODE_ALLOWLIST = (\n    '"
    "ADAPTER_TIMEOUT',\n    'CODEX_NOT_FOUND',\n "
    "   'INVALID_REQUEST',\n    'MALFORMED_OUTPU"
    "T',\n    'OUTPUT_LIMIT',\n    'PROMPT_TOO_LA"
    "RGE',\n    'ROLE_BACKEND_INCOMPLETE',\n    '"
    "ROLE_BACKEND_TIMEOUT',\n    'ROLE_BACKEND_U"
    "NAVAILABLE',\n    'ROLE_COMPLETION_INVALID'"
    ",\n    'ROLE_EFFECTIVE_POLICY_UNVERIFIED',\n"
    "    'ROLE_EVENT_BINDING_CHANGED',\n    'ROL"
    "E_EVENT_OUTPUT_LIMIT',\n    'ROLE_INPUT_LIM"
    "IT',\n    'ROLE_NON_QUOTA_ERROR',\n    'ROLE"
    "_OUTPUT_INVALID',\n    'ROLE_OUTPUT_LIMIT',"
    "\n    'ROLE_PROTOCOL_INVALID',\n    'ROLE_RP"
    "C_FAILED',\n    'ROLE_SANDBOX_UNSUPPORTED',"
    "\n    'ROLE_SERVER_REQUEST_FORBIDDEN',\n    "
    "'SANDBOX_UNAVAILABLE',\n    'SCHEMA_ERROR',"
    "\n    'SCHEMA_VIOLATION',\n    'VERSION_MISM"
    "ATCH',\n    'VERSION_PROBE_FAILED',\n)\n_DIAG"
    'NOSTIC_TARGET = False\n_DIAGNOSTIC_STATE_MA'
    'TCH = False\n_DIAGNOSTIC_STATE = Path(DIAGN'
    'OSTIC_STATE_LITERAL)\n\n\ndef _bounded_diagno'
    'stic_json(path: Path) -> Any:\n    import o'
    's as diagnostic_os\n    import stat as diag'
    'nostic_stat\n\n    from tools.agent_final_pr'
    'eparation_storage import Store as Diagnost'
    'icStore\n\n    cap = 65536\n    nonblocking ='
    ' getattr(diagnostic_os, "O_NONBLOCK", None'
    ')\n    nofollow = getattr(diagnostic_os, "O'
    '_NOFOLLOW", None)\n    if type(nonblocking)'
    ' is not int or type(nofollow) is not int:\n'
    '        raise ValueError("DIAGNOSTIC_OPEN_'
    'UNAVAILABLE")\n    descriptor = diagnostic_'
    'os.open(\n        path, diagnostic_os.O_RDO'
    'NLY | nonblocking | nofollow,\n    )\n    wi'
    'th diagnostic_os.fdopen(descriptor, "rb") '
    'as stream:\n        info = diagnostic_os.fs'
    'tat(stream.fileno())\n        if (\n        '
    '    not diagnostic_stat.S_ISREG(info.st_mo'
    'de)\n            or info.st_nlink != 1\n    '
    '        or info.st_uid != getattr(diagnost'
    'ic_os, "getuid")()\n            or info.st_'
    'mode & 0o077\n            or info.st_size >'
    ' cap\n        ):\n            raise ValueErr'
    'or("DIAGNOSTIC_SOURCE_UNSAFE")\n        raw'
    ' = stream.read(cap + 1)\n        if len(raw'
    ') > cap:\n            raise ValueError("DIA'
    'GNOSTIC_SOURCE_LIMIT")\n    return json.loa'
    'ds(raw, object_pairs_hook=DiagnosticStore.'
    '_object)\n\n\ndef _aggregate_diagnostic_match'
    '(request: Mapping[str, Any]) -> bool:\n    '
    'from tools import agent_final_preparation_'
    'records as diagnostic_records\n    from too'
    'ls.agent_continuation import digest as dia'
    'gnostic_digest\n\n    if not _DIAGNOSTIC_STA'
    'TE_MATCH:\n        return False\n    directo'
    'ry = _DIAGNOSTIC_STATE / "final-preparatio'
    'n"\n    state_path = directory / "role-stat'
    'e.json"\n    if not state_path.exists():\n  '
    '      return False\n    state = diagnostic_'
    'records.role_state(\n        _bounded_diagn'
    'ostic_json(state_path)\n    )\n    key = sta'
    'te["pending_reservation_digest"]\n    if st'
    'ate["calls_consumed"] != 1 or not isinstan'
    'ce(key, str):\n        return False\n    res'
    'ervation = diagnostic_records.reservation('
    '\n        _bounded_diagnostic_json(director'
    'y / ("reservation-" + key + ".json"))\n    '
    ')\n    return bool(\n        reservation["st'
    'atus"] == "RESERVED"\n        and reservati'
    'on["input_digest"] == diagnostic_digest(di'
    'ct(request))\n        and reservation["repo'
    'sitory"] == state["repository"]\n        an'
    'd reservation["epic"] == state["epic"]\n   '
    '     and reservation["launch_digest"] == s'
    'tate["launch_digest"]\n        and reservat'
    'ion["mandate_digest"] == state["mandate_di'
    'gest"]\n    )\n\n\ndef _observed_diagnostic_va'
    'lidate(request: Mapping[str, Any]) -> None'
    ':\n    global _DIAGNOSTIC_TARGET\n    try:\n '
    '       _DIAGNOSTIC_TARGET = _aggregate_dia'
    'gnostic_match(request)\n    except Exceptio'
    'n:\n        # No evidence on invalid/missin'
    'g binding; parent requires bound receipt.\n'
    '        _DIAGNOSTIC_TARGET = False\n    _or'
    'iginal_diagnostic_validate(request)\n\n\ndef '
    '_adapter_diagnostic_write(record: dict[str'
    ', Any]) -> None:\n    import os as diagnost'
    'ic_os\n\n    data = json.dumps(record, sort_'
    'keys=True, separators=(",", ":")).encode()'
    '\n    if len(data) > 1024:\n        raise Va'
    'lueError("DIAGNOSTIC_RECORD_LIMIT")\n    de'
    'scriptor = diagnostic_os.open(\n        _DI'
    'AGNOSTIC_STATE / "aggregate-adapter-diagno'
    'stic.json",\n        diagnostic_os.O_WRONLY'
    ' | diagnostic_os.O_CREAT | diagnostic_os.O'
    '_EXCL,\n        0o600,\n    )\n    with diagn'
    'ostic_os.fdopen(descriptor, "wb") as strea'
    'm:\n        stream.write(data)\n\n\ndef _obser'
    'ved_diagnostic_adapt(args: argparse.Namesp'
    'ace) -> dict[str, Any]:\n    from tools.age'
    'nt_role_app_server import RoleError\n\n    g'
    'lobal _DIAGNOSTIC_STATE_MATCH, _DIAGNOSTIC'
    '_TARGET\n    _DIAGNOSTIC_TARGET = False\n   '
    ' _DIAGNOSTIC_STATE_MATCH = (\n        args.'
    'role == "controller"\n        and getattr(a'
    'rgs, "continuation_state", None) == _DIAGN'
    'OSTIC_STATE\n    )\n    try:\n        result '
    '= _original_diagnostic_adapt(args)\n    exc'
    'ept (RoleError, AdapterError, SchemaError)'
    ' as error:\n        if _DIAGNOSTIC_TARGET:\n'
    '            code = getattr(error, "machine'
    '_code", "SCHEMA_ERROR")\n            if typ'
    'e(code) is not str or code not in _DIAGNOS'
    'TIC_CODE_ALLOWLIST:\n                code ='
    ' "OTHER"\n            classes = {\n         '
    '       RoleError: "RoleError",\n           '
    '     AdapterError: "AdapterError",\n       '
    '         SchemaError: "SchemaError",\n     '
    '       }\n            record = {\n          '
    '      "schema_version": "1.0",\n           '
    '     "binding": "ORIGINAL_RESERVED_AGGREGA'
    'TE_INPUT",\n                "phase": "ADAPT'
    '_EXCEPTION",\n                "exception_cl'
    'ass": classes.get(type(error), "OTHER"),\n '
    '               "machine_code": code,\n     '
    '       }\n            try:\n                '
    '_adapter_diagnostic_write(record)\n        '
    '    except (OSError, ValueError):\n        '
    '        # Parent explicitly STOPs on missi'
    'ng/partial/invalid receipt.\n              '
    '  pass\n        raise\n    if _DIAGNOSTIC_TA'
    'RGET:\n        try:\n            _adapter_di'
    'agnostic_write({\n                "schema_v'
    'ersion": "1.0",\n                "binding":'
    ' "ORIGINAL_RESERVED_AGGREGATE_INPUT",\n    '
    '            "phase": "ADAPT_RETURNED",\n   '
    '             "exception_class": None,\n    '
    '            "machine_code": None,\n        '
    '    })\n        except (OSError, ValueError'
    '):\n            # Parent explicitly STOPs o'
    'n missing/partial/invalid receipt.\n       '
    '     pass\n    return result\n\n\n_COUNTER_REC'
    'ORD = b\'{"role": "controller"}\\n\'\n\n\ndef _c'
    'ounter_directory(path: Path) -> int:\n    i'
    'mport stat as counter_stat\n\n    if (not pa'
    'th.is_absolute() or len(str(path)) > 4096\n'
    '            or len(path.parts) > 64 or any'
    '(p in (".", "..") for p in path.parts)):\n '
    '       raise RuntimeError("SYNTHETIC_COUNT'
    'ER_DIRECTORY_STOP")\n    flags = (\n        '
    'os.O_RDONLY | getattr(os, "O_DIRECTORY") |'
    ' getattr(os, "O_NOFOLLOW")\n        | getat'
    'tr(os, "O_NONBLOCK") | getattr(os, "O_CLOE'
    'XEC")\n    )\n    descriptor = os.open("/", '
    'flags)\n    try:\n        for component in p'
    'ath.parts[1:]:\n            following = os.'
    'open(component, flags, dir_fd=descriptor)\n'
    '            os.close(descriptor)\n         '
    '   descriptor = following\n        info = o'
    's.fstat(descriptor)\n        if (not counte'
    'r_stat.S_ISDIR(info.st_mode)\n             '
    '   or info.st_uid != getattr(os, "getuid")'
    '()\n                or counter_stat.S_IMODE'
    '(info.st_mode) != 0o700):\n            rais'
    'e RuntimeError("SYNTHETIC_COUNTER_DIRECTOR'
    'Y_STOP")\n        result = descriptor\n     '
    '   descriptor = -1\n        return result\n '
    '   finally:\n        if descriptor != -1:\n '
    '           os.close(descriptor)\n\n\ndef _cou'
    'nter_write_record(directory: int, data: by'
    'tes) -> None:\n    if type(data) is not byt'
    'es or len(data) > 64 or data != _COUNTER_R'
    'ECORD:\n        raise RuntimeError("SYNTHET'
    'IC_COUNTER_RECORD_STOP")\n    flags = os.O_'
    'WRONLY | os.O_CREAT | os.O_EXCL | getattr('
    'os, "O_NOFOLLOW")\n    flags |= getattr(os,'
    ' "O_NONBLOCK") | getattr(os, "O_CLOEXEC")\n'
    '    descriptor = os.open("aggregate-transp'
    'ort-count.jsonl", flags, 0o600,\n          '
    '               dir_fd=directory)\n    try:\n'
    '        if os.write(descriptor, data) != l'
    'en(data):\n            raise RuntimeError("'
    'SYNTHETIC_COUNTER_WRITE_STOP")\n    finally'
    ':\n        os.close(descriptor)\n\n\ndef _writ'
    'e_synthetic_counter_in_existing_codex_home'
    '() -> None:\n    directory = _counter_direc'
    'tory(Path(os.environ["CODEX_HOME"]))\n    t'
    'ry:\n        _counter_write_record(director'
    'y, _COUNTER_RECORD)\n    finally:\n        o'
    's.close(directory)\n\n\n_original_counter_run'
    '_codex = _run_codex\n\n\ndef _copy_synthetic_'
    'counter(scratch_path: Path) -> None:\n    i'
    'mport stat as counter_stat\n\n    source_dir'
    'ectory = _counter_directory(scratch_path /'
    ' "codex-home")\n    try:\n        flags = (\n'
    '            os.O_RDONLY | getattr(os, "O_N'
    'OFOLLOW")\n            | getattr(os, "O_NON'
    'BLOCK") | getattr(os, "O_CLOEXEC")\n       '
    ' )\n        descriptor = os.open("aggregate'
    '-transport-count.jsonl", flags,\n          '
    '                   dir_fd=source_directory'
    ')\n        try:\n            info = os.fstat'
    '(descriptor)\n            if (not counter_s'
    'tat.S_ISREG(info.st_mode) or info.st_nlink'
    ' != 1\n                    or info.st_uid !'
    '= getattr(os, "getuid")()\n                '
    '    or counter_stat.S_IMODE(info.st_mode) '
    '!= 0o600):\n                raise RuntimeEr'
    'ror("SYNTHETIC_COUNTER_SOURCE_STOP")\n     '
    '       chunks = []\n            total = 0\n '
    '           while True:\n                par'
    't = os.read(descriptor, 65 - total)\n      '
    '          if not part:\n                   '
    ' break\n                total += len(part)\n'
    '                if total > 64:\n           '
    '         raise RuntimeError("SYNTHETIC_COU'
    'NTER_RECORD_STOP")\n                chunks.'
    'append(part)\n            data = b"".join(c'
    'hunks)\n            if data != _COUNTER_REC'
    'ORD:\n                raise RuntimeError("S'
    'YNTHETIC_COUNTER_RECORD_STOP")\n        fin'
    'ally:\n            os.close(descriptor)\n   '
    ' finally:\n        os.close(source_director'
    'y)\n    destination = _counter_directory(_D'
    'IAGNOSTIC_STATE)\n    try:\n        _counter'
    '_write_record(destination, data)\n    final'
    'ly:\n        os.close(destination)\n\n\ndef _o'
    'bserved_synthetic_counter_run_codex(\n    *'
    ',\n    command: Sequence[str],\n    role: st'
    'r,\n    workdir: Path,\n    prompt: str,\n   '
    ' schema_path: Path,\n    output_path: Path,'
    '\n    model: str | None,\n    timeout: int,\n'
    '    allowed_paths: Sequence[Path] = (),\n  '
    '  scratch_path: Path | None = None,\n    ap'
    'p_server: bool = False,\n    invocation: st'
    'r | None = None,\n    backend_digest: str |'
    ' None = None,\n    guard: Any = None,\n    r'
    'easoning: str | None = None,\n    settings_'
    'observed: dict[str, Any] | None = None,\n) '
    '-> None:\n    _original_counter_run_codex(\n'
    '        command=command, role=role, workdi'
    'r=workdir, prompt=prompt,\n        schema_p'
    'ath=schema_path, output_path=output_path, '
    'model=model,\n        timeout=timeout, allo'
    'wed_paths=allowed_paths, scratch_path=scra'
    'tch_path,\n        app_server=app_server, i'
    'nvocation=invocation, backend_digest=backe'
    'nd_digest,\n        guard=guard, reasoning='
    'reasoning, settings_observed=settings_obse'
    'rved,\n    )\n    if _DIAGNOSTIC_TARGET is T'
    'rue:\n        if scratch_path is None:\n    '
    '        raise RuntimeError("SYNTHETIC_COUN'
    'TER_SCRATCH_STOP")\n        try:\n          '
    '  _copy_synthetic_counter(scratch_path)\n  '
    '      except (OSError, RuntimeError, Value'
    'Error):\n            raise RuntimeError("SY'
    'NTHETIC_COUNTER_COPY_STOP") from None\n    '
    'return None\n\n\nglobals()["_run_codex"] = _o'
    'bserved_synthetic_counter_run_codex\nglobal'
    's()["_validate_controller_review"] = _obse'
    'rved_diagnostic_validate\nglobals()["adapt"'
    '] = _observed_diagnostic_adapt\n\n'
)


def _instrument_synthetic_adapter(root: Path, state: Path) -> None:
    """Bind only this synthetic policy to its existing diagnostic insertion."""
    import ast

    # Fixed original source identities, not credentials or runtime authority.
    expected = {
        "codex_adapter.py": (
            # pragma: allowlist nextline secret
            "2072d4af1c60b16fc4711bc62f18ae2f22930fa5b6133302171aee096884949c"
        ),
        "agent_epic_workflow.py": (
            # pragma: allowlist nextline secret
            "7ab3df9a5c2f3d8f8683763eb41e08a47828ec8ffca5a4e5ef5f9798d1e5ad34"
        ),
        "agent_orchestrate.py": (
            # pragma: allowlist nextline secret
            "7420a3978e34d879caea4b98fbcb0ba8206f89154d311ee43901031942cdaec3"
        ),
    }
    path = root / "tools/codex_adapter.py"
    original_bytes = path.read_bytes()
    assert hashlib.sha256(original_bytes).hexdigest() == expected[
        "codex_adapter.py"
    ]
    original = original_bytes.decode("utf-8")
    admission = root / "tools/agent_owner_final_admission.py"
    admission_original = admission.read_bytes().decode("utf-8")
    assignments = [
        node for node in ast.parse(admission_original).body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_MOUNT_SOURCE_SHA256"
    ]
    assert len(assignments) == 1
    assert ast.literal_eval(assignments[0].value) == expected
    old_literal = '"' + expected["codex_adapter.py"] + '"'
    assert admission_original.count(old_literal) == 1
    marker = '\nif __name__ == "__main__":\n'
    assert original.count(marker) == 1
    diagnostic = state / "aggregate-adapter-diagnostic.json"
    assert not diagnostic.exists() and not diagnostic.is_symlink()
    state_text = str(state)
    parts = [state_text[i:i + 42] for i in range(0, len(state_text), 42)]
    state_literal = "\n" + "".join("    " + repr(p) + "\n" for p in parts)
    child = ADAPTER_DIAGNOSTIC.replace("DIAGNOSTIC_STATE_LITERAL", state_literal)
    instrumented = original.replace(marker, child + marker, 1)
    instrumented_digest = hashlib.sha256(instrumented.encode("utf-8")).hexdigest()
    assert instrumented_digest != expected["codex_adapter.py"]
    new_literal = '"' + instrumented_digest + '"'
    assert new_literal not in admission_original
    admission_instrumented = admission_original.replace(old_literal, new_literal, 1)
    # Inverse byte equality conserves both other pins and every guard/body.
    assert admission_instrumented.replace(new_literal, old_literal, 1) == (
        admission_original
    )
    path.write_text(instrumented)
    import stat as fixture_stat

    expected_root = state.parent / "repo"
    assert root.resolve(strict=True) == expected_root.resolve(strict=True)
    assert admission.resolve(strict=True) == root.resolve(strict=True) / (
        "tools/agent_owner_final_admission.py"
    )
    assert not admission.is_symlink()
    descriptor = os.open(admission, os.O_RDONLY | getattr(os, "O_NOFOLLOW")
                         | getattr(os, "O_CLOEXEC"))
    primary = None
    mode = None
    borrowed = False
    try:
        info = os.fstat(descriptor)
        mode = fixture_stat.S_IMODE(info.st_mode)
        assert fixture_stat.S_ISREG(info.st_mode)
        assert info.st_uid == getattr(os, "getuid")() and info.st_nlink == 1
        if not mode & fixture_stat.S_IWUSR:
            borrowed = True
            getattr(os, 'fchmod')(descriptor, mode | fixture_stat.S_IWUSR)
        admission.write_text(admission_instrumented)
    except BaseException as error:
        primary = error
        raise
    finally:
        secondary = None
        if borrowed:
            try:
                assert mode is not None
                getattr(os, 'fchmod')(descriptor, mode)
            except BaseException as error:
                secondary = error
        try:
            os.close(descriptor)
        except BaseException as error:
            if secondary is None:
                secondary = error
        if primary is None and secondary is not None:
            raise secondary

def _initial_fixture_resources(full_owner: bool = False) -> tuple[int, int, int]:
    """Source-owned synthetic initial phase/duration/expiry offsets only."""
    assert type(full_owner) is bool
    return (10800, 14400, 14390) if full_owner else (600, 3600, 3600)


def _staged_build(
    base: Path,
    source: Path,
    *,
    extra_tasks: tuple[int, ...] = (),
    delivery_enabled: bool = False,
    with_demo: bool = False,
    with_planning_policy: bool = False,
    full_owner: bool = False,
) -> dict[str, str]:
    phase_seconds, authority_seconds, expires_offset = _initial_fixture_resources(
        full_owner,
    )
    assert not full_owner or delivery_enabled
    assert not base.exists() and not base.is_symlink()
    base.mkdir(mode=0o700)
    root, state = base / "repo", base / "state"
    root.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    native = state / "native-fixture"
    native.write_bytes(_appserver_bytes())
    native.chmod(0o700)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()

    shutil.copytree(
        source / "tools",
        root / "tools",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(source / "docs/schemas", root / "docs/schemas")
    shutil.copytree(source / ".github/workflows", root / ".github/workflows")
    (root / "AGENTS.md").write_text("synthetic policy\n")
    (root / "CONTRIBUTING.md").write_text("synthetic process\n")
    if with_planning_policy:
        for name in workflow.POLICY_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("Synthetic planning policy fixture.\n")
    shutil.copyfile(Path.cwd() / "pyproject.toml", root / "pyproject.toml")
    (root / "code.py").write_text("VALUE = 1\n")
    shutil.copytree(Path.cwd() / "src/privacy_gateway", root / "src/privacy_gateway")
    for relative in (".pre-commit-config.yaml", ".secrets.baseline", ".gitignore",
                     "README.md", "LICENSE", *workflow.POLICY_FILES):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path.cwd() / relative, target)
    tiny = root / "tests"
    tiny.mkdir()
    (tiny / "conftest.py").write_text(CONFTEST)
    (tiny / "test_snapshot_smoke.py").write_text(SMOKE)
    _instrument_synthetic_adapter(root, state)
    git("init", "-b", "main")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    git("remote", "add", "origin", "https://github.com/OWNER/repo.git")
    git("add", ".")
    git("commit", "-m", "synthetic frozen runtime")
    sha = git("rev-parse", "HEAD")
    git("branch", "roadmap/294-autonomy")
    git("switch", "-c", "codex/301-test")
    accepted = install.prepare(root, sha, "OWNER/repo", base / "candidate")
    installed = base / "installed"
    # This copies candidate files into a NEW fixture directory; no process starts.
    install.activate(root, base / "candidate", installed, accepted["candidate_digest"])
    store = workflow.StateStore(state)
    now = int(time.time())
    mandate = _mandate(sha)
    mandate.update(
        schema_version="3.0",
        epic=294,
        roadmap_ref="roadmap/294-autonomy",
        issued_at=now - 10,
        expires_at=now + expires_offset,
    )
    mandate["limits"]["max_duration_seconds"] = authority_seconds
    mandate["task_grants"] = {
        "301": {
            "causal_scope": ["synthetic capacity fixture"],
            "allowed_paths": ["code.py"],
        }
    }
    for task in extra_tasks:
        mandate["task_grants"][str(task)] = {
            "causal_scope": ["synthetic capacity fixture"],
            "allowed_paths": ["code.py"],
        }
    if delivery_enabled:
        mandate["operations"] += [
            "commit_task",
            "push_task",
            "create_task_pr",
            "merge_task_pr",
            "close_task",
            "update_epic",
        ]
    if full_owner:
        mandate["operations"] += [
            "create_roadmap_pr", "merge_roadmap_pr", "close_epic",
        ]
    mandate["approval"]["approved_at"] = now - 10
    digest = delivery.mandate_digest(mandate)
    mandate["approval"]["mandate_digest"] = digest
    handover = _value(sha)
    body = "synthetic criterion\n"
    handover.update(
        schema_version="2.0",
        repository="OWNER/repo",
        epic=294,
        task=301,
        base_ref="roadmap/294-autonomy",
        head_ref="codex/301-test",
        allowed_paths=["code.py"],
        causal_scope=["synthetic capacity fixture"],
        acceptance_criteria=["synthetic criterion"],
        delivery_criterion_indices=[],
        trusted_policy={"source": "pinned_policy_sha", "policy_sha": sha},
    )
    handover["criteria_source"].update(
        issue=301, body_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest()
    )
    handover["mandate_provenance"] = {
        "schema_version": "3.0",
        "digest": digest,
        "policy_sha": sha,
    }
    from tools.agent_coordinator_handover import handover_digest

    approved = handover_digest(handover)
    handover["approval"]["plan_digest"] = approved
    plans: dict[str, Any] = {
        "schema_version": "1.0",
        "initial_handover_digest": approved,
        "tasks": {},
    }
    if with_demo:
        plans["tasks"]["301"] = {
            "plan": handover,
            "demo_plan": {
                "schema_version": "1.0",
                "repository": "OWNER/repo",
                "epic": 294,
                "task": 301,
                "baseline_ref": "main",
                "baseline_sha": sha,
                "roadmap_ref": "roadmap/294-autonomy",
                "roadmap_sha": sha,
                "consumer_visible": False,
                "not_applicable_reason": "Synthetic internal fixture",
                "scenario": None,
                "parameters": [],
                "old_argv": [],
                "new_argv": [],
            },
        }
    backend = document()
    backend["source"].update(
        executable=str(native),
        executable_sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
        version_digest=hashlib.sha256(b"codex-cli 0.157.1").hexdigest(),
    )
    continuation = owner()
    continuation.update(
        policy_sha=sha,
        mandate_digest=digest,
        owner_identity="OWNER",
        roadmap_ref=mandate["roadmap_ref"],
        backend_digest=guards.canonical_digest(backend).removeprefix("sha256:"),
    )
    continuation["limits"]["max_prompt_bytes"] = 262144
    wait = {
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
        "mandate_digest": digest,
        "limits": {
            "max_phase_seconds": phase_seconds,
            "max_read_attempts": 2,
            "max_ci_polls": 8,
            "max_read_commands": 512,
        },
    }
    config = {
        "schema_version": "6.0",
        "policy_root": str(installed / "policy"),
        "approved_mandate_digest": digest,
        "owner_identity": "OWNER",
        "started_at": now,
        "task_iterations": 0,
        "follow_up_issues": 0,
        "approved_order": [301, *extra_tasks],
        "approved_plans_digest": guards.canonical_digest(plans),
        "approved_final_digest": None,
        "approved_launch_digest": None,
        "approved_planning_digest": None,
        "approved_wait_digest": guards.canonical_digest(wait),
        "approved_quota_digest": None,
        "approved_continuation_digest": guards.canonical_digest(continuation),
    }
    binary = state / "adapter-bin"
    binary.mkdir(mode=0o700)
    script = binary / "runtime.py"
    script.write_bytes((installed / "runtime.py").read_bytes())
    script.chmod(0o600)
    command = [sys.executable, str(script)]
    from tools import agent_epic_loop as loop

    adapter = {
        "schema_version": "2.0",
        "live_command": command,
        "phase_commands": {p: command for p in loop.PHASES[1:]},
        "reconcile_commands": {p: command for p in loop.PHASES[1:]},
        "timeout_seconds": phase_seconds,
        "output_limit": 100000,
        "epic_command": command
        + ["epic_resume", "--state-dir", str(state), "--repository-root", str(root)],
    }
    checkpoint = {
        "schema_version": "3.0",
        "repository": "OWNER/repo",
        "epic": 294,
        "task": 301,
        "pr": None,
        "base_ref": "roadmap/294-autonomy",
        "base_sha": sha,
        "head_ref": "codex/301-test",
        "head_sha": sha,
        "policy_sha": sha,
        "phase": "PLAN",
        "completed_phases": [],
        "pending_phase": None,
        "merge_sha": None,
        "status": "READY",
        "rate_limit_pause": None,
    }
    for name, value in [
        ("epic-runtime", config),
        ("mandate", mandate),
        ("handover", handover),
        ("approved-plans", plans),
        ("wait-profile", wait),
        ("continuation-profile", continuation),
        ("role-backend", backend),
        ("runtime-adapter", adapter),
    ]:
        store.save(name, value)
    loop.CheckpointStore(state, root).save(checkpoint)
    launch = entry.prepare(root, installed, state)
    config["approved_launch_digest"] = launch["candidate_digest"]
    store.save("epic-runtime", config)
    # This only writes fixture control JSON; no supervisor/service/model starts.
    entry.control(root, state, "RUNNING")
    return {
        "root": str(root),
        "state": str(state),
        "installed": str(installed),
        "sha": sha,
        "body": body,
    }

def _prepared_two_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    full_owner: bool = False,
) -> dict[str, str]:
    phase_seconds = _initial_fixture_resources(full_owner)[0]
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
    (source / "docs/schemas/agent-learning-catalog.json").write_text(
        json.dumps(empty) + "\n"
    )
    (source / "docs/schemas/agent-learning-planning-catalog.json").write_text(
        json.dumps(catalog) + "\n"
    )
    value = _staged_build(
        tmp_path / "fixture",
        source,
        extra_tasks=(303,),
        delivery_enabled=True,
        with_demo=True,
        with_planning_policy=True,
        full_owner=full_owner,
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
    assert adapter["timeout_seconds"] == phase_seconds
    wait_profile = store.load("wait-profile")
    assert wait_profile is not None
    assert wait_profile["limits"] == {
        "max_phase_seconds": phase_seconds,
        "max_read_attempts": 2,
        "max_read_commands": 512,
        "max_ci_polls": 8,
    }
    adapter["timeout_seconds"] = phase_seconds
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

HISTORY = (
    '\nimport copy, subprocess, os\nsys.path.in'
    'sert(1,str(source))\nfrom tests.learning_'
    'session_primary_fixture import Primary\nf'
    'rom tools import agent_epic_runtime_cli '
    'as cli\nfrom tools import agent_epic_plan'
    'ning as planning\nfrom tools import agent'
    '_epic_loop as loop\nfrom tools import age'
    'nt_gate\nfrom tools import agent_orchestr'
    'ate as workflow\nfrom tools.agent_epic_pl'
    'anning_roles import PlanningRoles\nfrom t'
    'ools.agent_learning_planning import Plan'
    'ning\napi=Primary(root,Path(REMOTE),polic'
    "y_sha=reader.owner['policy_sha'],tasks=("
    '301,303),initialize=False)\npreview=json.'
    "loads((state/'session-preview.json').rea"
    'd_text())\noriginal_run=subprocess.run\nde'
    'f io(argv,**kwargs):\n    argv=list(argv)'
    "\n    if argv[0]=='gh':\n        assert 0 "
    "< kwargs.get('timeout',15) <= 120\n      "
    '  return subprocess.CompletedProcess(arg'
    "v,0,api.read(argv),'')\n    if argv[0]=='"
    "git':\n        exact='https://github.com/"
    "OWNER/repo.git'\n        argv=[str(api.re"
    'mote) if arg==exact else arg for arg in '
    'argv]\n        assert all(\n            no'
    "t any(s in arg for s in ('https://','htt"
    "p://','ssh://','git@'))\n            for "
    "arg in argv)\n        if argv[1] in {'fet"
    "ch','push','ls-remote'}:\n            ass"
    'ert str(api.remote) in argv or any(\n    '
    '            Path(arg).is_absolute() and '
    'Path(arg).is_dir()\n                and P'
    "ath(arg).name in {'mirror.git','snapshot"
    "'}\n                and Path(arg).parent."
    "name.startswith('pgw-gate-')\n           "
    "     and str(Path(arg)).startswith('/tmp"
    "/')\n                for arg in argv)\n   "
    "     kwargs['timeout']=min(kwargs.get('t"
    "imeout') or 15,15)\n    return original_r"
    'un(argv,**kwargs)\nsubprocess.run=io\nsess'
    'ion=cli.build_session(state,root,wake=Tr'
    'ue)\n# Retain Scope.reader itself, guard/'
    'frame state and command reservation logi'
    'c.\noriginal_reader=session.wait_scope.re'
    'ader\ndef bounded_reader(stage):\n    resu'
    'lt=original_reader(stage)\n    result.run'
    '=io\n    return result\nsession.wait_scope'
    '.reader=bounded_reader\nreader.read=sessi'
    'on._github_read\n\nfrom tools.agent_epic_s'
    'ession import Session\nfrom tools.agent_l'
    'earning import LearningError\nfrom datacl'
    'asses import asdict\nnative_calls=[]\norig'
    'inal_adapter=session.task_adapter\ndef ad'
    'apter(store,iterations):\n    engine=orig'
    'inal_adapter(store,iterations)\n    wrapp'
    'ed=engine.bootstrap.reviewed.adapter\n   '
    " prefix=[sys.executable,'-B',str(state/'"
    "synthetic-roles.py'),str(reader.policy),"
    '\n        str(source),str(state),str(root'
    ')]\n    synthetic=workflow.CommandAdapter'
    "(prefix+['executor'],prefix+['controller"
    "'],\n        root=root,timeout_seconds=10"
    ',output_limit=65536)\n    def native(comm'
    'and,payload):\n        wrapped.delegate.g'
    'uard()\n        assert command in (wrappe'
    'd.executor_command,wrapped.controller_co'
    "mmand)\n        role='executor' if comman"
    "d==wrapped.executor_command else 'contro"
    "ller'\n        native_calls.append((paylo"
    "ad['contract']['issue'],role))\n        t"
    'arget=(synthetic.executor_command if rol'
    "e=='executor'\n            else synthetic"
    '.controller_command)\n        return synt'
    'hetic._call(target,payload)\n    wrapped.'
    'delegate._call=native\n    engine.transpo'
    'rt.command=api.command\n    return engine'
    '\nsession.task_adapter=adapter\ntry:\n    s'
    'ession.resume()\nexcept loop.PhaseBlocked'
    ' as error:\n    assert error.machine_code'
    "=='FINAL_DEMO_UNCONFIRMED'\n    assert se"
    "ssion.config['approved_final_digest'] is"
    " None\nelse:\n    raise AssertionError('ex"
    "pected original null-final boundary')\ncu"
    "rsor=cli.private_json(state/'session-con"
    "trol/cursor.json')\nseed=session.store.lo"
    'ad()\nassert seed is not None\nSession._va'
    'lidate_cursor(cursor,seed)\nassert cursor'
    "['mode']=='FINAL' and cursor['completed_"
    "tasks']==[301,303]\nassert cursor['pendin"
    "g_task'] is None and cursor['current_tas"
    "k']==303\nassert session.config['approved"
    "_order']==[301,303]\nassert set(reader.ma"
    "ndate['task_grants'])=={'301','303'}\nass"
    "ert set(reader._json(state/'planning-pro"
    "file.json')['tasks'])=={'301','303'}\nass"
    "ert not (state/'planning/301').exists()\n"
    "assert native_calls==[(301,'executor'),("
    "301,'controller'),\n    (303,'executor'),"
    "(303,'controller')]\nplanning_calls=[json"
    ".loads(line) for line in\n    (state/'nat"
    "ive-planning-observations.jsonl').read_t"
    "ext().splitlines()]\nassert [(x['task'],x"
    "['role']) for x in planning_calls]==[(30"
    "3,'planner'),(303,'review')]\nassert not "
    "any(x['hinted'] for x in planning_calls)"
    '\nfor task in (301,303):\n    assert api.p'
    'ulls[task].merge_sha==preview[str(task)]'
    "['merge']\n    fact=reader.workflow(task)"
    "\n    assert fact.state['status']=='PASS'"
    "\n    assert fact.state['executor_calls']"
    "==fact.state['controller_calls']==1\n"
)
OBSERVATION = (
    '\nimport copy, subprocess, os\nsys.path.insert(1,str(source))\n'
    'from tests.learning_session_primary_fixture import Primary\nf'
    'rom tools import agent_epic_runtime_cli as cli\nfrom tools im'
    'port agent_epic_planning as planning\nfrom tools import agent'
    '_epic_loop as loop\nfrom tools import agent_gate\nfrom tools i'
    'mport agent_orchestrate as workflow\nfrom tools.agent_epic_pl'
    'anning_roles import PlanningRoles\nfrom tools.agent_learning_'
    'planning import Planning\napi=Primary(root,Path(REMOTE),polic'
    "y_sha=reader.owner['policy_sha'],tasks=(301,303),initialize="
    "False)\npreview=json.loads((state/'session-preview.json').rea"
    'd_text())\noriginal_run=subprocess.run\ngh_transport_attempts='
    '0\ngh_transport_overflow=False\ndef io(argv,**kwargs):\n    glo'
    'bal gh_transport_attempts,gh_transport_overflow\n    argv=lis'
    "t(argv)\n    if argv[0]=='gh':\n        assert 0 < kwargs.get("
    "'timeout',15) <= 120\n        gh_transport_attempts=min(1025,"
    'gh_transport_attempts+1)\n        gh_transport_overflow=gh_tr'
    'ansport_overflow or gh_transport_attempts>1024\n        retur'
    "n subprocess.CompletedProcess(argv,0,api.read(argv),'')\n    "
    "if argv[0]=='git':\n        exact='https://github.com/OWNER/r"
    "epo.git'\n        argv=[str(api.remote) if arg==exact else ar"
    'g for arg in argv]\n        assert all(\n            not any(s'
    " in arg for s in ('https://','http://','ssh://','git@'))\n   "
    "         for arg in argv)\n        if argv[1] in {'fetch','pu"
    "sh','ls-remote'}:\n            assert str(api.remote) in argv"
    ' or any(\n                Path(arg).is_absolute() and Path(ar'
    "g).is_dir()\n                and Path(arg).name in {'mirror.g"
    "it','snapshot'}\n                and Path(arg).parent.name.st"
    "artswith('pgw-gate-')\n                and str(Path(arg)).sta"
    "rtswith('/tmp/')\n                for arg in argv)\n        kw"
    "args['timeout']=min(kwargs.get('timeout') or 15,15)\n    retu"
    'rn original_run(argv,**kwargs)\nsubprocess.run=io\n\nfrom tools'
    '.agent_epic_wait_scope import Scope\nfrom tools.agent_epic_se'
    'ssion_runtime import SessionRuntime\nfrom tools.agent_final_p'
    'reparation_runtime import read_completed_learning_review_own'
    'ed\nfrom tools import agent_epic_wait as wait\naudit=[]\norigin'
    'al_command=wait.Reader.command\ndef observed_command(self,arg'
    'v):\n    before=self.inspect()\n    value=original_command(sel'
    "f,argv)\n    after=self.inspect()\n    added=set(after['reques"
    "ts'])-set(before['requests'])\n    assert len(added)==1\n    k"
    "ey=next(iter(added))\n    item=after['requests'][key]\n    ass"
    "ert item['command_digest']==wait.digest(tuple(argv))\n    ass"
    "ert item['status']=='READY' and item['attempts']==1\n    audi"
    "t.append({'directory':self.directory.name,'key':key,\n       "
    "           'command_digest':item['command_digest'],\n        "
    "          'attempts':item['attempts']})\n    return value\nwai"
    't.Reader.command=observed_command\nwith loop.RunnerLock(state'
    "/'session-control'), loop.RunnerLock(state):\n    scope=Scope"
    '.for_root(state,root)\n    assert scope is not None and scope'
    '.read_only is False\n    session=SessionRuntime(state,root,cl'
    "i.private_json(state/'epic-runtime.json'),wait_scope=scope)\n"
    '    original_reader=scope.reader\n    def bounded(stage):\n   '
    '     value=original_reader(stage); value.run=io; return valu'
    "e\n    scope.reader=bounded\n    with session._stage('FINAL:FI"
    "NAL_GATE'):\n        value=read_completed_learning_review_own"
    "ed(session)\nprint(json.dumps({'source':value,'audit':audit,\n"
    "    'transport':{'gh_attempts':gh_transport_attempts,\n      "
    "           'overflow':gh_transport_overflow}}))\n"
)
# Diagnostic-only decoded slots; original literal chunks remain unchanged.
_DIAGNOSTIC = (
        'original_run=subprocess.run\n_diagnostic_failures=[]\n_di'
        'ag_count=0\n_diag_directory=None\ntry:\n    assert state.i'
        's_absolute() and state.resolve(strict=True)==state\n    '
        "candidate=state/('gate-diagnostics-'+_diagnostic_purpos"
        'e)\n    os.mkdir(candidate,0o700)\n    info=candidate.lst'
        'at()\n    assert not candidate.is_symlink() and candidat'
        'e.is_dir()\n    assert info.st_uid==os.getuid() and info'
        '.st_mode & 0o777==0o700\n    _diag_directory=candidate\ne'
        'xcept (OSError,AssertionError):\n    _diagnostic_failure'
        "s.append('DIRECTORY_SETUP_FAILED')\ndef diagnostic_save("
        "path,data):\n    try:\n        if data is None:data=b''\n "
        "       if isinstance(data,str):data=data.encode('utf-8'"
        ",errors='replace')\n        if not isinstance(data,bytes"
        "):\n            _diagnostic_failures.append('OUTPUT_TYPE"
        "');return\n        fd=os.open(path,os.O_WRONLY|os.O_CREA"
        "T|os.O_EXCL,0o600)\n        with os.fdopen(fd,'wb') as s"
        'tream:stream.write(data[:65536])\n    except OSError:\n  '
        "      _diagnostic_failures.append('WRITE_FAILED')\ndef d"
        'iagnostic_run(argv,**kwargs):\n    global _diag_count\n  '
        "  names={('pytest','-q'):'pytest',('ruff','check','.'):"
        "'ruff',\n        ('mypy','.'):'mypy',('pre-commit','run'"
        ",'--all-files'):'pre_commit'}\n    name=names.get(tuple("
        'argv))\n    prefix=None\n    if name is not None:\n       '
        " try:\n            cwd=Path(kwargs['cwd']).resolve(stric"
        "t=True)\n            valid=(cwd.name=='snapshot' and cwd"
        ".parent.name.startswith('pgw-gate-')\n                an"
        "d cwd.parent.parent==Path('/tmp')\n                and n"
        "ot Path(kwargs['cwd']).is_symlink()\n                and"
        " kwargs.get('capture_output') is True and kwargs.get('t"
        "ext') is True)\n            if not valid:\n              "
        "  _diagnostic_failures.append('BOUNDARY_INVALID')\n     "
        '       else:\n                if _diag_directory is None'
        ' or _diag_count>=128:\n                    _diagnostic_f'
        "ailures.append('DIRECTORY_OR_RECORD_LIMIT')\n           "
        '     else:\n                    _diag_count+=1\n         '
        '           prefix=str(_diag_directory/(str(_diag_count)'
        "+'-'+name))\n        except (OSError,KeyError,TypeError,"
        "ValueError):\n            _diagnostic_failures.append('B"
        "OUNDARY_OR_DIRECTORY_FAILED')\n    try:\n        complete"
        'd=original_run(argv,**kwargs)\n    except (OSError,subpr'
        'ocess.SubprocessError) as error:\n        if prefix is n'
        "ot None:\n            allowed={'FileNotFoundError','Perm"
        "issionError','TimeoutExpired',\n                'CalledP"
        "rocessError','OSError','SubprocessError'}\n            k"
        'ind=type(error).__name__\n            diagnostic_save(pr'
        "efix+'.json',json.dumps({'command':name,'exception_clas"
        "s':\n                kind if kind in allowed else 'OTHER"
        "_OS_OR_SUBPROCESS_ERROR'}))\n        raise\n    if prefix"
        " is not None:\n        diagnostic_save(prefix+'.stdout',"
        "completed.stdout)\n        diagnostic_save(prefix+'.stde"
        "rr',completed.stderr)\n        diagnostic_save(prefix+'."
        "json',json.dumps({'command':name,'returncode':completed"
        '.returncode}))\n    return completed\n'
    )
for _diagnostic_name in ("HISTORY",):
    _original = globals()[_diagnostic_name]
    assert _original.count("original_run=subprocess.run\n") == 1
    _updated = _original.replace(
        "original_run=subprocess.run\n",
        "_diagnostic_purpose=" + repr(_diagnostic_name.lower())
        + "\n" + _DIAGNOSTIC, 1,
    )
    _io_offset = _updated.index("def io(")
    assert _updated[_io_offset:].count("    return original_run(argv,**kwargs)\n") == 1
    _updated = _updated[:_io_offset] + _updated[_io_offset:].replace(
        "    return original_run(argv,**kwargs)\n",
        "    return diagnostic_run(argv,**kwargs)\n", 1)
    globals()[_diagnostic_name] = _updated
assert HISTORY.count("    session.resume()\n") == 1
HISTORY = HISTORY.replace((
        '    session.resume()\n'
    ), (
        '    outcome=session.resume()\n    safe={key:outcome[key]'
        " for key in ('status','phase','machine_code') if key in"
        ' outcome}\n    if not all(isinstance(item,str) for item '
        'in safe.values()):\n        _diagnostic_failures.append('
        "'OUTCOME_TYPE')\n    else:\n        diagnostic_save(state"
        "/'session-normal-outcome.json',json.dumps(safe))\n"
    ), 1)
assert HISTORY.count(
        "cursor=cli.private_json(state/'session-control/cursor.j"
        "son')\n"
    ) == 1
HISTORY = HISTORY.replace((
        "cursor=cli.private_json(state/'session-control/cursor.j"
        "son')\n"
    ), (
        'finally:\n    if _diagnostic_failures:raise RuntimeError'
        "('DIAGNOSTIC_INCOMPLETE')\ncursor=cli.private_json(state"
        "/'session-control/cursor.json')\n"
    ), 1)


# The genuine Session owns preparation; only synthetic primary I/O is extended.
_FINAL_PREPARATION_PRIMARY = """
from tests.learning_session_primary_fixture import Pull
from urllib.parse import parse_qs
final_pull=None
final_checks=None
primary_read=api.read
primary_command=api.command
final_create_count=0

def final_read(argv):
    argv=tuple(argv)
    if argv[:4]==('gh','pr','view','900'):
        assert final_pull is not None
        assert argv[argv.index('--repo')+1]=='OWNER/repo'
        return json.dumps(final_pull.view())
    if argv[:2]==('gh','api'):
        endpoint=next(x for x in argv[2:] if x.startswith('repos/'))
        prefix='repos/OWNER/repo/commits/'
        if endpoint.startswith(prefix):
            sha,suffix=endpoint.removeprefix(prefix).split('/',1)
            if suffix=='check-suites?per_page=100':
                suites=[copy.deepcopy(v) for k,v in api.sources.items()
                        if k.startswith('check-suites/')
                        and v['head_sha']==sha]
                suites.sort(key=lambda row:row['id'])
                return json.dumps({'total_count':len(suites),
                                   'check_suites':suites})
            key='commits/'+sha+'/check-runs?per_page=100'
            if suffix=='check-runs?filter=all&per_page=100':
                rows=copy.deepcopy(api.sources[key]['check_runs'])
                if final_pull is not None and sha==final_pull.head_sha:
                    rows+=copy.deepcopy(final_checks['check_runs'])
                return json.dumps({'total_count':len(rows),'check_runs':rows})
            if (suffix in {'check-runs','check-runs?per_page=100'}
                    and final_pull is not None and sha==final_pull.head_sha):
                return json.dumps(copy.deepcopy(final_checks))
        if endpoint=='repos/OWNER/repo/pulls/900':
            assert final_pull is not None
            assert '--method' not in argv
            return json.dumps(final_pull.rest())
        if endpoint.startswith('repos/OWNER/repo/pulls?'):
            query=parse_qs(endpoint.split('?',1)[1])
            if query.get('base')==['main']:
                assert query=={
                    'state':['all'],'head':['OWNER:'+reader.owner['roadmap_ref']],
                    'base':['main'],'per_page':['100'],
                }
                assert '--paginate' in argv and '--slurp' in argv
                assert '--method' not in argv
                return json.dumps([[] if final_pull is None
                                   else [final_pull.rest()]])
    return primary_read(argv)

def final_command(argv):
    global final_pull,final_create_count,final_checks
    argv=tuple(argv)
    if (argv[:3]==('gh','api','repos/OWNER/repo/pulls')
            and '--method' in argv):
        fields=[x.split('=',1) for i,x in enumerate(argv)
                if i and argv[i-1]=='-f']
        values=dict(fields)
        if values.get('base')=='main':
            assert argv[argv.index('--method')+1]=='POST'
            assert len(fields)==len(values)==4
            assert set(values)=={'base','head','title','body'}
            assert values['head']==reader.owner['roadmap_ref']
            assert values['title']=='Roadmap #294'
            assert values['body'] and final_pull is None
            final_pull=Pull(900,294,values['head'],api.refs[values['head']],
                            'main',api.refs['main'])
            # A new PR produces distinct synthetic PR run/suite/job sources.
            # Old task push sources remain addressable and byte-identical.
            old_sources=copy.deepcopy(api.sources)
            fresh=Primary(root,api.remote,policy_sha=api.policy_sha,
                          tasks=(301,303),initialize=False)
            fresh._ci(final_pull,post=False)
            check_key='commits/'+final_pull.head_sha+'/check-runs?per_page=100'
            final_checks=copy.deepcopy(fresh.sources[check_key])
            # Common listings are computed by final_read from immutable facts.
            # Do not replace old historical acquisition records with PR listings.
            derived={check_key,
                     'commits/'+final_pull.head_sha
                     +'/check-runs?filter=all&per_page=100',
                     'commits/'+final_pull.head_sha+'/check-suites?per_page=100'}
            for key,record in fresh.sources.items():
                if key in derived:
                    continue
                if key in api.sources:
                    assert api.sources[key]==record
                else:
                    api.sources[key]=copy.deepcopy(record)
            assert all(api.sources[k]==v for k,v in old_sources.items())
            assert {row['head_sha'] for row in final_checks['check_runs']}=={
                final_pull.head_sha}
            final_create_count+=1
            return json.dumps(final_pull.rest())
    assert argv[:4]!=('gh','pr','merge','900')
    assert argv[:4]!=('gh','issue','close','294')
    return primary_command(argv)

api.read=final_read
api.command=final_command
"""
assert HISTORY.count('preview=json.loads(') == 1
HISTORY = HISTORY.replace(
    'preview=json.loads(', _FINAL_PREPARATION_PRIMARY + '\npreview=json.loads(', 1
)
assert HISTORY.count(
    "return subprocess.CompletedProcess(argv,0,api.read(argv),'')"
) == 1
HISTORY = HISTORY.replace(
    "return subprocess.CompletedProcess(argv,0,api.read(argv),'')",
    "read=api.command if '--method' in argv else api.read\n"
    "        return subprocess.CompletedProcess(argv,0,read(argv),'')", 1,
)
_LEGACY_FINAL_STOP = """except loop.PhaseBlocked as error:
    assert error.machine_code=='FINAL_DEMO_UNCONFIRMED'
    assert session.config['approved_final_digest'] is None
else:
    raise AssertionError('expected original null-final boundary')
"""
assert HISTORY.count(_LEGACY_FINAL_STOP) == 1
HISTORY = HISTORY.replace(_LEGACY_FINAL_STOP, '', 1)
_BEFORE_FINAL_SESSION = """session.task_adapter=adapter
with loop.RunnerLock(state/'session-control'), loop.RunnerLock(state):
    original_zero=cli.final_preparation_original_sources(state,root)['current']
    assert original_zero['calls_consumed']==0
    assert original_zero['pending_reservation_digest'] is None
    assert not (state/'final-preparation/role-settings').exists()
try:
"""
assert HISTORY.count('session.task_adapter=adapter\ntry:\n') == 1
HISTORY = HISTORY.replace(
    'session.task_adapter=adapter\ntry:\n', _BEFORE_FINAL_SESSION, 1
)
_FINAL_SESSION_WAIT = """assert outcome['status']=='WAITING_OWNER_FINAL_APPROVAL'
assert outcome['machine_code']=='WAITING_OWNER_FINAL_APPROVAL'
assert outcome['report']['status']=='WAITING_OWNER_FINAL_APPROVAL'
assert session.config['schema_version']=='8.0'
assert session.config['approved_final_digest'] is None
assert final_create_count==1 and final_pull.number==900
cursor=cli.private_json(state/'session-control/cursor.json')
"""
assert HISTORY.count(
    "cursor=cli.private_json(state/'session-control/cursor.json')\n"
) == 1
HISTORY = HISTORY.replace(
    "cursor=cli.private_json(state/'session-control/cursor.json')\n",
    _FINAL_SESSION_WAIT, 1,
)

WAIT_LAYOUT_DIAGNOSTIC = (
    'import hashlib\nimport json\nimport os\nimport stat\nfrom pathli'
    'b import Path\n\nWaitLayoutEntry = tuple[str, int] | tuple[str'
    ', str]\n\n\ndef _wait_layout_hash(value: str) -> str:\n    if ty'
    'pe(value) is not str:\n        raise ValueError("WAIT_LAYOUT_'
    'DIAGNOSTIC_TYPE_STOP")\n    if len(value) > 8192:\n        rai'
    'se ValueError("WAIT_LAYOUT_DIAGNOSTIC_INPUT_LIMIT_STOP")\n   '
    ' try:\n        raw = value.encode("utf-8")\n    except Unicode'
    'Error:\n        raise ValueError("WAIT_LAYOUT_DIAGNOSTIC_ENCO'
    'DING_STOP") from None\n    if len(raw) > 8192:\n        raise '
    'ValueError("WAIT_LAYOUT_DIAGNOSTIC_INPUT_LIMIT_STOP")\n    re'
    'turn hashlib.sha256(raw).hexdigest()\n\n\ndef _wait_layout_entr'
    'y(value: WaitLayoutEntry) -> dict[str, object]:\n    if type('
    'value) is not tuple or len(value) != 2:\n        raise ValueE'
    'rror("WAIT_LAYOUT_DIAGNOSTIC_TYPE_STOP")\n    kind, detail = '
    'value\n    if type(kind) is not str or len(kind) > 9:\n       '
    ' raise ValueError("WAIT_LAYOUT_DIAGNOSTIC_TYPE_STOP")\n    if'
    ' kind in {"file", "directory"}:\n        if type(detail) is n'
    'ot int or not 0 <= detail <= 0o777:\n            raise ValueE'
    'rror("WAIT_LAYOUT_DIAGNOSTIC_MODE_STOP")\n        return {"ki'
    'nd": kind, "mode": detail, "link_target_sha256": None}\n    i'
    'f kind == "link" and type(detail) is str:\n        return {\n '
    '           "kind": "link",\n            "mode": None,\n       '
    '     "link_target_sha256": _wait_layout_hash(detail),\n      '
    '  }\n    raise ValueError("WAIT_LAYOUT_DIAGNOSTIC_TYPE_STOP")'
    '\n\n\ndef _wait_layout_admit(layout: dict[str, WaitLayoutEntry]'
    ') -> None:\n    if type(layout) is not dict:\n        raise Va'
    'lueError("WAIT_LAYOUT_DIAGNOSTIC_TYPE_STOP")\n    if len(layo'
    'ut) > 16384:\n        raise ValueError("WAIT_LAYOUT_DIAGNOSTI'
    'C_INPUT_LIMIT_STOP")\n    for key, value in layout.items():\n '
    '       _wait_layout_hash(key)\n        _wait_layout_entry(val'
    'ue)\n\n\ndef _wait_layout_digest(layout: dict[str, WaitLayoutEn'
    'try]) -> str:\n    _wait_layout_admit(layout)\n    digest = ha'
    'shlib.sha256()\n    for key in sorted(layout):\n        value '
    '= {\n            "path_sha256": _wait_layout_hash(key), **_wa'
    'it_layout_entry(layout[key])\n        }\n        digest.update'
    '(json.dumps(value, sort_keys=True, separators=(",", ":")).en'
    'code())\n        digest.update(b"\\n")\n    return digest.hexdi'
    'gest()\n\n\ndef _wait_layout_bytes(\n    before: dict[str, WaitL'
    'ayoutEntry], after: dict[str, WaitLayoutEntry]\n) -> bytes:\n '
    '   if type(before) is not dict or type(after) is not dict:\n '
    '       raise ValueError("WAIT_LAYOUT_DIAGNOSTIC_TYPE_STOP")\n'
    '    if len(before) > 16384 or len(after) > 16384:\n        ra'
    'ise ValueError("WAIT_LAYOUT_DIAGNOSTIC_INPUT_LIMIT_STOP")\n  '
    '  before_digest = _wait_layout_digest(before)\n    after_dige'
    'st = _wait_layout_digest(after)\n    added = set(after) - set'
    '(before)\n    removed = set(before) - set(after)\n    changed '
    '= {key for key in set(before) & set(after) if before[key] !='
    ' after[key]}\n    keys = sorted(added | removed | changed)\n  '
    '  rows: list[dict[str, object]] = []\n    for key in keys[:32'
    ']:\n        rows.append({\n            "path_sha256": _wait_la'
    'yout_hash(key),\n            "change": (\n                "ADD'
    'ED" if key in added else "REMOVED" if key in removed else "C'
    'HANGED"\n            ),\n            "before": _wait_layout_en'
    'try(before[key]) if key in before else None,\n            "af'
    'ter": _wait_layout_entry(after[key]) if key in after else No'
    'ne,\n        })\n    value = {\n        "schema_version": "WAIT'
    '_LAYOUT_BOUNDED_DIAGNOSTIC_V1",\n        "qualification": "AL'
    'READY_READ_LAYOUT_METADATA_ONLY",\n        "before_count": le'
    'n(before),\n        "after_count": len(after),\n        "befor'
    'e_sha256": before_digest,\n        "after_sha256": after_dige'
    'st,\n        "added_count": len(added),\n        "removed_coun'
    't": len(removed),\n        "changed_count": len(changed),\n   '
    '     "total_difference_count": len(keys),\n        "rows_emit'
    'ted": len(rows),\n        "row_limit": 32,\n        "rows_trun'
    'cated": len(keys) > 32,\n        "rows": rows,\n    }\n    raw '
    '= (json.dumps(value, sort_keys=True, separators=(",", ":")) '
    '+ "\\n").encode()\n    if len(raw) > 16384:\n        raise Valu'
    'eError("WAIT_LAYOUT_DIAGNOSTIC_OUTPUT_LIMIT_STOP")\n    retur'
    'n raw\n\n\ndef _write_wait_layout_diagnostic(\n    fixture: Path'
    ', before: dict[str, WaitLayoutEntry], after: dict[str, WaitL'
    'ayoutEntry]\n) -> None:\n    raw = _wait_layout_bytes(before, '
    'after)\n    directory = fixture / "wait-layout-diagnostic"\n  '
    '  owner = getattr(os, "getuid")()\n    try:\n        directory'
    '.mkdir(mode=0o700)\n        flags = os.O_RDONLY | getattr(os,'
    ' "O_DIRECTORY") | getattr(os, "O_NOFOLLOW")\n        flags |='
    ' getattr(os, "O_CLOEXEC")\n        directory_fd = os.open(dir'
    'ectory, flags)\n        try:\n            info = os.fstat(dire'
    'ctory_fd)\n            if not stat.S_ISDIR(info.st_mode) or i'
    'nfo.st_uid != owner:\n                raise ValueError("WAIT_'
    'LAYOUT_DIAGNOSTIC_DIRECTORY_STOP")\n            if stat.S_IMO'
    'DE(info.st_mode) != 0o700:\n                raise ValueError('
    '"WAIT_LAYOUT_DIAGNOSTIC_DIRECTORY_STOP")\n            flags ='
    ' os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOL'
    'LOW")\n            flags |= getattr(os, "O_CLOEXEC")\n        '
    '    descriptor = os.open("layout.json", flags, 0o600, dir_fd'
    '=directory_fd)\n            try:\n                info = os.fs'
    'tat(descriptor)\n                if not stat.S_ISREG(info.st_'
    'mode) or info.st_uid != owner:\n                    raise Val'
    'ueError("WAIT_LAYOUT_DIAGNOSTIC_FILE_STOP")\n                '
    'if info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600'
    ':\n                    raise ValueError("WAIT_LAYOUT_DIAGNOST'
    'IC_FILE_STOP")\n                with os.fdopen(descriptor, "w'
    'b", closefd=False) as stream:\n                    if stream.'
    'write(raw) != len(raw):\n                        raise ValueE'
    'rror("WAIT_LAYOUT_DIAGNOSTIC_WRITE_STOP")\n            finall'
    'y:\n                os.close(descriptor)\n        finally:\n   '
    '         os.close(directory_fd)\n    except OSError:\n        '
    'raise RuntimeError("WAIT_LAYOUT_DIAGNOSTIC_WRITE_STOP") from'
    ' None\n'
)

LIFECYCLE = (
    '\nfrom tools.agent_final_preparation_runtime import review_le'
    'arning_owned\nfrom tools.agent_continuation import digest\nfro'
    'm tools.agent_epic_wait import digest as wait_digest\n\ndef in'
    'ventory(base,excluded=()):\n    values={}\n    for p in base.r'
    "glob('*'):\n        relative=p.relative_to(base)\n        if r"
    "elative.parts[0] in excluded or relative.as_posix()=='comple"
    "ted-observation.py':continue\n        mode=p.lstat().st_mode "
    "& 0o777\n        if p.is_symlink():value=('symlink',mode,os.r"
    "eadlink(p))\n        elif p.is_file():value=('file',mode,hash"
    'lib.sha256(p.read_bytes()).hexdigest())\n        else:value=('
    "'directory',mode)\n        values[str(relative)]=value\n    re"
    "turn values\ndef protected():\n    return {'STATE':inventory(s"
    "tate,('read-waits','invocations')),\n            'POLICY':inv"
    'entory(reader.policy)}\ndef wait_layout():\n    return {str(p.'
    "relative_to(state/'read-waits')):\n            ('link',os.rea"
    "dlink(p)) if p.is_symlink() else\n            ('directory' if"
    " p.is_dir() else 'file',p.lstat().st_mode & 0o777)\n         "
    "   for p in (state/'read-waits').rglob('*') if p.name!='wait"
    ".json'}\nwith loop.RunnerLock(state/'session-control'):\n    w"
    "ith session._stage('FINAL:FINAL_GATE'):\n        with loop.Ru"
    'nnerLock(state):\n            current=cli.final_preparation_o'
    "riginal_sources(state,root)['current']\n            assert cu"
    "rrent['calls_consumed']==0 and current['pending_reservation_"
    "digest'] is None\n            assert not (state/'final-prepar"
    "ation/role-settings').exists()\n            now=int(time.time"
    '())\n            deadline=int(session.wait_scope._active_read'
    'er().current_deadline)\n        result=review_learning_owned('
    'session,started_at=now,deadline_at=deadline)\n        with lo'
    "op.RunnerLock(state):\n            assert result['output']['v"
    "erdict']=='PASS'\n            assert len(result['request']['l"
    "earning_source_digest'])==64\n            current=cli.validat"
    "e_final_preseed(state,root)\n            assert current['call"
    "s_consumed']==1\n            reservations=list((state/'final-"
    "preparation').glob('reservation-*.json'))\n            assert"
    ' len(reservations)==1\n            reservation=cli.private_js'
    "on(reservations[0])\n            assert reservation['status']"
    "=='COMPLETED'\n            assert reservation['input_digest']"
    " and reservation['settings_digest'] and reservation['output_"
    "digest']\n            payload={**result['request'],'trusted_p"
    "olicy':result['policy'],'session_id':result['session_id']}\n "
    '           invocation=digest(payload)\n            assert res'
    "ervation['input_digest']==invocation\n            settings=cl"
    "i.private_json(state/'final-preparation/role-settings'/('rec"
    "eipt-'+invocation+'.json'))\n            assert settings==res"
    "ult['settings_source']\n            assert settings['receipt'"
    "]['invocation_digest']==invocation\n            assert settin"
    "gs['terminal']['invocation_digest']==invocation\n            "
    "assert settings['terminal']['adapter_exit_code']==0\n        "
    "    assert reservation['settings_digest']==digest(settings['"
    "receipt'])\n            assert reservation['output_digest']=="
    "digest(result['output'])\n            assert result['request'"
    "]['learning_source_digest'] in json.dumps(payload)\n         "
    "   environments=list((state/'final-preparation').rglob('envi"
    "ronment-*.json'))\n            assert len(environments)==1\n  "
    '          environment=cli.private_json(environments[0])\n    '
    "        assert environment['input_digest']==invocation\n     "
    "       assert environment['review_source_digest']==digest(re"
    'sult)\n            before=protected()\n            frames={p.n'
    "ame:p.read_bytes() for p in (state/'invocations').glob('*.js"
    "on')}\n            wait_paths={p:p.read_bytes() for p in (sta"
    "te/'read-waits').glob('*/wait.json')}\n            stage_read"
    'er=session.wait_scope._active_reader()\n            wait_befo'
    're=stage_reader.inspect()\n            layout_before=wait_lay'
    'out()\n# Release original locks before independently reconstr'
    'ucted observation process.\nheader=("import sys,json,hashlib,'
    'time\\nfrom pathlib import Path\\n"\n    +"sys.path.insert(0,"+'
    'repr(str(reader.policy))+ ")\\n"\n    +"from tools.agent_learn'
    'ing_evidence import SourceReader\\n"\n    +"state=Path("+repr('
    'str(state))+")\\nroot=Path("+repr(str(root))+")\\n"\n    +"sour'
    'ce=Path("+repr(str(source))+")\\nreader=SourceReader(state,ro'
    'ot)\\n"\n    +"REMOTE="+repr(REMOTE)+"\\n")\nobservation=state/\''
    "completed-observation.py'\nobservation.write_text(header+OBSE"
    "RVATION)\nobserved=original_run([sys.executable,'-B',str(obse"
    'rvation)],capture_output=True,text=True,timeout=max(1,deadli'
    'ne-time.time()))\nassert observed.returncode==0,observed.stde'
    "rr\ndecoded=json.loads(observed.stdout)\nassert decoded['sourc"
    "e']==result\naudit=decoded['audit']\ntransport=decoded['transp"
    "ort']\nassert type(transport) is dict and set(transport)=={'g"
    "h_attempts','overflow'}\nassert type(transport['gh_attempts']"
    ") is int\nassert type(transport['overflow']) is bool and tran"
    "sport['overflow'] is False\nassert 0 <= transport['gh_attempt"
    "s'] <= 1024\ngh_attempts=transport['gh_attempts']\nassert type"
    '(audit) is list and len(audit)<=1024\nfor row in audit:\n    a'
    "ssert type(row) is dict\n    assert set(row)=={'directory','k"
    "ey','command_digest','attempts'}\n    assert type(row['attemp"
    "ts']) is int and row['attempts']==1\nassert len(audit)==len({"
    "(x['directory'],x['key']) for x in audit})\nassert protected("
    ')==before\nframes_after={p.name:p.read_bytes() for p in (stat'
    "e/'invocations').glob('*.json')}\nassert all(frames_after[k]="
    '=v for k,v in frames.items())\nadded=set(frames_after)-set(fr'
    "ames)\nassert len(added)==1\nframe=cli.private_json(state/'inv"
    "ocations'/next(iter(added)))\nassert frame['parent_frame_dige"
    "st'] is None\nassert frame['command_digest']==wait_digest(['D"
    "ISCOVERY','LOCAL'])\nassert frame['runtime_config_digest']==c"
    'li.launch_config_digest(session.config,session.wait_scope.ad'
    "apter)\nafter_waits={p:p.read_bytes() for p in (state/'read-w"
    "aits').glob('*/wait.json')}\nassert set(after_waits)==set(wai"
    't_paths)\nlayout_after=wait_layout()\n_write_wait_layout_diagn'
    'ostic(state.parent,layout_before,layout_after)\nassert layout'
    '_after==layout_before\nnew_receipts={}\nnew_receipt_attempts={'
    '}\nfor path,data in wait_paths.items():\n    old=json.loads(da'
    "ta); new=json.loads(after_waits[path])\n    assert new['ident"
    "ity']==old['identity'] and new['started_at']==old['started_a"
    "t']\n    assert new['deadline_at']<=old['deadline_at']\n    as"
    "sert new['last_seen_at']>=old['last_seen_at']\n    assert new"
    "['ci']==old['ci']\n    assert all(new['requests'][k]==item fo"
    "r k,item in old['requests'].items())\n    for key in set(new["
    "'requests'])-set(old['requests']):\n        item=new['request"
    "s'][key]\n        assert type(item['attempts']) is int\n      "
    "  assert item['status']=='READY' and item['attempts']==1\n   "
    "     new_receipts[(path.parent.name,key)]=item['command_dige"
    "st']\n        new_receipt_attempts[(path.parent.name,key)]=it"
    "em['attempts']\n    assert len(new['requests'])<=512\nassert {"
    "(x['directory'],x['key']):x['command_digest'] for x in audit"
    "}==new_receipts\nassert {(x['directory'],x['key']):x['attempt"
    "s'] for x in audit}==new_receipt_attempts\nassert gh_attempts"
    "==sum(x['attempts'] for x in audit)\nassert gh_attempts==sum("
    'new_receipt_attempts.values())\nassert (gh_attempts==0)==(not'
    ' audit)==(not new_receipts)\n# No unrelated accounting files/'
    'directories may appear; existing locks remain.\nallowed_wait_'
    "files={'wait.json','epic-loop.lock'}\nassert all(p.name in al"
    "lowed_wait_files for p in (state/'read-waits').rglob('*') if"
    " p.is_file())\ntransport=(state/'aggregate-transport-count.js"
    "onl').read_text().splitlines()\nassert len(transport)==1 and "
    "json.loads(transport[0])=={'role':'controller'}\nassert nativ"
    "e_calls==[(301,'executor'),(301,'controller'),(303,'executor"
    "'),(303,'controller')]\nassert [(x['task'],x['role']) for x i"
    "n planning_calls]==[(303,'planner'),(303,'review')]\nprint(js"
    "on.dumps({'status':'COMPLETED','aggregate_calls':1,'workflow"
    "_calls':4,\n    'planning_calls':2,'fresh_observation_frames'"
    ":1,'read_requests_added':len(audit),\n    'gh_transport_attem"
    "pts':gh_attempts,\n    'audit_attempts':sum(x['attempts'] for"
    " x in audit),\n    'receipt_attempts':sum(new_receipt_attempt"
    "s.values()),\n    'learning_source_digest':result['request']["
    "'learning_source_digest']}))\n"
)


# Read the actual Session-completed aggregate; never consume a second call.
_MANUAL_AGGREGATE = (
    "with loop.RunnerLock(state/'session-cont"
    "rol'):\n    with session._stage('FINAL:FI"
    "NAL_GATE'):\n        with loop.RunnerLock"
    '(state):\n            current=cli.final_p'
    'reparation_original_sources(state,root)['
    "'current']\n            assert current['c"
    "alls_consumed']==0 and current['pending_"
    "reservation_digest'] is None\n           "
    " assert not (state/'final-preparation/ro"
    "le-settings').exists()\n            now=i"
    'nt(time.time())\n            deadline=int'
    '(session.wait_scope._active_reader().cur'
    'rent_deadline)\n        result=review_lea'
    'rning_owned(session,started_at=now,deadl'
    'ine_at=deadline)\n        with loop.Runne'
    'rLock(state):\n'
)
_SESSION_COMPLETED_AGGREGATE = (
    "with loop.RunnerLock(state/'session-cont"
    "rol'):\n    with session._stage('FINAL:FI"
    "NAL_GATE'):\n        with loop.RunnerLock"
    '(state):\n            deadline=int(sessio'
    'n.wait_scope._active_reader().current_de'
    'adline)\n            result=read_complete'
    'd_learning_review_owned(session)\n       '
    "     final=cli.private_json(state/'final"
    "-preparation/final-state.json')\n        "
    "    assert final['schema_version']=='2.0"
    "'\n            assert final['phase']=='WA"
    "ITING_OWNER_FINAL_APPROVAL'\n            "
    "assert final['calls_consumed']==1\n      "
    "      assert final['owner_wait']['invoca"
    "tion_id']==session.wait_scope.frame['inv"
    "ocation_id']\n            candidate=cli.p"
    "rivate_json(state/'final-preparation'/(\n"
    "                'candidate-'+final['cand"
    "idate_digest']+'.json'))\n            rep"
    "ort=cli.private_json(state/'final-prepar"
    "ation'/(\n                'report-'+outco"
    "me['report']['record_digest']+'.json'))\n"
    "            assert candidate['pr']==900 "
    "and report==outcome['report']\n          "
    "  assert report['candidate_digest']==can"
    "didate['candidate_digest']\n"
)
assert LIFECYCLE.count(_MANUAL_AGGREGATE) == 1
LIFECYCLE = LIFECYCLE.replace(
    _MANUAL_AGGREGATE, _SESSION_COMPLETED_AGGREGATE, 1
)
assert LIFECYCLE.count('import review_learning_owned') == 1
LIFECYCLE = LIFECYCLE.replace(
    'import review_learning_owned', 'import read_completed_learning_review_owned', 1
)

# Final same-process aggregate diagnostics must also be complete.
assert LIFECYCLE.count(
        "print(json.dumps({'status':'COMPLETED','aggregate_calls"
        "':1,"
    ) == 1
LIFECYCLE = LIFECYCLE.replace((
        "print(json.dumps({'status':'COMPLETED','aggregate_calls"
        "':1,"
    ), (
        "if _diagnostic_failures:raise AssertionError('DIAGNOSTI"
        "C_INCOMPLETE')\nprint(json.dumps({'status':'COMPLETED','"
        "aggregate_calls':1,"
    ), 1)

_OWNER_COMPLETE_FINAL_TAIL = (
    '# Appended after all earlier genuine assertions,'
    ' before its sole JSON summary.\n# This is synthet'
    'ic input, not an observed human approval or real'
    ' final demo.\nimport contextlib\nimport io as owne'
    'r_io_module\nfrom dataclasses import replace as o'
    'wner_replace\n\nfrom tools import agent_epic_wait '
    'as owner_wait\nfrom tools import agent_epic_wait_'
    'frame as owner_frames\nfrom tools import agent_fi'
    'nal_preparation_records as owner_records\nfrom to'
    'ols import agent_owner_final_approve as owner_pr'
    "ocedure\n\nowner_policy = Path(session.config['pol"
    "icy_root'])\nassert Path(owner_procedure.__file__"
    ").resolve() == (\n    owner_policy / 'tools/agent"
    "_owner_final_approve.py'\n)\nassert not loop.Runne"
    'rLock._held\nassert loop.SUPERVISOR_LOCK_FD_ENV n'
    'ot in os.environ\nassert owner_frames.FD_ENV not '
    'in os.environ and owner_frames.NONCE_ENV not in '
    'os.environ\nowner_final_before = cli.private_json'
    "(state / 'final-preparation/final-state.json')\na"
    "ssert owner_final_before['phase'] == 'WAITING_OW"
    "NER_FINAL_APPROVAL'\nassert owner_final_before['c"
    "andidate_digest'] == candidate['candidate_digest"
    "']\nowner_anchor = owner_final_before['owner_wait"
    "']\nowner_stage_identity = {\n    'repository': ca"
    "ndidate['repository'], 'epic': candidate['epic']"
    ",\n    'policy_sha': candidate['policy_sha'],\n   "
    " 'wait_digest': session.config['approved_wait_di"
    "gest'], 'stage': 'FINAL:FINAL_GATE',\n}\nowner_wai"
    "t_path = state / 'read-waits' / owner_wait.diges"
    "t(\n    owner_stage_identity\n).removeprefix('sha2"
    "56:') / 'wait.json'\nowner_wait_before = cli.priv"
    'ate_json(owner_wait_path)\nassert owner_wait_befo'
    "re['ci']['status'] == 'READY'\nassert owner_wait_"
    "before['ci']['attempts'] == 1\nowner_frame_paths "
    "= sorted((state / 'invocations').iterdir())\nasse"
    'rt len(owner_frame_paths) <= 4096\nowner_frame_by'
    'tes = {path: path.read_bytes() for path in owner'
    '_frame_paths}\nowner_identity = {key: candidate[k'
    "ey] for key in owner_records.APPROVAL - {\n    'a"
    "pproved_at', 'final_demo_digest',\n}}\nowner_now ="
    ' int(time.time())\nowner_mandate = cli.private_js'
    "on(state / 'mandate.json')\nassert owner_mandate["
    "'issued_at'] <= owner_now < owner_anchor['deadli"
    "ne_at']\nowner_demo = owner_records.seal({\n    's"
    "chema_version': '1.0', **owner_identity, 'kind':"
    " 'OWNER_REAL_FINAL_DEMO',\n    'procedure': 'ADR3"
    "03_OWNER_REAL_FINAL_DEMO_V1',\n    'started_at': "
    "owner_now, 'completed_at': owner_now,\n    'scena"
    "rios': [{'id': 'fixture-final-cli', 'surface': '"
    "CLI',\n                   'steps': ['Exercise the"
    " synthetic final fixture.'],\n                   "
    "'expected': 'Bounded synthetic final result.'}],"
    "\n    'observations': [{'id': 'fixture-final-cli'"
    ", 'observed_at': owner_now,\n                    "
    "  'outcome': 'OWNER_OBSERVED_EXPECTED',\n        "
    "              'observation': 'Synthetic fixture,"
    " no real human demo.'}],\n})\nowner_approval = own"
    "er_records.seal({\n    'schema_version': '2.0', *"
    "*owner_identity,\n    'final_demo_digest': owner_"
    "demo['record_digest'], 'approved_at': owner_now,"
    "\n}, self_digest='approved_digest')\nowner_input_p"
    "ath = state.parent / 'synthetic-final-owner-inpu"
    "t.json'\nwith owner_input_path.open('x', encoding"
    "='utf-8') as owner_input_file:\n    os.fchmod(own"
    "er_input_file.fileno(), 0o600)\n    json.dump({'a"
    "pproval': owner_approval, 'demo': owner_demo}, o"
    'wner_input_file)\nassert owner_input_path.stat().'
    'st_mode & 0o077 == 0\nowner_stdout, owner_stderr '
    '= owner_io_module.StringIO(), owner_io_module.St'
    'ringIO()\nowner_old_argv, owner_old_stdin = sys.a'
    'rgv, sys.stdin\ntry:\n    sys.argv = [str(owner_po'
    "licy / 'tools/agent_owner_final_approve.py'),\n  "
    "              '--state-dir', str(state), '--repo"
    "sitory-root', str(root),\n                '--appr"
    "oved-digest', owner_approval['approved_digest']]"
    "\n    with owner_input_path.open(encoding='utf-8'"
    ') as owner_input_file:\n        sys.stdin = owner'
    '_input_file\n        with contextlib.redirect_std'
    'out(owner_stdout), contextlib.redirect_stderr(ow'
    'ner_stderr):\n            owner_exit = owner_proc'
    'edure.main()\nfinally:\n    sys.argv, sys.stdin = '
    'owner_old_argv, owner_old_stdin\nassert owner_exi'
    't == 0 and not owner_stderr.getvalue()\nowner_iss'
    'ued = json.loads(owner_stdout.getvalue())\nassert'
    " owner_issued['status'] == 'OWNER_APPROVAL_RECOR"
    "DED'\nassert owner_issued['candidate_digest'] == "
    "candidate['candidate_digest']\nassert owner_issue"
    "d['approval_digest'] == owner_approval['approved"
    "_digest']\nassert set(path.name for path in (stat"
    "e / 'final-owner').iterdir()) == {\n    'approval"
    ".json', 'approval-pin.json', 'install-receipt.js"
    "on',\n    'command-source.json', 'final-demo-sour"
    "ce.json',\n}\nowner_graph_bytes = {path: path.read"
    "_bytes() for path in (state / 'final-owner').ite"
    'rdir()}\nowner_wait_issued = cli.private_json(own'
    "er_wait_path)\nassert owner_wait_issued['ci']['st"
    "atus'] == 'READY'\nassert owner_wait_issued['ci']"
    "['attempts'] == 2\nassert owner_wait_issued['star"
    "ted_at'] == owner_wait_before['started_at']\nasse"
    "rt owner_wait_issued['deadline_at'] == owner_wai"
    "t_before['deadline_at']\n\nowner_pre_read, owner_p"
    're_command = api.read, api.command\nowner_write_c'
    "ounts = {'merge_roadmap_pr': 0, 'close_epic': 0,"
    " 'update_epic': 0}\nowner_epic_closed = False\nown"
    'er_merge_sha = None\n\n\ndef owner_final_read(argv)'
    ':\n    argv = tuple(argv)\n    if owner_merge_sha '
    "is not None and argv == (\n        'gh', 'api', '"
    "repos/OWNER/repo/git/commits/' + owner_merge_sha"
    ",\n    ):\n        parents = api.git('show', '-s',"
    " '--format=%P', owner_merge_sha).split()\n       "
    " assert parents == [candidate['main_sha'], candi"
    "date['roadmap_sha']]\n        return json.dumps({"
    "'sha': owner_merge_sha,\n                        "
    "   'parents': [{'sha': sha} for sha in parents]}"
    ')\n    value = owner_pre_read(argv)\n    if argv[:'
    "4] == ('gh', 'issue', 'view', '294'):\n        pa"
    "rsed = json.loads(value)\n        parsed['state']"
    " = 'CLOSED' if owner_epic_closed else 'OPEN'\n   "
    '     return json.dumps(parsed)\n    return value\n'
    '\n\ndef owner_final_command(argv):\n    global fina'
    'l_pull, owner_epic_closed, owner_merge_sha\n    a'
    "rgv = tuple(argv)\n    if argv[:4] == ('gh', 'pr'"
    ", 'merge', '900'):\n        assert argv == ('gh',"
    " 'pr', 'merge', '900', '--repo', 'OWNER/repo',\n "
    "                       '--merge', '--match-head-"
    "commit', candidate['roadmap_sha'])\n        asser"
    't final_pull.merge_sha is None and owner_write_c'
    "ounts['merge_roadmap_pr'] == 0\n        assert ap"
    "i.refs['main'] == candidate['main_sha']\n        "
    'old_sources = copy.deepcopy(api.sources)\n       '
    " tree = api.git('rev-parse', candidate['roadmap_"
    "sha'] + '^{tree}')\n        owner_merge_sha = api"
    ".git('commit-tree', tree,\n                      "
    "            '-p', candidate['main_sha'],\n       "
    "                           '-p', candidate['road"
    "map_sha'],\n                                  '-m"
    "', 'synthetic final primary merge')\n        api."
    "git('push', '--', str(api.remote), owner_merge_s"
    "ha + ':refs/heads/main')\n        api.refs['main'"
    '] = owner_merge_sha\n        final_pull = owner_r'
    'eplace(final_pull, merge_sha=owner_merge_sha)\n  '
    '      fresh = Primary(root, api.remote, policy_s'
    'ha=api.policy_sha,\n                        tasks'
    '=(301, 303), initialize=False)\n        fresh._ci'
    '(final_pull, post=True)\n        for key, value i'
    'n fresh.sources.items():\n            if key.star'
    "tswith('actions/runs/') and key.count('/') == 2:"
    "\n                assert value['event'] == 'push'"
    " and value['head_sha'] == owner_merge_sha\n      "
    "          value['head_branch'] = 'main'\n        "
    '    if key in api.sources:\n                asser'
    't api.sources[key] == value\n            else:\n  '
    '              api.sources[key] = copy.deepcopy(v'
    'alue)\n        assert all(api.sources[key] == val'
    'ue for key, value in old_sources.items())\n      '
    "  assert api.git('rev-parse', 'refs/heads/main')"
    " == candidate['main_sha']\n        owner_write_co"
    "unts['merge_roadmap_pr'] += 1\n        return ''\n"
    "    if argv[:4] == ('gh', 'issue', 'close', '294"
    "'):\n        assert argv == ('gh', 'issue', 'clos"
    "e', '294', '--repo', 'OWNER/repo',\n             "
    "           '--reason', 'completed')\n        asse"
    'rt owner_merge_sha is not None and not owner_epi'
    'c_closed\n        owner_epic_closed = True\n      '
    "  owner_write_counts['close_epic'] += 1\n        "
    "return ''\n    if argv[:3] == ('gh', 'api', 'repo"
    "s/OWNER/repo/issues/294/comments'):\n        asse"
    "rt '--method' in argv and argv[argv.index('--met"
    "hod') + 1] == 'POST'\n        assert owner_epic_c"
    "losed and owner_write_counts['update_epic'] == 0"
    "\n        assert any(part.startswith('body=ROADMA"
    "P DONE at ') for part in argv)\n        result = "
    'owner_pre_command(argv)\n        owner_write_coun'
    "ts['update_epic'] += 1\n        return result\n   "
    ' return owner_pre_command(argv)\n\n\napi.read, api.'
    'command = owner_final_read, owner_final_command\n'
    'owner_previous_io = subprocess.run\nowner_gh_atte'
    'mpts = 0\n\n\ndef owner_transport_io(argv, **kwargs'
    '):\n    global owner_gh_attempts\n    args = tuple'
    "(argv)\n    if args and args[0] == 'gh':\n        "
    'owner_gh_attempts += 1\n        assert 0 < kwargs'
    ".get('timeout', 15) <= 120\n        mutation = '-"
    "-method' in args or args[:3] in {\n            ('"
    "gh', 'pr', 'merge'), ('gh', 'issue', 'close'),\n "
    '       }\n        observed = api.command(args) if'
    ' mutation else api.read(args)\n        return sub'
    "process.CompletedProcess(argv, 0, observed, '')\n"
    '    return owner_previous_io(argv, **kwargs)\n\n\ns'
    'ubprocess.run = owner_transport_io\nowner_resume_'
    'stdout, owner_resume_stderr = owner_io_module.St'
    'ringIO(), owner_io_module.StringIO()\nowner_old_a'
    'rgv = sys.argv\ntry:\n    sys.argv = [str(state / '
    "'adapter-bin/runtime.py'), 'epic_resume',\n      "
    "          '--state-dir', str(state), '--reposito"
    "ry-root', str(root)]\n    with contextlib.redirec"
    't_stdout(owner_resume_stdout), contextlib.redire'
    'ct_stderr(owner_resume_stderr):\n        owner_re'
    'sume_exit = cli.main()\nfinally:\n    sys.argv = o'
    'wner_old_argv\nassert owner_resume_exit == 0 and '
    'not owner_resume_stderr.getvalue()\nowner_resume '
    '= json.loads(owner_resume_stdout.getvalue())\nass'
    "ert owner_resume['status'] == 'ROADMAP_DONE'\nass"
    "ert owner_resume['machine_code'] == 'OK'\nassert "
    "owner_write_counts == {'merge_roadmap_pr': 1, 'c"
    "lose_epic': 1, 'update_epic': 1}\nowner_final_aft"
    "er = cli.private_json(state / 'final-preparation"
    "/final-state.json')\nassert owner_final_after['ph"
    "ase'] == 'DONE' and owner_final_after['pending_o"
    "peration_id'] is None\nassert owner_final_after['"
    "calls_consumed'] == 1 and owner_final_after['own"
    "er_wait'] == owner_anchor\nowner_wait_after = cli"
    '.private_json(owner_wait_path)\nassert owner_wait'
    "_after['ci']['attempts'] == 8 and owner_wait_aft"
    "er['ci']['status'] == 'READY'\nassert len(owner_w"
    "ait_after['requests']) <= 512\nassert all(row['st"
    "atus'] == 'READY' for row in owner_wait_after['r"
    "equests'].values())\nassert owner_wait_after['sta"
    "rted_at'] == owner_wait_before['started_at']\nass"
    "ert owner_wait_after['deadline_at'] == owner_wai"
    "t_before['deadline_at']\nassert set((state / 'inv"
    "ocations').iterdir()) == set(owner_frame_bytes)\n"
    'assert all(path.read_bytes() == raw for path, ra'
    'w in owner_frame_bytes.items())\nassert all(path.'
    'read_bytes() == raw for path, raw in owner_graph'
    "_bytes.items())\nassert api.refs['main'] == owner"
    "_merge_sha\nassert api.git('rev-parse', 'refs/hea"
    "ds/main') == candidate['main_sha']\nassert not lo"
    'op.RunnerLock._held\nowner_terminal_before = owne'
    'r_wait_path.read_bytes()\nowner_final_bytes = (st'
    "ate / 'final-preparation/final-state.json').read"
    '_bytes()\nowner_terminal_requests = owner_gh_atte'
    'mpts\nowner_terminal_stdout, owner_terminal_stder'
    'r = (\n    owner_io_module.StringIO(), owner_io_m'
    'odule.StringIO()\n)\nowner_old_argv = sys.argv\ntry'
    ":\n    sys.argv = [str(state / 'adapter-bin/runti"
    "me.py'), 'epic_resume',\n                '--state"
    "-dir', str(state), '--repository-root', str(root"
    ')]\n    with contextlib.redirect_stdout(owner_ter'
    'minal_stdout), contextlib.redirect_stderr(owner_'
    'terminal_stderr):\n        owner_terminal_exit = '
    'cli.main()\nfinally:\n    sys.argv = owner_old_arg'
    'v\nassert owner_terminal_exit == 0 and not owner_'
    'terminal_stderr.getvalue()\nowner_terminal = json'
    '.loads(owner_terminal_stdout.getvalue())\nassert '
    "owner_terminal['status'] == 'ROADMAP_DONE' and o"
    "wner_terminal['machine_code'] == 'OK'\nassert own"
    'er_gh_attempts == owner_terminal_requests\nassert'
    ' owner_wait_path.read_bytes() == owner_terminal_'
    "before\nassert (state / 'final-preparation/final-"
    "state.json').read_bytes() == owner_final_bytes\na"
    "ssert owner_write_counts == {'merge_roadmap_pr':"
    " 1, 'close_epic': 1, 'update_epic': 1}\nassert al"
    'l(path.read_bytes() == raw for path, raw in owne'
    'r_graph_bytes.items())\nassert not loop.RunnerLoc'
    "k._held\nowner_tail_result = {'status': owner_res"
    "ume['status'], 'ci_attempts': 8,\n               "
    "      'writes': owner_write_counts, 'synthetic_o"
    "wner_input': True}\n"
)
_OWNER_FINAL_DIAGNOSTIC = (
    "if _diagnostic_failures:raise AssertionError('DIAGNOSTIC_INCOMPLETE')\n"
)
assert LIFECYCLE.count(_OWNER_FINAL_DIAGNOSTIC) == 1
LIFECYCLE = LIFECYCLE.replace(
    _OWNER_FINAL_DIAGNOSTIC,
    _OWNER_COMPLETE_FINAL_TAIL + '\n' + _OWNER_FINAL_DIAGNOSTIC, 1,
)
_OWNER_FINAL_SUMMARY = "print(json.dumps({'status':'COMPLETED','aggregate_calls':1,"
assert LIFECYCLE.count(_OWNER_FINAL_SUMMARY) == 1
LIFECYCLE = LIFECYCLE.replace(
    _OWNER_FINAL_SUMMARY,
    "print(json.dumps({'owner_tail':owner_tail_result,"
    "'status':'COMPLETED','aggregate_calls':1,", 1,
)
# Existing request/audit counters describe the preceding OBSERVATION only.

# Observe the real Session driver synchronization; the fixture no longer syncs refs.
_FINAL_ROADMAP_FIXTURE = (
    "\nroadmap_ref=reader.owner['roadmap_ref']\n"
    "final_merge=api.pulls[303].merge_sha\n"
    "assert final_merge==preview['303']['merge']==api.refs[roadmap_ref]\n"
    "assert api.remote==Path(REMOTE)==root.parent.parent/'remote.git'\n"
    "assert api.remote.is_absolute() and api.remote.is_dir()\n"
    "assert not api.remote.is_symlink()\n"
    "assert api.git('rev-parse','HEAD')==final_merge\n"
    "assert api.git('branch','--show-current')==roadmap_ref\n"
    "assert api.git('rev-parse','refs/heads/'+roadmap_ref)==final_merge\n"
    "assert not api.git('status','--porcelain=v1','--untracked-files=all')\n"
)

def test_original_FINAL_learning_request_completion_and_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_bin = str(_canonical_bin())
    monkeypatch.setenv("PATH", canonical_bin + os.pathsep + os.environ["PATH"])
    original_cache, cache_before = _cache(tmp_path, monkeypatch)
    value = _prepared_two_tasks(tmp_path, monkeypatch, full_owner=True)
    initial_config8(value)
    state = Path(value["state"])
    assert not (state / "invocations").exists()
    roles(value)
    script = state / "synthetic-roles.py"
    text = script.read_text()
    old = "if fields['issue']==301: (root/'code.py').write_text('VALUE = 2\\n')"
    new = (
        "(root/'code.py').write_text('VALUE = '+"
        "str({301:2,303:3}[fields['issue']])+'\\n')"
    )
    assert text.count(old) == 1
    script.write_text(text.replace(old,new))
    harness = state / "genuine-lifecycle.py"
    header = (
        "import sys,json,hashlib,time\nfrom pathlib import Path\n"
        + "sys.path.insert(0," + repr(str(Path(value["installed"]) / "policy")) + ")\n"
        + "from tools.agent_learning_evidence import SourceReader\n"
        + "state=Path(" + repr(str(state)) + ")\n"
        + "root=Path(" + repr(value["root"]) + ")\n"
        + "source=Path(" + repr(str(Path.cwd())) + ")\n"
        + "reader=SourceReader(state,root)\nREMOTE=" + repr(value["remote"]) + "\n"
        + "OBSERVATION=" + repr(OBSERVATION) + "\n"
    )
    harness.write_text(
        header + HISTORY + _FINAL_ROADMAP_FIXTURE + WAIT_LAYOUT_DIAGNOSTIC + LIFECYCLE
    )
    run = subprocess.run([sys.executable,"-B",str(harness)],capture_output=True,
                         text=True,timeout=_initial_fixture_resources(True)[0])
    # Ordinary-test reporting: preserve the already captured result before
    # the original diagnostic assertion can hide its primary evidence.
    capture_failed = False
    try:
        if (type(run.returncode) is not int or not -255 <= run.returncode <= 255
                or type(run.stdout) is not str or type(run.stderr) is not str):
            raise ValueError("HARNESS_CAPTURE_SHAPE_STOP")
        captured: dict[str, bytes] = {}
        for stream_name, text in (("stdout", run.stdout), ("stderr", run.stderr)):
            # Bound new encoding allocation before UTF-8, then bound actual bytes.
            if len(text) > 200000:
                raise ValueError("HARNESS_CAPTURE_LIMIT_STOP")
            encoded = text.encode("utf-8")
            if len(encoded) > 200000:
                raise ValueError("HARNESS_CAPTURE_LIMIT_STOP")
            captured[stream_name] = encoded
        captured["result.json"] = (json.dumps({
            "schema_version": "1.0", "returncode": run.returncode,
            "stdout_bytes": len(captured["stdout"]),
            "stderr_bytes": len(captured["stderr"]),
            "stdout_sha256": hashlib.sha256(captured["stdout"]).hexdigest(),
            "stderr_sha256": hashlib.sha256(captured["stderr"]).hexdigest(),
        }, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        if len(captured["result.json"]) > 4096:
            raise ValueError("HARNESS_CAPTURE_METADATA_LIMIT_STOP")
        capture_directory = state / "harness-captured-output"
        os.mkdir(capture_directory, 0o700)
        # Metadata is the last visible completion marker. Partial files remain
        # evidence of UNKNOWN/incomplete capture, never a successful receipt.
        for stream_name in ("stdout", "stderr", "result.json"):
            descriptor = os.open(
                capture_directory / stream_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW"),
                0o600,
            )
            write_primary: BaseException | None = None
            try:
                view = memoryview(captured[stream_name])
                while view:
                    count = os.write(descriptor, view)
                    if count <= 0:
                        raise OSError("HARNESS_CAPTURE_WRITE_STOP")
                    view = view[count:]
            except BaseException as error:
                write_primary = error
                raise
            finally:
                try:
                    os.close(descriptor)
                except BaseException:
                    if write_primary is None:
                        raise
    except (OSError, ValueError, UnicodeError, TypeError):
        capture_failed = True
    if capture_failed:
        raise RuntimeError("HARNESS_CAPTURE_PERSISTENCE_STOP") from None
    diagnostic = state / "aggregate-adapter-diagnostic.json"
    assert diagnostic.is_file() and not diagnostic.is_symlink(), (
        "ADAPTER_DIAGNOSTIC_MISSING_STOP"
    )
    assert diagnostic.stat().st_size <= 1024, "ADAPTER_DIAGNOSTIC_LIMIT_STOP"
    metadata = json.loads(diagnostic.read_bytes())
    assert set(metadata) == {
        "schema_version", "binding", "phase", "exception_class", "machine_code",
    }
    assert metadata["schema_version"] == "1.0"
    assert metadata["binding"] == "ORIGINAL_RESERVED_AGGREGATE_INPUT"
    assert metadata["phase"] in {"ADAPT_EXCEPTION", "ADAPT_RETURNED"}
    assert metadata["exception_class"] in {
        None, "OTHER", "RoleError", "AdapterError", "SchemaError",
    }
    allowed_codes = {
        None, "OTHER",
        'ADAPTER_TIMEOUT',
        'CODEX_NOT_FOUND',
        'INVALID_REQUEST',
        'MALFORMED_OUTPUT',
        'OUTPUT_LIMIT',
        'PROMPT_TOO_LARGE',
        'ROLE_BACKEND_INCOMPLETE',
        'ROLE_BACKEND_TIMEOUT',
        'ROLE_BACKEND_UNAVAILABLE',
        'ROLE_COMPLETION_INVALID',
        'ROLE_EFFECTIVE_POLICY_UNVERIFIED',
        'ROLE_EVENT_BINDING_CHANGED',
        'ROLE_EVENT_OUTPUT_LIMIT',
        'ROLE_INPUT_LIMIT',
        'ROLE_NON_QUOTA_ERROR',
        'ROLE_OUTPUT_INVALID',
        'ROLE_OUTPUT_LIMIT',
        'ROLE_PROTOCOL_INVALID',
        'ROLE_RPC_FAILED',
        'ROLE_SANDBOX_UNSUPPORTED',
        'ROLE_SERVER_REQUEST_FORBIDDEN',
        'SANDBOX_UNAVAILABLE',
        'SCHEMA_ERROR',
        'SCHEMA_VIOLATION',
        'VERSION_MISMATCH',
        'VERSION_PROBE_FAILED',
    }
    assert metadata["machine_code"] in allowed_codes
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["owner_tail"]["status"] == "ROADMAP_DONE"
    assert result["owner_tail"]["ci_attempts"] == 8
    assert result["owner_tail"]["synthetic_owner_input"] is True
    assert result["status"] == "COMPLETED"
    assert result["aggregate_calls"] == 1
    assert result["workflow_calls"] == 4 and result["planning_calls"] == 2
    assert result["fresh_observation_frames"] == 1
    accounting = [result[key] for key in (
        "read_requests_added", "gh_transport_attempts",
        "audit_attempts", "receipt_attempts",
    )]
    assert all(type(value) is int and 0 <= value <= 1024 for value in accounting)
    assert len(set(accounting)) == 1
    assert hashlib.sha256((original_cache / "db.db").read_bytes()).hexdigest() == (
        cache_before["db.db"]["sha256"]
    )
    for name in cache_before:
        if name == "db.db":
            continue
        assert _tree_map(original_cache / name) == cache_before[name]["map"]


@pytest.mark.parametrize("case", [
    "source_hash_mismatch", "other_pin_mismatch", "exact_instrumented_policy",
])
def test_synthetic_policy_adapter_pin_binding(
    tmp_path: Path, case: str,
) -> None:
    """Source-only fixture contract; no native/runtime/installed authority."""
    import ast

    root, state = tmp_path / "repo", tmp_path / "state"
    (root / "tools").mkdir(parents=True)
    state.mkdir()
    original_root = Path(__file__).resolve().parents[1]
    adapter = root / "tools/codex_adapter.py"
    admission = root / "tools/agent_owner_final_admission.py"
    adapter.write_bytes((original_root / "tools/codex_adapter.py").read_bytes())
    admission.write_bytes((
        original_root / "tools/agent_owner_final_admission.py"
    ).read_bytes())
    if case == "source_hash_mismatch":
        adapter.write_text(adapter.read_text() + "\n")
    if case == "other_pin_mismatch":
        text = admission.read_text()
        marker = '"agent_epic_workflow.py": ('
        assert text.count(marker) == 1
        admission.write_text(text.replace(marker, '"unexpected.py": (', 1))
    before_adapter, before_admission = adapter.read_bytes(), admission.read_bytes()
    if case != "exact_instrumented_policy":
        with pytest.raises(AssertionError):
            _instrument_synthetic_adapter(root, state)
        assert adapter.read_bytes() == before_adapter
        assert admission.read_bytes() == before_admission
        return
    _instrument_synthetic_adapter(root, state)
    changed = adapter.read_bytes()
    assert changed != before_adapter
    assert changed.count(b"# Explicit aggregate-bound synthetic diagnostics;") == 1
    old_assignments = [node for node in ast.parse(before_admission).body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name)
                               and target.id == "_MOUNT_SOURCE_SHA256"
                               for target in node.targets)]
    new_assignments = [node for node in ast.parse(admission.read_text()).body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name)
                               and target.id == "_MOUNT_SOURCE_SHA256"
                               for target in node.targets)]
    assert len(old_assignments) == len(new_assignments) == 1
    old_pins = ast.literal_eval(old_assignments[0].value)
    new_pins = ast.literal_eval(new_assignments[0].value)
    assert new_pins == {
        **old_pins, "codex_adapter.py": hashlib.sha256(changed).hexdigest(),
    }
    assert admission.read_text().replace(
        new_pins["codex_adapter.py"], old_pins["codex_adapter.py"], 1,
    ).encode("utf-8") == before_admission


@pytest.mark.parametrize(
    "full_owner,expected", [
        (False, (600, 3600, 3600)),
        (True, (10800, 14400, 14390)),
        ("SYNTHETIC-NONBOOL", None),
    ], ids=[
        "resources_default_exact", "resources_full_exact",
        "resources_nonbool_refusal",
    ],
)
def test_initial_full_owner_resources(
    full_owner: Any, expected: tuple[int, int, int] | None,
) -> None:
    """Pure source-owned initial resources; no fixture/runtime generation."""
    if expected is None:
        with pytest.raises(AssertionError):
            _initial_fixture_resources(full_owner)
        return
    actual = _initial_fixture_resources(full_owner)
    assert actual == expected
    phase, duration, expiry = actual
    assert 0 < phase <= 86400 and 0 < phase < expiry <= duration
    if full_owner:
        # issuedT-10 plus14400 givesT+14390, notT+14400.
        assert expiry + 10 == duration


def _synthetic_readonly_pin_unit(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Synthetic files only; no install, subprocess or installed authority."""
    root, state = tmp_path / "repo", tmp_path / "state"
    (root / "tools").mkdir(parents=True)
    state.mkdir()
    original = Path(__file__).resolve().parents[1]
    for name in ("codex_adapter.py", "agent_owner_final_admission.py"):
        (root / "tools" / name).write_bytes((original / "tools" / name).read_bytes())
    admission = root / "tools/agent_owner_final_admission.py"
    admission.chmod(0o444)
    return root, state, admission


def test_initial_full_owner_readonly_mode_restore(tmp_path: Path) -> None:
    """Real scratch loan/write/restore; source pin rules remain original."""
    import stat

    root, state, admission = _synthetic_readonly_pin_unit(tmp_path)
    before = admission.read_bytes()
    mode = stat.S_IMODE(admission.stat().st_mode)
    assert mode == 0o444
    _instrument_synthetic_adapter(root, state)
    assert admission.read_bytes() != before
    assert stat.S_IMODE(admission.stat().st_mode) == mode
    assert not (state / "aggregate-adapter-diagnostic.json").exists()


def test_initial_full_owner_write_failure_primary_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed original write stays primary and original scratch mode returns."""
    import stat

    root, state, admission = _synthetic_readonly_pin_unit(tmp_path)
    before = admission.read_bytes()
    mode = stat.S_IMODE(admission.stat().st_mode)
    original_write = Path.write_text
    primary = OSError("SYNTHETIC-ADMISSION-WRITE-FAILURE")
    attempts: list[int] = []

    def failed_admission_write(
        path: Path, data: str, *args: Any, **kwargs: Any,
    ) -> int:
        if path == admission:
            attempts.append(stat.S_IMODE(path.stat().st_mode))
            assert attempts[-1] & stat.S_IWUSR
            raise primary
        return original_write(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failed_admission_write)
    with pytest.raises(OSError) as caught:
        _instrument_synthetic_adapter(root, state)
    assert caught.value is primary
    assert len(attempts) == 1
    assert admission.read_bytes() == before
    assert stat.S_IMODE(admission.stat().st_mode) == mode == 0o444
    assert not (state / "aggregate-adapter-diagnostic.json").exists()
