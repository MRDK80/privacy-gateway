"""Deterministic gate profile and evidence tests for task #197.

Тесты проверяют реальную сессию snapshot на синтетическом репозитории и
детерминированный runner для каждого fail-closed статуса. Skip и xfail не
используются: отсутствие свойства обязано быть видимым.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "agent_gate.py"
BASE_REF = "roadmap/179-codex-adapters"
HEAD_REF = "feat/197-complete-gate-evidence"
ALLOWED: tuple[str, ...] = ("src", "pkg")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")

def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module("agent_gate_under_test", MODULE_PATH)
agent_orchestrate = _load_module(
    "agent_orchestrate_for_gate_tests", REPO_ROOT / "tools" / "agent_orchestrate.py"
)


def _git(root: Path, *args: str) -> str:
    run = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    )
    return run.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Synthetic User")
    _git(root, "config", "user.email", "synthetic@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "core.eol", "lf")
    (root / "AGENTS.md").write_text("trusted policy\n", encoding="utf-8")
    (root / "CONTRIBUTING.md").write_text("trusted process\n", encoding="utf-8")
    package = root / "src" / "privacy_gateway"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")
    (root / "pkg").mkdir()
    (root / "pkg" / "unit.py").write_text("UNIT = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", BASE_REF)
    _git(root, "switch", "-c", HEAD_REF)
    (root / "pkg" / "unit.py").write_text("UNIT = 2\n", encoding="utf-8")
    (root / "pkg" / "new_unit.py").write_text("NEW = 1\n", encoding="utf-8")
    return root


def _session(root: Path, allowed: tuple[str, ...] = ALLOWED) -> Any:
    return gate.trusted_snapshot_session(
        root=root, base_sha=_git(root, "rev-parse", BASE_REF), allowed_paths=allowed
    )


def _outcome(status: str, exit_code: int | None, output: str = "") -> Any:
    return gate.CommandOutcome(status, exit_code, output)


PASSING_OUTPUT = {
    "pytest": "746 passed, 7 skipped in 9.05s",
    "ruff": "All checks passed!",
    "mypy": "Success: no issues found in 91 source files",
    "pre-commit": (
        "Detect secrets....................Passed\n"
        "trim trailing whitespace..........Passed\n"
    ),
}


def _runner(overrides: dict[str, Any] | None = None) -> Any:
    table = dict(overrides or {})

    def run(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> Any:
        check_id = {"pytest": "pytest", "ruff": "ruff", "mypy": "mypy"}.get(
            argv[0], argv[0]
        )
        if check_id in table:
            return table[check_id]
        return _outcome("passed", 0, PASSING_OUTPUT.get(check_id, ""))

    return run


def test_session_exposes_hashed_identity(repository: Path) -> None:
    with _session(repository) as session:
        assert HEX40.match(session.snapshot_commit)
        assert HEX40.match(session.tree_hash)
        assert HEX64.match(session.diff_sha256)
        assert session.provenance_complete is True
        assert session.snapshot_method == "commit-tree"


def test_session_identity_matches_orchestrator_snapshot(repository: Path) -> None:
    """Сессия #197 обязана хешировать то же состояние, что примитив #196."""
    contract = agent_orchestrate.TaskContract(
        issue=197,
        epic=179,
        acceptance_criteria=("complete gate evidence",),
        base_ref=BASE_REF,
        base_sha=_git(repository, "rev-parse", BASE_REF),
        head_ref=HEAD_REF,
        allowed_paths=ALLOWED,
    )
    reference = agent_orchestrate.create_trusted_snapshot(contract, root=repository)

    with _session(repository) as session:
        assert session.tree_hash == reference.tree_hash
        assert session.diff_sha256 == reference.diff_sha256


def test_snapshot_checkout_tracks_previously_untracked_file(repository: Path) -> None:
    with _session(repository) as session:
        tracked = _git(session.checkout, "ls-files").splitlines()

        assert "pkg/new_unit.py" in tracked
        assert (session.checkout / "pkg" / "unit.py").read_text(
            encoding="utf-8"
        ) == "UNIT = 2\n"


def test_session_does_not_move_source_refs_and_cleans_up(repository: Path) -> None:
    before_refs = _git(repository, "show-ref")
    before_status = _git(repository, "status", "--porcelain=v1")
    before_entries = sorted(path.name for path in repository.iterdir())

    with _session(repository) as session:
        workspace = session.checkout.parent

    assert _git(repository, "show-ref") == before_refs
    assert _git(repository, "status", "--porcelain=v1") == before_status
    assert sorted(path.name for path in repository.iterdir()) == before_entries
    assert not workspace.exists()


