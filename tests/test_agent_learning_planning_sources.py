"""Original planning workspace object bytes survive drift and reject corruption."""

from __future__ import annotations

import os
import subprocess
import zlib
from pathlib import Path
from typing import Any

import pytest
from tools import agent_learning as core
from tools.agent_learning_planning_sources import capture_workspace, verify_workspace

from tests.test_agent_learning_planning_journal import StorageReader
from tests.test_agent_learning_planning_records import identity

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX learning sources")


def git(root: Path, *args: str) -> str:
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Synthetic",
            "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic",
            "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
        }
    )
    return subprocess.check_output(
        ["git", *args], cwd=root, env=environment, text=True
    ).strip()


def setup(tmp_path: Path) -> tuple[StorageReader, dict[str, Any]]:
    root, seed = tmp_path / "repo", tmp_path / "seed"
    root.mkdir()
    seed.mkdir(mode=0o700)
    git(root, "init", "-q")
    (root / "synthetic.py").write_text("VALUE = 1\n")
    git(root, "add", "synthetic.py")
    git(root, "commit", "-qm", "synthetic initial")
    reader = StorageReader(seed, root)
    reader.config = {"schema_version": "8.0"}  # Local source-envelope double.
    return reader, capture_workspace(reader, identity(), "planner", 1)


def test_original_workspace_replay_checks_original_git_objects(tmp_path: Path) -> None:
    reader, original = setup(tmp_path)
    verify_workspace(reader, original)
    (reader.root / "synthetic.py").write_text("VALUE = 2\n")
    git(reader.root, "commit", "-qam", "synthetic changed")
    assert git(reader.root, "rev-parse", "HEAD") != original["head_sha"]
    # History verification is not replaced with a snapshot of today's tree.
    verify_workspace(reader, original)


