#!/usr/bin/env python3
"""Owner-only phase entry point; validate the frozen policy before imports.

Copy this exact file to <state>/adapter-bin/runtime.py only after its policy
revision is reviewed. No credentials, mandate issuance or activation occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NoReturn

CONFIG_KEYS = {
    "schema_version",
    "policy_root",
    "approved_mandate_digest",
    "approved_handover_digest",
    "owner_identity",
    "started_at",
    "task_iterations",
    "follow_up_issues",
    "approved_order",
}
EPIC_CONFIG_KEYS = (CONFIG_KEYS - {"approved_handover_digest"}) | {
    "approved_plans_digest",
    "approved_final_digest",
}


def epic_config_keys(config: dict[str, Any]) -> set[str]:
    version = config.get("schema_version")
    if version not in {"1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0"}:
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    if version == "8.0" and config.get("approved_final_digest") is not None:
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    keys = EPIC_CONFIG_KEYS | (
        {"approved_launch_digest"} if version != "1.0" else set()
    )
    return (
        keys
        | (
            {"approved_planning_digest"}
            if version in {"3.0", "4.0", "5.0", "6.0", "7.0", "8.0"}
            else set()
        )
        | (
            {"approved_wait_digest"}
            if version in {"4.0", "5.0", "6.0", "7.0", "8.0"}
            else set()
        )
        | (
            {"approved_quota_digest"}
            if version in {"5.0", "6.0", "7.0", "8.0"}
            else set()
        )
        | (
            {"approved_continuation_digest"}
            if version in {"6.0", "7.0", "8.0"}
            else set()
        )
        | ({"approved_learning_digest"} if version in {"7.0", "8.0"} else set())
        | ({"approved_final_preparation_digest"} if version == "8.0" else set())
    )


def planning_enabled(config: Mapping[str, Any]) -> bool:
    return config.get("schema_version") == "3.0" or (
        config.get("schema_version") in {"4.0", "5.0", "6.0", "7.0", "8.0"}
        and config.get("approved_planning_digest") is not None
    )


def wait_profile_guard(
    state: Path, config: dict[str, Any], mandate: dict[str, Any]
) -> dict[str, Any] | None:
    """Validate the closed owner profile before importing candidate code."""
    if config.get("schema_version") not in {"4.0", "5.0", "6.0", "7.0", "8.0"}:
        return None
    path = state / "wait-profile.json"
    owner = private_json(path)
    if path.stat().st_nlink != 1:
        raise DeploymentError("UNSAFE_RUNTIME_CONFIG")
    keys = {
        "schema_version",
        "repository",
        "epic",
        "roadmap_ref",
        "policy_sha",
        "mandate_digest",
        "owner_identity",
        "limits",
    }
    if (
        set(owner) != keys
        or owner.get("schema_version") != "1.0"
        or canonical_digest(owner) != config.get("approved_wait_digest")
        or owner.get("mandate_digest") != config.get("approved_mandate_digest")
        or any(
            owner.get(key) != mandate.get(key)
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        )
    ):
        raise DeploymentError("WAIT_APPROVAL_MISMATCH")
    maxima = {
        "max_phase_seconds": 86400,
        "max_read_attempts": 11,
        "max_ci_polls": 11,
        "max_read_commands": 512,
    }
    limits = owner["limits"]
    if (
        not isinstance(limits, dict)
        or set(limits) != set(maxima)
        or any(
            type(limits[key]) is not int or not 1 <= limits[key] <= maximum
            for key, maximum in maxima.items()
        )
    ):
        raise DeploymentError("WAIT_PROFILE_INVALID")
    if "approved_planning_digest" not in config or (
        config["approved_planning_digest"] is not None
        and (
            not isinstance(config["approved_planning_digest"], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", config["approved_planning_digest"])
            is None
        )
    ):
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    return owner


QUOTA_PROFILE_KEYS = {
    "schema_version",
    "repository",
    "epic",
    "roadmap_ref",
    "policy_sha",
    "mandate_digest",
    "owner_identity",
    "wait_profile_digest",
    "limit_id",
    "source",
    "limits",
    "wakeup",
}
QUOTA_PROFILE_LIMITS = {
    "poll_seconds": 300,
    "max_polls": 512,
    "max_wait_seconds": 86400,
    "max_wakeups": 512,
}


def quota_profile_document(value: Any) -> dict[str, Any]:
    """Validate closed opt-in policy without source execution or host effects."""
    if (
        not isinstance(value, dict)
        or set(value) != QUOTA_PROFILE_KEYS
        or value["schema_version"] != "1.0"
    ):
        raise DeploymentError("QUOTA_PROFILE_INVALID")
    if (
        not isinstance(value["repository"], str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]) is None
        or type(value["epic"]) is not int
        or value["epic"] <= 0
        or not isinstance(value["roadmap_ref"], str)
        or re.fullmatch(r"roadmap/[1-9][0-9]*-[A-Za-z0-9_.-]+", value["roadmap_ref"])
        is None
        or not value["roadmap_ref"].startswith(f"roadmap/{value['epic']}-")
        or not isinstance(value["owner_identity"], str)
        or not value["owner_identity"]
        or not isinstance(value["policy_sha"], str)
        or re.fullmatch(r"[0-9a-f]{40}", value["policy_sha"]) is None
        or not isinstance(value["limit_id"], str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["limit_id"]) is None
    ):
        raise DeploymentError("QUOTA_PROFILE_INVALID")
    for key in ("mandate_digest", "wait_profile_digest"):
        if (
            not isinstance(value[key], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", value[key]) is None
        ):
            raise DeploymentError("QUOTA_PROFILE_INVALID")
    source, bounds, wake = value["source"], value["limits"], value["wakeup"]
    if (
        not isinstance(source, dict)
        or set(source)
        != {
            "executable",
            "executable_sha256",
            "version_digest",
            "request_timeout_seconds",
        }
        or not isinstance(source["executable"], str)
        or not source["executable"].startswith("/")
        or any(c in source["executable"] for c in "\r\n\x00")
        or type(source["request_timeout_seconds"]) is not int
        or not 1 <= source["request_timeout_seconds"] <= 120
        or any(
            not isinstance(source[k], str)
            or re.fullmatch(r"[0-9a-f]{64}", source[k]) is None
            for k in ("executable_sha256", "version_digest")
        )
    ):
        raise DeploymentError("QUOTA_PROFILE_INVALID")
    if (
        not isinstance(bounds, dict)
        or set(bounds) != set(QUOTA_PROFILE_LIMITS)
        or any(
            type(bounds[k]) is not int or not 1 <= bounds[k] <= maximum
            for k, maximum in QUOTA_PROFILE_LIMITS.items()
        )
        or bounds["max_wakeups"] < bounds["max_polls"]
    ):
        raise DeploymentError("QUOTA_PROFILE_INVALID")
    if (
        not isinstance(wake, dict)
        or set(wake) != {"backend", "supervisor_sha256", "host_probe_digest"}
        or wake["backend"] != "systemd_user"
        or not isinstance(wake["supervisor_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", wake["supervisor_sha256"]) is None
        or (
            wake["host_probe_digest"] is not None
            and (
                not isinstance(wake["host_probe_digest"], str)
                or re.fullmatch(r"[0-9a-f]{64}", wake["host_probe_digest"]) is None
            )
        )
    ):
        raise DeploymentError("QUOTA_PROFILE_INVALID")
    return value


def recovery_config_enabled(config: Mapping[str, Any]) -> bool:
    """Task3 opts into recovery only with a binding; task2 behavior is unchanged."""
    return config.get("schema_version") == "2.0" or (
        config.get("schema_version") == "3.0"
        and config.get("approved_recovery_digest") is not None
    )


def quota_enabled(config: Mapping[str, Any]) -> bool:
    """Config6 composes separately approved quota only through a nonnull binding."""
    return config.get("schema_version") == "5.0" or (
        config.get("schema_version") in {"6.0", "7.0", "8.0"}
        and config.get("approved_quota_digest") is not None
    )


def quota_profile_guard(
    state: Path, config: dict[str, Any], mandate: dict[str, Any]
) -> dict[str, Any] | None:
    """Bind the opt-in quota document before importing the pinned runtime."""
    if not quota_enabled(config):
        return None
    owner = quota_profile_document(private_json(state / "quota-profile.json"))
    expected = {
        "schema_version",
        "repository",
        "epic",
        "roadmap_ref",
        "policy_sha",
        "mandate_digest",
        "owner_identity",
        "wait_profile_digest",
        "limit_id",
        "source",
        "limits",
        "wakeup",
    }
    if (
        set(owner) != expected
        or owner.get("schema_version") != "1.0"
        or canonical_digest(owner) != config.get("approved_quota_digest")
        or owner.get("mandate_digest") != config.get("approved_mandate_digest")
        or owner.get("wait_profile_digest") != config.get("approved_wait_digest")
        or any(
            owner.get(k) != mandate.get(k)
            for k in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        )
    ):
        raise DeploymentError("QUOTA_APPROVAL_MISMATCH")
    if (state / "quota-profile.json").stat().st_nlink != 1:
        raise DeploymentError("UNSAFE_RUNTIME_CONFIG")
    return owner


def role_backend_document(raw: Any) -> dict[str, Any]:
    """Closed source/settings validation usable before candidate imports."""
    keys = {
        "schema_version",
        "source",
        "model",
        "original_reasoning",
        "repeat_reasoning_levels",
    }
    if (
        not isinstance(raw, dict)
        or set(raw) != keys
        or raw["schema_version"] != "1.0"
        or raw["original_reasoning"] != "low"
        or raw["repeat_reasoning_levels"] != ["medium", "high"]
        or not isinstance(raw["model"], str)
        or not 1 <= len(raw["model"]) <= 128
        or any(ord(c) < 32 or ord(c) == 127 for c in raw["model"])
    ):
        raise DeploymentError("CONTINUATION_BACKEND_INVALID")
    source = raw["source"]
    if (
        not isinstance(source, dict)
        or set(source)
        != {
            "executable",
            "executable_sha256",
            "version_digest",
            "request_timeout_seconds",
        }
        or not isinstance(source["executable"], str)
        or not source["executable"].startswith("/")
        or any(c in source["executable"] for c in "\r\n\x00")
        or type(source["request_timeout_seconds"]) is not int
        or not 1 <= source["request_timeout_seconds"] <= 120
        or any(
            not isinstance(source[k], str)
            or re.fullmatch(r"[0-9a-f]{64}", source[k]) is None
            for k in ("executable_sha256", "version_digest")
        )
    ):
        raise DeploymentError("CONTINUATION_BACKEND_INVALID")
    return raw


def role_backend_guard(
    seed: Path, owner: dict[str, Any], quota_owner: dict[str, Any] | None
) -> dict[str, Any]:
    path = seed / "role-backend.json"
    value = role_backend_document(private_json(path))
    if (
        path.stat().st_nlink != 1
        or canonical_digest(value) != "sha256:" + owner["backend_digest"]
        or (quota_owner is not None and value["source"] != quota_owner["source"])
    ):
        raise DeploymentError("CONTINUATION_BACKEND_CHANGED")
    return value


def continuation_transport_guard(raw: dict[str, Any]) -> None:
    """Prove the supported pinned native executable without inference."""
    source = role_backend_document(raw)["source"]
    path = Path(source["executable"])
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_mode & 0o022
        or not os.access(path, os.X_OK)
        or any(parent.is_symlink() for parent in path.parents)
        or hashlib.sha256(path.read_bytes()).hexdigest() != source["executable_sha256"]
    ):
        raise DeploymentError("CONTINUATION_SOURCE_CHANGED")
    version = _quota_read([str(path), "--version"]).strip()
    if (
        version != b"codex-cli 0.157.1"
        or hashlib.sha256(version).hexdigest() != source["version_digest"]
    ):
        raise DeploymentError("CONTINUATION_SOURCE_UNSUPPORTED")


def continuation_profile_guard(
    state: Path, config: dict[str, Any], mandate: dict[str, Any]
) -> dict[str, Any] | None:
    """Closed owner profile binding before pinned runtime imports."""
    version = config.get("schema_version")
    if (
        version not in {"3.0", "6.0", "7.0", "8.0"}
        or "approved_continuation_digest" not in config
    ):
        return None
    seed = managed_state(state)
    epic = (
        config
        if version in {"6.0", "7.0", "8.0"}
        else private_json(seed / "epic-runtime.json")
    )
    if epic.get("schema_version") not in {"6.0", "7.0", "8.0"} or config.get(
        "approved_continuation_digest"
    ) != epic.get("approved_continuation_digest"):
        raise DeploymentError("CONTINUATION_APPROVAL_MISMATCH")
    owner = private_json(seed / "continuation-profile.json")
    keys = {
        "schema_version",
        "repository",
        "epic",
        "roadmap_ref",
        "policy_sha",
        "mandate_digest",
        "owner_identity",
        "backend_digest",
        "limits",
    }
    if (
        set(owner) != keys
        or owner.get("schema_version") != "1.0"
        or canonical_digest(owner) != epic.get("approved_continuation_digest")
        or owner.get("mandate_digest") != epic.get("approved_mandate_digest")
        or any(
            owner.get(k) != mandate.get(k)
            for k in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        )
        or not isinstance(owner.get("backend_digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", owner["backend_digest"]) is None
        or (seed / "continuation-profile.json").stat().st_nlink != 1
    ):
        raise DeploymentError("CONTINUATION_APPROVAL_MISMATCH")
    maxima = {
        "threshold_permille": 950,
        "max_handoffs": 32,
        "max_prompt_bytes": 262144,
        "max_record_bytes": 65536,
        "max_findings": 128,
    }
    limits = owner["limits"]
    if (
        not isinstance(limits, dict)
        or set(limits) != set(maxima)
        or any(
            type(limits[k]) is not int or not 1 <= limits[k] <= maximum
            for k, maximum in maxima.items()
        )
    ):
        raise DeploymentError("CONTINUATION_PROFILE_INVALID")
    quota_owner = quota_profile_guard(seed, epic, mandate)
    role_backend_guard(seed, owner, quota_owner)
    return owner


def learning_private_json(path: Path, cap: int = 65536) -> dict[str, Any]:
    """Bound owner-only learning bootstrap bytes before JSON decoding."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(items)
        if len(result) != len(items):
            raise DeploymentError("LEARNING_STATE_INVALID")
        return result

    try:
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise DeploymentError("LEARNING_STORAGE_UNSAFE")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != owner_uid()
                or info.st_mode & 0o077
                or info.st_size > cap
            ):
                raise DeploymentError("LEARNING_STORAGE_UNSAFE")
            raw = stream.read(cap + 1)
        if len(raw) > cap:
            raise DeploymentError("LEARNING_STORAGE_UNSAFE")
        value = json.loads(raw, object_pairs_hook=pairs)
        if not isinstance(value, dict):
            raise DeploymentError("LEARNING_STATE_INVALID")
        return dict(value)
    except (OSError, ValueError):
        raise DeploymentError("LEARNING_STATE_INVALID") from None


