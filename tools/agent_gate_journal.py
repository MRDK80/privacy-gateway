"""Private bounded evidence store; no authority or verification from stdout."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, BinaryIO

EVENT_LIMIT = 16 * 1024
SNAPSHOT_LIMIT = 32 * 1024 * 1024
JOURNAL_LIMIT = 256 * 1024 * 1024
TOTAL_LIMIT = 384 * 1024 * 1024
MAX_EVENTS = 100_000
ZERO = "0" * 64


class GateBlocked(RuntimeError):
    """Evidence is incomplete or cannot safely be persisted (fixed code only)."""


def encode(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def decode(data: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise GateBlocked("STORE_DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise GateBlocked("STORE_NONFINITE_JSON")

    value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise GateBlocked("STORE_JSON_SHAPE")
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def _read(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK")
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise GateBlocked("STORE_FILE_INVALID")
        if info.st_uid != getattr(os, "getuid")() or stat.S_IMODE(info.st_mode) & 0o077:
            raise GateBlocked("STORE_FILE_PERMISSIONS")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise GateBlocked("STORE_FILE_TOO_LARGE")
        return data
    finally:
        os.close(fd)


class Journal:
    """One writer, immutable commit receipts, complete-chain recovery."""

    def __init__(self, root: Path) -> None:
        if os.name != "posix":
            raise GateBlocked("UNSUPPORTED_PLATFORM")
        import fcntl

        self.root = root
        self.events: list[dict[str, Any]] = []
        self.last_snapshot: dict[str, Any] | None = None
        self.sequence = 0
        self.frontier = ZERO
        self.frontiers = [ZERO]
        self.offset = 0
        self.generation = 0
        self.poisoned = False
        self.closed = False
        self._used: int | None = None
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != getattr(os, "getuid")():
            raise GateBlocked("STORE_DIRECTORY_INVALID")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise GateBlocked("STORE_DIRECTORY_PERMISSIONS")
        # Existing lock only: open/recovery cannot manufacture missing history.
        self.lock_fd = os.open(root / "lock", os.O_RDWR | getattr(os, "O_NOFOLLOW"))
        try:
            lock_info = os.fstat(self.lock_fd)
            if (
                not stat.S_ISREG(lock_info.st_mode)
                or lock_info.st_uid != getattr(os, "getuid")()
                or stat.S_IMODE(lock_info.st_mode) != 0o600
            ):
                raise GateBlocked("STORE_LOCK_INVALID")
            getattr(fcntl, "flock")(
                self.lock_fd, getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB")
            )
        except BaseException:
            os.close(self.lock_fd)
            raise
        self.stream: BinaryIO | None = None

    @classmethod
    def create(cls, root: Path, genesis: dict[str, Any]) -> Journal:
        if os.name != "posix":
            raise GateBlocked("UNSUPPORTED_PLATFORM")
        try:
            root.mkdir(mode=0o700, parents=False, exist_ok=False)
            fd = os.open(root / "lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.fsync(fd)
            os.close(fd)
            store = cls(root)
            try:
                store._immutable(
                    "genesis.json", encode({"version": 1, "launch": genesis})
                )
                store._immutable("journal.jsonl", b"")
                store.genesis = {"version": 1, "launch": genesis}
                store.stream = store._open_journal()
                return store
            except BaseException:
                store.close()
                raise
        except (OSError, ValueError) as error:
            raise GateBlocked("STORE_CREATE_FAILED") from error

    @classmethod
    def open(cls, root: Path) -> Journal:
        try:
            store = cls(root)
            try:
                store._recover()
                store.stream = store._open_journal()
                return store
            except BaseException:
                store.close()
                raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise GateBlocked("STORE_RECOVERY_FAILED") from error

    def _open_journal(self) -> BinaryIO:
        fd = os.open(
            self.root / "journal.jsonl",
            os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW"),
        )
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise GateBlocked("STORE_JOURNAL_INVALID")
        return os.fdopen(fd, "ab", buffering=0)

    def _sync_directory(self) -> None:
        fd = os.open(
            self.root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY") | getattr(os, "O_NOFOLLOW"),
        )
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _capacity(self, extra: int) -> None:
        if self._used is None:
            total = 0
            with os.scandir(self.root) as entries:
                for index, entry in enumerate(entries):
                    if index >= MAX_EVENTS + 10:
                        raise GateBlocked("STORE_ENTRY_LIMIT")
                    info = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        raise GateBlocked("STORE_ENTRY_INVALID")
                    total += info.st_size
                    if total > TOTAL_LIMIT:
                        raise GateBlocked("STORE_TOTAL_LIMIT")
            self._used = total
        if self._used + extra > TOTAL_LIMIT:
            raise GateBlocked("STORE_TOTAL_LIMIT")

    def _immutable(self, name: str, data: bytes) -> None:
        self._capacity(len(data))
        fd = os.open(
            self.root / name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW"),
            0o600,
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        assert self._used is not None
        self._used += len(data)
        self._sync_directory()

    def append(self, event: dict[str, Any]) -> None:
        if self.poisoned or self.closed or self.stream is None:
            raise GateBlocked("STORE_NOT_WRITABLE")
        record = {
            "sequence": self.sequence + 1,
            "previous": self.frontier,
            "genesis": digest(self.genesis),
            "event": event,
        }
        data = encode(record) + b"\n"
        if len(data) > EVENT_LIMIT or self.sequence >= MAX_EVENTS:
            raise GateBlocked("STORE_EVENT_LIMIT")
        if self.offset + len(data) > JOURNAL_LIMIT:
            raise GateBlocked("STORE_JOURNAL_LIMIT")
        commit: dict[str, Any] = {
            "sequence": self.sequence + 1,
            "offset": self.offset + len(data),
            "hash": hashlib.sha256(data).hexdigest(),
        }
        self._capacity(len(data) + len(encode(commit)))
        try:
            if self.stream.write(data) != len(data):
                raise GateBlocked("STORE_SHORT_WRITE")
            assert self._used is not None
            self._used += len(data)
            os.fsync(self.stream.fileno())
            self._immutable(f"commit-{self.sequence + 1:08d}.json", encode(commit))
        except BaseException:
            self.poisoned = True
            raise
        self.sequence += 1
        self.offset += len(data)
        self.frontier = commit["hash"]
        self.frontiers.append(self.frontier)
        self.events.append(json.loads(encode(event)))

    def checkpoint(self, state: dict[str, Any]) -> None:
        if self.poisoned or self.closed:
            raise GateBlocked("STORE_NOT_WRITABLE")
        value = {
            "generation": self.generation + 1,
            "sequence": self.sequence,
            "frontier": self.frontier,
            "genesis": digest(self.genesis),
            "state": state,
        }
        value["checksum"] = digest(value)
        data = encode(value)
        if len(data) > SNAPSHOT_LIMIT:
            raise GateBlocked("STORE_SNAPSHOT_LIMIT")
        # Immutable generations avoid a mutable latest pointer or overwrite.
        try:
            self._immutable("snapshot.tmp", data)
            target = self.root / f"snapshot-{self.generation + 1:08d}.json"
            if target.exists():
                raise GateBlocked("STORE_SNAPSHOT_EXISTS")
            os.rename(self.root / "snapshot.tmp", target)
            self._sync_directory()
            self.generation += 1
            self.last_snapshot = value
            for old in sorted(self.root.glob("snapshot-*.json"))[:-2]:
                size = old.stat().st_size
                old.unlink()
                assert self._used is not None
                self._used -= size
            self._sync_directory()
        except BaseException:
            self.poisoned = True
            raise

    def _recover(self) -> None:
        self._capacity(0)
        allowed = {"genesis.json", "journal.jsonl", "lock", "snapshot.tmp"}
        for p in self.root.iterdir():
            if p.name not in allowed and not (
                p.name.startswith(("commit-", "snapshot-"))
                and len(p.name.split("-")[-1]) == 13
                and p.name.split("-")[-1][:-5].isdigit()
                and p.suffix == ".json"
            ):
                raise GateBlocked("STORE_UNKNOWN_ENTRY")
        self.genesis = decode(_read(self.root / "genesis.json", SNAPSHOT_LIMIT))
        if (
            type(self.genesis.get("version")) is not int
            or self.genesis.get("version") != 1
        ):
            raise GateBlocked("STORE_VERSION")
        data = _read(self.root / "journal.jsonl", JOURNAL_LIMIT)
        commits = sorted(self.root.glob("commit-*.json"))
        lines = data.splitlines(keepends=True)
        if len(lines) != len(commits) or len(lines) > MAX_EVENTS:
            raise GateBlocked("STORE_COMMITTED_HISTORY_MISSING")
        frontiers = [ZERO]
        for index, (line, path) in enumerate(zip(lines, commits, strict=True), 1):
            if len(line) > EVENT_LIMIT or not line.endswith(b"\n"):
                raise GateBlocked("STORE_TORN_EVENT")
            if path.name != f"commit-{index:08d}.json":
                raise GateBlocked("STORE_COMMIT_GAP")
            record = decode(line)
            commit = decode(_read(path, EVENT_LIMIT))
            self.offset += len(line)
            hashed = hashlib.sha256(line).hexdigest()
            if type(record.get("sequence")) is not int:
                raise GateBlocked("STORE_SEQUENCE_TYPE")
            if (
                type(commit.get("sequence")) is not int
                or type(commit.get("offset")) is not int
            ):
                raise GateBlocked("STORE_COMMIT_TYPE")
            if record != {
                "sequence": index,
                "previous": self.frontier,
                "genesis": digest(self.genesis),
                "event": record.get("event"),
            }:
                raise GateBlocked("STORE_CHAIN_INVALID")
            if commit != {"sequence": index, "offset": self.offset, "hash": hashed}:
                raise GateBlocked("STORE_COMMIT_INVALID")
            if not isinstance(record["event"], dict):
                raise GateBlocked("STORE_EVENT_INVALID")
            self.events.append(record["event"])
            self.frontier = hashed
            self.sequence = index
            frontiers.append(hashed)
        self.frontiers = frontiers
        for path in sorted(self.root.glob("snapshot-*.json")):
            snap = decode(_read(path, SNAPSHOT_LIMIT))
            checksum = snap.pop("checksum")
            sequence = snap["sequence"]
            if digest(snap) != checksum or type(sequence) is not int:
                raise GateBlocked("STORE_SNAPSHOT_INVALID")
            if (
                not 0 <= sequence <= self.sequence
                or snap["frontier"] != frontiers[sequence]
            ):
                raise GateBlocked("STORE_SNAPSHOT_FRONTIER")
            if snap["genesis"] != digest(self.genesis):
                raise GateBlocked("STORE_SNAPSHOT_GENESIS")
            generation = snap["generation"]
            if (
                type(generation) is not int
                or path.name != f"snapshot-{generation:08d}.json"
            ):
                raise GateBlocked("STORE_SNAPSHOT_GENERATION")
            self.generation = generation
            snap["checksum"] = checksum
            self.last_snapshot = snap

        # Only this uncommitted snapshot temp is discardable, and only after
        # validating ALL committed journal/receipt/snapshot history above.
        temporary = self.root / "snapshot.tmp"
        if temporary.exists():
            info = temporary.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != getattr(os, "getuid")()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > SNAPSHOT_LIMIT
            ):
                raise GateBlocked("STORE_TEMP_INVALID")
            temporary.unlink()
            assert self._used is not None
            self._used -= info.st_size
            self._sync_directory()

    @property
    def used_bytes(self) -> int:
        self._capacity(0)
        assert self._used is not None
        return self._used

    def close(self) -> None:
        if not self.closed:
            if self.stream is not None:
                self.stream.close()
            os.close(self.lock_fd)
            self.closed = True

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
