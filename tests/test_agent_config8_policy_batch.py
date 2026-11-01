"""A genuinely admitted config8 reader bounds fresh policy Git fanout.

All authority/install/control state is synthetic and confined to the new
pytest fixture. The repository-identity Git read is counted separately.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_epic_runtime_cli as cli
from tools.agent_orchestrate import StateStore

from tests.test_agent_epic_runtime_cli import policy_fixture
from tests.test_agent_epic_snapshot import git
from tests.test_agent_learning_evidence import child, prepared
from tests.test_agent_learning_workflow_binding import initial_config8

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX frozen policy")


GUARD_FANOUT = r"""
from tools import agent_epic_runtime_cli as cli
assert reader.config['schema_version']=='8.0'
assert set(reader.config)==cli.epic_config_keys(reader.config)
assert Path(cli.__file__).resolve()==reader.policy/'tools/agent_epic_runtime_cli.py'
assert reader.owner['policy_sha']==reader.mandate['policy_sha']
assert reader.owner['mandate_digest']==reader.config['approved_mandate_digest']
sha=reader.mandate['policy_sha']
policy_paths={path.relative_to(reader.policy).as_posix()
              for path in reader.policy.rglob('*') if path.is_file()}
assert len(policy_paths)>1
queries={sha+':'+relative for relative in policy_paths}
calls=[]
original_run=cli.subprocess.run
def observed_run(argv,*args,**kwargs):
    result=original_run(argv,*args,**kwargs)
    if isinstance(argv,(list,tuple)) and argv and argv[0]=='git':
        assert kwargs.get('cwd')==root
        command=list(argv[1:])
        if command==['remote','get-url','origin']:
            kind='REPOSITORY_IDENTITY'
        elif command==['show',sha+':tools/agent_epic_runtime_cli.py']:
            kind='ENTRY_SHOW'
        elif command==['ls-tree','-r','--name-only',sha,'tools','docs/schemas']:
            kind='INVENTORY'
        elif command==['cat-file','--batch']:
            kind='BATCH'
        elif len(command)==2 and command[0]=='show' and command[1] in queries:
            kind='PATH_SHOW'
        else:
            kind='UNEXPECTED'
        calls.append(kind)
    return result
cli.subprocess.run=observed_run
try:
    reader.guard()
finally:
    cli.subprocess.run=original_run
print(json.dumps({'validated_config8':True,'calls':calls,
                  'policy_files':len(policy_paths)}))