def learning_profile_document(raw: Any) -> dict[str, Any]:
    """Closed learning owner input, usable before any candidate imports."""
    fields = {
        "schema_version",
        "repository",
        "epic",
        "roadmap_ref",
        "policy_sha",
        "mandate_digest",
        "owner_identity",
        "catalog_digest",
        "limits",
    }
    if (
        not isinstance(raw, dict)
        or set(raw) != fields
        or raw["schema_version"] != "1.0"
    ):
        raise DeploymentError("LEARNING_PROFILE_INVALID")
    if (
        not isinstance(raw["repository"], str)
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", raw["repository"]
        )
        is None
        or type(raw["epic"]) is not int
        or not 1 <= raw["epic"] <= 2147483647
        or not isinstance(raw["roadmap_ref"], str)
        or re.fullmatch(
            r"roadmap/" + str(raw["epic"]) + r"-[a-z0-9-]+", raw["roadmap_ref"]
        )
        is None
        or not isinstance(raw["owner_identity"], str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}", raw["owner_identity"]) is None
    ):
        raise DeploymentError("LEARNING_PROFILE_INVALID")
    for name, pattern in {
        "policy_sha": r"[0-9a-f]{40}",
        "catalog_digest": r"[0-9a-f]{64}",
        "mandate_digest": r"sha256:[0-9a-f]{64}",
    }.items():
        if not isinstance(raw[name], str) or re.fullmatch(pattern, raw[name]) is None:
            raise DeploymentError("LEARNING_PROFILE_INVALID")
    maxima = {
        "max_hints_per_role": 3,
        "max_hint_bytes": 4096,
        "max_record_bytes": 65536,
        "max_records": 1024,
        "max_store_bytes": 8388608,
    }
    limits = raw["limits"]
    if (
        not isinstance(limits, dict)
        or set(limits) != set(maxima)
        or any(
            type(limits[k]) is not int
            or not (0 if k == "max_hints_per_role" else 1) <= limits[k] <= maximum
            for k, maximum in maxima.items()
        )
    ):
        raise DeploymentError("LEARNING_PROFILE_INVALID")
    return dict(raw)


def learning_profile_guard(
    state: Path, config: dict[str, Any], mandate: dict[str, Any]
) -> dict[str, Any] | None:
    """Task3 inherits learning only through its exact verified managed config7."""
    direct = config.get("schema_version") in {"7.0", "8.0"}
    child = (
        config.get("schema_version") == "3.0"
        and "approved_handover_digest" in config
        and "approved_continuation_digest" in config
    )
    if not direct and not child:
        return None
    seed = managed_state(state)
    epic = config if direct else private_json(seed / "epic-runtime.json")
    if epic.get("schema_version") not in {"7.0", "8.0"}:
        return None
    if set(epic) != epic_config_keys(epic):
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    if config.get("policy_root") != epic.get("policy_root") or config.get(
        "approved_mandate_digest"
    ) != epic.get("approved_mandate_digest"):
        raise DeploymentError("LEARNING_APPROVAL_MISMATCH")
    path = seed / "learning-profile.json"
    owner = learning_profile_document(learning_private_json(path))
    if (
        path.stat().st_nlink != 1
        or canonical_digest(owner) != epic.get("approved_learning_digest")
        or owner["mandate_digest"] != epic.get("approved_mandate_digest")
        or any(
            owner[k] != mandate.get(k)
            for k in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        )
    ):
        raise DeploymentError("LEARNING_APPROVAL_MISMATCH")
    catalogs: dict[str, str] = {}
    for key, name in (
        ("workflow_catalog_digest", "agent-learning-catalog.json"),
        ("planning_catalog_digest", "agent-learning-planning-catalog.json"),
    ):
        catalog_path = Path(epic["policy_root"]) / "docs/schemas" / name
        try:
            if (
                catalog_path.is_symlink()
                or any(p.is_symlink() for p in catalog_path.parents)
                or catalog_path.stat().st_nlink != 1
                or catalog_path.stat().st_size > 65536
            ):
                raise DeploymentError("LEARNING_CATALOG_CHANGED")
        except OSError:
            raise DeploymentError("LEARNING_CATALOG_CHANGED") from None
        catalog = learning_private_json(catalog_path)
        if (
            set(catalog) != {"schema_version", "rules"}
            or catalog.get("schema_version") != "1.0"
            or not isinstance(catalog.get("rules"), list)
            or len(catalog["rules"]) > 128
        ):
            raise DeploymentError("LEARNING_CATALOG_CHANGED")
        catalogs[key] = canonical_digest(catalog).removeprefix("sha256:")
    if canonical_digest(catalogs) != "sha256:" + owner["catalog_digest"]:
        raise DeploymentError("LEARNING_CATALOG_CHANGED")
    return owner


def planning_profile_guard(
    state: Path, config: dict[str, Any], mandate: dict[str, Any]
) -> dict[str, Any] | None:
    """Stdlib profile identity/closed-shape check before candidate imports."""
    if not planning_enabled(config):
        return None
    owner = private_json(state / "planning-profile.json")
    keys = {
        "schema_version",
        "repository",
        "epic",
        "roadmap_ref",
        "policy_sha",
        "mandate_digest",
        "owner_identity",
        "tasks",
        "limits",
    }
    if (
        set(owner) != keys
        or owner.get("schema_version") != "1.0"
        or canonical_digest(owner) != config.get("approved_planning_digest")
        or owner.get("mandate_digest") != config["approved_mandate_digest"]
        or any(
            owner.get(key) != mandate[key]
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        )
    ):
        raise DeploymentError("PLANNING_APPROVAL_MISMATCH")
    maxima = {
        "max_planner_attempts": 3,
        "max_review_attempts": 3,
        "max_input_chars": 1_000_000,
        "max_output_chars": 200_000,
        "max_paths": 64,
    }
    limits = owner["limits"]
    tasks = owner["tasks"]
    if (
        not isinstance(limits, dict)
        or set(limits) != set(maxima)
        or any(
            type(limits[key]) is not int or not 1 <= limits[key] <= maximum
            for key, maximum in maxima.items()
        )
        or not isinstance(tasks, dict)
        or not 1 <= len(tasks) <= 200
    ):
        raise DeploymentError("PLANNING_PROFILE_INVALID")
    for number, item in tasks.items():
        if (
            re.fullmatch(r"[1-9][0-9]*", number) is None
            or number not in mandate["task_grants"]
            or not isinstance(item, dict)
            or set(item) != {"criteria_sections", "task_class", "demo_template"}
            or not isinstance(item["criteria_sections"], list)
            or not 1 <= len(item["criteria_sections"]) <= 16
            or any(
                not isinstance(name, str)
                or not name
                or name.strip() != name
                or len(name) > 120
                or "\n" in name
                for name in item["criteria_sections"]
            )
            or len(set(item["criteria_sections"])) != len(item["criteria_sections"])
            or not isinstance(item["task_class"], str)
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item["task_class"]) is None
            or not isinstance(item["demo_template"], dict)
        ):
            raise DeploymentError("PLANNING_PROFILE_INVALID")
    return owner


BOOTSTRAP_MANDATE_KEYS = {
    "schema_version",
    "repository",
    "epic",
    "roadmap_ref",
    "policy_sha",
    "issued_at",
    "expires_at",
    "owner_identity",
    "limits",
    "revoked",
    "operations",
    "task_grants",
    "approval",
}


class DeploymentError(Exception):
    """A bounded code, never a private path or transport diagnostic."""


QUOTA_PROBE_KEYS = {
    "schema_version",
    "kind",
    "backend",
    "host_digest",
    "source_digest",
    "supervisor_sha256",
    "service_sha256",
    "timer_template_sha256",
    "origin_exit_digest",
    "restart_digest",
    "runner_count",
    "budget_before_digest",
    "budget_after_digest",
    "observed_at",
    "probe_digest",
}


