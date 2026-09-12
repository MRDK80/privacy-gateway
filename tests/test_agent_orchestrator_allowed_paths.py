"""Fail-closed allowlist tests for the agent orchestrator CLI (#180)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "agent_orchestrate.py"
BASE_REF = "roadmap/179-codex-adapters"
HEAD_REF = "fix/180-orchestrator-allowed-paths"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "agent_orchestrate_allowed_paths", MODULE_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


orchestrator = _load_module()


def _git(root: Path, *args: str) -> str:
    run = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    )
    return run.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Synthetic User")
    _git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    (tmp_path / "AGENTS.md").write_text("trusted policy\n", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text("trusted process\n", encoding="utf-8")
    (tmp_path / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "unit.py").write_text("UNIT = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    _git(tmp_path, "branch", BASE_REF)
    _git(tmp_path, "switch", "-c", HEAD_REF)
    (tmp_path / "code.py").write_text("VALUE = 2\n", encoding="utf-8")
    return tmp_path


def _argv(root: Path, *extra: str) -> list[str]:
    return [
        "180",
        "--epic",
        "179",
        "--base",
        BASE_REF,
        "--head",
        HEAD_REF,
        "--criterion",
        "synthetic criterion",
        "--root",
        str(root),
        *extra,
    ]


def _dry_run(
    root: Path, capsys: pytest.CaptureFixture[str], *extra: str
) -> tuple[int, dict[str, Any]]:
    code = orchestrator.main(_argv(root, "--dry-run", *extra))
    return code, dict(json.loads(capsys.readouterr().out))


def _contract(root: Path, allowed: tuple[str, ...]) -> Any:
    return orchestrator.TaskContract(
        issue=180,
        epic=179,
        acceptance_criteria=("synthetic criterion",),
        base_ref=BASE_REF,
        base_sha=_git(root, "rev-parse", BASE_REF),
        head_ref=HEAD_REF,
        allowed_paths=allowed,
        max_minutes=10,
        max_repair_iterations=2,
        max_report_chars=10_000,
    )


def _executor_report(contract: Any, head_sha: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "role": "executor",
        "task_issue": contract.issue,
        "base_sha": contract.base_sha,
        "head_sha": head_sha,
        "status": "completed",
        "acceptance_criteria": [
            {"requirement": "synthetic criterion", "status": "met", "evidence": "t"}
        ],
        "changed_files": ["code.py"],
        "checks": [],
        "residual_risks": [],
        "stop_reason": "scope_complete",
    }


def _verdict(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "role": "controller",
        "task_issue": request["issue"],
        "base_sha": request["base_sha"],
        "head_sha": request["head_sha"],
        "review_basis": {
            "trust_source_kind": "base_sha",
            "head_policy_applied": False,
            "executor_self_assessment_treated_as_evidence_only": True,
        },
        "verdict": "PASS",
        "repair_iteration": 0,
        "escalation_reason": None,
        "blocking_findings": [],
        "notes": [],
    }


class RecordingAdapter:
    def __init__(self, side_effect: Path | None = None) -> None:
        self.seen_allowlists: list[list[str]] = []
        self.executor_calls = 0
        self.controller_calls = 0
        self.side_effect = side_effect
        self.mutable_allowlist: bool | None = None

    def execute(self, request: dict[str, Any], session_id: str) -> dict[str, Any]:
        self.executor_calls += 1
        allowlist = request["contract"]["allowed_paths"]
        self.seen_allowlists.append(list(allowlist))
        self.mutable_allowlist = isinstance(allowlist, list)
        if isinstance(allowlist, list):
            allowlist.append("injected.py")
        if self.side_effect is not None:
            self.side_effect.write_text("OUTSIDE = 1\n", encoding="utf-8")
        return _executor_report(request["contract_object"], request["head_sha"])

    def review(
        self,
        request: dict[str, Any],
        trusted_policy: dict[str, str],
        session_id: str,
    ) -> dict[str, Any]:
        self.controller_calls += 1
        return _verdict(request)


def _storage(repository: Path) -> Any:
    return orchestrator.StateStore(repository.parent / f"{repository.name}-private")


def _passing_gate(_contract: Any) -> dict[str, Any]:
    return {"status": "passed", "machine_code": "OK", "exit_code": 0}


def test_single_allowed_path_is_reported_in_dry_run_contract(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, payload = _dry_run(repository, capsys, "--allowed-path", "code.py")

    assert code == 0
    assert payload["status"] == "DRY_RUN"
    assert payload["contract"]["allowed_paths"] == ["code.py"]
    assert payload["scope"] == {
        "mode": "explicit",
        "allowed_paths": ["code.py"],
        "enforced": True,
    }


def test_multiple_allowed_paths_are_accepted(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repository / "pkg" / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")

    code, payload = _dry_run(
        repository, capsys, "--allowed-path", "code.py", "--allowed-path", "pkg"
    )

    assert code == 0
    assert payload["contract"]["allowed_paths"] == ["code.py", "pkg"]


def test_dry_run_without_allowlist_is_explicitly_unscoped(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = repository.parent / f"{repository.name}-private"

    code, payload = _dry_run(repository, capsys)

    assert code == 0
    assert payload["contract"]["allowed_paths"] == []
    assert payload["scope"]["mode"] == "unscoped_dry_run"
    assert payload["scope"]["enforced"] is False
    assert not state.exists()


def test_real_run_without_allowlist_never_starts_executor(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = repository.parent / "executor-started"
    command = json.dumps(
        [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    )

    code = orchestrator.main(
        _argv(
            repository,
            "--executor-command",
            command,
            "--controller-command",
            command,
        )
    )
    payload = dict(json.loads(capsys.readouterr().out))

    assert code == 20
    assert payload["status"] == "FAIL_ESCALATE"
    assert payload["machine_code"] == "ALLOWLIST_REQUIRED"
    assert not marker.exists()


def test_run_refuses_empty_allowlist_before_any_adapter_call(
    repository: Path,
) -> None:
    adapter = RecordingAdapter()

    result = orchestrator.run(
        _contract(repository, ()),
        root=repository,
        storage=_storage(repository),
        adapter=adapter,
        gate=_passing_gate,
    )

    assert result.machine_code == "ALLOWLIST_REQUIRED"
    assert result.status == "FAIL_ESCALATE"
    assert adapter.executor_calls == 0
    assert adapter.controller_calls == 0


@pytest.mark.parametrize(
    ("value", "machine_code"),
    [
        (".", "ALLOWED_PATH_INVALID"),
        ("", "ALLOWED_PATH_INVALID"),
        ("/etc/passwd", "ALLOWED_PATH_INVALID"),
        ("C:/Windows/System32", "ALLOWED_PATH_INVALID"),
        ("C:\\Windows\\System32", "ALLOWED_PATH_INVALID"),
        ("pkg\\unit.py", "ALLOWED_PATH_INVALID"),
        ("..", "ALLOWED_PATH_INVALID"),
        ("../outside.py", "ALLOWED_PATH_INVALID"),
        ("pkg/../../outside.py", "ALLOWED_PATH_INVALID"),
        ("pkg//unit.py", "ALLOWED_PATH_INVALID"),
        (" code.py", "ALLOWED_PATH_INVALID"),
        ("~/code.py", "ALLOWED_PATH_INVALID"),
        ("AGENTS.md", "ALLOWED_PATH_PROTECTED"),
        (".github/workflows/tests.yml", "ALLOWED_PATH_PROTECTED"),
        ("docs/schemas/task-contract.schema.json", "ALLOWED_PATH_PROTECTED"),
        ("docs/agent-contracts.md", "ALLOWED_PATH_PROTECTED"),
        ("CONTRIBUTING.md", "ALLOWED_PATH_PROTECTED"),
        ("agent-memory/run.json", "ALLOWED_PATH_PROTECTED"),
        ("config.local/agent.json", "ALLOWED_PATH_PROTECTED"),
    ],
)
def test_invalid_allowed_path_fails_closed_with_stable_code(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    value: str,
    machine_code: str,
) -> None:
    code, payload = _dry_run(repository, capsys, "--allowed-path", value)

    assert code == 20
    assert payload["status"] == "FAIL_ESCALATE"
    assert payload["machine_code"] == machine_code
    assert payload["run_id"] == "not-started"


def test_allowed_path_is_normalized_and_deduplicated(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, payload = _dry_run(
        repository,
        capsys,
        "--allowed-path",
        "pkg/",
        "--allowed-path",
        "pkg",
        "--allowed-path",
        "code.py",
    )

    assert code == 0
    assert payload["contract"]["allowed_paths"] == ["pkg", "code.py"]


def test_symlink_inside_root_is_accepted(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    link = repository / "link"
    try:
        link.symlink_to(repository / "pkg", target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation is unavailable in this environment")

    code, payload = _dry_run(
        repository, capsys, "--allowed-path", "code.py", "--allowed-path", "link"
    )

    assert code == 0
    assert set(payload["contract"]["allowed_paths"]) == {"code.py", "link"}


def test_symlink_escape_is_rejected(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = repository.parent / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")
    link = repository / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation is unavailable in this environment")

    code, payload = _dry_run(repository, capsys, "--allowed-path", "escape/secret.py")

    assert code == 20
    assert payload["machine_code"] == "ALLOWED_PATH_ESCAPES_ROOT"


def test_executor_input_allowlist_cannot_be_widened(repository: Path) -> None:
    contract = _contract(repository, ("code.py",))
    adapter = RecordingAdapter()

    result = orchestrator.run(
        contract,
        root=repository,
        storage=_storage(repository),
        adapter=adapter,
        gate=_passing_gate,
    )

    assert result.status == "PASS"
    assert adapter.seen_allowlists == [["code.py"]]
    assert adapter.mutable_allowlist is False
    assert contract.allowed_paths == ("code.py",)


def test_post_executor_change_outside_allowlist_is_rejected(
    repository: Path,
) -> None:
    adapter = RecordingAdapter(side_effect=repository / "outside.py")

    result = orchestrator.run(
        _contract(repository, ("code.py",)),
        root=repository,
        storage=_storage(repository),
        adapter=adapter,
        gate=_passing_gate,
    )

    assert result.status == "FAIL_ESCALATE"
    assert result.machine_code == "SCOPE_VIOLATION"
    assert adapter.controller_calls == 0


def test_dot_allowlist_no_longer_disables_scope_check(repository: Path) -> None:
    adapter = RecordingAdapter(side_effect=repository / "outside.py")

    result = orchestrator.run(
        _contract(repository, (".",)),
        root=repository,
        storage=_storage(repository),
        adapter=adapter,
        gate=_passing_gate,
    )

    assert result.status == "FAIL_ESCALATE"
    assert result.machine_code == "SCOPE_VIOLATION"
    assert adapter.controller_calls == 0