"""


def test_config8_source_reader_guard_uses_at_most_three_policy_git_calls(
    tmp_path: Path,
) -> None:
    value = prepared(tmp_path)
    initial_config8(value)
    # child imports the actual temporary frozen policy and constructs SourceReader
    # under RunnerLock before this body; only its second complete guard is counted.
    result = child(value, GUARD_FANOUT, timeout_seconds=120)
    assert result["validated_config8"] is True
    assert result["policy_files"] > 1
    calls = result["calls"]
    assert isinstance(calls, list)
    assert "UNEXPECTED" not in calls
    assert calls.count("REPOSITORY_IDENTITY") == 1
    assert calls[-1] == "REPOSITORY_IDENTITY"
    policy_calls = [call for call in calls if call != "REPOSITORY_IDENTITY"]
    assert policy_calls[:2] == ["ENTRY_SHOW", "INVENTORY"]
    assert len(policy_calls) <= 3, (
        "fresh config8 SourceReader.guard policy Git fanout must be <=3; "
        f"observed {len(policy_calls)} policy calls"
    )


def _values(policy: Path) -> dict[str, bytes]:
    return {
        path.relative_to(policy).as_posix(): path.read_bytes()
        for path in sorted(policy.rglob("*"))
        if path.is_file()
    }


def _response(contents: bytes, *, kind: bytes = b"blob") -> bytes:
    size = str(len(contents)).encode("ascii")
    oid = hashlib.sha1(b"blob " + size + b"\0" + contents).hexdigest().encode()
    return oid + b" " + kind + b" " + size + b"\n" + contents + b"\n"


def _transport(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    policy: Path,
    *,
    changes: dict[str, bytes | Exception] | None = None,
    stderr: tuple[str, bytes] | None = None,
    nonzero: str | None = None,
) -> list[tuple[list[str], dict[str, Any]]]:
    """Malformed-provider unit seam; never used as genuine admission evidence."""
    values = _values(policy)
    outputs: dict[str, bytes | Exception] = {
        "show": values["tools/agent_epic_runtime_cli.py"],
        "ls-tree": "".join(path + "\n" for path in values).encode(),
        "cat-file": b"".join(_response(value) for value in values.values()),
    }
    outputs.update(changes or {})
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[bytes]:
        assert options["cwd"] == root
        assert options["capture_output"] is True
        assert options["check"] is False and options["timeout"] == 30
        calls.append((command, options))
        operation = command[1]
        output = outputs[operation]
        if isinstance(output, Exception):
            raise output
        errors = stderr[1] if stderr is not None and stderr[0] == operation else b""
        return subprocess.CompletedProcess(
            command, 1 if operation == nonzero else 0, output, errors
        )

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def _verify(fixture: tuple[Path, Path, str, Path]) -> None:
    root, policy, sha, entry = fixture
    cli.verify_policy_config8_batch(policy, root, sha, entry=entry)


def test_real_git_binary_parity_and_exact_three_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, policy, _, entry = policy_fixture(tmp_path)
    additions = {
        "tools/empty.bin": b"",
        "tools/bytes.bin": b"\0\r\n\xff\x80end\n",
        "tools/duplicate.bin": b"\0\r\n\xff\x80end\n",
        "docs/schemas/UPPER_9-.bin": b"raw\r\n",
    }
    for relative, contents in additions.items():
        (root / relative).write_bytes(contents)
        (policy / relative).write_bytes(contents)
        (policy / relative).chmod(0o600)
    git(root, "add", "tools", "docs")
    git(root, "commit", "-qm", "synthetic binary policy")
    sha = git(root, "rev-parse", "HEAD")
    cli.verify_policy(policy, root, sha, entry=entry)
    calls: list[tuple[list[str], dict[str, Any]]] = []
    original = subprocess.run

    def observe(
        command: list[str], **options: Any
    ) -> subprocess.CompletedProcess[bytes]:
        result = original(command, **options)
        calls.append((command, options))
        return result

    monkeypatch.setattr(subprocess, "run", observe)
    cli.verify_policy_config8_batch(policy, root, sha, entry=entry)
    assert [command for command, _ in calls] == [
        ["git", "show", f"{sha}:tools/agent_epic_runtime_cli.py"],
        ["git", "ls-tree", "-r", "--name-only", sha, "tools", "docs/schemas"],
        ["git", "cat-file", "--batch"],
    ]
    assert calls[-1][1]["input"] == "".join(
        f"{sha}:{relative}\n" for relative in _values(policy)
    ).encode("ascii")
    assert all(
        options["cwd"] == root
        and options["capture_output"] is True
        and options["check"] is False
        and options["timeout"] == 30
        for _, options in calls
    )


def test_each_real_guard_is_fresh_and_checks_unimported_current_bytes(
    tmp_path: Path,
) -> None:
    value = prepared(tmp_path)
    initial_config8(value)
    result = child(
        value,
        """from tools import agent_epic_runtime_cli as cli
calls=[]
original=cli.subprocess.run
def observe(argv,*args,**kwargs):
    result=original(argv,*args,**kwargs)
    if argv[0]=='git':calls.append(argv[1])
    return result
cli.subprocess.run=observe
try:
    reader.guard()
    reader.guard()
    target=reader.policy/'tools/codex_adapter.py'
    target.write_bytes(target.read_bytes()+b'\\n# synthetic drift\\n')
    try:reader.guard()
    except cli.DeploymentError as error:code=str(error)
    else:raise AssertionError('current policy drift admitted')
