"""Read original owner-only runner facts; candidates never supply source paths."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools import agent_coordinator_handover as handover
from tools import agent_epic_loop as loop
from tools import agent_epic_runtime_cli as guards
from tools import agent_learning as c
from tools import agent_learning_planning_catalog as planning_catalog
from tools import agent_memory as memory
from tools import agent_orchestrate as workflow
from tools.agent_continuation import digest
from tools.agent_continuation_store import WorkflowStore
from tools.agent_orchestrate import TaskContract


@dataclass(frozen=True, repr=False)
class WorkflowFact:
    directory: Path
    contract: TaskContract
    state: dict[str, Any]
    memory: dict[str, Any]


class SourceReader:
    """Fixed managed sources and pinned code, distinct from lesson data validation."""

    def __init__(
        self,
        state: Path,
        root: Path,
        *,
        read: Callable[[Sequence[str]], str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.seed = guards.secure_directory(guards.managed_state(state), root)
        self.config = guards.learning_private_json(self.seed / "epic-runtime.json")
        if self.config.get("schema_version") not in {"7.0", "8.0"}:
            raise c.LearningError("LEARNING_PROFILE_REQUIRED")
        if set(self.config) != guards.epic_config_keys(self.config):
            raise c.LearningError("LEARNING_SOURCE_INVALID")
        self.policy = Path(self.config["policy_root"])
        if not Path(__file__).resolve().is_relative_to(self.policy.resolve()):
            raise c.LearningError("UNTRUSTED_RUNTIME_COMMAND")
        self.read = read
        self.planning_verifier: Any = None
        self._current_pending_native: tuple[int, str] | None = None
        self.planning_replay_stack: set[tuple[int, str, int]] = set()
        self.owner: dict[str, Any] = {}
        self.catalog: dict[str, Any] = {}
        self.planning_catalog: dict[str, Any] = {}
        self.mandate: dict[str, Any] = {}
        self.guard()

    def guard(self) -> None:
        config = guards.learning_private_json(self.seed / "epic-runtime.json")
        if config != self.config:
            raise c.LearningError("LEARNING_SOURCE_CHANGED")
        mandate = guards.learning_private_json(self.seed / "mandate.json")
        guards.bootstrap_authority(config, mandate, epic=True)
        guards.managed_launch_guard(self.seed, self.root)
        self._verify_policy_source(mandate["policy_sha"])
        owner = guards.learning_profile_guard(self.seed, config, mandate)
        if owner is None:
            raise c.LearningError("LEARNING_PROFILE_REQUIRED")
        owner = c.profile(owner)
        catalog = c.catalog(
            guards.learning_private_json(
                self.policy / "docs/schemas/agent-learning-catalog.json"
            )
        )
        planning = planning_catalog.catalog(
            guards.learning_private_json(
                self.policy / "docs/schemas/agent-learning-planning-catalog.json"
            )
        )
        if (
            planning_catalog.catalog_digest(catalog, planning)
            != owner["catalog_digest"]
        ):
            raise c.LearningError("LEARNING_SOURCE_CHANGED")
        if self.owner and (
            self.owner != owner
            or self.catalog != catalog
            or self.planning_catalog != planning
            or self.mandate != mandate
        ):
            raise c.LearningError("LEARNING_SOURCE_CHANGED")
        self.owner, self.catalog, self.mandate = owner, catalog, mandate
        self.planning_catalog = planning
        if handover._repository_identity(self.root) != owner["repository"]:
            raise c.LearningError("LEARNING_IDENTITY_INVALID")

    def _verify_policy_source(self, policy_sha: str) -> None:
        verify = (
            guards.verify_policy_config8_batch
            if self.config.get("schema_version") == "8.0"
            else guards.verify_policy
        )
        verify(
            self.policy,
            self.root,
            policy_sha,
            entry=self.policy / "tools/agent_epic_runtime_cli.py",
        )

    def _source_tasks(self) -> tuple[int, ...]:
        return tuple(self.config["approved_order"])

    def task_directory(self, task: int) -> Path:
        c.integer(task, 2147483647, 1)
        if task not in self._source_tasks():
            raise c.LearningError("LEARNING_TASK_UNAPPROVED")
        seed = guards.learning_private_json(self.seed / "epic-loop-checkpoint.json")
        directory = (
            self.seed if task == seed["task"] else self.seed / "tasks" / str(task)
        )
        if not directory.exists() and not directory.is_symlink():
            raise c.LearningError("LEARNING_SOURCE_MISSING")
        guards.secure_directory(directory, self.root)
        return directory

    @contextmanager
    def ownership(self, directory: Path) -> Iterator[None]:
        guards.secure_directory(directory, self.root)
        try:
            loop.RunnerLock.current_owned(directory)
        except loop.LoopError:
            with loop.RunnerLock(directory):
                yield
        else:
            yield

    def _json(self, path: Path) -> dict[str, Any]:
        if not path.exists() and not path.is_symlink():
            raise c.LearningError("LEARNING_SOURCE_MISSING")
        return guards.learning_private_json(
            path, self.owner["limits"]["max_record_bytes"]
        )

    def contract(self, task: int) -> TaskContract:
        directory = self.task_directory(task)
        config = self._json(directory / "runtime-task.json")
        if config.get("schema_version") != "3.0":
            raise c.LearningError("LEARNING_SOURCE_INVALID")
        guards.bootstrap_authority(config, self.mandate, epic=False)
        guards.learning_profile_guard(directory, config, self.mandate)
        value = self._json(directory / "handover.json")
        if (
            set(value) != handover.REQUIRED_TOP_LEVEL | handover.OPTIONAL_TOP_LEVEL
            or value.get("schema_version") != "2.0"
            or handover.handover_digest(value) != config.get("approved_handover_digest")
            or value.get("approval")
            != {"plan_digest": config.get("approved_handover_digest")}
            or (value.get("repository"), value.get("epic"), value.get("task"))
            != (self.owner["repository"], self.owner["epic"], task)
            or value.get("trusted_policy")
            != {"source": "pinned_policy_sha", "policy_sha": self.owner["policy_sha"]}
            or value.get("mandate_provenance")
            != {
                "schema_version": "3.0",
                "digest": self.owner["mandate_digest"],
                "policy_sha": self.owner["policy_sha"],
            }
        ):
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        if value.get("permissions") != {
            key: False for key in handover.DENIED_ACTIONS
        } or not set(handover.DENIED_ACTIONS).issubset(
            value.get("forbidden_actions", [])
        ):
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        budgets = c.exact(
            value["budgets"],
            {"max_minutes", "max_repair_iterations", "max_report_chars"},
        )
        c.integer(budgets["max_minutes"], 240, 1)
        c.integer(budgets["max_repair_iterations"], 2)
        c.integer(budgets["max_report_chars"], handover.MAX_REPORT_CHARS, 1)
        for key in ("base_sha", "head_sha"):
            c.hash_value(value[key], size=40)
        if (
            not isinstance(value["base_ref"], str)
            or value["base_ref"] != self.owner["roadmap_ref"]
            or not isinstance(value["head_ref"], str)
            or value["head_ref"].startswith("roadmap/")
        ):
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        paths, criteria = value["allowed_paths"], value["acceptance_criteria"]
        if (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(p, str) for p in paths)
            or not isinstance(criteria, list)
            or not criteria
            or not all(isinstance(p, str) for p in criteria)
        ):
            raise c.LearningError("LEARNING_SOURCE_INVALID")
        grant = self.mandate["task_grants"].get(str(task))
        if not isinstance(grant, dict) or not set(paths).issubset(
            grant["allowed_paths"]
        ):
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        return TaskContract(
            issue=task,
            epic=self.owner["epic"],
            acceptance_criteria=tuple(criteria),
            base_ref=value["base_ref"],
            base_sha=value["base_sha"],
            head_ref=value["head_ref"],
            allowed_paths=tuple(paths),
            task_class=value["task_class"],
            max_minutes=budgets["max_minutes"],
            max_repair_iterations=budgets["max_repair_iterations"],
            max_report_chars=budgets["max_report_chars"],
            delivery_criterion_indices=tuple(value["delivery_criterion_indices"]),
            policy_sha=self.owner["policy_sha"],
        )

    def _memory(
        self, directory: Path, state: dict[str, Any], contract: TaskContract
    ) -> dict[str, Any]:
        import os
        import stat

        path = directory / "agent-memory/retrospectives.jsonl"
        try:
            if any(parent.is_symlink() for parent in (path, *path.parents)):
                raise c.LearningError("LEARNING_SOURCE_INVALID")
            if not path.exists():
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            guards.secure_directory(directory, self.root)
            guards.secure_directory(path.parent, self.root)
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW"))
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                maximum = self.owner["limits"]["max_store_bytes"]
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_mode & 0o077
                    or info.st_uid != self.seed.stat().st_uid
                    or info.st_size > maximum
                ):
                    raise c.LearningError("LEARNING_SOURCE_INVALID")
                data = stream.read(maximum + 1)
            if len(data) > maximum or not data.endswith(b"\n"):
                raise c.LearningError("LEARNING_SOURCE_INVALID")
            matches: list[dict[str, Any]] = []
            for line in data.splitlines():
                if len(line) > self.owner["limits"]["max_record_bytes"]:
                    raise c.LearningError("LEARNING_SOURCE_INVALID")
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise c.LearningError("LEARNING_SOURCE_INVALID")
                memory.validate_record(item)
                if item["record_id"] == state["run_id"]:
                    matches.append(item)
            if len(matches) != 1:
                raise c.LearningError("LEARNING_SOURCE_MISMATCH")
            item = matches[0]
            if (
                item["task_issue"] != contract.issue
                or item["epic_issue"] != contract.epic
                or item["base_sha"] != contract.base_sha
                or item["task_class"] != contract.task_class
                or item["repair_loops"] != state["repair_iterations"]
                or any(
                    item[k] != state[k]
                    for k in (
                        "executor_calls",
                        "controller_calls",
                        "findings",
                        "verdicts",
                        "failures",
                    )
                )
            ):
                raise c.LearningError("LEARNING_SOURCE_MISMATCH")
            return item
        except (guards.DeploymentError, OSError, ValueError, memory.MemoryError):
            raise c.LearningError("LEARNING_SOURCE_INVALID") from None

    def workflow(self, task: int, run_key: str | None = None) -> WorkflowFact:
        self.guard()
        directory = self.task_directory(task)
        with self.ownership(directory):
            contract = self.contract(task)
            key = workflow._run_key(contract)
            if run_key is not None and run_key != key:
                raise c.LearningError("LEARNING_SOURCE_MISMATCH")
            target = directory / "bounded-workflow"
            if not target.exists() and not target.is_symlink():
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            state = WorkflowStore(
                target, self.root, cap=self.owner["limits"]["max_record_bytes"]
            ).load(key)
            if state is None:
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            c.exact(
                state,
                {
                    "status",
                    "machine_code",
                    "repair_iterations",
                    "run_id",
                    "continuation_handoffs",
                    "terminal",
                    "started",
                    "executor_calls",
                    "controller_calls",
                    "findings",
                    "verdicts",
                    "failures",
                    "evidence",
                    "record_schema_version",
                },
            )
            if state["terminal"] is not True:
                raise c.LearningError("LEARNING_SOURCE_PENDING")
            if (
                not isinstance(state["status"], str)
                or state["status"] not in c.OUTCOMES
                or not isinstance(state["run_id"], str)
                or re.fullmatch(r"[0-9a-f]{32}", state["run_id"]) is None
                or not isinstance(state["started"], (int, float))
                or isinstance(state["started"], bool)
                or not math.isfinite(state["started"])
                or state["started"] < 0
                or state["record_schema_version"]
                != workflow.PUBLIC_RECORD_SCHEMA_VERSION
            ):
                raise c.LearningError("LEARNING_SOURCE_INVALID")
            for key, maximum in {
                "executor_calls": contract.max_repair_iterations + 1,
                "controller_calls": contract.max_repair_iterations + 1,
                "repair_iterations": contract.max_repair_iterations,
            }.items():
                c.integer(state[key], maximum)
            original = self._memory(directory, state, contract)
            return WorkflowFact(directory, contract, state, original)

    def _git(self, *argv: str) -> bytes:
        import subprocess

        try:
            result = subprocess.run(
                ["git", *argv],
                cwd=self.root,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            raise c.LearningError("LEARNING_SOURCE_INVALID") from None
        if result.returncode != 0:
            raise c.LearningError("LEARNING_SOURCE_INVALID")
        return result.stdout

    def historical_gate(
        self,
        gate: Any,
        contract: TaskContract,
        snapshot_path: Path,
        snapshot_digest: str,
    ) -> dict[str, Any]:
        """Verify retained immutable role objects, never today's worktree summary."""
        import hashlib
        import stat
        import subprocess

        from tools import agent_gate

        if not isinstance(gate, dict) or not agent_gate.gate_evidence_is_passing(gate):
            raise c.LearningError("LEARNING_GATE_REQUIRED")
        snapshot = c.exact(
            gate.get("snapshot"),
            {
                "base_sha",
                "snapshot_method",
                "snapshot_commit",
                "tree_hash",
                "diff_sha256",
                "provenance_complete",
            },
        )
        for name in ("base_sha", "snapshot_commit", "tree_hash"):
            c.hash_value(snapshot[name], size=40)
        c.hash_value(snapshot["diff_sha256"])
        if (
            snapshot["base_sha"] != contract.base_sha
            or snapshot["provenance_complete"] is not True
            or snapshot["snapshot_method"] != "commit-tree"
        ):
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        guards.secure_directory(snapshot_path, self.root)
        for path in (snapshot_path, *snapshot_path.rglob("*")):
            info = path.lstat()
            if (
                path.is_symlink()
                or info.st_mode & 0o077
                or info.st_uid != self.seed.stat().st_uid
                or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
            ):
                raise c.LearningError("LEARNING_SOURCE_INVALID")
        mirror, index = snapshot_path / "objects.git", snapshot_path / "index"
        environment = workflow._snapshot_environment(
            GIT_DIR=str(mirror),
            GIT_INDEX_FILE=str(index),
            GIT_WORK_TREE=str(self.root),
            GIT_OPTIONAL_LOCKS="0",
        )

        def git(*args: str) -> bytes:
            try:
                result = subprocess.run(
                    ["git", *args],
                    cwd=self.root,
                    env=environment,
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                raise c.LearningError("LEARNING_SOURCE_INVALID") from None
            if (
                result.returncode != 0
                or len(result.stdout) > self.owner["limits"]["max_store_bytes"]
            ):
                raise c.LearningError("LEARNING_SOURCE_INVALID")
            return result.stdout

        tree, base = snapshot["tree_hash"], contract.base_sha

        # commit-tree uses the pinned fixed identity/date and message. Hash the
        # Git object bytes without creating objects or refs in a read-only reader.
        def commit_id(message: str) -> str:
            raw = (
                f"tree {tree}\nparent {base}\n"
                "author privacy-gateway orchestrator "
                "<orchestrator@privacy-gateway.invalid> 0 +0000\n"
                "committer privacy-gateway orchestrator "
                "<orchestrator@privacy-gateway.invalid> 0 +0000\n\n" + message + "\n"
            ).encode()
            return hashlib.sha1(
                b"commit " + str(len(raw)).encode() + b"\0" + raw
            ).hexdigest()

        private_commit = commit_id("private unreviewed continuation snapshot")
        if snapshot["snapshot_commit"] != commit_id(
            agent_gate.SNAPSHOT_MESSAGE
        ) or snapshot_digest != digest(
            {"tree": tree, "commit": private_commit, "base_sha": base}
        ):
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        git("cat-file", "-e", private_commit + "^{commit}")
        if git("rev-parse", private_commit + "^{tree}").decode().strip() != tree:
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        git("diff", "--cached", "--quiet", "--no-ext-diff", tree, "--")
        actual_diff = git("diff", "--binary", "--no-ext-diff", base, tree)
        if hashlib.sha256(actual_diff).hexdigest() != snapshot["diff_sha256"]:
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        paths = git("diff", "--name-only", "-z", base, tree).decode().split("\0")
        workflow._assert_snapshot_scope([p for p in paths if p], contract.allowed_paths)
        return dict(gate)

    def controller(self, task: int, invocation: str | None = None) -> dict[str, Any]:
        """Historical completion chain; no current-task time budget is refunded."""
        fact = self.workflow(task)
        if fact.state["controller_calls"] == 0:
            raise c.LearningError("LEARNING_SOURCE_MISSING")
        _, chosen = self._completed_role_chain(task, fact.contract, fact.state)
        if chosen is None or (
            invocation is not None and invocation != chosen["invocation_digest"]
        ):
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        return chosen

    @contextmanager
    def current_pending_prefix(self, task: int, invocation: str) -> Iterator[None]:
        """Read-only scope for exactly the current consumed native reservation."""
        self.guard()
        c.hash_value(invocation)
        if self._current_pending_native is not None:
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        contract = self.contract(task)
        state = self._json(
            self.task_directory(task)
            / "bounded-workflow"
            / (workflow._run_key(contract) + ".json")
        )
        self._current_pending_native = (task, invocation)
        try:
            self._completed_role_chain(
                task, contract, state, pending_invocation=invocation
            )
            yield
            self._completed_role_chain(
                task, contract, state, pending_invocation=invocation
            )
            self.guard()
        finally:
            self._current_pending_native = None

    def _completed_role_chain(
        self,
        task: int,
        contract: TaskContract,
        state: dict[str, Any],
        *,
        pending_invocation: str | None = None,
    ) -> tuple[dict[tuple[str, int], dict[str, Any]], dict[str, Any] | None]:
        """Reread completed originals, optionally proving one fresh pending boundary."""
        if pending_invocation is None and self._current_pending_native is not None:
            pending_task, pending_invocation = self._current_pending_native
            if task != pending_task:
                raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        if pending_invocation is not None:
            c.hash_value(pending_invocation)
            actual = self._json(
                self.task_directory(task)
                / "bounded-workflow"
                / (workflow._run_key(contract) + ".json")
            )
            if (
                actual != state
                or state.get("terminal") is not False
                or state.get("role_pending") not in {"executor", "controller"}
                or state.get("invocation_digest") != pending_invocation
                or state.get("machine_code") != "ROLE_RESERVED"
            ):
                raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        import hashlib
        import stat
        import subprocess
        from itertools import islice

        from tools import agent_continuation as continuation
        from tools.agent_continuation_backend import validate_completion

        def snapshot(inv: str, stage: str, expected_digest: str) -> None:
            target = (
                directory / "snapshots" / digest({"invocation": inv, "stage": stage})
            )
            if not target.exists() and not target.is_symlink():
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            guards.secure_directory(target, self.root)
            for path in (target, *target.rglob("*")):
                info = path.lstat()
                if (
                    path.is_symlink()
                    or info.st_mode & 0o077
                    or info.st_uid != self.seed.stat().st_uid
                    or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
                ):
                    raise c.LearningError("LEARNING_SOURCE_INVALID")
            environment = workflow._snapshot_environment(
                GIT_DIR=str(target / "objects.git"),
                GIT_INDEX_FILE=str(target / "index"),
                GIT_WORK_TREE=str(self.root),
                GIT_OPTIONAL_LOCKS="0",
            )

            def git(*args: str, input_data: bytes | None = None) -> bytes:
                try:
                    result = subprocess.run(
                        ["git", *args],
                        cwd=self.root,
                        env=environment,
                        input=input_data,
                        capture_output=True,
                        check=False,
                        timeout=30,
                    )
                except (OSError, subprocess.SubprocessError):
                    raise c.LearningError("LEARNING_SOURCE_INVALID") from None
                if (
                    result.returncode != 0
                    or len(result.stdout) > self.owner["limits"]["max_store_bytes"]
                ):
                    raise c.LearningError("LEARNING_SOURCE_INVALID")
                return result.stdout

            # Hash the original index in memory: write-tree would modify the
            # retained private objects and today's workspace is not history.
            trees: dict[tuple[bytes, ...], dict[bytes, tuple[bytes, bytes]]] = {(): {}}
            objects: dict[bytes, bytes] = {}
            for entry in git("ls-files", "--stage", "-z").split(b"\0"):
                if not entry:
                    continue
                header, separator, name = entry.partition(b"\t")
                fields = header.split(b" ")
                if (
                    separator != b"\t"
                    or len(fields) != 3
                    or fields[2] != b"0"
                    or fields[0] not in (b"100644", b"100755", b"120000", b"160000")
                    or re.fullmatch(b"[0-9a-f]{40}", fields[1]) is None
                ):
                    raise c.LearningError("LEARNING_SOURCE_INVALID")
                kind = b"commit" if fields[0] == b"160000" else b"blob"
                if fields[1] in objects and objects[fields[1]] != kind:
                    raise c.LearningError("LEARNING_SOURCE_INVALID")
                objects[fields[1]] = kind
                parts = tuple(name.split(b"/"))
                if any(part in (b"", b".", b"..") for part in parts):
                    raise c.LearningError("LEARNING_SOURCE_INVALID")
                for depth in range(1, len(parts)):
                    parent = parts[: depth - 1]
                    child = parts[depth - 1]
                    previous = trees[parent].get(child)
                    if previous is not None and previous[0] != b"40000":
                        raise c.LearningError("LEARNING_SOURCE_INVALID")
                    trees[parent][child] = (b"40000", b"")
                    trees.setdefault(parts[:depth], {})
                entries = trees[parts[:-1]]
                if parts[-1] in entries:
                    raise c.LearningError("LEARNING_SOURCE_INVALID")
                entries[parts[-1]] = (
                    fields[0],
                    bytes.fromhex(fields[1].decode("ascii")),
                )
            tree = ""
            for prefix in sorted(trees, key=len, reverse=True):
                entries = trees[prefix]
                names = sorted(
                    entries,
                    key=lambda name: (
                        name + (b"/" if entries[name][0] == b"40000" else b"")
                    ),
                )
                raw = b"".join(
                    entries[name][0] + b" " + name + b"\0" + entries[name][1]
                    for name in names
                )
                tree = hashlib.sha1(
                    b"tree " + str(len(raw)).encode() + b"\0" + raw
                ).hexdigest()
                if prefix:
                    trees[prefix[:-1]][prefix[-1]] = (b"40000", bytes.fromhex(tree))
            base = contract.base_sha
            raw_commit = (
                f"tree {tree}\nparent {base}\n"
                "author privacy-gateway orchestrator "
                "<orchestrator@privacy-gateway.invalid> 0 +0000\n"
                "committer privacy-gateway orchestrator "
                "<orchestrator@privacy-gateway.invalid> 0 +0000\n\n"
                "private unreviewed continuation snapshot\n"
            ).encode()
            commit = hashlib.sha1(
                b"commit " + str(len(raw_commit)).encode() + b"\0" + raw_commit
            ).hexdigest()
            if (
                digest({"tree": tree, "commit": commit, "base_sha": base})
                != expected_digest
                or git("cat-file", "commit", commit) != raw_commit
            ):
                raise c.LearningError("LEARNING_SOURCE_MISMATCH")
            # Read and hash each indexed original object in one bounded
            # read-only Git batch. Index IDs alone cannot prove retained bytes.
            keys = sorted(objects)
            raw_objects = git(
                "cat-file",
                "--batch",
                input_data=b"\n".join(keys) + (b"\n" if keys else b""),
            )
            offset = 0
            for key in keys:
                end = raw_objects.find(b"\n", offset)
                object_fields = raw_objects[offset:end].split(b" ")
                if (
                    end < offset
                    or len(object_fields) != 3
                    or object_fields[:2] != [key, objects[key]]
                    or re.fullmatch(b"[0-9]{1,8}", object_fields[2]) is None
                ):
                    raise c.LearningError("LEARNING_SOURCE_INVALID")
                size = int(object_fields[2])
                offset = end + 1
                end = offset + size
                if end >= len(raw_objects) or raw_objects[end : end + 1] != b"\n":
                    raise c.LearningError("LEARNING_SOURCE_INVALID")
                contents = raw_objects[offset:end]
                if (
                    hashlib.sha1(
                        objects[key] + b" " + object_fields[2] + b"\0" + contents
                    )
                    .hexdigest()
                    .encode()
                    != key
                ):
                    raise c.LearningError("LEARNING_SOURCE_MISMATCH")
                offset = end + 1
            if offset != len(raw_objects):
                raise c.LearningError("LEARNING_SOURCE_INVALID")
            git("diff", "--cached", "--quiet", "--no-ext-diff", tree, "--")
            paths = git("diff", "--name-only", "-z", base, tree).decode().split("\0")
            workflow._assert_snapshot_scope(
                [path for path in paths if path], contract.allowed_paths
            )

        directory = self.task_directory(task) / "continuation"
        c.hash_value(state.get("run_id"), size=32)
        started = state.get("started")
        if (
            not isinstance(started, (int, float))
            or isinstance(started, bool)
            or not math.isfinite(started)
            or started < 0
        ):
            raise c.LearningError("LEARNING_SOURCE_INVALID")
        for key, bound in {
            "executor_calls": contract.max_repair_iterations + 1,
            "controller_calls": contract.max_repair_iterations + 1,
            "repair_iterations": contract.max_repair_iterations,
        }.items():
            c.integer(state.get(key), bound)
        if not directory.exists() and not directory.is_symlink():
            if not state["executor_calls"] and not state["controller_calls"]:
                return {}, None
            raise c.LearningError("LEARNING_SOURCE_MISSING")
        with (
            self.ownership(self.task_directory(task)),
            self.ownership(directory / "ownership"),
            self.ownership(self.seed / "role-settings/ownership"),
        ):
            profile = guards.continuation_profile_guard(
                self.task_directory(task),
                self._json(self.task_directory(task) / "runtime-task.json"),
                self.mandate,
            )
            if profile is None:
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            c.integer(
                state.get("continuation_handoffs"), profile["limits"]["max_handoffs"]
            )
            backend = guards.role_backend_guard(self.seed, profile, None)
            runtime = guards.learning_private_json(self.seed / "runtime-adapter.json")
            expected = {
                "repository": self.owner["repository"],
                "epic": contract.epic,
                "task": task,
                "run_id": state["run_id"],
                "policy_sha": contract.policy_sha,
                "continuation_profile_digest": digest(profile),
                "launch_config_digest": guards.launch_config_digest(
                    self.config, runtime
                ),
            }
            maximum = 2 * (contract.max_repair_iterations + 1)
            paths = list(islice(directory.glob("reservation-*.json"), maximum + 1))
            if len(paths) > maximum:
                raise c.LearningError("LEARNING_SOURCE_INVALID")
            chain: dict[tuple[str, int], dict[str, Any]] = {}
            chosen: dict[str, Any] | None = None
            pending_seen: tuple[str, int] | None = None
            for path in paths:
                raw = self._json(path)
                row = continuation.reservation(
                    raw, expected, max_calls=contract.max_repair_iterations + 1
                )
                inv = row["invocation_digest"]
                if path.name != "reservation-" + inv + ".json":
                    raise c.LearningError("LEARNING_SOURCE_MISMATCH")
                role, ordinal = row["role"], row["ordinal"]
                checkpoint = self._json(directory / ("checkpoint-" + inv + ".json"))
                c.exact(
                    checkpoint,
                    {
                        "run_id",
                        "continuation_handoffs",
                        "started",
                        "repair_iterations",
                        "terminal",
                        "status",
                        "machine_code",
                        "executor_calls",
                        "controller_calls",
                        "findings",
                        "verdicts",
                        "failures",
                        "role_pending",
                        "controller_boundary",
                        "invocation_digest",
                    },
                )
                c.integer(
                    checkpoint["continuation_handoffs"],
                    profile["limits"]["max_handoffs"],
                )
                consumed: dict[str, Any] = {
                    key: checkpoint.get(key)
                    for key in (
                        "started",
                        "executor_calls",
                        "controller_calls",
                        "repair_iterations",
                    )
                }
                for key, bound in {
                    "executor_calls": contract.max_repair_iterations + 1,
                    "controller_calls": contract.max_repair_iterations + 1,
                    "repair_iterations": contract.max_repair_iterations,
                }.items():
                    c.integer(consumed[key], bound)
                if (
                    row["status"]
                    != ("RESERVED" if inv == pending_invocation else "COMPLETED")
                    or (role, ordinal) in chain
                    or row["contract_digest"]
                    != digest(workflow._contract_fields(contract))
                    or row["checkpoint_digest"] != digest(checkpoint)
                    or row["consumed_digest"] != digest(consumed)
                    or checkpoint["terminal"] is not False
                    or checkpoint["status"] != "running"
                    or checkpoint["machine_code"] != "ROLE_RESERVED"
                    or checkpoint.get("run_id") != state["run_id"]
                    or checkpoint.get("started") != state["started"]
                    or checkpoint["continuation_handoffs"]
                    > state["continuation_handoffs"]
                    or checkpoint.get("role_pending") != role
                    or checkpoint.get("invocation_digest") != inv
                    or checkpoint.get(role + "_calls") != ordinal
                    or any(
                        consumed[name] > state[name]
                        for name in ("executor_calls", "controller_calls")
                    )
                    or ordinal > state[role + "_calls"]
                    or consumed["repair_iterations"] > state["repair_iterations"]
                    or (
                        role == "executor"
                        and ordinal != consumed["repair_iterations"] + 1
                    )
                    or row["started_at"] != int(state["started"])
                    or row["deadline_at"]
                    != min(
                        int(state["started"]) + contract.max_minutes * 60,
                        self.mandate["expires_at"],
                        self.config["started_at"]
                        + self.mandate["limits"]["max_duration_seconds"],
                    )
                ):
                    raise c.LearningError("LEARNING_SOURCE_MISMATCH")
                if inv == pending_invocation:
                    settings_path = (
                        self.seed / "role-settings" / ("receipt-" + inv + ".json")
                    )
                    after_path = (
                        directory
                        / "snapshots"
                        / digest({"invocation": inv, "stage": "after"})
                    )
                    if (
                        pending_seen is not None
                        or checkpoint != state
                        or role != state["role_pending"]
                        or ordinal != state[role + "_calls"]
                        or row["snapshot_after"] is not None
                        or row["outcome_digest"] is not None
                        or settings_path.exists()
                        or settings_path.is_symlink()
                        or after_path.exists()
                        or after_path.is_symlink()
                    ):
                        raise c.LearningError("LEARNING_SOURCE_MISMATCH")
                    snapshot(inv, "before", row["snapshot_before"])
                    pending_seen = (role, ordinal)
                    continue
                source = c.exact(
                    self._json(
                        self.seed / "role-settings" / ("receipt-" + inv + ".json")
                    ),
                    {"receipt", "terminal"},
                )
                receipt = validate_completion(
                    source["receipt"],
                    source["terminal"],
                    backend,
                    role=role,
                    invocation=inv,
                    output=None,
                    observed_exit=0,
                    reasoning=["low", "medium", "high"][consumed["repair_iterations"]],
                    expected_output_digest=source["terminal"][
                        "validated_output_digest"
                    ],
                )
                if receipt["receipt_digest"] != row["outcome_digest"]:
                    raise c.LearningError("LEARNING_SOURCE_MISMATCH")
                snapshot(inv, "before", row["snapshot_before"])
                snapshot(inv, "after", row["snapshot_after"])
                chain[(role, ordinal)] = {
                    "reservation": row,
                    "checkpoint": checkpoint,
                    "source": source,
                    "receipt": receipt,
                }
                if role != "controller":
                    continue
                review = c.exact(
                    self._json(directory / ("review-" + inv + ".json")),
                    {"schema_version", "identity", "receipt_digest", "verdict"},
                )
                boundary = c.exact(
                    checkpoint.get("controller_boundary"),
                    {"schema_version", "contract", "head_sha", "gate"},
                )
                if (
                    review["schema_version"] != "1.0"
                    or review["identity"] != expected
                    or review["receipt_digest"] != receipt["receipt_digest"]
                    or digest(review["verdict"])
                    != source["terminal"]["validated_output_digest"]
                    or boundary["schema_version"] != "1.0"
                    or digest(boundary["contract"])
                    != digest(workflow._contract_fields(contract))
                ):
                    raise c.LearningError("LEARNING_SOURCE_MISMATCH")
                c.hash_value(boundary["head_sha"], size=40)
                status = workflow._validate_verdict(
                    review["verdict"], contract, boundary["head_sha"]
                )
                snapshot_path = (
                    directory
                    / "snapshots"
                    / digest({"invocation": inv, "stage": "after"})
                )
                gate = self.historical_gate(
                    boundary["gate"], contract, snapshot_path, row["snapshot_after"]
                )
                if ordinal == state["controller_calls"]:
                    chosen = {
                        "invocation_digest": inv,
                        "status": status,
                        "receipt_digest": receipt["receipt_digest"],
                        "gate": gate,
                        "source_digest": digest(
                            {
                                "reservation": row,
                                "checkpoint": checkpoint,
                                "source": source,
                                "review": review,
                            }
                        ),
                    }
            expected_chain = {
                (role, ordinal)
                for role in ("executor", "controller")
                for ordinal in range(1, state[role + "_calls"] + 1)
            }
            if pending_invocation is not None:
                if pending_seen is None:
                    raise c.LearningError("LEARNING_SOURCE_MISSING")
                expected_chain.remove(pending_seen)
            if set(chain) != expected_chain:
                raise c.LearningError("LEARNING_SOURCE_MISMATCH")
            return chain, chosen

    def gate(self, task: int) -> list[dict[str, Any]]:
        from tools.agent_learning_sources import gate

        return gate(self, task)

    def delivery(self, task: int) -> dict[str, Any]:
        from tools.agent_learning_delivery import DeliverySources

        return DeliverySources(self).ledger(task)

    def ci(self, task: int, phase: str) -> dict[str, Any]:
        from tools.agent_learning_delivery import DeliverySources

        source = self.delivery(task)
        if phase not in {"pr", "post-merge"}:
            raise c.LearningError("LEARNING_CI_SOURCE_INVALID")
        return DeliverySources(self).ci(
            source["head_sha"] if phase == "pr" else source["merge_sha"],
            event="pull_request" if phase == "pr" else "push",
        )

    def demo(self, task: int, session: Any) -> dict[str, Any]:
        from tools.agent_learning_sources import historical_runtime

        with self.ownership(self.task_directory(task)):
            runtime = historical_runtime(self, session, task)
            with runtime._stage():
                value = dict(runtime._demo(require_completion=True))
            saved = self._json(runtime.store.path)
            if not loop._valid_demo_receipt(value, saved["merge_sha"]):
                raise c.LearningError("LEARNING_DEMO_SOURCE_INVALID")
            return value
