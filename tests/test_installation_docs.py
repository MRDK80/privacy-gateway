"""Validate documented install scenarios without network access in pytest."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tools import verify_installation_docs as docs

ROOT = Path(__file__).resolve().parents[1]


def test_complete_document_uses_one_immutable_source() -> None:
    text = (ROOT / "docs/INSTALLATION.md").read_text(encoding="utf-8")
    blocks = docs.extract_blocks(text)
    assert set(blocks) == docs.LABELS
    for name, body in blocks.items():
        if name.endswith("windows"):
            # A late successful uninstall cannot hide a failed earlier native call.
            lines = body.splitlines()
            for i, line in enumerate(lines):
                if line.startswith("& ") or "= & " in line:
                    assert "$LASTEXITCODE -ne 0" in lines[i + 1]
        else:
            assert body.startswith("set -e\n")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "source", "language"])
def test_invalid_document_fails_closed(mutation: str) -> None:
    text = (ROOT / "docs/INSTALLATION.md").read_text(encoding="utf-8")
    if mutation == "missing":
        text = text.replace("<!-- pgw-install-smoke: cli-linux -->", "<!-- omitted -->")
    elif mutation == "duplicate":
        text += "\n<!-- pgw-install-smoke: cli-linux -->\n```bash\ntrue\n```"
    elif mutation == "source":
        text = text.replace(
            "git+https://github.com/MRDK80/privacy-gateway.git@",
            "git+https://example.invalid/wrong@",
            1,
        )
    else:
        text = text.replace("```powershell", "```bash", 1)
    with pytest.raises(docs.InstallationDocsError):
        docs.extract_blocks(text)


def test_installer_environment_is_disposable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "PIP_INDEX_URL",
        "PYTHONPATH",
        "PIPX_DEFAULT_PYTHON",
        "GIT_SSH_COMMAND",
    ):
        monkeypatch.setenv(name, "synthetic-host-override")
    env = docs.isolated_environment(tmp_path)
    assert "PIP_INDEX_URL" not in env and "PYTHONPATH" not in env
    assert "GIT_SSH_COMMAND" not in env
    for name in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PIPX_HOME",
        "PIPX_BIN_DIR",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
    ):
        assert Path(env[name]).is_relative_to(tmp_path)
    assert env["PIP_CONFIG_FILE"] == os.devnull
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["PGW_PYTHON"] == sys.executable


@pytest.mark.parametrize("windows", [False, True])
def test_native_runner_uses_finite_wait(
    windows: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "synthetic-shell")

    def spawn(argv: list[str], **kwargs: Any) -> Any:
        assert argv[0] == "synthetic-shell"
        assert ("-File" in argv) == windows
        assert kwargs["start_new_session"] is not windows

        def communicate(timeout: int) -> tuple[str, None]:
            assert timeout == 600
            return "synthetic installer success", None

        return SimpleNamespace(returncode=0, communicate=communicate)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    target = tmp_path / "run"
    docs.run_block("synthetic script", target, windows)
    assert (target / "transcript.log").read_text() == "synthetic installer success"


def test_nonzero_installation_does_not_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "synthetic-shell")
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: SimpleNamespace(
            returncode=3,
            communicate=lambda **_: ("synthetic failure", None),
        ),
    )
    with pytest.raises(docs.InstallationDocsError):
        docs.run_block("synthetic script", tmp_path / "run", False)


def test_timeout_cleans_up_and_does_not_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "synthetic-shell")
    cleaned: list[object] = []

    def communicate(**kwargs: Any) -> tuple[str, None]:
        raise subprocess.TimeoutExpired("synthetic", 600)

    process: Any = SimpleNamespace(communicate=communicate)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: process)

    def cleanup(p: Any, windows: bool) -> str:
        cleaned.append(p)
        return "UNVERIFIED"

    monkeypatch.setattr(docs, "stop_tree", cleanup)
    with pytest.raises(docs.InstallationDocsError):
        docs.run_block("synthetic script", tmp_path / "run", False)
    assert cleaned == [process]


def test_cleanup_signals_group_even_after_leader_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    process: Any = SimpleNamespace(
        pid=12345, poll=lambda: 0, wait=lambda: calls.append("reaped")
    )
    monkeypatch.setattr(
        os, "killpg", lambda pid, sig: calls.append((pid, sig)), raising=False
    )
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    docs.stop_tree(process, False)
    assert calls and isinstance(calls[0], tuple)
    assert calls[-1] == "reaped"


def test_windows_exited_leader_reports_unverified_without_stale_pid_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    process: Any = SimpleNamespace(
        pid=12345, poll=lambda: 0, wait=lambda: calls.append("reaped")
    )

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("must not taskkill an exited leader PID")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert docs.stop_tree(process, True) == "WINDOWS_TREE_UNVERIFIED"
    assert calls == ["reaped"]