finally:cli.subprocess.run=original
print(json.dumps({'calls':calls,'code':code}))""",
        timeout_seconds=120,
    )
    assert result == {
        "calls": ["show", "ls-tree", "cat-file", "remote"] * 2
        + ["show", "ls-tree", "cat-file"],
        "code": "UNTRUSTED_RUNTIME_COMMAND",
    }


@pytest.mark.parametrize(
    "bad",
    [
        b"tools/a b",
        b"tools/a\rb",
        b"tools/a\0b",
        b'tools/"a"',
        b"tools/a\\b",
        b"tools/\xff",
        b"/tools/a",
        b"tools/../a",
        b"tools/./a",
        b"tools//a",
        b"tools/a/",
        b"other/a",
        b"tools/a.",
        b"tools/C:a",
        b"tools/CON.py",
        b"tools/prn",
        b"tools/Aux.x",
        b"tools/NUL",
        b"tools/com1.py",
        b"tools/lpt9/a",
        b"tools/" + b"a" * 1019,
    ],
)
def test_inventory_closed_names_refuse_before_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: bytes
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, _, _ = fixture
    inventory = b"tools/agent_epic_runtime.py\ntools/codex_adapter.py\n" + bad + b"\n"
    calls = _transport(monkeypatch, root, policy, changes={"ls-tree": inventory})
    with pytest.raises(cli.DeploymentError, match="^POLICY_REVISION_UNAVAILABLE$"):
        _verify(fixture)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "no-final-lf",
        "duplicate",
        "casefold",
        "required",
        "count",
        "bytes",
        "request",
    ],
)
def test_inventory_cardinality_and_request_bounds_precede_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, _, _ = fixture
    required = b"tools/agent_epic_runtime.py\ntools/codex_adapter.py\n"
    variants = {
        "empty": b"",
        "no-final-lf": required[:-1],
        "duplicate": required + b"tools/codex_adapter.py\n",
        "casefold": required + b"tools/CODEX_adapter.py\n",
        "required": b"tools/only.py\n",
        "count": required + b"".join(f"tools/f{i}\n".encode() for i in range(4095)),
        "bytes": b"a" * (1024 * 1024 + 1),
        "request": required
        + b"".join(
            ("tools/" + f"f{i:04d}" + "a" * 980 + "\n").encode() for i in range(1020)
        ),
    }
    inventory = variants[kind]
    if kind == "request":
        assert len(inventory) <= 1024 * 1024
        assert all(len(row) <= 1024 for row in inventory.splitlines())
    calls = _transport(monkeypatch, root, policy, changes={"ls-tree": inventory})
    with pytest.raises(cli.DeploymentError, match="^POLICY_REVISION_UNAVAILABLE$"):
        _verify(fixture)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "ambiguous",
        "tree",
        "commit",
        "gitlink",
        "upper-oid",
        "short-oid",
        "wrong-hash",
        "sha256-oid",
        "leading-zero",
        "negative-size",
        "huge-size",
        "header-cap",
        "truncated",
        "separator",
        "extra",
        "trailing",
        "cardinality",
        "non-ascii",
    ],
)
def test_batch_protocol_refuses_malformed_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, _, _ = fixture
    values = list(_values(policy).values())
    first, tail = _response(values[0]), b"".join(_response(v) for v in values[1:])
    oid, _, size = first.split(b"\n", 1)[0].split(b" ")
    variants = {
        "missing": b"query missing\n",
        "ambiguous": b"query ambiguous\n",
        "tree": _response(values[0], kind=b"tree") + tail,
        "commit": _response(values[0], kind=b"commit") + tail,
        "gitlink": _response(values[0], kind=b"160000") + tail,
        "upper-oid": oid.upper() + first[len(oid) :] + tail,
        "short-oid": first[1:] + tail,
        "wrong-hash": b"0" * 40 + first[40:] + tail,
        "sha256-oid": b"a" * 64 + first[40:] + tail,
        "leading-zero": oid + b" blob 0" + size + b"\n" + values[0] + b"\n" + tail,
        "negative-size": oid + b" blob -1\n" + tail,
        "huge-size": oid + b" blob 8388609\n",
        "header-cap": b"a" * 96 + b"\n",
        "truncated": first[:-1],
        "separator": first[:-1] + b"X" + tail,
        "extra": first + tail + _response(b"extra"),
        "trailing": first + tail + b"\0",
        "cardinality": tail,
        "non-ascii": b"\xff blob 0\n\n" + tail,
    }
    calls = _transport(monkeypatch, root, policy, changes={"cat-file": variants[kind]})
    with pytest.raises(cli.DeploymentError, match="^POLICY_REVISION_UNAVAILABLE$"):
        _verify(fixture)
    assert len(calls) == 3


@pytest.mark.parametrize("operation", ["show", "ls-tree", "cat-file"])
@pytest.mark.parametrize(
    "failure", ["nonzero", "stderr", "timeout", "missing-executable"]
)
def test_transport_refusal_and_original_exception_classes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, failure: str
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, _, _ = fixture
    changes: dict[str, bytes | Exception] = {}
    expected: type[Exception] = cli.DeploymentError
    if failure == "timeout":
        changes[operation] = subprocess.TimeoutExpired(["git"], 30)
        expected = subprocess.TimeoutExpired
    elif failure == "missing-executable":
        changes[operation] = FileNotFoundError("synthetic unavailable executable")
        expected = FileNotFoundError
    calls = _transport(
        monkeypatch,
        root,
        policy,
        changes=changes,
        stderr=(operation, b"x" * 65537) if failure == "stderr" else None,
        nonzero=operation if failure == "nonzero" else None,
    )
    with pytest.raises(expected):
        _verify(fixture)
    assert len(calls) == ["show", "ls-tree", "cat-file"].index(operation) + 1


@pytest.mark.parametrize("stage", ["show", "cat-file"])
def test_stdout_caps_are_checked_after_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, _, entry = fixture
    limit = (8 if stage == "show" else 64) * 1024 * 1024
    oversized = b"x" * (limit + 1)
    if stage == "show":
        entry.write_bytes(oversized)
    calls = _transport(monkeypatch, root, policy, changes={stage: oversized})
    with pytest.raises(cli.DeploymentError, match="^POLICY_REVISION_UNAVAILABLE$"):
        _verify(fixture)
    assert len(calls) == (1 if stage == "show" else 3)


@pytest.mark.parametrize("failure", ["mismatch", "read-error", "nonzero"])
def test_entry_original_failure_precedes_new_capture_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, _, entry = fixture
    changes: dict[str, bytes | Exception] = {"show": b"x" * (8 * 1024 * 1024 + 1)}
    if failure in {"read-error", "nonzero"}:
        entry.unlink()
    calls = _transport(
        monkeypatch,
        root,
        policy,
        changes=changes,
        stderr=("show", b"x" * 65537),
        nonzero="show" if failure == "nonzero" else None,
    )
    expected: type[Exception] = (
        FileNotFoundError if failure == "read-error" else cli.DeploymentError
    )
    code = (
        "POLICY_REVISION_UNAVAILABLE"
        if failure == "nonzero"
        else "UNTRUSTED_RUNTIME_COMMAND"
    )
    with pytest.raises(expected) as caught:
        _verify(fixture)
    if failure != "read-error":
        assert str(caught.value) == code
    assert len(calls) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "changed",
        "mode",
        "owner",
        "symlink",
        "parent-link",
        "missing",
        "extra",
        "extra-mode",
        "directory",
    ],
)
def test_original_installed_filesystem_checks_remain_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, _, _ = fixture
    calls = _transport(monkeypatch, root, policy)
    target = policy / "tools/codex_adapter.py"
    if fault == "changed":
        target.write_bytes(b"synthetic drift\n")
    elif fault == "mode":
        target.chmod(0o644)
    elif fault == "owner":
        original = Path.stat

        def other_owner(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
            info = original(path, follow_symlinks=follow_symlinks)
            if path == target:
                fields = list(info)
                fields[4] = info.st_uid + 1
                return os.stat_result(fields)
            return info

        monkeypatch.setattr(Path, "stat", other_owner)
    elif fault == "symlink":
        target.unlink()
        target.symlink_to(root / "tools/codex_adapter.py")
    elif fault == "parent-link":
        outside = tmp_path / "outside-tools"
        (policy / "tools").rename(outside)
        (policy / "tools").symlink_to(outside, target_is_directory=True)
    elif fault == "missing":
        target.unlink()
    elif fault == "directory":
        target.unlink()
        target.mkdir(mode=0o700)
    else:
        extra = policy / "tools/extra.py"
        extra.write_bytes(b"extra\n")
        extra.chmod(0o644 if fault == "extra-mode" else 0o600)
    expected: type[Exception] = (
        FileNotFoundError if fault == "missing" else cli.DeploymentError
    )
    with pytest.raises(expected):
        _verify(fixture)
    assert len(calls) == 3


def test_malformed_batch_precedes_current_filesystem_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, _, _ = fixture
    _transport(monkeypatch, root, policy, changes={"cat-file": b"query missing\n"})
    (policy / "tools/codex_adapter.py").unlink()
    with pytest.raises(cli.DeploymentError, match="^POLICY_REVISION_UNAVAILABLE$"):
        _verify(fixture)


def test_trusted_order_maps_responses_but_hash_does_not_attest_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, _, _ = fixture
    values = _values(policy)
    paths = list(values)
    # A permutation contains individually self-consistent objects. Only current
    # installed-byte comparison distinguishes this transport order violation.
    responses = list(values.values())
    responses[0], responses[-1] = responses[-1], responses[0]
    _transport(
        monkeypatch,
        root,
        policy,
        changes={"cat-file": b"".join(_response(v) for v in responses)},
    )
    with pytest.raises(cli.DeploymentError, match="^UNTRUSTED_RUNTIME_COMMAND$"):
        _verify(fixture)
    for path, contents in zip(paths, responses, strict=True):
        (policy / path).write_bytes(contents)
    # Keep entry's original comparison intact: it is a separate external file.
    _verify(fixture)


def test_real_git_replace_environment_is_retained_with_explicit_hash_refusal(
    tmp_path: Path,
) -> None:
    root, policy, sha, entry = policy_fixture(tmp_path)
    unique = b"unique synthetic replacement source\n"
    (root / "tools/codex_adapter.py").write_bytes(unique)
    (policy / "tools/codex_adapter.py").write_bytes(unique)
    git(root, "add", "tools/codex_adapter.py")
    git(root, "commit", "-qm", "synthetic unique replacement source")
    sha = git(root, "rev-parse", "HEAD")
    original = git(root, "rev-parse", f"{sha}:tools/codex_adapter.py")
    replacement = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=root,
            input=b"synthetic replacement\n",
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    git(root, "replace", original, replacement)
    (policy / "tools/codex_adapter.py").write_bytes(b"synthetic replacement\n")
    cli.verify_policy(policy, root, sha, entry=entry)
    # Git's unchanged replacement resolution reports the original header OID
    # with replacement contents; the accepted config8 consistency rule refuses.
    with pytest.raises(cli.DeploymentError, match="^POLICY_REVISION_UNAVAILABLE$"):
        cli.verify_policy_config8_batch(policy, root, sha, entry=entry)


def _constructor(value: dict[str, str], *, clock: int | None = None) -> dict[str, Any]:
    state = Path(value["state"])
    script = state / "batch-reader-constructor.py"
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        + "sys.path.insert(0,"
        + repr(str(Path(value["installed"]) / "policy"))
        + ")\n"
        + "from tools import agent_epic_runtime_cli as cli\n"
        + "from tools.agent_learning_evidence import SourceReader\n"
        + "from tools.agent_epic_loop import RunnerLock\n"
        + "state=Path("
        + repr(str(state))
        + ")\n"
        + "root=Path("
        + repr(value["root"])
        + ")\n"
        + ("cli.time.time=lambda:" + str(clock) + "\n" if clock is not None else "")
        + """calls=[]