def quota_probe_document(value: Any) -> dict[str, Any]:
    """Stdlib-only proof checks; never import candidate Python during readiness."""
    if (
        not isinstance(value, dict)
        or set(value) != QUOTA_PROBE_KEYS
        or value["schema_version"] != "1.0"
        or not isinstance(value["kind"], str)
        or value["kind"] not in {"synthetic", "host"}
        or value["backend"] != "systemd_user"
        or type(value["runner_count"]) is not int
        or value["runner_count"] != 1
        or type(value["observed_at"]) is not int
        or not 0 <= value["observed_at"] <= 253402300799
    ):
        raise DeploymentError("QUOTA_WAKE_PROBE_INVALID")
    for key in QUOTA_PROBE_KEYS - {
        "schema_version",
        "kind",
        "backend",
        "runner_count",
        "observed_at",
    }:
        if (
            not isinstance(value[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
        ):
            raise DeploymentError("QUOTA_WAKE_PROBE_INVALID")
    if (
        value["origin_exit_digest"] == value["restart_digest"]
        or value["budget_before_digest"] != value["budget_after_digest"]
        or value["probe_digest"]
        != canonical_digest(
            {key: item for key, item in value.items() if key != "probe_digest"}
        ).removeprefix("sha256:")
    ):
        raise DeploymentError("QUOTA_WAKE_PROBE_INVALID")
    return dict(value)


def _quota_read(command: list[str]) -> bytes:
    """Fixed trusted no-input native commands, capped before capture can grow."""
    if os.name != "posix":
        raise DeploymentError("QUOTA_SOURCE_UNSUPPORTED")
    try:
        child = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        raise DeploymentError("QUOTA_READINESS_SOURCE_UNKNOWN") from None
    assert child.stdout is not None
    deadline, data = time.monotonic() + 5, bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    continue
                chunk = os.read(child.stdout.fileno(), min(8192, 65537 - len(data)))
                if not chunk:
                    if child.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
                        raise DeploymentError("QUOTA_READINESS_SOURCE_UNKNOWN")
                    return bytes(data)
                data.extend(chunk)
                if len(data) > 65536:
                    raise DeploymentError("QUOTA_READINESS_OUTPUT_LIMIT")
        raise DeploymentError("QUOTA_READINESS_TIMEOUT")
    except (OSError, subprocess.TimeoutExpired):
        raise DeploymentError("QUOTA_READINESS_SOURCE_UNKNOWN") from None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()


def _quota_namespace_probe(state: Path, executable: str) -> None:
    """Execute only fixed stdlib write attempts in temporary synthetic files."""
    binary = shutil.which("bwrap") if sys.platform == "linux" else None
    if binary is None:
        raise DeploymentError("QUOTA_NAMESPACE_UNSUPPORTED")
    program = """import errno,json,sys
from pathlib import Path
root=Path(sys.argv[1])
def write(path):
    try:
        path.write_text('probe')
        return True
    except OSError as error:
        if error.errno not in {errno.EROFS,errno.EACCES,errno.EPERM}:
            raise
        return False
print(json.dumps({'allowed':write(root/'allowed'),
    'protected':write(root/'protected'),
    'scratch':write(root/'scratch'/'marker')}))
"""
    try:
        with tempfile.TemporaryDirectory(prefix="quota-namespace-", dir=state) as name:
            directory = Path(name)
            scratch = directory / "scratch"
            scratch.mkdir(mode=0o700)
            for role in ("executor", "controller"):
                for filename in ("allowed", "protected"):
                    (directory / filename).write_bytes(b"synthetic")
                    (directory / filename).chmod(0o600)
                argv = [
                    binary,
                    "--die-with-parent",
                    "--unshare-pid",
                    "--ro-bind",
                    "/",
                    "/",
                    "--proc",
                    "/proc",
                    "--dev",
                    "/dev",
                    "--bind",
                    str(scratch),
                    str(scratch),
                ]
                if role == "executor":
                    argv += [
                        "--bind",
                        str(directory / "allowed"),
                        str(directory / "allowed"),
                    ]
                argv += ["--", executable, "-I", "-c", program, str(directory)]
                result = json.loads(_quota_read(argv))
                if (
                    result
                    != {
                        "allowed": role == "executor",
                        "protected": False,
                        "scratch": True,
                    }
                    or (directory / "protected").read_bytes() != b"synthetic"
                ):
                    raise DeploymentError("QUOTA_NAMESPACE_UNSUPPORTED")
    except (OSError, ValueError, DeploymentError):
        raise DeploymentError("QUOTA_NAMESPACE_UNSUPPORTED") from None


def quota_deployment_guard(
    state: Path,
    root: Path,
    config: dict[str, Any],
    mandate: dict[str, Any],
    command: tuple[str, ...] | None,
) -> None:
    """Readiness reads only pinned bytes/native metadata, never candidate Python."""
    if not quota_enabled(config):
        return
    owner = quota_profile_guard(state, config, mandate)
    assert owner is not None
    wakeup = owner.get("wakeup")
    if not isinstance(wakeup, dict) or wakeup.get("host_probe_digest") is None:
        raise DeploymentError("QUOTA_WAKE_PROBE_REQUIRED")
    proof = quota_probe_document(private_json(state / "quota-host-probe.json"))
    if proof["kind"] != "host":
        raise DeploymentError("QUOTA_WAKE_PROBE_SYNTHETIC")
    if proof["probe_digest"] != wakeup["host_probe_digest"]:
        raise DeploymentError("QUOTA_WAKE_PROBE_BINDING_CHANGED")
    if (
        command is None
        or len(command) != 7
        or command[2:]
        != (
            "epic_resume",
            "--state-dir",
            str(state),
            "--repository-root",
            str(root.resolve()),
        )
    ):
        raise DeploymentError("QUOTA_WAKE_BINDING_CHANGED")
    supervisor_hash = hashlib.sha256(Path(command[1]).read_bytes()).hexdigest()
    if supervisor_hash != wakeup.get("supervisor_sha256"):
        raise DeploymentError("QUOTA_WAKE_BINDING_CHANGED")
    source = owner.get("source")
    if (
        not isinstance(source, dict)
        or set(source)
        != {
            "executable",
            "executable_sha256",
            "version_digest",
            "request_timeout_seconds",
        }
        or not isinstance(source["executable"], str)
    ):
        raise DeploymentError("QUOTA_SOURCE_INVALID")
    path = Path(source["executable"])
    info = path.lstat()
    if (
        not path.is_absolute()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_mode & 0o022
        or any(p.is_symlink() for p in path.parents)
        or hashlib.sha256(path.read_bytes()).hexdigest() != source["executable_sha256"]
    ):
        raise DeploymentError("QUOTA_SOURCE_CHANGED")
    version = _quota_read([str(path), "--version"]).strip()
    if (
        version != b"codex-cli 0.157.1"
        or hashlib.sha256(version).hexdigest() != source["version_digest"]
    ):
        raise DeploymentError("QUOTA_ROLE_SOURCE_UNSUPPORTED")
    value = private_json(state / "quota-wakeup-candidate.json")
    template = (
        "[Unit]\nDescription=Privacy Gateway finite quota wake\n[Timer]\n"
        "OnCalendar=@{at}\nPersistent=true\nAccuracySec=1s\nUnit={service}\n"
    )
    argv = [*command[:2], "quota_wake", *command[3:]]
    if any(any(c in arg for c in "\x00\r\n%") for arg in argv):
        raise DeploymentError("QUOTA_WAKE_COMMAND_INVALID")
    quoted = ['"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"' for arg in argv]
    service = (
        "[Unit]\nDescription=Privacy Gateway bounded quota supervisor\n"
        "[Service]\nType=oneshot\nExecStart="
        + " ".join(quoted)
        + ' "--wake-arm-id" "%i"\n'
    ).encode()
    bindings = {
        "schema_version": "1.0",
        "repository": mandate["repository"],
        "epic": mandate["epic"],
        "policy_sha": mandate["policy_sha"],
        "state_digest": canonical_digest(str(state.resolve())).removeprefix("sha256:"),
        "runtime_config_digest": launch_config_digest(
            config,
            private_json(state / "runtime-adapter.json"),
        ).removeprefix("sha256:"),
        "quota_profile_digest": canonical_digest(owner).removeprefix("sha256:"),
        "source_digest": canonical_digest(source).removeprefix("sha256:"),
        "supervisor_sha256": supervisor_hash,
        "host_probe_digest": proof["probe_digest"],
        "issued_at": config["started_at"],
        "expires_at": min(
            mandate["expires_at"],
            config["started_at"] + mandate["limits"]["max_duration_seconds"],
        ),
        "service_sha256": hashlib.sha256(service).hexdigest(),
        "timer_template_sha256": hashlib.sha256(template.encode()).hexdigest(),
    }
    bindings["unit_prefix"] = (
        "pgw-quota-"
        + canonical_digest(
            {key: bindings[key] for key in ("state_digest", "runtime_config_digest")}
        ).removeprefix("sha256:")[:32]
    )
    bindings["candidate_digest"] = canonical_digest(bindings).removeprefix("sha256:")
    if (
        value != bindings
        or not bindings["issued_at"] <= int(time.time()) < bindings["expires_at"]
    ):
        raise DeploymentError("QUOTA_WAKE_BINDING_CHANGED")
    units = secure_directory(Path.home() / ".config" / "systemd" / "user", root)
    prefix = ["/usr/bin/systemctl", "--user", "show"]
    paths = _quota_read([*prefix, "--property=UnitPath", "--value"]).decode().split()
    if str(units) not in paths:
        raise DeploymentError("QUOTA_WAKE_UNIT_PATH_UNSUPPORTED")
    name = str(bindings["unit_prefix"]) + "@.service"
    unit = units / name
    unit_info = unit.lstat()
    if (
        not stat.S_ISREG(unit_info.st_mode)
        or unit_info.st_nlink != 1
        or unit_info.st_uid != units.stat().st_uid
        or unit_info.st_mode & 0o077
        or unit_info.st_size > 65536
        or unit.read_bytes() != service
        or private_json(state / "quota-install.json")
        != {"candidate": value, "status": "APPLIED"}
    ):
        raise DeploymentError("QUOTA_WAKE_NOT_INSTALLED")
    for key, expected in {
        "FragmentPath": str(unit),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "LoadState": "loaded",
    }.items():
        if (
            _quota_read([*prefix, name, "--property=" + key, "--value"])
            .decode()
            .strip()
            != expected
        ):
            raise DeploymentError("QUOTA_WAKE_UNIT_BINDING_CHANGED")
    manager = _quota_read([*prefix, "--property=Version", "--value"]).strip()
    if not manager or len(manager) > 128 or not manager.isascii():
        raise DeploymentError("QUOTA_WAKE_HOST_UNKNOWN")
    host = canonical_digest(
        {
            "backend": "systemd_user",
            "uid": units.stat().st_uid,
            "units_digest": canonical_digest(str(units)).removeprefix("sha256:"),
            "manager_version_digest": hashlib.sha256(manager).hexdigest(),
        }
    ).removeprefix("sha256:")
    if any(
        proof[key] != expected
        for key, expected in {
            "host_digest": host,
            "source_digest": bindings["source_digest"],
            "supervisor_sha256": supervisor_hash,
            "service_sha256": bindings["service_sha256"],
            "timer_template_sha256": bindings["timer_template_sha256"],
        }.items()
    ):
        raise DeploymentError("QUOTA_WAKE_PROBE_BINDING_CHANGED")
    _quota_namespace_probe(state, command[0])


LAUNCH_KEYS = {
    "schema_version",
    "repository",
    "epic",
    "roadmap_ref",
    "policy_sha",
    "installation_digest",
    "runtime_config_digest",
    "mandate_digest",
    "owner_identity",
}


def canonical_digest(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )


def launch_config_digest(config: dict[str, Any], adapter: dict[str, Any]) -> str:
    return canonical_digest(
        {
            "epic_runtime": {
                key: value
                for key, value in config.items()
                if key != "approved_launch_digest"
            },
            "runtime_adapter": adapter,
        }
    )


def managed_state(state: Path) -> Path:
    if state.parent.name == "tasks":
        return state.parent.parent
    return state


def managed_launch_guard(
    state: Path, root: Path, *, read_only: bool = False, allow_initial: bool = False
) -> None:
    """Stdlib guard before imports and effects, including direct child entry."""
    seed = secure_directory(managed_state(state), root)
    present = any(
        (seed / name).exists() or (seed / name).is_symlink()
        for name in ("launch.json", "launch-control.json")
    )
    epic_path = seed / "epic-runtime.json"
    config = private_json(epic_path) if epic_path.exists() else None
    managed = config is not None and config.get("schema_version") in {
        "2.0",
        "3.0",
        "4.0",
        "5.0",
        "6.0",
        "7.0",
        "8.0",
    }
    if not present and not managed:
        return
    if config is None or set(config) != epic_config_keys(config) or not managed:
        raise DeploymentError("LAUNCH_PROFILE_REQUIRED")
    value = private_json(seed / "launch.json")
    if set(value) != LAUNCH_KEYS or value.get("schema_version") != "1.0":
        raise DeploymentError("LAUNCH_CANDIDATE_INVALID")
    if (
        type(value["epic"]) is not int
        or value["epic"] <= 0
        or not isinstance(value["repository"], str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]) is None
        or not isinstance(value["roadmap_ref"], str)
        or not value["roadmap_ref"].startswith(f"roadmap/{value['epic']}-")
        or not isinstance(value["owner_identity"], str)
        or not value["owner_identity"]
        or not isinstance(value["policy_sha"], str)
        or re.fullmatch(r"[0-9a-f]{40}", value["policy_sha"]) is None
        or any(
            not isinstance(value[key], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", value[key]) is None
            for key in (
                "installation_digest",
                "runtime_config_digest",
                "mandate_digest",
            )
        )
    ):
        raise DeploymentError("LAUNCH_CANDIDATE_INVALID")
    approved = config["approved_launch_digest"]
    if canonical_digest(value) != approved:
        raise DeploymentError("LAUNCH_APPROVAL_MISMATCH")
    adapter = private_json(seed / "runtime-adapter.json")
    if launch_config_digest(config, adapter) != value["runtime_config_digest"]:
        raise DeploymentError("LAUNCH_CONFIG_CHANGED")
    mandate = private_json(seed / "mandate.json")
    planning_profile_guard(seed, config, mandate)
    wait_profile_guard(seed, config, mandate)
    quota_profile_guard(seed, config, mandate)
    continuation_profile_guard(seed, config, mandate)
    learning_profile_guard(seed, config, mandate)
    if (
        any(
            value[key] != mandate.get(key)
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        )
        or value["mandate_digest"] != config["approved_mandate_digest"]
    ):
        raise DeploymentError("LAUNCH_IDENTITY_MISMATCH")
    if not read_only:
        bootstrap_authority(config, mandate, epic=True)
    if state != seed:
        child = private_json(state / "mandate.json")
        child_config = private_json(state / "runtime-task.json")
        payload = dict(child)
        approval = child.get("approval")
        if not isinstance(approval, dict):
            raise DeploymentError("LAUNCH_IDENTITY_MISMATCH")
        payload["approval"] = {
            key: item for key, item in approval.items() if key != "mandate_digest"
        }
        if (
            canonical_digest(payload) != value["mandate_digest"]
            or child_config.get("approved_mandate_digest") != value["mandate_digest"]
        ):
            raise DeploymentError("LAUNCH_IDENTITY_MISMATCH")
    policy = Path(config["policy_root"])
    secure_directory(policy, root)
    manifest = private_json(policy.parent / "candidate.json")
    if canonical_digest(manifest) != value["installation_digest"]:
        raise DeploymentError("LAUNCH_INSTALLATION_CHANGED")
    path = seed / "launch-control.json"
    if not path.exists() and not path.is_symlink() and allow_initial:
        return
    control = private_json(path)
    if (
        set(control) != {"schema_version", "launch_digest", "state", "generation"}
        or control["schema_version"] != "1.0"
        or control["launch_digest"] != approved
        or control["state"] not in {"RUNNING", "PAUSED", "STOPPED"}
        or type(control["generation"]) is not int
        or control["generation"] < 0
    ):
        raise DeploymentError("LAUNCH_CONTROL_INVALID")
    if not read_only and control["state"] != "RUNNING":
        raise DeploymentError("OWNER_" + control["state"])


def bootstrap_authority(
    config: dict[str, Any], mandate: dict[str, Any], *, epic: bool
) -> None:
    """Already-trusted stdlib guard; never import candidate policy to approve it."""
    if set(mandate) != BOOTSTRAP_MANDATE_KEYS:
        raise DeploymentError("MANDATE_INVALID")
    if mandate["schema_version"] not in ({"3.0"} if epic else {"2.0", "3.0"}):
        raise DeploymentError("MANDATE_SCHEMA_UNSUPPORTED")
    approval, limits = mandate["approval"], mandate["limits"]
    if (
        not isinstance(approval, dict)
        or set(approval) != {"mandate_digest", "approved_by", "approved_at"}
        or not isinstance(limits, dict)
        or set(limits)
        != {"max_duration_seconds", "max_task_iterations", "max_follow_up_issues"}
    ):
        raise DeploymentError("MANDATE_INVALID")
    payload = dict(mandate)
    payload["approval"] = {
        key: value for key, value in approval.items() if key != "mandate_digest"
    }
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    approved = config["approved_mandate_digest"]
    if approved != digest or approval["mandate_digest"] != digest:
        raise DeploymentError("MANDATE_APPROVAL_MISMATCH")
    integers = (
        mandate["issued_at"],
        mandate["expires_at"],
        approval["approved_at"],
        *limits.values(),
        config["started_at"],
        config["task_iterations"],
        config["follow_up_issues"],
    )
    if (
        any(type(value) is not int for value in integers)
        or type(mandate["revoked"]) is not bool
        or not isinstance(mandate["owner_identity"], str)
        or not mandate["owner_identity"]
    ):
        raise DeploymentError("MANDATE_INVALID")
    issued, expires, approved_at = (
        mandate["issued_at"],
        mandate["expires_at"],
        approval["approved_at"],
    )
    started, iterations, followups = (
        config["started_at"],
        config["task_iterations"],
        config["follow_up_issues"],
    )
    duration, maximum, follows = (
        limits["max_duration_seconds"],
        limits["max_task_iterations"],
        limits["max_follow_up_issues"],
    )
    if (
        issued < 0
        or expires <= issued
        or not issued <= approved_at <= expires
        or duration <= 0
        or maximum <= 0
        or follows < 0
        or started < issued
        or iterations < 0
        or followups < 0
    ):
        raise DeploymentError("MANDATE_INVALID")
    if mandate["owner_identity"] != config["owner_identity"]:
        raise DeploymentError("MANDATE_OWNER_MISMATCH")
    if approval["approved_by"] != config["owner_identity"]:
        raise DeploymentError("MANDATE_APPROVAL_IDENTITY_MISMATCH")
    if mandate["revoked"]:
        raise DeploymentError("MANDATE_REVOKED")
    now = int(time.time())
    if now < issued or now < approved_at:
        raise DeploymentError("MANDATE_NOT_YET_VALID")
    if now >= expires:
        raise DeploymentError("MANDATE_EXPIRED")
    if now - started >= duration:
        raise DeploymentError("MANDATE_DURATION_LIMIT")
    if iterations > maximum or (iterations == maximum and not epic):
        raise DeploymentError("MANDATE_TASK_LIMIT")
    if followups > follows:
        raise DeploymentError("MANDATE_FOLLOW_UP_LIMIT")
    if (
        not isinstance(mandate["policy_sha"], str)
        or re.fullmatch(r"[0-9a-f]{40}", mandate["policy_sha"]) is None
    ):
        raise DeploymentError("POLICY_SHA_INVALID")


def owner_uid() -> int:
    if os.name == "nt":
        raise DeploymentError("RUNTIME_ADAPTER_UNSUPPORTED")
    return int(getattr(os, "getuid", lambda: -1)())


def private_json(path: Path) -> dict[str, Any]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != owner_uid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise DeploymentError("UNSAFE_RUNTIME_CONFIG")
            value = json.load(stream)
    except (OSError, ValueError) as error:
        raise DeploymentError("RUNTIME_CONFIG_READ_FAILED") from error
    if not isinstance(value, dict):
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    return value


def secure_directory(path: Path, root: Path) -> Path:
    if os.name == "nt":
        raise DeploymentError("RUNTIME_ADAPTER_UNSUPPORTED")
    if path.is_symlink() or not path.is_absolute():
        raise DeploymentError("UNSAFE_RUNTIME_CONFIG")
    directory = path.resolve()
    if directory.is_relative_to(root.resolve()):
        raise DeploymentError("UNSAFE_RUNTIME_CONFIG")
    system = Path(directory.anchor).stat().st_uid
    owner = owner_uid()
    for ancestor in (directory, *directory.parents):
        info = ancestor.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {system, owner}
            or (
                stat.S_IMODE(info.st_mode) & 0o022
                and not (info.st_uid == system and info.st_mode & stat.S_ISVTX)
            )
            or (ancestor / ".git").is_file()
            or (ancestor / ".git" / "HEAD").is_file()
        ):
            raise DeploymentError("UNSAFE_RUNTIME_CONFIG")
    if (
        directory.stat().st_uid != owner
        or stat.S_IMODE(directory.stat().st_mode) & 0o077
    ):
        raise DeploymentError("UNSAFE_RUNTIME_CONFIG")
    return directory


def _legacy_policy_batch_sources(
    root: Path, sha: str, paths: list[str]
) -> dict[str, bytes] | None:
    """Fresh optional transport optimization; legacy checks remain authoritative.

    Eligibility bounds never reject a policy: unsupported or ambiguous Git
    results return to the original per-file show at its original check point.
    No positive proof or expected bytes survive this verify_policy invocation.
    """
    if not 1 <= len(paths) <= 4096 or any(
        re.fullmatch(r"(?:tools|docs/schemas)/[A-Za-z0-9_./-]+", path) is None
        or any(part in {".", ".."} for part in path.split("/"))
        for path in paths
    ):
        return None
    try:
        inventory = subprocess.run(
            ["git", "ls-tree", "-r", "-z", sha, "tools", "docs/schemas"],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if (
            inventory.returncode != 0
            or len(inventory.stdout) > 1024 * 1024
            or len(inventory.stderr) > 65536
            or not inventory.stdout.endswith(b"\0")
        ):
            return None
        records = inventory.stdout[:-1].split(b"\0")
        if len(records) != len(paths):
            return None
        object_ids: list[bytes] = []
        for record, relative in zip(records, paths, strict=True):
            header, separator, raw_path = record.partition(b"\t")
            match = re.fullmatch(rb"[0-7]{6} blob ([0-9a-f]{40})", header)
            if not separator or match is None or raw_path != relative.encode("ascii"):
                return None
            object_ids.append(match[1])
        batch = subprocess.run(
            ["git", "cat-file", "--batch"],
            cwd=root,
            input=b"\n".join(object_ids) + b"\n",
            capture_output=True,
            check=False,
            timeout=30,
        )
        if (
            batch.returncode != 0
            or len(batch.stdout) > 64 * 1024 * 1024
            or len(batch.stderr) > 65536
        ):
            return None
        position = 0
        sources: dict[str, bytes] = {}
        for relative, expected_id in zip(paths, object_ids, strict=True):
            end = batch.stdout.find(b"\n", position, position + 96)
            if end == -1:
                return None
            header_match = re.fullmatch(
                rb"([0-9a-f]{40}) blob (0|[1-9][0-9]*)",
                batch.stdout[position:end],
            )
            if header_match is None or header_match[1] != expected_id:
                return None
            size = int(header_match[2])
            if size > 8 * 1024 * 1024:
                return None
            start = end + 1
            finish = start + size
            if batch.stdout[finish : finish + 1] != b"\n":
                return None
            contents = batch.stdout[start:finish]
            actual_id = (
                hashlib.sha1(
                    b"blob " + str(len(contents)).encode("ascii") + b"\0" + contents
                )
                .hexdigest()
                .encode("ascii")
            )
            if actual_id != expected_id:
                return None
            sources[relative] = contents
            position = finish + 1
        return sources if position == len(batch.stdout) else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def verify_policy(policy: Path, root: Path, sha: str, *, entry: Path) -> None:
    """Every module/schema is byte-identical to the pinned Git policy object."""
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise DeploymentError("POLICY_SHA_INVALID")
    private = secure_directory(policy, root)

    def git(*args: str) -> bytes:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, check=False, timeout=30
        )
        if result.returncode != 0:
            raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
        return result.stdout

    expected_entry = git("show", f"{sha}:tools/agent_epic_runtime_cli.py")
    if entry.read_bytes() != expected_entry:
        raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
    paths = (
        git("ls-tree", "-r", "--name-only", sha, "tools", "docs/schemas")
        .decode()
        .splitlines()
    )
    if (
        "tools/agent_epic_runtime.py" not in paths
        or "tools/codex_adapter.py" not in paths
    ):
        raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
    batch_sources = _legacy_policy_batch_sources(root, sha, paths)
    for relative in paths:
        target = private / relative
        for parent in (target, *target.parents):
            if parent == private:
                break
            if parent.is_symlink():
                raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
        info = target.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or target.read_bytes()
            != (
                batch_sources[relative]
                if batch_sources is not None
                else git("show", f"{sha}:{relative}")
            )
        ):
            raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
    for item in private.rglob("*"):
        info = item.lstat()
        if (
            item.is_symlink()
            or info.st_uid != owner_uid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or (item.is_file() and item.relative_to(private).as_posix() not in paths)
        ):
            raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")


