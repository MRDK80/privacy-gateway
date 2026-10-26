"""Actual CLI evidence: resumed vs uninterrupted synthetic full coverage."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tools.agent_resumable_gate import main

REPO = Path(__file__).resolve().parents[1]


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux-only runner V1")


def test_cli_resume_matches_uninterrupted_baseline(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    root = tmp_path / "synthetic"
    root.mkdir()
    (root / "test_synthetic.py").write_text(
        "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n"
    )
    # Explicit synthetic toolchain: never substitute these fake commands in a
    # real project profile. This fixture checks dispatch/receipts, not Ruff itself.
    for name in ("ruff", "mypy", "pre_commit"):
        (root / f"{name}.py").write_text("raise SystemExit(0)\n")
    git = shutil.which("git")
    assert git is not None
    git = str(Path(git).resolve())
    for args in (
        ("init", "-b", "synthetic"),
        ("add", "."),
        (
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "synthetic",
        ),
    ):
        subprocess.run([git, *args], cwd=root, check=True, capture_output=True)
    nodes = ["test_synthetic.py::test_one", "test_synthetic.py::test_two"]
    value: dict[str, Any] = {
        "version": 1,
        "root": str(root),
        "python": str(Path(sys.executable).resolve()),
        "git": git,
        "head": "HEAD",
        "base": "HEAD",
        "inputs": [],
        "environment": {
            "PATH": os.defpath,
            "HOME": str(tmp_path),
            "LANG": "C.UTF-8",
            "PYTHONPATH": str(REPO),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "plan": {
            "version": 1,
            "review": "a" * 64,
            "inventory": nodes,
            "units": [[nodes[0]], [nodes[1]]],
            "allowed_skips": [],
        },
        "expires_at": time.time() + 60,
        "seconds": 60,
        "max_collections": 4,
    }
    target = tmp_path / "profile.json"
    target.write_text(json.dumps(value))
    target.chmod(0o600)
    approved = hashlib.sha256(target.read_bytes()).hexdigest()

    def invoke(action: str, name: str, extra: list[str]) -> dict[str, Any]:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "synthetic-cli",
                action,
                "--profile",
                str(target),
                "--approved-profile-sha256",
                approved,
                "--evidence",
                str(tmp_path / name),
                *extra,
            ],
        )
        assert main() == 0
        result: dict[str, Any] = json.loads(capsys.readouterr().out)
        return result

    assert (
        invoke("start", "resumed", ["--pause-after-units", "1"])["status"] == "PAUSED"
    )
    resumed = invoke("resume", "resumed", [])
    continuous = invoke("start", "continuous", [])
    assert {k: resumed[k] for k in ("status", "nodes", "units", "source")} == {
        k: continuous[k] for k in ("status", "nodes", "units", "source")
    }
    assert resumed["reused_units"] == 1
    assert resumed["executed_units"] == 1
    assert continuous["reused_units"] == 0
    assert continuous["executed_units"] == 2
    assert resumed["status"] == "AGGREGATE_PASS"
    for name in ("resumed", "continuous"):
        events = [
            json.loads(line)["event"]
            for line in (tmp_path / name / "journal.jsonl").read_text().splitlines()
        ]
        assert [e["unit"] for e in events if e["kind"] == "dispatch"] == [0, 1]
        assert len([e for e in events if e["kind"] == "terminal"]) == 2
        assert len([e for e in events if e["kind"] == "quality_terminal"]) == 3

    # Compare partitioned sessions to a single uninterrupted pytest session too.
    value["plan"]["units"] = [nodes]
    target = tmp_path / "baseline-profile.json"
    target.write_text(json.dumps(value))
    target.chmod(0o600)
    approved = hashlib.sha256(target.read_bytes()).hexdigest()
    baseline = invoke("start", "whole-session", [])
    assert baseline["nodes"] == resumed["nodes"]

    def outcomes(name: str) -> list[tuple[str, str, str]]:
        events = [
            json.loads(line)["event"]
            for line in (tmp_path / name / "journal.jsonl").read_text().splitlines()
        ]
        return [
            (e["nodeid"], e["when"], e["outcome"])
            for e in events
            if e["kind"] == "report"
        ]

    assert outcomes("resumed") == outcomes("whole-session")


def test_live_pause_signal_reaches_operator_callback_and_handler_is_restored(
    monkeypatch: Any, capsys: Any
) -> None:
    import signal

    from tools import agent_resumable_gate as cli

    signal_number = getattr(signal, "SIGUSR1")
    original = signal.getsignal(signal_number)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "synthetic-cli",
            "start",
            "--profile",
            "/synthetic",
            "--approved-profile-sha256",
            "a" * 64,
            "--evidence",
            "/synthetic-evidence",
        ],
    )
    monkeypatch.setattr(cli, "profile", lambda *args: {})

    def execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert not kwargs["pause_requested"]()
        assert callable(signal.getsignal(signal_number))
        signal.raise_signal(signal_number)
        assert kwargs["pause_requested"]()
        return {"status": "PAUSED"}

    monkeypatch.setattr(cli, "execute", execute)
    assert cli.main() == 0
    assert signal.getsignal(signal_number) == original
    assert json.loads(capsys.readouterr().out)["status"] == "PAUSED"
