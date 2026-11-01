"""Synthetic fixed-file evidence does not replace original owner proof."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tools import agent_final_preparation_owner as owner
from tools import agent_final_preparation_records as records
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_preparation_owner import read_owner_approval

from tests.test_agent_final_preparation_records import original

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private owner state")


def fixture(seed: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    seed.chmod(0o700)
    candidate = original("candidate")
    approval = original("approval")
    approval["candidate_digest"] = candidate["candidate_digest"]
    approval = records.seal(approval, self_digest="approved_digest")
    receipt = original("owner_receipt")
    receipt.update(
        candidate_digest=candidate["candidate_digest"],
        approval_digest=approval["approved_digest"],
    )
    receipt = records.seal(receipt)
    pin = original("owner_pin")
    pin.update(
        candidate_digest=candidate["candidate_digest"],
        approval_digest=approval["approved_digest"],
        installation_receipt_digest=receipt["record_digest"],
    )
    pin = records.seal(pin)
    directory = seed / "final-owner"
    directory.mkdir(mode=0o700)
    values = {
        "approval.json": approval,
        "approval-pin.json": pin,
        "install-receipt.json": receipt,
    }
    for name, value in values.items():
        path = directory / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    return candidate, values


def read(
    seed: Path, candidate: dict[str, Any], *, proved: bool = True
) -> dict[str, Any] | None:
    return read_owner_approval(
        seed,
        candidate,
        max_bytes=262144,
        installed_guard=lambda: None,
        source_verifier=lambda *_: proved,
    )


def test_absent_evidence_waits(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    assert read(tmp_path, original("candidate")) is None


def test_matching_files_require_original_source_proof(tmp_path: Path) -> None:
    candidate, values = fixture(tmp_path)
    assert read(tmp_path, candidate) == values["approval.json"]
    with pytest.raises(PhaseBlocked):
        read(tmp_path, candidate, proved=False)


@pytest.mark.parametrize(
    "key",
    ["main_sha", "roadmap_sha", "pr_ci_digest", "owner_identity", "launch_digest"],
)
def test_candidate_drift_blocks(tmp_path: Path, key: str) -> None:
    candidate, _ = fixture(tmp_path)
    candidate[key] = (
        "different-owner"
        if key == "owner_identity"
        else (
            "sha256:" + "9" * 64
            if key == "launch_digest"
            else "9" * (40 if key.endswith("sha") else 64)
        )
    )
    candidate = records.seal(candidate, self_digest="candidate_digest")
    with pytest.raises(PhaseBlocked):
        read(tmp_path, candidate)


@pytest.mark.parametrize(
    "fault", ["partial", "symlink", "hardlink", "public", "duplicate", "oversize"]
)
def test_unsafe_owner_files_block(tmp_path: Path, fault: str) -> None:
    candidate, _ = fixture(tmp_path)
    path = tmp_path / "final-owner" / "approval.json"
    if fault == "partial":
        path.unlink()
    elif fault == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "missing")
    elif fault == "hardlink":
        os.link(path, tmp_path / "other")
    elif fault == "public":
        path.chmod(0o644)
    elif fault == "duplicate":
        path.write_text('{"schema_version":"2.0","schema_version":"2.0"}')
    else:
        path.write_bytes(b"x" * 262145)
    with pytest.raises(PhaseBlocked):
        read(tmp_path, candidate)


def test_guard_failure_blocks(tmp_path: Path) -> None:
    candidate, _ = fixture(tmp_path)

    def guard() -> None:
        raise PhaseBlocked("FINAL_APPROVAL_INVALID")

    with pytest.raises(PhaseBlocked):
        read_owner_approval(
            tmp_path,
            candidate,
            max_bytes=262144,
            installed_guard=guard,
            source_verifier=lambda *_: True,
        )


@pytest.mark.parametrize(
    "name", ["approval.json", "approval-pin.json", "install-receipt.json"]
)
def test_fifo_owner_evidence_blocks_without_waiting(tmp_path: Path, name: str) -> None:
    if os.name != "posix":
        pytest.skip("POSIX private owner state")
    candidate, _ = fixture(tmp_path)
    path = tmp_path / "final-owner" / name
    path.unlink()
    getattr(os, "mkfifo")(path, mode=0o600)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import sys
from pathlib import Path

from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_preparation_owner import read_owner_approval

try:
    read_owner_approval(
        Path(sys.argv[1]),
        json.loads(sys.argv[2]),
        max_bytes=262144,
        installed_guard=lambda: None,
        source_verifier=lambda *_: True,
    )
except PhaseBlocked as exc:
    assert exc.machine_code == "FINAL_APPROVAL_INVALID"
    print(exc.machine_code)
else:
    raise AssertionError("FIFO owner evidence was accepted")
""",
            str(tmp_path),
            json.dumps(candidate),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "FINAL_APPROVAL_INVALID\n"


@pytest.mark.parametrize("directory", [False, True])
def test_non_posix_private_check_blocks(
    monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    monkeypatch.setattr(owner, "os", SimpleNamespace(name="nt"))
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        owner._private(-1, directory=directory)
