"""Narrow one-shot systemd user adapter; installation requires an exact approval."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from tools import agent_orchestrate as workflow
from tools.agent_epic_runtime_cli import DeploymentError, private_json, secure_directory
from tools.agent_quota import QuotaError
from tools.agent_quota_wait import bare_digest

CANDIDATE_KEYS = {
    "schema_version",
    "repository",
    "epic",
    "policy_sha",
    "state_digest",
    "runtime_config_digest",
    "quota_profile_digest",
    "source_digest",
    "supervisor_sha256",
    "host_probe_digest",
    "issued_at",
    "expires_at",
    "unit_prefix",
    "service_sha256",
    "timer_template_sha256",
    "candidate_digest",
}
TIMER_TEMPLATE = (
    "[Unit]\nDescription=Privacy Gateway finite quota wake\n[Timer]\n"
    "OnCalendar=@{at}\nPersistent=true\nAccuracySec=1s\nUnit={service}\n"
)


def _arg(value: str) -> str:
    if not value or any(c in value for c in "\x00\r\n%"):
        raise QuotaError("QUOTA_WAKE_COMMAND_INVALID")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def service_bytes(command: Sequence[str]) -> bytes:
    if (
        not command
        or not all(isinstance(v, str) for v in command)
        or not Path(command[0]).is_absolute()
    ):
        raise QuotaError("QUOTA_WAKE_COMMAND_INVALID")
    return (
        "[Unit]\nDescription=Privacy Gateway bounded quota supervisor\n"
        "[Service]\nType=oneshot\n"
        + "ExecStart="
        + " ".join(_arg(v) for v in command)
        + ' "--wake-arm-id" "%i"\n'
    ).encode()


def candidate(
    bindings: dict[str, Any], service: bytes, *, issued_at: int, expires_at: int
) -> dict[str, Any]:
    value = {
        "schema_version": "1.0",
        **bindings,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "unit_prefix": "pgw-quota-"
        + bare_digest(
            {k: bindings[k] for k in ("state_digest", "runtime_config_digest")}
        )[:32],
        "service_sha256": hashlib.sha256(service).hexdigest(),
        "timer_template_sha256": hashlib.sha256(TIMER_TEMPLATE.encode()).hexdigest(),
    }
    value["candidate_digest"] = bare_digest(value)
    return validate(value)


def validate(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != CANDIDATE_KEYS
        or value["schema_version"] != "1.0"
        or type(value["epic"]) is not int
        or value["epic"] <= 0
        or not isinstance(value["repository"], str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]) is None
        or not isinstance(value["policy_sha"], str)
        or re.fullmatch(r"[0-9a-f]{40}", value["policy_sha"]) is None
        or type(value["issued_at"]) is not int
        or type(value["expires_at"]) is not int
        or not 0 <= value["issued_at"] < value["expires_at"] <= 253402300799
    ):
        raise QuotaError("QUOTA_WAKE_CANDIDATE_INVALID")
    for key in CANDIDATE_KEYS - {
        "schema_version",
        "repository",
        "epic",
        "policy_sha",
        "issued_at",
        "expires_at",
        "unit_prefix",
    }:
        if (
            not isinstance(value[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
        ):
            raise QuotaError("QUOTA_WAKE_CANDIDATE_INVALID")
    prefix = (
        "pgw-quota-"
        + bare_digest({k: value[k] for k in ("state_digest", "runtime_config_digest")})[
            :32
        ]
    )
    if (
        value["unit_prefix"] != prefix
        or value["candidate_digest"]
        != bare_digest({k: v for k, v in value.items() if k != "candidate_digest"})
        or value["timer_template_sha256"]
        != hashlib.sha256(TIMER_TEMPLATE.encode()).hexdigest()
    ):
        raise QuotaError("QUOTA_WAKE_CANDIDATE_INVALID")
    return value


class SystemdUser:
    def __init__(
        self,
        state: Path,
        root: Path,
        units: Path,
        *,
        guard: Callable[[], None],
        now: Callable[[], int],
        run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
        cancel_guard: Callable[[], None] | None = None,
    ) -> None:
        if os.name != "posix":
            raise QuotaError("QUOTA_WAKE_UNSUPPORTED")
        self.state, self.root = secure_directory(state, root), root
        # Never bootstrap, enable linger, create the host directory, or alter settings.
        self.units = secure_directory(units, root)
        self.guard, self.now, self.run = guard, now, run
        self.cancel_guard = cancel_guard or guard

    def _host(
        self,
        argv: list[str],
        *,
        cancellation: bool = False,
    ) -> str:
        guard = self.cancel_guard if cancellation else self.guard
        guard()
        try:
            if self.run is None:
                from tools.agent_quota_process import run as bounded_run

                result = bounded_run(
                    ["/usr/bin/systemctl", "--user", *argv],
                    timeout=5,
                    cap=65536,
                    guard=guard,
                )
            else:
                result = self.run(
                    ["/usr/bin/systemctl", "--user", *argv],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            raise QuotaError("QUOTA_WAKE_HOST_UNKNOWN") from None
        guard()
        if result.returncode or len(result.stdout) > 65536:
            raise QuotaError("QUOTA_WAKE_HOST_UNKNOWN")
        try:
            decoded = result.stdout.decode("utf-8")
            if not isinstance(decoded, str):
                raise QuotaError("QUOTA_WAKE_HOST_UNKNOWN")
            return decoded
        except UnicodeError:
            raise QuotaError("QUOTA_WAKE_HOST_UNKNOWN") from None

    def host_digest(self) -> str:
        self._manager_path()
        version = self._host(["show", "--property=Version", "--value"]).strip()
        if not version or len(version) > 128 or not version.isascii():
            raise QuotaError("QUOTA_WAKE_HOST_UNKNOWN")
        return bare_digest(
            {
                "backend": "systemd_user",
                "uid": self.units.stat().st_uid,
                "units_digest": bare_digest(str(self.units)),
                "manager_version_digest": hashlib.sha256(version.encode()).hexdigest(),
            }
        )

    def _manager_path(self) -> None:
        paths = self._host(["show", "--property=UnitPath", "--value"]).split()
        if str(self.units) not in paths:
            raise QuotaError("QUOTA_WAKE_UNIT_PATH_UNSUPPORTED")

    def _loaded(
        self, name: str, *, cancellation: bool = False, fragment: str | None = None
    ) -> None:
        expected = {
            "FragmentPath": str(self.units / (fragment or name)),
            "DropInPaths": "",
            "NeedDaemonReload": "no",
            "LoadState": "loaded",
        }
        for property_name, value in expected.items():
            observed = self._host(
                ["show", name, "--property=" + property_name, "--value"],
                cancellation=cancellation,
            ).strip()
            if observed != value:
                raise QuotaError("QUOTA_WAKE_UNIT_BINDING_CHANGED")

    def _bytes(self, name: str) -> bytes | None:
        if (
            re.fullmatch(
                r"pgw-quota-[0-9a-f]{32}(?:@(?:[0-9a-f]{64})?\.service|-[0-9a-f]{64}\.timer)",
                name,
            )
            is None
        ):
            raise QuotaError("QUOTA_WAKE_UNIT_INVALID")
        path = self.units / name
        if not path.exists() and not path.is_symlink():
            return None
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != self.units.stat().st_uid
            or info.st_mode & 0o077
            or info.st_size > 65536
        ):
            raise QuotaError("QUOTA_WAKE_UNIT_UNSAFE")
        return path.read_bytes()

    def _create(self, name: str, data: bytes) -> None:
        if self._bytes(name) is not None:
            raise QuotaError("QUOTA_WAKE_UNIT_EXISTS")
        fd = os.open(
            self.units / name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def _lifecycle(self, value: dict[str, Any]) -> None:
        self.guard()
        current = self.now()
        if (
            type(current) is not int
            or not value["issued_at"] <= current < value["expires_at"]
        ):
            raise QuotaError("QUOTA_WAKE_EXPIRED")

    def install(
        self, value: dict[str, Any], service: bytes, *, approved_digest: str
    ) -> None:
        value = validate(value)
        self._lifecycle(value)
        if (
            approved_digest != value["candidate_digest"]
            or hashlib.sha256(service).hexdigest() != value["service_sha256"]
        ):
            raise QuotaError("QUOTA_WAKE_APPROVAL_REQUIRED")
        name = value["unit_prefix"] + "@.service"
        path = self.state / "quota-install.json"
        if path.exists() or path.is_symlink():
            # An existing installation is reconciled read-only, never installed twice.
            saved = private_json(path)
            if (
                saved != {"candidate": value, "status": "APPLIED"}
                or self._bytes(name) != service
            ):
                raise QuotaError("QUOTA_WAKE_INSTALL_UNKNOWN")
            self._manager_path()
            self._loaded(name)
            return
        if self._bytes(name) is not None:
            raise QuotaError("QUOTA_WAKE_INSTALL_CONFLICT")
        self._manager_path()
        workflow.StateStore(self.state).save(
            "quota-install", {"candidate": value, "status": "RESERVED"}
        )
        self._create(name, service)
        self._host(["daemon-reload"])
        self._lifecycle(value)
        if self._bytes(name) != service:
            raise QuotaError("QUOTA_WAKE_INSTALL_UNKNOWN")
        self._loaded(name)
        workflow.StateStore(self.state).save(
            "quota-install", {"candidate": value, "status": "APPLIED"}
        )

    def verify(self, value: dict[str, Any]) -> None:
        value = validate(value)
        self._lifecycle(value)
        saved = private_json(self.state / "quota-install.json")
        data = self._bytes(value["unit_prefix"] + "@.service")
        if (
            saved != {"candidate": value, "status": "APPLIED"}
            or data is None
            or hashlib.sha256(data).hexdigest() != value["service_sha256"]
        ):
            raise QuotaError("QUOTA_WAKE_NOT_INSTALLED")
        self._manager_path()
        self._loaded(value["unit_prefix"] + "@.service")

    def arm(
        self, value: dict[str, Any], arm_id: str, at: int, *, deadline: int
    ) -> dict[str, Any]:
        self.verify(value)
        if (
            re.fullmatch(r"[0-9a-f]{64}", arm_id) is None
            or type(at) is not int
            or not self.now() < at <= min(deadline, value["expires_at"])
        ):
            raise QuotaError("QUOTA_WAKE_DEADLINE_INVALID")
        name = value["unit_prefix"] + "-" + arm_id + ".timer"
        data = TIMER_TEMPLATE.format(
            at=at, service=value["unit_prefix"] + "@" + arm_id + ".service"
        ).encode()
        key = "quota-arm-" + arm_id
        record = {
            "candidate_digest": value["candidate_digest"],
            "arm_id": arm_id,
            "next_check_at": at,
            "unit_digest": hashlib.sha256(data).hexdigest(),
            "status": "RESERVED",
        }
        path = self.state / (key + ".json")
        if path.exists() or path.is_symlink():
            saved = private_json(path)
            if saved != {**record, "status": "ARMED"} or self._bytes(name) != data:
                raise QuotaError("QUOTA_WAKE_ARM_UNKNOWN")
            self._loaded(name)
            if (
                self._host(["show", name, "--property=ActiveState", "--value"]).strip()
                != "active"
            ):
                raise QuotaError("QUOTA_WAKE_ARM_UNKNOWN")
            return saved
        if self._bytes(name) is not None:
            raise QuotaError("QUOTA_WAKE_ARM_CONFLICT")
        workflow.StateStore(self.state).save(key, record)
        self._create(name, data)
        self._host(["daemon-reload"])
        self._lifecycle(value)
        self._loaded(name)
        if not self.now() < at <= min(deadline, value["expires_at"]):
            raise QuotaError("QUOTA_WAKE_DEADLINE_INVALID")
        self._host(["start", name])
        if (
            self._bytes(name) != data
            or self._host(["show", name, "--property=ActiveState", "--value"]).strip()
            != "active"
        ):
            raise QuotaError("QUOTA_WAKE_ARM_UNKNOWN")
        record["status"] = "ARMED"
        workflow.StateStore(self.state).save(key, record)
        return record

    def verify_callback(self, value: dict[str, Any], arm: dict[str, Any]) -> None:
        self.verify(value)
        arm_id = arm["arm_id"]
        if re.fullmatch(r"[0-9a-f]{64}", arm_id) is None:
            raise QuotaError("QUOTA_WAKE_STALE")
        name = value["unit_prefix"] + "-" + arm_id + ".timer"
        service = value["unit_prefix"] + "@" + arm_id + ".service"
        data = TIMER_TEMPLATE.format(at=arm["next_check_at"], service=service).encode()
        expected = {
            k: arm[k]
            for k in ("candidate_digest", "arm_id", "next_check_at", "unit_digest")
        }
        try:
            receipt = private_json(self.state / ("quota-arm-" + arm_id + ".json"))
        except DeploymentError:
            raise QuotaError("QUOTA_WAKE_ARM_UNKNOWN") from None
        if (
            receipt != {**expected, "status": "ARMED"}
            or self._bytes(name) != data
            or hashlib.sha256(data).hexdigest() != arm["unit_digest"]
        ):
            raise QuotaError("QUOTA_WAKE_ARM_UNKNOWN")
        self._loaded(name)
        self._loaded(service, fragment=value["unit_prefix"] + "@.service")
        if self._host(
            ["show", service, "--property=ActiveState", "--value"]
        ).strip() not in {"activating", "active"}:
            raise QuotaError("QUOTA_WAKE_ARM_UNKNOWN")

    def cancel(self, value: dict[str, Any], arm_id: str, expected_digest: str) -> None:
        # Cancellation may be necessary after expiry; it cannot mint new work.
        value = validate(value)
        if re.fullmatch(r"[0-9a-f]{64}", arm_id) is None:
            raise QuotaError("QUOTA_WAKE_UNIT_INVALID")
        name = value["unit_prefix"] + "-" + arm_id + ".timer"
        data = self._bytes(name)
        if data is None or hashlib.sha256(data).hexdigest() != expected_digest:
            raise QuotaError("QUOTA_WAKE_CANCEL_UNKNOWN")
        self.cancel_guard()
        self._loaded(name, cancellation=True)
        key = "quota-cancel-" + arm_id
        record = {
            "schema_version": "1.0",
            "candidate_digest": value["candidate_digest"],
            "arm_id": arm_id,
            "unit_digest": expected_digest,
            "status": "RESERVED",
        }
        path = self.state / (key + ".json")
        if path.exists() or path.is_symlink():
            previous = private_json(path)
            if (
                not isinstance(previous.get("status"), str)
                or previous["status"] not in {"RESERVED", "UNKNOWN", "APPLIED"}
                or {**previous, "status": "RESERVED"} != record
            ):
                raise QuotaError("QUOTA_WAKE_CANCEL_UNKNOWN")
            active = self._host(
                ["show", name, "--property=ActiveState", "--value"],
                cancellation=True,
            ).strip()
            if active != "inactive":
                raise QuotaError("QUOTA_WAKE_CANCEL_UNKNOWN")
            workflow.StateStore(self.state).save(key, {**record, "status": "APPLIED"})
            return
        workflow.StateStore(self.state).save(key, record)
        try:
            self._host(["stop", name], cancellation=True)
            if (
                self._host(
                    ["show", name, "--property=ActiveState", "--value"],
                    cancellation=True,
                ).strip()
                != "inactive"
            ):
                raise QuotaError("QUOTA_WAKE_CANCEL_UNKNOWN")
        except Exception:
            workflow.StateStore(self.state).save(key, {**record, "status": "UNKNOWN"})
            raise
        workflow.StateStore(self.state).save(key, {**record, "status": "APPLIED"})