def test_out_of_scope_change_blocks_session(repository: Path) -> None:
    (repository / "outside.py").write_text("OUTSIDE = 1\n", encoding="utf-8")

    with pytest.raises(gate.GateError) as error:
        with _session(repository):
            pass

    assert error.value.machine_code == "SNAPSHOT_SCOPE_VIOLATION"


def test_empty_allowlist_blocks_session(repository: Path) -> None:
    with pytest.raises(gate.GateError) as error:
        with _session(repository, ()):
            pass

    assert error.value.machine_code == "SNAPSHOT_SCOPE_VIOLATION"


def test_environment_probe_accepts_snapshot_package(repository: Path) -> None:
    with _session(repository) as session:
        assert gate.verify_environment(session) == "__init__.py"


def test_environment_probe_rejects_package_outside_snapshot(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = tmp_path / "foreign"
    (foreign / "outside_package").mkdir(parents=True)
    (foreign / "outside_package" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(foreign))

    with _session(repository) as session:
        with pytest.raises(gate.GateError) as error:
            gate.verify_environment(session, package="outside_package")

    assert error.value.machine_code == "GATE_ENVIRONMENT_UNVERIFIED"


def test_full_profile_produces_complete_passing_evidence(repository: Path) -> None:
    with _session(repository) as session:
        results = gate.run_profile(session, runner=_runner())
        evidence = gate.build_evidence(session, results)

    assert evidence["profile"] == "repository-full"
    assert evidence["profile_version"] == "1"
    assert evidence["expected_checks"] == ["pytest", "ruff", "mypy", "pre-commit"]
    assert evidence["executed_checks"] == evidence["expected_checks"]
    assert evidence["complete"] is True
    assert evidence["status"] == "passed"
    assert evidence["machine_code"] == "OK"
    assert gate.gate_evidence_is_passing(evidence) is True
    assert gate.is_repository_full(evidence) is True


def test_missing_mandatory_check_is_incomplete(repository: Path) -> None:
    with _session(repository) as session:
        results = gate.run_profile(
            session, checks=("pytest", "ruff", "mypy"), runner=_runner()
        )
        evidence = gate.build_evidence(session, results)

    assert evidence["complete"] is False
    assert evidence["status"] == "incomplete"
    assert evidence["machine_code"] == "GATE_EVIDENCE_INCOMPLETE"
    assert evidence["executed_checks"] == ["pytest", "ruff", "mypy"]
    assert gate.gate_evidence_is_passing(evidence) is False


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        ("unavailable", None),
        ("timeout", None),
        ("interrupted", -9),
        ("failed", 1),
    ],
)
def test_non_success_statuses_are_not_success(
    repository: Path, status: str, exit_code: int | None
) -> None:
    with _session(repository) as session:
        results = gate.run_profile(
            session, runner=_runner({"mypy": _outcome(status, exit_code)})
        )
        evidence = gate.build_evidence(session, results)

    checks = {item["id"]: item for item in evidence["checks"]}
    assert checks["mypy"]["status"] == status
    assert checks["mypy"]["exit_code"] == exit_code
    assert evidence["complete"] is True
    assert evidence["status"] == "failed"
    assert evidence["machine_code"] == "GATE_FAILED"
    assert gate.gate_evidence_is_passing(evidence) is False


def test_malformed_pytest_summary_is_parse_error(repository: Path) -> None:
    with _session(repository) as session:
        results = gate.run_profile(
            session, runner=_runner({"pytest": _outcome("passed", 0, "no summary")})
        )
        evidence = gate.build_evidence(session, results)

    checks = {item["id"]: item for item in evidence["checks"]}
    assert checks["pytest"]["status"] == "parse_error"
    assert evidence["status"] == "failed"
    assert gate.gate_evidence_is_passing(evidence) is False


def test_missing_pytest_counters_stay_unknown(repository: Path) -> None:
    with _session(repository) as session:
        results = gate.run_profile(session, runner=_runner())

    metrics = {result.id: result.metrics for result in results}["pytest"]
    assert metrics["passed"] == 746
    assert metrics["skipped"] == 7
    assert metrics["failed"] is None
    assert metrics["xfailed"] is None
    assert metrics["xpassed"] is None


