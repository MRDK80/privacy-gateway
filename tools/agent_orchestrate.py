#!/usr/bin/env python3
"""Budgeted, fail-closed executor/controller orchestration for agent tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.agent_memory import (  # noqa: E402
    MemoryError as AgentMemoryError,
)
from tools.agent_memory import (
    RetrospectiveStore,
    Usage,
    new_record,
)
from tools.agent_memory import (
    default_directory as default_memory_directory,
)

SCHEMA_VERSION = "1.0"
MAX_CAPTURE_CHARS = 200_000
POLICY_FILES = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/SECURITY.md",
    "docs/ARCHITECTURE.md",
    "docs/threat-model.md",
    "docs/agent-contracts.md",
)
PROTECTED_PATTERNS = (
    "AGENTS.md",
    ".agents/",
    ".codex/",
    ".github/hooks/",
    ".github/workflows/",
    "docs/schemas/",
    "docs/agent-contracts.md",
)
PRIVATE_PATTERNS = (
    ".agent-private/",
    ".agent-logs/",
    "agent-memory/",
    "raw-retrospectives/",
    "controller-notes/",
    "pending-lessons/",
    "known-pitfalls/",
    "usage-metrics/",
    "config.local/",
)


class OrchestrationError(Exception):
    """Controlled failure whose message is safe to report."""

    def __init__(self, machine_code: str) -> None:
        super().__init__(machine_code)
        self.machine_code = machine_code


@dataclass(frozen=True)
class ActionPermissions:
    commit: bool = False
    push: bool = False
    create_pr: bool = False
    comment: bool = False
    merge: bool = False


@dataclass(frozen=True)
class TaskContract:
    issue: int
    epic: int
    acceptance_criteria: tuple[str, ...]
    base_ref: str
    base_sha: str
    head_ref: str
    allowed_paths: tuple[str, ...]
    task_class: str = "unspecified"
    max_minutes: int = 60
    max_repair_iterations: int = 2
    max_report_chars: int = 20_000
    allowed_tools: tuple[str, ...] = ("workspace", "git-read", "quality-gate")
    permissions: ActionPermissions = field(default_factory=ActionPermissions)


@dataclass(frozen=True)
class RunResult:
    status: str
    machine_code: str
    repair_iterations: int
    run_id: str


class AgentAdapter(Protocol):
    def execute(self, request: dict[str, Any], session_id: str) -> dict[str, Any]: ...

    def review(
        self,
        request: dict[str, Any],
        trusted_policy: dict[str, str],
        session_id: str,
    ) -> dict[str, Any]: ...


Gate = Callable[[TaskContract], dict[str, Any]]


ALLOWED_PATH_MAX_CHARS = 4096


def _denied_scope_path(path: str) -> bool:
    """Report whether a repository-relative path is never delegable (#180)."""
    if path == "AGENTS.md" or path.endswith("/AGENTS.md"):
        return True
    for pattern in (*PROTECTED_PATTERNS, *PRIVATE_PATTERNS, *POLICY_FILES):
        bare = pattern.rstrip("/")
        if path == bare or path.startswith(f"{bare}/"):
            return True
    return False


def normalize_allowed_path(root: Path, value: str) -> str:
    """Normalize one repository-relative allowlist entry or fail closed."""
    if not value or value != value.strip():
        raise OrchestrationError("ALLOWED_PATH_INVALID")
    if len(value) > ALLOWED_PATH_MAX_CHARS:
        raise OrchestrationError("ALLOWED_PATH_INVALID")
    if "\x00" in value or "\\" in value or value.startswith(("/", "~")):
        raise OrchestrationError("ALLOWED_PATH_INVALID")
    if len(value) >= 2 and value[1] == ":":
        raise OrchestrationError("ALLOWED_PATH_INVALID")
    segments = value.rstrip("/").split("/")
    if any(
        segment in {"", ".", ".."} or segment != segment.strip()
        for segment in segments
    ):
        raise OrchestrationError("ALLOWED_PATH_INVALID")
    normalized = PurePosixPath("/".join(segments)).as_posix()
    anchor = Path(os.path.realpath(root))
    target = Path(os.path.realpath(anchor / normalized))
    if target == anchor or anchor not in target.parents:
        raise OrchestrationError("ALLOWED_PATH_ESCAPES_ROOT")
    if _denied_scope_path(normalized):
        raise OrchestrationError("ALLOWED_PATH_PROTECTED")
    return normalized


def normalize_allowed_paths(root: Path, values: Sequence[str]) -> tuple[str, ...]:
    """Normalize the allowlist, preserving order and dropping duplicates."""
    normalized: list[str] = []
    for value in values:
        candidate = normalize_allowed_path(root, value)
        if candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)
