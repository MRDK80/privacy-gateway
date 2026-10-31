"""Private positive-call Settings bijection; zero infrastructure is unsupported."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from tools import agent_epic_runtime_cli as guards
from tools import agent_learning as core
from tools.agent_continuation import canonical, digest


@dataclass(frozen=True, slots=True, repr=False)
class _SettingsCallSource:
    task: int
    run_id: str
    role: str
    ordinal: int
    native_digest: str
    source_bytes: bytes


@dataclass(frozen=True, slots=True, repr=False)
class SettingsCommitment:
    calls: int
    source_digest: str


def _require(value: bool) -> None:
    if not value:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")


def _settings_call_source(
    task: int, run_id: str, role: str, ordinal: int,
    native_digest: str, source: dict[str, Any],
) -> _SettingsCallSource:
    """Representation only; original successful source branch is authority."""
    core.integer(task, 2147483647, 1)
    core.hash_value(run_id, size=32)
    _require(role in {"executor", "controller"})
    core.integer(ordinal, 3, 1)
    core.hash_value(native_digest)
    _require(set(source) == {"receipt", "terminal"})
    return _SettingsCallSource(
        task, run_id, role, ordinal, native_digest, canonical(source)
    )


def _expected_settings(
    sources: tuple[_SettingsCallSource, ...],
) -> dict[str, _SettingsCallSource]:
    _require(bool(sources))  # All N=0 lifecycle states remain unsupported.
    keys = [(s.task, s.run_id, s.role, s.ordinal) for s in sources]
    natives = [s.native_digest for s in sources]
    _require(len(set(keys)) == len(keys) and len(set(natives)) == len(natives))
    return {"receipt-" + s.native_digest + ".json": s for s in sources}


def _match_source(source: _SettingsCallSource, value: dict[str, Any]) -> None:
    _require(canonical(value) == source.source_bytes)


def _names(directory: Path, expected: set[str]) -> None:
    entries = list(islice(directory.iterdir(), len(expected) + 1))
    _require(len(entries) == len(expected) and {p.name for p in entries} == expected)


def _infrastructure(directory: Path, root: Path) -> Path:
    guards.secure_directory(directory, root)
    ownership = directory / "ownership"
    guards.secure_directory(ownership, root)
    lock = ownership / "epic-loop.lock"
    info = lock.lstat()
    _require(
        stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_uid == ownership.stat().st_uid
    )
    return ownership


def _namespace(reader: Any, sources: tuple[_SettingsCallSource, ...]) -> None:
    expected = _expected_settings(sources)
    directory = reader.seed / "role-settings"
    reader.guard()
    ownership = _infrastructure(directory, reader.root)
    # No task replay while this shared ownership is held. Preconditions precede
    # RunnerLock's original operational mkdir/chmod/open protocol.
    with reader.ownership(ownership):
        _require(_infrastructure(directory, reader.root) == ownership)
        _names(directory, set(expected) | {"ownership"})
        _names(ownership, {"epic-loop.lock"})
        for name, source in expected.items():
            reader.guard()
            _match_source(source, reader._json(directory / name))
        _names(directory, set(expected) | {"ownership"})
        _names(ownership, {"epic-loop.lock"})
        _infrastructure(directory, reader.root)
        from tools.agent_epic_loop import RunnerLock

        RunnerLock.current_owned(ownership).assert_owned(ownership)
        reader.guard()


def _read_settings(historical: Any, session: Any) -> SettingsCommitment:
    from tools.agent_final_learning_binding_source import (
        _read_workflow_settings_snapshot,
    )
    from tools.agent_final_learning_domain import (
        _FinalPlanningReader,
        _PreAggregatePlanningReader,
    )

    _require(type(historical) in {_FinalPlanningReader, _PreAggregatePlanningReader})
    _require(historical.session is session)
    reader = historical.reader
    universe = reader._source_tasks()
    original = _read_workflow_settings_snapshot(reader, session)
    sources = original[3]
    try:
        _namespace(reader, sources)
    except OSError:
        raise core.LearningError("LEARNING_STORAGE_UNSAFE") from None
    # Original task/continuation replay occurs after shared lock release.
    _require(_read_workflow_settings_snapshot(reader, session) == original)
    _require(reader._source_tasks() == universe)
    historical._guard()
    return SettingsCommitment(
        len(sources), digest([
            [s.task, s.run_id, s.role, s.ordinal, s.native_digest,
             _digest_from_bytes(s.source_bytes)] for s in sources
        ])
    )


def _digest_from_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def read_settings_commitment_owned(session: Any) -> SettingsCommitment:
    from tools.agent_final_learning_domain import _FinalPlanningReader

    reader = _FinalPlanningReader(session)
    try:
        return _read_settings(reader, session)
    finally:
        reader.reader.close()


def read_preaggregate_settings_commitment_owned(session: Any) -> SettingsCommitment:
    from tools.agent_final_learning_domain import _PreAggregatePlanningReader

    reader = _PreAggregatePlanningReader(session)
    try:
        return _read_settings(reader, session)
    finally:
        reader.reader.close()