def test_mypy_without_source_file_count_stays_unknown(repository: Path) -> None:
    with _session(repository) as session:
        results = gate.run_profile(
            session, runner=_runner({"mypy": _outcome("passed", 0, "Success")})
        )

    metrics = {result.id: result.metrics for result in results}["mypy"]
    assert metrics["source_files"] is None


def test_ruff_statuses_are_derived_from_exit_code(repository: Path) -> None:
    with _session(repository) as session:
        silent = gate.run_profile(
            session, runner=_runner({"ruff": _outcome("passed", 0, "")})
        )
        failing = gate.run_profile(
            session,
            runner=_runner({"ruff": _outcome("failed", 1, "Found 3 errors.")}),
        )

    assert {item.id: item.status for item in silent}["ruff"] == "parse_error"
    failed = {item.id: item for item in failing}["ruff"]
    assert failed.status == "failed"
    assert failed.metrics["errors"] == 3


def test_precommit_hook_outcomes_are_recorded(repository: Path) -> None:
    with _session(repository) as session:
        results = gate.run_profile(session, runner=_runner())

    metrics = {result.id: result.metrics for result in results}["pre-commit"]
    assert metrics["hooks"] == [
        {"id": "Detect secrets", "outcome": "passed"},
        {"id": "trim trailing whitespace", "outcome": "passed"},
    ]


def test_diff_only_profile_is_not_the_full_profile(repository: Path) -> None:
    with _session(repository) as session:
        results = gate.run_profile(
            session,
            profile="repository-diff-only",
            runner=_runner({"git": _outcome("passed", 0, "")}),
        )
        evidence = gate.build_evidence(
            session, results, profile="repository-diff-only"
        )

    assert evidence["status"] == "passed"
    assert evidence["complete"] is True
    assert gate.is_repository_full(evidence) is False


def test_foreign_check_is_rejected(repository: Path) -> None:
    with _session(repository) as session:
        with pytest.raises(gate.GateError) as error:
            gate.run_profile(session, checks=("diff",), runner=_runner())

    assert error.value.machine_code == "GATE_PROFILE_MISMATCH"


def test_unknown_profile_is_rejected(repository: Path) -> None:
    with _session(repository) as session:
        with pytest.raises(gate.GateError) as error:
            gate.run_profile(session, profile="repository-invented")

    assert error.value.machine_code == "GATE_PROFILE_MISMATCH"


def test_evidence_contains_no_paths_or_private_artifacts(repository: Path) -> None:
    with _session(repository) as session:
        checkout = str(session.checkout)
        results = gate.run_profile(session, runner=_runner())
        evidence = gate.build_evidence(session, results)

    rendered = json.dumps(evidence, ensure_ascii=False)
    assert checkout not in rendered
    assert str(repository) not in rendered
    assert str(Path.home()) not in rendered
    assert "log_directory" not in rendered
    for check in evidence["checks"]:
        assert check["cwd"] == "<snapshot-checkout>"
        assert all(os.sep not in item for item in check["argv"])
        assert "/" not in check["summary"]
    for pattern in agent_orchestrate.PRIVATE_PATTERNS:
        assert pattern not in rendered


def test_truncated_output_does_not_turn_failure_into_success(
    repository: Path,
) -> None:
    noise = "746 passed, 7 skipped in 9.05s\n" + ("x" * 400_000)

    with _session(repository) as session:
        results = gate.run_profile(
            session, runner=_runner({"pytest": _outcome("failed", 1, noise)})
        )
        evidence = gate.build_evidence(session, results)

    checks = {item["id"]: item for item in evidence["checks"]}
    assert checks["pytest"]["status"] == "failed"
    assert checks["pytest"]["exit_code"] == 1
    assert len(checks["pytest"]["summary"]) <= 200
    assert evidence["status"] == "failed"


def test_real_command_runs_inside_snapshot_checkout(repository: Path) -> None:
    probe = (
        sys.executable,
        "-c",
        "print(open('pkg/new_unit.py').read().strip());print('0 passed')",
    )

    with _session(repository) as session:
        results = gate.run_profile(
            session,
            checks=("pytest",),
            commands={"pytest": probe},
        )

    result = {item.id: item for item in results}["pytest"]
    assert result.status == "passed"
    assert result.exit_code == 0
    assert result.duration_seconds >= 0.0
