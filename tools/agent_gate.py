#!/usr/bin/env python3
"""Snapshot-aware repository quality gate and versioned gate_evidence (#197).

The orchestrator used to call ``tools/agent_verify.py`` in its ``pre-commit``
phase, where the four mandatory project commands are not executed at all, so
``gate_evidence`` could not prove that the repository quality profile had run.

This module implements the decision recorded in
``docs/ADR-197-complete-gate-evidence.md``:

* ``trusted_snapshot_session`` keeps a disposable bare mirror and a snapshot
  checkout alive for the whole gate, so the very tree that was hashed is the
  tree the commands run on, and allowed files that were untracked in the
  source worktree are known to Git inside the checkout;
* ``verify_environment`` fails closed when the interpreter would import the
  package from outside the snapshot checkout, which is what an editable
  install silently does;
* ``run_profile`` executes a versioned profile with explicit argv, a per-check
  timeout and a closed set of statuses;
* ``build_evidence`` produces the redacted, versioned evidence object and
  refuses to call an incomplete profile a success.

Raw stdout and stderr never leave this module: summaries are rebuilt from
parsed metrics and filtered through a fixed vocabulary, so no absolute path,
home directory or private storage path can reach the controller prompt.

Protected and policy path enforcement stays with the orchestrator and runs
before a session is opened; the session itself only enforces the effective
allowlist and refuses an empty one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

EVIDENCE_SCHEMA_VERSION: Final[str] = "1"
PROFILE_VERSION: Final[str] = "1"
FULL_PROFILE: Final[str] = "repository-full"
DIFF_ONLY_PROFILE: Final[str] = "repository-diff-only"
CWD_LABEL: Final[str] = "<snapshot-checkout>"
SNAPSHOT_METHOD: Final[str] = "commit-tree"
SNAPSHOT_MESSAGE: Final[str] = "privacy-gateway trusted snapshot"
SNAPSHOT_BRANCH: Final[str] = "pgw-snapshot"
SNAPSHOT_REF: Final[str] = "refs/heads/pgw-snapshot"
DEFAULT_CHECK_TIMEOUT_SECONDS: Final[float] = 1800.0
PROBE_TIMEOUT_SECONDS: Final[float] = 120.0
MAX_OUTPUT_CHARS: Final[int] = 200_000
SUMMARY_MAX_CHARS: Final[int] = 200

SNAPSHOT_IDENTITY: Final[Mapping[str, str]] = {
    "GIT_AUTHOR_NAME": "privacy-gateway orchestrator",
    "GIT_AUTHOR_EMAIL": "orchestrator@privacy-gateway.invalid",
    "GIT_AUTHOR_DATE": "1970-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "privacy-gateway orchestrator",
    "GIT_COMMITTER_EMAIL": "orchestrator@privacy-gateway.invalid",
    "GIT_COMMITTER_DATE": "1970-01-01T00:00:00+00:00",
}

CHECK_COMMANDS: Final[Mapping[str, tuple[str, ...]]] = {
    "pytest": ("pytest", "-q"),
    "ruff": ("ruff", "check", "."),
    "mypy": ("mypy", "."),
    "pre-commit": ("pre-commit", "run", "--all-files"),
    "diff": ("git", "diff", "--check"),
}

PROFILES: Final[Mapping[str, tuple[str, ...]]] = {
    FULL_PROFILE: ("pytest", "ruff", "mypy", "pre-commit"),
    DIFF_ONLY_PROFILE: ("diff",),
}

CHECK_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "passed",
        "failed",
        "timeout",
        "unavailable",
        "interrupted",
        "not_run",
        "parse_error",
    }
)

PROFILE_STATUSES: Final[frozenset[str]] = frozenset({"passed", "failed", "incomplete"})

SUMMARY_ALLOWED_CHARS: Final[frozenset[str]] = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 _=,.:+-"
)

PYTEST_METRICS: Final[tuple[str, ...]] = (
    "passed",
    "failed",
    "skipped",
    "xfailed",
    "xpassed",
)

PYTEST_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(\d+) (passed|failed|skipped|xfailed|xpassed)"
)
RUFF_PATTERN: Final[re.Pattern[str]] = re.compile(r"Found (\d+) error")
MYPY_PATTERN: Final[re.Pattern[str]] = re.compile(r"in (\d+) source files?")
HOOK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<id>\S.*?)\.{3,}.*?(?P<outcome>Passed|Failed|Skipped)$"
)

PROBE_CODE: Final[str] = (
    "import importlib, json, sys;"
    "module = importlib.import_module(sys.argv[1]);"
    'print(json.dumps({"file": getattr(module, "__file__", "")}))'
)


class GateError(Exception):
    """Controlled gate failure carrying a machine code that is safe to report."""

    def __init__(self, machine_code: str) -> None:
        super().__init__(machine_code)
        self.machine_code = machine_code


@dataclass(frozen=True)
class SnapshotSession:
    """One trusted snapshot materialised as a disposable checkout."""

    base_sha: str
    snapshot_method: str
    snapshot_commit: str
    tree_hash: str
    diff_sha256: str
    provenance_complete: bool
    allowed_paths: tuple[str, ...]
    checkout: Path

    def identity(self) -> dict[str, Any]:
        """Return the redacted snapshot identity used inside gate evidence."""
        return {
            "base_sha": self.base_sha,
            "snapshot_method": self.snapshot_method,
            "snapshot_commit": self.snapshot_commit,
            "tree_hash": self.tree_hash,
            "diff_sha256": self.diff_sha256,
            "provenance_complete": self.provenance_complete,
        }


@dataclass(frozen=True)
class CommandOutcome:
    """Terminal state of one gate command, including its captured output."""

    status: str
    exit_code: int | None
    output: str


@dataclass(frozen=True)
class CheckResult:
    """Structured result of one mandatory check."""

    id: str
    argv: tuple[str, ...]
    status: str
    exit_code: int | None
    duration_seconds: float
    summary: str
    metrics: Mapping[str, Any]


Runner = Callable[[Sequence[str], Path, float], CommandOutcome]


def _environment(**overrides: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        environment.pop(name, None)
    environment.update(SNAPSHOT_IDENTITY)
    environment.update(overrides)
    return environment


def _git(
    arguments: Sequence[str], *, cwd: Path, environment: Mapping[str, str]
) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            capture_output=True,
            check=False,
            env=dict(environment),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GateError("SNAPSHOT_SESSION_FAILED") from error
    if completed.returncode != 0:
        raise GateError("SNAPSHOT_SESSION_FAILED")
    return completed.stdout


def _text(value: bytes) -> str:
    return value.decode("utf-8", "strict").strip()


def _remove_workspace(workspace: Path) -> None:
    """Remove the disposable workspace, including read-only Git objects.

    Git marks object files read-only, and on Windows a read-only file cannot
    be unlinked, so a single suppressed ``rmtree`` can silently leave the
    snapshot workspace behind. Write permission is restored before the second
    attempt.
    """
    shutil.rmtree(workspace, ignore_errors=True)
    if not workspace.exists():
        return
    for path in sorted(workspace.rglob("*"), reverse=True):
        try:
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            continue
    try:
        workspace.chmod(stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass
    shutil.rmtree(workspace, ignore_errors=True)


def _changed_paths(root: Path, base_sha: str) -> list[str]:
    environment = _environment()
    sources = (
        ("diff", "--name-only", "-z", f"{base_sha}...HEAD"),
        ("diff", "--name-only", "-z"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    found: set[str] = set()
    for source in sources:
        output = _git(source, cwd=root, environment=environment)
        decoded = output.decode("utf-8", "strict")
        found.update(item for item in decoded.split("\0") if item)
    return sorted(found)


def _assert_allowlist(paths: Sequence[str], allowed: Sequence[str]) -> None:
    scope = tuple(PurePosixPath(item).as_posix().rstrip("/") for item in allowed)
    if not scope:
        raise GateError("SNAPSHOT_SCOPE_VIOLATION")
    for path in paths:
        if not any(path == item or path.startswith(f"{item}/") for item in scope):
            raise GateError("SNAPSHOT_SCOPE_VIOLATION")


@contextmanager
def trusted_snapshot_session(
    *, root: Path, base_sha: str, allowed_paths: Sequence[str]
) -> Iterator[SnapshotSession]:
    """Materialise a trusted snapshot checkout for the duration of the gate.

    Refs of the source repository and of any remote are never moved: the only
    ref created points into the disposable mirror, which exists solely so the
    snapshot commit can be checked out.
    """
    anchor = Path(os.path.realpath(root))
    paths = _changed_paths(anchor, base_sha)
    _assert_allowlist(paths, allowed_paths)
    workspace = Path(tempfile.mkdtemp(prefix="pgw-gate-"))
    try:
        mirror = workspace / "mirror.git"
        index = workspace / "snapshot-index"
        checkout = workspace / "snapshot"
        _git(
            ("clone", "--quiet", "--bare", "--no-hardlinks", str(anchor), str(mirror)),
            cwd=workspace,
            environment=_environment(),
        )
        staging = _environment(
            GIT_DIR=str(mirror),
            GIT_WORK_TREE=str(anchor),
            GIT_INDEX_FILE=str(index),
        )
        _git(("read-tree", base_sha), cwd=anchor, environment=staging)
        if paths:
            _git(("add", "--all", "--", *paths), cwd=anchor, environment=staging)
        tree_hash = _text(_git(("write-tree",), cwd=anchor, environment=staging))
        snapshot_commit = _text(
            _git(
                ("commit-tree", tree_hash, "-p", base_sha, "-m", SNAPSHOT_MESSAGE),
                cwd=anchor,
                environment=staging,
            )
        )
        diff = _git(
            ("diff", "--binary", "--no-ext-diff", base_sha, tree_hash),
            cwd=anchor,
            environment=staging,
        )
        _git(
            ("update-ref", SNAPSHOT_REF, snapshot_commit),
            cwd=workspace,
            environment=_environment(GIT_DIR=str(mirror)),
        )
        _git(
            (
                "clone",
                "--quiet",
                "--no-hardlinks",
                "--single-branch",
                "--branch",
                SNAPSHOT_BRANCH,
                str(mirror),
                str(checkout),
            ),
            cwd=workspace,
            environment=_environment(),
        )
        yield SnapshotSession(
            base_sha=base_sha,
            snapshot_method=SNAPSHOT_METHOD,
            snapshot_commit=snapshot_commit,
            tree_hash=tree_hash,
            diff_sha256=hashlib.sha256(diff).hexdigest(),
            provenance_complete=True,
            allowed_paths=tuple(allowed_paths),
            checkout=checkout,
        )
    finally:
        _remove_workspace(workspace)


def verify_environment(
    session: SnapshotSession,
    *,
    package: str = "privacy_gateway",
    interpreter: str | None = None,
) -> str:
    """Fail closed unless the interpreter imports the package from the snapshot.

    An editable install resolves the package through an import hook that wins
    over ``PYTHONPATH``, so a gate started in the snapshot checkout can still
    execute the source worktree. That false-green run is forbidden, therefore
    the resolved module path is checked instead of assumed.
    """
    checkout = session.checkout.resolve()
    source = checkout / "src"
    entry = str(source if source.is_dir() else checkout)
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        os.pathsep.join((entry, existing)) if existing else entry
    )
    try:
        completed = subprocess.run(
            [interpreter or sys.executable, "-c", PROBE_CODE, package],
            cwd=checkout,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GateError("GATE_ENVIRONMENT_UNVERIFIED") from error
    if completed.returncode != 0:
        raise GateError("GATE_ENVIRONMENT_UNVERIFIED")
    try:
        payload = json.loads(completed.stdout)
    except ValueError as error:
        raise GateError("GATE_ENVIRONMENT_UNVERIFIED") from error
    if not isinstance(payload, dict):
        raise GateError("GATE_ENVIRONMENT_UNVERIFIED")
    location = payload.get("file")
    if not isinstance(location, str) or not location:
        raise GateError("GATE_ENVIRONMENT_UNVERIFIED")
    resolved = Path(location).resolve()
    try:
        resolved.relative_to(checkout)
    except ValueError as error:
        raise GateError("GATE_ENVIRONMENT_UNVERIFIED") from error
    return resolved.name


def _default_runner(
    argv: Sequence[str], cwd: Path, timeout_seconds: float
) -> CommandOutcome:
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return CommandOutcome("timeout", None, "")
    except KeyboardInterrupt:
        return CommandOutcome("interrupted", None, "")
    except (OSError, subprocess.SubprocessError):
        return CommandOutcome("unavailable", None, "")
    output = ((completed.stdout or "") + (completed.stderr or ""))[:MAX_OUTPUT_CHARS]
    if completed.returncode < 0:
        return CommandOutcome("interrupted", completed.returncode, output)
    status = "passed" if completed.returncode == 0 else "failed"
    return CommandOutcome(status, completed.returncode, output)


def _parse_pytest(output: str) -> tuple[dict[str, Any], bool]:
    metrics: dict[str, Any] = {name: None for name in PYTEST_METRICS}
    found = False
    for count, name in PYTEST_PATTERN.findall(output):
        metrics[name] = int(count)
        found = True
    return metrics, found


def _parse_ruff(output: str, exit_code: int | None) -> tuple[dict[str, Any], bool]:
    metrics: dict[str, Any] = {"errors": None}
    if exit_code == 0:
        if "All checks passed" in output:
            metrics["errors"] = 0
            return metrics, True
        return metrics, False
    match = RUFF_PATTERN.search(output)
    if match:
        metrics["errors"] = int(match.group(1))
    return metrics, True


def _parse_mypy(output: str) -> tuple[dict[str, Any], bool]:
    metrics: dict[str, Any] = {"source_files": None}
    match = MYPY_PATTERN.search(output)
    if match:
        metrics["source_files"] = int(match.group(1))
    return metrics, True


def _parse_precommit(
    output: str, exit_code: int | None
) -> tuple[dict[str, Any], bool]:
    hooks: list[dict[str, str]] = []
    for line in output.splitlines():
        match = HOOK_PATTERN.match(line.strip())
        if match:
            hooks.append(
                {
                    "id": _sanitize(match.group("id")),
                    "outcome": match.group("outcome").lower(),
                }
            )
    metrics: dict[str, Any] = {"hooks": hooks}
    if exit_code == 0 and not hooks:
        return metrics, False
    return metrics, True


def _parse(check_id: str, outcome: CommandOutcome) -> tuple[dict[str, Any], bool]:
    if check_id == "pytest":
        return _parse_pytest(outcome.output)
    if check_id == "ruff":
        return _parse_ruff(outcome.output, outcome.exit_code)
    if check_id == "mypy":
        return _parse_mypy(outcome.output)
    if check_id == "pre-commit":
        return _parse_precommit(outcome.output, outcome.exit_code)
    return {}, True


def _sanitize(value: str) -> str:
    return "".join(
        character if character in SUMMARY_ALLOWED_CHARS else "_" for character in value
    )


def _summary(check_id: str, status: str, metrics: Mapping[str, Any]) -> str:
    parts = [check_id, status]
    for key, value in metrics.items():
        if key == "hooks":
            hooks = [item for item in value if isinstance(item, dict)]
            parts.append(f"hooks={len(hooks)}")
            parts.extend(f"{item['id']}={item['outcome']}" for item in hooks)
            continue
        parts.append(f"{key}=" + ("unknown" if value is None else str(value)))
    return _sanitize(" ".join(parts))[:SUMMARY_MAX_CHARS]


def run_profile(
    session: SnapshotSession,
    *,
    profile: str = FULL_PROFILE,
    checks: Sequence[str] | None = None,
    commands: Mapping[str, Sequence[str]] | None = None,
    timeout_seconds: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
    runner: Runner | None = None,
) -> tuple[CheckResult, ...]:
    """Execute a versioned profile inside the snapshot checkout."""
    expected = PROFILES.get(profile)
    if expected is None:
        raise GateError("GATE_PROFILE_MISMATCH")
    selected = tuple(checks) if checks is not None else expected
    if any(item not in expected for item in selected):
        raise GateError("GATE_PROFILE_MISMATCH")
    table: Mapping[str, Sequence[str]] = commands or CHECK_COMMANDS
    execute = runner or _default_runner
    results: list[CheckResult] = []
    for check_id in selected:
        argv = table.get(check_id)
        if argv is None:
            raise GateError("GATE_PROFILE_MISMATCH")
        started = time.monotonic()
        outcome = execute(tuple(argv), session.checkout, timeout_seconds)
        duration = round(max(0.0, time.monotonic() - started), 3)
        if outcome.status not in CHECK_STATUSES:
            raise GateError("GATE_PROFILE_MISMATCH")
        metrics, parsed = _parse(check_id, outcome)
        status = outcome.status
        if status == "passed" and not parsed:
            status = "parse_error"
        results.append(
            CheckResult(
                id=check_id,
                argv=tuple(_sanitize(item) for item in argv),
                status=status,
                exit_code=outcome.exit_code,
                duration_seconds=duration,
                summary=_summary(check_id, status, metrics),
                metrics=metrics,
            )
        )
    return tuple(results)


def build_evidence(
    session: SnapshotSession,
    results: Sequence[CheckResult],
    *,
    profile: str = FULL_PROFILE,
) -> dict[str, Any]:
    """Assemble redacted versioned evidence and refuse to hide incompleteness."""
    expected = PROFILES.get(profile)
    if expected is None:
        raise GateError("GATE_PROFILE_MISMATCH")
    executed = tuple(result.id for result in results)
    if len(set(executed)) != len(executed):
        raise GateError("GATE_PROFILE_MISMATCH")
    if any(item not in expected for item in executed):
        raise GateError("GATE_PROFILE_MISMATCH")
    statuses = {result.id: result.status for result in results}
    complete = all(item in statuses for item in expected)
    passed = complete and all(statuses[item] == "passed" for item in expected)
    if not complete:
        status, machine_code = "incomplete", "GATE_EVIDENCE_INCOMPLETE"
    elif passed:
        status, machine_code = "passed", "OK"
    else:
        status, machine_code = "failed", "GATE_FAILED"
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "profile": profile,
        "profile_version": PROFILE_VERSION,
        "status": status,
        "complete": complete,
        "machine_code": machine_code,
        "expected_checks": list(expected),
        "executed_checks": list(executed),
        "snapshot": session.identity(),
        "checks": [
            {
                "id": result.id,
                "argv": list(result.argv),
                "cwd": CWD_LABEL,
                "status": result.status,
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
                "summary": result.summary,
                "metrics": dict(result.metrics),
            }
            for result in results
        ],
    }


def gate_evidence_is_passing(evidence: Mapping[str, Any]) -> bool:
    """Report whether evidence proves a complete and fully passing profile."""
    return bool(evidence.get("complete")) and evidence.get("status") == "passed"


def is_repository_full(evidence: Mapping[str, Any]) -> bool:
    """Report whether evidence describes the mandatory full profile."""
    return (
        evidence.get("profile") == FULL_PROFILE
        and evidence.get("profile_version") == PROFILE_VERSION
        and list(evidence.get("expected_checks") or ()) == list(PROFILES[FULL_PROFILE])
    )


def run_repository_full(
    *,
    root: Path,
    base_sha: str,
    allowed_paths: Sequence[str],
    package: str = "privacy_gateway",
    timeout_seconds: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run the mandatory profile on one trusted snapshot and return evidence."""
    with trusted_snapshot_session(
        root=root, base_sha=base_sha, allowed_paths=allowed_paths
    ) as session:
        verify_environment(session, package=package)
        results = run_profile(session, timeout_seconds=timeout_seconds)
        return build_evidence(session, results)