def verify_policy_config8_batch(
    policy: Path, root: Path, sha: str, *, entry: Path
) -> None:
    """Fresh full policy proof for the already admitted config8 SourceReader."""
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise DeploymentError("POLICY_SHA_INVALID")
    private = secure_directory(policy, root)

    def git(
        *args: str, input_bytes: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        if input_bytes is None:
            result = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, check=False, timeout=30
            )
        else:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=30,
                input=input_bytes,
            )
        if result.returncode != 0:
            raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
        return result

    entry_result = git("show", f"{sha}:tools/agent_epic_runtime_cli.py")
    if entry.read_bytes() != entry_result.stdout:
        raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
    if len(entry_result.stdout) > 8 * 1024 * 1024 or len(entry_result.stderr) > 65536:
        raise DeploymentError("POLICY_REVISION_UNAVAILABLE")

    inventory = git("ls-tree", "-r", "--name-only", sha, "tools", "docs/schemas")
    raw_paths = inventory.stdout
    if (
        len(raw_paths) > 1024 * 1024
        or len(inventory.stderr) > 65536
        or not raw_paths
        or not raw_paths.endswith(b"\n")
    ):
        raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
    records = raw_paths[:-1].split(b"\n")
    if len(records) > 4096:
        raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
    paths: list[str] = []
    folded: set[str] = set()
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
    }
    for raw_path in records:
        if not 1 <= len(raw_path) <= 1024:
            raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
        try:
            relative = raw_path.decode("ascii")
        except UnicodeDecodeError:
            raise DeploymentError("POLICY_REVISION_UNAVAILABLE") from None
        parts = relative.split("/")
        if (
            not relative.startswith(("tools/", "docs/schemas/"))
            or any(
                re.fullmatch(r"[A-Za-z0-9_.-]+", part) is None
                or part in {".", ".."}
                or part.endswith(".")
                or part.split(".", 1)[0].upper() in reserved
                for part in parts
            )
            or relative.casefold() in folded
        ):
            raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
        folded.add(relative.casefold())
        paths.append(relative)
    if (
        "tools/agent_epic_runtime.py" not in paths
        or "tools/codex_adapter.py" not in paths
    ):
        raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
    requests = "".join(f"{sha}:{relative}\n" for relative in paths).encode("ascii")
    if len(requests) > 1024 * 1024:
        raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
    batch = git("cat-file", "--batch", input_bytes=requests)
    if len(batch.stdout) > 64 * 1024 * 1024 or len(batch.stderr) > 65536:
        raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
    position = 0
    sources: list[bytes] = []
    for _ in paths:
        end = batch.stdout.find(b"\n", position, position + 96)
        if end == -1:
            raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
        header = re.fullmatch(
            rb"([0-9a-f]{40}) blob (0|[1-9][0-9]*)", batch.stdout[position:end]
        )
        if header is None:
            raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
        size = int(header[2])
        if size > 8 * 1024 * 1024:
            raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
        start = end + 1
        finish = start + size
        if batch.stdout[finish : finish + 1] != b"\n":
            raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
        contents = batch.stdout[start:finish]
        object_id = hashlib.sha1(
            b"blob " + str(len(contents)).encode("ascii") + b"\0" + contents
        ).hexdigest()
        if header[1] != object_id.encode("ascii"):
            raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
        sources.append(contents)
        position = finish + 1
    if position != len(batch.stdout):
        raise DeploymentError("POLICY_REVISION_UNAVAILABLE")
    for relative, expected in zip(paths, sources, strict=True):
        target = private / relative
        for parent in (target, *target.parents):
            if parent == private:
                break
            if parent.is_symlink():
                raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
        info = target.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or target.read_bytes() != expected
        ):
            raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
    for item in private.rglob("*"):
        info = item.lstat()
        if (
            item.is_symlink()
            or info.st_uid != owner_uid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or (item.is_file() and item.relative_to(private).as_posix() not in paths)
        ):
            raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")


def final_preseed_guard(state: Path, root: Path) -> None:
    """Admission only: reread the original seed before effect-capable imports.

    Existing read-only reconciliation bypasses this restart-only check through
    its original read_only route, never through a durable bypass artifact.
    """
    path = managed_state(state) / "epic-runtime.json"
    if not path.exists() and not path.is_symlink():
        return
    config = private_json(path)
    if config.get("schema_version") != "8.0":
        return
    if set(config) != epic_config_keys(config):
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    validate_final_preseed(state, root)


