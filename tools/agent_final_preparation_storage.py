"""Capped owner-only preparation storage; files do not grant final approval."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools import agent_final_preparation_delivery as delivery
from tools import agent_final_preparation_records as records
from tools.agent_continuation import digest
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_runtime_cli import secure_directory


class Store:
    def __init__(
        self, seed: Path, root: Path, profile: dict[str, Any], guard: Callable[[], None]
    ) -> None:
        self.seed = seed
        self.root = root
        self.owner = records.profile(profile)
        self.guard = guard
        self.directory = seed / "final-preparation"
        self._safe()
        if not self.directory.exists():
            from tools.agent_owner_final_admission import _existing_context

            if _existing_context(self.seed, self.root):
                records.invalid()
            RunnerLock.current_owned(self.seed)
            self.directory.mkdir(mode=0o700)
        secure_directory(self.directory, self.root)

    def _safe(self) -> None:
        self.guard()
        for path in (self.seed, self.directory):
            if any(p.is_symlink() for p in (path, *path.parents)):
                records.invalid()
        secure_directory(self.seed, self.root)
        if self.directory.exists():
            secure_directory(self.directory, self.root)

    def assert_owned(self) -> None:
        self._safe()
        RunnerLock.current_owned(self.seed)

    def _path(self, key: str) -> Path:
        if (
            re.fullmatch(
                r"(?:ledger|role-state|final-state|(?:assessment|candidate|report|reservation)-[0-9a-f]{64})",
                key,
            )
            is None
        ):
            records.invalid()
        return self.directory / (key + ".json")

    @staticmethod
    def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = dict(pairs)
        if len(value) != len(pairs):
            records.invalid()
        return value

    def _validate(self, key: str, value: Any) -> dict[str, Any]:
        if key == "ledger":
            ledger_entries = delivery.entries(value)
            for entry in ledger_entries.values():
                self._namespace(entry["request"])
            return ledger_entries
        kind = key.split("-")[0]
        if key == "role-state":
            result = records.role_state(value)
        elif key == "final-state":
            # Shape admission only. The fixed driver separately proves the
            # original publication sources and nonnull owner-wait anchor.
            if isinstance(value, dict) and value.get("schema_version") == "2.0":
                result = records.state2(value)
            else:
                result = records.state(value)
        else:
            validators = {
                "assessment": records.assessment,
                "candidate": records.candidate,
                "report": records.report,
                "reservation": records.reservation,
            }
            result = validators[kind](value)
            if kind == "reservation":
                expected = digest(
                    {
                        "launch_digest": result["launch_digest"],
                        "ordinal": result["ordinal"],
                    }
                )
            else:
                expected = result[
                    "candidate_digest" if kind == "candidate" else "record_digest"
                ]
            if key != kind + "-" + expected:
                records.invalid()
        self._namespace(result)
        return result

    def _namespace(self, value: dict[str, Any]) -> None:
        for key in (
            "repository",
            "epic",
            "mandate_digest",
            "policy_sha",
            "owner_identity",
        ):
            if key in value and value[key] != self.owner[key]:
                records.invalid()

    def read(self, key: str) -> dict[str, Any] | None:
        self.assert_owned()
        path = self._path(key)
        if not path.exists() and not path.is_symlink():
            return None
        try:
            nonblocking = getattr(os, "O_NONBLOCK", None)
            if type(nonblocking) is not int:
                records.invalid()
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | nonblocking)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != self.seed.stat().st_uid
                    or info.st_mode & 0o077
                    or info.st_size > self.owner["max_artifact_bytes"]
                ):
                    records.invalid()
                raw = stream.read(self.owner["max_artifact_bytes"] + 1)
                if len(raw) > self.owner["max_artifact_bytes"]:
                    records.invalid()
            value = json.loads(raw, object_pairs_hook=self._object)
        except (OSError, ValueError):
            records.invalid()
        return self._validate(key, value)

    def write(self, key: str, value: dict[str, Any]) -> None:
        self.assert_owned()
        path = self._path(key)
        value = self._validate(key, value)
        previous = self.read(key)
        if previous is not None:
            if key not in {
                "ledger",
                "role-state",
                "final-state",
            } and not key.startswith("reservation-"):
                if value != previous:
                    records.invalid()
                return
            if key == "final-state" and (
                previous["schema_version"] == "2.0"
                or value["schema_version"] == "2.0"
            ):
                # No schema1 upgrade/downgrade or anchor backfill. Every
                # subsequent state2 publication preserves its original anchor.
                records.state2_transition(previous, value)
            if key in {"role-state", "final-state"}:
                if value["calls_consumed"] < previous["calls_consumed"]:
                    records.invalid()
                if value["launch_digest"] != previous["launch_digest"]:
                    records.invalid()
            if key == "ledger":
                for operation, old in previous.items():
                    new = value.get(operation)
                    if new is None or old["request"] != new["request"]:
                        records.invalid()
                    if old["status"] == "APPLIED" and new != old:
                        records.invalid()
            if key.startswith("reservation-"):
                immutable = records.RESERVATION - {
                    "status",
                    "settings_digest",
                    "output_digest",
                }
                if any(value[field] != previous[field] for field in immutable):
                    records.invalid()
                if previous["status"] in {"COMPLETED", "UNKNOWN"} and value != previous:
                    records.invalid()
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        if len(encoded) > self.owner["max_artifact_bytes"]:
            records.invalid()
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(dir=self.directory, prefix=".pending-")
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._replace(temporary, path)
            temporary = None
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            records.invalid()
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _replace(self, temporary: str, path: Path) -> None:
        os.replace(temporary, path)

    def load(self) -> dict[str, dict[str, Any]]:
        value = self.read("ledger")
        return {} if value is None else delivery.entries(value)

    def save(self, entries: dict[str, dict[str, Any]]) -> None:
        self.write("ledger", entries)
