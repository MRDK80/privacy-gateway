"""Explicit private bootstrap recovery; evidence never grants authority."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tools import agent_epic_loop as loop

DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")

CATEGORIES = frozenset(
    {
        "timeout",
        "network",
        "permission",
        "stale_identity",
        "invalid_adapter_response",
        "unknown",
    }
)
STATE_KEYS = {
    "schema_version",
    "generation",
    "stopped",
    "category",
    "authorization_digest",
    "attempts",
    "pending",
    "binding_digest",
}
APPROVAL_KEYS = {
    "schema_version",
    "checkpoint_digest",
    "artifact_digest",
    "mandate_digest",
    "owner_identity",
    "issued_at",
    "expires_at",
    "generation",
    "budgets",
    "repository",
    "epic",
    "task",
    "policy_sha",
}


class RecoveryError(loop.LoopError):
    """Only a bounded machine code is exposed."""


def digest(value: Mapping[str, Any]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )


def checkpoint_digest(value: Mapping[str, Any]) -> str:
    return digest(
        {
            key: item
            for key, item in value.items()
            if key not in {"status", "pending_phase"}
        }
    )


def artifact_digest(value: Mapping[str, Any]) -> str:
    return digest(
        {key: item for key, item in value.items() if key != "delivery_identity"}
    )


def private_value(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or (
            os.name != "nt"
            and (
                info.st_uid != getattr(os, "getuid", lambda: -1)()
                or stat.S_IMODE(info.st_mode) & 0o077
            )
        ):
            raise RecoveryError("RECOVERY_STATE_INVALID")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RecoveryError("RECOVERY_STATE_INVALID")
        return value
    except (OSError, ValueError) as error:
        raise RecoveryError("RECOVERY_STATE_INVALID") from error


def present(directory: Path) -> bool:
    marker = directory / "bootstrap-recovery.json"
    if marker.exists() or marker.is_symlink():
        return True
    config = directory / "runtime-task.json"
    return (config.exists() or config.is_symlink()) and private_value(config).get(
        "schema_version"
    ) == "2.0"


def diagnostic(error: BaseException) -> str:
    cause = error.__cause__ or error
    if isinstance(cause, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(cause, OSError) and cause.errno in {
        errno.ENETDOWN,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
    }:
        return "network"
    if isinstance(cause, PermissionError):
        return "permission"
    return "unknown"


def command_entry(directory: Path, root: Path) -> None:
    """Read-only approval check before namespace entry; child revalidates fully."""
    control = Control(directory, root)
    with control.lock():
        value = control.load()
        if value["pending"] is not None:
            control.stop()
            raise RecoveryError("RECOVERY_INTERRUPTED")
        config = private_value(directory / "runtime-task.json")
        approval = private_value(directory / "bootstrap-recovery-approval.json")
        import time

        if (
            config.get("schema_version") != "2.0"
            or digest(approval) != config.get("approved_recovery_digest")
            or set(approval) != APPROVAL_KEYS
            or approval["generation"] != value["generation"]
            or type(approval["issued_at"]) is not int
            or type(approval["expires_at"]) is not int
            or not approval["issued_at"] <= int(time.time()) < approval["expires_at"]
        ):
            raise RecoveryError("RECOVERY_STOPPED")


class Control:
    def __init__(self, directory: Path, repository_root: Path) -> None:
        self.directory = directory.resolve()
        root = repository_root.resolve()
        if (
            directory.is_symlink()
            or self.directory.is_relative_to(root)
            or any(
                (parent / ".git").is_file() or (parent / ".git" / "HEAD").is_file()
                for parent in (self.directory, *self.directory.parents)
            )
        ):
            raise RecoveryError("UNSAFE_RECOVERY_STORAGE")
        self.path = self.directory / "bootstrap-recovery.json"

    def lock(self) -> loop.RunnerLock:
        return loop.RunnerLock(self.directory / "bootstrap-recovery-lock")

    def load(self) -> dict[str, Any]:
        value = (
            private_value(self.path)
            if self.path.exists() or self.path.is_symlink()
            else {
                "schema_version": "1.0",
                "generation": 0,
                "stopped": True,
                "category": "unknown",
                "authorization_digest": None,
                "attempts": {},
                "pending": None,
                "binding_digest": None,
            }
        )
        if (
            set(value) != STATE_KEYS
            or value["schema_version"] != "1.0"
            or type(value["generation"]) is not int
            or value["generation"] < 0
            or type(value["stopped"]) is not bool
            or not isinstance(value["category"], str)
            or value["category"] not in CATEGORIES
            or not isinstance(value["attempts"], dict)
            or any(
                not isinstance(key, str) or type(count) is not int or count < 0
                for key, count in value["attempts"].items()
            )
            or any(
                item is not None
                and (not isinstance(item, str) or DIGEST_RE.fullmatch(item) is None)
                for item in (value["authorization_digest"], value["binding_digest"])
            )
            or (value["pending"] is not None and not isinstance(value["pending"], str))
        ):
            raise RecoveryError("RECOVERY_STATE_INVALID")
        return value

    def save(self, value: Mapping[str, Any]) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self.directory, prefix=".recovery-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def stop(self, category: str = "unknown") -> None:
        """Caller holds the recovery lock when writes are in progress."""
        if category not in CATEGORIES:
            raise RecoveryError("RECOVERY_STATE_INVALID")
        value = self.load()
        if value["stopped"] and value["generation"] > 0 and value["pending"] is None:
            return
        value.update(
            generation=value["generation"] + 1,
            stopped=True,
            category=category,
            pending=None,
        )
        self.save(value)

    def check_entry(self) -> None:
        """Never auto-open; concrete recovery verifies a fresh approval separately."""
        value = self.load()
        if value["pending"] is not None or value["stopped"]:
            raise RecoveryError("RECOVERY_STOPPED")

    def authorize(
        self,
        approval: Mapping[str, Any],
        approved_digest: str | None,
        binding: Mapping[str, Any],
        *,
        now: int,
        owner: str,
        operation_ids: set[str],
    ) -> None:
        value = self.load()
        if value["pending"] is not None:
            self.stop()
            raise RecoveryError("RECOVERY_INTERRUPTED")
        if (
            set(approval) != APPROVAL_KEYS
            or approval.get("schema_version") != "1.0"
            or approved_digest is None
            or digest(approval) != approved_digest
            or any(approval.get(key) != item for key, item in binding.items())
            or approval["owner_identity"] != owner
            or type(approval["generation"]) is not int
            or approval["generation"] != value["generation"]
            or type(approval["issued_at"]) is not int
            or type(approval["expires_at"]) is not int
            or not 0 <= approval["issued_at"] <= now < approval["expires_at"]
            or not isinstance(approval["budgets"], dict)
            or set(approval["budgets"]) != operation_ids
            or any(
                type(count) is not int or not 1 <= count <= 3
                for count in approval["budgets"].values()
            )
        ):
            raise RecoveryError("RECOVERY_APPROVAL_INVALID")
        bound = digest(binding)
        if value["binding_digest"] not in {None, bound}:
            raise RecoveryError("RECOVERY_IDENTITY_CHANGED")
        if value["authorization_digest"] != approved_digest:
            # New owner approval can open only the current stopped generation.
            if not value["stopped"]:
                raise RecoveryError("RECOVERY_APPROVAL_CHANGED")
            value.update(
                authorization_digest=approved_digest, attempts={}, binding_digest=bound
            )
        value["stopped"] = False
        self.save(value)

    def reserve(self, operation_id: str, maximum: int) -> None:
        self.check_entry()
        value = self.load()
        consumed = value["attempts"].get(operation_id, 0)
        if consumed >= maximum:
            self.stop()
            raise RecoveryError("RECOVERY_BUDGET_EXHAUSTED")
        value["attempts"][operation_id] = consumed + 1
        value["pending"] = operation_id
        self.save(value)

    def complete(self, operation_id: str) -> None:
        value = self.load()
        if value["stopped"] or value["pending"] != operation_id:
            raise RecoveryError("RECOVERY_STOPPED")
        value["pending"] = None
        self.save(value)


class BootstrapRecovery:
    """Trusted workflow/authority callbacks; no caller-supplied success facts."""

    def __init__(
        self,
        directory: Path,
        root: Path,
        authority: Callable[[], Any],
        approved: Callable[[], str | None],
    ) -> None:
        self.control = Control(directory, root)
        self.root = root
        self.authority = authority
        self.approved = approved
        self.binding: dict[str, Any] = {}
        self.requests: dict[str, Any] = {}
        self.canonical: (
            Callable[[Any], tuple[str, Mapping[str, object] | None]] | None
        ) = None

    def prepare(
        self,
        saved: Mapping[str, Any],
        artifact: Mapping[str, Any],
        requests: Mapping[str, Any],
        canonical: Callable[[Any], tuple[str, Mapping[str, object] | None]],
    ) -> None:
        mandate, approved, context = self.authority()
        if saved.get("schema_version") != "3.0":
            raise RecoveryError("PINNED_CHECKPOINT_REQUIRED")
        self.binding = {
            "checkpoint_digest": checkpoint_digest(saved),
            "artifact_digest": artifact_digest(artifact),
            "mandate_digest": approved,
            **{key: saved[key] for key in ("repository", "epic", "task", "policy_sha")},
        }
        self.requests = dict(requests)
        self.canonical = canonical
        approval = private_value(
            self.control.directory / "bootstrap-recovery-approval.json"
        )
        self.control.authorize(
            approval,
            self.approved(),
            self.binding,
            now=context.now,
            owner=context.owner_identity,
            operation_ids={request.operation_id for request in requests.values()},
        )

    def refresh(self, request: Any) -> bool:
        from tools import agent_epic_delivery as delivery

        try:
            state = self.control.load()
            approval = private_value(
                self.control.directory / "bootstrap-recovery-approval.json"
            )
            mandate, approved, context = self.authority()
            return (
                not state["stopped"]
                and digest(approval) == self.approved() == state["authorization_digest"]
                and approval["generation"] == state["generation"]
                and approval["issued_at"] <= context.now < approval["expires_at"]
                and all(approval[key] == value for key, value in self.binding.items())
                and approved == self.binding["mandate_digest"]
                and delivery._authorized(request, mandate, approved, context) is None
            )
        except Exception:
            return False

    def reconcile(self, request: Any) -> tuple[str, Mapping[str, object] | None]:
        if self.canonical is None or request != self.requests.get(request.operation):
            raise RecoveryError("RECOVERY_IDENTITY_CHANGED")
        state, receipt = self.canonical(request)
        malformed = (
            state not in {"APPLIED", "NOT_APPLIED", "UNKNOWN"}
            or (state != "APPLIED" and receipt is not None)
            or (state == "APPLIED" and not isinstance(receipt, Mapping))
        )
        if state == "APPLIED" and isinstance(receipt, Mapping):
            keys = {
                "commit_task": {"head_sha", "tree_hash"},
                "push_task": {"head_sha"},
                "create_task_pr": {"head_sha", "pr"},
            }[request.operation]
            malformed = malformed or set(receipt) != keys or (
                receipt.get("head_sha") != self.requests["push_task"].head_sha
            )
            if request.operation == "create_task_pr":
                pr = receipt.get("pr")
                malformed = malformed or type(pr) is not int or pr < 1
            if request.operation == "commit_task":
                tree = receipt.get("tree_hash")
                malformed = malformed or (
                    not isinstance(tree, str) or loop.SHA_RE.fullmatch(tree) is None
                )
        if malformed:
            self.control.stop("invalid_adapter_response")
            return "UNKNOWN", None
        if state == "UNKNOWN":
            self.control.stop()
            return "UNKNOWN", None
        return state, receipt

    def verify_receipt(
        self, request: Any, receipt: Mapping[str, object] | None
    ) -> bool:
        state, fresh = self.reconcile(request)
        return state == "APPLIED" and fresh is not None and receipt == fresh

    def before_effect(self, request: Any) -> None:
        if not self.refresh(request):
            self.control.stop("stale_identity")
            raise RecoveryError("RECOVERY_STOPPED")
        state, _ = self.reconcile(request)
        if state != "NOT_APPLIED":
            self.control.stop()
            raise RecoveryError("RECOVERY_NOT_PROVEN_ABSENT")
        # Refresh again after canonical reads; expiry/revocation can change there.
        if not self.refresh(request):
            self.control.stop("stale_identity")
            raise RecoveryError("RECOVERY_STOPPED")
        approval = private_value(
            self.control.directory / "bootstrap-recovery-approval.json"
        )
        self.control.reserve(
            request.operation_id, approval["budgets"][request.operation_id]
        )

    def complete(self, request: Any) -> None:
        self.control.complete(request.operation_id)

    def stop(self, category: str = "unknown") -> None:
        self.control.stop(category)