def build_runtime(state: Path, root: Path, *, read_only: bool = False) -> Any:
    state = secure_directory(state, root)
    if not read_only:
        final_preseed_guard(state, root)
    config = private_json(state / "runtime-task.json")
    recovery_profile = config.get("schema_version") in {"2.0", "3.0"}
    if set(config) != CONFIG_KEYS | (
        {"approved_recovery_digest"} if recovery_profile else set()
    ) | (
        {"approved_continuation_digest"}
        if config.get("schema_version") == "3.0"
        else set()
    ) or config["schema_version"] not in {"1.0", "2.0", "3.0"}:
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    if (
        recovery_profile
        and config["approved_recovery_digest"] is not None
        and (
            not isinstance(config["approved_recovery_digest"], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", config["approved_recovery_digest"])
            is None
        )
    ):
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    if not recovery_profile and (state / "bootstrap-recovery.json").exists():
        raise DeploymentError("RECOVERY_PROFILE_REQUIRED")
    mandate = private_json(state / "mandate.json")
    bootstrap_authority(config, mandate, epic=False)
    continuation_profile_guard(state, config, mandate)
    learning_profile_guard(state, config, mandate)
    managed_launch_guard(state, root, read_only=read_only)
    policy = Path(config["policy_root"])
    verify_policy(policy, root, mandate.get("policy_sha", ""), entry=Path(__file__))
    existing = sys.modules.get("tools")
    if existing is not None and not Path(
        existing.__file__ or ""
    ).resolve().is_relative_to(policy.resolve()):
        raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(policy))
    from tools import agent_coordinator_handover as handover
    from tools import agent_epic_delivery as delivery
    from tools import agent_epic_loop as loop
    from tools.agent_epic_bootstrap import BootstrapTask
    from tools.agent_epic_runtime import TaskPhaseRuntime
    from tools.agent_epic_workflow import ReviewedWorkflow, codex_role_adapter

    def authority() -> tuple[dict[str, Any], str, delivery.MandateContext]:
        managed_launch_guard(state, root, read_only=read_only)
        current = private_json(state / "mandate.json")
        if current.get("policy_sha") != mandate.get("policy_sha"):
            raise DeploymentError("POLICY_PROVENANCE_MISMATCH")
        context = delivery.MandateContext(
            int(time.time()),
            config["owner_identity"],
            config["started_at"],
            config["task_iterations"],
            config["follow_up_issues"],
        )
        return current, config["approved_mandate_digest"], context

    current, approved, context = authority()
    if delivery.mandate_digest(current) != approved:
        raise DeploymentError("MANDATE_APPROVAL_MISMATCH")
    code = delivery.mandate_lifecycle_code(current, context)
    if code is not None:
        raise DeploymentError(code)
    saved = loop.CheckpointStore(state, root).load()
    task_handover = private_json(state / "handover.json")
    if handover.handover_digest(task_handover) != config["approved_handover_digest"]:
        raise DeploymentError("APPROVAL_MISMATCH")
    if saved is None:
        raise DeploymentError("CHECKPOINT_INVALID")
    pinned = current.get("schema_version") == "3.0"
    if pinned != (saved["schema_version"] == "3.0") or (
        pinned
        and (
            saved["policy_sha"] != current["policy_sha"]
            or task_handover.get("schema_version") != "2.0"
            or task_handover.get("trusted_policy")
            != {"source": "pinned_policy_sha", "policy_sha": current["policy_sha"]}
        )
    ):
        raise DeploymentError("POLICY_PROVENANCE_MISMATCH")
    if any(
        task_handover.get(key) != saved[key]
        for key in ("repository", "epic", "task", "base_ref", "base_sha", "head_ref")
    ):
        raise DeploymentError("HANDOVER_IDENTITY_MISMATCH")
    from tools.agent_epic_wait_scope import Scope

    wait_scope = Scope.inherited(state, root, read_only=read_only)
    role_options: dict[str, Any] = {}
    if config.get("schema_version") == "3.0":
        seed = managed_state(state)
        epic_config = private_json(seed / "epic-runtime.json")
        role_options["continuation_state"] = seed
        if quota_enabled(epic_config):
            role_options.update(quota_state=seed, wait_scope=wait_scope)
    reviewed = ReviewedWorkflow(
        root=root,
        directory=state,
        value=task_handover,
        approved_digest=config["approved_handover_digest"],
        adapter=codex_role_adapter(
            policy_root=policy,
            root=root,
            policy_sha=current["policy_sha"],
            **role_options,
        ),
    )
    engine = TaskPhaseRuntime(
        store=loop.CheckpointStore(state, root),
        authority=authority,
        artifacts=reviewed.artifacts,
        demo_plan=lambda: private_json(state / "demo-plan.json"),
        demo_completion=lambda: private_json(state / "demo-completion.json"),
        approved_order=tuple(config["approved_order"]),
        wait_scope=wait_scope,
    )
    engine.bootstrap = BootstrapTask(engine, reviewed, authority)
    from tools import agent_epic_recovery

    if recovery_config_enabled(config) or agent_epic_recovery.present(state):
        from tools.agent_epic_recovery import BootstrapRecovery

        def approved_recovery() -> str | None:
            fresh = private_json(state / "runtime-task.json")
            if fresh != config:
                raise DeploymentError("RECOVERY_APPROVAL_CHANGED")
            return config["approved_recovery_digest"]  # type: ignore[no-any-return]

        engine.recovery = BootstrapRecovery(state, root, authority, approved_recovery)
    return engine


def _retained_wait_before_quota(state: Path, root: Path) -> bool:
    """Fixed wake STOP admission before cleanup, probes or ordinary constructors."""
    state = secure_directory(state, root)
    config = private_json(state / "epic-runtime.json")
    if config.get("schema_version") != "8.0":
        return False
    if set(config) != epic_config_keys(config):
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    mandate = private_json(state / "mandate.json")
    bootstrap_authority(config, mandate, epic=True)
    managed_launch_guard(state, root)
    policy = Path(config["policy_root"])
    verify_policy(policy, root, mandate.get("policy_sha", ""), entry=Path(__file__))
    existing = sys.modules.get("tools")
    if existing is not None and not Path(
        existing.__file__ or ""
    ).resolve().is_relative_to(policy.resolve()):
        raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(policy))
    from tools.agent_owner_final_admission import _retained_sources

    # No frame/readers are constructed and no CI/transport budget is admitted.
    return _retained_sources(state, root) is not None


def build_session(state: Path, root: Path, *, wake: bool = False) -> Any:
    state = secure_directory(state, root)
    final_preseed_guard(state, root)
    if (state / "bootstrap-recovery.json").exists() or (
        (state / "runtime-task.json").exists()
        and recovery_config_enabled(private_json(state / "runtime-task.json"))
    ):
        raise DeploymentError("RECOVERY_ONE_TASK_REQUIRED")
    config = private_json(state / "epic-runtime.json")
    if set(config) != epic_config_keys(config):
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    if (
        any(
            type(config[key]) is not int or config[key] < 0
            for key in ("started_at", "task_iterations", "follow_up_issues")
        )
        or not isinstance(config["approved_order"], list)
        or any(type(task) is not int or task <= 0 for task in config["approved_order"])
    ):
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    mandate = private_json(state / "mandate.json")
    bootstrap_authority(config, mandate, epic=True)
    planning_profile_guard(state, config, mandate)
    wait_profile_guard(state, config, mandate)
    quota_profile_guard(state, config, mandate)
    continuation_profile_guard(state, config, mandate)
    learning_profile_guard(state, config, mandate)
    managed_launch_guard(state, root)
    policy = Path(config["policy_root"])
    verify_policy(policy, root, mandate.get("policy_sha", ""), entry=Path(__file__))
    existing = sys.modules.get("tools")
    if existing is not None and not Path(
        existing.__file__ or ""
    ).resolve().is_relative_to(policy.resolve()):
        raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(policy))
    from tools.agent_epic_session_runtime import SessionRuntime
    from tools.agent_epic_wait_scope import Scope

    scope = (
        Scope.retained_owner(state, root)
        if config["schema_version"] == "8.0" else None
    )
    if scope is None:
        from tools.agent_quota_supervisor import readiness as quota_readiness

        quota_readiness(state, root, config)
        scope = (
            Scope.for_session_wake(state, root) if wake
            else Scope.inherited(state, root)
        )
    return SessionRuntime(state, root, config, wait_scope=scope)


def build_entry(state: Path, root: Path, *, action: str | None = None) -> Any:
    """Frozen bootstrap checks before importing the installed entry module."""
    state = secure_directory(state, root)
    for name, code in (
        ("epic-runtime.json", "EPIC_CONFIG_REQUIRED"),
        ("mandate.json", "MANDATE_REQUIRED"),
    ):
        path = state / name
        if not path.exists() and not path.is_symlink():
            raise DeploymentError(code)
    config = private_json(state / "epic-runtime.json")
    if set(config) != epic_config_keys(config):
        raise DeploymentError("RUNTIME_CONFIG_INVALID")
    if action not in {"status", "pause", "stop"}:
        final_preseed_guard(state, root)
    mandate = private_json(state / "mandate.json")
    bootstrap_authority(config, mandate, epic=True)
    planning_profile_guard(state, config, mandate)
    wait_profile_guard(state, config, mandate)
    quota_profile_guard(state, config, mandate)
    continuation_profile_guard(state, config, mandate)
    learning_profile_guard(state, config, mandate)
    policy = Path(config["policy_root"])
    verify_policy(policy, root, mandate.get("policy_sha", ""), entry=Path(__file__))
    existing = sys.modules.get("tools")
    if existing is not None and not Path(
        existing.__file__ or ""
    ).resolve().is_relative_to(policy.resolve()):
        raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(policy))
    from tools import agent_epic_entry

    return agent_epic_entry


def build_quota_cleanup(state: Path, root: Path) -> Any:
    """Verify pinned bytes for narrow cleanup without granting expired work."""
    state = secure_directory(state, root)
    config = private_json(state / "epic-runtime.json")
    if not quota_enabled(config) or set(config) != epic_config_keys(config):
        raise DeploymentError("QUOTA_PROFILE_REQUIRED")
    managed_launch_guard(state, root, read_only=True)
    mandate = private_json(state / "mandate.json")
    policy = Path(config["policy_root"])
    verify_policy(policy, root, mandate.get("policy_sha", ""), entry=Path(__file__))
    existing = sys.modules.get("tools")
    if existing is not None and not Path(
        existing.__file__ or ""
    ).resolve().is_relative_to(policy.resolve()):
        raise DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(policy))
    from tools import agent_quota_supervisor

    return agent_quota_supervisor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "live",
            "effect",
            "reconcile",
            "epic_resume",
            "quota_wake",
            "quota_prepare",
            "quota_install",
            "entry",
        ),
    )
    parser.add_argument("--wake-arm-id")
    parser.add_argument("--phase")
    parser.add_argument("--approved-wakeup-digest")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument(
        "--action",
        choices=("discover", "prepare", "start", "status", "pause", "stop", "resume"),
    )
    parser.add_argument("--repository")
    parser.add_argument("--epic", type=int)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--installation", type=Path)
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args()
    if (
        args.command == "quota_wake"
        and (
            not isinstance(args.wake_arm_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", args.wake_arm_id) is None
        )
    ) or (args.command != "quota_wake" and args.wake_arm_id is not None):
        parser.error("exact wake arm instance required only for quota_wake")
    if args.command != "entry" and any(
        (
            args.action,
            args.repository,
            args.epic,
            args.registry,
            args.installation,
            args.background,
        )
    ):
        parser.error("entry options require entry command")
    if args.approved_wakeup_digest is not None and args.command != "quota_install":
        parser.error("wakeup approval requires quota_install")
    if args.command == "quota_install" and args.approved_wakeup_digest is None:
        parser.error("quota_install requires exact owner-approved wakeup digest")
    try:
        if args.command in {"quota_prepare", "quota_install"}:
            state = secure_directory(args.state_dir, args.repository_root)
            root = args.repository_root
            build_entry(state, root)  # Verify the frozen policy before any imports.
            config = private_json(state / "epic-runtime.json")
            managed_launch_guard(
                state,
                root,
                read_only=args.command == "quota_prepare",
                allow_initial=True,
            )
            from tools import agent_orchestrate as workflow
            from tools import agent_quota_probe as probe
            from tools.agent_quota_supervisor import prepare
            from tools.agent_quota_wakeup import SystemdUser

            value, service = prepare(state, root, config)
            path = state / "quota-wakeup-candidate.json"
            if path.exists() or path.is_symlink():
                if private_json(path) != value:
                    raise DeploymentError("QUOTA_WAKE_CANDIDATE_EXISTS")
            elif args.command == "quota_prepare":
                workflow.StateStore(state).save("quota-wakeup-candidate", value)
            else:
                raise DeploymentError("QUOTA_WAKE_CANDIDATE_REQUIRED")
            status = "PREPARED"
            if args.command == "quota_install":
                if args.approved_wakeup_digest != value["candidate_digest"]:
                    raise DeploymentError("QUOTA_WAKE_APPROVAL_REQUIRED")

                def guard() -> None:
                    managed_launch_guard(state, root, allow_initial=True)
                    current, _service = prepare(
                        state,
                        root,
                        private_json(state / "epic-runtime.json"),
                    )
                    if current != value:
                        raise DeploymentError("QUOTA_WAKE_BINDING_CHANGED")

                host = SystemdUser(
                    state,
                    root,
                    Path.home() / ".config" / "systemd" / "user",
                    guard=guard,
                    now=lambda: int(time.time()),
                )
                probe.require_host(
                    private_json(state / "quota-host-probe.json"),
                    expected_digest=value["host_probe_digest"],
                    bindings={
                        "host_digest": host.host_digest(),
                        **{
                            key: value[key]
                            for key in (
                                "source_digest",
                                "supervisor_sha256",
                                "service_sha256",
                                "timer_template_sha256",
                            )
                        },
                    },
                )
                host.install(
                    value,
                    service,
                    approved_digest=args.approved_wakeup_digest,
                )
                status = "INSTALLED"
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "status": status,
                        "candidate_digest": value["candidate_digest"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "entry":
            result = build_entry(
                args.state_dir, args.repository_root, action=args.action
            ).execute(
                args.action,
                args.repository_root,
                args.state_dir,
                repository=args.repository,
                epic=args.epic,
                registry=args.registry,
                installation=args.installation,
                background=args.background,
            )
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command in {"epic_resume", "quota_wake"}:
            if args.command == "quota_wake":
                if _retained_wait_before_quota(args.state_dir, args.repository_root):
                    print(json.dumps({
                        "status": "BLOCKED", "machine_code": "QUOTA_WAKE_STALE",
                    }, sort_keys=True))
                    return 0
                cleanup = build_quota_cleanup(args.state_dir, args.repository_root)
                config = private_json(args.state_dir / "epic-runtime.json")
                mandate = private_json(args.state_dir / "mandate.json")
                control = private_json(args.state_dir / "launch-control.json")
                stopped = (
                    control["state"] != "RUNNING" or mandate.get("revoked") is True
                )
                expired = time.time() >= min(
                    mandate["expires_at"],
                    config["started_at"] + mandate["limits"]["max_duration_seconds"],
                )
                if stopped or expired:
                    cancelled = cleanup.cancel_owned(
                        args.state_dir,
                        args.repository_root,
                    )
                    print(
                        json.dumps(
                            {
                                "schema_version": "1.0",
                                "status": "BLOCKED",
                                "machine_code": "QUOTA_WAKE_AUTHORITY_STOPPED",
                                "result": cancelled,
                            },
                            sort_keys=True,
                        )
                    )
                    return 0
            session = build_session(
                args.state_dir,
                args.repository_root,
                wake=args.command == "quota_wake",
            )
            retained = (
                session.wait_scope is not None and session.wait_scope._retained_owner
            )
            if retained and args.command == "quota_wake":
                session.wait_scope.guard()
                result = {"status": "BLOCKED", "machine_code": "QUOTA_WAKE_STALE"}
            elif retained:
                result = session.resume()
            elif quota_enabled(session.config):
                from tools.agent_quota_supervisor import run as quota_run

                result = quota_run(
                    session,
                    wake=args.command == "quota_wake",
                    wake_arm_id=args.wake_arm_id,
                )
            else:
                result = session.resume()
            status = result.get("status")
            if status == "WAITING_OWNER_FINAL_APPROVAL":
                if session.config.get("schema_version") != "8.0":
                    raise DeploymentError("EPIC_RUNTIME_RESULT_INVALID")
                from tools.agent_final_preparation_driver import validate_wait_result

                result = validate_wait_result(result)
            elif status not in {
                "ROADMAP_DONE", "BLOCKED", "ESCALATE", "PAUSED_RATE_LIMIT",
            }:
                raise DeploymentError("EPIC_RUNTIME_RESULT_INVALID")
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "status": status,
                        "machine_code": result["machine_code"],
                        "result": result,
                    },
                    sort_keys=True,
                )
            )
            return 0
        engine = build_runtime(
            args.state_dir,
            args.repository_root,
            read_only=args.command in {"live", "reconcile"},
        )
        if args.command == "live":
            value = dict(engine.live())
            if engine.merge_sha() is not None:
                value["merge_sha"] = engine.merge_sha()
        elif args.command == "reconcile":
            state, receipt = engine.reconcile(args.phase)
            value = {"state": state, "receipt": receipt}
        else:
            value = {
                "status": "APPLIED",
                "machine_code": "OK",
                "receipt": dict(engine.effect(args.phase)),
            }
    except Exception as error:
        code = getattr(
            error,
            "machine_code",
            str(error)
            if isinstance(error, DeploymentError)
            else "RUNTIME_PHASE_FAILED",
        )
        if (
            not isinstance(code, str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) is None
        ):
            code = "RUNTIME_PHASE_FAILED"
        if args.command in {
            "epic_resume",
            "quota_wake",
            "quota_prepare",
            "quota_install",
            "entry",
        }:
            value = {
                "schema_version": "1.0",
                "status": "BLOCKED"
                if type(error).__name__
                in {
                    "PhaseBlocked",
                    "QuotaError",
                    "DeploymentError",
                }
                else "ESCALATE",
                "machine_code": code,
                "result": None,
            }
        elif args.command == "reconcile":
            value = {"state": "UNKNOWN", "receipt": None}
        else:
            blocked = error.__class__.__name__ == "PhaseBlocked"
            value = {
                "status": "BLOCKED" if blocked else "ESCALATE",
                "machine_code": code,
                "receipt": None,
            }
    print(json.dumps(value, sort_keys=True))
    return 0


