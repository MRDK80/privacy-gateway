"""Synthetic navigation/protocol fixtures; no real launch-profile enrollment."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
from tools import repository_map as nav

POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor contract")


def data_for(files: dict[str, bytes]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for path, raw in files.items():
        records, nodes = nav.extract(path, raw)
        result[path] = {"sha256": nav.digest(raw), "nodes": nodes, "records": records}
    return {"files": result, "errors": [], "generation": "synthetic"}


def test_ast_metadata_never_executes_or_exports_values(tmp_path: Path) -> None:
    sentinel = tmp_path / "never-created"
    source = (
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).touch()\n"
        "class Example:\n"
        "    async def method(self, value: 'ANNOTATION_SENTINEL' = 'DEFAULT_SENTINEL',"
        " /, *, named=None, **kwargs) -> 'RETURN_SENTINEL':\n"
        "        '''DOCSTRING_SENTINEL'''\n"
        "        body = 'BODY_SENTINEL'\n"
        "        return dependency.run(value)\n"
    )
    records, nodes = nav.extract("src/example.py", source.encode())
    serialized = nav.encoded(records)
    for private in (b"ANNOTATION_SENTINEL", b"DEFAULT_SENTINEL", b"RETURN_SENTINEL",
                    b"DOCSTRING_SENTINEL", b"BODY_SENTINEL", str(sentinel).encode()):
        assert private not in serialized
    assert not sentinel.exists()
    assert nodes > len(records)
    method = next(x for x in records if x["name"] == "method")
    assert method["scope"] == "example.Example"
    assert method["asynchronous"] is True
    assert method["parameters"][1] == {
        "name": "value", "kind": "posonly", "default": True, "annotation": True,
    }
    assert nav.validate_records(records)
    calls = [x for x in records if x["kind"] == "reference"]
    assert all(x["resolution"] == "unresolved" for x in calls)


def test_duplicates_nested_relative_and_conditional_imports() -> None:
    data = data_for({"src/package/consumer.py": b"""
from .dependency import run as execute
if enabled:
    import conditional
async def duplicate():
    def nested():
        execute()
def duplicate():
    getattr(object, 'DYNAMIC_SENTINEL')()
