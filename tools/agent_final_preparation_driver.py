"""Config8 preparation-only Session driver; owner continuation is separate."""

from __future__ import annotations

import time
from typing import Any

from tools import agent_coordinator_handover as handover
from tools import agent_epic_runtime_cli as guards
from tools import agent_epic_wait as wait
from tools import agent_final_preparation_delivery as delivery
from tools import agent_final_preparation_records as records
from tools import agent_final_preparation_runtime as original
from tools.agent_epic_loop import PhaseBlocked, RunnerLock
from tools.agent_epic_session_runtime import SessionRuntime
from tools.agent_final_preparation_report import prepare_report
from tools.agent_final_preparation_role import RoleCounter
from tools.agent_final_preparation_sources import (
    CurrentPreparation,
    _current_dependency_gate,
    _driver_postaggregate_segment,
    _driver_preflight_segment,
    _driver_preparation_segment,
    _historical_capture_lifetime,
)
from tools.agent_final_preparation_storage import Store
from tools.agent_owner_final_records import read_original_shapes


def _require(condition: bool) -> None:
    if not condition:
        raise PhaseBlocked("FINAL_APPROVAL_INVALID")


class _Ledger:
    """One exact typed preparation ledger in the original owned Store."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def assert_owned(self) -> None:
        self.store.assert_owned()

    def load(self) -> dict[str, dict[str, Any]]:
        self.assert_owned()
        value = self.store.read("ledger")
        return {} if value is None else delivery.entries(value)

    def save(self, value: dict[str, dict[str, Any]]) -> None:
        self.store.write("ledger", delivery.entries(value))


class PreparationDriver:
    def __init__(self, session: SessionRuntime, iterations: int) -> None:
        _require(type(session) is SessionRuntime)
        _require(session.config.get("schema_version") == "8.0")
        _require(session.config["approved_final_digest"] is None)
        self.session, self.iterations = session, iterations
        scope = session.wait_scope
        _require(scope is not None and scope.active_stage == "FINAL:FINAL_GATE")
        assert scope is not None
        self.scope = scope
        RunnerLock.current_owned(session.state / "session-control")

    def _store(self) -> tuple[Store, RoleCounter, original._CurrentFinalSources]:
        pins = original._CurrentFinalSources(self.session)
        sources = guards.final_preparation_original_sources(
            self.session.state, self.session.root,
        )
        checked = original._session_final_sources(
            self.session.state, self.session.root, sources,
        )
        _require(checked["iterations"] == self.iterations)
        # No role zero or preparation directory is initialized by this driver.
        guards.secure_directory(
            self.session.state / "final-preparation", self.session.root,
        )
        store = Store(
            self.session.state, self.session.root, sources["profile"], pins.check,
        )
        counter = RoleCounter(store, self.session.config["approved_launch_digest"])
        return store, counter, pins

    def _state(
        self, store: Store, consumed: int, *, phase: str = "PREPARING",
        candidate: str | None = None, pending: str | None = None,
        anchor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = store.read("final-state")
        if previous is not None:
            _require(previous["schema_version"] == "2.0")
            if previous["owner_wait"] is not None:
                _require(anchor is None or anchor == previous["owner_wait"])
                anchor = previous["owner_wait"]
            _require(consumed >= previous["calls_consumed"])
        value = records.state2(records.seal({
            "schema_version": "2.0",
            **{key: (store.owner[key] if key != "launch_digest" else
                     self.session.config["approved_launch_digest"])
               for key in records.IDENTITY},
            "phase": phase, "candidate_digest": candidate,
            "pending_operation_id": pending, "calls_consumed": consumed,
            "blocked_code": None, "owner_wait": anchor,
        }))
        if previous is not None:
            records.state2_transition(previous, value)
        store.write("final-state", value)
        return value

    def _synchronize_first(self, profile: dict[str, Any]) -> None:
        session = self.session
        session._guard_mutation(self.iterations, final=True)
        if handover._git(session.root, "status", "--porcelain=v1", "-z"):
            raise PhaseBlocked("DIRTY_WORKTREE")
        before = handover._git(session.root, "rev-parse", "HEAD")
        records.hash_value(before, sha=True)
        refs = session.github.branches(profile["repository"])
        main, roadmap = refs.get("main"), refs.get(profile["roadmap_ref"])
        records.hash_value(main, sha=True)
        records.hash_value(roadmap, sha=True)
        owner = {**profile, "main_sha": main, "roadmap_sha": roadmap}
        session._guard_mutation(self.iterations, final=True)
        if (
            handover._git(session.root, "status", "--porcelain=v1", "-z")
            or handover._git(session.root, "rev-parse", "HEAD") != before
        ):
            raise PhaseBlocked("DIRTY_WORKTREE")
        if handover._git(session.root, "rev-parse", "refs/heads/main") != main:
            raise PhaseBlocked("MAIN_CHANGED")
        session._sync_final(owner, iterations=self.iterations)
        session._guard_mutation(self.iterations, final=True)
        handover._git(session.root, "switch", profile["roadmap_ref"])
        _require(handover._git(session.root, "rev-parse", "HEAD") == roadmap)
        _require(not handover._git(session.root, "status", "--porcelain=v1", "-z"))
        session._guard_mutation(self.iterations, final=True)
        current = session.github.branches(profile["repository"])
        _require(current.get("main") == main)
        _require(current.get(profile["roadmap_ref"]) == roadmap)

    def _complete_review(self) -> None:
        with RunnerLock(self.session.state):
            store, counter, pins = self._store()
            with _driver_preflight_segment(self.session):
                # All structural source preflight occurs before the factory/model.
                with _current_dependency_gate(self.session):
                    original.structural_child_preflight(self.session)
                state, reservation = counter.read()
                _require(state is not None)
                assert state is not None
                previous = store.read("final-state")
                if previous is not None:
                    _require(previous["schema_version"] == "2.0")
                consumed = state["calls_consumed"]
                if consumed:
                    _require(reservation is not None)
                    assert reservation is not None
                    _require(reservation["status"] == "COMPLETED")
                    original.read_completed_learning_review_owned(self.session)
                    pins.check()
                    return
                _require(reservation is None)
                _require(previous is None or previous["owner_wait"] is None)
                self._state(store, 0)
                profile = store.owner
                pins.check()
        # The fixed aggregate owns the seed itself; never reacquire inside an
        # already-held seed lock or keep its capability across delivery/wait.
        self._synchronize_first(profile)
        started = int(time.time())
        mandate = guards.private_json(self.session.state / "mandate.json")
        deadline = int(min(
            self.scope.frame["deadline_at"],
            self.scope._active_reader().current_deadline,
            mandate["expires_at"],
            self.session.config["started_at"]
            + mandate["limits"]["max_duration_seconds"],
        ))
        _require(started < deadline)
        original.review_learning_owned(
            self.session, started_at=started, deadline_at=deadline,
        )
        with RunnerLock(self.session.state):
            store, counter, pins = self._store()
            with _driver_postaggregate_segment(self.session):
                state, reservation = counter.read()
                _require(state is not None and state["calls_consumed"] == 1)
                _require(
                    reservation is not None and reservation["status"] == "COMPLETED"
                )
                self._state(store, 1)
                pins.check()

    def _anchor(self, source: CurrentPreparation, pr: int) -> dict[str, Any]:
        source.stable_assessment()
        reader = self.scope._active_reader()
        saved = reader.inspect()
        item = saved["ci"]
        _require(isinstance(item, dict) and item["status"] == "READY")
        _require(item["identity_digest"] == wait.digest(source.ci_identity(pr)))
        _require(item["attempts"] < reader.limits["max_ci_polls"])
        _require(not any(r["status"] != "READY" for r in saved["requests"].values()))
        mandate = guards.private_json(self.session.state / "mandate.json")
        deadline = min(
            self.scope.frame["deadline_at"], saved["deadline_at"],
            mandate["expires_at"], self.session.config["started_at"]
            + mandate["limits"]["max_duration_seconds"],
        )
        _require(saved["started_at"] <= saved["last_seen_at"] <= deadline)
        _require(saved["last_seen_at"] <= time.time() < deadline)
        _require(self.scope.control is not None)
        anchor = records.owner_wait({
            "invocation_id": self.scope.frame["invocation_id"],
            "frame_digest": wait.digest(self.scope.frame),
            "wait_identity_digest": wait.digest(source.ci_identity(pr)),
            "runtime_config_digest": self.scope.frame["runtime_config_digest"],
            "control_digest": wait.digest(self.scope.control),
            "stage_started_at": saved["started_at"], "deadline_at": deadline,
        })
        from tools.agent_epic_wait_frame import OwnerFrameSources

        # inspect() released its ordinary lock. Reacquire the existing canonical
        # wait lock, then prove the actual source route and original parent graph.
        with RunnerLock.owner_existing(reader.directory, self.session.root):
            with OwnerFrameSources.for_publication(
                self.session, anchor,
            ) as source_frames:
                current = source_frames._wait_original()
                _require(current["ci"]["status"] == "READY")
                _require(current["ci"]["attempts"] < reader.limits["max_ci_polls"])
                _require(all(
                    v["status"] == "READY" for v in current["requests"].values()
                ))
                source_frames.revalidate()
        return anchor

    def resume(self) -> dict[str, Any]:
        with _historical_capture_lifetime(self.session):
            self._complete_review()
            with RunnerLock(self.session.state):
                with _driver_preparation_segment(self.session):
                    source = CurrentPreparation(self.session)
                    store = Store(
                        self.session.state, self.session.root,
                        source.profile, source.check,
                    )
                    assessment = source.stable_assessment()
                    store.write("assessment-" + assessment["record_digest"], assessment)
                    request = source.request()
                    old_state = store.read("final-state")
                    self._state(store, 1, pending=request["operation_id"])
                    result = delivery.deliver(
                        request, authority=source.authority, binding=source.binding,
                        assessment=lambda value: self._assessment(source, value),
                        ledger=_Ledger(store), effect=source.effect,
                        reconcile=source.reconcile,
                    )
                    if (
                        result.status not in {"APPLIED", "NO_OP"}
                        or result.receipt is None
                    ):
                        return {
                            "status": result.status,
                            "machine_code": result.machine_code,
                        }
                    receipt = delivery.receipt(result.receipt, request)
                    pr = receipt["pr"]
                    assert type(pr) is int
                    checked = self.scope._active_reader().poll_ci(
                        source.ci_identity(pr), lambda: source.primary_ci(pr),
                    )
                    if checked.get("status") != "PASS":
                        raise PhaseBlocked("FINAL_STAGE_GATE_INVALID")
                    source.stable_assessment()
                    candidate = records.candidate(records.seal({
                        "schema_version": "1.0",
                        **{key: request[key] for key in records.CANDIDATE
                           if key not in {"owner_identity", "pr", "pr_ci_digest"}},
                        "owner_identity": source.profile["owner_identity"], "pr": pr,
                        "pr_ci_digest": checked["pr_ci_digest"],
                    }, self_digest="candidate_digest"))
                    store.write("candidate-" + candidate["candidate_digest"], candidate)
                    children = original._structural_children_current_owned(self.session)
                    learning, comparison = source.report_statuses()
                    report = prepare_report(
                        source.profile, candidate, assessment,
                        child_numbers=tuple(row["task"] for row in children),
                        learning_status=learning, comparison_status=comparison,
                    )
                    store.write("report-" + report["record_digest"], report)
                    _require(
                        store.read("candidate-" + candidate["candidate_digest"])
                        == candidate
                    )
                    _require(store.read("report-" + report["record_digest"]) == report)
                    _require(
                        store.read("assessment-" + assessment["record_digest"])
                        == assessment
                    )
                    source.stable_assessment()
                    anchor = old_state["owner_wait"] if old_state is not None else None
                    if anchor is None:
                        anchor = self._anchor(source, pr)
                    else:
                        self._existing_anchor(source, anchor, pr)
                    self._state(
                        store, 1, phase="WAITING_OWNER_FINAL_APPROVAL",
                        candidate=candidate["candidate_digest"], anchor=anchor,
                    )
                    source.check()
                    mandate, _approved, _context = source.authority()
                    graph = read_original_shapes(
                        self.session.state, self.session.root, candidate,
                        max_bytes=source.profile["max_artifact_bytes"],
                        issued_at=mandate["issued_at"],
                        expires_at=mandate["expires_at"],
                    )
                    if graph is not None:
                        # This source slice deliberately delivers preparation only.
                        # A neutral graph is NOT installed original-source admission.
                        raise PhaseBlocked("FINAL_APPROVAL_INVALID")
                    return {
                        "status": "WAITING_OWNER_FINAL_APPROVAL",
                        "machine_code": "WAITING_OWNER_FINAL_APPROVAL",
                        "report": report,
                    }

    @staticmethod
    def _assessment(source: CurrentPreparation, value: Any) -> dict[str, Any]:
        source.binding(value)
        return source.stable_assessment()

    def _existing_anchor(
        self, source: CurrentPreparation, anchor: dict[str, Any], pr: int,
    ) -> None:
        anchor = records.owner_wait(anchor)
        # Ordinary resume retains the original anchor, even under a fresh
        # invocation. It never replaces it with the current frame/deadline.
        _require(anchor["wait_identity_digest"] == wait.digest(source.ci_identity(pr)))
        saved = self.scope._active_reader().inspect()
        _require(anchor["stage_started_at"] == saved["started_at"])
        _require(saved["started_at"] <= saved["last_seen_at"] <= saved["deadline_at"])
        _require(saved["last_seen_at"] <= time.time() < min(
            anchor["deadline_at"], saved["deadline_at"],
        ))
        _require(anchor["control_digest"] == wait.digest(self.scope.control))
        _require(
            anchor["runtime_config_digest"] == self.scope.frame["runtime_config_digest"]
        )
        source.check()


def validate_wait_result(value: Any) -> dict[str, Any]:
    """Bounded internal transport shape, never source or approval admission."""
    import json

    _require(type(value) is dict)
    _require(set(value) == {"status", "machine_code", "report"})
    _require(value["status"] == "WAITING_OWNER_FINAL_APPROVAL")
    _require(value["machine_code"] == "WAITING_OWNER_FINAL_APPROVAL")
    report = records.report(value["report"])
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    _require(len(encoded) <= 20000 and len(encoded.encode()) <= 262144)
    return {**value, "report": report}


def read_wait_status(seed: Any, root: Any) -> dict[str, Any] | None:
    """Read existing durable bounded identifiers, no Store/lock/Scope creation."""
    sources = guards.final_preparation_original_sources(seed, root)
    if sources["config"].get("schema_version") != "8.0":
        return None
    cap = sources["profile"]["max_artifact_bytes"]
    seed_fd = preparation_fd = -1
    try:
        seed_fd = guards._final_preseed_seed(seed, root)
        preparation_fd = guards._final_preseed_directory(seed_fd, "final-preparation")
        try:
            raw = guards._final_preseed_read(preparation_fd, "final-state.json", cap)
        except FileNotFoundError:
            return None
        final_state = records.state2(raw)
        if final_state["phase"] != "WAITING_OWNER_FINAL_APPROVAL":
            return None
        _require(final_state["owner_wait"] is not None)
        candidate = records.candidate(guards._final_preseed_read(
            preparation_fd,
            "candidate-" + final_state["candidate_digest"] + ".json", cap,
        ))
        _require(candidate["candidate_digest"] == final_state["candidate_digest"])
        for key in records.IDENTITY:
            expected = (sources["config"]["approved_launch_digest"]
                        if key == "launch_digest" else sources["profile"][key])
            _require(candidate[key] == final_state[key] == expected)
        _require(candidate["owner_identity"] == sources["profile"]["owner_identity"])
        _require(candidate["policy_sha"] == sources["profile"]["policy_sha"])
        _require(candidate["roadmap_ref"] == sources["profile"]["roadmap_ref"])
        _require(
            handover._git(root, "rev-parse", "refs/heads/main") == candidate["main_sha"]
        )
        _require(handover._git(
            root, "rev-parse", "refs/heads/" + candidate["roadmap_ref"],
        ) == candidate["roadmap_sha"])
        _require(guards._final_preseed_read(
            preparation_fd, "final-state.json", cap,
        ) == final_state)
        fresh = guards.final_preparation_original_sources(seed, root)
        _require(fresh == sources)
        return {
            "repository": candidate["repository"], "epic": candidate["epic"],
            "pr": candidate["pr"], "candidate_digest": candidate["candidate_digest"],
        }
    finally:
        import os

        for descriptor in (preparation_fd, seed_fd):
            if descriptor >= 0:
                os.close(descriptor)