# Original final counter source validation lives in the frozen stdlib entry.
_final_preseed_CONFIG8_KEYS = EPIC_CONFIG_KEYS | {
    "approved_launch_digest",
    "approved_planning_digest",
    "approved_wait_digest",
    "approved_quota_digest",
    "approved_continuation_digest",
    "approved_learning_digest",
    "approved_final_preparation_digest",
}
_final_preseed_IDENTITY = {"repository", "epic", "mandate_digest", "launch_digest"}
_final_preseed_PROFILE = {
    "repository",
    "epic",
    "roadmap_ref",
    "policy_sha",
    "mandate_digest",
    "owner_identity",
    "max_report_chars",
    "max_artifact_bytes",
    "max_final_model_calls",
}
_final_preseed_COMMAND = _final_preseed_IDENTITY | {
    "kind",
    "procedure",
    "owner_identity",
    "policy_sha",
    "preparation_digest",
    "counter_zero_digest",
}
_final_preseed_APPROVAL = _final_preseed_IDENTITY | {
    "owner_identity",
    "preparation_digest",
    "counter_zero_digest",
    "owner_command_digest",
    "approved_at",
}
_final_preseed_RECEIPT = _final_preseed_APPROVAL | {"kind", "initialization_digest"}
_final_preseed_PIN = _final_preseed_IDENTITY | {
    "owner_identity",
    "initialization_digest",
    "installation_receipt_digest",
}
_final_preseed_ZERO = _final_preseed_IDENTITY | {
    "calls_consumed",
    "pending_reservation_digest",
}
_final_preseed_MAX_SOURCE_BYTES = 262144


def _final_preseed_invalid(
    code: str = "FINAL_INITIALIZATION_SOURCE_INVALID",
) -> NoReturn:
    raise DeploymentError(code)


def _final_preseed_require(ok: bool) -> None:
    if not ok:
        _final_preseed_invalid()


def _final_preseed_integer(
    value: Any, minimum: int = 0, maximum: int = 2**53 - 1
) -> None:
    _final_preseed_require(type(value) is int and minimum <= value <= maximum)


def _final_preseed_hash(value: Any, *, tagged: bool = False, sha: bool = False) -> None:
    pattern = (
        r"[0-9a-f]{40}"
        if sha
        else (r"sha256:[0-9a-f]{64}" if tagged else r"[0-9a-f]{64}")
    )
    _final_preseed_require(
        isinstance(value, str) and re.fullmatch(pattern, value) is not None
    )


def _final_preseed_content(value: Any) -> str:
    return canonical_digest(value).removeprefix("sha256:")


def _final_preseed_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(items)
    _final_preseed_require(len(value) == len(items))
    return value


def _final_preseed_private(fd: int, *, directory: bool) -> None:
    info = os.fstat(fd)
    _final_preseed_require(
        (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and info.st_uid == getattr(os, "getuid")()
        and not info.st_mode & 0o077
        and (directory or info.st_nlink == 1)
    )


def _final_preseed_directory(parent: int, name: str) -> int:
    fd = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_DIRECTORY"),
        dir_fd=parent,
    )
    try:
        _final_preseed_private(fd, directory=True)
    except Exception:
        os.close(fd)
        raise
    return fd


def _final_preseed_seed(path: Path, root: Path) -> int:
    # Preserve inherited outside-worktree/private-directory checks, then anchor
    # every path component without following links before reading named sources.
    secure_directory(path, root)
    fd = os.open(
        path.anchor,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_DIRECTORY"),
    )
    try:
        for part in path.parts[1:]:
            next_fd = os.open(
                part,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_DIRECTORY"),
                dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
        _final_preseed_private(fd, directory=True)
        return fd
    except Exception:
        os.close(fd)
        raise


def _final_preseed_read(parent: int, name: str, cap: int) -> dict[str, Any]:
    fd = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK"),
        dir_fd=parent,
    )
    try:
        _final_preseed_private(fd, directory=False)
        _final_preseed_require(os.fstat(fd).st_size <= cap)
        raw = bytearray()
        while len(raw) <= cap:
            chunk = os.read(fd, min(65536, cap + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        _final_preseed_require(len(raw) <= cap)
        value = json.loads(raw, object_pairs_hook=_final_preseed_pairs)
        _final_preseed_require(isinstance(value, dict))
        return dict(value)
    finally:
        os.close(fd)


def _final_preseed_sealed(
    value: dict[str, Any], keys: set[str], self_key: str = "record_digest"
) -> dict[str, Any]:
    _final_preseed_require(set(value) == keys | {"schema_version", self_key})
    _final_preseed_require(value["schema_version"] == "1.0")
    _final_preseed_hash(value[self_key])
    _final_preseed_require(
        value[self_key]
        == _final_preseed_content({k: v for k, v in value.items() if k != self_key})
    )
    _final_preseed_integer(value["epic"], 1, 2147483647)
    _final_preseed_require(
        isinstance(value["repository"], str)
        and re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}",
            value["repository"],
        )
        is not None
    )
    for key in ("mandate_digest", "launch_digest", "preparation_digest"):
        if key in value:
            _final_preseed_hash(value[key], tagged=True)
    if "policy_sha" in value:
        _final_preseed_hash(value["policy_sha"], sha=True)
    if "owner_identity" in value:
        owner = value["owner_identity"]
        _final_preseed_require(
            isinstance(owner, str)
            and 1 <= len(owner) <= 256
            and owner == owner.strip()
            and all(ord(char) >= 32 for char in owner)
        )
    if "roadmap_ref" in value:
        ref = value["roadmap_ref"]
        _final_preseed_require(
            isinstance(ref, str)
            and len(ref) <= 256
            and ref.startswith(f"roadmap/{value['epic']}-")
            and re.fullmatch(r"[A-Za-z0-9_./-]+", ref) is not None
            and ".." not in ref
            and "//" not in ref
            and not ref.endswith(("/", "."))
        )
    for key in (
        "counter_zero_digest",
        "owner_command_digest",
        "initialization_digest",
        "installation_receipt_digest",
    ):
        if key in value:
            _final_preseed_hash(value[key])
    if "approved_at" in value:
        _final_preseed_integer(value["approved_at"])
    return value


def _final_preseed_absent(parent: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return True
    return False


_final_preseed_RESERVATION = _final_preseed_IDENTITY | {
    "ordinal",
    "preparation_identity_digest",
    "input_digest",
    "backend_digest",
    "settings_digest",
    "started_at",
    "deadline_at",
    "status",
    "output_digest",
}
_final_preseed_POLICY_FILES = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/SECURITY.md",
    "docs/ARCHITECTURE.md",
    "docs/threat-model.md",
    "docs/agent-contracts.md",
)


def _final_preseed_exact(value: Any, keys: set[str]) -> dict[str, Any]:
    _final_preseed_require(isinstance(value, dict) and set(value) == keys)
    return dict(value)


