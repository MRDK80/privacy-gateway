"""Prepare pinned private policy, explicitly install it, and check readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools import agent_epic_runtime_cli as guards
from tools.agent_coordinator_handover import handover_digest
from tools.agent_epic_loop import CommandRuntimeAdapter, validate_checkpoint

CANDIDATE_KEYS = {"schema_version", "repository", "policy_sha", "inventory_digest"}


class InstallError(Exception):
    """Only closed safe codes, never raw diagnostic text."""


def digest(value: Any) -> str:
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise InstallError("POLICY_REVISION_UNAVAILABLE") from None
    if result.returncode:
        raise InstallError("POLICY_REVISION_UNAVAILABLE")
    return result.stdout


def _inventory(root: Path, sha: str) -> dict[str, bytes]:
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise InstallError("POLICY_SHA_INVALID")
    if _git(root, "cat-file", "-t", sha).strip() != b"commit":
        raise InstallError("POLICY_REVISION_UNAVAILABLE")
    result: dict[str, bytes] = {}
    try:
        for item in _git(root, "ls-tree", "-rz", sha, "tools", "docs/schemas").split(
            b"\0"
        ):
            if not item:
                continue
            metadata, raw_path = item.split(b"\t", 1)
            mode, kind, blob = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8")
            if (
                mode not in {"100644", "100755"}
                or kind != "blob"
                or Path(path).is_absolute()
                or any(part in {"..", "."} for part in Path(path).parts)
            ):
                raise InstallError("POLICY_INVENTORY_INVALID")
            result[path] = _git(root, "cat-file", "blob", blob)
    except (ValueError, UnicodeError):
        raise InstallError("POLICY_INVENTORY_INVALID") from None
    required = {
        "tools/agent_epic_runtime_cli.py",
        "tools/agent_epic_runtime.py",
        "tools/codex_adapter.py",
    }
    if not required <= result.keys():
        raise InstallError("POLICY_INVENTORY_INVALID")
    return result


def _inventory_digest(files: dict[str, bytes]) -> str:
    return digest(
        {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
    )


def _private(path: Path, root: Path) -> Path:
    try:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise InstallError("UNSAFE_INSTALL_DIRECTORY")
        return guards.secure_directory(path, root)
    except (guards.DeploymentError, OSError, RuntimeError):
        raise InstallError("UNSAFE_INSTALL_DIRECTORY") from None


def _destination(path: Path, root: Path) -> None:
    if not path.is_absolute() or path.is_symlink():
        raise InstallError("UNSAFE_INSTALL_DIRECTORY")
    _private(path.parent, root)
    if path.exists():
        raise InstallError("INSTALL_DESTINATION_EXISTS")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


def _materialize(
    directory: Path, value: dict[str, Any], files: dict[str, bytes]
) -> None:
    for relative, data in files.items():
        parent = directory / "policy" / relative
        parent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        for ancestor in parent.parents:
            if ancestor == directory:
                break
            ancestor.chmod(0o700)
        _write(directory / "policy" / relative, data)
    _write(directory / "runtime.py", files["tools/agent_epic_runtime_cli.py"])
    _write(directory / "candidate.json", json.dumps(value, sort_keys=True).encode())


def _cleanup(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def prepare(root: Path, sha: str, repository: str, destination: Path) -> dict[str, str]:
    """Prepare data only; the digest is not an approval."""
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise InstallError("REPOSITORY_IDENTITY_INVALID")
    _destination(destination, root)
    files = _inventory(root, sha)
    value = {
        "schema_version": "1.0",
        "repository": repository,
        "policy_sha": sha,
        "inventory_digest": _inventory_digest(files),
    }
    staging = Path(tempfile.mkdtemp(prefix=".pgw-install-", dir=destination.parent))
    try:
        _materialize(staging, value, files)
        _validate(root, staging)
        _destination(destination, root)
        os.rename(staging, destination)
    except InstallError:
        raise
    except (OSError, ValueError):
        raise InstallError("INSTALL_FAILED") from None
    finally:
        _cleanup(staging)
    return {"status": "PREPARED", "candidate_digest": digest(value)}


def _validate(root: Path, candidate: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    _private(candidate, root)
    try:
        value = guards.private_json(candidate / "candidate.json")
        if set(value) != CANDIDATE_KEYS or value["schema_version"] != "1.0":
            raise InstallError("INSTALL_CANDIDATE_INVALID")
        if (
            not isinstance(value["repository"], str)
            or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"])
            is None
        ):
            raise InstallError("INSTALL_CANDIDATE_INVALID")
        if not isinstance(value["policy_sha"], str):
            raise InstallError("INSTALL_CANDIDATE_INVALID")
        files = _inventory(root, value["policy_sha"])
        if value["inventory_digest"] != _inventory_digest(files):
            raise InstallError("INSTALL_CANDIDATE_INVALID")
        entry = candidate / "runtime.py"
        # Verify entry as a regular owner-only file, without reading it as JSON.
        info = entry.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != guards.owner_uid()
            or info.st_mode & 0o077
        ):
            raise InstallError("INSTALL_CANDIDATE_INVALID")
        guards.verify_policy(
            candidate / "policy", root, value["policy_sha"], entry=entry
        )
        if {p.name for p in candidate.iterdir()} != {
            "policy",
            "runtime.py",
            "candidate.json",
        }:
            raise InstallError("INSTALL_CANDIDATE_INVALID")
        return value, files
    except (guards.DeploymentError, OSError, ValueError, TypeError):
        raise InstallError("INSTALL_CANDIDATE_INVALID") from None


def activate(
    root: Path, candidate: Path, destination: Path, approved_digest: str
) -> dict[str, str]:
    """Install exact externally approved bytes; never create runtime state."""
    value, files = _validate(root, candidate)
    expected = digest(value)
    if approved_digest != expected:
        raise InstallError("INSTALL_APPROVAL_MISMATCH")
    if destination.exists() or destination.is_symlink():
        current, _ = _validate(root, destination)
        if current != value:
            raise InstallError("INSTALL_DESTINATION_CONFLICT")
        return {"status": "NO_OP", "candidate_digest": expected}
    _destination(destination, root)
    staging = Path(tempfile.mkdtemp(prefix=".pgw-install-", dir=destination.parent))
    try:
        _materialize(staging, value, files)
        _validate(root, staging)
        _destination(destination, root)
        os.rename(staging, destination)
    except InstallError:
        raise
    except (OSError, ValueError):
        raise InstallError("INSTALL_FAILED") from None
    finally:
        _cleanup(staging)
    return {"status": "INSTALLED", "candidate_digest": expected}


def _namespace() -> None:
    try:
        executable = CommandRuntimeAdapter._bubblewrap()
        result = subprocess.run(
            [
                str(executable),
                "--die-with-parent",
                "--unshare-pid",
                "--ro-bind",
                "/",
                "/",
                "--proc",
                "/proc",
                "--",
                sys.executable,
                "-I",
                "-c",
                "raise SystemExit(0)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if result.returncode:
            raise InstallError("NAMESPACE_UNAVAILABLE")
    except Exception:
        raise InstallError("NAMESPACE_UNAVAILABLE") from None


def _github(repository: str, identity: str) -> None:
    for endpoint, field, expected in (
        ("user", ".login", identity),
        (f"repos/{repository}", ".full_name", repository),
    ):
        try:
            result = subprocess.run(
                ["gh", "api", endpoint, "--jq", field],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise InstallError("GITHUB_ACCESS_UNAVAILABLE") from None
        if result.returncode or len(result.stdout) > 4096:
            raise InstallError("GITHUB_ACCESS_UNAVAILABLE")
        if result.stdout.strip() != expected.encode():
            raise InstallError("GITHUB_IDENTITY_MISMATCH")


def readiness(
    root: Path,
    installation: Path,
    state: Path,
    *,
    epic: bool,
    github_identity: str,
    namespace_probe: Callable[[], None] = _namespace,
    github_probe: Callable[[str, str], None] = _github,
) -> dict[str, Any]:
    """Read-only checks; no candidate imports or runtime effects."""
    checks: list[dict[str, str]] = []

    def check(name: str, action: Callable[[], Any]) -> None:
        try:
            action()
        except Exception as error:
            code = (
                str(error)
                if isinstance(error, (InstallError, guards.DeploymentError))
                else "READINESS_CHECK_FAILED"
            )
            if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) is None:
                code = "READINESS_CHECK_FAILED"
            checks.append({"name": name, "status": "FAIL", "machine_code": code})
        else:
            checks.append({"name": name, "status": "PASS", "machine_code": "OK"})

    check("installation", lambda: _validate(root, installation))
    check("namespace", namespace_probe)
    check("interpreter", lambda: _interpreter())

    def runtime() -> None:
        value, _ = _validate(root, installation)
        _private(state, root)
        adapter = CommandRuntimeAdapter.load(
            state / "runtime-adapter.json", root, state
        )
        if epic != (adapter.epic_command is not None):
            raise InstallError("RUNTIME_PROFILE_MISMATCH")
        filename = "epic-runtime.json" if epic else "runtime-task.json"
        config = guards.private_json(state / filename)
        keys = guards.EPIC_CONFIG_KEYS if epic else guards.CONFIG_KEYS
        recovery = not epic and config.get("schema_version") == "2.0"
        if epic and (
            (state / "bootstrap-recovery.json").exists()
            or (
                (state / "runtime-task.json").exists()
                and guards.private_json(state / "runtime-task.json").get(
                    "schema_version"
                )
                == "2.0"
            )
        ):
            raise InstallError("RECOVERY_ONE_TASK_REQUIRED")
        if set(config) != keys | (
            {"approved_recovery_digest"} if recovery else set()
        ) or config.get("schema_version") not in ({"1.0"} if epic else {"1.0", "2.0"}):
            raise InstallError("RUNTIME_CONFIG_INVALID")
        if (
            any(
                type(config[key]) is not int or config[key] < 0
                for key in ("started_at", "task_iterations", "follow_up_issues")
            )
            or not isinstance(config["approved_order"], list)
            or any(
                type(task) is not int or task <= 0 for task in config["approved_order"]
            )
        ):
            raise InstallError("RUNTIME_CONFIG_INVALID")
        if (
            recovery
            and config["approved_recovery_digest"] is not None
            and (
                not isinstance(config["approved_recovery_digest"], str)
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}", config["approved_recovery_digest"]
                )
                is None
            )
        ):
            raise InstallError("RUNTIME_CONFIG_INVALID")
        if not recovery and not epic and (state / "bootstrap-recovery.json").exists():
            raise InstallError("RECOVERY_PROFILE_REQUIRED")
        mandate = guards.private_json(state / "mandate.json")
        guards.bootstrap_authority(config, mandate, epic=epic)
        if (
            mandate["repository"] != value["repository"]
            or mandate["policy_sha"] != value["policy_sha"]
            or Path(config["policy_root"]).absolute() != installation / "policy"
        ):
            raise InstallError("POLICY_PROVENANCE_MISMATCH")
        saved = validate_checkpoint(guards.private_json(state / "checkpoint.json"))
        if (
            saved["repository"] != mandate["repository"]
            or saved["epic"] != mandate["epic"]
        ):
            raise InstallError("CHECKPOINT_IDENTITY_MISMATCH")
        if (mandate["schema_version"] == "3.0") != (
            saved["schema_version"] == "3.0"
        ) or (
            saved["schema_version"] == "3.0"
            and saved["policy_sha"] != mandate["policy_sha"]
        ):
            raise InstallError("POLICY_PROVENANCE_MISMATCH")
        if epic:
            plans = guards.private_json(state / "approved-plans.json")
            if (
                set(plans) != {"schema_version", "initial_handover_digest", "tasks"}
                or plans["schema_version"] != "1.0"
                or not isinstance(plans["tasks"], dict)
                or digest(plans) != config["approved_plans_digest"]
            ):
                raise InstallError("APPROVED_PLANS_INVALID")
            approved_handover = plans["initial_handover_digest"]
        else:
            approved_handover = config["approved_handover_digest"]
        handover = guards.private_json(state / "handover.json")
        if handover_digest(handover) != approved_handover:
            raise InstallError("APPROVAL_MISMATCH")
        if any(
            handover.get(key) != saved[key]
            for key in (
                "repository",
                "epic",
                "task",
                "base_ref",
                "base_sha",
                "head_ref",
            )
        ):
            raise InstallError("HANDOVER_IDENTITY_MISMATCH")
        if saved["schema_version"] == "3.0" and (
            handover.get("schema_version") != "2.0"
            or handover.get("trusted_policy")
            != {"source": "pinned_policy_sha", "policy_sha": mandate["policy_sha"]}
        ):
            raise InstallError("POLICY_PROVENANCE_MISMATCH")
        for command in (
            adapter.live_command,
            *adapter.phase_commands.values(),
            *adapter.reconcile_commands.values(),
            *((adapter.epic_command,) if adapter.epic_command else ()),
        ):
            if (
                Path(command[1]).read_bytes()
                != (installation / "runtime.py").read_bytes()
            ):
                raise InstallError("UNTRUSTED_RUNTIME_COMMAND")
        github_probe(value["repository"], github_identity)

    check("runtime_and_github", runtime)
    return {
        "schema_version": "1.0",
        "status": "READY" if all(c["status"] == "PASS" for c in checks) else "BLOCKED",
        "checks": checks,
    }


def _interpreter() -> None:
    if sys.version_info < (3, 11) or not Path(sys.executable).is_file():
        raise InstallError("INTERPRETER_UNSUPPORTED")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "install", "readiness"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--policy-sha")
    parser.add_argument("--repository")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--approved-digest")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--github-identity")
    parser.add_argument("--epic", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "prepare" and args.policy_sha and args.repository:
            result = prepare(
                args.repository_root, args.policy_sha, args.repository, args.directory
            )
        elif args.command == "install" and args.candidate and args.approved_digest:
            result = activate(
                args.repository_root,
                args.candidate,
                args.directory,
                args.approved_digest,
            )
        elif args.command == "readiness" and args.state_dir and args.github_identity:
            result = readiness(
                args.repository_root,
                args.directory,
                args.state_dir,
                epic=args.epic,
                github_identity=args.github_identity,
            )
        else:
            raise InstallError("INSTALL_ARGUMENTS_INVALID")
    except Exception as error:
        code = str(error) if isinstance(error, InstallError) else "INSTALL_FAILED"
        result = {"status": "BLOCKED", "machine_code": code}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] != "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
