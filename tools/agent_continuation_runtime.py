"""Mechanically bound private role reservations and safe next-role handoffs."""

from __future__ import annotations

import stat
import time
from itertools import islice
from pathlib import Path
from typing import Any

from tools import agent_continuation as records
from tools import agent_epic_loop as loop
from tools import agent_epic_runtime_cli as guards
from tools import agent_orchestrate as workflow
from tools.agent_continuation_store import Store, WorkflowStore


class Continuation:
    def __init__(
        self,
        *,
        adapter: Any,
        contract: workflow.TaskContract,
        directory: Path,
        root: Path,
    ) -> None:
        self.adapter, self.contract = adapter, contract
        self.directory, self.root = directory, root
        self.owner = records.profile(adapter.profile)
        if (
            contract.epic != self.owner["epic"]
            or contract.base_ref != self.owner["roadmap_ref"]
            or contract.policy_sha != self.owner["policy_sha"]
        ):
            raise records.ContinuationError("CONTINUATION_PROFILE_CHANGED")
        guards.secure_directory(directory, root)
        private = directory / "continuation"
        if not private.exists() and not private.is_symlink():
            private.mkdir(mode=0o700)
        self.store = Store(private, root)
        self.workflow = WorkflowStore(directory / "bounded-workflow", root)
        self.lock = loop.RunnerLock(self.store.directory / "ownership")
        config = guards.private_json(adapter.state / "epic-runtime.json")
        runtime = guards.private_json(adapter.state / "runtime-adapter.json")
        self.launch_digest = guards.launch_config_digest(config, runtime)
        mandate = guards.private_json(adapter.state / "mandate.json")
        self.outer_deadline = min(
            mandate["expires_at"],
            config["started_at"] + mandate["limits"]["max_duration_seconds"],
        )
        self.original_provider_guard: Any = None

    def __enter__(self) -> Continuation:
        loop.RunnerLock.current_owned(self.directory)
        self.adapter.guard()
        ownership = self.store.directory / "ownership"
        if not ownership.exists() and not ownership.is_symlink():
            ownership.mkdir(mode=0o700)
        guards.secure_directory(ownership, self.root)
        lock_path = ownership / "epic-loop.lock"
        if lock_path.exists() or lock_path.is_symlink():
            info = lock_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != ownership.stat().st_uid
            ):
                raise records.ContinuationError("CONTINUATION_STORAGE_UNSAFE")
        self.lock.__enter__()
        self.original_provider_guard = self.adapter.delegate.guard
        self.adapter.delegate.guard = self.provider_guard
        return self

    def __exit__(self, *args: Any) -> None:
        self.adapter.delegate.guard = self.original_provider_guard
        self.lock.__exit__(*args)

    def provider_guard(self) -> None:
        self.original_provider_guard()
        self.check()
        state = self.workflow.load(workflow._run_key(self.contract))
        if state is None:
            raise records.ContinuationError("CONTINUATION_RESERVATION_REQUIRED")
        self.consumed(state)

    def check(self) -> None:
        loop.RunnerLock.current_owned(self.directory)
        self.lock.assert_owned(self.lock.directory)
        self.adapter.guard()

    def identity(self, state: dict[str, Any]) -> dict[str, Any]:
        return records.identity(
            {
                "repository": self.owner["repository"],
                "epic": self.contract.epic,
                "task": self.contract.issue,
                "run_id": state["run_id"],
                "policy_sha": self.contract.policy_sha,
                "continuation_profile_digest": records.digest(self.owner),
                "launch_config_digest": self.launch_digest,
            }
        )

    def validate_state(self, state: dict[str, Any]) -> None:
        self.identity(state)
        self.consumed(state)
        records.integer(
            state.get("continuation_handoffs", 0), self.owner["limits"]["max_handoffs"]
        )
        if type(state.get("terminal")) is not bool or state.get("role_pending") not in [
            None,
            "executor",
            "controller",
        ]:
            raise records.ContinuationError("CONTINUATION_STATE_INVALID")

    def consumed(self, state: dict[str, Any]) -> dict[str, Any]:
        started = state.get("started")
        if (
            not isinstance(started, (int, float))
            or isinstance(started, bool)
            or not 0 <= time.time() - started < self.contract.max_minutes * 60
            or time.time() >= self.outer_deadline
        ):
            raise records.ContinuationError("TIME_BUDGET_EXHAUSTED")
        for key, maximum in (
            ("executor_calls", self.contract.max_repair_iterations + 1),
            ("controller_calls", self.contract.max_repair_iterations + 1),
            ("repair_iterations", self.contract.max_repair_iterations),
        ):
            records.integer(state.get(key), maximum)
        return {
            k: state[k]
            for k in (
                "started",
                "executor_calls",
                "controller_calls",
                "repair_iterations",
            )
        }

    @staticmethod
    def snapshot_key(invocation: str, stage: str) -> str:
        return records.digest({"invocation": invocation, "stage": stage})

    def preflight(self, role: str, payload: dict[str, Any]) -> None:
        self.check()
        records.preflight_request(payload, self.owner, role=role)

    def reserve(
        self, role: str, payload: dict[str, Any], state: dict[str, Any]
    ) -> None:
        self.check()
        consumed = self.consumed(state)
        invocation = state["invocation_digest"]
        if invocation != records.digest(
            {k: v for k, v in payload.items() if k != "contract_object"}
        ):
            raise records.ContinuationError("CONTINUATION_BINDING_CHANGED")
        key = "reservation-" + invocation
        if self.store.load(key) is not None:
            raise records.ContinuationError("CONTINUATION_RESERVATION_CONFLICT")
        workspace = self.store.snapshot(
            self.contract, self.snapshot_key(invocation, "before")
        )
        value = {
            "schema_version": "1.0",
            "identity": self.identity(state),
            "contract_digest": records.digest(workflow._contract_fields(self.contract)),
            "role": role,
            "ordinal": state[role + "_calls"],
            "invocation_digest": invocation,
            "checkpoint_digest": records.digest(state),
            "started_at": int(consumed["started"]),
            "deadline_at": min(
                int(consumed["started"]) + self.contract.max_minutes * 60,
                self.outer_deadline,
            ),
            "consumed_digest": records.digest(consumed),
            "snapshot_before": workspace["snapshot_digest"],
            "snapshot_after": None,
            "status": "RESERVED",
            "outcome_digest": None,
        }
        records.reservation(
            value,
            self.identity(state),
            max_calls=self.contract.max_repair_iterations + 1,
        )
        self.store.save(
            "checkpoint-" + invocation, state,
            cap=self.owner["limits"]["max_record_bytes"],
        )
        self.store.save(key, value, cap=self.owner["limits"]["max_record_bytes"])

    def complete(
        self, role: str, state: dict[str, Any], output: dict[str, Any]
    ) -> dict[str, Any]:
        self.check()
        self.consumed(state)
        invocation = state["invocation_digest"]
        key = "reservation-" + invocation
        value = records.reservation(
            self.store.load(key),
            self.identity(state),
            max_calls=self.contract.max_repair_iterations + 1,
        )
        receipt_source = self.adapter.store.load(invocation)
        if not isinstance(receipt_source, dict):
            raise records.ContinuationError("CONTINUATION_REASONING_UNVERIFIED")
        from tools.agent_continuation_backend import validate_completion

        receipt = validate_completion(
            receipt_source["receipt"],
            receipt_source["terminal"],
            self.adapter.owner,
            role=role,
            invocation=invocation,
            output=output,
            observed_exit=0,
            reasoning=["low", "medium", "high"][state["repair_iterations"]],
        )
        if (
            value["status"] != "RESERVED"
            or value["role"] != role
            or value["checkpoint_digest"] != records.digest(state)
            or value["consumed_digest"] != records.digest(self.consumed(state))
        ):
            raise records.ContinuationError("CONTINUATION_BINDING_CHANGED")
        workspace = self.store.snapshot(
            self.contract, self.snapshot_key(invocation, "after")
        )
        completed = {
            **value,
            "status": "COMPLETED",
            "snapshot_after": workspace["snapshot_digest"],
            "outcome_digest": receipt["receipt_digest"],
        }
        self.store.save(key, completed, cap=self.owner["limits"]["max_record_bytes"])
        if role == "controller":
            self.store.save(
                "review-" + invocation,
                {
                    "schema_version": "1.0",
                    "identity": self.identity(state),
                    "receipt_digest": receipt["receipt_digest"],
                    "verdict": output,
                },
                cap=self.owner["limits"]["max_record_bytes"],
            )
        return {
            "invocation_digest": invocation,
            "workspace": workspace,
            "reservation_digest": records.digest(completed),
            "settings_receipt_digest": receipt["receipt_digest"],
            "checkpoint_digest": value["checkpoint_digest"],
        }

    def interrupted(self, state: dict[str, Any]) -> None:
        """Capture exact partial workspace; UNKNOWN never authorizes role replay."""
        self.check()
        invocation = state["invocation_digest"]
        key = "reservation-" + invocation
        value = records.reservation(
            self.store.load(key),
            self.identity(state),
            max_calls=self.contract.max_repair_iterations + 1,
        )
        workspace = self.store.snapshot(
            self.contract, self.snapshot_key(invocation, "interrupted")
        )
        self.store.save(
            key,
            {
                **value,
                "status": "UNKNOWN",
                "snapshot_after": workspace["snapshot_digest"],
            },
            cap=self.owner["limits"]["max_record_bytes"],
        )

    def resume_executor(self, state: dict[str, Any]) -> dict[str, Any] | None:
        self.check()
        proof = state.get("completed_executor")
        if proof is None or state.get("role_pending") is not None:
            return None
        records.exact(
            proof,
            {
                "invocation_digest",
                "workspace",
                "reservation_digest",
                "settings_receipt_digest",
                "policy_digest",
                "checkpoint_digest",
            },
        )
        self.consumed(state)
        invocation = proof["invocation_digest"]
        saved = records.reservation(
            self.store.load("reservation-" + invocation),
            self.identity(state),
            max_calls=self.contract.max_repair_iterations + 1,
        )
        source = self.adapter.store.load(invocation)
        if not isinstance(source, dict) or set(source) != {"receipt", "terminal"}:
            raise records.ContinuationError("CONTINUATION_REASONING_UNVERIFIED")
        from tools.agent_continuation_backend import validate_completion

        receipt, terminal = source["receipt"], source["terminal"]
        validate_completion(
            receipt,
            terminal,
            self.adapter.owner,
            role="executor",
            invocation=invocation,
            output=None,
            observed_exit=0,
            reasoning=["low", "medium", "high"][state["repair_iterations"]],
            expected_output_digest=terminal["validated_output_digest"],
        )
        if (
            saved["checkpoint_digest"] != proof["checkpoint_digest"]
            or saved["status"] != "COMPLETED"
            or saved["role"] != "executor"
            or saved["contract_digest"]
            != records.digest(workflow._contract_fields(self.contract))
            or saved["consumed_digest"] != records.digest(self.consumed(state))
            or saved["snapshot_after"] != proof["workspace"]["snapshot_digest"]
            or records.digest(saved) != proof["reservation_digest"]
            or saved["outcome_digest"] != proof["settings_receipt_digest"]
            or receipt.get("receipt_digest") != proof["settings_receipt_digest"]
            or receipt.get("outcome") != "COMPLETED"
            or receipt.get("backend_digest") != self.owner["backend_digest"]
            or receipt.get("invocation_digest") != invocation
            or receipt.get("terminal_digest") != records.digest(terminal)
            or receipt.get("receipt_digest")
            != records.digest(
                {k: v for k, v in receipt.items() if k != "receipt_digest"}
            )
            or terminal.get("status") != "completed"
            or type(terminal.get("adapter_exit_code")) is not int
            or terminal["adapter_exit_code"] != 0
            or proof["policy_digest"]
            != records.digest(workflow._load_trusted_policy(self.root, self.contract))
        ):
            raise records.ContinuationError("CONTINUATION_BINDING_CHANGED")
        self.store.verify_snapshot(
            self.contract, self.snapshot_key(invocation, "after"), proof["workspace"]
        )
        return dict(proof)

    def prepare_handoff(self, state: dict[str, Any]) -> dict[str, Any]:
        self.check()
        proof = self.resume_executor(state)
        if proof is None:
            raise records.ContinuationError("CONTINUATION_COMPLETION_REQUIRED")
        receipt = self.adapter.store.load(proof["invocation_digest"])
        if receipt is None:
            raise records.ContinuationError("CONTINUATION_REASONING_UNVERIFIED")
        original_reasoning, repeat_reasoning, reasoning_digest = (
            self.reasoning_evidence(state, proof)
        )
        findings = self.findings(state)
        gates: list[dict[str, Any]] = []
        gate = state.get("continuation_gate")
        if gate is not None:
            from tools import agent_gate

            if not agent_gate.gate_evidence_is_passing(gate):
                raise records.ContinuationError("CONTINUATION_GATE_INVALID")
            workflow._assert_gate_snapshot_unchanged(self.root, self.contract, gate)
            gates = [
                {
                    "kind": "local",
                    "sha": gate["snapshot"]["snapshot_commit"],
                    "evidence_digest": records.digest(gate),
                    "outcome": "PASS",
                }
            ]
        key = "handoff-" + records.digest(
            {
                "contract": workflow._contract_fields(self.contract),
                "run_id": state["run_id"],
            }
        )
        previous = self.store.load(key, cap=self.owner["limits"]["max_record_bytes"])
        if previous is not None:
            existing = records.handoff(
                previous,
                self.identity(state),
                self.owner,
                max_repairs=self.contract.max_repair_iterations,
            )
            if (
                existing["source"]["checkpoint_digest"] == records.digest(state)
                and existing["status"] == "PREPARED"
            ):
                return existing
            if existing["consumed"]["handoffs"] != state.get(
                "continuation_handoffs", 0
            ):
                raise records.ContinuationError("CONTINUATION_HANDOFF_BUDGET_CHANGED")
            count = existing["consumed"]["handoffs"] + (
                0
                if existing["status"] == "PREPARED"
                and existing["source"]["invocation_digest"]
                == proof["invocation_digest"]
                else 1
            )
        else:
            if state.get("continuation_handoffs", 0) != 0:
                raise records.ContinuationError("CONTINUATION_HANDOFF_UNKNOWN")
            count = 1
        records.integer(count, self.owner["limits"]["max_handoffs"], minimum=1)
        state["continuation_handoffs"] = count
        self.workflow.save(workflow._run_key(self.contract), state)
        checkpoint = records.digest(state)
        consumed = self.consumed(state)
        value = {
            "schema_version": "1.0",
            "identity": self.identity(state),
            "contract_digest": records.digest(workflow._contract_fields(self.contract)),
            "policy_digest": proof["policy_digest"],
            "workspace": proof["workspace"],
            "progress": {
                "role": "controller",
                "phase": "CONTROLLER" if gate is not None else "GATE",
                "ordinal": state["controller_calls"] + 1,
                "checkpoint_digest": checkpoint,
            },
            "pending": None,
            "findings": findings,
            "gates": gates,
            "consumed": {
                "started_at": int(consumed["started"]),
                "deadline_at": min(
                    int(consumed["started"]) + self.contract.max_minutes * 60,
                    self.outer_deadline,
                ),
                "repair_iterations": state["repair_iterations"],
                "executor_calls": state["executor_calls"],
                "controller_calls": state["controller_calls"],
                "handoffs": count,
                "original_reasoning": original_reasoning,
                "repeat_reasoning": repeat_reasoning,
                "reasoning_evidence_digest": reasoning_digest,
            },
            "source": {
                "invocation_digest": proof["invocation_digest"],
                "backend_digest": self.owner["backend_digest"],
                "checkpoint_digest": checkpoint,
                "reservation_digest": proof["reservation_digest"],
            },
            "status": "PREPARED",
        }
        value["record_digest"] = records.digest(value)
        records.handoff(
            value,
            self.identity(state),
            self.owner,
            max_repairs=self.contract.max_repair_iterations,
        )
        self.store.save(key, value, cap=self.owner["limits"]["max_record_bytes"])
        return value

    def verify_handoff(self, state: dict[str, Any]) -> None:
        self.check()
        key = "handoff-" + records.digest(
            {
                "contract": workflow._contract_fields(self.contract),
                "run_id": state["run_id"],
            }
        )
        raw = self.store.load(key, cap=self.owner["limits"]["max_record_bytes"])
        value = records.handoff(
            raw,
            self.identity(state),
            self.owner,
            max_repairs=self.contract.max_repair_iterations,
        )
        proof = self.resume_executor(state)
        gate = state.get("continuation_gate")
        expected_gates: list[dict[str, Any]] = []
        if gate is not None:
            from tools import agent_gate

            if not agent_gate.gate_evidence_is_passing(gate):
                raise records.ContinuationError("CONTINUATION_GATE_INVALID")
            workflow._assert_gate_snapshot_unchanged(self.root, self.contract, gate)
            expected_gates = [
                {
                    "kind": "local",
                    "sha": gate["snapshot"]["snapshot_commit"],
                    "evidence_digest": records.digest(gate),
                    "outcome": "PASS",
                }
            ]
        if (
            proof is None
            or value["status"] != "PREPARED"
            or value["pending"] is not None
            or value["source"]["invocation_digest"] != proof["invocation_digest"]
            or value["progress"]
            != {
                "role": "controller",
                "phase": "CONTROLLER" if gate is not None else "GATE",
                "ordinal": state["controller_calls"] + 1,
                "checkpoint_digest": records.digest(state),
            }
            or value["consumed"]["started_at"] != int(state["started"])
            or value["consumed"]["deadline_at"]
            != min(
                int(state["started"]) + self.contract.max_minutes * 60,
                self.outer_deadline,
            )
            or value["consumed"]["original_reasoning"]
            != self.reasoning_evidence(state, proof)[0]
            or value["consumed"]["repeat_reasoning"]
            != self.reasoning_evidence(state, proof)[1]
            or value["consumed"]["handoffs"] != state.get("continuation_handoffs")
            or value["consumed"]["executor_calls"] != state["executor_calls"]
            or value["consumed"]["controller_calls"] != state["controller_calls"]
            or value["consumed"]["repair_iterations"] != state["repair_iterations"]
            or value["source"]["checkpoint_digest"] != records.digest(state)
            or value["workspace"] != proof["workspace"]
            or value["contract_digest"]
            != records.digest(workflow._contract_fields(self.contract))
            or value["policy_digest"] != proof["policy_digest"]
            or value["source"]["reservation_digest"] != proof["reservation_digest"]
            or value["consumed"]["reasoning_evidence_digest"]
            != self.reasoning_evidence(state, proof)[2]
            or value["findings"] != self.findings(state)
            or value["gates"] != expected_gates
        ):
            raise records.ContinuationError("CONTINUATION_HANDOFF_STALE")

    def consume_handoff(self, state: dict[str, Any]) -> None:
        self.verify_handoff(state)
        key = "handoff-" + records.digest(
            {
                "contract": workflow._contract_fields(self.contract),
                "run_id": state["run_id"],
            }
        )
        value = self.store.load(key, cap=self.owner["limits"]["max_record_bytes"])
        assert value is not None
        value = {**value, "status": "CONSUMED"}
        value["record_digest"] = records.digest(
            {k: v for k, v in value.items() if k != "record_digest"}
        )
        self.store.save(key, value, cap=self.owner["limits"]["max_record_bytes"])

    def reasoning_evidence(
        self, state: dict[str, Any], proof: dict[str, Any]
    ) -> tuple[str, str | None, str]:
        from tools.agent_continuation_backend import validate_completion

        maximum = 2 * (self.contract.max_repair_iterations + 1)
        paths = list(islice(
            self.store.directory.glob("reservation-*.json"), maximum + 1
        ))
        if len(paths) > maximum:
            raise records.ContinuationError("ROLE_CALL_BUDGET_EXHAUSTED")
        chain: dict[tuple[str, int], str] = {}
        current: dict[str, Any] | None = None
        for path in paths:
            value = records.reservation(
                self.store.load(path.stem), self.identity(state),
                max_calls=self.contract.max_repair_iterations + 1,
            )
            role, ordinal = value["role"], value["ordinal"]
            index = (role, ordinal)
            invocation = value["invocation_digest"]
            checkpoint = self.store.load("checkpoint-" + invocation)
            source = self.adapter.store.load(invocation)
            if (
                value["status"] != "COMPLETED" or index in chain
                or checkpoint is None or source is None
                or ordinal > state[role + "_calls"]
                or records.digest(checkpoint) != value["checkpoint_digest"]
                or self.identity(checkpoint) != self.identity(state)
                or checkpoint[role + "_calls"] != ordinal
                or checkpoint["started"] != state["started"]
                or checkpoint["repair_iterations"] > state["repair_iterations"]
                or (role == "executor"
                    and ordinal != checkpoint["repair_iterations"] + 1)
                or checkpoint.get("role_pending") != role
                or checkpoint.get("invocation_digest") != invocation
                or records.digest(self.consumed(checkpoint)) != value["consumed_digest"]
            ):
                raise records.ContinuationError("CONTINUATION_REASONING_UNVERIFIED")
            effort = ["low", "medium", "high"][checkpoint["repair_iterations"]]
            receipt = validate_completion(
                source["receipt"], source["terminal"], self.adapter.owner,
                role=role, invocation=invocation, output=None, observed_exit=0,
                reasoning=effort,
                expected_output_digest=source["terminal"]["validated_output_digest"],
            )
            if receipt["receipt_digest"] != value["outcome_digest"]:
                raise records.ContinuationError("CONTINUATION_BINDING_CHANGED")
            if role == "controller":
                review = self.store.load("review-" + invocation)
                if (
                    review is None or review.get("identity") != self.identity(state)
                    or review.get("receipt_digest") != receipt["receipt_digest"]
                    or records.digest(review.get("verdict"))
                    != source["terminal"]["validated_output_digest"]
                ):
                    raise records.ContinuationError(
                        "CONTINUATION_FINDINGS_SOURCE_REQUIRED"
                    )
            chain[index] = receipt["receipt_digest"]
            if invocation == proof["invocation_digest"]:
                current = receipt
        expected = {
            (role, ordinal)
            for role in ("executor", "controller")
            for ordinal in range(1, state[role + "_calls"] + 1)
        }
        if set(chain) != expected or current is None:
            raise records.ContinuationError("CONTINUATION_REASONING_UNVERIFIED")
        return (
            "low",
            current["effective"]["reasoning"] if state["repair_iterations"] else None,
            records.digest([
                {"role": role, "ordinal": ordinal,
                 "receipt_digest": chain[role, ordinal]}
                for role, ordinal in sorted(chain)
            ]),
        )

    def findings(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        maximum = self.contract.max_repair_iterations + 1
        paths = list(islice(self.store.directory.glob("review-*.json"), maximum + 1))
        if len(paths) > maximum:
            raise records.ContinuationError("ROLE_CALL_BUDGET_EXHAUSTED")
        result: list[dict[str, Any]] = []
        for path in sorted(paths):
            source = records.exact(
                self.store.load(path.stem),
                {"schema_version", "identity", "receipt_digest", "verdict"},
            )
            if source["schema_version"] != "1.0" or source["identity"] != self.identity(
                state
            ):
                raise records.ContinuationError("CONTINUATION_BINDING_CHANGED")
            invocation = path.stem.removeprefix("review-")
            receipt = self.adapter.store.load(invocation)
            if receipt is None:
                raise records.ContinuationError("CONTINUATION_FINDINGS_SOURCE_REQUIRED")
            output = source["verdict"]
            workflow._validate_verdict(
                output, self.contract, workflow._head_sha(self.root, self.contract)
            )
            from tools.agent_continuation_backend import validate_completion

            verified = validate_completion(
                receipt["receipt"],
                receipt["terminal"],
                self.adapter.owner,
                role="controller",
                invocation=invocation,
                output=output,
                observed_exit=0,
                reasoning=receipt["receipt"]["effective"]["reasoning"],
            )
            if verified["receipt_digest"] != source["receipt_digest"]:
                raise records.ContinuationError("CONTINUATION_BINDING_CHANGED")
            for finding in output["blocking_findings"]:
                category = finding.get("category")
                if category not in {
                    "security",
                    "contract",
                    "scope",
                    "correctness",
                    "quality",
                    "context",
                    "unknown",
                }:
                    category = "unknown"
                result.append(
                    {
                        "finding_digest": records.digest(finding),
                        "category": category,
                        "status": "open",
                        "source_review_digest": source["receipt_digest"],
                    }
                )
                if len(result) > self.owner["limits"]["max_findings"]:
                    raise records.ContinuationError("CONTINUATION_FINDINGS_TOO_LARGE")
        return result
