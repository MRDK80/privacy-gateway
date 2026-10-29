"""Isolated cumulative review with original-launch consumption and native sources.

No final effects, owner-pin authority, or config8 factory integration are supplied
by this helper. Its caller must hold the original seed lock and authority guard.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tools import agent_final_preparation_records as records
from tools import agent_gate
from tools import agent_orchestrate as workflow
from tools.agent_continuation import digest, exact
from tools.agent_continuation_backend import SettingsAdapter, validate_completion
from tools.agent_continuation_store import Store as SourceStore
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_epic_runtime_cli import secure_directory
from tools.agent_final_preparation_role import RoleCounter


def review_record_fact(raw: Any, identity: dict[str, Any]) -> dict[str, Any]:
    """Normalize source shape/identity only; does not admit source authority."""
    source = exact(
        raw,
        {
            "identity",
            "request",
            "policy",
            "session_id",
            "output",
            "settings_source",
        },
    )
    if source["identity"] != identity:
        raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
    return source


def completed_reservation_fact(
    source: dict[str, Any],
    identity: dict[str, Any],
    contract: workflow.TaskContract,
    reservation: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Derive verdict/seal only after the caller's original source checks."""
    if workflow._validate_verdict(
        source["output"], contract, identity["roadmap_sha"]
    ) not in {"PASS", "PASS_WITH_NOTES"}:
        raise PhaseBlocked("FINAL_DIFF_REVIEW_REQUIRED")
    completed = records.seal(
        {
            **reservation,
            "status": "COMPLETED",
            "settings_digest": digest(receipt),
            "output_digest": digest(source["output"]),
        }
    )
    return completed