original=cli.subprocess.run
def observe(argv,*args,**kwargs):
    result=original(argv,*args,**kwargs)
    if argv[0]=='git':calls.append(argv[1])
    return result
cli.subprocess.run=observe
with RunnerLock(state):
    try:SourceReader(state,root)
    except Exception as error:code=str(error)
    else:raise AssertionError('invalid reader authority admitted')
print(json.dumps({'code':code,'calls':calls}))
"""
    )
    result = subprocess.run(
        [sys.executable, "-B", str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    decoded = json.loads(result.stdout)
    assert isinstance(decoded, dict)
    return decoded


@pytest.mark.parametrize("version", [None, True, 8, 8.0, "9.0", "6.0"])
def test_unadmitted_schema_never_reaches_policy_dispatch(
    tmp_path: Path, version: object
) -> None:
    value = prepared(tmp_path)
    initial_config8(value)
    store = StateStore(Path(value["state"]))
    config = store.load("epic-runtime")
    assert config is not None
    if version is None:
        config.pop("schema_version")
    else:
        config["schema_version"] = version
    store.save("epic-runtime", config)
    assert _constructor(value) == {"code": "LEARNING_PROFILE_REQUIRED", "calls": []}


@pytest.mark.parametrize(
    "fault", ["approval", "expired", "revoked", "stop", "launch-drift"]
)
def test_real_reader_authority_refuses_before_policy_git(
    tmp_path: Path, fault: str
) -> None:
    value = prepared(tmp_path)
    initial_config8(value)
    store = StateStore(Path(value["state"]))
    config, mandate = store.load("epic-runtime"), store.load("mandate")
    assert config is not None and mandate is not None
    clock = None
    if fault == "approval":
        config["approved_mandate_digest"] = "sha256:" + "0" * 64
        store.save("epic-runtime", config)
    elif fault == "expired":
        clock = int(mandate["expires_at"])
    elif fault == "revoked":
        mandate["revoked"] = True
        approved = delivery.mandate_digest(mandate)
        mandate["approval"]["mandate_digest"] = approved
        config["approved_mandate_digest"] = approved
        store.save("mandate", mandate)
        store.save("epic-runtime", config)
    elif fault == "stop":
        control = store.load("launch-control")
        assert control is not None
        control["state"] = "STOPPED"
        store.save("launch-control", control)
    else:
        adapter = store.load("runtime-adapter")
        assert adapter is not None
        adapter["timeout_seconds"] += 1
        store.save("runtime-adapter", adapter)
    assert _constructor(value, clock=clock) == {
        "calls": [],
        "code": {
            "approval": "MANDATE_APPROVAL_MISMATCH",
            "expired": "MANDATE_EXPIRED",
            "revoked": "MANDATE_REVOKED",
            "stop": "OWNER_STOPPED",
            "launch-drift": "LAUNCH_CONFIG_CHANGED",
        }[fault],
    }


@pytest.mark.parametrize(
    "fault", ["config", "cached-owner", "cached-catalog", "late-profile"]
)
def test_guard_keeps_earlier_and_later_negative_barriers(
    tmp_path: Path, fault: str
) -> None:
    value = prepared(tmp_path)
    initial_config8(value)
    changes = {
        "config": """config=cli.learning_private_json(state/'epic-runtime.json')