""", "tests/test_dependency.py": b"pass\n"})
    rows = nav.query(data, "symbol", "duplicate")["rows"]
    assert len(rows) == 2 and rows[0]["line"] != rows[1]["line"]
    imports = nav.query(data, "import-users", "package.dependency")["rows"]
    assert len(imports) == 1 and imports[0]["alias"] == "execute"
    assert nav.query(data, "import-users", "conditional")["rows"] == []
    assert nav.query(data, "test-candidates", "dependency")["rows"][0][
        "basis"
    ] == "filename-candidate"
    assert b"DYNAMIC_SENTINEL" not in nav.encoded(data)


@pytest.mark.parametrize("path", ["../src/a.py", "/src/a.py", "src//a.py",
                                  "src/./a.py", "src/a\\b.py", "src/a\n.py"])
def test_invalid_source_paths(path: str) -> None:
    with pytest.raises(nav.MapError, match="UNSAFE_PATH"):
        nav.eligible(path)


@pytest.mark.parametrize("path", ["src/.env/a.py", "tools/installed/a.py",
                                  "tests/artifacts/a.py", "docs/a.py", "src/a.txt"])
def test_private_even_when_tracked(path: str) -> None:
    assert not nav.eligible(path)


def test_query_limits_and_partial_results() -> None:
    data = data_for({"src/example.py": b"\n".join(
        f"def function_{i}(): pass".encode() for i in range(80)
    )})
    result = nav.query(data, "prefix", "function_", 2)
    assert len(result["rows"]) == 2 and result["has_more"]
    assert len(nav.encoded(result)) <= 16384
    data["errors"] = [{"file": "src/bad.py", "reason": "SYNTAX_ERROR"}]
    assert nav.query(data, "symbol", "absent")["status"] == "INCOMPLETE"
    for text in ("x" * 129, "literal.*", ""):
        with pytest.raises(nav.MapError, match="INVALID_QUERY"):
            nav.query(data, "symbol", text)
    with pytest.raises(nav.MapError, match="INVALID_QUERY"):
        nav.query(data, "symbol", "x", 65)


def test_syntax_and_node_limits_are_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(nav.MapError, match="SYNTAX_ERROR"):
        nav.extract("src/example.py", b"def SECRET_SOURCE(")
    monkeypatch.setattr(nav, "NODE_CAP", 2)
    with pytest.raises(nav.MapError, match="NODE_LIMIT"):
        nav.extract("src/example.py", b"value = 1")


def test_windows_or_missing_primitives_refuse_before_touch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(nav, "platform_supported", lambda: False)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("filesystem touched")

    monkeypatch.setattr(os, "open", forbidden)
    assert nav.main(["--root", "unread"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "INCOMPLETE", "reason": "UNSUPPORTED_FILESYSTEM", "rows": [],
    }


def test_actual_windows_refuses() -> None:
    if os.name == "nt":
        assert not nav.platform_supported()


def record(path: str = "src/example.py", mode: str = "100644") -> bytes:
    return f"{mode} {'a' * 40} 0\t{path}\0".encode()


def test_incremental_nul_protocol_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = nav.StageParser()
    raw = record() + record("docs/noneligible.py")
    for byte in raw:
        parser.feed(bytes([byte]), float("inf"))
    assert parser.paths == {"src/example.py"} and parser.records == 2
    for raw in (record(mode="040000"), record(mode="120000"), b"bad\0"):
        with pytest.raises(nav.MapError, match="ENUM_INVALID"):
            nav.StageParser().feed(raw, float("inf"))
    with pytest.raises(nav.MapError, match="ENUM_LIMIT"):
        nav.StageParser().feed(b"x" * 4097, float("inf"))
    with pytest.raises(nav.MapError, match="ENUM_TIMEOUT"):
        nav.StageParser().feed(record(), 0)
    monkeypatch.setattr(nav, "FILE_COUNT", 1)
    with pytest.raises(nav.MapError, match="ENUM_LIMIT"):
        nav.StageParser().feed(record() + record("src/second.py"), float("inf"))


@POSIX
def test_anchored_paths_and_private_files(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "src").mkdir()
    source = root / "src/a.py"
    source.write_bytes(b"value = 1")
    directory = nav.Directory(str(root))
    try:
        assert directory.read("src/a.py", 100, deadline=float("inf"))[0] == b"value = 1"
        source.unlink()
        source.symlink_to(tmp_path / "outside")
        with pytest.raises(OSError):
            directory.read("src/a.py", 100, deadline=float("inf"))
        source.unlink()
        source.write_bytes(b"value = 1")
        os.link(source, root / "second")
        with pytest.raises(nav.MapError, match="UNSAFE_PATH"):
            directory.read("src/a.py", 100, deadline=float("inf"))
    finally:
        directory.close()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(OSError):
        nav.Directory(str(alias))


@POSIX
def test_parent_replacement_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src/a.py").write_bytes(b"original")
    directory = nav.Directory(str(root))
    read = os.read
    changed = False

    def swapping_read(fd: int, size: int) -> bytes:
        nonlocal changed
        result = read(fd, size)
        if result and not changed:
            changed = True
            (root / "src").rename(root / "old")
            (root / "src").mkdir()
            (root / "src/a.py").write_bytes(b"different")
        return result

    monkeypatch.setattr(os, "read", swapping_read)
    try:
        with pytest.raises(nav.MapError, match="CHANGED_DURING_READ"):
            directory.read("src/a.py", 100, deadline=float("inf"))
    finally:
        directory.close()


@POSIX
def test_profile_requires_private_hash_bound_file(tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    value = {"version": 1, "git_version": "2.43.0", "executable": "/usr/bin/git",
             "executable_sha256": "a" * 64,
             "executable_uid": int(getattr(os, "getuid")()),
             "build": "synthetic-test-only", "admin": "/tmp/synthetic/.git",
             "common": "/tmp/synthetic/.git", "config_sha256": None,
             "worktree_config_sha256": None,
             "config_review": "no-includes-redirects-promisor-sha1",
             "shared_index_mtime": True}
    raw = nav.encoded(value)
    profile.write_bytes(raw)
    profile.chmod(0o600)
    assert nav.Profile.load(str(profile), nav.digest(raw), float("inf")).build == (
        "synthetic-test-only"
    )
    profile.chmod(0o644)
    with pytest.raises(nav.MapError, match="UNSAFE_CACHE"):
        nav.Profile.load(str(profile), nav.digest(raw), float("inf"))
    profile.chmod(0o600)
    value["git_version"] = "unknown"
    raw = nav.encoded(value)
    profile.write_bytes(raw)
    with pytest.raises(nav.MapError, match="UNSUPPORTED_GIT"):
        nav.Profile.load(str(profile), nav.digest(raw), float("inf"))


def fixture_git(binary: Path, *args: str) -> bytes:
    env = {
        "LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
        "GIT_ATTR_NOSYSTEM": "1", "GIT_ALLOW_PROTOCOL": "",
        "GIT_PROTOCOL_FROM_USER": "0",
    }
    return subprocess.run(
        [str(binary), "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never",
         "-c", "user.name=Synthetic", "-c", "user.email=user@example.com", *args],
        env=env, check=True, capture_output=True, timeout=10,
    ).stdout


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> tuple[Path, nav.Profile]:
    if os.name != "posix":
        pytest.skip("POSIX launch contract")
    binary = Path(shutil.which("git") or "/missing-git").resolve(strict=True)
    # Ownership failures remain errors; unsupported versions are coverage gaps.
    assert binary.stat().st_uid in {0, int(getattr(os, "getuid")())}, (
        "trusted Git owner unavailable"
    )
    if fixture_git(binary, "--version").decode().strip() != "git version 2.43.0":
        pytest.skip("UNSUPPORTED_GIT: positive integration requires Git2.43.0")
    root = tmp_path / "repository"
    root.mkdir()
    fixture_git(binary, "init", "-q", str(root))
    (root / "src").mkdir()
    (root / "src/example.py").write_text("def original(): pass\n")
    fixture_git(binary, "-C", str(root), "add", "src/example.py")
    admin = root / ".git"
    config = admin / "config"
    config.write_bytes(b"[core]\nrepositoryformatversion = 0\nbare = false\n")
    profile = nav.Profile(str(binary), nav.digest(binary.read_bytes()),
                          binary.stat().st_uid, "synthetic-test-only",
                          str(admin), str(admin), nav.digest(config.read_bytes()), None)
    return root, profile


@POSIX
def test_fixed_launch_env_and_dirty_index(
    synthetic_repo: tuple[Path, nav.Profile], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, profile = synthetic_repo
    tree = nav.Directory(str(root))
    launch = nav.Launch(tree, profile, time.monotonic() + 30)
    try:
        launch.verify()
        monkeypatch.setenv("GIT_TRACE", "/never-write")
        monkeypatch.setenv("GIT_CONFIG_COUNT", "100")
        argv, env = launch.command()
        assert "HOME" not in env and "XDG_CONFIG_HOME" not in env
        assert "GIT_TRACE" not in env and "GIT_CONFIG_COUNT" not in env
        assert "core.fsmonitor=false" in argv and "core.hooksPath=/dev/null" in argv
        assert env["GIT_INDEX_FILE"] == str(root / ".git/index")
        paths, counts = nav.membership(launch)
        assert paths == ["src/example.py"] and counts["stdout"] > 0
    finally:
        launch.close()
        tree.close()


@POSIX
def test_config_changes_refuse_before_spawn(
    synthetic_repo: tuple[Path, nav.Profile]
) -> None:
    root, profile = synthetic_repo
    (root / ".git/config").write_bytes(b"[include]\npath=/untrusted\n")
    tree = nav.Directory(str(root))
    launch = nav.Launch(tree, profile, time.monotonic() + 30)
    try:
        with pytest.raises(nav.MapError, match="UNSUPPORTED_GIT"):
            launch.verify()
    finally:
        launch.close()
        tree.close()


@POSIX
def test_cache_refresh_hashes_dirty_same_mtime_and_removes_stale(
    synthetic_repo: tuple[Path, nav.Profile], tmp_path: Path
) -> None:
    root, profile = synthetic_repo
    cache_path = tmp_path / "cache"
    cache_path.mkdir(mode=0o700)
    tree = nav.Directory(str(root))
    cache = nav.Cache(str(cache_path), tree)
    cache.lock()
    try:
        for content, expected_parses in (("def original(): pass\n", 1),
                                         ("def original(): pass\n", 0),
                                         ("def replaced(): pass\n", 1)):
            source = root / "src/example.py"
            old = source.stat()
            source.write_text(content)
            os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
            launch = nav.Launch(tree, profile, time.monotonic() + 30)
            try:
                launch.verify()
                data, stats = nav.refresh(
                    tree, cache, launch, [], time.monotonic() + 30
                )
                assert stats["parsed_files"] == expected_parses
                assert stats["source_reads"] == 1
            finally:
                launch.close()
        assert nav.query(data, "symbol", "original")["rows"] == []
        source.write_text("def invalid(")
        launch = nav.Launch(tree, profile, time.monotonic() + 30)
        try:
            launch.verify()
            data, _ = nav.refresh(tree, cache, launch, [], time.monotonic() + 30)
            assert data["errors"][0]["file"] == "src/example.py"
            assert data["errors"][0]["reason"] == "SYNTAX_ERROR"
            assert data["errors"][0]["line"] == 1
            assert isinstance(data["errors"][0]["offset"], int)
            assert nav.query(data, "symbol", "replaced")["rows"] == []
        finally:
            launch.close()
    finally:
        cache.unlock()
        cache.close()
        tree.close()


@POSIX
def test_cache_busy_corrupt_and_private_mode(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    tree = nav.Directory(str(root))
    cache = nav.Cache(str(private), tree)
    cache.lock()
    try:
        with pytest.raises(nav.MapError, match="BUSY"):
            cache.lock()
        target = private / "map.json"
        target.write_bytes(b"not-json")
        target.chmod(0o600)
        with pytest.raises(nav.MapError, match="CORRUPT_CACHE"):
            cache.load("key", float("inf"))
        target.chmod(0o644)
        with pytest.raises(nav.MapError, match="UNSAFE_CACHE"):
            cache.load("key", float("inf"))
    finally:
        cache.unlock()
        cache.close()
        tree.close()


def fake_child() -> nav.Child:
    process = cast(Any, type("Process", (), {"pid": 123, "returncode": None})())
    return nav.Child(process, 0, (1,), (2,), (3,))


@POSIX
@pytest.mark.parametrize("lost", [False, True])
def test_no_signal_after_reap_or_lost(
    monkeypatch: pytest.MonkeyPatch, lost: bool
) -> None:
    child = fake_child()
    signals: list[tuple[int, int]] = []

    def waitpid(pid: int, flags: int) -> tuple[int, int]:
        if lost:
            raise ChildProcessError
        return pid, 0

    monkeypatch.setattr(os, "waitpid", waitpid)
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append((pid, sig)))
    assert child.stop() is (not lost)
    child.stop()
    assert signals == []


@POSIX
def test_term_reaped_before_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    child = fake_child()
    signals: list[tuple[int, int]] = []

    def waitpid(pid: int, flags: int) -> tuple[int, int]:
        return (pid, 0) if signals else (0, 0)

    monkeypatch.setattr(os, "waitpid", waitpid)
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append((pid, sig)))
    assert child.stop()
    assert signals == [(123, signal.SIGTERM)]


@POSIX
def test_escalation_requires_still_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    child = fake_child()
    signals: list[tuple[int, int]] = []
    clock = [0.0]

    def now() -> float:
        clock[0] += 0.2
        return clock[0]

    def waitpid(pid: int, flags: int) -> tuple[int, int]:
        return (pid, 0) if len(signals) == 2 else (0, 0)

    monkeypatch.setattr(time, "monotonic", now)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    monkeypatch.setattr(os, "waitpid", waitpid)
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append((pid, sig)))
    assert child.stop()
    assert signals == [(123, signal.SIGTERM), (123, getattr(signal, "SIGKILL"))]


@POSIX
def test_linked_worktrees_use_own_dirty_indexes(
    synthetic_repo: tuple[Path, nav.Profile], tmp_path: Path
) -> None:
    root, ordinary = synthetic_repo
    binary = Path(ordinary.executable)
    fixture_git(binary, "-C", str(root), "commit", "-qm", "synthetic")
    for number in (1, 2):
        linked = tmp_path / f"linked-{number}"
        fixture_git(binary, "-C", str(root), "worktree", "add", "-qb",
                    f"synthetic-{number}", str(linked))
        path = linked / f"src/only_{number}.py"
        path.write_text("def local(): pass\n")
        fixture_git(binary, "-C", str(linked), "add", str(path))
        pointer = (linked / ".git").read_text()
        admin = pointer.removeprefix("gitdir: ").strip()
        profile = nav.Profile(
            ordinary.executable, ordinary.executable_sha256, ordinary.executable_uid,
            "synthetic-test-only", admin, ordinary.common,
            ordinary.config_sha256, None,
        )
        tree = nav.Directory(str(linked))
        launch = nav.Launch(tree, profile, time.monotonic() + 30)
        try:
            launch.verify()
            paths, _ = nav.membership(launch)
            assert paths == ["src/example.py", f"src/only_{number}.py"]
            (Path(admin) / "gitdir").write_text("/wrong/backlink\n")
            with pytest.raises(nav.MapError, match="CHANGED_DURING_READ"):
                launch.recheck()
        finally:
            launch.close()
            tree.close()


@POSIX
def test_fsmonitor_hook_disabled_and_no_source_or_index_content_write(
    synthetic_repo: tuple[Path, nav.Profile], tmp_path: Path
) -> None:
    root, original = synthetic_repo
    sentinel = tmp_path / "hook-ran"
    hook = tmp_path / "synthetic-hook"
    hook.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n")
    hook.chmod(0o700)
    config = root / ".git/config"
    config.write_text(config.read_text() + f"\tfsmonitor = {hook}\n")
    profile = nav.Profile(
        original.executable, original.executable_sha256, original.executable_uid,
        "synthetic-test-only-reviewed-hook", original.admin, original.common,
        nav.digest(config.read_bytes()), None,
    )
    before = {name: (root / name).read_bytes() for name in (
        "src/example.py", ".git/index", ".git/config",
    )}
    tree = nav.Directory(str(root))
    launch = nav.Launch(tree, profile, time.monotonic() + 30)
    try:
        launch.verify()
        assert nav.membership(launch)[0] == ["src/example.py"]
        assert not sentinel.exists()
        assert before == {name: (root / name).read_bytes() for name in before}
    finally:
        launch.close()
        tree.close()


class ProtocolLaunch:
    """Only exercises membership transport, never approves a Git profile."""

    def __init__(self, directory: Path, body: str) -> None:
        self.root = cast(Any, type("Root", (), {"path": str(directory)})())
        self.admin = cast(Any, type("Admin", (), {
            "snapshot": lambda self, name: (1,), "root_identity": (2,),
        })())
        self.binary_mark = (3,)
        self.deadline = time.monotonic() + 10
        self.body = body

    def recheck(self) -> None:
        pass

    def command(self) -> tuple[list[str], dict[str, str]]:
        import sys

        return [sys.executable, "-B", "-c", self.body], {"LC_ALL": "C"}


@POSIX
@pytest.mark.parametrize("body,reason", [
    ("import os;os.write(1,b'partial')", "ENUM_INVALID"),
    ("raise SystemExit(2)", "ENUM_FAILED"),
    ("import os;os.write(2,b'x'*16385)", "ENUM_LIMIT"),
    ("import os;os.write(1,b'x'*4097)", "ENUM_LIMIT"),
])
def test_transport_discards_incomplete_membership(
    tmp_path: Path, body: str, reason: str
) -> None:
    launch = cast(nav.Launch, ProtocolLaunch(tmp_path, body))
    with pytest.raises(nav.MapError, match=reason):
        nav.membership(launch)


@POSIX
def test_transport_complete_buffered_exit(tmp_path: Path) -> None:
    raw = record()
    launch = cast(nav.Launch, ProtocolLaunch(
        tmp_path, f"import os;os.write(1,{raw!r});os.write(2,b'private discarded')",
    ))
    paths, counts = nav.membership(launch)
    assert paths == ["src/example.py"]
    assert counts == {"stdout": len(raw), "stderr": len(b"private discarded")}


@POSIX
def test_no_launch_without_sole_reaper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(threading, "active_count", lambda: 2)
    with pytest.raises(nav.MapError, match="CHILD_STOP_UNKNOWN"):
        nav.membership(cast(nav.Launch, ProtocolLaunch(tmp_path, "pass")))


@POSIX
def test_unconfirmed_reap_reports_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    child = fake_child()
    clock = [0.0]
    signals: list[tuple[int, int]] = []

    def now() -> float:
        clock[0] += 0.2
        return clock[0]

    monkeypatch.setattr(time, "monotonic", now)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    monkeypatch.setattr(os, "waitpid", lambda pid, flags: (0, 0))
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append((pid, sig)))
    assert not child.stop()
    assert child in nav._UNREAPED
    assert signals == [(123, signal.SIGTERM), (123, getattr(signal, "SIGKILL"))]


@POSIX
def test_oversize_and_untracked_bounds(
    synthetic_repo: tuple[Path, nav.Profile], tmp_path: Path
) -> None:
    root, profile = synthetic_repo
    private = tmp_path / "cache"
    private.mkdir(mode=0o700)
    tree = nav.Directory(str(root))
    cache = nav.Cache(str(private), tree)
    launch = nav.Launch(tree, profile, time.monotonic() + 30)
    cache.lock()
    try:
        with pytest.raises(nav.MapError, match="INPUT_LIMIT"):
            nav.refresh(tree, cache, launch, [f"src/{i}.py" for i in range(33)],
                        time.monotonic() + 30)
        (root / "src/example.py").write_bytes(b"x" * (nav.FILE_CAP + 1))
        launch.verify()
        data, _ = nav.refresh(tree, cache, launch, [], time.monotonic() + 30)
        assert data["errors"] == [{
            "file": "src/example.py", "reason": "INPUT_LIMIT",
            "line": None, "offset": None,
        }]
    finally:
        launch.close()
        cache.unlock()
        cache.close()
        tree.close()


@POSIX
def test_pipe_inheritor_unknown_without_signaling_reaped_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reads: list[Any] = []
    writes: list[int] = []
    for _ in range(2):
        read_fd, write_fd = os.pipe()
        reads.append(os.fdopen(read_fd, "rb"))
        writes.append(write_fd)
    process = cast(Any, type("Process", (), {
        "pid": 123, "stdout": reads[0], "stderr": reads[1], "returncode": None,
    })())
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(os, "waitpid", lambda pid, flags: (pid, 0))
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append((pid, sig)))
    launch = ProtocolLaunch(tmp_path, "unused")
    launch.deadline = time.monotonic() + 0.05
    try:
        with pytest.raises(nav.MapError, match="CHILD_STOP_UNKNOWN"):
            nav.membership(cast(nav.Launch, launch))
        assert signals == []
    finally:
        for descriptor in writes:
            os.close(descriptor)
        for stream in reads:
            stream.close()


@POSIX
def test_cache_failed_replace_retains_old_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    private = tmp_path / "cache"
    private.mkdir(mode=0o700)
    tree = nav.Directory(str(root))
    cache = nav.Cache(str(private), tree)
    cache.lock()
    target = private / "map.json"
    target.write_bytes(b"old-generation")
    target.chmod(0o600)

    def fail_replace(*args: Any, **kwargs: Any) -> None:
        raise OSError("synthetic")

    monkeypatch.setattr(os, "replace", fail_replace)
    try:
        with pytest.raises(OSError):
            cache.save({"synthetic": True}, time.monotonic() + 30)
        assert target.read_bytes() == b"old-generation"
        assert not (private / "map.tmp").exists()
    finally:
        cache.unlock()
        cache.close()
        tree.close()


@POSIX
def test_explicit_untracked_delete_rename_and_membership_failure_no_stale_rows(
    synthetic_repo: tuple[Path, nav.Profile], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, profile = synthetic_repo
    private = tmp_path / "cache"
    private.mkdir(mode=0o700)
    tree = nav.Directory(str(root))
    cache = nav.Cache(str(private), tree)
    cache.lock()
    try:
        extra = root / "src/extra.py"
        extra.write_text("def extra(): pass\n")
        launch = nav.Launch(tree, profile, time.monotonic() + 30)
        try:
            launch.verify()
            data, _ = nav.refresh(tree, cache, launch, ["src/extra.py"],
                                  time.monotonic() + 30)
            assert nav.query(data, "symbol", "extra")["rows"]
        finally:
            launch.close()
        (root / "src/example.py").rename(root / "src/renamed.py")
        fixture_git(Path(profile.executable), "-C", str(root), "add", "-A")
        (root / "src/renamed.py").unlink()
        launch = nav.Launch(tree, profile, time.monotonic() + 30)
        try:
            launch.verify()
            data, _ = nav.refresh(tree, cache, launch, ["src/extra.py"],
                                  time.monotonic() + 30)
            assert "src/example.py" not in data["files"]
            assert nav.query(data, "symbol", "original")["rows"] == []
            assert data["errors"][0]["reason"] == "SOURCE_MISSING"

            def failed_membership(value: nav.Launch) -> Any:
                raise nav.MapError("ENUM_FAILED")

            monkeypatch.setattr(nav, "membership", failed_membership)
            with pytest.raises(nav.MapError, match="ENUM_FAILED"):
                nav.refresh(tree, cache, launch, ["src/extra.py"],
                            time.monotonic() + 30)
        finally:
            launch.close()
    finally:
        cache.unlock()
        cache.close()
        tree.close()


def test_stage_record_cap_counts_noneligible_entries() -> None:
    parser = nav.StageParser()
    with pytest.raises(nav.MapError, match="ENUM_LIMIT"):
        parser.feed(record("docs/noneligible.py") * 8193, float("inf"))
    assert parser.records == 8193


@POSIX
def test_stdout_cap_transport(tmp_path: Path) -> None:
    body = "import os;raw=b'100644 '+b'a'*40+b' 0\\tdocs/'+b'x'*100+b'.py\\0';" \
           "os.write(1,raw*8000)"
    with pytest.raises(nav.MapError, match="ENUM_LIMIT"):
        nav.membership(cast(nav.Launch, ProtocolLaunch(tmp_path, body)))


@POSIX
def test_split_index_only_allows_admin_freshness_touch(
    synthetic_repo: tuple[Path, nav.Profile]
) -> None:
    root, profile = synthetic_repo
    fixture_git(Path(profile.executable), "-C", str(root),
                "update-index", "--split-index")
    shared = list((root / ".git").glob("sharedindex.*"))
    assert shared
    protected = {str(path): path.read_bytes() for path in [
        root / "src/example.py", root / ".git/index", root / ".git/config", *shared,
    ]}
    tree = nav.Directory(str(root))
    launch = nav.Launch(tree, profile, time.monotonic() + 30)
    try:
        launch.verify()
        assert nav.membership(launch)[0] == ["src/example.py"]
        assert protected == {path: Path(path).read_bytes() for path in protected}
        assert all(path.parent == root / ".git" for path in shared)
        # Shared-index mtime may change; its immutability is deliberately not asserted.
    finally:
        launch.close()
        tree.close()


@POSIX
def test_daemon_fsmonitor_mode_is_overridden(
    synthetic_repo: tuple[Path, nav.Profile]
) -> None:
    root, original = synthetic_repo
    config = root / ".git/config"
    config.write_text(config.read_text() + "\tfsmonitor = true\n")
    profile = nav.Profile(
        original.executable, original.executable_sha256, original.executable_uid,
        "synthetic-test-only", original.admin, original.common,
        nav.digest(config.read_bytes()), None,
    )
    tree = nav.Directory(str(root))
    launch = nav.Launch(tree, profile, time.monotonic() + 30)
    try:
        launch.verify()
        assert "core.fsmonitor=false" in launch.command()[0]
        assert nav.membership(launch)[0] == ["src/example.py"]
        assert not list((root / ".git").glob("fsmonitor--daemon*"))
    finally:
        launch.close()
        tree.close()


@POSIX
@pytest.mark.parametrize("operation", ["read", "snapshot"])
def test_fifo_is_rejected_without_waiting_for_writer(
    tmp_path: Path, operation: str
) -> None:
    import sys

    root = tmp_path / "fifo-root"
    root.mkdir()
    getattr(os, "mkfifo")(root / "entry")
    body = """
