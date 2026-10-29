"""Owner-only snapshot and reservation storage outside the task worktree."""

from __future__ import annotations

import json
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from tools import agent_continuation as contract
from tools import agent_orchestrate as workflow
from tools.agent_epic_runtime_cli import private_json, secure_directory


class WorkflowStore(workflow.StateStore):
    """Bound the existing canonical checkpoint without changing its schema."""

    def __init__(self, directory: Path, root: Path, *, cap: int = 65536) -> None:
        if not directory.exists() and not directory.is_symlink():
            secure_directory(directory.parent, root)
            directory.mkdir(mode=0o700)
        super().__init__(secure_directory(directory, root))
        self.root, self.cap = root, cap

    def load(self, run_key: str) -> dict[str, Any] | None:
        if re.fullmatch(r"[0-9a-f]{24}", run_key) is None:
            raise contract.ContinuationError("CONTINUATION_STORAGE_INVALID")
        secure_directory(self.directory, self.root)
        path = self._path(run_key)
        if not path.exists() and not path.is_symlink():
            return None
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_uid != self.directory.stat().st_uid
            or info.st_size > self.cap
        ):
            raise contract.ContinuationError("CONTINUATION_STORAGE_UNSAFE")
        return private_json(path)

    def save(self, run_key: str, value: dict[str, Any]) -> None:
        self.load(run_key)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        if len(encoded) > self.cap:
            raise contract.ContinuationError("CONTINUATION_RECORD_TOO_LARGE")
        super().save(run_key, value)


