"""Contract tests for the production Codex adapters (#181).

The tests never invoke a real model. A deterministic fake Codex CLI is written
to a temporary directory and passed through ``--codex-command``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "tools" / "codex_adapter.py"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40

FAKE_LINES = (
    "import json, sys",
    "from pathlib import Path",
    "if '--version' in sys.argv:",
    "    print(VERSION)",
    "    raise SystemExit(0)",
    "argv = sys.argv[1:]",
    "target = Path(argv[argv.index('-o') + 1])",
    "target.write_text(PAYLOAD, encoding='utf-8')",
    "marker = Path(__file__).with_name('argv.json')",
    "marker.write_text(json.dumps(argv), encoding='utf-8')",
    "raise SystemExit(EXIT_CODE)",
)

EXECUTOR_PAYLOAD = {
    "status": "completed",
    "acceptance_criteria": [
        {"requirement": "adapter exists", "status": "met", "evidence": "tests"}
    ],
    "changed_files": ["tools/codex_adapter.py"],
    "checks": [
        {
            "name": "pytest",
            "status": "passed",
            "machine_code": None,
            "exit_code": 0,
        }
    ],
    "residual_risks": [],
    "stop_reason": "scope_complete",
}

CONTROLLER_PAYLOAD = {
    "verdict": "PASS",
    "repair_iteration": 0,
    "escalation_reason": None,
    "blocking_findings": [],
    "notes": ["reviewed"],
}


def _fake_codex(
    tmp_path: Path,
    payload: str,
    *,
    version: str = "codex-cli 0.154.0",
    exit_code: int = 0,
) -> list[str]:
    script = tmp_path / "fake_codex.py"
    header = (
        "VERSION = " + repr(version),
        "PAYLOAD = " + repr(payload),
        "EXIT_CODE = " + repr(exit_code),
    )
    body = chr(10).join(header + FAKE_LINES) + chr(10)
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


def _run(
    role: str, command: list[str], request: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ADAPTER),
            "--role",
            role,
            "--codex-command",
            json.dumps(command),
        ],
        cwd=REPO_ROOT,
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=False,
    )


def _executor_request() -> dict[str, object]:
    return {
        "contract": {"issue": 181, "base_sha": BASE_SHA},
        "head_sha": HEAD_SHA,
        "repair_iteration": 0,
        "session_id": "session",
    }


def _controller_request() -> dict[str, object]:
    return {
        "issue": 181,
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "acceptance_criteria": ["adapter exists"],
        "diff": "diff --git a/x b/x",
        "gate_evidence": {"status": "passed"},
        "repair_iteration": 0,
        "trusted_policy": {"AGENTS.md": "trusted policy text"},
        "session_id": "session",
    }


def test_executor_report_is_authoritative_and_valid(tmp_path: Path) -> None:
    command = _fake_codex(tmp_path, json.dumps(EXECUTOR_PAYLOAD))
    completed = _run("executor", command, _executor_request())
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["role"] == "executor"
    assert report["schema_version"] == "1.0"
    assert report["task_issue"] == 181
    assert report["base_sha"] == BASE_SHA
    assert report["head_sha"] == HEAD_SHA


def test_controller_verdict_pins_review_basis(tmp_path: Path) -> None:
    command = _fake_codex(tmp_path, json.dumps(CONTROLLER_PAYLOAD))
    completed = _run("controller", command, _controller_request())
    assert completed.returncode == 0, completed.stderr
    verdict = json.loads(completed.stdout)
    assert verdict["verdict"] == "PASS"
    assert verdict["review_basis"]["head_policy_applied"] is False
    assert verdict["review_basis"]["trust_source_kind"] == "local_read_only_bundle"
    assert verdict["review_basis"][
        "executor_self_assessment_treated_as_evidence_only"
    ]


def test_model_supplied_review_basis_cannot_win(tmp_path: Path) -> None:
    hostile = dict(CONTROLLER_PAYLOAD)
    hostile["review_basis"] = {
        "trust_source_kind": "base_sha",
        "head_policy_applied": True,
        "executor_self_assessment_treated_as_evidence_only": False,
    }
    command = _fake_codex(tmp_path, json.dumps(hostile))
    completed = _run("controller", command, _controller_request())
    assert completed.returncode == 0, completed.stderr
    verdict = json.loads(completed.stdout)
    assert verdict["review_basis"]["head_policy_applied"] is False


def test_controller_runs_read_only_outside_the_worktree(tmp_path: Path) -> None:
    command = _fake_codex(tmp_path, json.dumps(CONTROLLER_PAYLOAD))
    completed = _run("controller", command, _controller_request())
    assert completed.returncode == 0, completed.stderr
    recorded = json.loads(_argv_from(tmp_path))
    assert "--sandbox" in recorded
    assert recorded[recorded.index("--sandbox") + 1] == "read-only"
    assert "--ignore-rules" in recorded
    assert "--ephemeral" in recorded
    workdir = Path(recorded[recorded.index("-C") + 1])
    assert workdir.name == "policy-bundle"
    assert str(REPO_ROOT) not in str(workdir)


def _argv_from(tmp_path: Path) -> str:
    marker = tmp_path / "argv.json"
    if marker.exists():
        return marker.read_text(encoding="utf-8")
    raise AssertionError("fake Codex did not record its argv")


def test_schema_violation_fails_closed(tmp_path: Path) -> None:
    invalid = dict(CONTROLLER_PAYLOAD)
    invalid["blocking_findings"] = [
        {
            "severity": "high",
            "requirement": "r",
            "evidence": "e",
            "required_fix": "f",
        }
    ]
    command = _fake_codex(tmp_path, json.dumps(invalid))
    completed = _run("controller", command, _controller_request())
    assert completed.returncode == 20
    assert "SCHEMA_VIOLATION" in completed.stderr


def test_malformed_output_fails_closed(tmp_path: Path) -> None:
    command = _fake_codex(tmp_path, "not json at all")
    completed = _run("executor", command, _executor_request())
    assert completed.returncode == 20
    assert "MALFORMED_OUTPUT" in completed.stderr


def test_nonzero_exit_fails_closed(tmp_path: Path) -> None:
    command = _fake_codex(tmp_path, json.dumps(EXECUTOR_PAYLOAD), exit_code=1)
    completed = _run("executor", command, _executor_request())
    assert completed.returncode == 20
    assert "MODEL_UNAVAILABLE" in completed.stderr


def test_old_codex_version_fails_closed(tmp_path: Path) -> None:
    command = _fake_codex(
        tmp_path, json.dumps(EXECUTOR_PAYLOAD), version="codex-cli 0.153.9"
    )
    completed = _run("executor", command, _executor_request())
    assert completed.returncode == 20
    assert "VERSION_MISMATCH" in completed.stderr


def test_invalid_request_fails_closed(tmp_path: Path) -> None:
    command = _fake_codex(tmp_path, json.dumps(EXECUTOR_PAYLOAD))
    completed = _run("executor", command, {"contract": {}})
    assert completed.returncode == 20
    assert "INVALID_REQUEST" in completed.stderr