def _final_preseed_request_variant(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Closed native request syntax; never learning-source provenance admission."""
    keys = {
        "issue",
        "acceptance_criteria",
        "base_sha",
        "head_sha",
        "repair_iteration",
        "gate_evidence",
        "reviewed_state",
        "diff",
        "contract",
    }
    if isinstance(raw, dict) and set(raw) == keys:
        return _final_preseed_exact(raw, keys), None
    request = _final_preseed_exact(raw, keys | {"learning_source_digest"})
    _final_preseed_hash(request["learning_source_digest"])
    return request, request["learning_source_digest"]


def _final_preseed_git(root: Path, *args: str, optional: bool = False) -> str | None:
    # Fixed native read-only Git; no inherited Git environment, fsmonitor,
    # textconv, external diff, hooks or optional index writes. Bound all output
    # while it is read, not after an unbounded capture_output allocation.
    environment = {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    command = [
        "/usr/bin/git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.untrackedCache=false",
        *args,
    ]
    with subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as child:
        assert child.stdout is not None
        output = bytearray()
        deadline = time.monotonic() + 5
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    _final_preseed_require(remaining > 0)
                    _final_preseed_require(bool(selector.select(remaining)))
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    output.extend(chunk)
                    _final_preseed_require(
                        len(output) <= _final_preseed_MAX_SOURCE_BYTES
                    )
            result = child.wait(timeout=max(0.001, deadline - time.monotonic()))
            if optional and result:
                return None
            _final_preseed_require(result == 0)
            return output.decode().strip()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()



class _FinalPreseedPolicyObjects:
    """Seven fixed policy requests, one private bootstrap-local stream."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.child: subprocess.Popen[bytes] | None = None
        self.selector: selectors.BaseSelector | None = None
        self.end: float | None = None
        self.io_end: float | None = None
        self.requests = 0

    @staticmethod
    def _invalid() -> NoReturn:
        _final_preseed_invalid()

    def _window(self) -> None:
        now = time.monotonic()
        if self.end is not None and now >= self.end:
            self._invalid()
        self.io_end, self.end = now + 4.0, now + 5.0

    def __enter__(self) -> _FinalPreseedPolicyObjects:
        primary: BaseException | None = None
        try:
            self._window()
            self.child = subprocess.Popen(
                ["/usr/bin/git", "-c", "core.fsmonitor=false", "-c",
                 "core.hooksPath=/dev/null", "-c", "core.untrackedCache=false",
                 "cat-file", "--batch"], cwd=self.root,
                env={
                    "PATH": "/usr/bin:/bin", "LC_ALL": "C",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
                },
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, shell=False, close_fds=True, bufsize=0,
            )
            if self.child.stdin is None or self.child.stdout is None:
                self._invalid()
            set_blocking = getattr(os, "set_blocking", None)
            if not callable(set_blocking):
                self._invalid()
            set_blocking(self.child.stdin.fileno(), False)
            set_blocking(self.child.stdout.fileno(), False)
            self.selector = selectors.DefaultSelector()
        except BaseException as error:
            primary = error
        if primary is not None:
            self._cleanup(primary)
            self._raise(primary)
        return self

    @staticmethod
    def _raise(error: BaseException) -> NoReturn:
        if isinstance(error, DeploymentError):
            raise error
        if isinstance(
            error, (OSError, ValueError, UnicodeError, subprocess.SubprocessError),
        ):
            raise DeploymentError("FINAL_INITIALIZATION_SOURCE_INVALID") from None
        raise error

    def _deadline(self, *, final: bool = False) -> None:
        deadline = self.end if final else self.io_end
        if deadline is None or time.monotonic() >= deadline:
            self._invalid()

    def _wait(self, fd: int, event: int, *, final: bool = False) -> None:
        if self.selector is None:
            self._invalid()
        assert self.selector is not None
        deadline = self.end if final else self.io_end
        if deadline is None:
            self._invalid()
        assert deadline is not None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._invalid()
        self.selector.register(fd, event)
        primary = None
        try:
            if not self.selector.select(remaining):
                self._invalid()
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                self.selector.unregister(fd)
            except BaseException:
                if primary is None:
                    raise
        if time.monotonic() >= deadline:
            self._invalid()

    def _read_some(self, count: int, *, final: bool = False) -> bytes:
        child = self.child
        if child is None or child.stdout is None:
            self._invalid()
        assert child is not None and child.stdout is not None
        fd = child.stdout.fileno()
        while True:
            self._wait(fd, selectors.EVENT_READ, final=final)
            try:
                raw = os.read(fd, min(count, 65536))
                self._deadline(final=final)
                return raw
            except (BlockingIOError, InterruptedError):
                continue

    def _exact(self, size: int) -> bytes:
        raw = bytearray()
        while len(raw) < size:
            chunk = self._read_some(size - len(raw))
            if not chunk:
                self._invalid()
            raw.extend(chunk)
        return bytes(raw)

    def policy(self, policy_sha: str, path: str) -> str | None:
        primary: BaseException | None = None
        result: str | None = None
        try:
            _final_preseed_hash(policy_sha, sha=True)
            if (self.requests >= len(_final_preseed_POLICY_FILES)
                    or path != _final_preseed_POLICY_FILES[self.requests]):
                self._invalid()
            if self.requests:
                self._window()
            self.requests += 1
            child = self.child
            if child is None or child.stdin is None:
                self._invalid()
            assert child is not None and child.stdin is not None
            expression = (policy_sha + ":" + path).encode("ascii")
            pending = memoryview(expression + b"\n")
            while pending:
                self._wait(child.stdin.fileno(), selectors.EVENT_WRITE)
                try:
                    count = os.write(child.stdin.fileno(), pending)
                except (BlockingIOError, InterruptedError):
                    continue
                self._deadline()
                if count <= 0:
                    self._invalid()
                pending = pending[count:]
            header = bytearray()
            while len(header) < 128:
                byte = self._read_some(1)
                if not byte:
                    self._invalid()
                header.extend(byte)
                if byte == b"\n":
                    break
            if bytes(header) != expression + b" missing\n":
                match = re.fullmatch(
                    rb"([0-9a-f]{40}) blob (0|[1-9][0-9]*)\n", bytes(header),
                )
                if match is None:
                    self._invalid()
                assert match is not None
                size = int(match[2])
                if size > _final_preseed_MAX_SOURCE_BYTES:
                    self._invalid()
                raw = self._exact(size)
                if self._exact(1) != b"\n":
                    self._invalid()
                if hashlib.sha1(f"blob {size}\0".encode() + raw).hexdigest() != (
                    match[1].decode("ascii")
                ):
                    self._invalid()
                result = raw.decode().strip()
            self._deadline(final=True)
        except BaseException as error:
            primary = error
        if primary is not None:
            self._raise(primary)
        return result

    def _finish(self) -> None:
        child = self.child
        if child is None or child.stdin is None:
            self._invalid()
        assert child is not None and child.stdin is not None
        child.stdin.close()
        if self._read_some(1, final=True):
            self._invalid()
        remaining = 0.0 if self.end is None else self.end - time.monotonic()
        if remaining <= 0 or child.wait(timeout=remaining) != 0:
            self._invalid()
        self._deadline(final=True)

    def _cleanup(self, primary: BaseException | None) -> None:
        secondary: BaseException | None = None
        child = self.child
        if child is not None:
            for stream in (child.stdin, child.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException as error:
                        if secondary is None:
                            secondary = error
            try:
                if child.poll() is None:
                    child.kill()
            except BaseException as error:
                if secondary is None:
                    secondary = error
            try:
                remaining = 0.0 if self.end is None else self.end - time.monotonic()
                child.wait(timeout=max(0.0, remaining))
            except BaseException as error:
                if secondary is None:
                    secondary = error
        if self.selector is not None:
            try:
                self.selector.close()
            except BaseException as error:
                if secondary is None:
                    secondary = error
        if primary is None:
            if secondary is not None:
                self._raise(secondary)
            self._deadline(final=True)

    def __exit__(self, _type: Any, error: Any, _traceback: Any) -> None:
        primary: BaseException | None = error
        if primary is None:
            try:
                self._finish()
            except BaseException as failure:
                primary = failure
        self._cleanup(primary)
        if primary is not None:
            self._raise(primary)
        return None

def _final_preseed_native_completion(
    original: dict[str, Any],
    backend: dict[str, Any],
    invocation: str,
    output: dict[str, Any],
    reservation: dict[str, Any],
) -> None:
    original = _final_preseed_exact(original, {"receipt", "terminal"})
    receipt = _final_preseed_exact(
        original["receipt"],
        {
            "schema_version",
            "role",
            "invocation_digest",
            "backend_digest",
            "requested",
            "effective",
            "thread_digest",
            "turn_digest",
            "outcome",
            "terminal_digest",
            "receipt_digest",
        },
    )
    terminal = _final_preseed_exact(
        original["terminal"],
        {
            "role",
            "invocation_digest",
            "thread_digest",
            "turn_digest",
            "status",
            "validated_output_digest",
            "adapter_exit_code",
        },
    )
    settings = {"model": backend["model"], "reasoning": "low"}
    for key in (
        "invocation_digest",
        "backend_digest",
        "thread_digest",
        "turn_digest",
        "terminal_digest",
        "receipt_digest",
    ):
        _final_preseed_hash(receipt[key])
    _final_preseed_require(
        receipt["schema_version"] == "1.0"
        and receipt["role"] == terminal["role"] == "controller"
        and receipt["invocation_digest"] == terminal["invocation_digest"] == invocation
        and receipt["backend_digest"] == _final_preseed_content(backend)
        and receipt["requested"] == receipt["effective"] == settings
        and receipt["outcome"] == "COMPLETED"
        and receipt["receipt_digest"]
        == _final_preseed_content(
            {k: v for k, v in receipt.items() if k != "receipt_digest"}
        )
        and receipt["terminal_digest"] == _final_preseed_content(terminal)
        and receipt["thread_digest"] == terminal["thread_digest"]
        and receipt["turn_digest"] == terminal["turn_digest"]
        and terminal["status"] == "completed"
        and terminal["validated_output_digest"] == _final_preseed_content(output)
        and type(terminal["adapter_exit_code"]) is int
        and terminal["adapter_exit_code"] == 0
        and reservation["settings_digest"] == _final_preseed_content(receipt)
        and reservation["output_digest"] == _final_preseed_content(output)
    )


def _final_preseed_consumed(
    seed_fd: int,
    preparation_fd: int,
    root: Path,
    config: dict[str, Any],
    profile: dict[str, Any],
    current: dict[str, Any],
    identity: dict[str, Any],
    reservation_name: str,
) -> None:
    cap = profile["max_artifact_bytes"]
    _final_preseed_require(
        current["pending_reservation_digest"] == reservation_name[12:-5]
    )
    reservation = _final_preseed_sealed(
        _final_preseed_read(preparation_fd, reservation_name, cap),
        _final_preseed_RESERVATION,
    )
    for key, expected in identity.items():
        _final_preseed_require(
            type(reservation[key]) is type(expected) and reservation[key] == expected
        )
    _final_preseed_integer(reservation["ordinal"], 1, 1)
    _final_preseed_integer(reservation["started_at"])
    _final_preseed_integer(reservation["deadline_at"], reservation["started_at"] + 1)
    for key in ("preparation_identity_digest", "input_digest", "backend_digest"):
        _final_preseed_hash(reservation[key])
    for key in ("settings_digest", "output_digest"):
        if reservation[key] is not None:
            _final_preseed_hash(reservation[key])
    _final_preseed_require(
        reservation["status"] in {"RESERVED", "UNKNOWN", "COMPLETED"}
    )
    if reservation["status"] != "COMPLETED":
        _final_preseed_invalid("FINAL_INITIALIZATION_NATIVE_CHAIN_REQUIRED")
    continuation = _final_preseed_read(
        seed_fd, "continuation-profile.json", _final_preseed_MAX_SOURCE_BYTES
    )
    _final_preseed_exact(
        continuation,
        {
            "schema_version",
            "repository",
            "epic",
            "roadmap_ref",
            "policy_sha",
            "mandate_digest",
            "owner_identity",
            "backend_digest",
            "limits",
        },
    )
    _final_preseed_require(
        continuation["schema_version"] == "1.0"
        and canonical_digest(continuation) == config["approved_continuation_digest"]
    )
    for key in (
        "repository",
        "epic",
        "roadmap_ref",
        "policy_sha",
        "mandate_digest",
        "owner_identity",
    ):
        _final_preseed_require(
            type(continuation[key]) is type(profile[key])
            and continuation[key] == profile[key]
        )
    limits = _final_preseed_exact(
        continuation["limits"],
        {
            "threshold_permille",
            "max_handoffs",
            "max_prompt_bytes",
            "max_record_bytes",
            "max_findings",
        },
    )
    for key, maximum in {
        "threshold_permille": 950,
        "max_handoffs": 32,
        "max_prompt_bytes": 262144,
        "max_record_bytes": 65536,
        "max_findings": 128,
    }.items():
        _final_preseed_integer(limits[key], 1, maximum)
    backend = role_backend_document(
        _final_preseed_read(
            seed_fd, "role-backend.json", _final_preseed_MAX_SOURCE_BYTES
        )
    )
    _final_preseed_require(
        _final_preseed_content(backend)
        == continuation["backend_digest"]
        == reservation["backend_digest"]
    )
    sources_fd = settings_fd = -1
    try:
        sources_fd = _final_preseed_directory(preparation_fd, "sources")
        settings_fd = _final_preseed_directory(preparation_fd, "role-settings")
        source = _final_preseed_exact(
            _final_preseed_read(
                sources_fd, "review-" + _final_preseed_content(identity) + ".json", cap
            ),
            {
                "identity",
                "request",
                "policy",
                "session_id",
                "output",
                "settings_source",
            },
        )
        environment = _final_preseed_sealed(
            _final_preseed_read(
                sources_fd,
                "environment-" + _final_preseed_content(identity) + ".json",
                cap,
            ),
            {
                "repository",
                "epic",
                "mandate_digest",
                "launch_digest",
                "kind",
                "preparation_identity_digest",
                "input_digest",
                "review_source_digest",
                "launcher_python_version",
                "launcher_executable_sha256",
            },
        )
        for key in (
            "preparation_identity_digest",
            "input_digest",
            "review_source_digest",
            "launcher_executable_sha256",
        ):
            _final_preseed_hash(environment[key])
        _final_preseed_require(
            environment["kind"] == "FINAL_AGGREGATE_ENVIRONMENT"
            and isinstance(environment["launcher_python_version"], str)
            and re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+", environment["launcher_python_version"]
            )
            is not None
            and all(
                environment[key] == reservation[key]
                for key in (
                    "repository",
                    "epic",
                    "mandate_digest",
                    "launch_digest",
                    "preparation_identity_digest",
                    "input_digest",
                )
            )
            and environment["review_source_digest"] == _final_preseed_content(source)
        )
        original = _final_preseed_read(
            settings_fd,
            "receipt-" + reservation["input_digest"] + ".json",
            min(cap, limits["max_record_bytes"]),
        )
        main = _final_preseed_git(root, "rev-parse", "refs/heads/main")
        roadmap = _final_preseed_git(
            root, "rev-parse", "refs/heads/" + profile["roadmap_ref"]
        )
        tree = _final_preseed_git(root, "rev-parse", str(roadmap) + "^{tree}")
        for value in (main, roadmap, tree):
            _final_preseed_hash(value, sha=True)
        _final_preseed_require(
            not _final_preseed_git(root, "status", "--porcelain=v1")
            and _final_preseed_git(root, "rev-parse", "HEAD") == roadmap
        )
        expected_identity = {
            **identity,
            "main_sha": main,
            "roadmap_sha": roadmap,
            "tree_sha": tree,
            "policy_sha": profile["policy_sha"],
            "backend_digest": _final_preseed_content(backend),
        }
        _final_preseed_require(
            source["identity"] == expected_identity
            and reservation["preparation_identity_digest"]
            == _final_preseed_content(expected_identity)
        )
        request, learning_source_digest = _final_preseed_request_variant(
            source["request"]
        )
        paths = _final_preseed_git(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            str(main),
            str(roadmap),
        )
        _final_preseed_require(bool(paths))
        diff = _final_preseed_git(
            root, "diff", "--no-ext-diff", "--no-textconv", str(main), str(roadmap)
        )
        _final_preseed_require(isinstance(diff, str) and len(diff) <= 200000)
        criteria = [
            "Review cumulative roadmap diff, security invariants "
            "and final delivery contract"
        ]
        contract = {
            "issue": identity["epic"],
            "epic": identity["epic"],
            "acceptance_criteria": criteria,
            "base_ref": "main",
            "base_sha": main,
            "head_ref": profile["roadmap_ref"],
            "allowed_paths": str(paths).splitlines(),
            "policy_sha": profile["policy_sha"],
            "permissions": {
                "commit": False,
                "push": False,
                "create_pr": False,
                "comment": False,
                "merge": False,
            },
            "max_repair_iterations": 0,
            "remaining_repair_iterations": 0,
            "max_minutes": 60,
            "task_class": "final-review",
        }
        gate = request["gate_evidence"]
        _final_preseed_require(isinstance(gate, dict))
        checks = ["pytest", "ruff", "mypy", "pre-commit"]
        _final_preseed_require(
            gate.get("profile") == "repository-full"
            and gate.get("profile_version") == "1"
            and gate.get("expected_checks") == gate.get("executed_checks") == checks
            and gate.get("complete") is True
            and gate.get("status") == "passed"
            and gate.get("machine_code") == "OK"
        )
        entries = gate.get("checks")
        _final_preseed_require(
            isinstance(entries, list) and len(entries) == len(checks)
        )
        _final_preseed_require(
            all(
                isinstance(entry, dict)
                and entry.get("id") == key
                and entry.get("status") == "passed"
                for entry, key in zip(entries, checks)
            )
        )
        snapshot = gate["snapshot"]
        _final_preseed_require(
            isinstance(snapshot, dict) and snapshot["tree_hash"] == tree
        )
        _final_preseed_hash(snapshot["snapshot_commit"], sha=True)
        expected_request = {
            "issue": identity["epic"],
            "acceptance_criteria": criteria,
            "base_sha": main,
            "head_sha": roadmap,
            "repair_iteration": 0,
            "gate_evidence": gate,
            "reviewed_state": {"snapshot_commit": snapshot["snapshot_commit"]},
            "diff": diff,
            "contract": contract,
        }
        if learning_source_digest is not None:
            expected_request["learning_source_digest"] = learning_source_digest
        _final_preseed_require(request == expected_request)
        policy = {}
        if config["schema_version"] == "8.0":
            with _FinalPreseedPolicyObjects(root) as objects:
                for path in _final_preseed_POLICY_FILES:
                    content = objects.policy(profile["policy_sha"], path)
                    if content is not None:
                        policy[path] = content
        else:
            for path in _final_preseed_POLICY_FILES:
                content = _final_preseed_git(
                    root,
                    "show",
                    "--no-ext-diff",
                    "--no-textconv",
                    profile["policy_sha"] + ":" + path,
                    optional=True,
                )
                if content is not None:
                    policy[path] = content
        _final_preseed_require(
            "AGENTS.md" in policy
            and "CONTRIBUTING.md" in policy
            and source["policy"] == policy
        )
        session = source["session_id"]
        _final_preseed_require(
            isinstance(session, str)
            and re.fullmatch(r"final-controller-[0-9a-f]{32}", session) is not None
        )
        payload = {**request, "trusted_policy": policy, "session_id": session}
        invocation = _final_preseed_content(payload)
        _final_preseed_require(
            invocation == reservation["input_digest"]
            and source["settings_source"] == original
        )
        output = _final_preseed_exact(
            source["output"],
            {
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
            },
        )
        basis = _final_preseed_exact(
            output["review_basis"],
            {
                "trust_source_kind",
                "head_policy_applied",
                "executor_self_assessment_treated_as_evidence_only",
            },
        )
        _final_preseed_require(
            output["schema_version"] == "1.0"
            and output["role"] == "controller"
            and type(output["task_issue"]) is int
            and output["task_issue"] == identity["epic"]
            and output["base_sha"] == main
            and output["head_sha"] == roadmap
            and output["verdict"] in {"PASS", "PASS_WITH_NOTES"}
            and output["blocking_findings"] == []
            and basis["trust_source_kind"] == "local_read_only_bundle"
            and basis["head_policy_applied"] is False
            and basis["executor_self_assessment_treated_as_evidence_only"] is True
            and len(json.dumps(output, ensure_ascii=False)) <= 20000
        )
        _final_preseed_native_completion(
            original, backend, invocation, output, reservation
        )
    finally:
        for fd in (sources_fd, settings_fd):
            if fd >= 0:
                os.close(fd)


@contextmanager
def _final_preseed_original_sources(
    state: Path, root: Path
) -> Iterator[dict[str, Any]]:
    """Shared anchored/capped original graph; never grants restart admission."""
    if os.name != "posix":
        _final_preseed_invalid()
    seed_fd = preparation_fd = initialization_fd = -1
    try:
        seed_fd = _final_preseed_seed(managed_state(state), root)
        config = _final_preseed_read(
            seed_fd, "epic-runtime.json", _final_preseed_MAX_SOURCE_BYTES
        )
        _final_preseed_require(
            set(config) == _final_preseed_CONFIG8_KEYS
            and config["schema_version"] == "8.0"
            and config["approved_final_digest"] is None
        )
        mandate = _final_preseed_read(
            seed_fd, "mandate.json", _final_preseed_MAX_SOURCE_BYTES
        )
        bootstrap_authority(config, mandate, epic=True)
        launch = _final_preseed_read(
            seed_fd, "launch.json", _final_preseed_MAX_SOURCE_BYTES
        )
        _final_preseed_require(
            set(launch) == LAUNCH_KEYS and launch["schema_version"] == "1.0"
        )
        _final_preseed_hash(config["approved_launch_digest"], tagged=True)
        _final_preseed_hash(config["approved_final_preparation_digest"], tagged=True)
        _final_preseed_require(
            canonical_digest(launch) == config["approved_launch_digest"]
        )
        adapter = _final_preseed_read(
            seed_fd, "runtime-adapter.json", _final_preseed_MAX_SOURCE_BYTES
        )
        _final_preseed_require(
            launch_config_digest(config, adapter) == launch["runtime_config_digest"]
        )
        for key in ("installation_digest", "runtime_config_digest", "mandate_digest"):
            _final_preseed_hash(launch[key], tagged=True)
        _final_preseed_require(
            launch["mandate_digest"] == config["approved_mandate_digest"]
        )
        for key in (
            "repository",
            "epic",
            "roadmap_ref",
            "policy_sha",
            "owner_identity",
        ):
            _final_preseed_require(
                type(launch[key]) is type(mandate[key]) and launch[key] == mandate[key]
            )
        profile = _final_preseed_sealed(
            _final_preseed_read(
                seed_fd,
                "final-preparation-profile.json",
                _final_preseed_MAX_SOURCE_BYTES,
            ),
            _final_preseed_PROFILE,
        )
        for key in (
            "repository",
            "epic",
            "roadmap_ref",
            "policy_sha",
            "owner_identity",
            "mandate_digest",
        ):
            _final_preseed_require(
                type(profile[key]) is type(launch[key]) and profile[key] == launch[key]
            )
        _final_preseed_require(
            canonical_digest(profile) == config["approved_final_preparation_digest"]
        )
        _final_preseed_integer(profile["max_report_chars"], 1, 20000)
        _final_preseed_integer(
            profile["max_artifact_bytes"], 1, _final_preseed_MAX_SOURCE_BYTES
        )
        _final_preseed_integer(profile["max_final_model_calls"], 1, 1)
        cap = profile["max_artifact_bytes"]
        preparation_fd = _final_preseed_directory(seed_fd, "final-preparation")
        current = _final_preseed_sealed(
            _final_preseed_read(preparation_fd, "role-state.json", cap),
            _final_preseed_ZERO,
        )
        _final_preseed_integer(current["calls_consumed"], 0, 1)
        identity = {
            "repository": launch["repository"],
            "epic": launch["epic"],
            "mandate_digest": launch["mandate_digest"],
            "launch_digest": config["approved_launch_digest"],
        }
        initialization_fd = _final_preseed_directory(seed_fd, "final-initialization")
        command = _final_preseed_sealed(
            _final_preseed_read(initialization_fd, "command-source.json", cap),
            _final_preseed_COMMAND,
        )
        approval = _final_preseed_sealed(
            _final_preseed_read(initialization_fd, "approval.json", cap),
            _final_preseed_APPROVAL,
            "approved_digest",
        )
        receipt = _final_preseed_sealed(
            _final_preseed_read(initialization_fd, "install-receipt.json", cap),
            _final_preseed_RECEIPT,
        )
        pin = _final_preseed_sealed(
            _final_preseed_read(initialization_fd, "approval-pin.json", cap),
            _final_preseed_PIN,
        )
        for value in (current, command, approval, receipt, pin):
            for key, expected in identity.items():
                _final_preseed_require(
                    type(value[key]) is type(expected) and value[key] == expected
                )
            if "owner_identity" in value:
                _final_preseed_require(
                    value["owner_identity"] == launch["owner_identity"]
                )
        _final_preseed_require(
            command["policy_sha"] == launch["policy_sha"]
            and command["kind"] == "OWNER_ORIGINAL_FINAL_COUNTER_PROVISIONING"
            and command["procedure"] == "ADR303_ORIGINAL_COUNTER_PROVISIONING_V1"
            and receipt["kind"] == "OWNER_FINAL_COUNTER_INITIALIZATION"
        )
        original_zero = {
            "schema_version": "1.0",
            **identity,
            "calls_consumed": 0,
            "pending_reservation_digest": None,
        }
        original_zero["record_digest"] = _final_preseed_content(original_zero)
        for value in (command, approval, receipt):
            _final_preseed_require(
                value["preparation_digest"]
                == config["approved_final_preparation_digest"]
                and value["counter_zero_digest"]
                == _final_preseed_content(original_zero)
            )
        _final_preseed_require(
            approval["owner_command_digest"] == _final_preseed_content(command)
            and receipt["owner_command_digest"] == _final_preseed_content(command)
            and receipt["approved_at"] == approval["approved_at"]
            and receipt["initialization_digest"] == _final_preseed_content(approval)
            and pin["initialization_digest"] == _final_preseed_content(approval)
            and pin["installation_receipt_digest"] == _final_preseed_content(receipt)
        )
        reservation = (
            "reservation-"
            + _final_preseed_content(
                {"launch_digest": identity["launch_digest"], "ordinal": 1}
            )
            + ".json"
        )
        yield {
            "config": config,
            "mandate": mandate,
            "adapter": adapter,
            "launch": launch,
            "profile": profile,
            "command": command,
            "approval": approval,
            "receipt": receipt,
            "pin": pin,
            "current": current,
            "identity": identity,
            "reservation": reservation,
            "original_zero": original_zero,
            "seed_fd": seed_fd,
            "preparation_fd": preparation_fd,
        }
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
        subprocess.TimeoutExpired,
    ):
        _final_preseed_invalid()
    finally:
        for fd in (initialization_fd, preparation_fd, seed_fd):
            if fd >= 0:
                os.close(fd)