@pytest.mark.parametrize("kind", ["commit", "tree", "blob"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_original_object_loss_or_corruption_blocks_proof(
    tmp_path: Path, kind: str, damage: str
) -> None:
    reader, original = setup(tmp_path)
    oid = original["head_sha"] if kind == "commit" else original["tree_hash"]
    if kind == "blob":
        oid = git(reader.root, "rev-parse", "HEAD:synthetic.py")
    path = reader.root / ".git" / "objects" / oid[:2] / oid[2:]
    if damage == "missing":
        path.unlink()
    else:
        raw = zlib.decompress(path.read_bytes())
        header, content = raw.split(b"\0", 1)
        replacement = bytes([content[0] ^ 1]) + content[1:]
        path.chmod(0o600)
        path.write_bytes(zlib.compress(header + b"\0" + replacement))
    with pytest.raises(
        core.LearningError, match="LEARNING_SOURCE_INVALID|LEARNING_SOURCE_MISMATCH"
    ):
        verify_workspace(reader, original)


def test_unbounded_original_object_graph_is_blocked(tmp_path: Path) -> None:
    reader, original = setup(tmp_path)
    reader.owner["limits"]["max_records"] = 1
    with pytest.raises(core.LearningError, match="LEARNING_SOURCE_INVALID"):
        verify_workspace(reader, original)


def test_dirty_workspace_is_not_a_planning_origin(tmp_path: Path) -> None:
    reader, _ = setup(tmp_path)
    (reader.root / "synthetic.py").write_text("VALUE = 2\n")
    with pytest.raises(core.LearningError, match="LEARNING_PLANNING_SOURCE_INVALID"):
        capture_workspace(reader, identity(), "planner", 1)


def planning_fixture(tmp_path: Path) -> dict[str, str]:
    from tools.agent_continuation import digest
    from tools.agent_epic_runtime_cli import canonical_digest, launch_config_digest
    from tools.agent_orchestrate import StateStore

    from tests.test_agent_learning_evidence import prepared

    value = prepared(tmp_path, with_demo=True, with_planning_policy=True)
    state = Path(value["state"])
    store = StateStore(state)
    config, adapter, launch, mandate = (
        store.load(name)
        for name in ("epic-runtime", "runtime-adapter", "launch", "mandate")
    )
    assert (
        config is not None
        and adapter is not None
        and launch is not None
        and mandate is not None
    )
    plans = store.load("approved-plans")
    assert plans is not None
    owner = {
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
            "301": {
                "criteria_sections": ["Acceptance"],
                "task_class": "runtime",
                "demo_template": plans["tasks"]["301"]["demo_plan"],
            }
        },
        "limits": {
            "max_planner_attempts": 2,
            "max_review_attempts": 2,
            "max_input_chars": 100000,
            "max_output_chars": 20000,
            "max_paths": 4,
        },
    }
    store.save("planning-profile", owner)
    config["approved_planning_digest"] = canonical_digest(owner)
    launch["runtime_config_digest"] = launch_config_digest(config, adapter)
    config["approved_launch_digest"] = canonical_digest(launch)
    store.save("epic-runtime", config)
    store.save("launch", launch)
    control = store.load("launch-control")
    assert control is not None
    control["launch_digest"] = config["approved_launch_digest"]
    store.save("launch-control", control)
    assert digest(owner) == config["approved_planning_digest"].removeprefix("sha256:")
    return value


PLANNING_SOURCE_CALL = r"""
import copy,subprocess,time
from tools import agent_epic_planning as planning
from tools import agent_epic_runtime_cli as cli
from tools.agent_learning_planning import Planning
from tools.agent_learning_planning_sources import replay
session=cli.build_session(state,root,wake=True)
mandate,approved,context=session._fresh_authority(0)
owner=session._planning_owner(mandate)
learning=Planning(reader,session)
body="## Acceptance\n- synthetic criterion\n"
base=reader.owner["policy_sha"]
plan={
 "schema_version":"2.0","repository":"OWNER/repo","epic":294,"task":301,
 "policy_sha":base,"criteria_source":{"kind":"github_issue","issue":301,
 "state":"OPEN","body_sha256":planning.body_digest(body)},
 "acceptance_criteria":["- synthetic criterion"],"delivery_criterion_indices":[],
 "default_ref":"main","default_sha":base,"base_ref":mandate["roadmap_ref"],
 "base_sha":base,"head_ref":"codex/301-test",
 "causal_scope":["synthetic capacity fixture"],"allowed_paths":["code.py"],
 "changes_policy_or_architecture":False,"task_class":"runtime",
 "budgets":{"max_minutes":5,"max_repair_iterations":2,"max_report_chars":10000},
}
proposal={"schema_version":"1.0","plan":plan,
 "demo_plan":owner["tasks"]["301"]["demo_template"],
 "criteria_mapping":[{"criterion_index":0,"allowed_paths":["code.py"],
 "reason":"Synthetic scoped change"}]}
calls=[]
def invoke(role,request):
    calls.append(role)
    if role=="planner":
        output=copy.deepcopy(proposal)
        if failure: output["criteria_mapping"]=[]
    else:
        output={"schema_version":"1.0","plan_digest":cli.canonical_digest(request["proposal"]),"verdict":"PASS","findings":[]}
    result=subprocess.run([sys.executable,"-c","import sys; print(sys.stdin.read())"],
      input=json.dumps(output),text=True,capture_output=True,check=True)
    return json.loads(result.stdout)
code="OK"
try:
    planning.Admission(state,root).admit(owner=owner,mandate=mandate,context=context,
      task=301,body=body,base=base,default=base,dependencies=[],invoke=invoke,
      guard=reader.guard,learning=learning)
except planning.loop.PhaseBlocked as error:
    code=error.machine_code
source=replay(reader,301,"planner",1)
saved=reader._json(state/"planning/301/admission.json")
result={"code":code,"validation":source["validation"]["outcome"],
        "calls":calls,"pending":saved["pending"] is not None,
        "attempts":saved["attempts"]}
if not failure:
    result["review_validation"]=replay(reader,301,"planning_review",1)["validation"]["outcome"]
print(json.dumps(result))
"""


@pytest.mark.parametrize("failure", [False, True])
def test_original_consumed_planning_validation_replays_without_new_calls(
    tmp_path: Path, failure: bool
) -> None:
    from tests.test_agent_learning_evidence import child

    value = planning_fixture(tmp_path)
    result = child(
        value, f"failure={failure!r}\n" + PLANNING_SOURCE_CALL, timeout_seconds=60
    )
    assert result["validation"] == ("FAIL" if failure else "PASS")
    assert result["code"] == ("PLANNING_CRITERIA_INCOMPLETE" if failure else "OK")
    assert result["calls"] == (["planner"] if failure else ["planner", "review"])
    assert result["pending"] is failure
    assert result["attempts"] == {"planner": 1, "review": 0 if failure else 1}
    if not failure:
        assert result["review_validation"] == "PASS"


@pytest.mark.parametrize(
    "artifact",
    ["request", "response", "context", "workspace", "validation", "metadata"],
)
def test_missing_original_planning_artifact_blocks_fresh_process(
    tmp_path: Path, artifact: str
) -> None:
    import json

    from tests.test_agent_learning_evidence import child, error_body

    value = planning_fixture(tmp_path)
    child(value, "failure=False\n" + PLANNING_SOURCE_CALL, timeout_seconds=60)
    source = Path(value["state"]) / "planning/301"
    if artifact == "metadata":
        target = next(
            path
            for path in (source / "learning-invocations").glob("*.json")
            if json.loads(path.read_text())["role"] == "planner"
        )
    else:
        target = source / "learning-validation" / ("planner-1-" + artifact + ".json")
    target.unlink()
    result = child(
        value,
        "from tools.agent_learning_planning_sources import replay\n"
        + error_body("replay(reader,301,'planner',1)"),
        timeout_seconds=60,
    )
    assert result["code"] == "LEARNING_SOURCE_MISSING"
    saved = json.loads((source / "admission.json").read_text())
    assert saved["attempts"] == {"planner": 1, "review": 1}
    assert saved["status"] == "ADMITTED"


def test_verify_workspace_one_batch_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Causal old API: original real commit/tree/blob needs3processes, new1."""
    reader, original = setup(tmp_path)
    actual = subprocess.Popen
    launches: list[list[str]] = []

    def observed(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if list(argv)[:2] == ["git", "cat-file"]:
            launches.append(list(argv))
        return actual(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", observed)
    verify_workspace(reader, original)
    assert len(launches) == 1
    assert launches[0] == ["git", "cat-file", "--batch"]


def test_verify_workspace_fresh_second_call(tmp_path: Path) -> None:
    reader, original = setup(tmp_path)
    verify_workspace(reader, original)
    oid = git(reader.root, "rev-parse", "HEAD:synthetic.py")
    (reader.root / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    with pytest.raises(core.LearningError, match="LEARNING_SOURCE_INVALID"):
        verify_workspace(reader, original)


@pytest.mark.parametrize("case", [
    "partial_stream_success", "missing_object", "malformed_header", "wrong_oid",
    "wrong_kind", "oversized_object", "truncated_body", "wrong_separator",
    "hash_mismatch", "trailing_stdout", "nonzero_exit", "request_write_timeout",
    "header_timeout", "body_timeout", "final_exit_timeout",
    "primary_cleanup_failure_preserved", "cleanup_only_failure_closed",
    "graph_cumulative_cap", "pending_duplicate_count_preserved",
    "reserved_cleanup_window_boundary", "legacy_default_per_object_transport",
    "unadmitted_schema_never_batched",
])
def test_workspace_batch_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Protocol/clock doubles; no28second sleeps or installed authority claim."""
    import hashlib
    import selectors
    from types import SimpleNamespace

    from tools import agent_epic_runtime_cli as guards
    from tools import agent_learning_planning_sources as sources
    from tools.agent_learning_evidence import SourceReader

    reader, original = setup(tmp_path)
    if case == "legacy_default_per_object_transport":
        reader.config = {"schema_version": "7.0"}
        actual = subprocess.Popen
        calls: list[list[str]] = []

        def old_transport(argv: Any, *args: Any, **kwargs: Any) -> Any:
            if list(argv)[:2] == ["git", "cat-file"]:
                calls.append(list(argv))
            return actual(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", old_transport)
        verify_workspace(reader, original)
        assert len(calls) == 3 and all("--batch" not in row for row in calls)
        return
    if case == "unadmitted_schema_never_batched":
        monkeypatch.setattr(
            guards, "learning_private_json", lambda path: {"schema_version": "9.0"},
        )
        with pytest.raises(core.LearningError, match="LEARNING_PROFILE_REQUIRED"):
            SourceReader(reader.seed, reader.root)
        return

    def oid(kind: str, raw: bytes) -> str:
        return hashlib.sha1(f"{kind} {len(raw)}\0".encode() + raw).hexdigest()

    blob = b"synthetic object bytes" * 3
    blob_id = oid("blob", blob)
    tree = b"100644 synthetic.py\0" + bytes.fromhex(blob_id)
    if case == "pending_duplicate_count_preserved":
        tree += b"100644 duplicate.py\0" + bytes.fromhex(blob_id)
        reader.owner["limits"]["max_records"] = 2
    tree_id = oid("tree", tree)
    commit = ("tree " + tree_id + "\n\nsynthetic\n").encode()
    commit_id = oid("commit", commit)
    if case == "graph_cumulative_cap":
        reader.owner["limits"]["max_store_bytes"] = max(map(len, (blob, tree, commit)))
    if case == "oversized_object":
        reader.owner["limits"]["max_store_bytes"] = len(blob) - 1
    objects = {blob_id: ("blob", blob), tree_id: ("tree", tree),
               commit_id: ("commit", commit)}
    clock = [0.0]
    wire = bytearray()
    requests: list[str] = []
    input_bytes = bytearray()
    close_attempts: list[str] = []
    waits: list[float] = []

    class Pipe:
        def __init__(self, name: str, number: int) -> None:
            self.name, self.number = name, number
            self.closed = False

        def fileno(self) -> int:
            return self.number

        def close(self) -> None:
            close_attempts.append(self.name)
            self.closed = True
            if self.name == "stdout" and case in {
                "primary_cleanup_failure_preserved", "cleanup_only_failure_closed",
            }:
                if case == "primary_cleanup_failure_preserved":
                    raise KeyboardInterrupt
                raise OSError("synthetic close")

    class Child:
        def __init__(self) -> None:
            self.stdin, self.stdout = Pipe("stdin", 101), Pipe("stdout", 102)
            self.returncode: int | None = None
            self.killed = False

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.killed, self.returncode = True, -9

        def wait(self, timeout: float) -> int:
            waits.append(timeout)
            if case == "final_exit_timeout" and not self.killed:
                clock[0] = 30.0
                self.returncode = 0
                return 0
            if self.returncode is None:
                self.returncode = 1 if case == "nonzero_exit" else 0
            assert self.returncode is not None
            return self.returncode

    child = Child()
    starts: list[Any] = []

    def popen(argv: Any, **kwargs: Any) -> Any:
        starts.append((list(argv), kwargs))
        return child

    def write(fd: int, raw: Any) -> int:
        assert fd == 101
        chunk = bytes(raw[:5])
        if case == "request_write_timeout":
            clock[0] = 28.0
        input_bytes.extend(chunk)
        if input_bytes.endswith(b"\n"):
            requested = input_bytes.decode().strip()
            requests.append(requested)
            input_bytes.clear()
            kind, body = objects[requested]
            header = f"{requested} {kind} {len(body)}\n".encode()
            if case == "missing_object":
                header, body = (requested + " missing\n").encode(), b""
            elif case == "malformed_header":
                header = f"{requested} {kind} 01\n".encode()
            elif case == "wrong_oid":
                header = f"{'f' * 40} {kind} {len(body)}\n".encode()
            elif case == "wrong_kind":
                header = f"{requested} tree {len(body)}\n".encode()
            elif case == "truncated_body":
                body = body[:-1]
            elif case == "hash_mismatch":
                body = b"X" + body[1:]
            wire.extend(header + body)
            if case != "truncated_body":
                wire.extend(b"X" if case == "wrong_separator" else b"\n")
            if case == "trailing_stdout":
                wire.extend(b"X")
        return len(chunk)

    def read(fd: int, size: int) -> bytes:
        assert fd == 102
        count = min(size, 3, len(wire))
        raw = bytes(wire[:count])
        del wire[:count]
        if case == "body_timeout" and raw == b"\n" and not wire:
            clock[0] = 28.0
        return raw

    class Selector:
        def __init__(self) -> None:
            self.fd, self.event = 0, 0

        def register(self, fd: int, event: int) -> None:
            self.fd, self.event = fd, event

        def unregister(self, fd: int) -> None:
            assert fd == self.fd

        def select(self, timeout: float) -> list[Any]:
            assert 0 < timeout <= 30
            stopped = case == "header_timeout" and self.fd == 102
            if stopped:
                clock[0] = 28.0
                return []
            return [(SimpleNamespace(fd=self.fd), self.event)]

        def close(self) -> None:
            close_attempts.append("selector")

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(os, "set_blocking", lambda *args: None)
    monkeypatch.setattr(os, "read", read)
    monkeypatch.setattr(os, "write", write)
    monkeypatch.setattr(selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(sources, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    if case == "primary_cleanup_failure_preserved":
        with pytest.raises(LookupError, match="^primary$"):
            with sources._WorkspaceObjects(reader) as stream:
                assert stream.object(blob_id, "blob") == blob
                raise LookupError("primary")
    elif case == "partial_stream_success":
        with sources._WorkspaceObjects(reader) as stream:
            assert stream.object(blob_id, "blob") == blob
    elif case in {"graph_cumulative_cap", "pending_duplicate_count_preserved"}:
        value = core.seal({**original, "head_sha": commit_id, "tree_hash": tree_id})
        with pytest.raises(core.LearningError, match="LEARNING_SOURCE_INVALID"):
            verify_workspace(reader, value)
        if case == "pending_duplicate_count_preserved":
            assert requests == [commit_id, tree_id]
    else:
        with pytest.raises(core.LearningError, match="LEARNING_SOURCE_INVALID"):
            with sources._WorkspaceObjects(reader) as stream:
                stream.object(blob_id, "blob")
                if case == "reserved_cleanup_window_boundary":
                    clock[0] = 30.0
                    stream.object(blob_id, "blob")
        if case == "reserved_cleanup_window_boundary":
            assert requests == [blob_id]  # Expired prior window was not revived.
    assert len(starts) == 1
    assert starts[0][0] == ["git", "cat-file", "--batch"]
    assert starts[0][1]["stderr"] == subprocess.DEVNULL
    assert {"stdin", "stdout", "selector"}.issubset(close_attempts)
    assert child.poll() is not None and all(0 <= value <= 30 for value in waits)
