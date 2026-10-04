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
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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
            or target.read_bytes() != git("show", f"{sha}:{relative}")
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


def build_runtime(state: Path, root: Path) -> Any:
    state = secure_directory(state, root)
    config = private_json(state / "runtime-task.json")
    recovery_profile = config.get("schema_version") == "2.0"
    if set(config) != CONFIG_KEYS | (
        {"approved_recovery_digest"} if recovery_profile else set()
    ) or config["schema_version"] not in {"1.0", "2.0"}:
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
    reviewed = ReviewedWorkflow(
        root=root,
        directory=state,
        value=task_handover,
        approved_digest=config["approved_handover_digest"],
        adapter=codex_role_adapter(
            policy_root=policy, root=root, policy_sha=current["policy_sha"]
        ),
    )
    engine = TaskPhaseRuntime(
        store=loop.CheckpointStore(state, root),
        authority=authority,
        artifacts=reviewed.artifacts,
        demo_plan=lambda: private_json(state / "demo-plan.json"),
        demo_completion=lambda: private_json(state / "demo-completion.json"),
        approved_order=tuple(config["approved_order"]),
    )
    engine.bootstrap = BootstrapTask(engine, reviewed, authority)
    if recovery_profile:
        from tools.agent_epic_recovery import BootstrapRecovery

        def approved_recovery() -> str | None:
            fresh = private_json(state / "runtime-task.json")
            if fresh != config:
                raise DeploymentError("RECOVERY_APPROVAL_CHANGED")
            return config["approved_recovery_digest"]  # type: ignore[no-any-return]

        engine.recovery = BootstrapRecovery(state, root, authority, approved_recovery)
    return engine


def build_session(state: Path, root: Path) -> Any:
    state = secure_directory(state, root)
    if (state / "bootstrap-recovery.json").exists() or (
        (state / "runtime-task.json").exists()
        and private_json(state / "runtime-task.json").get("schema_version") == "2.0"
    ):
        raise DeploymentError("RECOVERY_ONE_TASK_REQUIRED")
    config = private_json(state / "epic-runtime.json")
    if set(config) != EPIC_CONFIG_KEYS or config.get("schema_version") != "1.0":
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

    return SessionRuntime(state, root, config)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("live", "effect", "reconcile", "epic_resume")
    )
    parser.add_argument("--phase")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "epic_resume":
            result = build_session(args.state_dir, args.repository_root).resume()
            status = result.get("status")
            if status not in {
                "ROADMAP_DONE",
                "BLOCKED",
                "ESCALATE",
                "PAUSED_RATE_LIMIT",
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
        engine = build_runtime(args.state_dir, args.repository_root)
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
        if args.command == "epic_resume":
            value = {
                "schema_version": "1.0",
                "status": "BLOCKED"
                if type(error).__name__ in {"PhaseBlocked", "DeploymentError"}
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


if __name__ == "__main__":
    raise SystemExit(main())
