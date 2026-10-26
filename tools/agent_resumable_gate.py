"""Opt-in local resumable gate. Requires an exact reviewed operator profile.

Run as python -m tools.agent_resumable_gate; the legacy agent_verify remains unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools.agent_gate_identity import fingerprint
from tools.agent_gate_journal import GateBlocked, Journal, _read, decode, digest
from tools.agent_gate_runner import Runner, boot_identity
from tools.agent_gate_state import capacity_preflight, validate_plan

PROFILE_KEYS = {
    "version",
    "root",
    "python",
    "git",
    "head",
    "base",
    "inputs",
    "environment",
    "plan",
    "expires_at",
    "seconds",
    "max_collections",
}
ENV_KEYS = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "PYTHONPATH",
    "TMPDIR",
    "PRE_COMMIT_HOME",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "PYTHONDONTWRITEBYTECODE",
    "VIRTUAL_ENV",
}


def profile(path: Path, approved_sha: str) -> dict[str, Any]:
    if os.name != "posix":
        raise GateBlocked("UNSUPPORTED_PLATFORM")
    raw = _read(path, 24 * 1024 * 1024)
    if len(raw) > 24 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != approved_sha:
        raise GateBlocked("PROFILE_PIN_INVALID")
    value = decode(raw)
    if (
        set(value) != PROFILE_KEYS
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise GateBlocked("PROFILE_SHAPE")
    for field in ("root", "python", "git"):
        target = Path(value[field])
        if not target.is_absolute() or target.resolve(strict=True) != target:
            raise GateBlocked("PROFILE_PATH_IDENTITY")
    if not isinstance(value["inputs"], list) or len(value["inputs"]) > 100:
        raise GateBlocked("PROFILE_INPUTS")
    for item in value["inputs"]:
        path_value = Path(item)
        if (
            not path_value.is_absolute()
            or path_value.resolve(strict=True) != path_value
        ):
            raise GateBlocked("PROFILE_INPUT_IDENTITY")
    env = value["environment"]
    if (
        not isinstance(env, dict)
        or not set(env) <= ENV_KEYS
        or any(not isinstance(v, str) or "\0" in v for v in env.values())
        or env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") != "1"
        or env.get("PYTHONDONTWRITEBYTECODE") != "1"
    ):
        raise GateBlocked("PROFILE_ENVIRONMENT")
    if (
        sum(len(k.encode()) + len(v.encode()) for k, v in env.items()) > 64 * 1024
        or any(len(v.encode()) > 8192 for v in env.values())
        or not {"HOME", "PATH"} <= set(env)
    ):
        raise GateBlocked("PROFILE_ENVIRONMENT_CAPACITY")
    for field in ("expires_at", "seconds"):
        number = value[field]
        if type(number) not in (int, float) or not math.isfinite(number):
            raise GateBlocked("PROFILE_CLOCK")
    if not 0 < value["seconds"] <= 7 * 24 * 3600:
        raise GateBlocked("PROFILE_SECONDS")
    if time.time() >= value["expires_at"]:
        raise GateBlocked("PROFILE_EXPIRED")
    if (
        type(value["max_collections"]) is not int
        or not 1 <= value["max_collections"] <= 16
    ):
        raise GateBlocked("PROFILE_COLLECTION_BUDGET")
    for field in ("head", "base"):
        if (
            not isinstance(value[field], str)
            or not value[field]
            or value[field].startswith("-")
        ):
            raise GateBlocked("PROFILE_GIT_REF")
    validate_plan(value["plan"])
    capacity_preflight(value["plan"], value["max_collections"])
    return value


def identity(value: dict[str, Any], profile_sha: str) -> str:
    root = Path(value["root"])
    git_env = {
        "PATH": os.defpath,
        "HOME": value["environment"]["HOME"],
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
    }
    bindings = {
        "profile": profile_sha,
        "plan": digest(value["plan"]),
        "boot": boot_identity(),
    }
    for name in ("head", "base"):
        result = subprocess.run(
            [
                value["git"],
                "-c",
                "core.fsmonitor=false",
                "rev-parse",
                "--verify",
                value[name] + "^{commit}",
            ],
            cwd=root,
            env=git_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
            check=False,
        )
        text = result.stdout.decode("ascii").strip()
        if (
            result.returncode
            or len(text) != 40
            or any(c not in "0123456789abcdef" for c in text)
        ):
            raise GateBlocked("GIT_IDENTITY_INVALID")
        bindings[name] = text
    inputs = [
        Path(value["python"]),
        Path(value["git"]),
        *[Path(p) for p in value["inputs"]],
    ]
    # The reviewed profile must include the complete dependency/plugin/hook input
    # closure. A list of package names/versions alone is deliberately insufficient.
    return fingerprint(root, inputs, value["environment"], bindings)


def execute(
    value: dict[str, Any],
    profile_sha: str,
    evidence: Path,
    *,
    resume: bool,
    pause_after: int | None,
    pause_requested: Callable[[], bool] = lambda: False,
    recovery: tuple[dict[str, Any], str] | None = None,
) -> dict[str, Any]:
    root = Path(value["root"])
    absolute_evidence = evidence.absolute()
    if absolute_evidence.is_relative_to(root):
        raise GateBlocked("STORE_INSIDE_SOURCE")

    def identify() -> str:
        return identity(value, profile_sha)

    initial = identify()
    expiry = value["expires_at"]
    if resume:
        store = Journal.open(absolute_evidence)
    else:
        launch = {
            "plan": value["plan"],
            "fingerprint": initial,
            "expires_at": expiry,
            "deadline": time.monotonic() + value["seconds"],
            "boot": boot_identity(),
            "max_collections": value["max_collections"],
            "profile": profile_sha,
        }
        store = Journal.create(absolute_evidence, launch)
    with store:
        if store.genesis["launch"].get("profile") != profile_sha:
            raise GateBlocked("ORIGINAL_PROFILE_CHANGED")
        runner = Runner(
            store,
            value["plan"],
            identity=identify,
            expires_at=expiry,
            deadline=store.genesis["launch"]["deadline"],
            allow_recovery=recovery is not None,
        )
        reused_units = sum(
            unit["terminal"] is not None for unit in runner.state.units.values()
        )
        initial_spawns = sum(
            e["kind"] == "spawn" and e["scope"] == "unit" for e in store.events
        )
        if recovery is not None:
            runner.recover(
                recovery[0],
                recovery[1],
                root=root,
                python=Path(value["python"]),
                environment=value["environment"],
            )
        if not runner.inventory_verified:
            runner.verify_inventory(
                root=root,
                python=Path(value["python"]),
                environment=value["environment"],
            )
        completed_now = 0
        for unit in runner.state.remaining():
            if pause_requested() or (
                pause_after is not None and completed_now >= pause_after
            ):
                runner.pause()
                return {
                    "status": "PAUSED",
                    "remaining_units": runner.state.remaining(),
                    "source": initial,
                    "not_agent_verify_1_0": True,
                }
            runner.run_unit(
                unit,
                root=root,
                python=Path(value["python"]),
                environment=value["environment"],
            )
            completed_now += 1
        for name in ("ruff", "mypy", "precommit"):
            if pause_requested():
                runner.pause()
                return {
                    "status": "PAUSED",
                    "remaining_units": [],
                    "source": initial,
                    "not_agent_verify_1_0": True,
                }
            if name not in runner.state.quality:
                runner.run_quality(
                    name,
                    root=root,
                    python=Path(value["python"]),
                    environment=value["environment"],
                )
        if not runner.complete():
            raise GateBlocked("AGGREGATE_INCOMPLETE")
        return {
            "status": "AGGREGATE_PASS",
            "source": initial,
            "nodes": len(value["plan"]["inventory"]),
            "units": len(value["plan"]["units"]),
            "not_agent_verify_1_0": True,
            "reused_units": reused_units,
            "executed_units": sum(
                e["kind"] == "spawn" and e["scope"] == "unit" for e in store.events
            )
            - initial_spawns,
            "child_sessions": sum(e["kind"] == "spawn" for e in store.events),
            "pytest_sessions": sum(
                e["kind"] == "spawn" and e["scope"] != "quality" for e in store.events
            ),
            "skipped": sum(
                any(report["outcome"] == "skipped" for report in phases.values())
                for unit in runner.state.units.values()
                for phases in unit["nodes"].values()
            ),
            "xfail": 0,
            "limitations": [
                "REVIEWED_PARTITION_AND_INPUT_CLOSURE_REQUIRED",
                "NO_ESCAPED_DESCENDANTS_ASSURANCE",
                "SAME_OS_OWNER_OUTSIDE_TRUST",
            ],
            "github_ci_verified": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "resume", "reconcile"))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--approved-profile-sha256", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--pause-after-units", type=int)
    parser.add_argument("--disposition", type=Path)
    parser.add_argument("--approved-disposition-sha256")
    options = parser.parse_args()
    requested = [False]
    previous_handler: Any = None
    signal_number = getattr(signal, "SIGUSR1", None)

    def request_pause(signum: int, frame: Any) -> None:
        requested[0] = True

    try:
        if signal_number is not None:
            previous_handler = signal.signal(signal_number, request_pause)
        value = profile(options.profile, options.approved_profile_sha256)
        if options.pause_after_units is not None and options.pause_after_units < 0:
            raise GateBlocked("PAUSE_COUNT_INVALID")
        recovery = None
        if options.action == "reconcile":
            if (
                options.disposition is None
                or options.approved_disposition_sha256 is None
            ):
                raise GateBlocked("OWNER_DISPOSITION_REQUIRED")
            raw = _read(options.disposition, 8192)
            if hashlib.sha256(raw).hexdigest() != options.approved_disposition_sha256:
                raise GateBlocked("OWNER_DISPOSITION_FILE_PIN")
            permit = decode(raw)
            recovery = (permit, digest(permit))
        elif (
            options.disposition is not None
            or options.approved_disposition_sha256 is not None
        ):
            raise GateBlocked("UNEXPECTED_OWNER_DISPOSITION")
        result = execute(
            value,
            options.approved_profile_sha256,
            options.evidence,
            resume=options.action != "start",
            pause_after=options.pause_after_units,
            pause_requested=lambda: requested[0],
            recovery=recovery,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        GateBlocked,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.SubprocessError,
    ):
        # Never expose input text, exception messages, paths or report payloads.
        print('{"status":"BLOCKED","automatic_retry":false}')
        return 1
    finally:
        if previous_handler is not None and signal_number is not None:
            signal.signal(signal_number, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
