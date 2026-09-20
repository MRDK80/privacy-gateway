"""Diagnostics tests for #186: distinguishable and redacted adapter failures."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "tools" / "codex_adapter.py"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
FAKE_CREDENTIAL = "fake-credential-value-not-a-secret"

FAKE_LINES = (
    "import sys",
    "if '--version' in sys.argv:",
    "    print(VERSION)",
    "    raise SystemExit(VERSION_EXIT)",
    "sys.stderr.write(STDERR)",
    "raise SystemExit(EXIT_CODE)",
)


def _fake_codex(
    tmp_path: Path,
    *,
    version: str = "codex-cli 0.154.0",
    version_exit: int = 0,
    exit_code: int = 1,
    stderr: str = "",
) -> list[str]:
    script = tmp_path / "fake_codex.py"
    header = (
        "VERSION = " + repr(version),
        "VERSION_EXIT = " + repr(version_exit),
        "EXIT_CODE = " + repr(exit_code),
        "STDERR = " + repr(stderr),
    )
    script.write_text(chr(10).join(header + FAKE_LINES) + chr(10), encoding="utf-8")
    return [sys.executable, str(script)]


def _request() -> dict[str, object]:
    return {
        "contract": {
            "issue": 186,
            "base_sha": BASE_SHA,
            "allowed_paths": ["tools/codex_adapter.py"],
        },
        "head_sha": HEAD_SHA,
        "repair_iteration": 0,
        "session_id": "session",
    }


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ADAPTER),
            "--role",
            "executor",
            "--codex-command",
            json.dumps(command),
        ],
        cwd=REPO_ROOT,
        input=json.dumps(_request()),
        capture_output=True,
        text=True,
        check=False,
    )


def test_missing_binary_reports_codex_not_found(tmp_path: Path) -> None:
    completed = _run([str(tmp_path / "missing-codex-binary")])
    assert completed.returncode == 20
    assert "CODEX_NOT_FOUND" in completed.stderr


def test_failing_version_probe_is_distinguishable(tmp_path: Path) -> None:
    completed = _run(_fake_codex(tmp_path, version_exit=3))
    assert completed.returncode == 20
    assert "VERSION_PROBE_FAILED" in completed.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="Bubblewrap is Linux-only")
def test_nonzero_exec_keeps_model_unavailable(tmp_path: Path) -> None:
    completed = _run(_fake_codex(tmp_path, exit_code=1, stderr="some failure"))
    assert completed.returncode == 20
    assert "MODEL_UNAVAILABLE" in completed.stderr
    assert "codex_exit=1" in completed.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="Bubblewrap is Linux-only")
def test_invalid_json_schema_token_is_surfaced(tmp_path: Path) -> None:
    noise = (
        "Invalid schema for response_format 'codex_output_schema': invalid_json_schema"
    )
    completed = _run(_fake_codex(tmp_path, exit_code=1, stderr=noise))
    assert completed.returncode == 20
    assert "tokens=invalid_json_schema" in completed.stderr


def test_raw_codex_stderr_is_never_forwarded(tmp_path: Path) -> None:
    noise = (
        "prompt leak: task-data block "
        + FAKE_CREDENTIAL
        + " /home/user/.codex/auth.json invalid_json_schema"
    )
    completed = _run(_fake_codex(tmp_path, exit_code=1, stderr=noise))
    assert completed.returncode == 20
    assert FAKE_CREDENTIAL not in completed.stderr
    assert "auth.json" not in completed.stderr
    assert "prompt leak" not in completed.stderr
    assert "task-data" not in completed.stderr


def test_orchestrator_reports_adapter_machine_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from tools.agent_orchestrate import CommandAdapter, OrchestrationError

    role = tmp_path / "failing_role.py"
    role.write_text(
        chr(10).join(
            (
                "import sys",
                "sys.stderr.write('SCHEMA_DERIVE_UNSUPPORTED' + chr(10))",
                "sys.stderr.write('detail=codex_exit=1 "
                "tokens=invalid_json_schema' + chr(10))",
                "raise SystemExit(20)",
            )
        )
        + chr(10),
        encoding="utf-8",
    )
    command = [sys.executable, str(role)]
    adapter = CommandAdapter(
        command,
        command,
        root=REPO_ROOT,
        timeout_seconds=60,
        output_limit=20000,
    )
    with pytest.raises(OrchestrationError) as error:
        adapter.execute({"issue": 186}, "session")
    assert error.value.machine_code == "MODEL_UNAVAILABLE"
    captured = capsys.readouterr()
    assert "machine_code" in captured.err
    assert "SCHEMA_DERIVE_UNSUPPORTED" in captured.err
    assert "tokens=invalid_json_schema" in captured.err


def test_orchestrator_reports_missing_os_sandbox(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from tools.agent_orchestrate import _report_adapter_diagnostic

    _report_adapter_diagnostic(20, "SANDBOX_UNAVAILABLE\n")
    assert "machine_code=SANDBOX_UNAVAILABLE" in capsys.readouterr().err