def environment_links_fact(
    value: dict[str, Any],
    counter_identity: dict[str, Any],
    reservation: dict[str, Any] | None,
    source: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    """Compare already parsed facts; neither reads nor authorizes sources."""
    if reservation is None or any(
        value[key] != expected
        for key, expected in {
            **counter_identity,
            "preparation_identity_digest": digest(identity),
            "input_digest": reservation["input_digest"],
            "review_source_digest": digest(source),
        }.items()
    ):
        raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedAggregate:
    request: dict[str, Any]
    policy: dict[str, str]
    payload: dict[str, Any]
    session_id: str
    version: str
    executable: Path
    checksum: str


def learning_request_digest_fact(raw: Any) -> str:
    """Closed retained request syntax only, never learning-source authority."""
    from tools.agent_epic_runtime_cli import _final_preseed_request_variant

    _, commitment = _final_preseed_request_variant(raw)
    if commitment is None:
        raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
    return commitment


class AggregateReview:
    def __init__(self, counter: RoleCounter, adapter: SettingsAdapter) -> None:
        if (
            type(adapter) is not SettingsAdapter
            or adapter.root != counter.store.root
            or adapter.state != counter.store.directory
            or adapter.store.store.directory
            != counter.store.directory / "role-settings"
            or adapter.guard is not counter.store.guard
        ):
            records.invalid()
        self.counter, self.adapter = counter, adapter
        self.directory = counter.store.directory / "sources"
        self.key = "review-" + digest(counter.identity)

    def _git(self, *args: str) -> str:
        value = subprocess.run(
            ["git", *args],
            cwd=self.counter.store.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if value.returncode:
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_INVALID")
        return value.stdout.strip()

    def _identity(self) -> dict[str, Any]:
        self.counter.store.assert_owned()
        profile = self.counter.store.owner
        main = self._git("rev-parse", "refs/heads/main")
        roadmap = self._git("rev-parse", "refs/heads/" + profile["roadmap_ref"])
        if (
            self._git("status", "--porcelain=v1")
            or self._git("rev-parse", "HEAD") != roadmap
        ):
            raise PhaseBlocked("FINAL_REVIEW_TREE_DIRTY")
        for value in (main, roadmap):
            records.hash_value(value, sha=True)
        return {
            **self.counter.identity,
            "main_sha": main,
            "roadmap_sha": roadmap,
            "tree_sha": self._git("rev-parse", roadmap + "^{tree}"),
            "policy_sha": profile["policy_sha"],
            "backend_digest": digest(self._backend_original()),
        }

    def _store(self) -> SourceStore:
        secure_directory(self.directory, self.counter.store.root)
        return SourceStore(self.directory, self.counter.store.root)

    def _contract(self, identity: dict[str, Any]) -> workflow.TaskContract:
        paths = tuple(
            self._git(
                "diff", "--name-only", identity["main_sha"], identity["roadmap_sha"]
            ).splitlines()
        )
        if not paths:
            raise PhaseBlocked("FINAL_DIFF_EMPTY")
        return workflow.TaskContract(
            issue=identity["epic"],
            epic=identity["epic"],
            acceptance_criteria=(
                "Review cumulative roadmap diff, security invariants "
                "and final delivery contract",
            ),
            base_ref="main",
            base_sha=identity["main_sha"],
            head_ref=self.counter.store.owner["roadmap_ref"],
            allowed_paths=paths,
            max_repair_iterations=0,
            policy_sha=identity["policy_sha"],
        )

    def _request(
        self, identity: dict[str, Any], gate: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, str], workflow.TaskContract]:
        contract = self._contract(identity)
        cumulative = self._git(
            "diff", "--no-ext-diff", identity["main_sha"], identity["roadmap_sha"]
        )
        if len(cumulative) > 200000:
            raise PhaseBlocked("FINAL_DIFF_TOO_LARGE")
        if (
            not agent_gate.gate_evidence_is_passing(gate)
            or gate["snapshot"]["tree_hash"] != identity["tree_sha"]
        ):
            raise PhaseBlocked("FINAL_LOCAL_GATE_FAILED")
        request = {
            "issue": contract.issue,
            "acceptance_criteria": list(contract.acceptance_criteria),
            "base_sha": contract.base_sha,
            "head_sha": identity["roadmap_sha"],
            "repair_iteration": 0,
            "gate_evidence": gate,
            "reviewed_state": {"snapshot_commit": gate["snapshot"]["snapshot_commit"]},
            "diff": cumulative,
            "contract": {
                "issue": contract.issue,
                "epic": contract.epic,
                "acceptance_criteria": list(contract.acceptance_criteria),
                "base_ref": contract.base_ref,
                "base_sha": contract.base_sha,
                "head_ref": contract.head_ref,
                "allowed_paths": list(contract.allowed_paths),
                "policy_sha": contract.policy_sha,
                "permissions": asdict(contract.permissions),
                "max_repair_iterations": 0,
                "remaining_repair_iterations": 0,
                "max_minutes": 60,
                "task_class": "final-review",
            },
        }
        policy = workflow._load_trusted_policy(self.counter.store.root, contract)
        return request, policy, contract

    def _backend_original(self) -> dict[str, Any]:
        return self.adapter.owner

    def _settings_original(self, invocation: str) -> dict[str, Any] | None:
        return self.adapter.store.load(invocation)

    def _validate(
        self, raw: Any, identity: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        source = review_record_fact(raw, identity)
        request, policy, contract = self._request(
            identity, source["request"]["gate_evidence"]
        )
        if request != source["request"] or policy != source["policy"]:
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
        payload = {
            **request,
            "trusted_policy": policy,
            "session_id": source["session_id"],
        }
        invocation = digest(payload)
        original = self._settings_original(invocation)
        if original is None or original != source["settings_source"]:
            raise PhaseBlocked("FINAL_REVIEW_SETTINGS_MISSING")
        original = exact(original, {"receipt", "terminal"})
        receipt = validate_completion(
            original["receipt"],
            original["terminal"],
            self._backend_original(),
            role="controller",
            invocation=invocation,
            output=source["output"],
            observed_exit=original["terminal"]["adapter_exit_code"],
            reasoning="low",
        )
        state, reservation = self.counter.read()
        if state is None or reservation is None or reservation["status"] == "UNKNOWN":
            raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
        if (
            reservation["input_digest"] != invocation
            or reservation["preparation_identity_digest"] != digest(identity)
            or reservation["backend_digest"] != digest(self._backend_original())
        ):
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
        completed = completed_reservation_fact(
            source, identity, contract, reservation, receipt
        )
        self._environment(source, identity)
        return source, completed

    def _environment(
        self, source: dict[str, Any], identity: dict[str, Any]
    ) -> dict[str, Any]:
        self.counter.store.assert_owned()
        raw = self._store().load(
            "environment-" + digest(self.counter.identity),
            cap=self.counter.store.owner["max_artifact_bytes"],
        )
        value = records.aggregate_environment(raw)
        _, reservation = self.counter.read()
        return environment_links_fact(
            value, self.counter.identity, reservation, source, identity
        )

    def _executable_digest(self, executable: Path) -> str:
        try:
            return hashlib.sha256(executable.read_bytes()).hexdigest()
        except OSError:
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_INVALID") from None

    def _launcher(self) -> tuple[str, Path, str]:
        try:
            version = records.launcher_version(platform.python_version())
        except ValueError:
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_INVALID") from None
        try:
            executable = Path(sys.executable).resolve(strict=True)
        except (OSError, RuntimeError):
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_INVALID") from None
        checksum = self._executable_digest(executable)
        return version, executable, checksum

    def _verify(self, raw: Any, identity: dict[str, Any]) -> dict[str, Any]:
        source, completed = self._validate(raw, identity)
        self.counter.store.write(self.counter.key, completed)
        return source

    def review(self, *, started_at: int, deadline_at: int) -> dict[str, Any]:
        identity = self._identity()
        state, reservation = self.counter.read()
        if self.directory.exists():
            source = self._store().load(
                self.key, cap=self.counter.store.owner["max_artifact_bytes"]
            )
            if source is not None:
                return self._verify(source, identity)
        if state is not None and state["calls_consumed"]:
            raise PhaseBlocked("FINAL_ROLE_CALL_SPENT")
        prepared = self._prepare_new_review(identity)
        return self._dispatch_review(
            identity, prepared, started_at=started_at, deadline_at=deadline_at
        )

    def _prepare_new_review(self, identity: dict[str, Any]) -> _PreparedAggregate:
        contract = self._contract(identity)
        with agent_gate.trusted_snapshot_session(
            root=self.adapter.root,
            base_sha=identity["main_sha"],
            allowed_paths=contract.allowed_paths,
        ) as session:
            agent_gate.verify_environment(session)
            version, executable, checksum = self._launcher()
            gate = agent_gate.build_evidence(session, agent_gate.run_profile(session))
        request, policy, _ = self._request(identity, gate)
        session_id = "final-controller-" + uuid.uuid4().hex
        payload = {**request, "trusted_policy": policy, "session_id": session_id}
        from tools.agent_continuation import preflight_request

        preflight_request(payload, self.adapter.profile, role="controller")
        if self._identity() != identity:
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
        return _PreparedAggregate(
            request, policy, payload, session_id, version, executable, checksum
        )

    def _dispatch_review(
        self, identity: dict[str, Any], prepared: _PreparedAggregate,
        *, started_at: int, deadline_at: int,
    ) -> dict[str, Any]:
        request, policy, payload = prepared.request, prepared.policy, prepared.payload
        session_id = prepared.session_id
        version, executable, checksum = (
            prepared.version, prepared.executable, prepared.checksum
        )
        self.counter.reserve(
            preparation_identity_digest=digest(identity),
            input_digest=digest(payload),
            backend_digest=digest(self._backend_original()),
            started_at=started_at,
            deadline_at=deadline_at,
        )
        self.directory.mkdir(mode=0o700)
        try:
            output = self.adapter.review(request, policy, session_id)
        except Exception:
            self.counter.unknown()
            raise
        source = {
            "identity": identity,
            "request": request,
            "policy": policy,
            "session_id": session_id,
            "output": output,
            "settings_source": self.adapter.store.load(digest(payload)),
        }
        # Recheck the original resolved executable, not a later alias/resume value.
        if self._executable_digest(executable) != checksum:
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
        self.counter.store.assert_owned()
        self._store().save(
            self.key, source, cap=self.counter.store.owner["max_artifact_bytes"]
        )
        environment = records.aggregate_environment(
            records.seal(
                {
                    "schema_version": "1.0",
                    "kind": "FINAL_AGGREGATE_ENVIRONMENT",
                    **self.counter.identity,
                    "preparation_identity_digest": digest(identity),
                    "input_digest": digest(payload),
                    "review_source_digest": digest(source),
                    "launcher_python_version": version,
                    "launcher_executable_sha256": checksum,
                }
            )
        )
        self.counter.store.assert_owned()
        self._store().save(
            "environment-" + digest(self.counter.identity),
            environment,
            cap=self.counter.store.owner["max_artifact_bytes"],
        )
        return self._verify(source, self._identity())


class _LearningAggregateReview(AggregateReview):
    """Fixed commitment variant used only by the owned learning coordinator."""

    _learning_source_digest: str

    def _request(
        self, identity: dict[str, Any], gate: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, str], workflow.TaskContract]:
        request, policy, contract = super()._request(identity, gate)
        request["learning_source_digest"] = self._learning_source_digest
        learning_request_digest_fact(request)
        return request, policy, contract

    def _validate(
        self, raw: Any, identity: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        # Authenticate the actual original request/native chain BEFORE any packet
        # recollection. This field is syntax until super validates the chain.
        source = review_record_fact(raw, identity)
        self._learning_source_digest = learning_request_digest_fact(source["request"])
        return super()._validate(raw, identity)