class Store:
    def __init__(self, directory: Path, root: Path) -> None:
        self.root = root.resolve()
        self.directory = secure_directory(directory, root)
        self.storage = workflow.StateStore(self.directory)

    def load(self, key: str, *, cap: int = 65536) -> dict[str, Any] | None:
        if (
            re.fullmatch(
                r"(?:handoff|reservation|receipt|review|checkpoint|environment)-[0-9a-f]{64}",
                key,
            )
            is None
        ):
            raise contract.ContinuationError("CONTINUATION_STORAGE_INVALID")
        path = self.directory / (key + ".json")
        if not path.exists() and not path.is_symlink():
            return None
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_uid != self.directory.stat().st_uid
            or info.st_size > cap
        ):
            raise contract.ContinuationError("CONTINUATION_STORAGE_UNSAFE")
        return private_json(path)

    def save(self, key: str, value: dict[str, Any], *, cap: int = 65536) -> None:
        self.load(key, cap=cap)
        if len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()) > cap:
            raise contract.ContinuationError("CONTINUATION_RECORD_TOO_LARGE")
        self.storage.save(key, value)

    def snapshot(self, task: workflow.TaskContract, invocation: str) -> dict[str, str]:
        contract.hash_value(invocation)
        paths = workflow._snapshot_changed_paths(self.root, task.base_sha)
        workflow._assert_snapshot_scope(paths, task.allowed_paths)
        for name in paths:
            path = self.root / name
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise contract.ContinuationError("CONTINUATION_SNAPSHOT_UNSAFE")
            if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
                raise contract.ContinuationError("CONTINUATION_SNAPSHOT_UNSAFE")
        base = self.directory / "snapshots"
        if not base.exists():
            base.mkdir(mode=0o700)
        secure_directory(base, self.root)
        target = base / invocation
        if target.exists() or target.is_symlink():
            raise contract.ContinuationError("CONTINUATION_SNAPSHOT_EXISTS")
        target.mkdir(mode=0o700)
        mirror = target / "objects.git"
        environment = workflow._snapshot_environment()

        def git(args: list[str], *, env: dict[str, str] = environment) -> str:
            try:
                result = subprocess.run(
                    ["git", *args],
                    cwd=self.root,
                    env=env,
                    capture_output=True,
                    timeout=120,
                    umask=0o077,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                raise contract.ContinuationError(
                    "CONTINUATION_SNAPSHOT_FAILED"
                ) from None
            if result.returncode:
                raise contract.ContinuationError("CONTINUATION_SNAPSHOT_FAILED")
            return result.stdout.decode("utf-8", "strict").strip()

        git(
            [
                "clone",
                "--quiet",
                "--bare",
                "--no-hardlinks",
                str(self.root),
                str(mirror),
            ]
        )
        env = workflow._snapshot_environment(
            GIT_DIR=str(mirror),
            GIT_WORK_TREE=str(self.root),
            GIT_INDEX_FILE=str(target / "index"),
        )
        git(["read-tree", task.base_sha], env=env)
        if paths:
            git(["add", "--all", "--", *paths], env=env)
        tree = git(["write-tree"], env=env)
        commit = git(
            [
                "commit-tree",
                tree,
                "-p",
                task.base_sha,
                "-m",
                "private unreviewed continuation snapshot",
            ],
            env=env,
        )
        # Owner-only every object/metadata file, with no worktree object/ref writes.
        for path in target.rglob("*"):
            if path.is_symlink():
                raise contract.ContinuationError("CONTINUATION_SNAPSHOT_UNSAFE")
            path.chmod(0o700 if path.is_dir() else 0o600)
        head = git(["rev-parse", "HEAD"])
        return {
            "base_sha": task.base_sha,
            "head_sha": head,
            "snapshot_sha": commit,
            "snapshot_digest": contract.digest(
                {"tree": tree, "commit": commit, "base_sha": task.base_sha}
            ),
        }

    def verify_snapshot(
        self,
        task: workflow.TaskContract,
        invocation: str,
        workspace: dict[str, str],
    ) -> None:
        """Reread private Git objects and current bytes; a summary is not evidence."""
        contract.hash_value(invocation)
        contract.exact(
            workspace, {"base_sha", "head_sha", "snapshot_sha", "snapshot_digest"}
        )
        for key in ("base_sha", "head_sha", "snapshot_sha"):
            contract.hash_value(workspace[key], sha=True)
        contract.hash_value(workspace["snapshot_digest"])
        target = self.directory / "snapshots" / invocation
        if not target.is_dir() or target.is_symlink():
            raise contract.ContinuationError("CONTINUATION_WORKSPACE_CHANGED")
        secure_directory(target, self.root)
        for path in (target, *target.rglob("*")):
            info = path.lstat()
            if (
                not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                or info.st_mode & 0o077
                or info.st_uid != self.directory.stat().st_uid
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
            ):
                raise contract.ContinuationError("CONTINUATION_SNAPSHOT_UNSAFE")
        mirror = target / "objects.git"
        environment = workflow._snapshot_environment()
        private_environment = workflow._snapshot_environment(
            GIT_OPTIONAL_LOCKS="0",
            GIT_DIR=str(mirror),
            GIT_WORK_TREE=str(self.root),
            GIT_INDEX_FILE=str(target / "index"),
        )

        def git(args: list[str], *, private: bool = False) -> bytes:
            try:
                result = subprocess.run(
                    ["git", *args],
                    cwd=self.root,
                    env=private_environment if private else environment,
                    capture_output=True,
                    timeout=120,
                    umask=0o077,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                raise contract.ContinuationError(
                    "CONTINUATION_WORKSPACE_CHANGED"
                ) from None
            if result.returncode:
                raise contract.ContinuationError("CONTINUATION_WORKSPACE_CHANGED")
            return result.stdout

        snapshot = workspace["snapshot_sha"]
        actual_head = git(["rev-parse", "HEAD"]).decode("ascii").strip()
        parent = (
            git(["show", "-s", "--format=%P", snapshot], private=True)
            .decode("ascii")
            .strip()
        )
        tree = (
            git(["rev-parse", snapshot + "^{tree}"], private=True)
            .decode("ascii")
            .strip()
        )
        if (
            workspace["base_sha"] != task.base_sha
            or actual_head != workspace["head_sha"]
            or parent != task.base_sha
            or workspace["snapshot_digest"]
            != contract.digest({"tree": tree, "commit": snapshot, "base_sha": parent})
        ):
            raise contract.ContinuationError("CONTINUATION_WORKSPACE_CHANGED")
        captured = (
            git(["diff", "--name-only", "-z", task.base_sha, snapshot], private=True)
            .decode("utf-8", "strict")
            .split("\0")
        )
        current = workflow._snapshot_changed_paths(self.root, task.base_sha)
        if set(current) != set(filter(None, captured)):
            raise contract.ContinuationError("CONTINUATION_WORKSPACE_CHANGED")
        workflow._assert_snapshot_scope(current, task.allowed_paths)
        git(["diff", "--quiet", snapshot, "--"], private=True)
