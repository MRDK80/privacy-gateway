#!/usr/bin/env python3
"""Budgeted, fail-closed executor/controller orchestration for agent tasks."""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import agent_gate  # noqa: E402
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
from tools.schema_validate import SchemaError, load_schema  # noqa: E402

SCHEMA_VERSION = "1.0"
CONTROLLER_VERDICT_SCHEMA = Path("docs") / "schemas" / "controller-verdict.schema.json"
TRUST_BOUNDARY_INVARIANTS = {
    "head_policy_applied": False,
    "executor_self_assessment_treated_as_evidence_only": True,
}
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
        segment in {"", ".", ".."} or segment != segment.strip() for segment in segments
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


SNAPSHOT_METHOD = "commit-tree"
SNAPSHOT_MESSAGE = "privacy-gateway trusted snapshot"
SNAPSHOT_IDENTITY: dict[str, str] = {
    "GIT_AUTHOR_NAME": "privacy-gateway orchestrator",
    "GIT_AUTHOR_EMAIL": "orchestrator@privacy-gateway.invalid",
    "GIT_AUTHOR_DATE": "1970-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "privacy-gateway orchestrator",
    "GIT_COMMITTER_EMAIL": "orchestrator@privacy-gateway.invalid",
    "GIT_COMMITTER_DATE": "1970-01-01T00:00:00+00:00",
}


@dataclass(frozen=True)
class SnapshotEvidence:
    """Provenance одного проверенного состояния рабочего дерева (#196)."""

    base_sha: str
    snapshot_method: str
    snapshot_commit: str
    tree_hash: str
    diff_sha256: str
    provenance_complete: bool
    allowed_paths: tuple[str, ...]