def validate_final_preseed(state: Path, root: Path) -> dict[str, Any]:
    """Strict restart admission: proven original zero or completed native chain."""
    with _final_preseed_original_sources(state, root) as sources:
        current: dict[str, Any] = sources["current"]
        original_zero = sources["original_zero"]
        seed_fd, preparation_fd = sources["seed_fd"], sources["preparation_fd"]
        config, profile = sources["config"], sources["profile"]
        identity, reservation = sources["identity"], sources["reservation"]
        if current["calls_consumed"] == 0:
            _final_preseed_require(
                current["pending_reservation_digest"] is None
                and current == original_zero
            )
            _final_preseed_require(
                all(
                    _final_preseed_absent(preparation_fd, name)
                    for name in ("role-settings", "sources", reservation)
                )
            )
        else:
            _final_preseed_consumed(
                seed_fd,
                preparation_fd,
                root,
                config,
                profile,
                current,
                identity,
                reservation,
            )
        return current


def final_preparation_original_sources(state: Path, root: Path) -> dict[str, Any]:
    """Read original sources for an already owned continuous scope, not restart.

    This reader creates no namespaces, reservations or authority. Its callers
    still owe actual seed/supervisor ownership and original lifecycle guards.
    """
    with _final_preseed_original_sources(state, root) as sources:
        current = sources["current"]
        if current["calls_consumed"] == 0:
            _final_preseed_require(current == sources["original_zero"])
        else:
            _final_preseed_require(
                current["pending_reservation_digest"]
                == sources["reservation"]
                .removeprefix("reservation-")
                .removesuffix(".json")
            )
        return {
            k: v for k, v in sources.items() if k not in {"seed_fd", "preparation_fd"}
        }


if __name__ == "__main__":
    raise SystemExit(main())
