"""Synthetic source-shape comparisons do not prove human or runtime authority."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_runtime_cli as guards
from tools import agent_final_preparation_records as records
from tools import agent_owner_final_records as reader
from tools.agent_epic_loop import PhaseBlocked

from tests.test_agent_final_preparation_records import original
from tests.test_agent_owner_final_sources import document

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX original shapes")
NAMES = (
    "approval.json",
    "approval-pin.json",
    "install-receipt.json",
    "command-source.json",
    "final-demo-source.json",
)


def write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    seed, root = tmp_path / "seed", tmp_path / "repo"
    seed.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    directory = seed / "final-owner"
    directory.mkdir(mode=0o700)
    candidate = original("candidate")
    demo = document("demo")
    demo["candidate_digest"] = candidate["candidate_digest"]
    demo = records.seal(demo)
    approval = original("approval")
    approval.update(
        candidate_digest=candidate["candidate_digest"],
        approved_at=110,
        final_demo_digest=demo["record_digest"],
    )
    approval = records.seal(approval, self_digest="approved_digest")
    command = document("command")
    command.update(
        candidate_digest=candidate["candidate_digest"],
        approval_digest=approval["approved_digest"],
        final_demo_digest=demo["record_digest"],
    )
    command = records.seal(command)
    receipt = original("owner_receipt")
    receipt.update(
        candidate_digest=candidate["candidate_digest"],
        approved_at=110,
        approval_digest=approval["approved_digest"],
        owner_command_digest=command["record_digest"],
    )
    receipt = records.seal(receipt)
    pin = original("owner_pin")
    pin.update(
        candidate_digest=candidate["candidate_digest"],
        approved_at=110,
        approval_digest=approval["approved_digest"],
        installation_receipt_digest=receipt["record_digest"],
    )
    for name, value in zip(
        NAMES, (approval, records.seal(pin), receipt, command, demo), strict=True
    ):
        write(directory / name, value)
    return seed, root, candidate


def read(
    seed: Path, root: Path, candidate: dict[str, Any]
) -> reader.OriginalShapes | None:
    return reader.read_original_shapes(
        seed, root, candidate, max_bytes=262144, issued_at=90, expires_at=120
    )


def test_complete_shapes_are_neutral_and_read_only(tmp_path: Path) -> None:
    seed, root, candidate = fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in (seed / "final-owner").iterdir()}
    result = read(seed, root, candidate)
    assert isinstance(result, reader.OriginalShapes)
    assert result.approval["candidate_digest"] == candidate["candidate_digest"]
    assert "synthetic-owner" not in repr(result)
    assert "Synthetic contract fixture" not in repr(result)
    assert not hasattr(result, "approved") and not hasattr(result, "permissions")
    assert {p.name: p.read_bytes() for p in (seed / "final-owner").iterdir()} == before


@pytest.mark.parametrize("directory", [False, True])
def test_wholly_absent_waits_without_creation(tmp_path: Path, directory: bool) -> None:
    seed, root = tmp_path / "seed", tmp_path / "repo"
    seed.mkdir(mode=0o700)
    root.mkdir()
    if directory:
        (seed / "final-owner").mkdir(mode=0o700)
    assert read(seed, root, original("candidate")) is None
    assert (seed / "final-owner").exists() is directory


@pytest.mark.parametrize("name", NAMES)
def test_any_missing_original_blocks(tmp_path: Path, name: str) -> None:
    seed, root, candidate = fixture(tmp_path)
    (seed / "final-owner" / name).unlink()
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        read(seed, root, candidate)


@pytest.mark.parametrize(
    "fault",
    ["symlink", "hardlink", "public", "duplicate", "oversize", "directory", "unknown"],
)
def test_unsafe_originals_block(tmp_path: Path, fault: str) -> None:
    seed, root, candidate = fixture(tmp_path)
    path = seed / "final-owner/approval.json"
    if fault in {"symlink", "directory"}:
        path.unlink()
        if fault == "symlink":
            path.symlink_to(tmp_path / "missing")
        else:
            path.mkdir(mode=0o700)
    elif fault == "hardlink":
        os.link(path, tmp_path / "alias")
    elif fault == "public":
        path.chmod(0o644)
    elif fault == "duplicate":
        path.write_text('{"private-synthetic":"x","private-synthetic":"x"}')
    elif fault == "oversize":
        path.write_bytes(b"x" * 262145)
    else:
        write(seed / "final-owner/unexpected.json", {})
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$") as caught:
        read(seed, root, candidate)
    assert "private-synthetic" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="mkfifo unavailable")
def test_fifo_rejects_in_bounded_subprocess(tmp_path: Path) -> None:
    seed, root, candidate = fixture(tmp_path)
    path = seed / "final-owner/approval.json"
    path.unlink()
    getattr(os, "mkfifo")(path, 0o600)
    script = """import json,sys