def _run(root: Path, command: Sequence[str], timeout: float | None = None) -> str:
    try:
        completed = subprocess.run(
            list(command),
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OrchestrationError("COMMAND_UNAVAILABLE") from error
    if completed.returncode != 0:
        raise OrchestrationError("COMMAND_FAILED")
    return completed.stdout[:MAX_CAPTURE_CHARS]


def _git(root: Path, *args: str) -> str:
    return _run(root, ("git", *args)).strip()


def _safe_ref(root: Path, value: str) -> bool:
    try:
        _run(root, ("git", "check-ref-format", "--branch", value))
    except OrchestrationError:
        return False
    return bool(value) and not value.startswith("-")


def build_contract(
    *,
    issue: int,
    epic: int,
    issue_text: str,
    acceptance_criteria: Sequence[str],
    base_ref: str,
    head_ref: str,
    root: Path,
    allowed_paths: Sequence[str] = (),
    task_class: str = "unspecified",
    max_minutes: int = 60,
    max_repair_iterations: int = 2,
    max_report_chars: int = 20_000,
    permissions: ActionPermissions | None = None,
) -> TaskContract:
    """Build a validated contract; issue text is deliberately treated as data only."""
    del issue_text
    if issue < 1 or epic < 1 or not acceptance_criteria:
        raise OrchestrationError("INVALID_CONTRACT")
    if not base_ref.startswith("roadmap/") or not _safe_ref(root, base_ref):
        raise OrchestrationError("INVALID_CONTRACT")
    if not _safe_ref(root, head_ref) or head_ref.startswith("roadmap/"):
        raise OrchestrationError("INVALID_CONTRACT")
    if not 0 <= max_repair_iterations <= 2:
        raise OrchestrationError("INVALID_CONTRACT")
    if max_minutes < 1 or max_report_chars < 1:
        raise OrchestrationError("INVALID_CONTRACT")
    base_sha = _git(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    return TaskContract(
        issue=issue,
        epic=epic,
        acceptance_criteria=tuple(acceptance_criteria),
        base_ref=base_ref,
        base_sha=base_sha,
        head_ref=head_ref,
        allowed_paths=normalize_allowed_paths(root, allowed_paths),
        task_class=task_class,
        max_minutes=max_minutes,
        max_repair_iterations=max_repair_iterations,
        max_report_chars=max_report_chars,
        permissions=permissions or ActionPermissions(),
    )


class StateStore:
    """Private, owner-only and atomically updated run state outside the repository."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, run_key: str) -> Path:
        return self.directory / f"{run_key}.json"

    def load(self, run_key: str) -> dict[str, Any] | None:
        path = self._path(run_key)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise OrchestrationError("INVALID_STATE") from error
        if not isinstance(value, dict):
            raise OrchestrationError("INVALID_STATE")
        return cast(dict[str, Any], value)

    def save(self, run_key: str, value: dict[str, Any]) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
            temporary = self.directory / f".{run_key}.{uuid.uuid4().hex}.tmp"
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path(run_key))
        except OSError as error:
            raise OrchestrationError("STATE_WRITE_FAILED") from error


def default_storage() -> StateStore:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return StateStore(base / "privacy-gateway" / "agent-runs")


def _run_key(contract: TaskContract) -> str:
    payload = f"{contract.issue}\0{contract.base_sha}\0{contract.head_ref}"
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _head_sha(root: Path, contract: TaskContract) -> str:
    current = _git(root, "branch", "--show-current")
    if current != contract.head_ref:
        raise OrchestrationError("WRONG_BRANCH")
    actual_base = _git(root, "rev-parse", "--verify", f"{contract.base_ref}^{{commit}}")
    if actual_base != contract.base_sha:
        raise OrchestrationError("POLICY_PROVENANCE_MISMATCH")
    try:
        _git(root, "merge-base", "--is-ancestor", contract.base_sha, "HEAD")
    except OrchestrationError as error:
        raise OrchestrationError("POLICY_PROVENANCE_MISMATCH") from error
    return _git(root, "rev-parse", "HEAD")


def _changed_files(root: Path, contract: TaskContract) -> list[str]:
    committed = _run(
        root, ("git", "diff", "--name-only", "-z", f"{contract.base_sha}...HEAD")
    )
    working = _run(root, ("git", "diff", "--name-only", "-z"))
    staged = _run(root, ("git", "diff", "--cached", "--name-only", "-z"))
    untracked = _run(root, ("git", "ls-files", "--others", "--exclude-standard", "-z"))
    return sorted(
        {
            item
            for output in (committed, working, staged, untracked)
            for item in output.split("\0")
            if item
        }
    )


def _matches_prefix(path: str, patterns: Sequence[str]) -> bool:
    return any(path == pattern or path.startswith(pattern) for pattern in patterns)


def _assert_private_artifacts_safe(root: Path) -> None:
    indexed = _run(root, ("git", "ls-files", "-z")).split("\0")
    staged = _run(root, ("git", "diff", "--cached", "--name-only", "-z")).split("\0")
    if any(_matches_prefix(path, PRIVATE_PATTERNS) for path in (*indexed, *staged)):
        raise OrchestrationError("PRIVATE_ARTIFACT_STAGED")


def _assert_scope(paths: Sequence[str], contract: TaskContract) -> None:
    if any(
        _matches_prefix(path, (*PROTECTED_PATTERNS, *POLICY_FILES))
        or path.endswith("/AGENTS.md")
        for path in paths
    ):
        raise OrchestrationError("HEAD_POLICY_CHANGED")
    allowed = tuple(
        PurePosixPath(item).as_posix().rstrip("/") for item in contract.allowed_paths
    )
    if not allowed:
        if paths:
            raise OrchestrationError("ALLOWLIST_REQUIRED")
        return
    for path in paths:
        if not any(path == item or path.startswith(f"{item}/") for item in allowed):
            raise OrchestrationError("SCOPE_VIOLATION")


def _load_trusted_policy(root: Path, contract: TaskContract) -> dict[str, str]:
    policy: dict[str, str] = {}
    for path in POLICY_FILES:
        try:
            content = _git(root, "show", f"{contract.base_sha}:{path}")
        except OrchestrationError:
            continue
        policy[path] = content
    if "AGENTS.md" not in policy or "CONTRIBUTING.md" not in policy:
        raise OrchestrationError("POLICY_PROVENANCE_MISMATCH")
    return policy


def _diff(root: Path, contract: TaskContract, limit: int) -> str:
    text = _run(root, ("git", "diff", "--no-ext-diff", contract.base_sha))
    untracked = _run(root, ("git", "ls-files", "--others", "--exclude-standard", "-z"))
    for path in (item for item in untracked.split("\0") if item):
        try:
            completed = subprocess.run(
                ["git", "diff", "--no-index", "--", "/dev/null", path],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise OrchestrationError("COMMAND_UNAVAILABLE") from error
        if completed.returncode not in {0, 1}:
            raise OrchestrationError("COMMAND_FAILED")
        text += completed.stdout
    if len(text) > limit:
        raise OrchestrationError("OUTPUT_LIMIT")
    return text


def _validate_report(value: Any, contract: TaskContract, head_sha: str) -> None:
    if not isinstance(value, dict):
        raise OrchestrationError("MALFORMED_OUTPUT")
    required = {
        "schema_version",
        "role",
        "task_issue",
        "base_sha",
        "head_sha",
        "status",
        "acceptance_criteria",
        "changed_files",
        "checks",
        "residual_risks",
        "stop_reason",
    }
    if set(value) != required:
        raise OrchestrationError("MALFORMED_OUTPUT")
    expected = ("1.0", "executor", contract.issue, contract.base_sha, head_sha)
    actual = tuple(
        value[key]
        for key in ("schema_version", "role", "task_issue", "base_sha", "head_sha")
    )
    if actual != expected or value["status"] not in {
        "completed",
        "blocked",
        "needs_human",
    }:
        raise OrchestrationError("MALFORMED_OUTPUT")
    if len(json.dumps(value, ensure_ascii=False)) > contract.max_report_chars:
        raise OrchestrationError("OUTPUT_LIMIT")


def _validate_verdict(value: Any, contract: TaskContract, head_sha: str) -> str:
    if not isinstance(value, dict):
        raise OrchestrationError("MALFORMED_OUTPUT")
    required = {
        "schema_version",
        "role",
        "task_issue",
        "base_sha",
        "head_sha",
        "review_basis",
        "verdict",
        "repair_iteration",
        "escalation_reason",
        "blocking_findings",
        "notes",
    }
    if set(value) != required:
        raise OrchestrationError("MALFORMED_OUTPUT")
    expected = ("1.0", "controller", contract.issue, contract.base_sha, head_sha)
    actual = tuple(
        value[key]
        for key in ("schema_version", "role", "task_issue", "base_sha", "head_sha")
    )
    basis = value.get("review_basis")
    valid_basis = isinstance(basis, dict) and basis == {
        "trust_source_kind": "base_sha",
        "head_policy_applied": False,
        "executor_self_assessment_treated_as_evidence_only": True,
    }
    verdict = value.get("verdict")
    if (
        actual != expected
        or not valid_basis
        or verdict not in {"PASS", "PASS_WITH_NOTES", "FAIL_RETRY", "FAIL_ESCALATE"}
    ):
        raise OrchestrationError("MALFORMED_OUTPUT")
    findings = value.get("blocking_findings")
    if not isinstance(findings, list):
        raise OrchestrationError("MALFORMED_OUTPUT")
    if verdict in {"PASS", "PASS_WITH_NOTES"} and findings:
        raise OrchestrationError("MALFORMED_OUTPUT")
    if verdict in {"FAIL_RETRY", "FAIL_ESCALATE"} and not findings:
        raise OrchestrationError("MALFORMED_OUTPUT")
    if len(json.dumps(value, ensure_ascii=False)) > contract.max_report_chars:
        raise OrchestrationError("OUTPUT_LIMIT")
    return cast(str, verdict)


def _default_gate(root: Path, contract: TaskContract) -> dict[str, Any]:
    command = (
        sys.executable,
        str(Path(__file__).with_name("agent_verify.py")),
        "pre-commit",
        "--base",
        contract.base_ref,
        "--head",
        contract.head_ref,
        "--format",
        "json",
        "--root",
        str(root),
    )
    try:
        completed = subprocess.run(
            list(command), cwd=root, capture_output=True, text=True, check=False
        )
    except OSError as error:
        raise OrchestrationError("COMMAND_UNAVAILABLE") from error
    try:
        result = json.loads(completed.stdout)
    except ValueError as error:
        raise OrchestrationError("GATE_MALFORMED") from error
    if not isinstance(result, dict):
        raise OrchestrationError("GATE_MALFORMED")
    if result.get("exit_code") != completed.returncode:
        raise OrchestrationError("GATE_MALFORMED")
    if result.get("status") == "passed":
        changed = result.get("changed_files")
        if not isinstance(changed, list) or not all(
            isinstance(path, str) for path in changed
        ):
            raise OrchestrationError("GATE_MALFORMED")
        try:
            secret_scan = subprocess.run(
                ["pre-commit", "run", "detect-secrets", "--files", *changed],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise OrchestrationError("PUBLIC_PATCH_SCAN_UNAVAILABLE") from error
        result["public_patch_scan"] = {
            "name": "detect-secrets",
            "status": "passed" if secret_scan.returncode == 0 else "failed",
            "exit_code": secret_scan.returncode,
        }
        if secret_scan.returncode != 0:
            result["status"] = "failed"
            result["machine_code"] = "PUBLIC_PATCH_SCAN_FAILED"
    return cast(dict[str, Any], result)


def run(
    contract: TaskContract,
    *,
    root: Path,
    storage: StateStore,
    adapter: AgentAdapter,
    gate: Gate | None = None,
    memory: RetrospectiveStore | None = None,
    usage: Usage = Usage(),
) -> RunResult:
    """Run or safely resume the bounded executor/gate/controller loop."""
    try:
        storage.directory.resolve().relative_to(root.resolve())
    except ValueError:
        pass
    else:
        return RunResult("FAIL_ESCALATE", "UNSAFE_STORAGE", 0, "not-started")
    if not contract.allowed_paths:
        return RunResult("FAIL_ESCALATE", "ALLOWLIST_REQUIRED", 0, "not-started")
    run_key = _run_key(contract)
    previous = storage.load(run_key)
    if previous and previous.get("terminal") is True:
        return RunResult(
            status=str(previous["status"]),
            machine_code=str(previous["machine_code"]),
            repair_iterations=int(previous["repair_iterations"]),
            run_id=str(previous["run_id"]),
        )
    run_id = str(previous.get("run_id")) if previous else uuid.uuid4().hex
    started = (
        float(previous.get("started", time.monotonic()))
        if previous
        else time.monotonic()
    )
    repairs = int(previous.get("repair_iterations", 0)) if previous else 0
    executor_calls = int(previous.get("executor_calls", 0)) if previous else 0
    controller_calls = int(previous.get("controller_calls", 0)) if previous else 0
    findings = int(previous.get("findings", 0)) if previous else 0
    verdicts: list[str] = list(previous.get("verdicts", [])) if previous else []
    failures: list[str] = list(previous.get("failures", [])) if previous else []
    head_sha_for_memory = contract.base_sha
    memory_store = memory or RetrospectiveStore(
        storage.directory.parent / "agent-memory", root
    )

    def finish(status: str, machine_code: str) -> RunResult:
        result = RunResult(status, machine_code, repairs, run_id)
        final_verdicts = list(verdicts)
        if status in {"PASS", "PASS_WITH_NOTES", "FAIL_ESCALATE"} and (
            not final_verdicts or final_verdicts[-1] != status
        ):
            final_verdicts.append(status)
        final_failures = list(failures)
        if machine_code != "OK":
            final_failures.append(machine_code)
        try:
            memory_store.append(
                new_record(
                    record_id=run_id,
                    task_issue=contract.issue,
                    epic_issue=contract.epic,
                    task_class=contract.task_class,
                    base_sha=contract.base_sha,
                    head_sha=head_sha_for_memory,
                    duration_seconds=max(0, int(time.monotonic() - started)),
                    executor_calls=executor_calls,
                    controller_calls=controller_calls,
                    iterations=controller_calls,
                    repair_loops=repairs,
                    verdicts=tuple(final_verdicts or ("FAIL_ESCALATE",)),
                    failures=tuple(final_failures),
                    false_positives=0,
                    manual_interventions=0,
                    findings=findings,
                    usage=usage,
                )
            )
        except AgentMemoryError:
            result = RunResult("FAIL_ESCALATE", "MEMORY_WRITE_FAILED", repairs, run_id)
        storage.save(
            run_key,
            {
                **asdict(result),
                "terminal": True,
                "started": started,
                "executor_calls": executor_calls,
                "controller_calls": controller_calls,
                "findings": findings,
                "verdicts": final_verdicts,
                "failures": final_failures,
            },
        )
        return result

    try:
        head_sha = _head_sha(root, contract)
        head_sha_for_memory = head_sha
        _assert_private_artifacts_safe(root)
        paths = _changed_files(root, contract)
        _assert_scope(paths, contract)
        trusted_policy = _load_trusted_policy(root, contract)
        gate_runner = gate or (lambda value: _default_gate(root, value))
        while True:
            if time.monotonic() - started > contract.max_minutes * 60:
                return finish("FAIL_ESCALATE", "TIME_BUDGET_EXHAUSTED")
            executor_session = uuid.uuid4().hex
            executor_calls += 1
            report = adapter.execute(
                {
                    "contract": asdict(contract),
                    "contract_object": contract,
                    "head_sha": head_sha,
                    "repair_iteration": repairs,
                },
                executor_session,
            )
            _validate_report(report, contract, head_sha)
            if _head_sha(root, contract) != head_sha:
                return finish("FAIL_ESCALATE", "UNAUTHORIZED_HEAD_CHANGE")
            _assert_private_artifacts_safe(root)
            paths = _changed_files(root, contract)
            _assert_scope(paths, contract)
            gate_result = gate_runner(contract)
            if gate_result.get("status") != "passed":
                failures.append(str(gate_result.get("machine_code", "GATE_FAILED")))
                if repairs >= contract.max_repair_iterations:
                    return finish("FAIL_ESCALATE", "GATE_FAILED")
                repairs += 1
                storage.save(
                    run_key,
                    {
                        "run_id": run_id,
                        "started": started,
                        "repair_iterations": repairs,
                        "terminal": False,
                        "status": "running",
                        "machine_code": "GATE_FAILED",
                        "executor_calls": executor_calls,
                        "controller_calls": controller_calls,
                        "findings": findings,
                        "verdicts": verdicts,
                        "failures": failures,
                    },
                )
                continue
            diff = _diff(root, contract, contract.max_report_chars)
            controller_session = uuid.uuid4().hex
            controller_calls += 1
            verdict_payload = adapter.review(
                {
                    "issue": contract.issue,
                    "acceptance_criteria": list(contract.acceptance_criteria),
                    "diff": diff,
                    "gate_evidence": gate_result,
                    "base_sha": contract.base_sha,
                    "head_sha": head_sha,
                    "repair_iteration": repairs,
                },
                trusted_policy,
                controller_session,
            )
            verdict = _validate_verdict(verdict_payload, contract, head_sha)
            verdicts.append(verdict)
            findings += len(verdict_payload["blocking_findings"])
            if verdict in {"PASS", "PASS_WITH_NOTES"}:
                return finish(verdict, "OK")
            if verdict == "FAIL_ESCALATE" or repairs >= contract.max_repair_iterations:
                return finish("FAIL_ESCALATE", "REVIEW_ESCALATED")
            repairs += 1
            storage.save(
                run_key,
                {
                    "run_id": run_id,
                    "started": started,
                    "repair_iterations": repairs,
                    "terminal": False,
                    "status": "running",
                    "machine_code": "REPAIR_REQUIRED",
                    "executor_calls": executor_calls,
                    "controller_calls": controller_calls,
                    "findings": findings,
                    "verdicts": verdicts,
                    "failures": failures,
                },
            )
    except OrchestrationError as error:
        return finish("FAIL_ESCALATE", error.machine_code)
    except Exception:
        return finish("FAIL_ESCALATE", "INTERNAL_ERROR")


class CommandAdapter:
    """Provider-neutral JSON-over-stdin adapter using a fresh process per role call."""

    def __init__(
        self,
        executor_command: Sequence[str],
        controller_command: Sequence[str],
        *,
        root: Path,
        timeout_seconds: int,
        output_limit: int,
    ) -> None:
        self.executor_command = tuple(executor_command)
        self.controller_command = tuple(controller_command)
        self.root = root
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit

    def _call(self, command: Sequence[str], payload: dict[str, Any]) -> dict[str, Any]:
        safe_payload = {
            key: value for key, value in payload.items() if key != "contract_object"
        }
        try:
            completed = subprocess.run(
                list(command),
                cwd=self.root,
                input=json.dumps(safe_payload),
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise OrchestrationError("ADAPTER_TIMEOUT") from error
        except OSError as error:
            raise OrchestrationError("MODEL_UNAVAILABLE") from error
        if completed.returncode != 0:
            raise OrchestrationError("MODEL_UNAVAILABLE")
        if len(completed.stdout) > self.output_limit:
            raise OrchestrationError("OUTPUT_LIMIT")
        try:
            value = json.loads(completed.stdout)
        except ValueError as error:
            raise OrchestrationError("MALFORMED_OUTPUT") from error
        if not isinstance(value, dict):
            raise OrchestrationError("MALFORMED_OUTPUT")
        return cast(dict[str, Any], value)

    def execute(self, request: dict[str, Any], session_id: str) -> dict[str, Any]:
        return self._call(self.executor_command, {**request, "session_id": session_id})

    def review(
        self,
        request: dict[str, Any],
        trusted_policy: dict[str, str],
        session_id: str,
    ) -> dict[str, Any]:
        return self._call(
            self.controller_command,
            {**request, "trusted_policy": trusted_policy, "session_id": session_id},
        )


def _json_command(value: str) -> list[str]:
    try:
        command = json.loads(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("command must be a JSON array") from error
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item for item in command)
    ):
        raise argparse.ArgumentTypeError(
            "command must be a non-empty JSON string array"
        )
    return command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue", type=int)
    parser.add_argument("--epic", type=int, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--criterion", action="append", required=True)
    parser.add_argument("--executor-command", type=_json_command)
    parser.add_argument("--controller-command", type=_json_command)
    parser.add_argument("--max-minutes", type=int, default=60)
    parser.add_argument("--max-repairs", type=int, default=2)
    parser.add_argument("--max-report-chars", type=int, default=20_000)
    parser.add_argument("--task-class", default="unspecified")
    parser.add_argument("--storage", type=Path)
    parser.add_argument("--memory-storage", type=Path)
    parser.add_argument(
        "--allowed-path",
        action="append",
        metavar="PATH",
        help=(
            "repository-relative file or directory the executor may change; "
            "repeatable, required for a real run, rejects '.', absolute paths, "
            "traversal, symlink escape and protected or private policy paths"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        contract = build_contract(
            issue=args.issue,
            epic=args.epic,
            issue_text="",
            acceptance_criteria=args.criterion,
            base_ref=args.base,
            head_ref=args.head,
            root=args.root,
            max_minutes=args.max_minutes,
            max_repair_iterations=args.max_repairs,
            max_report_chars=args.max_report_chars,
            task_class=args.task_class,
            allowed_paths=args.allowed_path or (),
        )
        if args.dry_run:
            _head_sha(args.root, contract)
            _assert_private_artifacts_safe(args.root)
            if contract.allowed_paths:
                _assert_scope(_changed_files(args.root, contract), contract)
            _load_trusted_policy(args.root, contract)
            print(
                json.dumps(
                    {
                        "status": "DRY_RUN",
                        "contract": asdict(contract),
                        "scope": {
                            "mode": (
                                "explicit"
                                if contract.allowed_paths
                                else "unscoped_dry_run"
                            ),
                            "allowed_paths": list(contract.allowed_paths),
                            "enforced": bool(contract.allowed_paths),
                        },
                    },
                    sort_keys=True,
                )
            )
            return 0
        if not contract.allowed_paths:
            raise OrchestrationError("ALLOWLIST_REQUIRED")
        if not args.executor_command or not args.controller_command:
            raise OrchestrationError("ADAPTER_REQUIRED")
        adapter = CommandAdapter(
            args.executor_command,
            args.controller_command,
            root=args.root,
            timeout_seconds=args.max_minutes * 60,
            output_limit=args.max_report_chars,
        )
        result = run(
            contract,
            root=args.root,
            storage=StateStore(args.storage) if args.storage else default_storage(),
            adapter=adapter,
            memory=RetrospectiveStore(
                args.memory_storage or default_memory_directory(), args.root
            ),
        )
    except (OrchestrationError, AgentMemoryError) as error:
        result = RunResult("FAIL_ESCALATE", error.machine_code, 0, "not-started")
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.status in {"PASS", "PASS_WITH_NOTES"} else 20


if __name__ == "__main__":
    raise SystemExit(main())
