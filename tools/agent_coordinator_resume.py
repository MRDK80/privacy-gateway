#!/usr/bin/env python3
"""Persist and revalidate a minimal private coordinator checkpoint."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import agent_memory  # noqa: E402

SCHEMA_VERSION = "1.0"
SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
ALLOWED_ACTIONS = frozenset({"branch_created", "handover_started"})
AUTHORITY_KEYS = {
    "schema_version",
    "repository",
    "epic",
    "task",
    "base_ref",
    "base_sha",
    "head_ref",
    "head_sha",
    "handover_digest",
    "policy",
    "approval",
    "allowed_paths",
    "budgets",
}
CHECKPOINT_KEYS = {
    "schema_version",
    "repository",
    "epic",
    "task",
    "base_ref",
    "base_sha",
    "head_ref",
    "head_sha",
    "handover_digest",
    "policy",
    "approval",
    "allowed_paths",
    "budgets",
    "repair_iterations",
    "completed_actions",
    "pending_action",
    "status",
}


class ResumeError(Exception):
    """Fail-closed coordinator resume error safe to expose as a code."""

    def __init__(self, machine_code: str) -> None:
        super().__init__(machine_code)
        self.machine_code = machine_code


@dataclass(frozen=True)
class ResumeResult:
    status: str
    machine_code: str
    action: str


def default_directory() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured) if configured else Path.home() / ".local" / "state"
    return base / "privacy-gateway" / "agent-coordinator"


def _exact(value: Any, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ResumeError("CHECKPOINT_INVALID")
    return cast(Mapping[str, Any], value)


def validate_checkpoint(value: Any) -> dict[str, Any]:
    """Validate the closed, authority-bearing checkpoint schema."""
    item = _exact(value, CHECKPOINT_KEYS)
    policy = _exact(item["policy"], {"source", "base_sha"})
    approval = _exact(item["approval"], {"plan_digest"})
    budgets = _exact(
        item["budgets"],
        {"max_minutes", "max_repair_iterations", "max_report_chars"},
    )
    paths = item["allowed_paths"]
    actions = item["completed_actions"]
    pending = item["pending_action"]
    repairs = item["repair_iterations"]
    if (
        item["schema_version"] != SCHEMA_VERSION
        or not isinstance(item["repository"], str)
        or item["repository"].count("/") != 1
        or not isinstance(item["epic"], int)
        or isinstance(item["epic"], bool)
        or not isinstance(item["task"], int)
        or isinstance(item["task"], bool)
        or item["epic"] < 1
        or item["task"] < 1
        or item["epic"] == item["task"]
        or not isinstance(item["base_ref"], str)
        or not item["base_ref"].startswith(f"roadmap/{item['epic']}-")
        or not isinstance(item["head_ref"], str)
        or item["head_ref"].startswith("roadmap/")
        or not isinstance(item["base_sha"], str)
        or SHA_RE.fullmatch(item["base_sha"]) is None
        or not isinstance(item["head_sha"], str)
        or SHA_RE.fullmatch(item["head_sha"]) is None
        or not isinstance(item["handover_digest"], str)
        or DIGEST_RE.fullmatch(item["handover_digest"]) is None
        or policy != {"source": "base_sha", "base_sha": item["base_sha"]}
        or approval != {"plan_digest": item["handover_digest"]}
        or not isinstance(paths, list)
        or not paths
        or not all(
            isinstance(path, str) and path and path == path.strip() for path in paths
        )
        or len(set(paths)) != len(paths)
        or not isinstance(actions, list)
        or len(set(actions)) != len(actions)
        or not set(actions).issubset(ALLOWED_ACTIONS)
        or (pending is not None and pending not in ALLOWED_ACTIONS)
        or pending in actions
        or not isinstance(repairs, int)
        or isinstance(repairs, bool)
        or repairs < 0
        or not isinstance(item["status"], str)
        or item["status"] not in {"READY", "RUNNING", "BLOCKED", "ESCALATE", "TERMINAL"}
    ):
        raise ResumeError("CHECKPOINT_INVALID")
    max_repairs = budgets.get("max_repair_iterations")
    if (
        not isinstance(budgets.get("max_minutes"), int)
        or isinstance(budgets.get("max_minutes"), bool)
        or not 1 <= budgets["max_minutes"] <= 240
        or not isinstance(max_repairs, int)
        or isinstance(max_repairs, bool)
        or not 0 <= max_repairs <= 2
        or not isinstance(budgets.get("max_report_chars"), int)
        or isinstance(budgets.get("max_report_chars"), bool)
        or not 1 <= budgets["max_report_chars"] <= 200_000
        or repairs > max_repairs
    ):
        raise ResumeError("REPAIR_LIMIT_EXHAUSTED")
    return dict(item)


class CheckpointStore:
    """Atomic private storage deliberately forbidden inside the repository."""

    def __init__(self, directory: Path, repository_root: Path) -> None:
        self.directory = directory.resolve()
        root = repository_root.resolve()
        try:
            self.directory.relative_to(root)
        except ValueError:
            pass
        else:
            raise ResumeError("UNSAFE_STORAGE")
        self.path = self.directory / "checkpoint.json"

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ResumeError("CHECKPOINT_READ_FAILED") from error
        return validate_checkpoint(value)

    def save(self, value: Mapping[str, Any]) -> None:
        checked = validate_checkpoint(value)
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.directory, 0o700)
            descriptor, temporary = tempfile.mkstemp(
                dir=self.directory, prefix=".checkpoint-", suffix=".tmp"
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(checked, stream, sort_keys=True, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except OSError as error:
            raise ResumeError("CHECKPOINT_WRITE_FAILED") from error


def resume(
    expected: Mapping[str, Any],
    *,
    live: Mapping[str, Any],
    store: CheckpointStore,
    action: str,
    effect: Callable[[], None],
) -> ResumeResult:
    """Revalidate exact identity and execute a local action at most once."""
    if action not in ALLOWED_ACTIONS:
        return ResumeResult("BLOCKED", "ACTION_NOT_ALLOWED", action)
    try:
        wanted = validate_checkpoint(expected)
        saved = store.load()
        if saved is None:
            saved = wanted
            store.save(saved)
        if any(saved[key] != wanted[key] for key in AUTHORITY_KEYS):
            raise ResumeError("RESUME_IDENTITY_CHANGED")
        live_keys = {
            "repository",
            "epic",
            "task",
            "base_ref",
            "base_sha",
            "head_ref",
            "head_sha",
            "clean",
        }
        facts = _exact(live, live_keys)
        for key in live_keys - {"clean"}:
            if facts[key] != saved[key]:
                raise ResumeError("LIVE_STATE_CHANGED")
        if facts["clean"] is not True:
            raise ResumeError("DIRTY_WORKTREE")
        if action in saved["completed_actions"]:
            return ResumeResult("NO_OP", "ALREADY_COMPLETED", action)
        if saved["pending_action"] is not None:
            raise ResumeError("ACTION_OUTCOME_UNKNOWN")
        pending = dict(saved)
        pending["pending_action"] = action
        pending["status"] = "RUNNING"
        store.save(pending)
        try:
            effect()
        except Exception:
            failed = dict(pending)
            failed["status"] = "ESCALATE"
            store.save(failed)
            raise ResumeError("SIDE_EFFECT_FAILED")
        updated = dict(pending)
        updated["completed_actions"] = [*saved["completed_actions"], action]
        updated["pending_action"] = None
        updated["status"] = "RUNNING"
        store.save(updated)
        return ResumeResult("CONTINUE", "OK", action)
    except ResumeError as error:
        return ResumeResult("ESCALATE", error.machine_code, action)
    except Exception:
        return ResumeResult("ESCALATE", "INTERNAL_ERROR", action)


def select_relevant_memory(
    records: Iterable[Mapping[str, Any]], *, epic: int, task: int, limit: int = 3
) -> list[dict[str, Any]]:
    """Return only bounded redacted facts for the exact epic/task identity."""
    if isinstance(limit, bool) or not 1 <= limit <= 10:
        raise ResumeError("MEMORY_LIMIT_INVALID")
    selected: list[dict[str, Any]] = []
    for record in records:
        try:
            agent_memory.validate_record(record)
        except agent_memory.MemoryError:
            continue
        if record.get("epic_issue") != epic or record.get("task_issue") != task:
            continue
        selected.append(
            {
                "record_id": record["record_id"],
                "schema_version": record["schema_version"],
                "verdicts": list(record["verdicts"]),
                "failures": list(record["failures"]),
                "repair_loops": record["repair_loops"],
                "findings": record["findings"],
            }
        )
    return selected[-limit:]


def result_json(result: ResumeResult) -> str:
    return json.dumps(asdict(result), sort_keys=True)