config['task_iterations']+=1
(state/'epic-runtime.json').write_text(json.dumps(config))""",
        "cached-owner": "reader.owner['limits']['max_records']=1",
        "cached-catalog": "reader.catalog={'schema_version':'1.0','rules':[{}]}",
        "late-profile": "pass",
    }
    body = "from tools import agent_epic_runtime_cli as cli\n" + changes[fault] + "\n"
    body += (
        "LATE="
        + repr(fault == "late-profile")
        + "\n"
        + """calls=[]
original=cli.subprocess.run
def observe(argv,*args,**kwargs):
    result=original(argv,*args,**kwargs)
    if argv[0]=='git':calls.append(argv[1])
    if LATE and argv[:3]==['git','cat-file','--batch']:
        path=state/'learning-profile.json'
        owner=cli.learning_private_json(path)
        owner['limits']['max_records']=1
        path.write_text(json.dumps(owner))
    return result
cli.subprocess.run=observe
try:
    try:reader.guard()
    except Exception as error:code=str(error)
    else:raise AssertionError('negative reader barrier bypassed')
finally:cli.subprocess.run=original
print(json.dumps({'code':code,'calls':calls}))"""
    )
    result = child(value, body, timeout_seconds=120)
    assert result == {
        "code": "LEARNING_APPROVAL_MISMATCH"
        if fault == "late-profile"
        else "LEARNING_SOURCE_CHANGED",
        "calls": [] if fault == "config" else ["show", "ls-tree", "cat-file"],
    }


def test_genuine_config7_reader_preserves_legacy_verifier(tmp_path: Path) -> None:
    value = prepared(tmp_path)
    result = child(
        value,
        """from tools import agent_epic_runtime_cli as cli