import sys,time
from tools import repository_map as nav
root=nav.Directory(sys.argv[1])
try:
    try:
        if sys.argv[2]=='read':
            root.read('entry', 16, deadline=time.monotonic()+0.1)
        else:
            root.snapshot('entry')
    except nav.MapError as error:
        assert error.code=='UNSAFE_PATH'
        print('REJECTED')
    else:
        raise AssertionError('accepted nonregular file')
finally:
    root.close()
"""
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-c", body, str(root), operation],
            cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=1,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("FIFO admission blocked until isolated-child timeout")
    assert result.returncode == 0
    assert result.stdout == "REJECTED\n"


@POSIX
def test_rejected_files_still_consume_aggregate_ast_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "node-root"
    root.mkdir()
    (root / "src").mkdir()
    paths = ["src/" + letter * 129 + ".py" for letter in "abc"]
    for path in paths:
        (root / path).write_bytes(b"value = 1\n" * 100)
    tree = nav.Directory(str(root))

    class CacheStub:
        def load(self, key: str, deadline: float) -> None:
            return None

        def save(self, data: dict[str, Any], deadline: float) -> None:
            pass

    class LaunchStub:
        profile = cast(Any, type("Profile", (), {
            "executable_sha256": "a" * 64, "config_sha256": None,
            "worktree_config_sha256": None,
        })())

        def recheck(self) -> None:
            pass

    monkeypatch.setattr(nav, "NODE_CAP", 1000)
    monkeypatch.setattr(nav, "membership", lambda launch: (
        paths, {"stdout": 0, "stderr": 0},
    ))
    try:
        with pytest.raises(nav.MapError, match="NODE_LIMIT"):
            nav.refresh(tree, cast(nav.Cache, CacheStub()),
                        cast(nav.Launch, LaunchStub()), [], time.monotonic() + 30)
    finally:
        tree.close()


def test_unsupported_git_profile_refused_before_launch_or_source_index_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = {
        "version": 1, "git_version": "2.55.0",
        "executable": "/synthetic/git", "executable_sha256": "a" * 64,
        "executable_uid": 0, "build": "unsupported-synthetic-test-only",
        "admin": "/synthetic/admin", "common": "/synthetic/admin",
        "config_sha256": None, "worktree_config_sha256": None,
        "config_review": "no-includes-redirects-promisor-sha1",
        "shared_index_mtime": True,
    }
    raw = nav.encoded(value)
    reads: list[str] = []

    def profile_bytes(
        path: str, cap: int, deadline: float, *, private: bool = False
    ) -> bytes:
        assert path == "/synthetic-profile.json" and cap == 16384 and private
        reads.append(path)
        return raw

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("unsupported profile reached launch/source filesystem")

    # Supply private-profile bytes at the existing I/O seam. Real Profile.load
    # must reject version before opening source/admin/index or launching Git.
    # This unit case does not claim a real private-file or positive Git proof.
    monkeypatch.setattr(nav, "read_absolute", profile_bytes)
    monkeypatch.setattr(nav, "Directory", forbidden)
    monkeypatch.setattr(nav, "Launch", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(nav.MapError, match="^UNSUPPORTED_GIT$"):
        nav.Profile.load("/synthetic-profile.json", nav.digest(raw),
                         time.monotonic() + 30)
    assert reads == ["/synthetic-profile.json"]