from pathlib import Path
from tools.agent_owner_final_records import read_original_shapes
from tools.agent_epic_loop import PhaseBlocked
try:
 read_original_shapes(Path(sys.argv[1]),Path(sys.argv[2]),json.loads(sys.argv[3]),max_bytes=262144,issued_at=90,expires_at=120)
except PhaseBlocked as error:
 assert error.machine_code=='FINAL_APPROVAL_INVALID'
else:
 raise AssertionError('FIFO accepted')
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            script,
            str(seed),
            str(root),
            json.dumps(candidate),
        ],
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "field",
    [
        "owner_identity",
        "candidate_digest",
        "pr_ci_digest",
        "main_sha",
        "launch_digest",
        "approval_digest",
        "final_demo_digest",
        "approved_at",
        "unknown",
    ],
)
def test_resealed_command_crosslink_conflict_blocks(tmp_path: Path, field: str) -> None:
    seed, root, candidate = fixture(tmp_path)
    path = seed / "final-owner/command-source.json"
    value = json.loads(path.read_text())
    value[field] = (
        "different-owner"
        if field == "owner_identity"
        else (
            "sha256:" + "9" * 64
            if field == "launch_digest"
            else (
                111
                if field == "approved_at"
                else "9" * (40 if field.endswith("sha") else 64)
            )
        )
    )
    write(path, records.seal(value))
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        read(seed, root, candidate)


@pytest.mark.parametrize(
    "issued,expires", [(101, 120), (90, 109), (True, 120), (121, 120)]
)
def test_original_mandate_time_representation_bounds(
    tmp_path: Path, issued: int, expires: int
) -> None:
    seed, root, candidate = fixture(tmp_path)
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        reader.read_original_shapes(
            seed,
            root,
            candidate,
            max_bytes=262144,
            issued_at=issued,
            expires_at=expires,
        )


@pytest.mark.parametrize("target", ["seed", "owner", "file", "bytes"])
def test_source_namespace_and_bytes_drift_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    seed, root, candidate = fixture(tmp_path)
    original_read = guards._final_preseed_read
    count = 0

    def changed(parent: int, name: str, cap: int) -> dict[str, Any]:
        nonlocal count
        value = original_read(parent, name, cap)
        count += 1
        if count == 5:
            if target in {"seed", "owner"}:
                path = seed if target == "seed" else seed / "final-owner"
                path.rename(path.with_name(path.name + "-retained"))
                path.mkdir(mode=0o700)
            else:
                path = seed / "final-owner/approval.json"
                raw = path.read_bytes()
                if target == "file":
                    path.unlink()
                path.write_bytes(raw + b" ")
                path.chmod(0o600)
        return value

    monkeypatch.setattr(guards, "_final_preseed_read", changed)
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        read(seed, root, candidate)


