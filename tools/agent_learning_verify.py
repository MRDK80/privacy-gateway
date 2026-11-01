"""Independent rereads promote catalog candidates; hints grant no authority."""

from __future__ import annotations

from typing import Any

from tools import agent_gate
from tools import agent_learning as c
from tools import agent_learning_records as records
from tools import agent_orchestrate as workflow
from tools.agent_continuation import digest
from tools.agent_epic_loop import RunnerLock
from tools.agent_epic_session_runtime import SessionRuntime
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_sources import environment
from tools.agent_learning_store import Store

UNAVAILABLE = frozenset(
    {
        "LEARNING_SOURCE_MISSING",
        "LEARNING_SOURCE_PENDING",
        "LEARNING_DELIVERY_SOURCE_MISSING",
        "LEARNING_DELIVERY_SOURCE_PENDING",
        "LEARNING_CI_SOURCE_MISSING",
        "LEARNING_GATE_REQUIRED",
    }
)


def current_problem_class(
    reader: SourceReader,
    task_class: str,
    role: str,
    gate: dict[str, Any] | None,
) -> str | None:
    """Classify only the original current gate and one exact pinned predicate.

    Workflow supplies this gate separately from the untrusted role payload.
    Aggregate gate checks cannot claim an individual regression-test identity.
    """
    reader.guard()
    if role not in c.ROLES:
        raise c.LearningError("LEARNING_INVOCATION_INVALID")
    expected = list(agent_gate.PROFILES[agent_gate.FULL_PROFILE])
    if (
        not isinstance(gate, dict)
        or not agent_gate.is_repository_full(gate)
        or gate.get("schema_version") != agent_gate.EVIDENCE_SCHEMA_VERSION
        or gate.get("executed_checks") != expected
        or gate.get("complete") is not True
        or gate.get("status") != "failed"
        or gate.get("machine_code") != "GATE_FAILED"
    ):
        return None
    checks = gate.get("checks")
    if not isinstance(checks, list) or len(checks) != len(expected):
        return None
    failed: list[dict[str, Any]] = []
    for name, check in zip(expected, checks, strict=True):
        if (
            not isinstance(check, dict)
            or check.get("id") != name
            or check.get("argv") != list(agent_gate.CHECK_COMMANDS[name])
            or check.get("status") not in {"passed", "failed"}
            or type(check.get("exit_code")) is not int
            or not 0 <= check["exit_code"] <= 255
            or (check["status"] == "passed") != (check["exit_code"] == 0)
        ):
            return None
        if check["status"] == "failed":
            failed.append(
                {
                    "gate_profile": agent_gate.FULL_PROFILE,
                    "check_name": name,
                    "argv_digest": digest(check["argv"]),
                    "test_identity": None,
                }
            )
    catalog_role = "controller" if role == "planning_review" else role
    current_environment = environment(
        reader, planning=role in {"planner", "planning_review"}
    )
    matches = [
        rule
        for rule in reader.catalog["rules"]
        if rule["role"] == catalog_role
        and rule["task_class"] == task_class
        and rule["environment"] == current_environment
        and rule["symptom_code"] == gate["machine_code"]
        and all(predicate in failed for predicate in rule["regression_checks"])
    ]
    return matches[0]["problem_class"] if len(matches) == 1 else None


