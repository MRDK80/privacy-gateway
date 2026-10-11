"""Trusted handover -> bounded workflow -> exact artifact, without delivery."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tools import agent_coordinator_handover as handover
from tools import agent_epic_loop as loop
from tools import agent_gate
from tools import agent_orchestrate as workflow
from tools.agent_quota_wait import bare_digest


def codex_role_adapter(
    *,
    policy_root: Path,
    root: Path,
    policy_sha: str,
    quota_state: Path | None = None,
    wait_scope: Any = None,
) -> workflow.CommandAdapter:
    """Use only an owner-installed policy copy, never task checkout code."""
    if os.name == "nt":
        raise loop.LoopError("RUNTIME_ADAPTER_UNSUPPORTED")
    if loop.SHA_RE.fullmatch(policy_sha) is None:
        raise loop.LoopError("UNTRUSTED_RUNTIME_COMMAND")
    if policy_root.is_symlink():
        raise loop.LoopError("UNTRUSTED_RUNTIME_COMMAND")
    private = policy_root.resolve()
    script = private / "tools" / "codex_adapter.py"
    if private.is_relative_to(root.resolve()) or any(
        (parent / ".git").is_file() or (parent / ".git" / "HEAD").is_file()
        for parent in (private, *private.parents)
    ):
        raise loop.LoopError("UNTRUSTED_RUNTIME_COMMAND")
    owner = getattr(os, "getuid", lambda: -1)()
    loop.CommandRuntimeAdapter._secure_ancestry(private, owner)
    for path in (
        private,
        private / "tools",
        script,
        private / "docs",
        private / "docs" / "schemas",
    ):
        try:
            info = path.lstat()
        except OSError as error:
            raise loop.LoopError("UNTRUSTED_RUNTIME_COMMAND") from error
        if (
            path.is_symlink()
            or info.st_uid != owner
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise loop.LoopError("UNTRUSTED_RUNTIME_COMMAND")
    if not script.is_file():
        raise loop.LoopError("UNTRUSTED_RUNTIME_COMMAND")
    for path in private.rglob("*"):
        info = path.lstat()
        if (
            path.is_symlink()
            or info.st_uid != owner
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise loop.LoopError("UNTRUSTED_RUNTIME_COMMAND")
        if path.name == "__pycache__" or path.suffix == ".pyc":
            raise loop.LoopError("UNTRUSTED_RUNTIME_COMMAND")
        if path.is_file():
            relative = path.relative_to(private).as_posix()
            try:
                expected = subprocess.run(
                    ["git", "show", f"{policy_sha}:{relative}"],
                    cwd=root,
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise loop.LoopError("UNTRUSTED_RUNTIME_COMMAND") from error
            if expected.returncode != 0 or expected.stdout != path.read_bytes():
                raise loop.LoopError("UNTRUSTED_RUNTIME_COMMAND")
    commands: list[tuple[str, ...]] = [
        (
            str(Path(sys.executable).absolute()),
            "-B",
            str(script),
            "--role",
            role,
            "--root",
            str(root.resolve()),
            "--schema-dir",
            str(private / "docs" / "schemas"),
        )
        for role in ("executor", "controller")
    ]
    commands[1] += ("--input-limit", "1000000")
    if quota_state is not None:
        from tools import agent_epic_runtime_cli as guards
        from tools import agent_quota as quota
        from tools import agent_quota_role as transport

        state = guards.secure_directory(quota_state, root)
        config = guards.private_json(state / "epic-runtime.json")
        if config.get("schema_version") != "5.0":
            raise loop.LoopError("QUOTA_PROFILE_REQUIRED")
        mandate = guards.private_json(state / "mandate.json")
        owner_profile = quota.profile(
            guards.quota_profile_guard(state, config, mandate)
        )
        if owner_profile["policy_sha"] != policy_sha:
            raise loop.LoopError("POLICY_PROVENANCE_MISMATCH")

        def guard() -> None:
            guards.managed_launch_guard(state, root)
            fresh_config = guards.private_json(state / "epic-runtime.json")
            fresh_mandate = guards.private_json(state / "mandate.json")
            if fresh_config != config or fresh_mandate != mandate:
                raise loop.LoopError("QUOTA_BINDING_CHANGED")
            fresh_owner = guards.quota_profile_guard(state, config, mandate)
            if fresh_owner != owner_profile:
                raise loop.LoopError("QUOTA_APPROVAL_MISMATCH")

        def on_interruption(event: dict[str, Any]) -> None:
            from tools import agent_epic_entry as entry
            from tools import agent_quota_source as source
            from tools.agent_quota_supervisor import prepare
            from tools.agent_quota_wait import QuotaWait

            guard()
            if wait_scope is None:
                raise transport.Interrupted(event)
            workflow.StateStore(state / "quota-role-events").save(
                event["invocation_digest"],
                event,
            )
            cursor_path = state / "session-control" / "cursor.json"
            cursor = guards.private_json(cursor_path) if cursor_path.exists() else None
            if cursor is not None and cursor.get("mode") == "FINAL":
                stage = wait_scope.active_stage
                if stage is None or not stage.startswith(
                    ("FINAL:", "RECONCILE:FINAL:")
                ):
                    raise transport.Interrupted(event)
            else:
                saved = entry._active_store(state, root).load()
                if saved is None:
                    raise transport.Interrupted(event)
                phase = saved["pending_phase"] or saved["phase"]
                stage = f"TASK:{saved['task']}:{phase}"
            identity = {
                "repository": mandate["repository"],
                "epic": mandate["epic"],
                "policy_sha": mandate["policy_sha"],
                "mandate_digest": config["approved_mandate_digest"],
                "quota_profile_digest": bare_digest(owner_profile),
                "wait_profile_digest": config["approved_wait_digest"],
                "runtime_config_digest": guards.launch_config_digest(
                    config,
                    guards.private_json(state / "runtime-adapter.json"),
                ),
                "limit_id": owner_profile["limit_id"],
                "source_digest": bare_digest(owner_profile["source"]),
            }
            candidate, _service = prepare(state, root, config)
            try:
                with wait_scope.context(stage) as reader:
                    current = QuotaWait(
                        reader,
                        owner_profile,
                        identity,
                        runtime_deadline=candidate["expires_at"],
                        now=lambda: int(time.time()),
                        guard=guard,
                    )
                    value = current.poll(
                        lambda: source.read(
                            owner_profile["source"],
                            owner_profile["limit_id"],
                            guard=guard,
                        )
                    )
            except (quota.QuotaError, loop.LoopError):
                raise transport.Interrupted(event) from None
            if value["status"] != "WAITING":
                raise transport.Interrupted(event)
            guard()
            raise loop.QuotaPaused(
                {
                    "exhausted_windows": value["exhausted_windows"],
                    "resets_at": value["resets_at"],
                    "next_check_at": value["next_check_at"],
                },
                interruption=event,
            )

        guard()
        for index in range(2):
            commands[index] += (
                "--quota-profile",
                str(state / "quota-profile.json"),
                "--approved-quota-digest",
                config["approved_quota_digest"],
            )
        return transport.CommandAdapter(
            commands[0],
            commands[1],
            root=root,
            timeout_seconds=3600,
            output_limit=200_000,
            backend_digest=bare_digest(owner_profile["source"]),
            guard=guard,
            on_interruption=on_interruption,
        )
    return workflow.CommandAdapter(
        commands[0], commands[1], root=root, timeout_seconds=3600, output_limit=200_000
    )


class ReviewedWorkflow:
    """Capture actual gate evidence and a validated independent review.

    The adapter must be owner-installed existing codex_adapter commands with
    ADR-225 executor isolation; never commands from task/model/checkpoint text.
    This component does not commit, push or create a PR.
    """

    def __init__(
        self,
        *,
        root: Path,
        directory: Path,
        value: Mapping[str, Any],
        approved_digest: str,
        adapter: workflow.AgentAdapter,
    ) -> None:
        self.root = root
        self.directory = directory
        self.value = value
        self.approved_digest = approved_digest
        self.adapter = adapter
        loop.CheckpointStore(directory, root)
        self.key = hashlib.sha256(
            json.dumps(
                {
                    key: value.get(key)
                    for key in (
                        "repository",
                        "epic",
                        "task",
                        "base_ref",
                        "base_sha",
                        "head_ref",
                        "trusted_policy",
                        "mandate_provenance",
                        "schema_version",
                    )
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self.artifact_store = workflow.StateStore(directory / "workflow-artifacts")

    def artifacts(self) -> Mapping[str, Any]:
        return self.artifact_store.load(self.key) or {}

    def controller_resume_available(self) -> bool:
        if getattr(self.adapter, "quota_resume_enabled", False) is not True:
            return False
        validated = handover.validate_handover(
            self.value,
            root=self.root,
            approved_digest=self.approved_digest,
            github=handover.GitHubCLI(),
        )
        return workflow.controller_resume_available(
            validated.contract,
            root=self.root,
            storage=workflow.StateStore(self.directory / "bounded-workflow"),
        )

    def run(self) -> Mapping[str, Any]:
        existing = self.artifacts()
        if existing:
            return existing
        validated = handover.validate_handover(
            self.value,
            root=self.root,
            approved_digest=self.approved_digest,
            github=handover.GitHubCLI(),
        )
        captured: dict[str, Any] = {}
        delegate = self.adapter
        contract = validated.contract

        class CaptureAdapter:
            quota_resume_enabled = (
                getattr(delegate, "quota_resume_enabled", False) is True
            )

            def execute(
                self, request: dict[str, Any], session_id: str
            ) -> dict[str, Any]:
                return delegate.execute(request, session_id)

            def review(
                self,
                request: dict[str, Any],
                trusted_policy: dict[str, str],
                session_id: str,
            ) -> dict[str, Any]:
                value = delegate.review(request, trusted_policy, session_id)
                verdict = workflow._validate_verdict(
                    value, contract, request["head_sha"]
                )
                gate = request.get("gate_evidence")
                reviewed = request.get("reviewed_state")
                snapshot = gate.get("snapshot") if isinstance(gate, Mapping) else None
                if (
                    verdict in {"PASS", "PASS_WITH_NOTES"}
                    and isinstance(gate, Mapping)
                    and agent_gate.gate_evidence_is_passing(gate)
                    and isinstance(snapshot, Mapping)
                    and reviewed == {"snapshot_commit": snapshot.get("snapshot_commit")}
                ):
                    captured.update(
                        {
                            "gate": dict(gate),
                            "environment": {
                                "python_version": platform.python_version()
                            },
                            "allowed_paths": list(contract.allowed_paths),
                            "review_receipt": {
                                "schema_version": "1.0",
                                "task": contract.issue,
                                "base_sha": contract.base_sha,
                                "head_ref": contract.head_ref,
                                "verdict": verdict,
                                "reviewed_state": dict(reviewed),
                            },
                        }
                    )
                    if contract.policy_sha is not None:
                        captured["review_receipt"]["schema_version"] = "2.0"
                        captured["review_receipt"]["policy_sha"] = contract.policy_sha
                else:
                    captured.clear()
                return value

        try:
            result = workflow.run(
                contract,
                root=self.root,
                storage=workflow.StateStore(self.directory / "bounded-workflow"),
                adapter=CaptureAdapter(),
            )
        except loop.QuotaPaused as error:
            saved = loop.CheckpointStore(self.directory, self.root).load()
            reservation = workflow.StateStore(self.directory / "bounded-workflow").load(
                workflow._run_key(contract)
            )
            event = error.interruption
            if (
                saved is None
                or event is None
                or reservation is None
                or reservation.get("invocation_digest")
                != event.get("invocation_digest")
                or reservation.get("role_pending") != event.get("role")
            ):
                raise loop.LoopError("QUOTA_INTERRUPTION_BINDING_INVALID") from None
            workflow.StateStore(self.directory).save(
                "quota-interruption",
                {
                    "schema_version": "1.0",
                    "checkpoint_identity_digest": bare_digest(
                        {key: saved[key] for key in loop.IDENTITY_KEYS}
                    ),
                    "phase": saved["pending_phase"] or saved["phase"],
                    "workflow_key": workflow._run_key(contract),
                    "event": event,
                },
            )
            raise
        if not captured or not (
            result.status in {"PASS", "PASS_WITH_NOTES"}
            or (
                result.status == "FAIL_ESCALATE"
                and result.machine_code == "EXTERNAL_GATE_PENDING"
            )
        ):
            raise loop.PhaseBlocked("WORKFLOW_REVIEW_NOT_VERIFIED")
        # Do not persist raw reports, prompts, controller notes or user input.
        self.artifact_store.save(self.key, captured)
        return captured
