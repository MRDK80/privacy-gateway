"""Prospective same-live-call association; no historical input reconstruction."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

from tools import agent_continuation as continuation
from tools import agent_learning as core
from tools import agent_learning_binding_records as binding_records
from tools import agent_learning_records as records
from tools import agent_orchestrate as workflow
from tools import codex_adapter
from tools.agent_continuation import digest
from tools.agent_continuation_runtime import Continuation
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_runtime_cli import secure_directory
from tools.agent_learning_planning_records import problem_binding
from tools.agent_learning_store import KEY_FIELDS, Store

NEUTRAL = {
    "run_id",
    "started",
    "repair_iterations",
    "executor_calls",
    "controller_calls",
    "findings",
    "verdicts",
    "failures",
    "continuation_handoffs",
}


def require(value: bool) -> None:
    if not value:
        raise core.LearningError("LEARNING_SOURCE_MISMATCH")


def same(left: Any, right: Any) -> bool:
    return digest(left) == digest(right)


@dataclass(repr=False)
class Witness:
    runtime_identity: Any
    reader_identity: Any
    task: int
    role: str
    contract_digest: str
    owner_digest: str
    catalog_digest: str
    config_digest: str
    pre_canonical: dict[str, Any] | None
    first_state_neutral: dict[str, Any] | None
    retained_origins: list[dict[str, Any]]
    selected_rows: list[dict[str, Any]]
    prepared_digest: str
    completed_prefix: list[dict[str, Any]]
    handoff_prefix: dict[str, Any] | None
    lifecycle: str = field(default="CREATED")


class Binding:
    def __init__(self, runtime: Any) -> None:
        from tools.agent_epic_session_runtime import SessionRuntime
        from tools.agent_learning_evidence import SourceReader
        from tools.agent_learning_invocation import Runtime

        require(type(runtime) is Runtime and type(runtime.reader) is SourceReader)
        require(type(runtime.verifier.session) is SessionRuntime)
        self.runtime, self.reader = runtime, runtime.reader
        self.context: Continuation | None = None
        self.neutral: dict[str, Any] | None = None
        self.witness: Witness | None = None
        self.capability: tuple[Witness, dict[str, Any], str] | None = None

    def context_from_orchestrator(self, context: Any, neutral: dict[str, Any]) -> None:
        require(type(context) is Continuation)
        require(context.root.resolve() == self.reader.root)
        require(self.capability is None)
        context.check()
        require(
            context.lock
            is RunnerLock.current_owned(context.directory / "continuation/ownership")
        )
        core.exact(neutral, NEUTRAL)
        self.context, self.neutral = context, copy.deepcopy(neutral)

    def _guard(self, task: int) -> None:
        require(self.context is not None)
        assert self.context is not None
        require(self.context.directory == self.reader.task_directory(task))
        RunnerLock.current_owned(self.context.directory)
        require(
            self.context.lock
            is RunnerLock.current_owned(
                self.context.directory / "continuation/ownership"
            )
        )
        self.context.check()
        self.reader.guard()
        require(self.reader.config["schema_version"] == "8.0")

    def _current(self, task: int) -> dict[str, Any] | None:
        from tools.agent_learning_workflow_companions import canonical

        return canonical(self.reader, task)

    def _metadata_key(
        self, state: dict[str, Any], role: str, ordinal: int, repair: int, task: int
    ) -> str:
        return records.invocation_id(
            {
                "identity": {
                    "repository": self.reader.owner["repository"],
                    "epic": self.reader.owner["epic"],
                    "task": task,
                    "policy_sha": self.reader.owner["policy_sha"],
                    "owner_profile_digest": digest(self.reader.owner),
                    "original_run_id": state["run_id"],
                },
                "role": role,
                "ordinal": ordinal,
                "repair_iteration": repair,
            }
        )

    def _prefix(
        self, task: int, state: dict[str, Any], *, pending: str | None = None
    ) -> list[dict[str, Any]]:
        self._guard(task)
        reader = self.reader
        directory = reader.task_directory(task)
        with (
            reader.ownership(reader.seed / "role-settings/ownership"),
            reader.ownership(directory / "learning-invocations"),
        ):
            prefix = self._prefix_owned(task, state, pending=pending)
            target = directory / "learning"
            if target.exists() or target.is_symlink():
                with Store(
                    target,
                    reader.root,
                    directory,
                    reader.owner,
                    validators=records.VALIDATORS,
                ) as store:
                    self._prefix_store_equal(task, state, prefix, store)
            else:
                self._prefix_store_equal(task, state, prefix, None)
            self._guard(task)
            return prefix

    def _prefix_store_equal(
        self,
        task: int,
        state: dict[str, Any],
        prefix: list[dict[str, Any]],
        store: Store | None,
    ) -> None:
        reader = self.reader
        self._guard(task)
        directory = reader.task_directory(task)
        RunnerLock.current_owned(reader.seed / "role-settings/ownership")
        RunnerLock.current_owned(directory / "learning-invocations")
        target = directory / "learning"
        if store is None:
            require(not target.exists() and not target.is_symlink() and not prefix)
            return
        require(type(store) is Store)
        assert store is not None
        require(
            store.directory == target
            and store.task == directory
            and store.root == reader.root
            and same(store.owner, reader.owner)
            and store.validators == records.VALIDATORS
        )
        store._owned()
        store.ensure_usable()
        rows = store.records()
        expected_records: dict[str, dict[str, Any]] = {}
        for row in prefix:
            checkpoint = reader._json(
                directory / "continuation"
                / ("checkpoint-" + row["native_invocation_digest"] + ".json")
            )
            require(digest(checkpoint) == row["checkpoint_digest"])
            key = self._metadata_key(
                state, row["role"], row["ordinal"],
                checkpoint["repair_iterations"], task,
            )
            metadata = records.invocation(
                reader._json(directory / "learning-invocations" / (key + ".json"))
            )
            require(
                metadata["status"] == "COMPLETED"
                and digest(metadata) == row["metadata_digest"]
            )
            matching = [
                item["record"] for item in rows
                if item["kind"] == "invocation"
                and item["record"]["invocation_key"] == key
            ]
            require(len(matching) == 1 and same(matching[0], metadata))
            expected_records[key] = metadata
        expected_identity = {
            "repository": reader.owner["repository"],
            "epic": reader.owner["epic"],
            "task": task,
            "policy_sha": reader.owner["policy_sha"],
            "owner_profile_digest": digest(reader.owner),
            "original_run_id": state["run_id"],
        }
        current_records: dict[str, dict[str, Any]] = {}
        for item in rows:
            if item["kind"] != "invocation":
                continue
            record = item["record"]
            if record["identity"]["original_run_id"] != state["run_id"]:
                continue
            require(same(record["identity"], expected_identity))
            require(record["role"] in {"executor", "controller"})
            require(record["status"] == "COMPLETED")
            current_records[record["invocation_key"]] = record
        require(same(current_records, expected_records))
        self._guard(task)

    def _prefix_owned(
        self, task: int, state: dict[str, Any], *, pending: str | None = None
    ) -> list[dict[str, Any]]:
        reader = self.reader
        self._guard(task)
        RunnerLock.current_owned(reader.seed / "role-settings/ownership")
        RunnerLock.current_owned(reader.task_directory(task) / "learning-invocations")
        contract = reader.contract(task)
        chain, _ = reader._completed_role_chain(
            task, contract, state, pending_invocation=pending
        )
        directory = reader.task_directory(task)
        rows: list[dict[str, Any]] = []
        for (role, ordinal), original in sorted(chain.items()):
            native, checkpoint = original["reservation"], original["checkpoint"]
            inv = native["invocation_digest"]
            key = self._metadata_key(
                state, role, ordinal, checkpoint["repair_iterations"], task
            )
            metadata = records.invocation(
                reader._json(directory / "learning-invocations" / (key + ".json"))
            )
            problem = problem_binding(
                reader._json(
                    directory / "learning-invocations" / (key + ".problem.json")
                )
            )
            require(metadata["status"] == "COMPLETED")
            require(metadata["role"] == role and metadata["ordinal"] == ordinal)
            require(metadata["repair_iteration"] == checkpoint["repair_iterations"])
            require(problem["invocation_key"] == key and problem["role"] == role)
            review = (
                None
                if role == "executor"
                else reader._json(
                    directory / "continuation" / ("review-" + inv + ".json")
                )
            )
            rows.append(
                {
                    "role": role,
                    "ordinal": ordinal,
                    "native_invocation_digest": inv,
                    "native_reservation_digest": digest(native),
                    "checkpoint_digest": digest(checkpoint),
                    "metadata_digest": digest(metadata),
                    "problem_digest": digest(problem),
                    "settings_source_digest": digest(original["source"]),
                    "controller_review_digest": None
                    if review is None
                    else digest(review),
                }
            )
        return rows

    def _handoff(self, state: dict[str, Any], *, verify: bool) -> dict[str, Any] | None:
        assert self.context is not None
        key = "handoff-" + digest(
            {
                "contract": workflow._contract_fields(self.context.contract),
                "run_id": state["run_id"],
            }
        )
        path = self.context.directory / "continuation" / (key + ".json")
        if not path.exists() and not path.is_symlink():
            require(not verify)
            return None
        source = continuation.handoff(
            self.reader._json(path),
            self.context.identity(state),
            self.context.owner,
            max_repairs=self.context.contract.max_repair_iterations,
        )
        if verify:
            self.context.verify_handoff(state)
        return {"key": key, "source": source, "source_digest": digest(source)}

    def _payload_contract(
        self,
        role: str,
        payload: dict[str, Any],
        contract: workflow.TaskContract,
        repairs: int,
    ) -> None:
        require(role in {"executor", "controller"})
        require(type(payload.get("repair_iteration")) is int)
        require(payload["repair_iteration"] == repairs)
        expected = (
            workflow._contract_fields(contract)
            if role == "executor"
            else workflow._controller_contract_fields(contract, repairs)
        )
        require(same(payload["contract"], expected))

    def capture(
        self,
        role: str,
        payload: dict[str, Any],
        previous: dict[str, Any] | None,
        references: list[dict[str, Any]],
    ) -> None:
        task = payload["contract"]["issue"]
        self._guard(task)
        require(self.capability is None and self.neutral is not None)
        require(
            self.witness is None or self.witness.lifecycle in {"CONSUMED", "INVALID"}
        )
        assert self.context is not None and self.neutral is not None
        contract = self.reader.contract(task)
        first = None
        if previous is None:
            require(role == "executor")
            self._payload_contract(role, payload, contract, 0)
            first = copy.deepcopy(self.neutral)
            core.hash_value(first["run_id"], size=32)
            for name in NEUTRAL - {"run_id", "started", "verdicts", "failures"}:
                require(type(first[name]) is int and first[name] == 0)
            require(first["verdicts"] == [] and first["failures"] == [])
            # Original no-prior canonical plus native/metadata zero-call proof.
            require(self._current(task) is None)
            require(
                not list(
                    islice(
                        (self.context.directory / "continuation").glob(
                            "reservation-*.json"
                        ),
                        1,
                    )
                )
            )
            from tools.agent_learning_measure import _metadata

            require(
                _metadata(
                    self.reader,
                    self.context.directory / "learning-invocations",
                    task=task,
                    run_id=first["run_id"],
                )
                == []
            )
            metadata_directory = self.context.directory / "learning-invocations"
            if metadata_directory.exists():
                require(
                    all(
                        path.name == "epic-loop.lock"
                        for path in islice(metadata_directory.iterdir(), 2)
                    )
                )
            prefix: list[dict[str, Any]] = []
            baseline = first
        else:
            require(
                previous.get("terminal") is False
                and previous.get("role_pending") is None
            )
            require(same(previous, self._current(task)))
            require(all(same(previous[name], self.neutral[name]) for name in NEUTRAL))
            repairs = core.integer(
                previous["repair_iterations"], contract.max_repair_iterations
            )
            self._payload_contract(role, payload, contract, repairs)
            prefix = self._prefix(task, previous)
            baseline = previous
        import math

        started = baseline["started"]
        require(type(started) in (int, float) and started >= 0)
        require(type(started) is int or math.isfinite(started))
        handoff = self._handoff(
            baseline,
            verify=role == "controller" and bool(baseline.get("completed_executor")),
        )
        if previous is None:
            require(handoff is None)
        original = {
            key: value for key, value in payload.items() if key != "contract_object"
        }
        self.witness = Witness(
            self.runtime,
            self.reader,
            task,
            role,
            digest(workflow._contract_fields(contract)),
            digest(self.reader.owner),
            digest(self.reader.catalog),
            digest(self.reader.config),
            copy.deepcopy(previous),
            first,
            copy.deepcopy(self.runtime.verifier._workflow_selection_origins),
            copy.deepcopy(references),
            digest(original),
            prefix,
            handoff,
        )
        self._guard(task)

    def begin(self) -> None:
        require(self.witness is not None and self.witness.lifecycle == "CREATED")
        assert self.witness is not None
        self.witness.lifecycle = "CONSUMING"

    def invalidate(self) -> None:
        if self.witness is not None:
            self.witness.lifecycle = "INVALID"
        self.capability = None

    def _origins(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        reader = self.reader
        origins: list[dict[str, Any]] = []
        retained: list[dict[str, Any]] = []
        tasks: dict[int, list[dict[str, Any]]] = {}
        for task in reader.config["approved_order"]:
            try:
                directory = reader.task_directory(task)
            except core.LearningError as error:
                if error.machine_code == "LEARNING_SOURCE_MISSING":
                    continue
                raise
            tasks[task] = []
            target = directory / "learning"
            if not target.exists() and not target.is_symlink():
                origins.append(
                    {
                        "task": task,
                        "store_kind": "workflow",
                        "records_digest": digest(None),
                        "record_identities": [],
                    }
                )
                continue
            with reader.ownership(directory):
                with Store(
                    target,
                    reader.root,
                    directory,
                    reader.owner,
                    validators=records.VALIDATORS,
                ) as store:
                    store.ensure_usable()
                    rows = store.records()
            tasks[task] = rows
            retained.extend(rows)
            if len(retained) > reader.owner["limits"]["max_records"]:
                raise core.LearningError("LEARNING_STORAGE_EXHAUSTED")
            origins.append(
                {
                    "task": task,
                    "store_kind": "workflow",
                    "records_digest": digest(rows),
                    "record_identities": sorted(
                        [
                            {
                                "kind": row["kind"],
                                "key": row["record"][KEY_FIELDS[row["kind"]]],
                                "record_digest": digest(row["record"]),
                            }
                            for row in rows
                        ],
                        key=lambda row: (row["kind"], row["key"]),
                    ),
                }
            )
        assert self.witness is not None
        require(same(origins, self.witness.retained_origins))
        # Only the exact known CURRENT transition replaces generic completeness.
        for task, rows in tasks.items():
            if task != self.witness.task:
                self.runtime.verifier._source_complete(task, rows, retained)
        return origins, retained

    def _references(
        self, retained: list[dict[str, Any]], envelope: dict[str, Any]
    ) -> None:
        assert self.witness is not None
        witness = self.witness
        require(len(envelope["hints"]) == len(witness.selected_rows))
        require(envelope["selection_digest"] == digest(envelope["hints"]))
        tombstones = {
            row["record"]["lesson_id"] for row in retained if row["kind"] == "tombstone"
        }

        def exact(kind: str, expected: str) -> dict[str, Any]:
            found = [
                row["record"]
                for row in retained
                if row["kind"] == kind and digest(row["record"]) == expected
            ]
            require(bool(found) and all(same(row, found[0]) for row in found))
            return dict(found[0])

        for hint, row in zip(envelope["hints"], witness.selected_rows):
            require(
                hint["lesson_id"] == row["lesson_id"]
                and row["lesson_id"] not in tombstones
            )
            verified = exact("verification", row["verification_digest"])
            candidate = exact("candidate", row["candidate_digest"])
            before = exact("observation", row["before_digest"])
            after = exact("observation", row["after_digest"])
            require(verified["candidate_digest"] == digest(candidate))
            require(verified["observation_digest"] == digest(after))
            require(verified["lesson_id"] == row["lesson_id"])
            rules = [
                rule
                for rule in self.reader.catalog["rules"]
                if rule["rule_id"] == candidate["rule_id"]
            ]
            require(len(rules) == 1)
            rule = rules[0]
            require(verified["catalog_digest"] == self.reader.owner["catalog_digest"])
            require(
                same(
                    hint,
                    {
                        "lesson_id": verified["lesson_id"],
                        "trust": "UNTRUSTED_VERIFIED_HINT",
                        **{
                            key: rule[key]
                            for key in (
                                "problem_class",
                                "symptom_code",
                                "diagnostic_steps",
                                "minimal_fix",
                                "applicability",
                            )
                        },
                        "permissions": None,
                    },
                )
            )
            proof = self.runtime.verifier.pair(before, after, rule)
            require(proof is not None and digest(proof) == row["pair_digest"])
            assert proof is not None
            require(
                all(
                    proof[key] == verified[key]
                    for key in ("evidence_digest", "applicability_digest")
                )
            )

    def _delta(
        self, role: str, safe: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        witness = self.witness
        require(witness is not None)
        assert witness is not None and self.context is not None
        self._guard(witness.task)
        require(
            witness.runtime_identity is self.runtime
            and witness.reader_identity is self.reader
        )
        require(witness.role == role)
        require(
            witness.contract_digest
            == digest(workflow._contract_fields(self.context.contract))
        )
        require(
            witness.owner_digest == digest(self.reader.owner)
            and witness.catalog_digest == digest(self.reader.catalog)
            and witness.config_digest == digest(self.reader.config)
        )
        stripped = {
            key: value
            for key, value in safe.items()
            if key not in {"session_id", "trusted_policy"}
        }
        require(digest(stripped) == witness.prepared_digest)
        baseline = witness.pre_canonical or witness.first_state_neutral
        assert baseline is not None
        expected = {key: copy.deepcopy(baseline[key]) for key in NEUTRAL}
        expected[role + "_calls"] += 1
        expected.update(
            terminal=False,
            status="running",
            machine_code="ROLE_RESERVED",
            role_pending=role,
            invocation_digest=digest(safe),
            controller_boundary=None,
        )
        if role == "controller":
            # Original handoff is the source-backed completed-executor/gate link.
            require(
                witness.pre_canonical is not None and witness.handoff_prefix is not None
            )
            assert witness.pre_canonical is not None
            gate = witness.pre_canonical.get("continuation_gate")
            require(isinstance(gate, dict))
            expected["controller_boundary"] = {
                "schema_version": "1.0",
                "contract": workflow._contract_fields(self.context.contract),
                "head_sha": safe["head_sha"],
                "gate": gate,
            }
        canonical = self._current(witness.task)
        require(same(canonical, expected))
        assert canonical is not None
        for name in ("executor_calls", "controller_calls"):
            core.integer(
                canonical[name], self.context.contract.max_repair_iterations + 1
            )
        core.integer(
            canonical["repair_iterations"], self.context.contract.max_repair_iterations
        )
        inv = digest(safe)
        source = self.reader._json(
            self.context.directory / "continuation" / ("reservation-" + inv + ".json")
        )
        source = continuation.reservation(
            source,
            self.context.identity(canonical),
            max_calls=self.context.contract.max_repair_iterations + 1,
        )
        require(
            source["status"] == "RESERVED"
            and source["snapshot_after"] is None
            and source["outcome_digest"] is None
        )
        require(
            source["checkpoint_digest"] == digest(canonical)
            and source["invocation_digest"] == inv
        )
        key = self._metadata_key(
            canonical,
            role,
            canonical[role + "_calls"],
            canonical["repair_iterations"],
            witness.task,
        )
        metadata = records.invocation(
            self.reader._json(
                self.context.directory / "learning-invocations" / (key + ".json")
            )
        )
        require(metadata["status"] == "RESERVED" and metadata["invocation_key"] == key)
        require(
            metadata["input_digest"]
            == digest(
                {k: v for k, v in safe.items() if k != "untrusted_verified_hints"}
            )
        )
        require(
            metadata["prompt_digest"]
            == hashlib.sha256(codex_adapter._prompt(role, safe).encode()).hexdigest()
        )
        envelope = safe["untrusted_verified_hints"]
        require(
            metadata["selection_digest"]
            == envelope["selection_digest"]
            == digest(envelope["hints"])
        )
        require(
            metadata["lesson_ids"]
            == sorted(row["lesson_id"] for row in witness.selected_rows)
        )
        require(metadata["catalog_digest"] == self.reader.owner["catalog_digest"])
        self._problem(metadata, safe)
        allowed = {key + ".json", key + ".problem.json", "epic-loop.lock"}
        for row in witness.completed_prefix:
            checkpoint = self.reader._json(
                self.context.directory
                / "continuation"
                / ("checkpoint-" + row["native_invocation_digest"] + ".json")
            )
            old_key = self._metadata_key(
                canonical,
                row["role"],
                row["ordinal"],
                checkpoint["repair_iterations"],
                witness.task,
            )
            allowed.update({old_key + ".json", old_key + ".problem.json"})
        metadata_directory = self.context.directory / "learning-invocations"
        names: set[str] = set()
        for path in islice(metadata_directory.iterdir(), len(allowed) + 1):
            self._guard(witness.task)
            require(path.name in allowed and path.name not in names)
            if path.name != "epic-loop.lock":
                self.reader._json(path)
            names.add(path.name)
        require(names - {"epic-loop.lock"} == allowed - {"epic-loop.lock"})
        require(
            same(
                self._prefix(witness.task, canonical, pending=inv),
                witness.completed_prefix,
            )
        )
        actual_handoff = self._handoff(canonical, verify=False)
        original_handoff = witness.handoff_prefix
        if (
            role == "controller"
            and witness.pre_canonical is not None
            and witness.pre_canonical.get("completed_executor")
        ):
            require(original_handoff is not None)
            assert original_handoff is not None
            consumed = {**original_handoff["source"], "status": "CONSUMED"}
            consumed["record_digest"] = digest(
                {k: v for k, v in consumed.items() if k != "record_digest"}
            )
            require(
                same(
                    actual_handoff,
                    {
                        "key": original_handoff["key"],
                        "source": consumed,
                        "source_digest": digest(consumed),
                    },
                )
            )
        else:
            require(same(actual_handoff, original_handoff))
        self._guard(witness.task)
        return canonical, source, metadata

    def _problem(self, metadata: dict[str, Any], safe: dict[str, Any]) -> None:
        assert self.context is not None
        role, _, _, problem, origin = self.runtime._workflow_prepared
        require(role == metadata["role"])
        hints = safe["untrusted_verified_hints"]["hints"]
        expected = problem_binding(
            core.seal(
                {
                    "schema_version": "1.0",
                    "invocation_key": metadata["invocation_key"],
                    "role": role,
                    "source_kind": "SAME_RUN_GATE" if hints else "NONE",
                    "source_digest": digest(origin) if hints else None,
                    "problem_class": problem if hints else None,
                }
            )
        )
        actual = problem_binding(
            self.reader._json(
                self.context.directory
                / "learning-invocations"
                / (metadata["invocation_key"] + ".problem.json")
            )
        )
        require(same(actual, expected))

    def _write(self, value: dict[str, Any], previous: dict[str, Any] | None) -> None:
        assert self.witness is not None and self.context is not None
        task = self.witness.task
        self._guard(task)
        limits = self.reader.owner["limits"]
        value = binding_records.binding(
            value,
            max_repairs=self.context.contract.max_repair_iterations,
            max_hints=limits["max_hints_per_role"],
        )
        directory = self.context.directory / "workflow-learning-bindings"
        if not directory.exists() and not directory.is_symlink():
            require(previous is None)
            directory.mkdir(mode=0o700)
        secure_directory(directory, self.reader.root)
        count, size = 0, 0
        for path in islice(directory.iterdir(), limits["max_records"] + 1):
            self._guard(task)
            if count >= limits["max_records"]:
                raise core.LearningError("LEARNING_STORAGE_EXHAUSTED")
            require(re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is not None)
            original = binding_records.binding(
                self.reader._json(path),
                max_repairs=self.context.contract.max_repair_iterations,
                max_hints=limits["max_hints_per_role"],
            )
            require(original["invocation_key"] == path.stem)
            size += path.lstat().st_size
            count += 1
        target = directory / (value["invocation_key"] + ".json")
        exists = target.exists() or target.is_symlink()
        if previous is None:
            require(not exists)
            if count >= limits["max_records"]:
                raise core.LearningError("LEARNING_STORAGE_EXHAUSTED")
        else:
            require(exists and same(self.reader._json(target), previous))
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        old_size = target.lstat().st_size if exists else 0
        if (
            len(encoded) > limits["max_record_bytes"]
            or size - old_size + len(encoded) > limits["max_store_bytes"]
        ):
            raise core.LearningError("LEARNING_STORAGE_EXHAUSTED")
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(dir=directory, prefix=".binding-")
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._guard(task)
            os.replace(temporary, target)
            temporary = None
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            raise core.LearningError("LEARNING_WRITE_FAILED") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        self._guard(task)
        require(same(self.reader._json(target), value))
        self._guard(task)

    def reserve(self, role: str, safe: dict[str, Any]) -> None:
        try:
            witness = self.witness
            require(witness is not None and witness.lifecycle == "CONSUMING")
            assert witness is not None and self.context is not None
            self._guard(witness.task)
            _, retained = self._origins()
            self._references(retained, safe["untrusted_verified_hints"])
            self._delta(role, safe)
            ownership = self.reader.seed / "role-settings/ownership"
            secure_directory(ownership, self.reader.root)
            with (
                RunnerLock(ownership),
                RunnerLock(self.context.directory / "learning-invocations"),
            ):
                canonical, native, metadata = self._delta(role, safe)
                # Reserve never constructs a learning Store that wasn't present.
                target = self.context.directory / "learning"
                if target.exists() or target.is_symlink():
                    with Store(
                        target,
                        self.reader.root,
                        self.context.directory,
                        self.reader.owner,
                        validators=records.VALIDATORS,
                    ) as store:
                        store.ensure_usable()
                        self._current_store_equal(store)
                        value = self._reserved_value(
                            role, safe, canonical, native, metadata
                        )
                        self._write(value, None)
                else:
                    self._current_store_equal(None)
                    value = self._reserved_value(
                        role, safe, canonical, native, metadata
                    )
                    self._write(value, None)
                self._delta(role, safe)
                self._guard(witness.task)
            witness.lifecycle = "CONSUMED"
            self.capability = (witness, copy.deepcopy(value), digest(safe))
        except BaseException:
            self.invalidate()
            raise

    def _current_store_equal(self, store: Store | None) -> None:
        assert self.witness is not None
        witness = self.witness
        self._guard(witness.task)
        directory = self.reader.task_directory(witness.task)
        RunnerLock.current_owned(self.reader.seed / "role-settings/ownership")
        RunnerLock.current_owned(directory / "learning-invocations")
        if store is None:
            require(
                not (directory / "learning").exists()
                and not (directory / "learning").is_symlink()
            )
            rows = None
        else:
            store._owned()
            store.ensure_usable()
            rows = store.records()
        expected = [
            row for row in witness.retained_origins
            if row["task"] == witness.task and row["store_kind"] == "workflow"
        ]
        require(len(expected) == 1 and expected[0]["records_digest"] == digest(rows))
        canonical = self._current(witness.task)
        require(canonical is not None)
        assert canonical is not None
        prefix = self._prefix_owned(
            witness.task, canonical, pending=canonical["invocation_digest"]
        )
        require(same(prefix, witness.completed_prefix))
        self._prefix_store_equal(witness.task, canonical, prefix, store)

    def _reserved_value(
        self,
        role: str,
        safe: dict[str, Any],
        canonical: dict[str, Any],
        native: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.witness is not None
        return core.seal(
            {
                "schema_version": "1.0",
                "identity": metadata["identity"],
                "role": role,
                "ordinal": metadata["ordinal"],
                "repair_iteration": metadata["repair_iteration"],
                "invocation_key": metadata["invocation_key"],
                "stable_metadata_digest": binding_records.stable_metadata(metadata),
                "native_invocation_digest": digest(safe),
                "native_reservation_digest": digest(native),
                "checkpoint_digest": digest(canonical),
                "ordered_lessons": copy.deepcopy(self.witness.selected_rows),
                "selection_digest": metadata["selection_digest"],
                "input_digest": metadata["input_digest"],
                "prompt_digest": metadata["prompt_digest"],
                "status": "RESERVED",
                "completion": None,
            }
        )

    def complete(self, role: str, safe: dict[str, Any]) -> None:
        try:
            require(self.capability is not None)
            assert self.capability is not None and self.context is not None
            witness, previous, invocation = self.capability
            require(witness is self.witness and witness.lifecycle == "CONSUMED")
            require(
                witness.runtime_identity is self.runtime
                and witness.reader_identity is self.reader
            )
            require(role == witness.role and invocation == digest(safe))
            self._guard(witness.task)
            ownership = self.reader.seed / "role-settings/ownership"
            with (
                RunnerLock(ownership),
                RunnerLock(self.context.directory / "learning-invocations"),
            ):
                canonical = self._current(witness.task)
                require(
                    canonical is not None
                    and digest(canonical) == previous["checkpoint_digest"]
                )
                assert canonical is not None
                chain, _ = self.reader._completed_role_chain(
                    witness.task, self.context.contract, canonical
                )
                original = chain.get((role, previous["ordinal"]))
                require(original is not None)
                assert original is not None
                native, source = original["reservation"], original["source"]
                reserved = {
                    **native,
                    "status": "RESERVED",
                    "snapshot_after": None,
                    "outcome_digest": None,
                }
                require(digest(reserved) == previous["native_reservation_digest"])
                require(native["invocation_digest"] == invocation)
                key = previous["invocation_key"]
                metadata = records.invocation(
                    self.reader._json(
                        self.context.directory
                        / "learning-invocations"
                        / (key + ".json")
                    )
                )
                require(metadata["status"] == "COMPLETED")
                require(
                    binding_records.stable_metadata(metadata)
                    == previous["stable_metadata_digest"]
                )
                require(
                    metadata["prompt_digest"]
                    == hashlib.sha256(
                        codex_adapter._prompt(role, safe).encode()
                    ).hexdigest()
                )
                self._problem(metadata, safe)
                target = self.context.directory / "learning"
                secure_directory(target, self.reader.root)
                with Store(
                    target,
                    self.reader.root,
                    self.context.directory,
                    self.reader.owner,
                    validators=records.VALIDATORS,
                ) as store:
                    store.ensure_usable()
                    require(
                        {"kind": "invocation", "record": metadata} in store.records()
                    )
                    self._guard(witness.task)
                    value = core.seal(
                        {
                            **previous,
                            "status": "COMPLETED",
                            "completion": {
                                "native_reservation_digest": digest(native),
                                "settings_source_digest": digest(source),
                                "settings_receipt_digest": digest(source["receipt"]),
                                "terminal_digest": digest(source["terminal"]),
                                "completed_metadata_digest": digest(metadata),
                            },
                        }
                    )
                    require(
                        native["outcome_digest"] == source["receipt"]["receipt_digest"]
                    )
                    self._write(value, previous)
            self.capability = None
        except BaseException:
            self.invalidate()
            raise