@pytest.mark.parametrize("target", ["seed", "owner"])
def test_namespace_symlink_rejects(tmp_path: Path, target: str) -> None:
    seed, root, candidate = fixture(tmp_path)
    path = seed if target == "seed" else seed / "final-owner"
    retained = path.with_name(path.name + "-retained")
    path.rename(retained)
    path.symlink_to(retained, target_is_directory=True)
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        read(seed, root, candidate)


@pytest.mark.parametrize(
    "name,field",
    [
        ("approval.json", "final_demo_digest"),
        ("approval-pin.json", "installation_receipt_digest"),
        ("install-receipt.json", "owner_command_digest"),
        ("final-demo-source.json", "pr_ci_digest"),
        ("final-demo-source.json", "owner_identity"),
    ],
)
def test_other_original_crosslinks_block(tmp_path: Path, name: str, field: str) -> None:
    seed, root, candidate = fixture(tmp_path)
    path = seed / "final-owner" / name
    value = json.loads(path.read_text())
    value[field] = "foreign-owner" if field == "owner_identity" else "9" * 64
    write(
        path,
        records.seal(
            value,
            self_digest="approved_digest"
            if name == "approval.json"
            else "record_digest",
        ),
    )
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        read(seed, root, candidate)


@pytest.mark.parametrize("where", ["seed", "owner"])
def test_nonprivate_directory_blocks(tmp_path: Path, where: str) -> None:
    seed, root, candidate = fixture(tmp_path)
    (seed if where == "seed" else seed / "final-owner").chmod(0o755)
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        read(seed, root, candidate)


def test_six_real_entries_stop_after_bound_plus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, root, candidate = fixture(tmp_path)
    write(seed / "final-owner/sixth.json", {})
    original_scandir = os.scandir
    yielded: list[str] = []

    @contextmanager
    def counted(fd: int) -> Iterator[Iterator[os.DirEntry[str]]]:
        with original_scandir(fd) as entries:

            def observed() -> Iterator[os.DirEntry[str]]:
                for entry in entries:
                    yielded.append(entry.name)
                    yield entry

            yield observed()

    monkeypatch.setattr(os, "scandir", counted)
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        read(seed, root, candidate)
    assert len(yielded) == 6


def test_large_real_namespace_acquisition_is_finite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, root, candidate = fixture(tmp_path)
    for number in range(32):
        write(seed / "final-owner" / f"unknown-{number}.json", {})
    original_scandir = os.scandir
    count = 0

    @contextmanager
    def counted(fd: int) -> Iterator[Iterator[os.DirEntry[str]]]:
        with original_scandir(fd) as entries:

            def observed() -> Iterator[os.DirEntry[str]]:
                nonlocal count
                for entry in entries:
                    count += 1
                    yield entry

            yield observed()

    monkeypatch.setattr(os, "scandir", counted)
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        read(seed, root, candidate)
    assert count == 6


def test_absent_fixed_name_never_enumerates_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, root = tmp_path / "seed", tmp_path / "repo"
    seed.mkdir(mode=0o700)
    root.mkdir()
    for number in range(32):
        write(seed / f"unrelated-{number}.json", {})

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unrelated seed enumeration")

    monkeypatch.setattr(os, "listdir", forbidden)
    monkeypatch.setattr(os, "scandir", forbidden)
    assert read(seed, root, original("candidate")) is None
    assert not (seed / "final-owner").exists()


def test_absence_recheck_refuses_new_fixed_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, root = tmp_path / "seed", tmp_path / "repo"
    seed.mkdir(mode=0o700)
    root.mkdir()
    original_seed = guards._final_preseed_seed
    calls = 0

    def changed(path: Path, repository: Path) -> int:
        nonlocal calls
        calls += 1
        fd = original_seed(path, repository)
        if calls == 2:
            (seed / "final-owner").mkdir(mode=0o700)
        return fd

    monkeypatch.setattr(guards, "_final_preseed_seed", changed)
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        read(seed, root, original("candidate"))