assert reader.config['schema_version']=='7.0'
calls=[]
original=cli.subprocess.run
def observe(argv,*args,**kwargs):
    result=original(argv,*args,**kwargs)
    if argv[0]=='git' and argv[1:]!=['remote','get-url','origin']:
        calls.append(argv[1])
    return result
cli.subprocess.run=observe
try:reader.guard()
finally:cli.subprocess.run=original
print(json.dumps({'calls':calls}))""",
        timeout_seconds=120,
    )
    calls = result["calls"]
    assert calls[:2] == ["show", "ls-tree"]
    assert calls == ["show", "ls-tree", "ls-tree", "cat-file"]


@pytest.mark.parametrize("sha", ["A" * 40, "a" * 39, "revision"])
def test_sha_refusal_precedes_any_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sha: str
) -> None:
    root, policy, _, entry = policy_fixture(tmp_path)
    calls = _transport(monkeypatch, root, policy)
    with pytest.raises(cli.DeploymentError, match="^POLICY_SHA_INVALID$"):
        cli.verify_policy_config8_batch(policy, root, sha, entry=entry)
    assert calls == []


def test_private_policy_root_admission_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, _, _ = fixture
    calls = _transport(monkeypatch, root, policy)
    policy.chmod(0o755)
    with pytest.raises(cli.DeploymentError, match="^UNSAFE_RUNTIME_CONFIG$"):
        _verify(fixture)
    assert calls == []


def test_no_new_universal_hardlink_guarantee(tmp_path: Path) -> None:
    root, policy, sha, entry = policy_fixture(tmp_path)
    source = root / "tools/codex_adapter.py"
    source.chmod(0o600)
    target = policy / "tools/codex_adapter.py"
    target.unlink()
    os.link(source, target)
    assert target.stat().st_nlink == 2
    cli.verify_policy(policy, root, sha, entry=entry)
    cli.verify_policy_config8_batch(policy, root, sha, entry=entry)


def test_real_git_alternate_object_store_keeps_original_resolution(
    tmp_path: Path,
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, sha, entry = fixture
    alternate = tmp_path / "alternate.git"
    subprocess.run(
        ["git", "clone", "--bare", "--no-hardlinks", str(root), str(alternate)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    objects = root / ".git/objects"
    for directory in objects.iterdir():
        if directory.name != "info":
            if directory.is_dir():
                shutil.rmtree(directory)
            else:
                directory.unlink()
    (objects / "info/alternates").write_text(str(alternate / "objects") + "\n")
    cli.verify_policy(policy, root, sha, entry=entry)
    cli.verify_policy_config8_batch(policy, root, sha, entry=entry)


def test_original_runtime_module_and_other_reader_bytes_are_exact() -> None:
    runtime = Path(cli.__file__).read_text(encoding="utf-8")
    functions = [
        node for node in ast.parse(runtime).body if isinstance(node, ast.FunctionDef)
    ]
    added = next(
        node for node in functions if node.name == "verify_policy_config8_batch"
    )
    segment = ast.get_source_segment(runtime, added)
    assert segment is not None and runtime.count(segment + "\n\n\n") == 1
    original = runtime.replace(segment + "\n\n\n", "", 1)
    assert (
        hashlib.sha256(original.encode()).hexdigest()
        # Public source-byte SHA256 pin; never credential material.
        # pragma: allowlist nextline secret
        == "1e12a2d3dfe28130a397daf029c6d703957e813d7a61b071c0fb20270dfa6696"
    )
    evidence_path = Path(cli.__file__).with_name("agent_learning_evidence.py")
    evidence = evidence_path.read_text(encoding="utf-8")
    dispatch = """        verify = (
            guards.verify_policy_config8_batch
            if self.config.get("schema_version") == "8.0"
            else guards.verify_policy
        )
        verify(
"""
    assert evidence.count(dispatch) == 1
    original = evidence.replace(dispatch, "        guards.verify_policy(\n", 1)
    assert (
        hashlib.sha256(original.encode()).hexdigest()
        # Public source-byte SHA256 pin; never credential material.
        # pragma: allowlist nextline secret
        == "334597145117c4c69f3bef144ec57270adc77e34d639f87cd66b0a6c278610c4"
    )


def test_exact_blob_and_stderr_caps_are_inclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = policy_fixture(tmp_path)
    root, policy, _, _ = fixture
    (policy / "tools/codex_adapter.py").write_bytes(b"x" * (8 * 1024 * 1024))
    for operation in ("show", "ls-tree", "cat-file"):
        _transport(monkeypatch, root, policy, stderr=(operation, b"x" * 65536))
        _verify(fixture)
