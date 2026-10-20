"""Private, bounded static navigation; never import indexed modules.

Standalone POSIX tool. Profiles require separate external owner approval.
See ADR-377; the cache and results never authorize an action.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, NoReturn

FILE_CAP = 256 * 1024
SOURCE_CAP = 8 * 1024 * 1024
CACHE_CAP = 16 * 1024 * 1024
FILE_COUNT = 512
NODE_CAP = 1_000_000
ENTRY_CAP = 100_000
ROOTS = {"src", "tools", "tests"}
DENIED = {
    "venv", "env", "node_modules", "build", "dist", "__pycache__", "cache",
    "log", "logs", "artifacts", "installed", "generated",
}
FORMAT = "repository-map-1"
HEX = re.compile(r"[0-9a-f]{64}\Z")
_UNREAPED: list[Child] = []


class MapError(Exception):
    """A fixed code only, never raw paths, source or transport diagnostics."""

    def __init__(
        self, code: str, *, line: int | None = None, offset: int | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.line = line if type(line) is int and 1 <= line <= FILE_CAP else None
        self.offset = (offset if type(offset) is int
                       and 0 <= offset <= FILE_CAP else None)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def platform_supported() -> bool:
    return (
        os.name == "posix" and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_NONBLOCK")
        and hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd and os.unlink in os.supports_dir_fd
    )


def components(value: str, *, absolute: bool = False) -> list[str]:
    if not isinstance(value, str) or not value or "\\" in value:
        raise MapError("UNSAFE_PATH")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise MapError("UNSAFE_PATH")
    if value.startswith("/") != absolute:
        raise MapError("UNSAFE_PATH")
    parts = value[1:].split("/") if absolute else value.split("/")
    if any(x in {"", ".", ".."} for x in parts):
        raise MapError("UNSAFE_PATH")
    return parts


def eligible(value: str) -> bool:
    parts = components(value)
    return (
        parts[0] in ROOTS and value.endswith(".py")
        and all(not x.startswith(".") and x not in DENIED for x in parts)
    )


def identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def check_ancestor(info: os.stat_result) -> None:
    if info.st_uid not in {0, int(getattr(os, "getuid")())}:
        raise MapError("UNSAFE_PATH")
    if info.st_mode & 0o022 and not (
        info.st_uid == 0 and info.st_mode & stat.S_ISVTX
    ):
        raise MapError("UNSAFE_PATH")


class Directory:
    """Anchored directory traversal; callers never resolve before walking."""

    def __init__(self, path: str, *, private: bool = False) -> None:
        parts = components(path, absolute=True)
        fd = os.open(
            "/", os.O_RDONLY | int(getattr(os, "O_DIRECTORY"))
            | int(getattr(os, "O_NOFOLLOW")),
        )
        try:
            for part in parts:
                if private:
                    check_ancestor(os.fstat(fd))
                next_fd = os.open(
                    part, os.O_RDONLY | int(getattr(os, "O_DIRECTORY"))
                    | int(getattr(os, "O_NOFOLLOW")), dir_fd=fd
                )
                os.close(fd)
                fd = next_fd
            info = os.fstat(fd)
            if private and (info.st_uid != int(getattr(os, "getuid")())
                            or stat.S_IMODE(info.st_mode) != 0o700):
                raise MapError("UNSAFE_CACHE")
            if str(Path(path).resolve(strict=True)) != path:
                raise MapError("UNSAFE_PATH")
            self.fd = fd
            self.path = path
            self.root_identity = (info.st_dev, info.st_ino)
            self.private = private
        except BaseException:
            os.close(fd)
            raise

    def close(self) -> None:
        os.close(self.fd)

    def recheck(self) -> None:
        fresh = Directory(self.path, private=self.private)
        try:
            if fresh.root_identity != self.root_identity:
                raise MapError("CHANGED_DURING_READ")
        finally:
            fresh.close()

    def parent(self, relative: str) -> tuple[int, str]:
        parts = components(relative)
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                next_fd = os.open(
                    part, os.O_RDONLY | int(getattr(os, "O_DIRECTORY"))
                    | int(getattr(os, "O_NOFOLLOW")), dir_fd=fd
                )
                os.close(fd)
                fd = next_fd
            return fd, parts[-1]
        except BaseException:
            os.close(fd)
            raise

    def read(
        self, relative: str, cap: int, *, deadline: float,
        uid: int | None = None, mode: int | None = None,
        source_budget: list[int] | None = None,
    ) -> tuple[bytes, tuple[int, ...]]:
        parent, name = self.parent(relative)
        fd = -1
        try:
            fd = os.open(
                name, os.O_RDONLY | int(getattr(os, "O_NOFOLLOW"))
                | int(getattr(os, "O_NONBLOCK")), dir_fd=parent
            )
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise MapError("UNSAFE_PATH")
            if uid is not None and before.st_uid != uid:
                raise MapError("UNSAFE_OWNER")
            if mode is not None and stat.S_IMODE(before.st_mode) != mode:
                raise MapError("UNSAFE_CACHE")
            if before.st_size > cap:
                raise MapError("INPUT_LIMIT")
            chunks: list[bytes] = []
            size = 0
            while True:
                if time.monotonic() >= deadline:
                    raise MapError("OPERATION_TIMEOUT")
                chunk = os.read(fd, min(65536, cap + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if source_budget is not None:
                    source_budget[0] += len(chunk)
                    if source_budget[0] > SOURCE_CAP:
                        raise MapError("SOURCE_LIMIT")
                if size > cap:
                    raise MapError("INPUT_LIMIT")
                chunks.append(chunk)
            after = os.fstat(fd)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (identity(before) != identity(after)
                    or identity(after) != identity(current)):
                raise MapError("CHANGED_DURING_READ")
            self.recheck()
            if self.snapshot(relative) != identity(after):
                raise MapError("CHANGED_DURING_READ")
            return b"".join(chunks), identity(after)
        finally:
            if fd >= 0:
                os.close(fd)
            os.close(parent)

    def snapshot(self, relative: str) -> tuple[int, ...]:
        parent, name = self.parent(relative)
        try:
            fd = os.open(
                name, os.O_RDONLY | int(getattr(os, "O_NOFOLLOW"))
                | int(getattr(os, "O_NONBLOCK")), dir_fd=parent
            )
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise MapError("UNSAFE_PATH")
                return identity(info)
            finally:
                os.close(fd)
        finally:
            os.close(parent)


def read_absolute(
    path: str, cap: int, deadline: float, *, private: bool = False
) -> bytes:
    parts = components(path, absolute=True)
    parent_path = "/" + "/".join(parts[:-1])
    if len(parts) == 1:
        raise MapError("UNSAFE_PATH")
    directory = Directory(parent_path)
    try:
        return directory.read(
            parts[-1], cap, deadline=deadline,
            uid=int(getattr(os, "getuid")()) if private else None,
            mode=0o600 if private else None,
        )[0]
    finally:
        directory.close()


@dataclass(frozen=True, repr=False)
class Profile:
    executable: str
    executable_sha256: str
    executable_uid: int
    build: str
    admin: str
    common: str
    config_sha256: str | None
    worktree_config_sha256: str | None

    @classmethod
    def load(cls, path: str, approved_digest: str, deadline: float) -> Profile:
        if not HEX.fullmatch(approved_digest):
            raise MapError("UNSUPPORTED_GIT")
        raw = read_absolute(path, 16384, deadline, private=True)
        if digest(raw) != approved_digest:
            raise MapError("UNSUPPORTED_GIT")
        # Enrollment is an external human decision; this loader never creates it.
        value = json.loads(raw)
        keys = {
            "version", "git_version", "executable", "executable_sha256",
            "executable_uid", "build", "admin", "common", "config_sha256",
            "worktree_config_sha256", "config_review", "shared_index_mtime",
        }
        if not isinstance(value, dict) or set(value) != keys:
            raise MapError("UNSUPPORTED_GIT")
        if (value["version"] != 1 or value["git_version"] != "2.43.0"
                or value["config_review"] != "no-includes-redirects-promisor-sha1"
                or value["shared_index_mtime"] is not True):
            raise MapError("UNSUPPORTED_GIT")
        for key in ("executable", "admin", "common"):
            components(value[key], absolute=True)
        if (type(value["executable_uid"]) is not int
                or value["executable_uid"] not in {0, int(getattr(os, "getuid")())}
                or not isinstance(value["build"], str)
                or not 1 <= len(value["build"]) <= 128):
            raise MapError("UNSUPPORTED_GIT")
        for key in ("executable_sha256", "config_sha256", "worktree_config_sha256"):
            item = value[key]
            if item is None and key != "executable_sha256":
                continue
            if not isinstance(item, str) or not HEX.fullmatch(item):
                raise MapError("UNSUPPORTED_GIT")
        return cls(*(value[k] for k in (
            "executable", "executable_sha256", "executable_uid", "build",
            "admin", "common", "config_sha256", "worktree_config_sha256",
        )))


class Launch:
    """One validated, profile-bound Git launch; no discovery or enrollment."""

    def __init__(self, root: Directory, profile: Profile, deadline: float) -> None:
        self.root = root
        self.profile = profile
        self.deadline = deadline
        self.admin = Directory(profile.admin)
        try:
            self.common = Directory(profile.common)
        except BaseException:
            self.admin.close()
            raise
        self.watches: list[tuple[Directory, str, tuple[int, ...] | None]] = []

    def close(self) -> None:
        self.admin.close()
        self.common.close()

    def watch(self, tree: Directory, name: str, cap: int) -> bytes:
        value, mark = tree.read(name, cap, deadline=self.deadline)
        self.watches.append((tree, name, mark))
        return value

    def config(self, tree: Directory, name: str, expected: str | None) -> None:
        if expected is None:
            try:
                tree.snapshot(name)
            except FileNotFoundError:
                self.watches.append((tree, name, None))
                return
            raise MapError("UNSUPPORTED_GIT")
        if digest(self.watch(tree, name, 65536)) != expected:
            raise MapError("UNSUPPORTED_GIT")

    def verify(self) -> None:
        p = self.profile
        if p.executable_uid not in {0, int(getattr(os, "getuid")())}:
            raise MapError("UNSUPPORTED_GIT")
        parts = components(p.executable, absolute=True)
        binary = Directory("/" + "/".join(parts[:-1]))
        try:
            raw, mark = binary.read(
                parts[-1], 8 * 1024 * 1024, deadline=self.deadline,
                uid=p.executable_uid,
            )
            if (mark[2] & 0o022 or not mark[2] & 0o111
                    or digest(raw) != p.executable_sha256):
                raise MapError("UNSUPPORTED_GIT")
            self.binary_mark = mark
        finally:
            binary.close()
        git_mark = os.stat(".git", dir_fd=self.root.fd, follow_symlinks=False)
        if stat.S_ISDIR(git_mark.st_mode):
            if p.admin != self.root.path + "/.git" or p.common != p.admin:
                raise MapError("UNSUPPORTED_GIT")
        elif stat.S_ISREG(git_mark.st_mode):
            expected = ("gitdir: " + p.admin + "\n").encode()
            if self.watch(self.root, ".git", 4096) != expected:
                raise MapError("UNSUPPORTED_GIT")
            prefix = p.common + "/worktrees/"
            if not p.admin.startswith(prefix):
                raise MapError("UNSUPPORTED_GIT")
            tail = p.admin[len(prefix):]
            if len(components(tail)) != 1:
                raise MapError("UNSUPPORTED_GIT")
            if self.watch(self.admin, "commondir", 4096) != b"../..\n":
                raise MapError("UNSUPPORTED_GIT")
            backlink = (self.root.path + "/.git\n").encode()
            if self.watch(self.admin, "gitdir", 4096) != backlink:
                raise MapError("UNSUPPORTED_GIT")
        else:
            raise MapError("UNSUPPORTED_GIT")
        self.config(self.common, "config", p.config_sha256)
        self.config(self.admin, "config.worktree", p.worktree_config_sha256)
        self.watches.append((self.admin, "index", self.admin.snapshot("index")))
        self.recheck()

    def recheck(self) -> None:
        self.root.recheck()
        self.admin.recheck()
        self.common.recheck()
        for tree, name, expected in self.watches:
            try:
                current = tree.snapshot(name)
            except FileNotFoundError:
                current = None
            if current != expected:
                raise MapError("CHANGED_DURING_READ")
        parts = components(self.profile.executable, absolute=True)
        tree = Directory("/" + "/".join(parts[:-1]))
        try:
            if tree.snapshot(parts[-1]) != self.binary_mark:
                raise MapError("CHANGED_DURING_READ")
        finally:
            tree.close()

    def command(self) -> tuple[list[str], dict[str, str]]:
        p = self.profile
        argv = [p.executable, "--no-pager", "--no-optional-locks",
                "--no-replace-objects",
                "--git-dir=" + p.admin, "--work-tree=" + self.root.path]
        for item in (
            "core.fsmonitor=false", "core.hooksPath=/dev/null",
            "core.untrackedCache=false",
            "core.preloadIndex=false", "core.splitIndex=false",
            "maintenance.auto=false",
            "gc.auto=0", "protocol.allow=never",
        ):
            argv += ["-c", item]
        argv += ["ls-files", "--stage", "--abbrev=40", "--sparse", "-z", "--",
                 ":(top,literal)src/", ":(top,literal)tools/", ":(top,literal)tests/"]
        env = {
            "LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0", "GIT_ATTR_NOSYSTEM": "1",
            "GIT_ALLOW_PROTOCOL": "", "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_COMMON_DIR": p.common, "GIT_INDEX_FILE": p.admin + "/index",
        }
        return argv, env


class StageParser:
    def __init__(self) -> None:
        self.pending = bytearray()
        self.records = 0
        self.paths: set[str] = set()

    def feed(self, chunk: bytes, deadline: float) -> None:
        for byte in chunk:
            if time.monotonic() >= deadline:
                raise MapError("ENUM_TIMEOUT")
            self.pending.append(byte)
            if len(self.pending) > 4096:
                raise MapError("ENUM_LIMIT")
            if byte != 0:
                continue
            self.records += 1
            if self.records > 8192:
                raise MapError("ENUM_LIMIT")
            raw = bytes(self.pending[:-1])
            self.pending.clear()
            match = re.fullmatch(rb"(100644|100755) ([0-9a-f]{40}) 0\t(.+)", raw)
            if match is None:
                raise MapError("ENUM_INVALID")
            try:
                path = match[3].decode("utf-8")
            except UnicodeError:
                raise MapError("ENUM_INVALID") from None
            if eligible(path):
                if path in self.paths:
                    raise MapError("ENUM_INVALID")
                self.paths.add(path)
                if len(self.paths) > FILE_COUNT:
                    raise MapError("ENUM_LIMIT")


class Child:
    """Sole exact-child reaper; no Popen polling/waiting or group signals."""

    def __init__(
        self, process: subprocess.Popen[bytes], spawned: float,
        binary_identity: tuple[int, ...], admin_identity: tuple[int, ...],
        index_identity: tuple[int, ...],
    ) -> None:
        self.process = process
        self.spawned = spawned
        self.binary_identity = binary_identity
        self.admin_identity = admin_identity
        self.index_identity = index_identity
        self.state = "OWNED_UNREAPED"

    def reap(self) -> None:
        if self.state != "OWNED_UNREAPED":
            return
        try:
            pid, status = os.waitpid(self.process.pid, int(getattr(os, "WNOHANG")))
        except (ChildProcessError, OSError):
            self.state = "LOST"
            return
        if pid:
            self.process.returncode = os.waitstatus_to_exitcode(status)
            self.state = "REAPED"

    def stop(self) -> bool:
        self.reap()
        for sig in (signal.SIGTERM, getattr(signal, "SIGKILL")):
            if self.state != "OWNED_UNREAPED":
                break
            try:
                os.kill(self.process.pid, sig)
            except ProcessLookupError:
                pass
            except OSError:
                self.state = "LOST"
                break
            until = time.monotonic() + 0.5
            while time.monotonic() < until and self.state == "OWNED_UNREAPED":
                self.reap()
                if self.state == "OWNED_UNREAPED":
                    time.sleep(0.01)
            self.reap()
        if self.state != "REAPED":
            _UNREAPED.append(self)
        return self.state == "REAPED"


def membership(launch: Launch) -> tuple[list[str], dict[str, int]]:
    if (threading.current_thread() is not threading.main_thread()
            or threading.active_count() != 1):
        raise MapError("CHILD_STOP_UNKNOWN")
    sigchld = getattr(signal, "SIGCHLD", None)
    if sigchld is None:
        raise MapError("CHILD_STOP_UNKNOWN")
    selector = selectors.DefaultSelector()
    try:
        previous = signal.signal(sigchld, signal.SIG_DFL)
    except (OSError, ValueError):
        selector.close()
        raise MapError("CHILD_STOP_UNKNOWN") from None
    child: Child | None = None
    counts = {"stdout": 0, "stderr": 0}
    parser = StageParser()
    deadline = min(launch.deadline, time.monotonic() + 5.0)
    streams: list[IO[bytes]] = []
    try:
        if time.monotonic() >= deadline:
            raise MapError("ENUM_TIMEOUT")
        launch.recheck()
        argv, env = launch.command()
        if time.monotonic() >= deadline:
            raise MapError("ENUM_TIMEOUT")
        index_identity = launch.admin.snapshot("index")
        spawned = time.monotonic()
        if spawned >= deadline:
            raise MapError("ENUM_TIMEOUT")
        process = subprocess.Popen(
            argv, cwd=launch.root.path, env=env, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
            start_new_session=True,
        )
        child = Child(
            process, spawned, launch.binary_mark, launch.admin.root_identity,
            index_identity,
        )
        if process.stdout is None or process.stderr is None:
            raise MapError("ENUM_FAILED")
        streams.extend((process.stdout, process.stderr))
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            getattr(os, "set_blocking")(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map() or child.state == "OWNED_UNREAPED":
            if time.monotonic() >= deadline:
                child.reap()
                if selector.get_map() and child.state == "REAPED":
                    raise MapError("CHILD_STOP_UNKNOWN")
                raise MapError("ENUM_TIMEOUT")
            wait = min(0.05, max(0.0, deadline - time.monotonic()))
            for key, _ in selector.select(wait):
                name = str(key.data)
                cap = 1024 * 1024 if name == "stdout" else 16384
                chunk = os.read(key.fd, min(16384, cap - counts[name] + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                counts[name] += len(chunk)
                if counts[name] > cap:
                    raise MapError("ENUM_LIMIT")
                if name == "stdout":
                    parser.feed(chunk, deadline)
            child.reap()
            if child.state == "LOST":
                raise MapError("CHILD_STOP_UNKNOWN")
        if time.monotonic() >= deadline:
            raise MapError("ENUM_TIMEOUT")
        if parser.pending:
            raise MapError("ENUM_INVALID")
        if process.returncode != 0:
            raise MapError("ENUM_FAILED")
        launch.recheck()
        if time.monotonic() >= deadline:
            raise MapError("ENUM_TIMEOUT")
        return sorted(parser.paths), counts
    except BaseException as error:
        for stream in streams:
            stream.close()
        if child is not None and not child.stop():
            raise MapError("CHILD_STOP_UNKNOWN") from None
        if isinstance(error, (MapError, KeyboardInterrupt, SystemExit)):
            raise
        raise MapError("ENUM_FAILED") from None
    finally:
        selector.close()
        for stream in streams:
            stream.close()
        if child is None or child.state == "REAPED":
            signal.signal(sigchld, previous)


def identifier(value: Any, *, dotted: bool = False) -> bool:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
        return False
    if dotted:
        return all(x.isidentifier() for x in value.split("."))
    return value.isidentifier()


def module_name(path: str) -> str:
    parts = path[:-3].split("/")
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts.pop()
    name = ".".join(parts)
    if not identifier(name, dotted=True):
        raise MapError("UNSAFE_IDENTIFIER")
    return name


def target(node: ast.AST) -> str | None:
    names: list[str] = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    names.append(node.id)
    if len(names) > 4 or any(not identifier(n) for n in names):
        return None
    return ".".join(reversed(names))


class Extractor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.module = module_name(path)
        self.scope: list[str] = []
        self.records: list[dict[str, Any]] = []
        self.conditional = 0

    def add(self, kind: str, name: str, node: ast.AST, **extra: Any) -> None:
        if not identifier(name, dotted=True):
            raise MapError("UNSAFE_IDENTIFIER")
        scope = ".".join([self.module, *self.scope])
        self.records.append({
            "kind": kind, "name": name, "scope": scope,
            "line": getattr(node, "lineno", 1),
            "end": getattr(node, "end_lineno", None) or getattr(node, "lineno", 1),
            **extra,
        })
        if len(self.records) > ENTRY_CAP:
            raise MapError("ENTRY_LIMIT")

    def function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = node.args
        params: list[dict[str, Any]] = []
        positional = [*args.posonlyargs, *args.args]
        required = len(positional) - len(args.defaults)
        for i, arg in enumerate(positional):
            params.append({
                "name": arg.arg,
                "kind": "posonly" if i < len(args.posonlyargs) else "pos",
                "default": i >= required, "annotation": arg.annotation is not None,
            })
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            params.append({"name": arg.arg, "kind": "kwonly",
                           "default": default is not None,
                           "annotation": arg.annotation is not None})
        for optional_arg, kind in ((args.vararg, "vararg"), (args.kwarg, "kwarg")):
            if optional_arg is not None:
                params.append({
                    "name": optional_arg.arg, "kind": kind, "default": False,
                    "annotation": optional_arg.annotation is not None,
                })
        self.add("symbol", node.name, node, symbol_kind="function", parameters=params,
                 asynchronous=isinstance(node, ast.AsyncFunctionDef),
                 returns_annotated=node.returns is not None)
        self.scope.append(node.name)
        # Signatures/decorators/defaults never become call edges or exported values.
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.add("symbol", node.name, node, symbol_kind="class", parameters=[],
                 asynchronous=False, returns_annotated=False)
        self.scope.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            self.add("import", item.name, node,
                     alias=item.asname or item.name.split(".")[0],
                     relative=0, conditional=bool(self.conditional),
                     basis="static-import")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for item in node.names:
            if item.name == "*":
                continue
            name = ".".join(x for x in (node.module, item.name) if x)
            self.add("import", name, node, alias=item.asname or item.name,
                     relative=node.level, conditional=bool(self.conditional),
                     basis="static-import")

    def visit_Call(self, node: ast.Call) -> None:
        name = target(node.func)
        if name is not None:
            self.add("reference", name, node,
                     basis="candidate", resolution="unresolved")
        # Inspect nested calls structurally; never serialize argument values.
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.conditional += 1
        self.generic_visit(node)
        self.conditional -= 1

    def visit_For(self, node: ast.For) -> None:
        self.conditional += 1
        self.generic_visit(node)
        self.conditional -= 1

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.conditional += 1
        self.generic_visit(node)
        self.conditional -= 1

    def visit_While(self, node: ast.While) -> None:
        self.conditional += 1
        self.generic_visit(node)
        self.conditional -= 1

    def visit_Match(self, node: ast.Match) -> None:
        self.conditional += 1
        self.generic_visit(node)
        self.conditional -= 1

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self.conditional += 1
        self.generic_visit(node)
        self.conditional -= 1

    def visit_Try(self, node: ast.Try) -> None:
        self.conditional += 1
        self.generic_visit(node)
        self.conditional -= 1


def extract(
    path: str, raw: bytes, *, node_budget: list[int] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    try:
        tree = ast.parse(raw, filename="<source>")
    except SyntaxError as error:
        raise MapError("SYNTAX_ERROR", line=error.lineno, offset=error.offset) from None
    except (UnicodeError, ValueError):
        raise MapError("SYNTAX_ERROR") from None
    count = 0
    budget = node_budget if node_budget is not None else [0]
    for _ in ast.walk(tree):
        count += 1
        budget[0] += 1
        if budget[0] > NODE_CAP:
            raise MapError("NODE_LIMIT")
    visitor = Extractor(path)
    visitor.visit(tree)
    if not validate_records(visitor.records):
        raise MapError("UNSAFE_IDENTIFIER")
    return visitor.records, count


def validate_records(records: Any) -> bool:
    if not isinstance(records, list) or len(records) > ENTRY_CAP:
        return False
    common = {"kind", "name", "scope", "line", "end"}
    for record in records:
        if not isinstance(record, dict):
            return False
        if (not identifier(record.get("name"), dotted=True)
                or not identifier(record.get("scope"), dotted=True)
                or type(record.get("line")) is not int or record["line"] < 1
                or type(record.get("end")) is not int
                or record["end"] < record["line"]):
            return False
        kind = record.get("kind")
        if kind == "symbol":
            if set(record) != common | {
                "symbol_kind", "parameters", "asynchronous", "returns_annotated",
            }:
                return False
            if (record["symbol_kind"] not in {"function", "class"}
                    or type(record["asynchronous"]) is not bool
                    or type(record["returns_annotated"]) is not bool
                    or not isinstance(record["parameters"], list)
                    or len(record["parameters"]) > 1024):
                return False
            for param in record["parameters"]:
                if (not isinstance(param, dict)
                        or set(param) != {"name", "kind", "default", "annotation"}
                        or not identifier(param["name"])
                        or param["kind"] not in {
                            "posonly", "pos", "kwonly", "vararg", "kwarg"
                        }
                        or type(param["default"]) is not bool
                        or type(param["annotation"]) is not bool):
                    return False
        elif kind == "import":
            if (set(record) != common | {"alias", "relative", "conditional", "basis"}
                    or not identifier(record["alias"])
                    or type(record["relative"]) is not int
                    or not 0 <= record["relative"] <= 128
                    or type(record["conditional"]) is not bool
                    or record["basis"] != "static-import"):
                return False
        elif kind == "reference":
            if (set(record) != common | {"basis", "resolution"}
                    or record["basis"] != "candidate"
                    or record["resolution"] != "unresolved"):
                return False
        else:
            return False
    return True


class Cache:
    def __init__(self, path: str, root: Directory) -> None:
        if path == root.path or path.startswith(root.path + "/"):
            raise MapError("UNSAFE_CACHE")
        self.tree = Directory(path, private=True)
        self.lock_fd: int | None = None
        self.lock_mark: tuple[int, ...] | None = None

    def close(self) -> None:
        self.tree.close()

    def lock(self) -> None:
        try:
            fd = os.open(
                "lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | int(getattr(os, "O_NOFOLLOW")), 0o600, dir_fd=self.tree.fd,
            )
        except FileExistsError:
            raise MapError("BUSY") from None
        getattr(os, "fchmod")(fd, 0o600)
        self.lock_fd = fd
        self.lock_mark = identity(os.fstat(fd))

    def unlock(self) -> None:
        if self.lock_fd is None:
            return
        try:
            if self.tree.snapshot("lock") != self.lock_mark:
                raise MapError("UNSAFE_CACHE")
            self.tree.recheck()
            os.unlink("lock", dir_fd=self.tree.fd)
        finally:
            os.close(self.lock_fd)
            self.lock_fd = None

    def load(self, key: str, deadline: float) -> dict[str, Any] | None:
        try:
            raw, _ = self.tree.read("map.json", CACHE_CAP, deadline=deadline,
                                    uid=int(getattr(os, "getuid")()), mode=0o600)
        except FileNotFoundError:
            return None
        try:
            outer = json.loads(raw)
            if not isinstance(outer, dict) or set(outer) != {"checksum", "data"}:
                raise ValueError
            data = outer["data"]
            if digest(encoded(data)) != outer["checksum"]:
                raise ValueError
            if (not isinstance(data, dict)
                    or set(data) != {"format", "key", "files", "errors", "generation"}
                    or data["format"] != FORMAT
                    or not isinstance(data["files"], dict)
                    or len(data["files"]) > FILE_COUNT
                    or not isinstance(data["errors"], list)):
                raise ValueError
            if data["key"] != key:
                raise MapError("STALE_CACHE")
            for path, record in data["files"].items():
                if (not eligible(path) or not isinstance(record, dict)
                        or set(record) != {"sha256", "nodes", "records"}
                        or not isinstance(record["sha256"], str)
                        or not HEX.fullmatch(record["sha256"])
                        or type(record["nodes"]) is not int
                        or not 0 <= record["nodes"] <= NODE_CAP
                        or not validate_records(record["records"])):
                    raise ValueError
            for error in data["errors"]:
                if (not isinstance(error, dict)
                        or set(error) != {"file", "reason", "line", "offset"}
                        or not eligible(error["file"])
                        or error["reason"] not in {
                            "SYNTAX_ERROR", "UNSAFE_IDENTIFIER", "SOURCE_MISSING",
                            "CHANGED_DURING_READ", "UNSAFE_PATH", "INPUT_LIMIT",
                        }):
                    raise ValueError
                for location in (error["line"], error["offset"]):
                    if location is not None and (type(location) is not int
                                                 or not 0 <= location <= FILE_CAP):
                        raise ValueError
            if len(data["errors"]) > FILE_COUNT:
                raise ValueError
            if (sum(x["nodes"] for x in data["files"].values()) > NODE_CAP
                    or sum(len(x["records"])
                           for x in data["files"].values()) > ENTRY_CAP):
                raise ValueError
            if data["generation"] != digest(encoded({
                "files": data["files"], "errors": data["errors"],
            })):
                raise ValueError
            return dict(data)
        except MapError as error:
            if error.code == "STALE_CACHE":
                raise
            raise MapError("CORRUPT_CACHE") from None
        except (ValueError, TypeError, KeyError):
            raise MapError("CORRUPT_CACHE") from None

    def save(self, data: dict[str, Any], deadline: float) -> None:
        if self.lock_fd is None:
            raise MapError("BUSY")
        self.tree.recheck()
        try:
            self.tree.read("map.json", CACHE_CAP, deadline=deadline,
                           uid=int(getattr(os, "getuid")()), mode=0o600)
        except FileNotFoundError:
            pass
        raw = encoded({"checksum": digest(encoded(data)), "data": data})
        if len(raw) > CACHE_CAP:
            raise MapError("CACHE_LIMIT")
        descriptor = os.open("map.tmp",
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | int(getattr(os, "O_NOFOLLOW")),
                             0o600, dir_fd=self.tree.fd)
        getattr(os, "fchmod")(descriptor, 0o600)
        mark = os.fstat(descriptor)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
            written, current = self.tree.read(
                "map.tmp", CACHE_CAP, deadline=deadline,
                uid=int(getattr(os, "getuid")()), mode=0o600,
            )
            if (written != raw
                    or (current[0], current[1]) != (mark.st_dev, mark.st_ino)):
                raise MapError("UNSAFE_CACHE")
            self.tree.recheck()
            # Existing targets must have passed load's regular/owner/mode checks.
            os.replace("map.tmp", "map.json",
                       src_dir_fd=self.tree.fd, dst_dir_fd=self.tree.fd)
        except BaseException:
            # Cleanup only the exact temporary created by this invocation.
            try:
                current = self.tree.snapshot("map.tmp")
                if (current[0], current[1]) == (mark.st_dev, mark.st_ino):
                    os.unlink("map.tmp", dir_fd=self.tree.fd)
            except (OSError, MapError):
                pass
            raise


def refresh(
    root: Directory, cache: Cache, launch: Launch, untracked: list[str],
    deadline: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if len(untracked) > 32 or len(set(untracked)) != len(untracked):
        raise MapError("INPUT_LIMIT")
    if any(not eligible(x) for x in untracked):
        raise MapError("UNSAFE_PATH")
    key = digest(encoded({
        "root": root.path, "identity": root.root_identity, "format": FORMAT,
        "python": list(sys.version_info[:2]), "untracked": sorted(untracked),
        "profile": {"binary": launch.profile.executable_sha256,
                    "config": launch.profile.config_sha256,
                    "worktree_config": launch.profile.worktree_config_sha256},
    }))
    old = cache.load(key, deadline)
    paths, pipe_counts = membership(launch)
    paths = sorted(set(paths) | set(untracked))
    if len(paths) > FILE_COUNT or len({x.casefold() for x in paths}) != len(paths):
        raise MapError("INPUT_LIMIT")
    files: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    watches: dict[str, tuple[int, ...]] = {}
    stats: dict[str, Any] = {
        "source_reads": 0, "source_bytes": 0, "parsed_files": 0,
        "metadata_stdout_bytes": pipe_counts["stdout"],
        "metadata_stderr_bytes": pipe_counts["stderr"], "git_internal_io": "UNKNOWN",
    }
    nodes = entries = 0
    source_budget = [0]
    node_budget = [0]
    stats["source_read_start_monotonic"] = time.monotonic()
    for path in paths:
        if time.monotonic() >= deadline:
            raise MapError("OPERATION_TIMEOUT")
        try:
            stats["source_reads"] += 1
            raw, mark = root.read(
                path, min(FILE_CAP, SOURCE_CAP - source_budget[0]),
                deadline=deadline, source_budget=source_budget,
            )
            stats["source_bytes"] = source_budget[0]
            fingerprint = digest(raw)
            previous = old["files"].get(path) if old else None
            if previous is not None and previous["sha256"] == fingerprint:
                record = previous
                node_budget[0] += record["nodes"]
                if node_budget[0] > NODE_CAP:
                    raise MapError("NODE_LIMIT")
            else:
                records, count = extract(path, raw, node_budget=node_budget)
                record = {"sha256": fingerprint, "nodes": count, "records": records}
                stats["parsed_files"] += 1
            nodes += record["nodes"]
            entries += len(record["records"])
            if nodes > NODE_CAP or entries > ENTRY_CAP:
                raise MapError("NODE_LIMIT" if nodes > NODE_CAP else "ENTRY_LIMIT")
            files[path] = record
            watches[path] = mark
        except FileNotFoundError:
            errors.append({"file": path, "reason": "SOURCE_MISSING",
                           "line": None, "offset": None})
        except MapError as error:
            if error.code not in {
                "SYNTAX_ERROR", "UNSAFE_IDENTIFIER", "CHANGED_DURING_READ",
                "UNSAFE_PATH", "INPUT_LIMIT",
            }:
                raise
            errors.append({"file": path, "reason": error.code,
                           "line": error.line, "offset": error.offset})
        finally:
            stats["source_bytes"] = source_budget[0]
    for path, mark in watches.items():
        if root.snapshot(path) != mark:
            raise MapError("CHANGED_DURING_READ")
    root.recheck()
    launch.recheck()
    stats["source_read_end_monotonic"] = time.monotonic()
    if time.monotonic() >= deadline:
        raise MapError("OPERATION_TIMEOUT")
    generation = digest(encoded({"files": files, "errors": errors}))
    data = {"format": FORMAT, "key": key, "files": files, "errors": errors,
            "generation": generation}
    cache.save(data, deadline)
    return data, stats


def import_name(path: str, record: dict[str, Any]) -> str | None:
    if record["relative"] == 0:
        return str(record["name"])
    module = module_name(path).split(".")
    package = module if path.endswith("/__init__.py") else module[:-1]
    level = record["relative"]
    if level > len(package):
        return None
    return ".".join([*package[:len(package) - level + 1], record["name"]])


def query(
    data: dict[str, Any], kind: str, text: str, limit: int = 64,
    *, deadline: float = float("inf"),
) -> dict[str, Any]:
    if not isinstance(text, str) or not text or len(text.encode("utf-8")) > 128:
        raise MapError("INVALID_QUERY")
    if type(limit) is not int or not 1 <= limit <= 64:
        raise MapError("INVALID_QUERY")
    if kind == "file":
        if not eligible(text):
            raise MapError("INVALID_QUERY")
    elif not identifier(text, dotted=True):
        raise MapError("INVALID_QUERY")
    rows: list[dict[str, Any]] = []
    for path, item in sorted(data["files"].items()):
        module = module_name(path)
        if (kind == "test-candidates" and path.startswith("tests/")
                and path.rsplit("/", 1)[-1] == "test_" + text.split(".")[-1] + ".py"):
            rows.append({"file": path, "module": module, "sha256": item["sha256"],
                         "kind": "test-file", "name": module, "line": 1,
                         "basis": "filename-candidate"})
        for record in item["records"]:
            if time.monotonic() >= deadline:
                raise MapError("OPERATION_TIMEOUT")
            qualified = record["scope"] + "." + record["name"]
            match = False
            basis = "static-structure"
            if kind == "file":
                match = path == text
            elif kind in {"symbol", "prefix"}:
                match = record["kind"] == "symbol" and (
                    (qualified == text or record["name"] == text) if kind == "symbol"
                    else qualified.startswith(text) or record["name"].startswith(text)
                )
            elif kind in {"import-users", "test-candidates"}:
                imported = (import_name(path, record)
                            if record["kind"] == "import" else None)
                match = (imported is not None and not record["conditional"]
                         and (imported == text or imported.startswith(text + ".")))
                basis = "static-import"
                if kind == "test-candidates":
                    match = match and path.startswith("tests/")
            elif kind == "references":
                match = record["kind"] == "reference" and record["name"] == text
                basis = "unresolved-call-candidate"
            else:
                raise MapError("INVALID_QUERY")
            if match:
                rows.append({"file": path, "module": module, "sha256": item["sha256"],
                             "basis": basis, **record})
    rows.sort(key=lambda x: (x["file"], x["line"], x["kind"], x["name"]))
    response: dict[str, Any] = {
        "status": "INCOMPLETE" if data["errors"] else "COMPLETE",
        "generation": data["generation"], "rows": [], "has_more": False,
        "qualification": "static navigation only; reread source before action",
        "errors": data["errors"],
    }
    for row in rows:
        if len(response["rows"]) >= limit:
            response["has_more"] = True
            break
        response["rows"].append(row)
        if len(encoded(response)) > 15000:
            response["rows"].pop()
            response["has_more"] = True
            break
    if len(encoded(response)) > 15000:
        raise MapError("RESPONSE_LIMIT")
    return response


class Arguments(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise MapError("INVALID_INPUT")


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    if not platform_supported():
        print(json.dumps({"status": "INCOMPLETE",
                          "reason": "UNSUPPORTED_FILESYSTEM", "rows": []}))
        return 2
    root = cache = launch = None
    response: dict[str, Any]
    try:
        parser = Arguments(description="Private static metadata; see ADR-377.")
        parser.add_argument("--root", required=True)
        parser.add_argument("--cache", required=True)
        parser.add_argument("--profile", required=True)
        parser.add_argument("--approved-profile-digest", required=True)
        parser.add_argument("--include-untracked", action="append", default=[])
        parser.add_argument("--kind", required=True)
        parser.add_argument("--query", required=True)
        parser.add_argument("--limit", type=int, default=64)
        args = parser.parse_args(argv)
        deadline = started + 30.0
        root = Directory(args.root)
        cache = Cache(args.cache, root)
        cache.lock()
        profile = Profile.load(args.profile, args.approved_profile_digest, deadline)
        launch = Launch(root, profile, deadline)
        launch.verify()
        data, stats = refresh(root, cache, launch, args.include_untracked, deadline)
        response = query(data, args.kind, args.query, args.limit, deadline=deadline)
        response["metrics"] = {**stats, "elapsed_seconds": time.monotonic() - started}
        if len(encoded(response)) > 16384:
            raise MapError("RESPONSE_LIMIT")
    except MapError as error:
        response = {"status": "INCOMPLETE", "reason": error.code, "rows": []}
    except (OSError, ValueError, TypeError, KeyError, RecursionError, MemoryError):
        response = {"status": "INCOMPLETE", "reason": "UNAVAILABLE", "rows": []}
    finally:
        if launch is not None:
            launch.close()
        if cache is not None:
            try:
                cache.unlock()
            except (OSError, MapError):
                response = {"status": "INCOMPLETE",
                            "reason": "UNSAFE_CACHE", "rows": []}
            cache.close()
        if root is not None:
            root.close()
    print(json.dumps(response, sort_keys=True))
    return 0 if response["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