def _snapshot_environment(**overrides: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(SNAPSHOT_IDENTITY)
    environment.update(overrides)
    return environment


def _snapshot_run(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
) -> bytes:
    """Выполнить git в bytes-режиме и упасть закрыто на любой проблеме."""
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            capture_output=True,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise OrchestrationError("SNAPSHOT_FAILED") from error
    if completed.returncode != 0:
        raise OrchestrationError("SNAPSHOT_FAILED")
    return completed.stdout


def _snapshot_changed_paths(root: Path, base_sha: str) -> list[str]:
    """Собрать изменённые пути, включая staged и разрешённые untracked."""
    environment = _snapshot_environment()
    sources = (
        ("diff", "--name-only", "-z", f"{base_sha}...HEAD"),
        ("diff", "--name-only", "-z"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    found: set[str] = set()
    for source in sources:
        output = _snapshot_run(source, cwd=root, environment=environment)
        found.update(
            item for item in output.decode("utf-8", "strict").split("\0") if item
        )
    return sorted(found)


def _assert_snapshot_scope(paths: Sequence[str], allowed: Sequence[str]) -> None:
    """Запретить snapshot для protected, policy и out-of-scope путей."""
    if any(
        _matches_prefix(path, (*PROTECTED_PATTERNS, *POLICY_FILES))
        or path.endswith("/AGENTS.md")
        for path in paths
    ):
        raise OrchestrationError("SNAPSHOT_SCOPE_VIOLATION")
    scope = tuple(PurePosixPath(item).as_posix().rstrip("/") for item in allowed)
    if not scope:
        raise OrchestrationError("SNAPSHOT_SCOPE_VIOLATION")
    for path in paths:
        if not any(path == item or path.startswith(f"{item}/") for item in scope):
            raise OrchestrationError("SNAPSHOT_SCOPE_VIOLATION")


def _build_snapshot_state(
    root: Path, base_sha: str, allowed: Sequence[str]
) -> tuple[str, str, str]:
    """Создать snapshot в disposable clone и вернуть tree, commit и diff hash."""
    anchor = Path(os.path.realpath(root))
    paths = _snapshot_changed_paths(anchor, base_sha)
    _assert_snapshot_scope(paths, allowed)
    workspace = Path(tempfile.mkdtemp(prefix="pgw-snapshot-"))
    try:
        mirror = workspace / "mirror.git"
        index = workspace / "snapshot-index"
        _snapshot_run(
            ("clone", "--quiet", "--bare", "--no-hardlinks", str(anchor), str(mirror)),
            cwd=workspace,
            environment=_snapshot_environment(),
        )
        environment = _snapshot_environment(
            GIT_DIR=str(mirror),
            GIT_WORK_TREE=str(anchor),
            GIT_INDEX_FILE=str(index),
        )
        _snapshot_run(("read-tree", base_sha), cwd=anchor, environment=environment)
        if paths:
            _snapshot_run(
                ("add", "--all", "--", *paths), cwd=anchor, environment=environment
            )
        tree_hash = (
            _snapshot_run(("write-tree",), cwd=anchor, environment=environment)
            .decode("utf-8", "strict")
            .strip()
        )
        snapshot_commit = (
            _snapshot_run(
                ("commit-tree", tree_hash, "-p", base_sha, "-m", SNAPSHOT_MESSAGE),
                cwd=anchor,
                environment=environment,
            )
            .decode("utf-8", "strict")
            .strip()
        )
        diff = _snapshot_run(
            ("diff", "--binary", "--no-ext-diff", base_sha, tree_hash),
            cwd=anchor,
            environment=environment,
        )
        return tree_hash, snapshot_commit, hashlib.sha256(diff).hexdigest()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def create_trusted_snapshot(contract: TaskContract, *, root: Path) -> SnapshotEvidence:
    """Зафиксировать проверяемое состояние рабочего дерева без записи refs."""
    tree_hash, snapshot_commit, diff_sha256 = _build_snapshot_state(
        root, contract.base_sha, contract.allowed_paths
    )
    return SnapshotEvidence(
        base_sha=contract.base_sha,
        snapshot_method=SNAPSHOT_METHOD,
        snapshot_commit=snapshot_commit,
        tree_hash=tree_hash,
        diff_sha256=diff_sha256,
        provenance_complete=True,
        allowed_paths=tuple(contract.allowed_paths),
    )


def assert_tree_unchanged(evidence: SnapshotEvidence, *, root: Path) -> None:
    """Проверить, что дерево совпадает со snapshot, иначе упасть закрыто."""
    tree_hash, _commit, _digest = _build_snapshot_state(
        root, evidence.base_sha, evidence.allowed_paths
    )
    if tree_hash != evidence.tree_hash:
        raise OrchestrationError("TREE_MUTATED_AFTER_SNAPSHOT")


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


@functools.lru_cache(maxsize=1)
def _canonical_trust_source_kinds() -> frozenset[str]:
    """Return the canonical ``trust_source_kind`` enum, fail-closed on any doubt.

    The schema is read from the orchestrator's own trusted checkout, never from
    the task head worktree, so a task branch cannot widen the accepted set.
    """

    schema_path = Path(__file__).resolve().parents[1] / CONTROLLER_VERDICT_SCHEMA
    try:
        schema = load_schema(schema_path)
        node = schema["properties"]["review_basis"]["properties"]["trust_source_kind"]
        values = node["enum"]
    except (OSError, ValueError, KeyError, TypeError, SchemaError) as error:
        raise OrchestrationError("TRUST_SCHEMA_UNAVAILABLE") from error
    if not isinstance(values, list) or not values:
        raise OrchestrationError("TRUST_SCHEMA_UNAVAILABLE")
    if not all(isinstance(item, str) and item for item in values):
        raise OrchestrationError("TRUST_SCHEMA_UNAVAILABLE")
    return frozenset(str(item) for item in values)


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
    valid_basis = (
        isinstance(basis, dict)
        and set(basis) == {"trust_source_kind", *TRUST_BOUNDARY_INVARIANTS}
        and all(
            basis[key] is expected
            for key, expected in TRUST_BOUNDARY_INVARIANTS.items()
        )
        and basis["trust_source_kind"] in _canonical_trust_source_kinds()
    )
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


def _production_gate(root: Path, contract: TaskContract) -> dict[str, Any]:
    """Run the mandatory snapshot-aware profile and preserve its machine code."""
    try:
        return agent_gate.run_repository_full(
            root=root,
            base_sha=contract.base_sha,
            allowed_paths=contract.allowed_paths,
        )
    except agent_gate.GateError as error:
        raise OrchestrationError(error.machine_code) from error


def _assert_gate_snapshot_unchanged(
    root: Path, contract: TaskContract, evidence: dict[str, Any]
) -> None:
    """Bind controller review to the source tree represented by gate evidence."""
    snapshot = evidence.get("snapshot")
    required = {
        "base_sha",
        "snapshot_method",
        "snapshot_commit",
        "tree_hash",
        "diff_sha256",
        "provenance_complete",
    }
    if not isinstance(snapshot, dict) or set(snapshot) != required:
        raise OrchestrationError("GATE_EVIDENCE_INCOMPLETE")
    if snapshot.get("base_sha") != contract.base_sha:
        raise OrchestrationError("GATE_PROFILE_MISMATCH")
    values = (
        snapshot.get("snapshot_method"),
        snapshot.get("snapshot_commit"),
        snapshot.get("tree_hash"),
        snapshot.get("diff_sha256"),
    )
    if not all(isinstance(value, str) and value for value in values):
        raise OrchestrationError("GATE_EVIDENCE_INCOMPLETE")
    if snapshot.get("provenance_complete") is not True:
        raise OrchestrationError("GATE_EVIDENCE_INCOMPLETE")
    provenance = SnapshotEvidence(
        base_sha=contract.base_sha,
        snapshot_method=str(snapshot["snapshot_method"]),
        snapshot_commit=str(snapshot["snapshot_commit"]),
        tree_hash=str(snapshot["tree_hash"]),
        diff_sha256=str(snapshot["diff_sha256"]),
        provenance_complete=True,
        allowed_paths=contract.allowed_paths,
    )
    assert_tree_unchanged(provenance, root=root)


PUBLIC_RECORD_SCHEMA_VERSION = "1.0"
EVIDENCE_REDACTION_VERSION = "3"
PUBLIC_GATE_FIELDS = (
    "profile",
    "profile_version",
    "complete",
    "status",
    "machine_code",
    "expected_checks",
    "executed_checks",
)
PUBLIC_CHECK_FIELDS = ("name", "status", "exit_code")
PUBLIC_SNAPSHOT_FIELDS = (
    "base_sha",
    "snapshot_commit",
    "tree_hash",
    "diff_sha256",
    "snapshot_method",
    "provenance_complete",
    "tree_unchanged",
)
SUMMARY_ALLOWED_CHARS = frozenset(agent_gate.SUMMARY_ALLOWED_CHARS)
FINDING_ALLOWED_CHARS = SUMMARY_ALLOWED_CHARS | frozenset("/")
SUMMARY_MAX_CHARS = int(agent_gate.SUMMARY_MAX_CHARS)
SECRET_SHAPE_RE = re.compile(r"[A-Za-z0-9_\-+/=]{20,}")
IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
CANONICAL_FINDING_SEVERITIES = frozenset({"critical", "high", "medium", "low"})
LEGACY_FINDING_SEVERITIES = frozenset({"blocking", "major", "minor", "info"})
FINDING_SEVERITIES = CANONICAL_FINDING_SEVERITIES | LEGACY_FINDING_SEVERITIES
FINDING_REDACTION_FAILED = "FINDING_REDACTION_FAILED"
SEVERITY_KEYS = ("severity", "level")
CATEGORY_KEYS = ("category", "code", "kind", "type")
SUMMARY_KEYS = ("summary", "message", "description", "title", "detail")
PATH_KEYS = ("path", "file", "filename")
LINE_KEYS = ("line_start", "line", "start_line")
END_LINE_KEYS = ("line_end", "end_line")
CHECK_KEYS = ("check_id", "check", "check_name")
LOCATION_KEYS = ("location",)
EVIDENCE_TEXT_MAX_CHARS = SUMMARY_MAX_CHARS // 2
FINDING_TEXT_FIELDS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("requirement", ("requirement",), SUMMARY_MAX_CHARS),
    ("evidence", ("evidence",), EVIDENCE_TEXT_MAX_CHARS),
    ("required_fix", ("required_fix", "fix"), SUMMARY_MAX_CHARS),
)
FINDING_TEXT_FIELD_NAMES = frozenset(name for name, _, _ in FINDING_TEXT_FIELDS)
DROP_REASONS = frozenset(
    {"missing", "disallowed_chars", "looks_like_secret", "path_like", "unparseable"}
)


def _looks_like_secret(text: str) -> bool:
    """Сообщить, похож ли фрагмент на секрет или ключевой материал."""
    if "PRIVATE KEY" in text:
        return True
    for candidate in SECRET_SHAPE_RE.findall(text):
        has_digit = any(character.isdigit() for character in candidate)
        has_alpha = any(character.isalpha() for character in candidate)
        if has_digit and has_alpha:
            return True
    return False


def _safe_text(value: Any) -> str | None:
    """Нормализовать текст модели или отбросить его целиком, fail-closed."""
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    if not collapsed:
        return None
    filtered = "".join(
        character for character in collapsed if character in SUMMARY_ALLOWED_CHARS
    )
    filtered = " ".join(filtered.split())[:SUMMARY_MAX_CHARS]
    if not filtered:
        return None
    if _looks_like_secret(filtered):
        return None
    if filtered.startswith(("/", "~", "\\")) or "\\" in filtered:
        return None
    if ".." in filtered:
        return None
    return filtered


UNSAFE_PATH_RE = re.compile(r"(?:^|[\s('\"=])(?:/|~/|[A-Za-z]:[\\/]|\\\\)")
SNAPSHOT_COMMIT_RE = re.compile(r"\b(?:snapshot )?commit [0-9a-f]{40}\b")


def _redact_text(value: Any, limit: int) -> tuple[str | None, str | None, bool]:
    """Отредактировать одно текстовое поле finding и назвать причину отказа."""
    if not isinstance(value, str):
        return None, "missing", False
    collapsed = " ".join(value.split())
    if not collapsed:
        return None, "missing", False
    if UNSAFE_PATH_RE.search(collapsed) or "\\" in collapsed or ".." in collapsed:
        return None, "path_like", False
    secret_scan = SNAPSHOT_COMMIT_RE.sub("snapshot commit SHA", collapsed)
    if _looks_like_secret(secret_scan):
        return None, "looks_like_secret", False
    filtered = "".join(
        character for character in collapsed if character in FINDING_ALLOWED_CHARS
    )
    filtered = " ".join(filtered.split())
    if not filtered:
        return None, "disallowed_chars", False
    if len(filtered) > limit:
        boundary = filtered.rfind(" ", 0, limit + 1)
        if boundary < 1:
            return None, "disallowed_chars", False
        return filtered[:boundary], None, True
    return filtered, None, False


def _redact_finding_texts(
    raw: Any,
) -> tuple[dict[str, str | None], list[str], dict[str, str], list[str]]:
    """Отредактировать канонические текстовые поля независимо друг от друга."""
    texts: dict[str, str | None] = {}
    reasons: dict[str, str] = {}
    truncated: list[str] = []
    for name, keys, limit in FINDING_TEXT_FIELDS:
        text, reason, was_truncated = _redact_text(_first_present(raw, keys), limit)
        texts[name] = text
        if reason is not None:
            reasons[name] = reason
        if was_truncated:
            truncated.append(name)
    dropped = sorted(name for name, text in texts.items() if text is None)
    return texts, dropped, {name: reasons[name] for name in dropped}, sorted(truncated)


def _safe_relative_path(value: Any) -> str | None:
    """Вернуть путь относительно корня репозитория либо ничего."""
    if not isinstance(value, str) or not value:
        return None
    candidate = value.strip()
    if not candidate or candidate.startswith(("/", "~", "\\")):
        return None
    if "\\" in candidate or "\x00" in candidate:
        return None
    if len(candidate) >= 2 and candidate[1] == ":":
        return None
    normalized = PurePosixPath(candidate.lstrip("./")).as_posix()
    if not normalized or normalized == "." or ".." in normalized.split("/"):
        return None
    return normalized


def _safe_identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not IDENTIFIER_RE.match(value):
        return None
    return value


def _safe_line(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _first_present(value: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _finding_fingerprint(raw: Any) -> str:
    payload = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _failed_finding(raw: Any) -> dict[str, Any]:
    return {
        "severity": "unknown",
        "category": FINDING_REDACTION_FAILED,
        "location": {"path": None, "line_start": None, "line_end": None},
        "summary": None,
        "requirement": None,
        "evidence": None,
        "required_fix": None,
        "check_id": None,
        "finding_fingerprint": _finding_fingerprint(raw),
        "redaction": {
            "applied": True,
            "version": EVIDENCE_REDACTION_VERSION,
            "summary_dropped": True,
            "dropped_fields": sorted(FINDING_TEXT_FIELD_NAMES),
            "drop_reasons": {
                name: "unparseable" for name in sorted(FINDING_TEXT_FIELD_NAMES)
            },
            "truncated_fields": [],
        },
    }


def redact_finding(raw: Any) -> dict[str, Any]:
    """Свести blocking finding к структурированной redacted-выжимке (#200)."""
    if not isinstance(raw, Mapping):
        return _failed_finding(raw)
    severity = _first_present(raw, SEVERITY_KEYS)
    category = _safe_identifier(_first_present(raw, CATEGORY_KEYS))
    summary = _safe_text(_first_present(raw, SUMMARY_KEYS))
    location = _first_present(raw, LOCATION_KEYS)
    if location is None:
        location = _first_present(raw, PATH_KEYS)
    if isinstance(location, Mapping):
        path = _safe_relative_path(_first_present(location, PATH_KEYS))
        line_start = _safe_line(_first_present(location, LINE_KEYS))
        line_end = _safe_line(_first_present(location, END_LINE_KEYS))
    else:
        path = _safe_relative_path(location)
        line_start = _safe_line(_first_present(raw, LINE_KEYS))
        line_end = _safe_line(_first_present(raw, END_LINE_KEYS))
    texts, dropped, reasons, truncated = _redact_finding_texts(raw)
    return {
        "severity": severity if severity in FINDING_SEVERITIES else "unknown",
        "category": category or "unclassified",
        "location": {
            "path": path,
            "line_start": line_start,
            "line_end": line_end,
        },
        "summary": summary,
        "requirement": texts["requirement"],
        "evidence": texts["evidence"],
        "required_fix": texts["required_fix"],
        "check_id": _safe_identifier(_first_present(raw, CHECK_KEYS)),
        "finding_fingerprint": _finding_fingerprint(raw),
        "redaction": {
            "applied": True,
            "version": EVIDENCE_REDACTION_VERSION,
            "summary_dropped": summary is None
            and len(dropped) == len(FINDING_TEXT_FIELDS),
            "dropped_fields": dropped,
            "drop_reasons": reasons,
            "truncated_fields": truncated,
        },
    }


def _redact_metrics(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    metrics: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not IDENTIFIER_RE.match(key):
            continue
        if isinstance(item, bool) or isinstance(item, int | float) or item is None:
            metrics[key] = item
    return metrics


def _redact_check(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    name = _safe_identifier(_first_present(value, ("name", "id", "check")))
    if name is None:
        return None
    duration = value.get("duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, int | float):
        duration = None
    exit_code = value.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        exit_code = None
    status = value.get("status")
    return {
        "name": name,
        "status": status if isinstance(status, str) else None,
        "exit_code": exit_code,
        "duration_seconds": duration,
        "metrics": _redact_metrics(value.get("metrics")),
    }


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list | tuple):
        return None
    return [item for item in value if isinstance(item, str) and item]


def redact_gate_evidence(gate_result: Any) -> dict[str, Any]:
    """Построить единственную redacted-структуру gate evidence прогона."""
    if not isinstance(gate_result, Mapping):
        return {
            "profile": None,
            "profile_version": None,
            "complete": None,
            "status": None,
            "machine_code": None,
            "expected_checks": None,
            "executed_checks": None,
            "checks": None,
        }
    raw_checks = gate_result.get("checks")
    checks: list[dict[str, Any]] | None = None
    if isinstance(raw_checks, Mapping):
        raw_checks = list(raw_checks.values())
    if isinstance(raw_checks, list):
        checks = [
            check for check in map(_redact_check, raw_checks) if check is not None
        ]
    complete = gate_result.get("complete")
    profile = gate_result.get("profile")
    version = gate_result.get("profile_version")
    status = gate_result.get("status")
    machine_code = gate_result.get("machine_code")
    return {
        "profile": profile if isinstance(profile, str) else None,
        "profile_version": version if isinstance(version, str) else None,
        "complete": complete if isinstance(complete, bool) else None,
        "status": status if isinstance(status, str) else None,
        "machine_code": machine_code if isinstance(machine_code, str) else None,
        "expected_checks": _string_list(gate_result.get("expected_checks")),
        "executed_checks": _string_list(gate_result.get("executed_checks")),
        "checks": checks,
    }


def redact_snapshot_identity(
    gate_result: Any, *, tree_unchanged: bool | None
) -> dict[str, Any]:
    """Взять идентичность snapshot из gate evidence, не пересчитывая её."""
    snapshot = gate_result.get("snapshot") if isinstance(gate_result, Mapping) else None
    values: dict[str, Any] = {field_name: None for field_name in PUBLIC_SNAPSHOT_FIELDS}
    values["tree_unchanged"] = tree_unchanged
    if not isinstance(snapshot, Mapping):
        return values
    for key in ("base_sha", "snapshot_commit", "tree_hash", "diff_sha256"):
        item = snapshot.get(key)
        values[key] = item if isinstance(item, str) and item else None
    method = snapshot.get("snapshot_method")
    values["snapshot_method"] = method if isinstance(method, str) else None
    complete = snapshot.get("provenance_complete")
    values["provenance_complete"] = complete if isinstance(complete, bool) else None
    return values


def redact_verdict(payload: Any) -> dict[str, Any]:
    """Сохранить суждение контроллера без сырого текста модели."""
    if not isinstance(payload, Mapping):
        return {
            "verdict": None,
            "review_basis": None,
            "escalation_reason": None,
            "blocking_findings": None,
        }
    basis = payload.get("review_basis")
    safe_basis: dict[str, Any] | None = None
    if isinstance(basis, Mapping):
        safe_basis = {
            key: item
            for key, item in basis.items()
            if isinstance(key, str) and isinstance(item, bool | str)
        }
    raw_findings = payload.get("blocking_findings")
    findings: list[dict[str, Any]] | None = None
    if isinstance(raw_findings, list):
        findings = [redact_finding(item) for item in raw_findings]
    verdict = payload.get("verdict")
    return {
        "verdict": verdict if isinstance(verdict, str) else None,
        "review_basis": safe_basis,
        "escalation_reason": _safe_text(payload.get("escalation_reason")),
        "blocking_findings": findings,
    }


def _new_evidence_state() -> dict[str, Any]:
    return {
        "gate": None,
        "snapshot": None,
        "verdict": None,
        "tree_unchanged": None,
        "total_seconds": None,
        "executor_seconds": None,
        "controller_seconds": None,
        "gate_seconds": None,
    }


def _record_gate_evidence(state: dict[str, Any], gate_result: Any) -> None:
    state["gate"] = redact_gate_evidence(gate_result)
    state["snapshot"] = redact_snapshot_identity(
        gate_result, tree_unchanged=state.get("tree_unchanged")
    )


def _iteration_number(payload: Any, history: list[dict[str, Any]]) -> int:
    """Взять номер итерации из вердикта либо из позиции в истории."""
    if isinstance(payload, Mapping):
        candidate = payload.get("repair_iteration")
        if (
            isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and candidate >= 0
        ):
            return candidate
    return len(history)


def _record_verdict(state: dict[str, Any], payload: Any) -> None:
    """Накопить историю вердиктов, не затирая предыдущие итерации (#204)."""
    redacted = redact_verdict(payload)
    history = state.get("verdict_history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "iteration": _iteration_number(payload, history),
            "verdict": redacted["verdict"],
            "blocking_findings": redacted["blocking_findings"],
        }
    )
    state["verdict_history"] = history
    state["verdict"] = {**redacted, "iteration_history": history}


def _durations(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "total_seconds": state.get("total_seconds"),
        "executor_seconds": state.get("executor_seconds"),
        "controller_seconds": state.get("controller_seconds"),
        "gate_seconds": state.get("gate_seconds"),
    }


def private_evidence(state: Mapping[str, Any]) -> dict[str, Any]:
    """Полный redacted пакет для приватной retrospective."""
    return {
        "gate": state.get("gate"),
        "snapshot": state.get("snapshot"),
        "verdict": state.get("verdict"),
        "durations": _durations(state),
    }


def public_evidence(state: Mapping[str, Any]) -> dict[str, Any]:
    """Явный whitelist детерминированных фактов для записи прогона."""
    gate = state.get("gate")
    snapshot = state.get("snapshot")
    public_gate: dict[str, Any] | None = None
    if isinstance(gate, Mapping):
        public_gate = {key: gate.get(key) for key in PUBLIC_GATE_FIELDS}
        checks = gate.get("checks")
        if isinstance(checks, list):
            public_gate["checks"] = [
                {key: check.get(key) for key in PUBLIC_CHECK_FIELDS}
                for check in checks
                if isinstance(check, Mapping)
            ]
        else:
            public_gate["checks"] = None
    public_snapshot: dict[str, Any] | None = None
    if isinstance(snapshot, Mapping):
        public_snapshot = {key: snapshot.get(key) for key in PUBLIC_SNAPSHOT_FIELDS}
    return {
        "schema_version": PUBLIC_RECORD_SCHEMA_VERSION,
        "gate": public_gate,
        "snapshot": public_snapshot,
        "duration_seconds": state.get("total_seconds"),
    }


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
    started = float(previous.get("started", time.time())) if previous else time.time()
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

    evidence_state = _new_evidence_state()

    def finish(status: str, machine_code: str) -> RunResult:
        result = RunResult(status, machine_code, repairs, run_id)
        final_verdicts = list(verdicts)
        if status in {"PASS", "PASS_WITH_NOTES", "FAIL_ESCALATE"} and (
            not final_verdicts or final_verdicts[-1] != status
        ):
            final_verdicts.append(status)
        final_failures = list(failures)
        evidence_state["total_seconds"] = round(max(0.0, time.time() - started), 3)
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
                    duration_seconds=max(0, int(time.time() - started)),
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
                    evidence=private_evidence(evidence_state),
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
                "evidence": public_evidence(evidence_state),
                "record_schema_version": PUBLIC_RECORD_SCHEMA_VERSION,
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
        production_gate = gate is None
        gate_runner = gate or (lambda value: _production_gate(root, value))
        while True:
            if time.time() - started > contract.max_minutes * 60:
                return finish("FAIL_ESCALATE", "TIME_BUDGET_EXHAUSTED")
            executor_session = uuid.uuid4().hex
            executor_calls += 1
            executor_started = time.perf_counter()
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
            evidence_state["executor_seconds"] = round(
                max(0.0, time.perf_counter() - executor_started), 3
            )
            if _head_sha(root, contract) != head_sha:
                return finish("FAIL_ESCALATE", "UNAUTHORIZED_HEAD_CHANGE")
            _assert_private_artifacts_safe(root)
            paths = _changed_files(root, contract)
            _assert_scope(paths, contract)
            gate_started = time.perf_counter()
            gate_result = gate_runner(contract)
            evidence_state["gate_seconds"] = round(
                max(0.0, time.perf_counter() - gate_started), 3
            )
            _record_gate_evidence(evidence_state, gate_result)
            gate_passed = (
                agent_gate.gate_evidence_is_passing(gate_result)
                if production_gate
                else gate_result.get("status") == "passed"
            )
            if not gate_passed:
                gate_code = (
                    str(gate_result.get("machine_code", "GATE_FAILED"))
                    if production_gate
                    else "GATE_FAILED"
                )
                failures.append(gate_code)
                if repairs >= contract.max_repair_iterations:
                    return finish("FAIL_ESCALATE", gate_code)
                repairs += 1
                storage.save(
                    run_key,
                    {
                        "run_id": run_id,
                        "started": started,
                        "repair_iterations": repairs,
                        "terminal": False,
                        "status": "running",
                        "machine_code": gate_code,
                        "executor_calls": executor_calls,
                        "controller_calls": controller_calls,
                        "findings": findings,
                        "verdicts": verdicts,
                        "failures": failures,
                    },
                )
                continue
            if production_gate:
                _assert_gate_snapshot_unchanged(root, contract, gate_result)
                evidence_state["tree_unchanged"] = True
                _record_gate_evidence(evidence_state, gate_result)
            diff = _diff(root, contract, contract.max_report_chars)
            controller_session = uuid.uuid4().hex
            controller_calls += 1
            controller_started = time.perf_counter()
            verdict_payload = adapter.review(
                {
                    "issue": contract.issue,
                    "acceptance_criteria": list(contract.acceptance_criteria),
                    "contract": {
                        "issue": contract.issue,
                        "epic": contract.epic,
                        "acceptance_criteria": list(contract.acceptance_criteria),
                        "base_ref": contract.base_ref,
                        "base_sha": contract.base_sha,
                        "head_ref": contract.head_ref,
                        "allowed_paths": list(contract.allowed_paths),
                        "permissions": asdict(contract.permissions),
                        "max_repair_iterations": contract.max_repair_iterations,
                        "remaining_repair_iterations": contract.max_repair_iterations
                        - repairs,
                        "max_minutes": contract.max_minutes,
                        "task_class": contract.task_class,
                    },
                    "diff": diff,
                    "gate_evidence": gate_result,
                    "reviewed_state": {
                        "snapshot_commit": gate_result.get("snapshot", {}).get(
                            "snapshot_commit"
                        )
                    },
                    "base_sha": contract.base_sha,
                    "head_sha": head_sha,
                    "repair_iteration": repairs,
                },
                trusted_policy,
                controller_session,
            )
            verdict = _validate_verdict(verdict_payload, contract, head_sha)
            evidence_state["controller_seconds"] = round(
                max(0.0, time.perf_counter() - controller_started), 3
            )
            _record_verdict(evidence_state, verdict_payload)
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


ADAPTER_MACHINE_CODES: frozenset[str] = frozenset(
    {
        "INVALID_REQUEST",
        "VERSION_MISMATCH",
        "VERSION_PROBE_FAILED",
        "CODEX_NOT_FOUND",
        "MODEL_UNAVAILABLE",
        "ADAPTER_TIMEOUT",
        "OUTPUT_LIMIT",
        "MALFORMED_OUTPUT",
        "SCHEMA_VIOLATION",
        "SCHEMA_UNSUPPORTED",
        "SCHEMA_DERIVE_UNSUPPORTED",
        "SCHEMA_ERROR",
    }
)
DETAIL_ALLOWED_CHARS: frozenset[str] = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_=,.- "
)
DETAIL_MAX_CHARS = 200


def _report_adapter_diagnostic(exit_code: int, stderr: str) -> None:
    """Re-emit the role adapter machine code on the orchestrator stderr.

    The adapter already prints a redacted machine code and an optional
    `detail=` line, but the orchestrator captures both streams, so the signal
    used to be lost. Only a known machine code and a character-filtered detail
    line are re-emitted; raw adapter output is never echoed (#186).
    """
    candidates = [line.strip() for line in stderr.splitlines() if line.strip()]
    code = next(
        (line for line in candidates if line in ADAPTER_MACHINE_CODES), "UNKNOWN"
    )
    detail = ""
    for line in candidates:
        if not line.startswith("detail="):
            continue
        body = line[len("detail=") :][:DETAIL_MAX_CHARS]
        if body and all(character in DETAIL_ALLOWED_CHARS for character in body):
            detail = body
        break
    message = f"adapter_diagnostic exit_code={exit_code} machine_code={code}"
    if detail:
        message = f"{message} detail={detail}"
    print(message, file=sys.stderr)


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
            _report_adapter_diagnostic(completed.returncode, completed.stderr)
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
