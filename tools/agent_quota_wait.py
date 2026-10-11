"""Finite private quota reservations bound to an existing #299 stage budget."""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable
from typing import Any

from tools import agent_epic_wait as wait
from tools import agent_orchestrate as workflow
from tools import agent_quota as quota
from tools.agent_epic_runtime_cli import private_json

IDENTITY_KEYS = {
    "repository",
    "epic",
    "policy_sha",
    "mandate_digest",
    "quota_profile_digest",
    "wait_profile_digest",
    "runtime_config_digest",
    "limit_id",
    "source_digest",
}
KEYS = {
    "schema_version",
    "identity",
    "stage",
    "original_wait_digest",
    "started_at",
    "deadline_at",
    "last_seen_at",
    "attempts",
    "status",
    "exhausted_windows",
    "resets_at",
    "next_check_at",
    "interrupted",
    "wakeup",
}


def bare_digest(value: Any) -> str:
    return wait.digest(value).removeprefix("sha256:")


class QuotaWait:
    def __init__(
        self,
        reader: wait.Reader,
        owner: dict[str, Any],
        identity: dict[str, Any],
        *,
        runtime_deadline: int,
        now: Callable[[], int],
        guard: Callable[[], None],
    ) -> None:
        self.reader, self.profile = reader, quota.profile(owner)
        self.identity = copy.deepcopy(identity)
        self.now, self.guard = now, guard
        if set(identity) != IDENTITY_KEYS:
            raise quota.QuotaError("QUOTA_BINDING_INVALID")
        for key in ("repository", "epic", "policy_sha", "limit_id"):
            if identity[key] != owner[key]:
                raise quota.QuotaError("QUOTA_BINDING_INVALID")
        if (
            identity["mandate_digest"] != owner["mandate_digest"]
            or identity["wait_profile_digest"] != owner["wait_profile_digest"]
            or identity["quota_profile_digest"] != bare_digest(owner)
            or identity["source_digest"] != bare_digest(owner["source"])
        ):
            raise quota.QuotaError("QUOTA_BINDING_INVALID")
        for key in IDENTITY_KEYS - {"repository", "epic", "policy_sha", "limit_id"}:
            pattern = (
                r"sha256:[0-9a-f]{64}"
                if key
                in {"mandate_digest", "wait_profile_digest", "runtime_config_digest"}
                else r"[0-9a-f]{64}"
            )
            if (
                not isinstance(identity[key], str)
                or re.fullmatch(pattern, identity[key]) is None
            ):
                raise quota.QuotaError("QUOTA_BINDING_INVALID")
        # Persist original stage record first, under its original guards and lock.
        with reader._locked():
            original = reader._load()
            reader._check(original)
            reader._save(original)
        self.original = {
            k: original[k] for k in ("identity", "started_at", "deadline_at")
        }
        self.original_digest = bare_digest(self.original)
        self.deadline = min(math.floor(original["deadline_at"]), runtime_deadline)
        self.path = reader.directory / "quota.json"

    def _validate(self, value: Any) -> dict[str, Any]:
        if (
            not isinstance(value, dict)
            or set(value) != KEYS
            or value["schema_version"] != "1.0"
            or value["identity"] != self.identity
            or value["stage"] != self.reader.identity["stage"]
            or value["original_wait_digest"] != self.original_digest
        ):
            raise quota.QuotaError("QUOTA_STATE_INVALID")
        for key in ("started_at", "deadline_at", "last_seen_at", "attempts"):
            if type(value[key]) is not int or value[key] < 0:
                raise quota.QuotaError("QUOTA_STATE_INVALID")
        if (
            not value["started_at"] <= value["last_seen_at"] <= value["deadline_at"]
            or value["deadline_at"] > self.deadline
            or value["deadline_at"]
            > value["started_at"] + self.profile["limits"]["max_wait_seconds"]
            or value["attempts"] > self.profile["limits"]["max_polls"]
            or not isinstance(value["status"], str)
            or value["status"] not in {"READY", "RESERVED", "WAITING", "FAILED"}
            or not isinstance(value["exhausted_windows"], list)
            or any(not isinstance(w, str) for w in value["exhausted_windows"])
            or len(set(value["exhausted_windows"])) != len(value["exhausted_windows"])
            or any(
                w not in {"primary", "secondary"} for w in value["exhausted_windows"]
            )
        ):
            raise quota.QuotaError("QUOTA_STATE_INVALID")
        reset, next_at = value["resets_at"], value["next_check_at"]
        if reset is not None and (
            type(reset) is not int or not 0 <= reset <= 253402300799
        ):
            raise quota.QuotaError("QUOTA_STATE_INVALID")
        if value["status"] == "WAITING":
            if (
                type(next_at) is not int
                or not value["last_seen_at"] <= next_at <= value["deadline_at"]
            ):
                raise quota.QuotaError("QUOTA_STATE_INVALID")
        elif next_at is not None:
            raise quota.QuotaError("QUOTA_STATE_INVALID")
        interrupted = value["interrupted"]
        if interrupted is not None:
            if (
                not isinstance(interrupted, dict)
                or set(interrupted)
                != {
                    "checkpoint_identity_digest",
                    "phase",
                    "operation_digest",
                    "outcome",
                }
                or interrupted["phase"] not in self.reader.identity["stage"].split(":")
                or not isinstance(interrupted["outcome"], str)
                or interrupted["outcome"]
                not in {"PENDING", "APPLIED", "NOT_APPLIED", "UNKNOWN"}
                or any(
                    not isinstance(interrupted[k], str)
                    or re.fullmatch(r"[0-9a-f]{64}", interrupted[k]) is None
                    for k in ("checkpoint_identity_digest", "operation_digest")
                )
            ):
                raise quota.QuotaError("QUOTA_STATE_INVALID")
        wakeup = value["wakeup"]
        if wakeup is not None:
            if (
                not isinstance(wakeup, dict)
                or set(wakeup)
                != {
                    "candidate_digest",
                    "arm_id",
                    "attempts",
                    "status",
                    "next_check_at",
                    "unit_digest",
                }
                or any(
                    not isinstance(wakeup[k], str)
                    or re.fullmatch(r"[0-9a-f]{64}", wakeup[k]) is None
                    for k in ("candidate_digest", "arm_id", "unit_digest")
                )
                or type(wakeup["attempts"]) is not int
                or not 1 <= wakeup["attempts"] <= self.profile["limits"]["max_wakeups"]
                or not isinstance(wakeup["status"], str)
                or wakeup["status"]
                not in {"RESERVED", "ARMED", "FIRED", "CANCELLED", "UNKNOWN"}
                or type(wakeup["next_check_at"]) is not int
                or not value["started_at"]
                <= wakeup["next_check_at"]
                <= value["deadline_at"]
            ):
                raise quota.QuotaError("QUOTA_STATE_INVALID")
        return value

    def _load(self) -> dict[str, Any]:
        if self.path.exists() or self.path.is_symlink():
            self.reader._private(self.path.lstat())
            return self._validate(private_json(self.path))
        now = self.now()
        value = {
            "schema_version": "1.0",
            "identity": copy.deepcopy(self.identity),
            "stage": self.reader.identity["stage"],
            "original_wait_digest": self.original_digest,
            "started_at": now,
            "deadline_at": min(
                self.deadline, now + self.profile["limits"]["max_wait_seconds"]
            ),
            "last_seen_at": now,
            "attempts": 0,
            "status": "READY",
            "exhausted_windows": [],
            "resets_at": None,
            "next_check_at": None,
            "interrupted": None,
            "wakeup": None,
        }
        return self._validate(value)

    def _save(self, value: dict[str, Any]) -> None:
        workflow.StateStore(self.reader.directory).save("quota", self._validate(value))

    def _check(self, value: dict[str, Any]) -> int:
        self.guard()
        self.reader.guard()
        now = self.now()
        if type(now) is not int or now < value["last_seen_at"]:
            raise quota.QuotaError("QUOTA_CLOCK_INVALID")
        if now >= value["deadline_at"]:
            raise quota.QuotaError("QUOTA_DEADLINE_EXCEEDED")
        value["last_seen_at"] = now
        return now

    def read_guard(self) -> None:
        """Cancel a live source against its original persisted finite deadline."""
        self._check(self._load())

    def read_timeout(self) -> float:
        value = self._load()
        now = self._check(value)
        return float(value["deadline_at"] - now)

    def poll(self, read: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self.reader._locked():
            value = self._load()
            now = self._check(value)
            if value["status"] in {"RESERVED", "FAILED"}:
                raise quota.QuotaError(
                    "QUOTA_OUTCOME_UNKNOWN"
                    if value["status"] == "RESERVED"
                    else "QUOTA_WAIT_FAILED"
                )
            if value["wakeup"] is not None and value["wakeup"]["status"] in {
                "RESERVED",
                "UNKNOWN",
            }:
                raise quota.QuotaError("QUOTA_WAKE_OUTCOME_UNKNOWN")
            if value["status"] == "WAITING" and now < value["next_check_at"]:
                self._save(value)
                return copy.deepcopy(value)
            if value["attempts"] >= self.profile["limits"]["max_polls"]:
                raise quota.QuotaError("QUOTA_POLL_BUDGET_EXHAUSTED")
            value["attempts"] += 1
            value.update(status="RESERVED", next_check_at=None)
            self._save(value)
            try:
                snapshot = read()
                self._check(value)
                windows, reset = quota.exhausted(snapshot)
            except Exception:
                value.update(status="FAILED", next_check_at=None)
                self._save(value)
                raise
            value.update(
                status="WAITING" if windows else "READY",
                exhausted_windows=windows,
                resets_at=reset,
            )
            if windows:
                next_at = min(
                    value["last_seen_at"] + self.profile["limits"]["poll_seconds"],
                    value["deadline_at"],
                )
                if reset is not None and reset > value["last_seen_at"]:
                    next_at = min(next_at, reset)
                value["next_check_at"] = next_at
            self._save(value)
            return copy.deepcopy(value)