class Verifier:
    def __init__(self, reader: SourceReader, session: SessionRuntime) -> None:
        reader.guard()
        if (
            not isinstance(session, SessionRuntime)
            or session.state != reader.seed
            or session.root.resolve() != reader.root
            or dict(session.config) != reader.config
        ):
            raise c.LearningError("LEARNING_SOURCE_INVALID")
        self.reader, self.session = reader, session
        reader.planning_verifier = self

    def retained(self) -> list[dict[str, Any]]:
        return self._retained()

    def _retained_for_capture(self, task: int) -> list[dict[str, Any]]:
        # This internal path conveys no selection authority. Its sole caller
        # already owns the original task and validates terminal source facts.
        RunnerLock.current_owned(self.reader.task_directory(task))
        return self._retained(capturing=task)

    def _retained(self, *, capturing: int | None = None) -> list[dict[str, Any]]:
        return self._retained_originals(capturing=capturing)[0]

    def _retained_originals(
        self, *, capturing: int | None = None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        reader = self.reader
        reader.guard()
        result: list[dict[str, Any]] = []
        tasks: dict[int, list[dict[str, Any]]] = {}
        origins: list[dict[str, Any]] = []
        for task in reader._source_tasks():
            try:
                directory = reader.task_directory(task)
            except c.LearningError as error:
                if error.machine_code == "LEARNING_SOURCE_MISSING":
                    continue
                raise
            tasks[task] = []
            if not (directory / "learning").exists():
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
                    directory / "learning",
                    reader.root,
                    directory,
                    reader.owner,
                    validators=records.VALIDATORS,
                ) as store:
                    if task != capturing:
                        store.ensure_usable()
                    items = store.records()
            from tools.agent_learning_store import KEY_FIELDS

            origins.append(
                {
                    "task": task,
                    "store_kind": "workflow",
                    "records_digest": digest(items),
                    "record_identities": sorted(
                        [
                            {
                                "kind": row["kind"],
                                "key": row["record"][KEY_FIELDS[row["kind"]]],
                                "record_digest": digest(row["record"]),
                            }
                            for row in items
                        ],
                        key=lambda row: (row["kind"], row["key"]),
                    ),
                }
            )
            tasks[task] = items
            result.extend(items)
            if len(result) > reader.owner["limits"]["max_records"]:
                raise c.LearningError("LEARNING_STORAGE_EXHAUSTED")
        for task, items in tasks.items():
            if task != capturing:
                self._source_complete(task, items, result)
        return result, origins

    def _source_complete(
        self,
        task: int,
        items: list[dict[str, Any]],
        retained: list[dict[str, Any]],
    ) -> None:
        from tools import agent_epic_planning as admission
        from tools.agent_learning_measure import _metadata, comparison, observed

        reader = self.reader
        directory = reader.task_directory(task)
        with reader.ownership(directory):
            try:
                contract = reader.contract(task)
                canonical = reader._json(
                    directory
                    / "bounded-workflow"
                    / (workflow._run_key(contract) + ".json")
                )
                if canonical.get("terminal") is False:
                    if (
                        canonical.get("status") != "running"
                        or canonical.get("role_pending") is not None
                    ):
                        raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
                    # The terminal SourceReader deliberately accepts only the
                    # closed final shape. During an existing run, completed
                    # prior calls may select for the next original ordinal;
                    # RESERVED/UNKNOWN or missing call metadata cannot do so.
                    c.hash_value(canonical.get("run_id"), size=32)
                    active = _metadata(
                        reader,
                        directory / "learning-invocations",
                        task=task,
                        run_id=canonical["run_id"],
                    )
                    for role in ("executor", "controller"):
                        count = c.integer(
                            canonical.get(role + "_calls"),
                            contract.max_repair_iterations + 1,
                        )
                        if sum(item["role"] == role for item in active) != count:
                            raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
                    c.integer(
                        canonical.get("repair_iterations"),
                        contract.max_repair_iterations,
                    )
                    required_fields = {
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
                    }
                    optional_fields = {
                        "role_pending",
                        "controller_boundary",
                        "invocation_digest",
                        "completed_executor",
                        "continuation_gate",
                    }
                    if (
                        not required_fields.issubset(canonical)
                        or set(canonical) - required_fields - optional_fields
                    ):
                        raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
                    chain, _ = reader._completed_role_chain(task, contract, canonical)
                    indexes = {(item["role"], item["ordinal"]) for item in active}
                    if len(indexes) != len(active) or indexes != set(chain):
                        raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
                    for item in active:
                        original = chain[item["role"], item["ordinal"]]
                        if (
                            item["catalog_digest"] != reader.owner["catalog_digest"]
                            or item["repair_iteration"]
                            != original["checkpoint"]["repair_iterations"]
                        ):
                            raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
                    return
                fact = reader.workflow(task)
            except c.LearningError as error:
                if error.machine_code == "LEARNING_SOURCE_MISSING":
                    if any(
                        (item["kind"] == "invocation" and item["record"]["lesson_ids"])
                        or (
                            item["kind"] == "measurement"
                            and item["record"]["hint_set_digest"] is not None
                        )
                        for item in items
                    ):
                        raise
                    metadata_path = directory / "learning-invocations"
                    if metadata_path.is_symlink():
                        raise c.LearningError("LEARNING_STORAGE_UNSAFE") from None
                    if not metadata_path.exists():
                        return
                    try:
                        empty = not any(
                            path.name != "epic-loop.lock"
                            for path in metadata_path.iterdir()
                        )
                    except OSError:
                        raise c.LearningError("LEARNING_STORAGE_UNSAFE") from None
                    if empty:
                        return
                raise
            metadata = _metadata(
                reader,
                directory / "learning-invocations",
                task=task,
                run_id=fact.state["run_id"],
            )
            if any(
                sum(item["role"] == role for item in metadata)
                != fact.state[role + "_calls"]
                for role in ("executor", "controller")
            ):
                raise c.LearningError("LEARNING_MEASUREMENT_SOURCE_INVALID")
            planning = admission.Admission(reader.seed, reader.root)._storage(task)
            if (planning / "admission.json").exists():
                original = reader._json(planning / "admission.json")
                admission.exact(original, admission.RECORD_KEYS)
                identity = {
                    key: original[key]
                    for key in (
                        "schema_version",
                        "repository",
                        "epic",
                        "task",
                        "planning_digest",
                        "mandate_digest",
                        "policy_sha",
                    )
                }
                metadata += _metadata(
                    reader,
                    planning / "learning-invocations",
                    task=task,
                    run_id=digest(identity),
                )
            if not any(item["lesson_ids"] for item in metadata):
                return
            value, applied = observed(reader, self.session, task)
            reader.controller(task)
            _, evaluation = comparison(reader, self.session, value, retained)
            required = [("measurement", value), ("evaluation", evaluation)]
            required.extend(("invocation", item) for item in applied)
            if evaluation["comparison"]["status"] == "ROLLBACK_REQUIRED":
                required.extend(
                    (
                        "tombstone",
                        records.tombstone(
                            c.seal(
                                {
                                    "schema_version": "1.0",
                                    "lesson_id": lesson,
                                    "reason_code": "LEARNING_REGRESSION",
                                    "evaluation_digest": digest(evaluation),
                                }
                            )
                        ),
                    )
                    for lesson in sorted(
                        {lesson for item in applied for lesson in item["lesson_ids"]}
                    )
                )
            if any(
                {"kind": kind, "record": record} not in items
                for kind, record in required
            ):
                raise c.LearningError("LEARNING_ROLLBACK_PENDING")

    def applicability(self, task: int, rule: dict[str, Any]) -> str:
        fact = self.reader.workflow(task)
        contract = fact.contract
        grant = self.reader.mandate["task_grants"][str(task)]
        return digest(
            {
                "role": rule["role"],
                "problem_class": rule["problem_class"],
                "environment": rule["environment"],
                "policy_sha": self.reader.owner["policy_sha"],
                "catalog_digest": self.reader.owner["catalog_digest"],
                "criteria_digest": digest(list(contract.acceptance_criteria)),
                "grant_digest": digest(grant),
                "initial_head": fact.memory["head_sha"],
                "initial_base": contract.base_sha,
                "allowed_paths": list(contract.allowed_paths),
            }
        )

    def pair(
        self, before: dict[str, Any], after: dict[str, Any], rule: dict[str, Any]
    ) -> dict[str, Any] | None:
        reader = self.reader
        before, after = records.observation(before), records.observation(after)
        if (
            before["kind"] != "TERMINAL_WORKFLOW"
            or after["kind"] != "TASK_DELIVERY"
            or before["identity"]["run_id"] == after["identity"]["run_id"]
            or before["outcome"]["status"] in c.SUCCESS
            or rule["symptom_code"] not in before["outcome"]["machine_codes"]
            or rule["environment"] != environment(reader)
        ):
            return None
        previous, current = before["identity"]["task"], after["identity"]["task"]
        first, last = reader.workflow(previous), reader.workflow(current)
        for observed, fact in ((before, first), (after, last)):
            expected_identity = {
                "repository": reader.owner["repository"],
                "epic": reader.owner["epic"],
                "task": fact.contract.issue,
                "run_id": fact.state["run_id"],
                "policy_sha": reader.owner["policy_sha"],
            }
            expected_metrics = {
                key: fact.state[key]
                for key in (
                    "findings",
                    "repair_iterations",
                    "executor_calls",
                    "controller_calls",
                )
            }
            expected_metrics["duration_seconds"] = fact.memory["duration_seconds"]
            if (
                any(
                    observed["identity"][key] != value
                    for key, value in expected_identity.items()
                )
                or observed["metrics"] != expected_metrics
            ):
                return None
        if (
            before["outcome"]
            != {
                "status": first.state["status"],
                "machine_codes": sorted(set(first.state["failures"])),
            }
            or before["identity"]["head_sha"] != first.memory["head_sha"]
            or before["identity"]["merge_sha"] is not None
            or any(
                before["source"][key] is not None
                for key in records.SOURCE_FIELDS - {"workflow_digest"}
            )
            or first.state["status"] in c.SUCCESS
            or rule["symptom_code"] not in first.state["failures"]
            or last.state["status"] not in c.SUCCESS
            or after["outcome"] != {"status": "TASK_DONE", "machine_codes": []}
        ):
            return None
        if (
            first.contract.task_class != rule["task_class"]
            or last.contract.task_class != rule["task_class"]
            or first.state["run_id"] != before["identity"]["run_id"]
            or last.state["run_id"] != after["identity"]["run_id"]
            or digest({"state": first.state, "memory": first.memory})
            != before["source"]["workflow_digest"]
            or digest({"state": last.state, "memory": last.memory})
            != after["source"]["workflow_digest"]
            or self.applicability(previous, rule) != self.applicability(current, rule)
        ):
            return None
        controller = reader.controller(current)
        if controller[
            "status"
        ] not in c.SUCCESS or not agent_gate.gate_evidence_is_passing(
            controller["gate"]
        ):
            return None
        delivery = reader.delivery(current)
        ci_pr, ci_post = reader.ci(current, "pr"), reader.ci(current, "post-merge")
        demo = reader.demo(current, self.session)
        expected = {
            "gate_digest": digest(controller["gate"]),
            "controller_digest": controller["source_digest"],
            "delivery_digest": delivery["source_digest"],
            "pr_ci_digest": digest(ci_pr),
            "post_ci_digest": digest(ci_post),
            "demo_digest": digest(demo),
        }
        if (
            any(after["source"][key] != value for key, value in expected.items())
            or after["identity"]["head_sha"] != delivery["head_sha"]
            or after["identity"]["merge_sha"] != delivery["merge_sha"]
        ):
            return None
        failed, passed = reader.gate(previous), reader.gate(current)
        for identity in rule["regression_checks"]:

            def matches(row: dict[str, Any], outcome: str) -> bool:
                return any(
                    all(check[key] == value for key, value in identity.items())
                    and check["outcome"] == outcome
                    for check in row["checks"]
                )

            if not any(matches(row, "FAIL") for row in failed):
                return None
            if not any(
                matches(row, "PASS")
                and row["snapshot_digest"] == digest(controller["gate"]["snapshot"])
                for row in passed
            ):
                return None
        return {
            "before_digest": digest(before),
            "after_digest": digest(after),
            "applicability_digest": self.applicability(current, rule),
            "evidence_digest": digest(
                {
                    "first": {"state": first.state, "memory": first.memory},
                    "last": {"state": last.state, "memory": last.memory},
                    "controller": controller,
                    "delivery": delivery,
                    "pr_ci": ci_pr,
                    "post_ci": ci_post,
                    "demo": demo,
                    "failed_checks": failed,
                    "passing_checks": passed,
                }
            ),
        }

    def candidates(self, task: int) -> list[dict[str, Any]]:
        reader = self.reader
        retained = self.retained()
        observations = [
            item["record"] for item in retained if item["kind"] == "observation"
        ]
        corrections = [
            item
            for item in observations
            if item["kind"] == "TASK_DELIVERY" and item["identity"]["task"] == task
        ]
        results = []
        for rule in reader.catalog["rules"]:
            for after in corrections:
                for before in observations:
                    try:
                        proof = self.pair(before, after, rule)
                    except c.LearningError as error:
                        if error.machine_code in UNAVAILABLE:
                            continue
                        raise
                    if proof is None:
                        continue
                    value = {
                        "schema_version": "1.0",
                        "rule_id": rule["rule_id"],
                        "role": rule["role"],
                        "observation_digest": proof["after_digest"],
                        "applicability_digest": proof["applicability_digest"],
                        "evidence_digest": proof["evidence_digest"],
                    }
                    value["candidate_id"] = digest(value)
                    candidate = records.candidate(c.seal(value))
                    verified = {
                        "schema_version": "1.0",
                        "candidate_digest": digest(candidate),
                        "catalog_digest": reader.owner["catalog_digest"],
                        "observation_digest": proof["after_digest"],
                        "evidence_digest": proof["evidence_digest"],
                        "applicability_digest": proof["applicability_digest"],
                    }
                    verified["lesson_id"] = digest(verified)
                    verified = records.verification(c.seal(verified))
                    directory = reader.task_directory(task)
                    with reader.ownership(directory):
                        with Store(
                            directory / "learning",
                            reader.root,
                            directory,
                            reader.owner,
                            validators=records.VALIDATORS,
                        ) as store:
                            store.append("candidate", candidate, "candidate_id")
                            store.append("verification", verified, "lesson_id")
                    results.append(verified)
        return results

    def selection(
        self, task_class: str, role: str, problem_class: str | None = None
    ) -> dict[str, Any]:
        if role not in c.ROLES:
            raise c.LearningError("LEARNING_INVOCATION_INVALID")
        return self._selection_original(
            task_class, role, problem_class, self.retained()
        )[0]

    def selection_proof(
        self, task_class: str, role: str, problem_class: str | None = None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if role not in c.ROLES:
            raise c.LearningError("LEARNING_INVOCATION_INVALID")
        retained, origins = self._retained_originals()
        value, references = self._selection_original(
            task_class, role, problem_class, retained
        )
        self._workflow_selection_origins = origins
        return value, references

    def _selection_original(
        self,
        task_class: str,
        role: str,
        problem_class: str | None,
        retained: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        reader = self.reader
        if role not in c.ROLES:
            raise c.LearningError("LEARNING_INVOCATION_INVALID")
        catalog_role = "controller" if role == "planning_review" else role
        tombstones = {
            item["record"]["lesson_id"]
            for item in retained
            if item["kind"] == "tombstone"
        }
        observations = [
            item["record"] for item in retained if item["kind"] == "observation"
        ]
        candidates = {
            digest(item["record"]): item["record"]
            for item in retained
            if item["kind"] == "candidate"
        }
        selected: list[dict[str, Any]] = []
        references: list[dict[str, Any]] = []
        verifications = [item for item in retained if item["kind"] == "verification"]
        for item in sorted(verifications, key=lambda item: item["record"]["lesson_id"]):
            verified = item["record"]
            if (
                verified["lesson_id"] in tombstones
                or verified["catalog_digest"] != reader.owner["catalog_digest"]
            ):
                continue
            candidate = candidates.get(verified["candidate_digest"])
            if candidate is None:
                raise c.LearningError("LEARNING_SOURCE_MISSING")
            rules = [
                rule
                for rule in reader.catalog["rules"]
                if rule["rule_id"] == candidate["rule_id"]
                and rule["role"] == catalog_role
                and rule["task_class"] == task_class
                and problem_class is not None
                and rule["problem_class"] == problem_class
                and rule["environment"]
                == environment(reader, planning=role in {"planner", "planning_review"})
            ]
            if not rules:
                continue
            rule = rules[0]
            afters = [
                obs
                for obs in observations
                if digest(obs) == verified["observation_digest"]
            ]
            confirmed = False
            confirmed_reference: dict[str, Any] | None = None
            for after in afters:
                for before in observations:
                    proof = self.pair(before, after, rule)
                    if proof is not None and all(
                        proof[key] == verified[key]
                        for key in ("evidence_digest", "applicability_digest")
                    ):
                        confirmed = True
                        confirmed_reference = {
                            "lesson_id": verified["lesson_id"],
                            "verification_digest": digest(verified),
                            "candidate_digest": digest(candidate),
                            "before_digest": digest(before),
                            "after_digest": digest(after),
                            "pair_digest": digest(proof),
                        }
                        break
            if not confirmed:
                raise c.LearningError("LEARNING_VERIFICATION_STALE")
            hint = {
                "lesson_id": verified["lesson_id"],
                "trust": "UNTRUSTED_VERIFIED_HINT",
                "problem_class": rule["problem_class"],
                "symptom_code": rule["symptom_code"],
                "diagnostic_steps": rule["diagnostic_steps"],
                "minimal_fix": rule["minimal_fix"],
                "applicability": rule["applicability"],
                "permissions": None,
            }
            if digest(hint) not in {digest(value) for value in selected}:
                selected.append(hint)
                assert confirmed_reference is not None
                references.append(confirmed_reference)
            if len(selected) >= reader.owner["limits"]["max_hints_per_role"]:
                break
        if reader.owner["limits"]["max_hints_per_role"] == 0:
            selected = []
            references = []
        value = {
            "schema_version": "1.0",
            "role": role,
            "catalog_digest": reader.owner["catalog_digest"],
            "selection_digest": digest(selected),
            "hints": selected,
        }
        import json

        if (
            len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
            > reader.owner["limits"]["max_hint_bytes"]
        ):
            raise c.LearningError("LEARNING_HINT_CAPACITY_EXHAUSTED")
        if len(references) != len(selected) or any(
            hint["lesson_id"] != row["lesson_id"]
            for hint, row in zip(selected, references)
        ):
            raise c.LearningError("LEARNING_SOURCE_MISMATCH")
        return value, references
