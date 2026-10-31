"""Fixed existing-original owner context; not human approval authority.

This context owns one procedure invocation's original locks and frame-read budget.
The installed procedure and complete five-original verifier remain separate joins.
No generic verifier, clock, transport or budget parameter is accepted here.
"""

from __future__ import annotations

import os
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast

from tools import agent_epic_runtime_cli as guards
from tools import agent_epic_wait as wait
from tools import agent_epic_wait_frame as frames
from tools.agent_epic_loop import PhaseBlocked, RunnerLock

if TYPE_CHECKING:
    from tools.agent_epic_session_runtime import SessionRuntime
    from tools.agent_final_preparation_ci import Comparison, PrimaryCI
    from tools.agent_final_preparation_sources import CurrentPreparation

_CURRENT: dict[tuple[int, Path, Path], _OriginalInvocation] = {}


@dataclass(frozen=True)
class _VerifiedEffectGate:
    request: Any
    ci_identity: str
    attempt: int
    candidate_digest: str
    approval_digest: str
    report_digest: str
    owner_graph: Any
    frame_digest: str
    deadline_at: float


def _invalid() -> NoReturn:
    raise PhaseBlocked("FINAL_APPROVAL_INVALID")


def _key(state: Path, root: Path) -> tuple[int, Path, Path]:
    return os.getpid(), state.resolve(strict=True), root.resolve(strict=True)


class _OriginalInvocation:
    """One fixed local-source lifetime, with no effect/approval permission."""

    def __init__(self, state: Path, root: Path) -> None:
        self.state, self.root = state, root
        self._key = _key(state, root)
        self._stack = ExitStack()
        self.frames: frames.OwnerFrameSources | None = None
        self.scope: Any = None
        self._normal_supervisor: RunnerLock | None = None
        self.normal_session: SessionRuntime | None = None
        self.session: SessionRuntime | None = None
        self.pre_merge_sources: CurrentPreparation | None = None
        self.candidate: dict[str, Any] | None = None
        self.report: dict[str, Any] | None = None
        self.approval: dict[str, Any] | None = None
        self._new_ledger_request: str | None = None
        self._gate_a_request: str | None = None
        self._verified_b: _VerifiedEffectGate | None = None
        self._owner_graph: Any = None
        self._observed_merge_sha: str | None = None
        self._view_flow = "UNSELECTED"
        self._view_poisoned = False
        self._view_clock: dict[str, Any] | None = None
        self._view_last_epoch: int | None = None
        self._a_witness: _OwnerAdmissionA | None = None
        self._intent_lineage: dict[str, Any] | None = None

    @classmethod
    def for_owned_session(cls, session: Any) -> _OriginalInvocation:
        from tools.agent_epic_session_runtime import SessionRuntime
        from tools.agent_epic_wait_scope import Scope

        if (
            type(session) is not SessionRuntime or type(session.wait_scope) is not Scope
            or not session.wait_scope._retained_owner
        ):
            _invalid()
        owned = RunnerLock.current_owned(session.state / "session-control")
        if type(owned) is not RunnerLock or (
            owned._inherited_fd is not None and not owned._normal_retained_child
        ):
            _invalid()
        session.wait_scope.guard()
        result = cls(session.state, session.root)
        result._normal_supervisor = owned
        result.normal_session = session
        return result

    def __enter__(self) -> _OriginalInvocation:
        if self._key in _CURRENT:
            _invalid()
        try:
            if self._normal_supervisor is None:
                self._stack.enter_context(RunnerLock.owner_existing(
                    self.state / "session-control", self.root,
                ))
            else:
                if (
                    RunnerLock.current_owned(self.state / "session-control")
                    is not self._normal_supervisor
                ):
                    _invalid()
                self._normal_supervisor.assert_owned(self.state / "session-control")
            self._stack.enter_context(RunnerLock.owner_existing(self.state, self.root))
            source = guards.final_preparation_original_sources(self.state, self.root)
            if source["config"].get("schema_version") != "8.0":
                _invalid()
            profile = guards.wait_profile_guard(
                self.state, source["config"], source["mandate"],
            )
            if profile is None:
                _invalid()
            identity = {
                "repository": source["mandate"]["repository"],
                "epic": source["mandate"]["epic"],
                "policy_sha": source["mandate"]["policy_sha"],
                "wait_digest": source["config"]["approved_wait_digest"],
                "stage": "FINAL:FINAL_GATE",
            }
            self.directory = (
                self.state / "read-waits"
                / wait.digest(identity).removeprefix("sha256:")
            )
            self._stack.enter_context(RunnerLock.owner_existing(
                self.directory, self.root,
            ))
            self.frames = frames.OwnerFrameSources(self.state, self.root)
            original = self.frames._wait_original()
            if (
                original["ci"]["status"] != "READY"
                or original["ci"]["attempts"] > profile["limits"]["max_ci_polls"]
                or any(v["status"] != "READY" for v in original["requests"].values())
            ):
                _invalid()
            self.identity, self.limits = identity, profile["limits"]
            self.original = original
            _CURRENT[self._key] = self
            if original["ci"]["attempts"] == profile["limits"]["max_ci_polls"]:
                # Only the fixed normal LOCAL terminal proof may admit an
                # exhausted READY record. Standalone/active paths still stop.
                _terminal_done(self)
            return self
        except BaseException:
            try:
                self.__exit__(None, None, None)
            except BaseException:
                pass
            raise

    def guard(self) -> None:
        if _CURRENT.get(_key(self.state, self.root)) is not self or self.frames is None:
            _invalid()
        self.frames.revalidate()

    def __exit__(self, kind: Any, value: Any, traceback: Any) -> None:
        failure: BaseException | None = None
        try:
            if self.frames is not None:
                self.frames.close()
        except BaseException as error:
            failure = error
        try:
            self._stack.close()
        except BaseException as error:
            if failure is None:
                failure = error
        finally:
            if _CURRENT.get(self._key) is self:
                del _CURRENT[self._key]
            _OWNER_VIEWS.pop(self, None)
            self._a_witness = None
            self._intent_lineage = None
            self.frames = None
            self.scope = None
        if failure is not None and kind is None:
            raise failure


def _current_sources(state: Path, root: Path) -> _OriginalInvocation:
    result = _CURRENT.get(_key(state, root))
    if type(result) is not _OriginalInvocation:
        _invalid()
    assert result is not None
    result.guard()
    return result


def _current_scope(scope: Any) -> _OriginalInvocation:
    from tools.agent_epic_wait_scope import Scope

    if type(scope) is not Scope:
        _invalid()
    result = _current_sources(scope.state, scope.root)
    if (
        result.scope is not scope or result.frames is None
        or scope.frame != result.frames.frame or scope.read_only
        or scope.config != result.frames.sources["config"]
        or scope.mandate != result.frames.sources["mandate"]
    ):
        _invalid()
    return result


def _existing_context(state: Path, root: Path) -> bool:
    """Fixed storage admission fact; never supplied by an artifact or callback."""
    result = _CURRENT.get(_key(state, root))
    if result is None:
        return False
    if type(result) is not _OriginalInvocation:
        _invalid()
    result.guard()
    return True


def _preparation_record(source: _OriginalInvocation, name: str) -> dict[str, Any]:
    from tools.agent_epic_wait_frame import _owner_close_all

    source.guard()
    if source.frames is None:
        _invalid()
    assert source.frames is not None
    parent = guards._final_preseed_directory(source.frames._seed, "final-preparation")
    try:
        value = guards._final_preseed_read(
            parent, name + ".json",
            source.frames.sources["profile"]["max_artifact_bytes"],
        )
    finally:
        _owner_close_all((parent,))
    source.guard()
    return value




_OWNER_VIEWS: dict[_OriginalInvocation, Any] = {}


def _owner_view_current(invocation: _OriginalInvocation) -> Any:
    return _OWNER_VIEWS.get(invocation)


def _owner_view_source(view: Any) -> Any:
    if view.source is None:
        _invalid()
    return view.source


def _owner_namespace_snapshot(invocation: _OriginalInvocation) -> Any:
    invocation.guard()
    try:
        result = _owner_namespace_identity(invocation)
    except FileNotFoundError:
        result = None
    invocation.guard()
    return result


def _owner_view_shape(view: Any) -> Any:
    from tools.agent_owner_final_records import read_original_shapes

    invocation = view.invocation
    candidate = view.bindings.get("candidate_value")
    if candidate is None or invocation.frames is None:
        _invalid()
    namespace = _owner_namespace_snapshot(invocation)
    mandate = invocation.frames.sources["mandate"]
    graph = read_original_shapes(
        invocation.state,
        invocation.root,
        candidate,
        max_bytes=_owner_view_source(view).profile["max_artifact_bytes"],
        issued_at=mandate["issued_at"],
        expires_at=mandate["expires_at"],
    )
    if _owner_namespace_snapshot(invocation) != namespace:
        _invalid()
    return namespace, graph


def _owner_view_clock(invocation: _OriginalInvocation, reader: Any) -> dict[str, Any]:
    from copy import deepcopy

    value = reader._load()
    persisted = deepcopy(value)
    now = reader._check(value)
    # This is the original Reader's one observed clock sample; keep it separate
    # from persisted last_seen_at and never make an untracked second sample.
    if type(now) not in {int, float}:
        _invalid()
    previous = getattr(invocation, "_view_clock", None)
    if previous is not None and (
        now < previous["observed"]
        or persisted["started_at"] != previous["persisted"]["started_at"]
        or persisted["last_seen_at"] < previous["persisted"]["last_seen_at"]
        or persisted["deadline_at"] > previous["persisted"]["deadline_at"]
        or persisted["ci"] is None
        or previous["persisted"]["ci"] is None
        or persisted["ci"]["identity_digest"]
        != previous["persisted"]["ci"]["identity_digest"]
        or persisted["ci"]["attempts"] < previous["persisted"]["ci"]["attempts"]
        or any(
            persisted["requests"].get(key) != old
            for key, old in previous["persisted"]["requests"].items()
        )
    ):
        _invalid()
    if (
        now < persisted["last_seen_at"]
        or now >= persisted["deadline_at"]
        or reader.current_deadline > persisted["deadline_at"]
        or any(row["status"] != "READY" for row in persisted["requests"].values())
    ):
        _invalid()
    invocation._view_clock = {"persisted": persisted, "observed": now}
    return cast(dict[str, Any], persisted)


def _owner_view_begin(view: Any) -> None:
    from copy import deepcopy

    from tools.agent_epic_session_runtime import SessionRuntime
    from tools.agent_final_preparation_records import state2
    from tools.agent_final_preparation_sources import (
        _HumanGateView,
        _MergeAGateView,
        _OwnerAGateView,
        _OwnerBGateView,
    )

    invocation = view.invocation
    if invocation in _OWNER_VIEWS or getattr(invocation, "_view_poisoned", False):
        _invalid()
    if type(view) not in {
        _HumanGateView,
        _MergeAGateView,
        _OwnerAGateView,
        _OwnerBGateView,
    }:
        _invalid()
    if (
        _current_scope(invocation.scope) is not invocation
        or type(invocation.session) is not SessionRuntime
        or invocation.session.wait_scope is not invocation.scope
        or invocation.frames is None
    ):
        _invalid()
    scope = invocation.scope
    if scope is None:
        _invalid()
    reader = scope._active_reader()
    if not (reader._owner_original and reader._owner_assessing and reader._active_poll):
        _invalid()
    saved = _owner_view_clock(invocation, reader)
    if saved["ci"] is None or saved["ci"]["status"] != "RESERVED":
        _invalid()
    final = state2(_preparation_record(invocation, "final-state"))
    history = None
    if view.kind not in {"HUMAN", "MERGE_A"}:
        history = _owner_ledger(invocation)
    if view.kind in {"HUMAN", "MERGE_A"}:
        if (
            final["phase"] != "WAITING_OWNER_FINAL_APPROVAL"
            or final["pending_operation_id"] is not None
            or saved["ci"]["attempts"] != invocation.original["ci"]["attempts"] + 1
        ):
            _invalid()
        if view.kind == "HUMAN":
            if invocation.normal_session is not None or reader._owner_continuing:
                _invalid()
        elif (
            invocation.normal_session is not invocation.session
            or not reader._owner_continuing
        ):
            _invalid()
    else:
        if (
            invocation.normal_session is not invocation.session
            or not reader._owner_continuing
        ):
            _invalid()
        if getattr(invocation, "_view_flow", "UNSELECTED") != "FRESH_OPTIMIZED":
            _invalid()
        _owner_validate_view_request(invocation, view.request)
        if view.kind == "OWNER_A":
            if (
                saved["ci"]["attempts"]
                != getattr(invocation, "_view_last_epoch", -1) + 1
            ):
                _invalid()
            _owner_validate_a_epoch(invocation, view.request, final, history)
        else:
            _owner_consume_intent_lineage(invocation, view.request, saved)
    _installed_sources(invocation)
    view.phase = "BUILDING"
    view.bindings = {
        "scope": scope,
        "session": invocation.session,
        "reader": reader,
        "guard": reader.guard,
        "stage": scope.active_stage,
        "supervisor": RunnerLock.current_owned(invocation.state / "session-control"),
        "owner": RunnerLock.current_owned(invocation.state),
        "ancestor": deepcopy(
            {
                "frame": scope.frame,
                "control": scope.control,
                "config": scope.config,
                "mandate": scope.mandate,
            }
        ),
        "prior": {
            key: getattr(invocation, key)
            for key in (
                "pre_merge_sources",
                "candidate",
                "report",
                "approval",
                "_owner_graph",
            )
        },
        "expected": {
            key: getattr(invocation, key)
            for key in (
                "pre_merge_sources",
                "candidate",
                "report",
                "approval",
                "_owner_graph",
            )
        },
        "final": deepcopy(final),
        "ledger": deepcopy(history),
        "ci": deepcopy(saved["ci"]),
    }
    _OWNER_VIEWS[invocation] = view


def _owner_view_ready(view: Any) -> None:
    from copy import deepcopy

    from tools import agent_final_preparation_records as records

    invocation = view.invocation
    current = _owner_view_source(view)
    if any(
        getattr(invocation, key) is not value
        for key, value in view.bindings["prior"].items()
    ):
        _invalid()
    final = view.bindings["final"]
    candidate = records.candidate(
        _preparation_record(
            invocation,
            "candidate-" + final["candidate_digest"],
        )
    )
    if (
        final["owner_wait"] != invocation.frames.anchor
        or candidate["candidate_digest"] != final["candidate_digest"]
        or candidate["preparation_digest"] != current.baseline["record_digest"]
    ):
        _invalid()
    if invocation.candidate is not None and invocation.candidate != candidate:
        _invalid()
    view.bindings["candidate_value"] = deepcopy(candidate)
    history = _owner_ledger(invocation)
    view.bindings["ledger"] = deepcopy(history)
    if view.kind in {"HUMAN", "MERGE_A"} and history is not None:
        invocation._view_flow = "FULL_DEFAULT"
        del _OWNER_VIEWS[invocation]
        view.phase = "DEFAULT"
        return
    view.bindings["assessment_value"] = deepcopy(current.baseline)
    view.bindings["candidate_record"] = "candidate-" + candidate["candidate_digest"]
    view.bindings["assessment_record"] = (
        "assessment-" + current.baseline["record_digest"]
    )
    view.bindings["graph"] = _owner_view_shape(view)
    if view.kind == "HUMAN" and view.bindings["graph"][1] is not None:
        invocation._view_flow = "FULL_DEFAULT"
        del _OWNER_VIEWS[invocation]
        view.phase = "DEFAULT"
        return
    if view.kind == "MERGE_A":
        view.request = _typed_owner_request(candidate, "merge_roadmap_pr")
        invocation._view_flow = "FRESH_OPTIMIZED"
    elif view.kind == "HUMAN":
        invocation._view_flow = "HUMAN_OPTIMIZED"
    view.optimized = True
    view.phase = "DERIVING"
    _owner_view_check(view)


def _owner_view_derivation(view: Any, receiver: Any) -> None:
    from tools.agent_final_preparation_sources import _OwnerGateDerivations

    if type(receiver) is not _OwnerGateDerivations or receiver is not view.derivations:
        _invalid()
    _owner_view_check(view)


def _owner_view_check(view: Any) -> None:
    from tools import agent_epic_final_delivery as final_delivery
    from tools import agent_final_preparation_records as records

    if view is None:
        _invalid()
    invocation = view.invocation
    binding = view.bindings
    source = _owner_view_source(view)
    scope = binding["scope"]
    if scope is None or invocation.session is None or source.session is None:
        _invalid()
    if (
        _owner_view_current(invocation) is not view
        or not view.optimized
        or view.phase not in {"DERIVING", "PROVISIONAL", "EXITING"}
        or getattr(invocation, "_view_poisoned", False)
        or _current_scope(scope) is not invocation
        or invocation.scope is not scope
        or invocation.session is not binding["session"]
        or source.session is not binding["session"]
        or source.session.wait_scope is not scope
        or scope._active_reader() is not binding["reader"]
        or binding["reader"].guard is not binding["guard"]
        or scope.active_stage != binding["stage"]
        or RunnerLock.current_owned(invocation.state / "session-control")
        is not binding["supervisor"]
        or RunnerLock.current_owned(invocation.state) is not binding["owner"]
        or {
            "frame": scope.frame,
            "control": scope.control,
            "config": scope.config,
            "mandate": scope.mandate,
        }
        != binding["ancestor"]
        or any(
            getattr(invocation, key) is not value
            for key, value in binding["expected"].items()
        )
    ):
        _invalid()
    source.check()
    _installed_sources(invocation)
    if view.kind == "HUMAN":
        source.session._fresh_authority(source.iterations, at_task_start=False)
    else:
        code = final_delivery.authorization_code(view.request, source.authority)
        if code is not None:
            raise PhaseBlocked(code)
    saved = _owner_view_clock(invocation, binding["reader"])
    if (
        saved["ci"] is None
        or saved["ci"]["status"] != "RESERVED"
        or saved["ci"]["attempts"] != binding["ci"]["attempts"]
        or saved["ci"]["identity_digest"] != binding["ci"]["identity_digest"]
        or records.state2(_preparation_record(invocation, "final-state"))
        != binding["final"]
        or records.candidate(
            _preparation_record(invocation, binding["candidate_record"])
        )
        != binding["candidate_value"]
        or records.assessment(
            _preparation_record(invocation, binding["assessment_record"])
        )
        != binding["assessment_value"]
        or _owner_ledger(invocation) != binding["ledger"]
        or _owner_view_shape(view) != binding["graph"]
    ):
        _invalid()
    if (
        "report_record" in binding
        and records.report(
            _preparation_record(
                invocation,
                binding["report_record"],
            )
        )
        != binding["report_value"]
    ):
        _invalid()
    invocation.guard()


def _owner_publish_join_bindings(
    invocation: _OriginalInvocation,
    receiver: Any,
    candidate: dict[str, Any],
    report: dict[str, Any],
) -> None:
    from copy import deepcopy

    view = _owner_view_current(invocation)
    if view is None:
        invocation.pre_merge_sources = receiver
        invocation.candidate, invocation.report = candidate, report
        return
    if receiver is not view.derivations or view.phase != "DERIVING":
        _invalid()
    _owner_view_check(view)
    for key, proposed in (("candidate", candidate), ("report", report)):
        previous = view.bindings["prior"][key]
        if previous is not None and previous != proposed:
            _invalid()
    invocation.pre_merge_sources = _owner_view_source(view)
    invocation.candidate, invocation.report = candidate, report
    view.bindings["expected"].update(
        pre_merge_sources=invocation.pre_merge_sources,
        candidate=candidate,
        report=report,
    )
    view.bindings["report_record"] = "report-" + report["record_digest"]
    view.bindings["report_value"] = deepcopy(report)
    view.phase = "PROVISIONAL"
    _owner_view_check(view)


def _owner_request_for_receipt(invocation: _OriginalInvocation, current: Any) -> Any:
    view = _owner_view_current(invocation)
    if view is None:
        return current.request()
    if current is not view.source:
        _invalid()
    _owner_view_check(view)
    return view.derivations.request()


def _owner_semantic_boundary(invocation: _OriginalInvocation, current: Any) -> None:
    view = _owner_view_current(invocation)
    if view is None:
        current.stable_assessment()
        return
    if current is not view.source or view.phase not in {"PROVISIONAL", "EXITING"}:
        _invalid()
    _owner_view_check(view)


def _owner_approval_observe(invocation: _OriginalInvocation, graph: Any) -> None:
    from copy import deepcopy

    view = _owner_view_current(invocation)
    if view is None:
        return
    _owner_semantic_boundary(invocation, invocation.pre_merge_sources)
    if graph is None or graph != view.bindings["graph"][1]:
        _invalid()
    view.approval_read = {"stage": "OBSERVED", "graph": deepcopy(graph)}


def _owner_approval_verified(
    invocation: _OriginalInvocation,
    candidate: Any,
    approval: Any,
    pin: Any,
    receipt: Any,
) -> None:
    view = _owner_view_current(invocation)
    if view is None:
        return
    item = view.approval_read
    if item is None or item["stage"] != "OBSERVED":
        _invalid()
    graph = item["graph"]
    if (
        candidate != view.bindings["candidate_value"]
        or approval != graph.approval
        or pin != graph.pin
        or receipt != graph.receipt
    ):
        _invalid()
    item["stage"] = "VERIFIED"


def _owner_approval_reader_return(invocation: _OriginalInvocation, result: Any) -> None:
    view = _owner_view_current(invocation)
    if view is None:
        return
    item = view.approval_read
    if item is None or item["stage"] != "VERIFIED" or result != item["graph"].approval:
        _invalid()
    item["stage"] = "RETURNED"


def _owner_approval_complete(
    invocation: _OriginalInvocation, result: Any, graph: Any
) -> None:
    from copy import deepcopy

    view = _owner_view_current(invocation)
    if view is None:
        return
    item = view.approval_read
    if item is None or item["stage"] != "RETURNED" or result != item["graph"].approval:
        _invalid()
    if graph != item["graph"] or invocation._owner_graph != graph:
        _invalid()
    view.bindings["expected"]["_owner_graph"] = invocation._owner_graph
    view.admitted_approval = deepcopy(result)
    item["stage"] = "COMPLETE"
    _owner_view_check(view)


def _owner_assign_join_approval(invocation: _OriginalInvocation, result: Any) -> None:
    view = _owner_view_current(invocation)
    if view is not None:
        if result is None:
            if view.bindings["graph"][1] is not None:
                _invalid()
        elif (
            view.approval_read is None
            or view.approval_read["stage"] != "COMPLETE"
            or result != view.admitted_approval
        ):
            _invalid()
    invocation.approval = result
    if view is not None:
        view.bindings["expected"]["approval"] = result
        _owner_view_check(view)


def _owner_poison_view(invocation: _OriginalInvocation) -> None:
    invocation._view_poisoned = True
    invocation._a_witness = None
    invocation._intent_lineage = None
    invocation._verified_b = None
    invocation._gate_a_request = None
    invocation._new_ledger_request = None


def _owner_view_finish(view: Any, error: BaseException | None) -> None:
    invocation = view.invocation
    if view.phase == "DEFAULT":
        return
    primary = error
    if (
        primary is None
        and view.result is not None
        and view.result.get("status") not in {"PASS", "CI_PENDING"}
    ):
        primary = PhaseBlocked(
            view.result.get("machine_code", "FINAL_STAGE_GATE_INVALID")
        )
    if view.source is not None:
        view.phase = "EXITING"
        try:
            _owner_view_check(view)
        except BaseException as failure:
            if primary is None:
                primary = failure
        try:
            view.source.stable_assessment()
        except BaseException as failure:
            if primary is None:
                primary = failure
        try:
            if (
                invocation.pre_merge_sources is not view.source
                or invocation.candidate is None
            ):
                _invalid()
            graph = view.bindings.get("graph", (None, None))[1]
            if graph is None:
                if _owner_view_shape(view)[1] is not None:
                    _invalid()
            elif _current_owner_approval(invocation) != invocation.approval:
                _invalid()
        except BaseException as failure:
            if primary is None:
                primary = failure
    elif primary is None:
        try:
            _invalid()
        except BaseException as failure:
            primary = failure
    try:
        if primary is None:
            _owner_view_check(view)
            invocation._view_last_epoch = view.bindings["ci"]["attempts"]
            if view.kind in {"MERGE_A", "OWNER_A"} and invocation.approval is not None:
                if view.result is not None and view.result.get("status") == "PASS":
                    invocation._a_witness = _OwnerAdmissionA(view)
                elif (
                    view.result is not None
                    and view.result.get("status") == "CI_PENDING"
                ):
                    invocation._view_flow = "DISABLED_DEFAULT"
            view.phase = "CLOSED"
    except BaseException as failure:
        if primary is None:
            primary = failure
    finally:
        if _OWNER_VIEWS.get(invocation) is view:
            del _OWNER_VIEWS[invocation]
        if primary is not None:
            for key, value in view.bindings.get("prior", {}).items():
                setattr(invocation, key, value)
            _owner_poison_view(invocation)
            view.phase = "POISONED"
        view.facts.clear()
        view.approval_read = None
    if primary is not None:
        raise primary


def _owner_mainci_outer_hook(group: Any) -> bool:
    view = _owner_view_current(group.invocation)
    if view is None:
        return False
    if (
        view.kind not in {"OWNER_A", "OWNER_B"}
        or view.phase not in {"PROVISIONAL", "EXITING"}
        or view.request is not group.request
        or view.source is not group.current
        or view.bindings["scope"] is not group.scope
        or view.bindings["reader"] is not group.reader
        or _MAIN_CI_GROUPS.get(group.invocation) is not group
    ):
        _invalid()
    _owner_view_check(view)
    return True


def _owner_validate_view_request(invocation: _OriginalInvocation, request: Any) -> None:
    from dataclasses import asdict

    from tools import agent_epic_final_delivery as final_delivery

    if type(request) is not final_delivery.Request or invocation.candidate is None:
        _invalid()
    expected = _typed_owner_request(
        invocation.candidate,
        request.operation,
        request.merge_sha,
    )
    if asdict(request) != asdict(expected):
        _invalid()


def _owner_validate_a_epoch(
    invocation: _OriginalInvocation,
    request: Any,
    final: Any,
    history: Any,
) -> None:
    _owner_validate_view_request(invocation, request)
    candidate = invocation.candidate
    if candidate is None or history is None or request.operation_id in history:
        _invalid()
    merge = _typed_owner_request(candidate, "merge_roadmap_pr")
    merge_entry = history.get(merge.operation_id)
    if (
        final["phase"] != "MAIN_CI_PENDING"
        or request.operation not in {"close_epic", "update_epic"}
        or type(merge_entry) is not dict
        or merge_entry["status"] != "APPLIED"
        or merge_entry["receipt"]["merge_sha"] != request.merge_sha
    ):
        _invalid()
    expected_pending = merge
    expected_keys = {merge.operation_id}
    if request.operation == "update_epic":
        closed = _typed_owner_request(candidate, "close_epic", request.merge_sha)
        if history.get(closed.operation_id, {}).get("status") != "APPLIED":
            _invalid()
        expected_pending = closed
        expected_keys.add(closed.operation_id)
    if set(history) != expected_keys or final["pending_operation_id"] != (
        _pending_owner_identity(expected_pending)
    ):
        _invalid()


class _OwnerAdmissionA:
    """Ephemeral permission for exact pending/INTENT local writes only."""

    def __init__(self, view: Any) -> None:
        from copy import deepcopy
        from dataclasses import asdict

        self.invocation, self.source = view.invocation, view.source
        self.request = view.request
        self.request_fields = asdict(view.request)
        self.binding = deepcopy(
            {
                key: value
                for key, value in view.bindings.items()
                if key
                in {
                    "ancestor",
                    "final",
                    "ledger",
                    "ci",
                    "candidate_value",
                    "assessment_value",
                    "candidate_record",
                    "assessment_record",
                    "report_record",
                    "report_value",
                    "graph",
                }
            }
        )
        self.scope, self.reader = view.bindings["scope"], view.bindings["reader"]
        self.guard = view.bindings["guard"]
        self.supervisor, self.owner = (
            view.bindings["supervisor"],
            view.bindings["owner"],
        )
        self.phase = "CREATED"

    def check(self) -> None:
        from dataclasses import asdict

        from tools import agent_epic_final_delivery as final_delivery
        from tools import agent_final_preparation_records as records
        from tools.agent_owner_final_records import read_original_shapes

        inv, binding = self.invocation, self.binding
        source: Any = self.source
        if source is None:
            _invalid()
        if (
            getattr(inv, "_view_poisoned", False)
            or getattr(inv, "_a_witness", None) is not self
            or self.phase not in {"CREATED", "PUBLISHED"}
            or _OWNER_VIEWS.get(inv) is not None
            or _current_scope(self.scope) is not inv
            or inv.pre_merge_sources is not self.source
            or inv.scope is not self.scope
            or inv.session is not source.session
            or self.scope._active_reader() is not self.reader
            or self.reader.guard is not self.guard
            or RunnerLock.current_owned(inv.state / "session-control")
            is not self.supervisor
            or RunnerLock.current_owned(inv.state) is not self.owner
            or asdict(self.request) != self.request_fields
            or {
                "frame": self.scope.frame,
                "control": self.scope.control,
                "config": self.scope.config,
                "mandate": self.scope.mandate,
            }
            != binding["ancestor"]
        ):
            _invalid()
        if self.source is None:
            _invalid()
        self.source.check()
        _installed_sources(inv)
        code = final_delivery.authorization_code(self.request, self.source.authority)
        if code is not None:
            raise PhaseBlocked(code)
        value = _owner_view_clock(inv, self.reader)
        if (
            value["ci"] is None
            or value["ci"]["status"] != "RESERVED"
            or value["ci"]["attempts"] != binding["ci"]["attempts"]
            or value["ci"]["identity_digest"] != binding["ci"]["identity_digest"]
            or records.state2(_preparation_record(inv, "final-state"))
            != binding["final"]
            or records.candidate(_preparation_record(inv, binding["candidate_record"]))
            != binding["candidate_value"]
            or records.assessment(
                _preparation_record(inv, binding["assessment_record"])
            )
            != binding["assessment_value"]
            or records.report(_preparation_record(inv, binding["report_record"]))
            != binding["report_value"]
            or _owner_ledger(inv) != binding["ledger"]
            or inv.frames is None
        ):
            _invalid()
        namespace = _owner_namespace_snapshot(inv)
        mandate = inv.frames.sources["mandate"]
        graph = read_original_shapes(
            inv.state,
            inv.root,
            binding["candidate_value"],
            max_bytes=self.source.profile["max_artifact_bytes"],
            issued_at=mandate["issued_at"],
            expires_at=mandate["expires_at"],
        )
        if (
            graph is None
            or (namespace, graph) != binding["graph"]
            or _owner_namespace_snapshot(inv) != namespace
        ):
            _invalid()
        _demo_prose_guard(graph.demo)
        if not (
            graph.demo["started_at"]
            <= graph.demo["completed_at"]
            <= graph.approval["approved_at"]
            <= time.time()
            < inv.frames.anchor["deadline_at"]
        ):
            _invalid()
        inv.guard()


def _owner_default_clock(invocation: _OriginalInvocation) -> None:
    if getattr(invocation, "_view_clock", None) is not None:
        _owner_view_clock(invocation, invocation.scope._active_reader())


def _owner_a_publish_before(
    invocation: _OriginalInvocation,
    phase: str,
    pending: str | None,
    old: dict[str, Any],
    value: dict[str, Any],
) -> _OwnerAdmissionA | None:
    from tools import agent_final_preparation_records as records

    witness = getattr(invocation, "_a_witness", None)
    if witness is None:
        if getattr(invocation, "_view_flow", "UNSELECTED") == "FRESH_OPTIMIZED" and (
            phase == "MERGING"
            or (
                phase == "MAIN_CI_PENDING"
                and old["phase"] == phase
                and old["pending_operation_id"] != pending
            )
        ):
            _invalid()
        _owner_default_clock(invocation)
        _owner_stage_guard(invocation)
        _owner_default_clock(invocation)
        return None
    if type(witness) is not _OwnerAdmissionA or witness.phase != "CREATED":
        _invalid()
    witness.check()
    request = witness.request
    expected_phase = (
        "MERGING" if request.operation == "merge_roadmap_pr" else ("MAIN_CI_PENDING")
    )
    expected = records.state2(
        records.seal(
            {
                **{key: item for key, item in old.items() if key != "record_digest"},
                "phase": expected_phase,
                "pending_operation_id": _pending_owner_identity(request),
            }
        )
    )
    if (
        phase != expected_phase
        or pending != _pending_owner_identity(request)
        or old != witness.binding["final"]
        or value != expected
        or invocation._gate_a_request != request.operation_id
    ):
        _invalid()
    return witness


def _owner_a_publish_after(
    invocation: _OriginalInvocation,
    witness: _OwnerAdmissionA | None,
    value: dict[str, Any],
) -> None:
    from copy import deepcopy

    if witness is None:
        _owner_default_clock(invocation)
        return
    if invocation._a_witness is not witness or witness.phase != "CREATED":
        _invalid()
    witness.binding["final"] = deepcopy(value)
    witness.phase = "PUBLISHED"
    witness.check()


def _owner_a_intent_before(
    invocation: _OriginalInvocation,
    request: Any,
    previous: Any,
    value: Any,
) -> _OwnerAdmissionA | None:
    from tools import agent_epic_final_delivery as final_delivery

    witness = getattr(invocation, "_a_witness", None)
    if witness is None:
        if getattr(invocation, "_view_flow", "UNSELECTED") == "FRESH_OPTIMIZED":
            _invalid()
        _owner_default_clock(invocation)
        _current_owner_approval(invocation)
        _owner_default_clock(invocation)
        return None
    if (
        type(witness) is not _OwnerAdmissionA
        or witness.phase != "PUBLISHED"
        or witness.request != request
    ):
        _invalid()
    witness.check()
    expected = dict(previous)
    expected[request.operation_id] = final_delivery._entry(request, "INTENT", None)
    if (
        value != expected
        or request.operation_id in previous
        or previous != (witness.binding["ledger"] or {})
        or invocation._gate_a_request != request.operation_id
    ):
        _invalid()
    return witness


def _owner_a_intent_after(
    invocation: _OriginalInvocation,
    witness: _OwnerAdmissionA | None,
    value: Any,
) -> None:
    from copy import deepcopy

    from tools.agent_continuation import digest

    if witness is None:
        _owner_default_clock(invocation)
        return
    if invocation._a_witness is not witness or witness.phase != "PUBLISHED":
        _invalid()
    witness.binding["ledger"] = deepcopy(value)
    witness.check()
    invocation._intent_lineage = {
        "request": witness.request,
        "scope": witness.scope,
        "reader": witness.reader,
        "guard": witness.guard,
        "state": digest(witness.binding["final"]),
        "ledger": digest(value),
        "attempt": witness.binding["ci"]["attempts"],
        "identity": witness.binding["ci"]["identity_digest"],
    }
    witness.phase = "COMMITTED"
    invocation._a_witness = None


def _owner_consume_intent_lineage(
    invocation: _OriginalInvocation,
    request: Any,
    saved: dict[str, Any],
) -> None:
    from tools.agent_continuation import digest

    marker = getattr(invocation, "_intent_lineage", None)
    reader = invocation.scope._active_reader()
    _owner_request_binding(invocation, request)
    history = _owner_ledger(invocation)
    final = _preparation_record(invocation, "final-state")
    if (
        type(marker) is not dict
        or marker["request"] != request
        or marker["scope"] is not invocation.scope
        or marker["reader"] is not reader
        or marker["guard"] is not reader.guard
        or digest(final) != marker["state"]
        or digest(history) != marker["ledger"]
        or history is None
        or history.get(request.operation_id, {}).get("status") != "INTENT"
        or saved["ci"] is None
        or saved["ci"]["status"] != "RESERVED"
        or saved["ci"]["attempts"] != marker["attempt"] + 1
        or saved["ci"]["identity_digest"] != marker["identity"]
    ):
        _invalid()
    invocation._intent_lineage = None


def _premerge_join_full(
    invocation: _OriginalInvocation, current: Any
) -> dict[str, Any]:
    from tools import agent_final_preparation_records as records
    from tools import agent_final_preparation_runtime as original
    from tools.agent_final_preparation_report import prepare_report

    session, frame_sources = invocation.session, invocation.frames
    if frame_sources is None:
        _invalid()
    final = records.state2(_preparation_record(invocation, "final-state"))
    if final["owner_wait"] != frame_sources.anchor:
        _invalid()
    candidate = records.candidate(
        _preparation_record(
            invocation,
            "candidate-" + final["candidate_digest"],
        )
    )
    assessment = current.stable_assessment()
    request = current.request()
    for key in records.CANDIDATE - {"owner_identity", "pr", "pr_ci_digest"}:
        if candidate[key] != request[key]:
            _invalid()
    if candidate["owner_identity"] != current.profile["owner_identity"]:
        _invalid()
    if (
        wait.digest(current.ci_identity(candidate["pr"]))
        != final["owner_wait"]["wait_identity_digest"]
    ):
        _invalid()
    _original_create_receipt(
        invocation,
        _owner_underlying_receiver(invocation, current),
        candidate,
    )
    checked = current.primary_ci(candidate["pr"])
    if (
        checked.get("status") != "PASS"
        or checked.get("pr_ci_digest") != candidate["pr_ci_digest"]
    ):
        return {"status": "BLOCKED", "machine_code": "FINAL_STAGE_GATE_INVALID"}
    children = original._structural_children_current_owned(session)
    learning, comparison = current.report_statuses()
    report = prepare_report(
        current.profile,
        candidate,
        assessment,
        child_numbers=tuple(row["task"] for row in children),
        learning_status=learning,
        comparison_status=comparison,
    )
    if (
        records.assessment(
            _preparation_record(
                invocation,
                "assessment-" + assessment["record_digest"],
            )
        )
        != assessment
        or records.report(
            _preparation_record(
                invocation,
                "report-" + report["record_digest"],
            )
        )
        != report
    ):
        _invalid()
    current.stable_assessment()
    _owner_publish_join_bindings(invocation, current, candidate, report)
    from tools.agent_owner_final_records import read_original_shapes

    mandate = frame_sources.sources["mandate"]
    graph = read_original_shapes(
        invocation.state,
        invocation.root,
        candidate,
        max_bytes=current.profile["max_artifact_bytes"],
        issued_at=mandate["issued_at"],
        expires_at=mandate["expires_at"],
    )
    _owner_assign_join_approval(
        invocation,
        None if graph is None else _current_owner_approval(invocation),
    )
    invocation.guard()
    return cast(dict[str, Any], checked)


def _local_join_full(invocation: _OriginalInvocation, current: Any) -> dict[str, Any]:
    from tools import agent_final_preparation_records as records
    from tools import agent_final_preparation_runtime as original
    from tools.agent_final_preparation_report import prepare_report

    session, frame_sources = invocation.session, invocation.frames
    if frame_sources is None:
        _invalid()
    final = records.state2(_preparation_record(invocation, "final-state"))
    if (
        final["owner_wait"] != frame_sources.anchor
        or final["calls_consumed"] != 1
        or final["phase"] == "BLOCKED"
    ):
        _invalid()
    candidate = records.candidate(
        _preparation_record(
            invocation,
            "candidate-" + final["candidate_digest"],
        )
    )
    request = current.request()
    for key in records.CANDIDATE - {"owner_identity", "pr", "pr_ci_digest"}:
        if candidate[key] != request[key]:
            _invalid()
    if (
        candidate["owner_identity"] != current.profile["owner_identity"]
        or wait.digest(current.ci_identity(candidate["pr"]))
        != final["owner_wait"]["wait_identity_digest"]
    ):
        _invalid()
    _original_create_receipt(
        invocation,
        _owner_underlying_receiver(invocation, current),
        candidate,
    )
    assessment = current.stable_assessment()
    children = original._structural_children_current_owned(session)
    learning, comparison = current.report_statuses()
    report = prepare_report(
        current.profile,
        candidate,
        assessment,
        child_numbers=tuple(row["task"] for row in children),
        learning_status=learning,
        comparison_status=comparison,
    )
    if (
        records.assessment(
            _preparation_record(
                invocation,
                "assessment-" + assessment["record_digest"],
            )
        )
        != assessment
        or records.report(
            _preparation_record(
                invocation,
                "report-" + report["record_digest"],
            )
        )
        != report
    ):
        _invalid()
    _owner_publish_join_bindings(invocation, current, candidate, report)
    approval = _current_owner_approval(invocation)
    _owner_assign_join_approval(invocation, approval)
    current.stable_assessment()
    invocation.guard()
    return {"candidate": candidate, "approval": approval, "final": final}


def _owner_prepare_current(invocation: _OriginalInvocation) -> Any:
    from tools.agent_epic_session_runtime import SessionRuntime
    from tools.agent_final_preparation_sources import CurrentPreparation

    _installed_sources(invocation)
    if (
        invocation.frames is None
        or type(invocation.session) is not SessionRuntime
        or invocation.session.wait_scope is not invocation.scope
    ):
        _invalid()
    return CurrentPreparation(invocation.session)


def _owner_underlying_receiver(invocation: _OriginalInvocation, receiver: Any) -> Any:
    view = _owner_view_current(invocation)
    if view is None:
        return receiver
    if receiver is not view.derivations:
        _invalid()
    _owner_view_check(view)
    return _owner_view_source(view)


def _owner_full_premerge(invocation: _OriginalInvocation) -> dict[str, Any]:
    return _premerge_join_full(invocation, _owner_prepare_current(invocation))


def _owner_full_local(invocation: _OriginalInvocation) -> dict[str, Any]:
    return _local_join_full(invocation, _owner_prepare_current(invocation))


def _premerge_join_view(view: Any) -> dict[str, Any]:
    _owner_view_derivation(view, view.derivations)
    return _premerge_join_full(view.invocation, view.derivations)


def _local_join_view(view: Any) -> dict[str, Any]:
    _owner_view_derivation(view, view.derivations)
    return _local_join_full(view.invocation, view.derivations)


def _assess_current(scope: Any) -> dict[str, Any]:
    from tools.agent_epic_session_runtime import SessionRuntime
    from tools.agent_final_preparation_sources import _HumanGateView, _MergeAGateView

    invocation = _current_scope(scope)
    if getattr(invocation, "_view_poisoned", False):
        _invalid()
    reader = scope._active_reader()
    mode = getattr(invocation, "_view_flow", "UNSELECTED")
    if (
        type(invocation.session) is SessionRuntime
        and mode == "UNSELECTED"
        and reader._owner_original
        and reader._owner_assessing
        and reader._active_poll
    ):
        factory = (
            _HumanGateView if invocation.normal_session is None else _MergeAGateView
        )
        with factory(invocation) as view:
            result = (
                _owner_premerge_default_constructed(invocation, view.source)
                if view.phase == "DEFAULT"
                else _premerge_join_view(view)
            )
            view.result = result
        return result
    invocation._view_flow = "FULL_DEFAULT" if mode == "UNSELECTED" else mode
    return _owner_full_premerge(invocation)


def _owner_premerge_default_constructed(
    invocation: _OriginalInvocation,
    current: Any,
) -> dict[str, Any]:
    if current is None or _owner_view_current(invocation) is not None:
        _invalid()
    return _premerge_join_full(invocation, current)


def _owner_original_local(scope: Any) -> dict[str, Any]:
    invocation = _current_scope(scope)
    if _owner_view_current(invocation) is not None:
        _invalid()
    if getattr(invocation, "_view_flow", "UNSELECTED") == "UNSELECTED":
        invocation._view_flow = "FULL_DEFAULT"
    return _owner_full_local(invocation)


def _source_gate_join_full(
    invocation: _OriginalInvocation, request: Any
) -> dict[str, Any]:
    from dataclasses import asdict

    from tools import agent_epic_final_delivery as final_delivery

    candidate = invocation.candidate
    if candidate is None or type(request) is not final_delivery.Request:
        _invalid()
    expected = _typed_owner_request(candidate, request.operation, request.merge_sha)
    if asdict(request) != asdict(expected):
        _invalid()
    if request.operation == "merge_roadmap_pr":
        checked = _owner_full_premerge(invocation)
    else:
        _owner_full_local(invocation)
        checked = _owner_main_ci(invocation, request)
        if checked.get("merge_sha") != request.merge_sha:
            _invalid()
    if checked.get("status") not in {"PASS", "CI_PENDING"}:
        raise PhaseBlocked("FINAL_STAGE_GATE_INVALID")
    approval = _current_owner_approval(invocation)
    if invocation.approval != approval:
        _invalid()
    current = invocation.pre_merge_sources
    if current is None:
        _invalid()
    code = final_delivery.authorization_code(request, current.authority)
    if code is not None:
        raise PhaseBlocked(code)
    invocation.guard()
    return checked


def _source_gate_join_view(view: Any, request: Any) -> dict[str, Any]:
    _owner_view_derivation(view, view.derivations)
    if view.request != request:
        _invalid()
    invocation = view.invocation
    from dataclasses import asdict

    from tools import agent_epic_final_delivery as final_delivery

    candidate = invocation.candidate
    if candidate is None or type(request) is not final_delivery.Request:
        _invalid()
    expected = _typed_owner_request(candidate, request.operation, request.merge_sha)
    if asdict(request) != asdict(expected):
        _invalid()
    if request.operation == "merge_roadmap_pr":
        checked = _premerge_join_view(view)
    else:
        _local_join_view(view)
        checked = _owner_main_ci(invocation, request)
        if checked.get("merge_sha") != request.merge_sha:
            _invalid()
    if checked.get("status") not in {"PASS", "CI_PENDING"}:
        raise PhaseBlocked("FINAL_STAGE_GATE_INVALID")
    approval = _current_owner_approval(invocation)
    if invocation.approval != approval:
        _invalid()
    current = invocation.pre_merge_sources
    if current is None:
        _invalid()
    code = final_delivery.authorization_code(request, current.authority)
    if code is not None:
        raise PhaseBlocked(code)
    invocation.guard()
    return checked


def _owner_source_gate(invocation: _OriginalInvocation, request: Any) -> dict[str, Any]:
    from tools.agent_final_preparation_sources import _OwnerAGateView

    if getattr(invocation, "_view_poisoned", False):
        _invalid()
    mode = getattr(invocation, "_view_flow", "UNSELECTED")
    if mode == "FRESH_OPTIMIZED":
        if request.operation not in {"close_epic", "update_epic"}:
            _invalid()
        with _OwnerAGateView(invocation, request) as view:
            result = _source_gate_join_view(view, request)
            view.result = result
        return result
    if _owner_view_current(invocation) is not None:
        _invalid()
    if getattr(invocation, "_view_clock", None) is not None:
        _owner_view_clock(invocation, invocation.scope._active_reader())
    result = _source_gate_join_full(invocation, request)
    if getattr(invocation, "_view_clock", None) is not None:
        _owner_view_clock(invocation, invocation.scope._active_reader())
    return result


def _owner_b_source_gate(
    invocation: _OriginalInvocation, request: Any
) -> dict[str, Any]:
    from tools.agent_final_preparation_sources import _OwnerBGateView

    if getattr(invocation, "_view_poisoned", False):
        _invalid()
    mode = getattr(invocation, "_view_flow", "UNSELECTED")
    if mode == "FRESH_OPTIMIZED":
        if _owner_view_current(invocation) is not None:
            _invalid()
        try:
            with _OwnerBGateView(invocation, request) as view:
                result = _source_gate_join_view(view, request)
                view.result = result
            return result
        except BaseException:
            _owner_poison_view(invocation)
            raise
    if _owner_view_current(invocation) is not None or (
        getattr(invocation, "_a_witness", None) is not None
        or getattr(invocation, "_intent_lineage", None) is not None
    ):
        _invalid()
    if getattr(invocation, "_view_clock", None) is not None:
        _owner_view_clock(invocation, invocation.scope._active_reader())
    result = _source_gate_join_full(invocation, request)
    if getattr(invocation, "_view_clock", None) is not None:
        _owner_view_clock(invocation, invocation.scope._active_reader())
    return result


def _retained_sources(state: Path, root: Path) -> dict[str, Any] | None:
    """Local publication pointer only; no frame read or approval/CI authority."""
    from tools import agent_final_preparation_records as records
    from tools.agent_epic_wait_frame import _owner_close_all

    sources = guards.final_preparation_original_sources(state, root)
    seed = preparation = -1
    try:
        seed = guards._final_preseed_seed(state, root)
        try:
            preparation = guards._final_preseed_directory(seed, "final-preparation")
        except FileNotFoundError:
            return None
        try:
            raw = guards._final_preseed_read(
                preparation, "final-state.json",
                sources["profile"]["max_artifact_bytes"],
            )
        except FileNotFoundError:
            return None
        final = records.state2(raw)
        if final["owner_wait"] is None:
            return None
        if final["calls_consumed"] != 1:
            _invalid()
        for key in records.IDENTITY:
            expected = (sources["config"]["approved_launch_digest"]
                        if key == "launch_digest" else sources["profile"][key])
            if final[key] != expected:
                _invalid()
        return {"sources": sources, "final": final}
    finally:
        _owner_close_all((preparation, seed))


def _resume_retained(session: Any, supervisor: RunnerLock | None) -> dict[str, Any]:
    """Normal retained wait: no new Session cursor, frame, directory or budget."""
    from tools.agent_epic_session_runtime import SessionRuntime
    from tools.agent_epic_wait_scope import Scope

    if (
        type(session) is not SessionRuntime or type(session.wait_scope) is not Scope
        or not session.wait_scope._retained_owner
    ):
        _invalid()
    pending = session.wait_scope
    pending.guard()
    from tools.agent_epic_loop import SUPERVISOR_LOCK_FD_ENV

    if supervisor is None and SUPERVISOR_LOCK_FD_ENV in os.environ:
        with RunnerLock.normal_retained_child(session.state, session.root) as owned:
            return _resume_retained(session, owned)
    if supervisor is None:
        context = _OriginalInvocation(session.state, session.root)
        context.normal_session = session
    else:
        if (
            type(supervisor) is not RunnerLock
            or RunnerLock.current_owned(session.state / "session-control")
            is not supervisor
        ):
            _invalid()
        context = _OriginalInvocation.for_owned_session(session)
    try:
        with context as source:
            final = _preparation_record(source, "final-state")
            if final.get("phase") == "DONE":
                return _terminal_done(source)
            session._borrow_owner_scope()
            with session._stage("FINAL:FINAL_GATE"):
                # Fixed original normal continuation only. The standalone
                # human procedure still has assessment/install authority only.
                # DONE/exhausted terminal admission remains separately blocked.
                from tools.agent_final_preparation_sources import (
                    _historical_capture_lifetime,
                    _owner_capture_segment,
                )

                with _historical_capture_lifetime(session):
                    with _owner_capture_segment(session):
                        return cast(
                            dict[str, Any],
                            session.wait_scope._active_reader().owner_continue_once(),
                        )
    finally:
        session.wait_scope = pending


# Closed already-reviewed model namespace implementation. Unknown code is not
# inferred safe merely because another policy revision contains it.
# SHA-256 source integrity pins, not secret values.
_MOUNT_SOURCE_SHA256 = {
    "codex_adapter.py": (
        # pragma: allowlist nextline secret
        "2072d4af1c60b16fc4711bc62f18ae2f22930fa5b6133302171aee096884949c"
    ),
    "agent_epic_workflow.py": (
        # pragma: allowlist nextline secret
        "7ab3df9a5c2f3d8f8683763eb41e08a47828ec8ffca5a4e5ef5f9798d1e5ad34"
    ),
    "agent_orchestrate.py": (
        # pragma: allowlist nextline secret
        "7420a3978e34d879caea4b98fbcb0ba8206f89154d311ee43901031942cdaec3"
    ),
}


def _owned_code(parent: int, name: str, maximum: int) -> bytes:
    from tools.agent_epic_wait_frame import OwnerFrameSources, _owner_close_all

    descriptor = os.open(
        name, os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK"),
        dir_fd=parent,
    )
    try:
        guards._final_preseed_private(descriptor, directory=False)
        before = OwnerFrameSources._identity(os.fstat(descriptor))
        if before[5] > maximum:
            _invalid()
        result = bytearray()
        while len(result) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(result)))
            if not chunk:
                break
            result.extend(chunk)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            len(result) > maximum
            or OwnerFrameSources._identity(os.fstat(descriptor)) != before
            or OwnerFrameSources._identity(current) != before
        ):
            _invalid()
        return bytes(result)
    finally:
        _owner_close_all((descriptor,))


def _installed_sources(invocation: _OriginalInvocation) -> None:
    """Observable installation/mount consistency, not historical-human proof."""
    import hashlib

    from tools import agent_epic_loop as loop
    from tools.agent_epic_wait_frame import _owner_close_all

    invocation.guard()
    frame_sources = invocation.frames
    if frame_sources is None:
        _invalid()
    config, mandate = frame_sources.sources["config"], frame_sources.sources["mandate"]
    policy = guards.secure_directory(Path(config["policy_root"]), invocation.root)
    if Path(__file__).resolve() != policy / "tools" / "agent_owner_final_admission.py":
        _invalid()
    guards.verify_policy_config8_batch(
        policy, invocation.root, mandate["policy_sha"],
        entry=policy / "tools" / "agent_epic_runtime_cli.py",
    )
    policy_fd = tools_fd = aliases = -1
    try:
        policy_fd = os.open(
            policy, os.O_RDONLY | getattr(os, "O_DIRECTORY")
            | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK"),
        )
        guards._final_preseed_private(policy_fd, directory=True)
        tools_fd = guards._final_preseed_directory(policy_fd, "tools")
        runtime = _owned_code(tools_fd, "agent_epic_runtime_cli.py", 4 * 1024 * 1024)
        _owned_code(tools_fd, "agent_owner_final_approve.py", 4 * 1024 * 1024)
        for name, expected in _MOUNT_SOURCE_SHA256.items():
            actual = hashlib.sha256(
                _owned_code(tools_fd, name, 4 * 1024 * 1024),
            ).hexdigest()
            if actual != expected:
                _invalid()
        aliases = guards._final_preseed_directory(frame_sources._seed, "adapter-bin")
        if _owned_code(aliases, "runtime.py", len(runtime)) != runtime:
            _invalid()
        adapter = guards._final_preseed_read(
            frame_sources._seed, "runtime-adapter.json", 262144,
        )
        if (
            set(adapter) != loop.RUNTIME_CONFIG_KEYS | {"epic_command"}
            or adapter["schema_version"] != "2.0"
            or type(adapter["timeout_seconds"]) is not int
            or not 1 <= adapter["timeout_seconds"] <= 86400
            or type(adapter["output_limit"]) is not int
            or not 1024 <= adapter["output_limit"] <= 1_000_000
        ):
            _invalid()
        phase, reconcile = adapter["phase_commands"], adapter["reconcile_commands"]
        if (
            not isinstance(phase, dict) or set(phase) != set(loop.PHASES[1:])
            or not isinstance(reconcile, dict) or set(reconcile) != set(loop.PHASES[1:])
        ):
            _invalid()
        commands = [adapter["live_command"], adapter["epic_command"]]
        commands.extend(phase.values())
        commands.extend(reconcile.values())
        for raw in commands:
            command = loop.CommandRuntimeAdapter._trusted_command(
                loop.CommandRuntimeAdapter._command(raw),
                invocation.state, guards.owner_uid(),
            )
            if Path(command[1]) != invocation.state / "adapter-bin" / "runtime.py":
                _invalid()
        epic = loop.CommandRuntimeAdapter._command(adapter["epic_command"])
        if epic[2:] != (
            "epic_resume", "--state-dir", str(invocation.state),
            "--repository-root", str(invocation.root.resolve()),
        ):
            _invalid()
    finally:
        _owner_close_all((aliases, tools_fd, policy_fd))
    # The known model command writes only normalized worktree-relative paths and
    # its newly created scratch child. Existing managed seed/installation have
    # no Git ancestor and are excluded from those model write roots. We do not
    # infer physical human provenance or arbitrary host namespace guarantees.
    guards.secure_directory(invocation.state, invocation.root)
    guards.secure_directory(policy, invocation.root)
    invocation.guard()


def _demo_prose_guard(demo: dict[str, Any]) -> None:
    """Finite prohibited fixture marker only; no PII-completeness claim."""
    marker = "SYNTHETIC-OWNER-SECRET-DO-NOT-RETAIN"
    prose = []
    for scenario in demo["scenarios"]:
        prose.extend(scenario["steps"])
        prose.append(scenario["expected"])
    prose.extend(item["observation"] for item in demo["observations"])
    if any(marker in value for value in prose):
        _invalid()


def _write_owner_originals(
    invocation: _OriginalInvocation, supplied: Any,
) -> dict[str, Any]:
    """Fixed external human procedure's writer; never a runtime import route.

    The human invocation/input invariant is the external admitted owner ceremony.
    This function checks observable originals; it does not infer humanity.
    """
    import json
    import time

    from tools import agent_owner_final_records as originals
    from tools.agent_epic_wait_frame import _owner_close_all
    from tools.agent_owner_final_input import UnverifiedOwnerInput

    if type(supplied) is not UnverifiedOwnerInput:
        _invalid()
    sources = invocation.pre_merge_sources
    candidate, report, frame_sources = (
        invocation.candidate, invocation.report, invocation.frames,
    )
    if sources is None or candidate is None or report is None or frame_sources is None:
        _invalid()
    _installed_sources(invocation)
    sources.check()
    mandate = frame_sources.sources["mandate"]
    # Repeat all closed links against the actual admitted candidate and times;
    # the neutral input container itself is never approval/source authority.
    expected = originals._links(
        [
            supplied.approval, supplied.pin, supplied.receipt,
            supplied.command, supplied.demo,
        ],
        candidate, mandate["issued_at"], mandate["expires_at"],
    )
    _demo_prose_guard(expected.demo)
    if not (
        expected.approval["approved_at"]
        <= time.time() < frame_sources.anchor["deadline_at"]
    ):
        _invalid()
    names = (
        "approval.json", "approval-pin.json", "install-receipt.json",
        "command-source.json", "final-demo-source.json",
    )
    values = (
        expected.approval, expected.pin, expected.receipt,
        expected.command, expected.demo,
    )
    maximum = frame_sources.sources["profile"]["max_artifact_bytes"]
    encoded = [
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        for value in values
    ]
    if any(len(raw) > maximum for raw in encoded):
        _invalid()
    parent = -1
    try:
        try:
            parent = guards._final_preseed_directory(frame_sources._seed, "final-owner")
        except FileNotFoundError:
            # This is the explicitly authorized human original installation,
            # never ordinary runtime/no-create wait admission initialization.
            os.mkdir("final-owner", 0o700, dir_fd=frame_sources._seed)
            parent = guards._final_preseed_directory(frame_sources._seed, "final-owner")
        present: set[str] = set()
        with os.scandir(parent) as entries:
            for ordinal, entry in enumerate(entries, 1):
                if ordinal > 5:
                    _invalid()
                present.add(entry.name)
        if present:
            if present != set(names):
                _invalid()
            retained = originals.read_original_shapes(
                invocation.state, invocation.root, candidate, max_bytes=maximum,
                issued_at=mandate["issued_at"], expires_at=mandate["expires_at"],
            )
            if retained != expected:
                _invalid()
        else:
            for name, raw in zip(names, encoded, strict=True):
                invocation.guard()
                sources.check()
                descriptor = os.open(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK"),
                    0o600, dir_fd=parent,
                )
                try:
                    guards._final_preseed_private(descriptor, directory=False)
                    view = memoryview(raw)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            _invalid()
                        view = view[written:]
                    os.fsync(descriptor)
                    if _owned_code(parent, name, len(raw)) != raw:
                        _invalid()
                finally:
                    _owner_close_all((descriptor,))
            os.fsync(parent)
        retained = originals.read_original_shapes(
            invocation.state, invocation.root, candidate, max_bytes=maximum,
            issued_at=mandate["issued_at"], expires_at=mandate["expires_at"],
        )
        if retained != expected:
            _invalid()
        sources.check()
        _installed_sources(invocation)
        return {
            "status": "OWNER_APPROVAL_RECORDED", "machine_code": "OK",
            "candidate_digest": candidate["candidate_digest"],
            "approval_digest": expected.approval["approved_digest"],
            "command_digest": expected.command["record_digest"],
            "demo_digest": expected.demo["record_digest"],
            "pin_digest": expected.pin["record_digest"],
            "receipt_digest": expected.receipt["record_digest"],
        }
    finally:
        # No rollback/deletion of partially issued originals. Partial/conflict
        # remains blocked for a separately reviewed explicit owner recovery.
        _owner_close_all((parent,))


def _normal_retained_command(
    state: Path, root: Path, command: tuple[str, ...], *, child: bool,
) -> bool:
    """Fixed configured epic child association, never standalone FD authority."""
    import sys

    from tools import agent_epic_loop as loop
    from tools.agent_epic_wait_frame import FD_ENV, NONCE_ENV, _owner_close_all

    config = guards.private_json(state / "epic-runtime.json")
    if config.get("schema_version") != "8.0":
        return False
    retained = _retained_sources(state, root)
    if retained is None:
        return False
    sources = retained["sources"]
    if config != sources["config"]:
        _invalid()
    guards.managed_launch_guard(state, root)
    guards.bootstrap_authority(config, sources["mandate"], epic=True)
    policy = guards.secure_directory(Path(config["policy_root"]), root)
    guards.verify_policy_config8_batch(
        policy, root, sources["mandate"]["policy_sha"],
        entry=policy / "tools" / "agent_epic_runtime_cli.py",
    )
    seed = aliases = tools = -1
    try:
        seed = guards._final_preseed_seed(state, root)
        adapter = guards._final_preseed_read(seed, "runtime-adapter.json", 262144)
        expected = loop.CommandRuntimeAdapter._trusted_command(
            loop.CommandRuntimeAdapter._command(adapter["epic_command"]),
            state, guards.owner_uid(),
        )
        same_command = (
            command[1:] == expected[1:]
            and Path(command[0]).resolve() == Path(expected[0]).resolve()
        ) if child else command == expected
        if not same_command or expected[2:] != (
            "epic_resume", "--state-dir", str(state),
            "--repository-root", str(root.resolve()),
        ):
            _invalid()
        aliases = guards._final_preseed_directory(seed, "adapter-bin")
        policy_fd = os.open(
            policy, os.O_RDONLY | getattr(os, "O_DIRECTORY")
            | getattr(os, "O_NOFOLLOW") | getattr(os, "O_NONBLOCK"),
        )
        try:
            guards._final_preseed_private(policy_fd, directory=True)
            tools = guards._final_preseed_directory(policy_fd, "tools")
        finally:
            _owner_close_all((policy_fd,))
        runtime = _owned_code(tools, "agent_epic_runtime_cli.py", 4 * 1024 * 1024)
        if _owned_code(aliases, "runtime.py", len(runtime)) != runtime:
            _invalid()
        if child and (
            FD_ENV in os.environ or NONCE_ENV in os.environ
            or Path(sys.executable).resolve() != Path(expected[0]).resolve()
            or tuple(sys.argv) != expected[1:]
        ):
            _invalid()
        control = guards.private_json(state / "launch-control.json")
        if control["state"] != "RUNNING":
            _invalid()
        # Recheck the retained local pointer after source/alias admission.
        if _retained_sources(state, root) != retained:
            _invalid()
        return True
    finally:
        _owner_close_all((tools, aliases, seed))


def _current_owner_approval(invocation: _OriginalInvocation) -> dict[str, Any]:
    """Fixed five-original composition; no supplied verifier or approval flag."""
    import time

    from tools.agent_final_preparation_owner import read_current_owner_approval
    from tools.agent_owner_final_records import read_original_shapes

    frames_source = invocation.frames
    current = invocation.pre_merge_sources
    candidate = invocation.candidate
    if frames_source is None or current is None or candidate is None:
        _invalid()
    maximum = frames_source.sources["profile"]["max_artifact_bytes"]
    mandate = frames_source.sources["mandate"]
    namespace = _owner_namespace_identity(invocation)
    before_graph = read_original_shapes(
        invocation.state, invocation.root, candidate, max_bytes=maximum,
        issued_at=mandate["issued_at"], expires_at=mandate["expires_at"],
    )
    if before_graph is None or _owner_namespace_identity(invocation) != namespace:
        _invalid()
    _owner_approval_observe(invocation, before_graph)

    def installed_guard() -> None:
        _installed_sources(invocation)
        _owner_semantic_boundary(invocation, current)
        invocation.guard()

    def source_verifier(
        checked_candidate: dict[str, Any], approval: dict[str, Any],
        pin: dict[str, Any], receipt: dict[str, Any],
    ) -> bool:
        # These callbacks are fixed installed code, not public injection points.
        installed_guard()
        if checked_candidate != candidate:
            _invalid()
        graph = read_original_shapes(
            invocation.state, invocation.root, candidate, max_bytes=maximum,
            issued_at=mandate["issued_at"], expires_at=mandate["expires_at"],
        )
        if graph != before_graph or graph is None or (
            graph.approval != approval or graph.pin != pin
            or graph.receipt != receipt
        ):
            _invalid()
        _demo_prose_guard(graph.demo)
        now = time.time()
        if not (
            graph.demo["started_at"] <= graph.demo["completed_at"]
            <= approval["approved_at"] <= now < frames_source.anchor["deadline_at"]
        ):
            _invalid()
        # The real installed human invocation/input boundary remains an external
        # prerequisite, not a claim inferred from record labels or private mode.
        installed_guard()
        _owner_approval_verified(invocation, checked_candidate, approval, pin, receipt)
        return True

    result = read_current_owner_approval(
        invocation.state, candidate, max_bytes=maximum,
        installed_guard=installed_guard, source_verifier=source_verifier,
    )
    if result is None or _owner_namespace_identity(invocation) != namespace:
        _invalid()
    _owner_approval_reader_return(invocation, result)
    installed_guard()
    if invocation._owner_graph is not None and invocation._owner_graph != before_graph:
        _invalid()
    invocation._owner_graph = before_graph
    _owner_approval_complete(invocation, result, before_graph)
    return result


def _owner_namespace_identity(invocation: _OriginalInvocation) -> tuple[int, ...]:
    from tools.agent_epic_wait_frame import OwnerFrameSources, _owner_close_all

    invocation.guard()
    seed = owner = -1
    try:
        seed = guards._final_preseed_seed(invocation.state, invocation.root)
        owner = guards._final_preseed_directory(seed, "final-owner")
        return OwnerFrameSources._identity(os.fstat(owner))
    finally:
        _owner_close_all((owner, seed))


def _main_ci_normalize(
    comparison: Comparison,
    check_runs: Any,
    suites: dict[int, Any],
    runs: dict[int, Any],
    jobs: dict[int, Any],
    definitions: dict[str, bytes],
    workflows: dict[str, Any],
) -> PrimaryCI:
    import re

    from tools import agent_learning as learning
    from tools.agent_continuation import digest
    from tools.agent_coordinator_delivery import REQUIRED_CHECKS
    from tools.agent_final_preparation_ci import (
        Comparison,
        PrimaryCheck,
        PrimaryCI,
        _bounded,
        _require,
        _workflow,
    )
    from tools.agent_learning_delivery import normalize_checks

    _require(type(comparison) is Comparison)
    repo, sha = comparison.repository, comparison.sha
    _require(isinstance(repo, str) and len(repo) <= 201)
    _require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is not None)
    learning.hash_value(sha, size=40)
    learning.hash_value(comparison.policy_sha, size=40)
    for value in (suites, runs, jobs):
        _require(isinstance(value, dict) and len(value) <= 5)
        _require(all(type(key) is int and key > 0 for key in value))
        for original in value.values():
            _bounded(original)
    expected_files = {"tests.yml", "pre-commit.yml"}
    _require(set(definitions) == set(workflows) == expected_files)
    _bounded(check_runs)
    _require(
        isinstance(check_runs, dict) and isinstance(check_runs.get("check_runs"), list)
    )
    learning.integer(check_runs.get("total_count"), 100)
    for original_check in check_runs["check_runs"]:
        if (
            isinstance(original_check, dict)
            and original_check.get("name") in REQUIRED_CHECKS
        ):
            _require(isinstance(original_check.get("app"), dict))
            _require(isinstance(original_check.get("check_suite"), dict))
            learning.integer(original_check.get("id"), 2**63 - 1, 1)
            learning.integer(original_check["app"].get("id"), 2**63 - 1, 1)
            learning.integer(original_check["check_suite"].get("id"), 2**63 - 1, 1)
    checks = normalize_checks(check_runs, repo, sha)
    normalized_workflows = {
        "pre-commit" if filename == "pre-commit.yml" else "tests": _workflow(
            definitions[filename], workflows[filename], filename
        )
        for filename in sorted(expected_files)
    }
    bound_runs: dict[int, dict[str, Any]] = {}
    bound_suites: dict[int, dict[str, Any]] = {}
    for check in checks:
        suite_id, run_id = check["suite_id"], check["run_id"]
        suite, run, original_jobs = suites[suite_id], runs[run_id], jobs[run_id]
        expected = normalized_workflows[
            "pre-commit" if check["name"] == "pre-commit" else "tests"
        ]
        _require(
            isinstance(suite, dict)
            and learning.integer(suite.get("id"), 2**63 - 1, 1) == suite_id
            and suite.get("head_sha") == sha
            and isinstance(suite.get("app"), dict)
            and learning.integer(suite["app"].get("id"), 2**63 - 1, 1)
            == check["app_id"]
            and isinstance(suite.get("repository"), dict)
            and suite["repository"].get("full_name") == repo
        )
        _require(
            isinstance(run, dict)
            and learning.integer(run.get("id"), 2**63 - 1, 1) == run_id
            and run.get("head_sha") == sha
            and run.get("event") == "push"
            and run.get("head_branch") == "main"
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and isinstance(run.get("repository"), dict)
            and run["repository"].get("full_name") == repo
            and learning.integer(run.get("workflow_id"), 2**63 - 1, 1) == expected["id"]
            and run.get("path") == expected["path"]
            and run.get("name") == expected["name"]
            and learning.integer(run.get("check_suite_id"), 2**63 - 1, 1) == suite_id
        )
        attempt = learning.integer(run.get("run_attempt"), 1000, 1)
        _require(
            isinstance(original_jobs, dict)
            and isinstance(original_jobs.get("jobs"), list)
            and len(original_jobs["jobs"]) <= 100
            and learning.integer(original_jobs.get("total_count"), 100)
            == len(original_jobs["jobs"])
        )
        matched = [
            job
            for job in original_jobs["jobs"]
            if isinstance(job, dict)
            and job.get("name") == check["name"]
            and learning.integer(job.get("id"), 2**63 - 1, 1) == check["job_id"]
            and learning.integer(job.get("run_id"), 2**63 - 1, 1) == run_id
            and job.get("head_sha") == sha
            and learning.integer(job.get("run_attempt"), 1000, 1) == attempt
            and job.get("status") == "completed"
            and job.get("conclusion") == "success"
        ]
        _require(len(matched) == 1)
        check["attempt"] = attempt
        bound_suites[suite_id] = suite
        bound_runs[run_id] = {"run": run, "jobs": original_jobs, "attempt": attempt}
    _require(set(suites) == set(bound_suites))
    _require(set(runs) == set(jobs) == set(bound_runs))
    source_digest = digest(
        {"runs": bound_runs, "suites": bound_suites, "workflows": normalized_workflows}
    )
    facts = tuple(PrimaryCheck(**check) for check in checks)
    return PrimaryCI(
        repo,
        sha,
        comparison.policy_sha,
        "push",
        facts,
        source_digest,
        digest(
            {
                "repository": repo,
                "sha": sha,
                "policy_sha": comparison.policy_sha,
                "source_digest": source_digest,
                "checks": checks,
            }
        ),
    )




def _original_create_receipt(
    invocation: _OriginalInvocation, current: CurrentPreparation,
    candidate: dict[str, Any],
) -> None:
    """Original preparation APPLIED create is required in every owner phase."""
    from tools import agent_final_preparation_delivery as preparation

    entries = preparation.entries(_preparation_record(invocation, "ledger"))
    request = _owner_request_for_receipt(invocation, current)
    if set(entries) != {request["operation_id"]}:
        _invalid()
    entry = entries[request["operation_id"]]
    expected = {
        "pr": candidate["pr"], "repository": request["repository"],
        "base_ref": "main", "head_ref": request["roadmap_ref"],
        "main_sha": request["main_sha"], "roadmap_sha": request["roadmap_sha"],
        "operation_id": request["operation_id"],
    }
    if (
        entry["status"] != "APPLIED" or entry["request"] != request
        or preparation.receipt(entry["receipt"], request) != expected
    ):
        _invalid()


def _typed_owner_request(
    candidate: dict[str, Any], operation: str, merge_sha: str | None = None,
) -> Any:
    """Original typed final operation identity; data construction grants nothing."""
    from tools import agent_epic_final_delivery as final_delivery
    from tools import agent_final_preparation_records as records
    from tools.agent_epic_final_runtime import digest

    candidate = records.candidate(candidate)
    if type(operation) is not str or operation not in {
        "merge_roadmap_pr", "close_epic", "update_epic",
    }:
        _invalid()
    if merge_sha is not None:
        if type(merge_sha) is not str:
            _invalid()
        records.hash_value(merge_sha, sha=True)
    identity = {key: candidate[key] for key in (
        "repository", "epic", "roadmap_ref", "roadmap_sha", "main_sha", "policy_sha",
    )}
    value = final_delivery.Request(
        operation_id=digest({
            **identity, "operation": operation,
            "pr": candidate["pr"], "merge_sha": merge_sha,
        }),
        operation=operation, **identity, pr=candidate["pr"], merge_sha=merge_sha,
    )
    if not final_delivery.valid_request(value):
        _invalid()
    return value


def _pending_owner_identity(request: Any) -> str:
    """Fixed inverse projection; original tagged request/ledger key is retained."""
    from tools import agent_epic_final_delivery as final_delivery
    from tools import agent_final_preparation_records as records

    if (
        type(request) is not final_delivery.Request
        or type(request.operation_id) is not str
    ):
        _invalid()
    records.hash_value(request.operation_id, tagged=True)
    pending = request.operation_id.removeprefix("sha256:")
    records.hash_value(pending)
    if request.operation_id != "sha256:" + pending:
        _invalid()
    return pending


def _owner_ledger(invocation: _OriginalInvocation) -> dict[str, Any] | None:
    """Read only existing typed history; absence is not an empty-ledger proof."""
    import json
    from dataclasses import asdict

    from tools import agent_final_preparation_records as records
    from tools.agent_epic_wait_frame import OwnerFrameSources, _owner_close_all

    invocation.guard()
    if invocation.frames is None:
        _invalid()
    candidate = invocation.candidate
    if candidate is None:
        view = _owner_view_current(invocation)
        if (
            view is None or view.source is None
            or view.phase not in {"BUILDING", "DERIVING", "EXITING"}
            or view.kind not in {"HUMAN", "MERGE_A"}
            or getattr(invocation, "_view_poisoned", False)
            or view.invocation is not invocation
            or view.source.session is not invocation.session
            or view.bindings["session"] is not invocation.session
            or view.bindings["scope"] is not invocation.scope
            or _current_scope(invocation.scope) is not invocation
        ):
            _invalid()
        candidate = records.candidate(view.bindings.get("candidate_value"))
        if candidate["preparation_digest"] != view.source.baseline["record_digest"]:
            _invalid()
    parent = -1
    try:
        try:
            parent = guards._final_preseed_directory(
                invocation.frames._seed, "final-ledger",
            )
        except FileNotFoundError:
            invocation.guard()
            return None
        before = OwnerFrameSources._identity(os.fstat(parent))
        iterator = os.scandir(parent)
        try:
            names = []
            for entry in iterator:
                names.append(entry.name)
                if len(names) > 1:
                    _invalid()
        except BaseException:
            try:
                iterator.close()
            except BaseException:
                pass
            raise
        else:
            iterator.close()
        if names != ["delivery-ledger.json"]:
            _invalid()
        raw = _owned_code(
            parent, "delivery-ledger.json",
            invocation.frames.sources["profile"]["max_artifact_bytes"],
        )
        value = json.loads(raw, object_pairs_hook=guards._final_preseed_pairs)
        if type(value) is not dict or not 1 <= len(value) <= 3:
            _invalid()
        merge_request = _typed_owner_request(candidate, "merge_roadmap_pr")
        merge_entry = value.get(merge_request.operation_id)
        if type(merge_entry) is not dict:
            _invalid()
        merge_sha = None
        if merge_entry.get("status") == "APPLIED":
            receipt = merge_entry.get("receipt")
            if type(receipt) is not dict or set(receipt) != {"merge_sha"}:
                _invalid()
            records.hash_value(receipt["merge_sha"], sha=True)
            merge_sha = receipt["merge_sha"]
        expected = {merge_request.operation_id: merge_request}
        if merge_sha is not None:
            for operation in ("close_epic", "update_epic"):
                request = _typed_owner_request(candidate, operation, merge_sha)
                expected[request.operation_id] = request
        if not set(value) <= set(expected):
            _invalid()
        for key, entry in value.items():
            request = expected[key]
            if (
                type(entry) is not dict
                or set(entry) != {"operation", "request", "status", "receipt"}
                or entry["operation"] != request.operation
                or type(entry["request"]) is not dict
                or entry["request"] != asdict(request)
                or any(
                    type(entry["request"].get(field)) is not type(expected)
                    for field, expected in asdict(request).items()
                )
                or type(entry["status"]) is not str
                or entry["status"] not in {"INTENT", "UNKNOWN", "APPLIED"}
            ):
                _invalid()
            if entry["status"] != "APPLIED":
                if entry["receipt"] is not None:
                    _invalid()
            elif request.operation != "merge_roadmap_pr":
                receipt = entry["receipt"]
                if (
                    type(receipt) is not dict
                    or type(receipt.get("epic")) is not int
                    or receipt != {"epic": request.epic, "merge_sha": merge_sha}
                ):
                    _invalid()
        update = _typed_owner_request(candidate, "update_epic", merge_sha) if (
            merge_sha is not None
        ) else None
        if update is not None and update.operation_id in value:
            closed = _typed_owner_request(candidate, "close_epic", merge_sha)
            if value.get(closed.operation_id, {}).get("status") != "APPLIED":
                _invalid()
        named = os.stat(
            "final-ledger", dir_fd=invocation.frames._seed,
            follow_symlinks=False,
        )
        if (
            OwnerFrameSources._identity(os.fstat(parent)) != before
            or OwnerFrameSources._identity(named) != before
        ):
            _invalid()
        invocation.guard()
        return value
    except (OSError, ValueError, TypeError, RecursionError):
        _invalid()
    finally:
        _owner_close_all((parent,))


def _owner_merged_remote(invocation: _OriginalInvocation) -> dict[str, Any]:
    """Fresh remote facts for the one original typed APPLIED merge, not a gate."""
    from tools import agent_final_preparation_records as records

    invocation.guard()
    current, candidate = invocation.pre_merge_sources, invocation.candidate
    if current is None or candidate is None:
        _invalid()
    entries = _owner_ledger(invocation)
    request = _typed_owner_request(candidate, "merge_roadmap_pr")
    if entries is None:
        _invalid()
    entry = entries.get(request.operation_id)
    if type(entry) is not dict or entry.get("status") != "APPLIED":
        _invalid()
    merge_sha = records.hash_value(entry["receipt"]["merge_sha"], sha=True)
    if merge_sha in {candidate["main_sha"], candidate["roadmap_sha"]}:
        _invalid()
    repo = candidate["repository"]

    def snapshot() -> dict[str, Any]:
        _owner_mainci_snapshot_guard(invocation)
        refs = current.session.github.branches(repo)
        if (
            refs.get("main") != merge_sha
            or refs.get(candidate["roadmap_ref"]) != candidate["roadmap_sha"]
        ):
            _invalid()
        pr = current._json(f"repos/{repo}/pulls/{candidate['pr']}")
        if (
            type(pr) is not dict or type(pr.get("number")) is not int
            or pr["number"] != candidate["pr"] or pr.get("state") != "closed"
            or pr.get("merged") is not True
            or pr.get("merge_commit_sha") != merge_sha
        ):
            _invalid()
        for key, ref in (("base", "main"), ("head", candidate["roadmap_ref"])):
            item = pr.get(key)
            if (
                type(item) is not dict or item.get("ref") != ref
                or type(item.get("repo")) is not dict
                or item["repo"].get("full_name") != repo
            ):
                _invalid()
        if (
            pr["head"].get("sha") != candidate["roadmap_sha"]
            or pr["base"].get("sha") not in {candidate["main_sha"], merge_sha}
        ):
            _invalid()
        commit = current._json(f"repos/{repo}/git/commits/{merge_sha}")
        if type(commit) is not dict or commit.get("sha") != merge_sha:
            _invalid()
        parents = commit.get("parents")
        if (
            type(parents) is not list or len(parents) != 2
            or any(type(parent) is not dict for parent in parents)
            or [parent.get("sha") for parent in parents]
            != [candidate["main_sha"], candidate["roadmap_sha"]]
        ):
            _invalid()
        _owner_mainci_snapshot_guard(invocation)
        invocation.guard()
        return {"refs": refs, "pr": pr, "commit": commit}

    before = snapshot()
    if snapshot() != before or _owner_ledger(invocation) != entries:
        _invalid()
    return {"merge_sha": merge_sha, "originals": before}


def _main_ci_pending(
    comparison: Comparison, checks: list[Any],
    suites: dict[int, Any], runs: dict[int, Any],
    jobs: dict[int, Any], definitions: dict[str, bytes],
    workflows: dict[str, Any],
) -> None:
    """Pending has its own complete identity proof, never a normalize fallback."""
    from tools import agent_final_preparation_ci as ci
    from tools import agent_final_preparation_records as records
    from tools.agent_final_preparation_sources import _require

    _require(type(comparison) is ci.Comparison)
    repo, sha = comparison.repository, comparison.sha
    known = {"queued", "in_progress", "completed"}
    try:
        normalized = {
            filename: ci._workflow(
                definitions[filename], workflows[filename], filename,
            ) for filename in definitions
        }
    except ci.PrimaryCIError:
        raise PhaseBlocked("FINAL_STAGE_GATE_INVALID") from None
    for check in checks:
        _require(type(check.get("status")) is str)
        _require(check.get("status") in known)
        if check["status"] == "completed":
            _require(check.get("conclusion") == "success")
        else:
            _require(check.get("conclusion") is None)
        sid = check["check_suite"]["id"]
        suite = suites[sid]
        _require(isinstance(suite, dict))
        _require(type(suite.get("id")) is int)
        _require(suite.get("id") == sid and suite.get("head_sha") == sha)
        _require(isinstance(suite.get("repository"), dict))
        _require(suite["repository"].get("full_name") == repo)
        _require(isinstance(suite.get("app"), dict))
        records.integer(suite["app"].get("id"), 2**63 - 1, 1)
        _require(suite["app"].get("id") == 15368)
        run_id = int(check["details_url"].split("/runs/")[1].split("/")[0])
        run = runs[run_id]
        _require(isinstance(run, dict))
        _require(type(run.get("id")) is int)
        _require(run.get("id") == run_id and run.get("head_sha") == sha)
        _require(run.get("event") == "push")
        _require(run.get("head_branch") == "main")
        _require(isinstance(run.get("repository"), dict))
        _require(run["repository"].get("full_name") == repo)
        _require(run.get("check_suite_id") == sid)
        _require(type(run.get("check_suite_id")) is int)
        _require(type(run.get("status")) is str)
        _require(run.get("status") in known)
        if run["status"] == "completed":
            _require(run.get("conclusion") == "success")
        else:
            _require(run.get("conclusion") is None)
        workflow = normalized[
            "pre-commit.yml" if check["name"] == "pre-commit" else "tests.yml"
        ]
        for key in ("path", "name"):
            _require(run.get(key) == workflow[key])
        records.integer(run.get("workflow_id"), 2**63 - 1, 1)
        records.integer(run.get("run_attempt"), 1000, 1)
        _require(run.get("workflow_id") == workflow["id"])
        listing = jobs[run_id]
        _require(isinstance(listing, dict))
        values = listing.get("jobs")
        _require(isinstance(values, list) and len(values) <= 100)
        _require(type(listing.get("total_count")) is int)
        _require(listing["total_count"] == len(values))
        _require(all(isinstance(r, dict) for r in values))
        found = [r for r in values if r.get("id") == check["id"]]
        _require(len(found) == 1)
        job = found[0]
        for key in ("id", "run_id"):
            records.integer(job.get(key), 2**63 - 1, 1)
        records.integer(job.get("run_attempt"), 1000, 1)
        _require(type(job.get("status")) is str)
        _require(job.get("status") == check["status"])
        _require(job.get("conclusion") == check.get("conclusion"))
        if run["status"] == "completed":
            _require(job["status"] == "completed")
        elif run["status"] == "queued":
            _require(job["status"] == "queued")
        for key, value in {
            "name": check["name"], "head_sha": sha, "run_id": run_id,
            "run_attempt": run["run_attempt"],
        }.items():
            _require(job.get(key) == value)
        _require(job.get("status") in known)
        if job["status"] == "completed":
            _require(job.get("conclusion") == "success")
        else:
            _require(job.get("conclusion") is None)



def _owner_current_ci_reader(session: Any) -> Any:
    """Fixed current family; callers cannot request historical acquisition."""
    from tools.agent_final_learning_domain import _CurrentFinalSourceReader

    return _CurrentFinalSourceReader(session)


_MAIN_CI_GROUPS: dict[_OriginalInvocation, _MainCiProofGroup] = {}


class _MainCiProofGroup:
    """One fixed post-merge current-CI read, never effect permission."""

    def __init__(self, invocation: _OriginalInvocation, request: Any) -> None:
        from copy import deepcopy
        from dataclasses import asdict

        from tools import agent_epic_final_delivery as final_delivery

        self.invocation, self.request = invocation, request
        self.active = False
        self.current = invocation.pre_merge_sources
        self.session, self.scope = invocation.session, invocation.scope
        if self.current is None or self.session is None or self.scope is None:
            _invalid()
        self.reader = self.scope._active_reader()
        self.guard = self.reader.guard
        self.stage = self.scope.active_stage
        if (
            type(request) is not final_delivery.Request
            or request.operation not in {"close_epic", "update_epic"}
            or invocation.candidate is None or invocation.report is None
            or asdict(request) != asdict(_typed_owner_request(
                invocation.candidate, request.operation, request.merge_sha,
            ))
        ):
            _invalid()
        self.request_fields = asdict(request)
        self.candidate, self.report = invocation.candidate, invocation.report
        self.identity = deepcopy({
            "frame": self.scope.frame, "config": self.scope.config,
            "mandate": self.scope.mandate, "candidate": self.candidate,
            "report": self.report,
        })
        self.records: dict[str, Any] = {}
        self.ledger: Any = None
        self.graph: Any = None
        self.namespace: tuple[int, ...] = ()
        self.seen_wait: dict[str, Any] = {}
        self.observed_clock_floor: float | None = None

    def _full(self) -> None:
        current = self.current
        if current is None:
            _invalid()
        if _owner_mainci_outer_hook(self):
            return
        current.stable_assessment()
        if _current_owner_approval(self.invocation) != self.invocation.approval:
            _invalid()

    def _records(self) -> dict[str, Any]:
        from tools import agent_final_preparation_records as records

        current = self.current
        if current is None:
            _invalid()
        return {
            "state": records.state2(_preparation_record(
                self.invocation, "final-state",
            )),
            "candidate": records.candidate(_preparation_record(
                self.invocation, "candidate-" + self.candidate["candidate_digest"],
            )),
            "report": records.report(_preparation_record(
                self.invocation, "report-" + self.report["record_digest"],
            )),
            "assessment": records.assessment(_preparation_record(
                self.invocation,
                "assessment-" + current.baseline["record_digest"],
            )),
        }

    def _graph(self) -> Any:
        from tools.agent_owner_final_records import read_original_shapes

        frames = self.invocation.frames
        if frames is None:
            _invalid()
        mandate = frames.sources["mandate"]
        namespace = _owner_namespace_identity(self.invocation)
        graph = read_original_shapes(
            self.invocation.state, self.invocation.root, self.candidate,
            max_bytes=frames.sources["profile"]["max_artifact_bytes"],
            issued_at=mandate["issued_at"], expires_at=mandate["expires_at"],
        )
        if (
            graph is None or namespace != self.namespace
            or _owner_namespace_identity(self.invocation) != self.namespace
        ):
            _invalid()
        _demo_prose_guard(graph.demo)
        if not (
            graph.demo["started_at"] <= graph.demo["completed_at"]
            <= graph.approval["approved_at"] <= time.time()
            < frames.anchor["deadline_at"]
        ):
            _invalid()
        return graph

    def check(self) -> None:
        from copy import deepcopy
        from dataclasses import asdict

        from tools import agent_epic_final_delivery as final_delivery

        invocation, current, scope = self.invocation, self.current, self.scope
        if (
            not self.active or _MAIN_CI_GROUPS.get(invocation) is not self
            or _current_scope(scope) is not invocation
            or invocation.session is not self.session
            or invocation.normal_session is not self.session
            or invocation.pre_merge_sources is not current
            or invocation.scope is not scope or scope is None
            or scope.active_stage != self.stage
            or scope._active_reader() is not self.reader
            or self.reader.guard is not self.guard
            or invocation.candidate is not self.candidate
            or invocation.report is not self.report
            or asdict(self.request) != self.request_fields
            or {
                "frame": scope.frame, "config": scope.config,
                "mandate": scope.mandate, "candidate": invocation.candidate,
                "report": invocation.report,
            } != self.identity
        ):
            _invalid()
        if current is None:
            _invalid()
        current.check()
        _installed_sources(invocation)
        code = final_delivery.authorization_code(self.request, current.authority)
        if code is not None:
            raise PhaseBlocked(code)
        value = self.reader._load()
        # Keep the persisted snapshot separate from _check's local clock update.
        persisted = deepcopy(value)
        observed = self.reader._check(value)
        if (
            self.observed_clock_floor is not None
            and observed < self.observed_clock_floor
        ):
            _invalid()
        self.observed_clock_floor = observed
        previous = self.seen_wait
        if (
            persisted["ci"] is None or persisted["ci"]["status"] != "RESERVED"
            or previous["ci"] is None
            or persisted["ci"]["attempts"] != previous["ci"]["attempts"]
            or persisted["ci"]["identity_digest"]
            != previous["ci"]["identity_digest"]
            or persisted["started_at"] != previous["started_at"]
            or persisted["deadline_at"] > previous["deadline_at"]
            or persisted["last_seen_at"] < previous["last_seen_at"]
            or any(v["status"] != "READY" for v in persisted["requests"].values())
            or any(persisted["requests"].get(k) != v
                   for k, v in previous["requests"].items())
        ):
            _invalid()
        self.seen_wait = persisted
        if self._records() != self.records or _owner_ledger(invocation) != self.ledger:
            _invalid()
        if self._graph() != self.graph:
            _invalid()
        invocation.guard()

    def __enter__(self) -> _MainCiProofGroup:
        from copy import deepcopy
        from dataclasses import asdict

        invocation = self.invocation
        if invocation in _MAIN_CI_GROUPS:
            _invalid()
        _MAIN_CI_GROUPS[invocation] = self
        self.active = True
        try:
            if _current_scope(self.scope) is not invocation:
                _invalid()
            value = self.reader._load()
            if value["ci"] is None or value["ci"]["status"] != "RESERVED":
                _invalid()
            self.seen_wait = deepcopy(value)
            self._full()
            self.records = deepcopy(self._records())
            state = self.records["state"]
            if invocation.frames is None:
                _invalid()
            assert invocation.frames is not None
            if (
                state["candidate_digest"] != self.candidate["candidate_digest"]
                or self.records["candidate"] != self.candidate
                or self.records["report"] != self.report
                or self.current is None
                or self.records["assessment"] != self.current.baseline
                or self.candidate["preparation_digest"]
                != self.records["assessment"]["record_digest"]
                or self.report["candidate_digest"] != self.candidate["candidate_digest"]
                or state["owner_wait"] != invocation.frames.anchor
            ):
                _invalid()
            self.ledger = deepcopy(_owner_ledger(invocation))
            merge = _typed_owner_request(self.candidate, "merge_roadmap_pr")
            if self.ledger is None:
                _invalid()
            entry = self.ledger.get(merge.operation_id)
            if (
                type(entry) is not dict or entry["status"] != "APPLIED"
                or entry["request"] != asdict(merge)
                or entry["receipt"]["merge_sha"] != self.request.merge_sha
            ):
                _invalid()
            self.namespace = _owner_namespace_identity(invocation)
            self.graph = self._graph()
            self.check()
            return self
        except BaseException:
            self.active = False
            if _MAIN_CI_GROUPS.get(invocation) is self:
                del _MAIN_CI_GROUPS[invocation]
            raise

    def __exit__(self, _kind: Any, error: Any, _traceback: Any) -> None:
        primary = error
        try:
            try:
                self.check()
            except BaseException as failure:
                if primary is None:
                    primary = failure
            try:
                self._full()
            except BaseException as failure:
                if primary is None:
                    primary = failure
        finally:
            self.active = False
            if _MAIN_CI_GROUPS.get(self.invocation) is self:
                del _MAIN_CI_GROUPS[self.invocation]
        if primary is not None:
            raise primary


def _owner_mainci_snapshot_guard(invocation: _OriginalInvocation) -> None:
    group = _MAIN_CI_GROUPS.get(invocation)
    if group is None:
        _owner_stage_guard(invocation)
        return
    if type(group) is not _MainCiProofGroup or group.invocation is not invocation:
        _invalid()
    group.check()


def _owner_main_ci(
    invocation: _OriginalInvocation, request: Any,
) -> dict[str, Any]:
    """Acquire actual push/main originals for the typed APPLIED merge only."""
    import re
    from dataclasses import asdict

    from tools import agent_coordinator_handover as handover
    from tools import agent_final_preparation_ci as ci
    from tools.agent_continuation import digest
    from tools.agent_coordinator_delivery import REQUIRED_CHECKS
    from tools.agent_final_preparation_sources import _current_dependency_gate
    from tools.agent_learning import LearningError
    from tools.agent_learning_delivery import DeliverySources

    with _MainCiProofGroup(invocation, request):
        if invocation.session is None:
            _invalid()
        with _current_dependency_gate(invocation.session):
            before = _owner_merged_remote(invocation)
            current = invocation.pre_merge_sources
            if current is None or invocation.session is None:
                _invalid()
            candidate = invocation.candidate
            if candidate is None:
                _invalid()
            repo, sha = candidate["repository"], before["merge_sha"]
            reader = _owner_current_ci_reader(invocation.session)
            try:
                try:
                    checks = DeliverySources(reader)._event_checks(sha, "push")
                except LearningError:
                    _invalid()
            finally:
                reader.close()
            selected = checks["check_runs"]
            if (
                len(selected) != len(REQUIRED_CHECKS)
                or {row["name"] for row in selected} != set(REQUIRED_CHECKS)
            ):
                _invalid()
            suites: dict[int, Any] = {}
            runs: dict[int, Any] = {}
            jobs: dict[int, Any] = {}
            for row in selected:
                sid = row["check_suite"]["id"]
                url = row.get("details_url")
                if type(url) is not str or len(url) > 512:
                    _invalid()
                match = re.fullmatch(
                    re.escape(f"https://github.com/{repo}/actions/runs/")
                    + r"([1-9][0-9]*)/job/([1-9][0-9]*)", url,
                )
                if match is None:
                    _invalid()
                rid, jid = map(int, match.groups())
                if jid != row["id"]:
                    _invalid()
                if sid not in suites:
                    suites[sid] = current._json(f"repos/{repo}/check-suites/{sid}")
                if rid not in runs:
                    from tools import agent_final_preparation_records as records

                    run = current._json(f"repos/{repo}/actions/runs/{rid}")
                    if type(run) is not dict:
                        _invalid()
                    attempt = records.integer(run.get("run_attempt"), 1000, 1)
                    runs[rid] = run
                    jobs[rid] = current._json(
                        f"repos/{repo}/actions/runs/{rid}/attempts/{attempt}"
                        "/jobs?per_page=100"
                    )
            definitions: dict[str, bytes] = {}
            workflows: dict[str, Any] = {}
            for filename in ("tests.yml", "pre-commit.yml"):
                definitions[filename] = handover._git(
                    invocation.root, "show",
                    f"{candidate['policy_sha']}:.github/workflows/{filename}",
                ).encode()
                workflows[filename] = current._json(
                    f"repos/{repo}/actions/workflows/{filename}"
                )
            comparison = ci.Comparison(repo, sha, candidate["policy_sha"])
            if any(row.get("status") != "completed" for row in selected):
                _main_ci_pending(
                    comparison, selected, suites, runs, jobs, definitions, workflows,
                )
                if _owner_merged_remote(invocation) != before:
                    _invalid()
                return {
                    "status": "CI_PENDING", "machine_code": "CI_PENDING",
                    "merge_sha": sha,
                }
            try:
                normalized = _main_ci_normalize(
                    comparison, checks, suites, runs, jobs, definitions, workflows,
                )
            except ci.PrimaryCIError:
                _invalid()
            if _owner_merged_remote(invocation) != before:
                _invalid()
            return {
                "status": "PASS", "machine_code": "OK", "merge_sha": sha,
                "main_ci_digest": digest({
                    "candidate_digest": candidate["candidate_digest"],
                    "merge_sha": sha, "primary": asdict(normalized),
                }),
            }


def _owner_publish(
    invocation: _OriginalInvocation, phase: str, pending: str | None,
) -> dict[str, Any]:
    if getattr(invocation, "_view_poisoned", False):
        _invalid()
    try:
        return _owner_publish_raw(invocation, phase, pending)
    except BaseException:
        if getattr(invocation, "_a_witness", None) is not None:
            _owner_poison_view(invocation)
        raise


def _owner_publish_raw(
    invocation: _OriginalInvocation, phase: str, pending: str | None,
) -> dict[str, Any]:
    """Publish only an existing anchored state2, with no phase rollback."""
    from tools import agent_final_preparation_records as records
    from tools.agent_final_preparation_storage import Store

    current, candidate = invocation.pre_merge_sources, invocation.candidate
    if current is None or candidate is None or invocation.frames is None:
        _invalid()
    old = records.state2(_preparation_record(invocation, "final-state"))
    allowed = {
        "WAITING_OWNER_FINAL_APPROVAL": {"MERGING"},
        "MERGING": {"MERGING", "MAIN_CI_PENDING"},
        "MAIN_CI_PENDING": {"MAIN_CI_PENDING", "DONE"},
        "DONE": {"DONE"},
    }
    if (
        phase not in allowed.get(old["phase"], set())
        or old["candidate_digest"] != candidate["candidate_digest"]
        or old["owner_wait"] != invocation.frames.anchor
        or old["calls_consumed"] != 1
    ):
        _invalid()
    history = _owner_ledger(invocation)
    merge = _typed_owner_request(candidate, "merge_roadmap_pr")
    if phase == "MERGING":
        if (
            old["phase"] != "WAITING_OWNER_FINAL_APPROVAL"
            or old["pending_operation_id"] is not None or history is not None
            or pending != _pending_owner_identity(merge)
            or invocation._gate_a_request != merge.operation_id
            or invocation._new_ledger_request != merge.operation_id
        ):
            _invalid()
    else:
        if (
        history is None
        or history.get(merge.operation_id, {}).get("status") != "APPLIED"
    ):
            _invalid()
        sha = history[merge.operation_id]["receipt"]["merge_sha"]
        requests = [merge] + [
            _typed_owner_request(candidate, operation, sha)
            for operation in ("close_epic", "update_epic")
        ]
        if phase == "DONE":
            if (
                pending is not None
                or set(history) != {request.operation_id for request in requests}
                or any(entry["status"] != "APPLIED" for entry in history.values())
                or old["pending_operation_id"] != _pending_owner_identity(requests[-1])
            ):
                _invalid()
        else:
            if pending not in {
                _pending_owner_identity(request) for request in requests
            }:
                _invalid()
            if old["phase"] == "MERGING" and pending != _pending_owner_identity(merge):
                _invalid()
    value = records.state2(records.seal({
        **{key: item for key, item in old.items() if key != "record_digest"},
        "phase": phase, "pending_operation_id": pending,
    }))
    records.state2_transition(old, value)
    local_witness = _owner_a_publish_before(invocation, phase, pending, old, value)
    store = Store(invocation.state, invocation.root, current.profile, invocation.guard)
    if store.read("final-state") != old:
        _invalid()
    store.write("final-state", value)
    if store.read("final-state") != value:
        _invalid()
    _owner_a_publish_after(invocation, local_witness, value)
    invocation._verified_b = None
    invocation._observed_merge_sha = None
    invocation.guard()
    return value


def _owner_request_binding(invocation: _OriginalInvocation, request: Any) -> None:
    """Exact original typed request and durable pending association."""
    from dataclasses import asdict

    from tools import agent_epic_final_delivery as final_delivery
    from tools import agent_final_preparation_records as records

    candidate = invocation.candidate
    if candidate is None or type(request) is not final_delivery.Request:
        _invalid()
    expected = _typed_owner_request(candidate, request.operation, request.merge_sha)
    if asdict(request) != asdict(expected):
        _invalid()
    final = records.state2(_preparation_record(invocation, "final-state"))
    if (
        final["candidate_digest"] != candidate["candidate_digest"]
        or final["pending_operation_id"] != _pending_owner_identity(expected)
        or final["phase"] != (
            "MERGING" if request.operation == "merge_roadmap_pr" else "MAIN_CI_PENDING"
        )
    ):
        _invalid()
    invocation.guard()




def _owner_effect_gate(invocation: _OriginalInvocation, request: Any) -> None:
    """Gate B: fresh CI after exact visible INTENT, immediately before write."""
    _owner_request_binding(invocation, request)
    checked = _owner_b_source_gate(invocation, request)
    if checked.get("status") != "PASS":
        raise PhaseBlocked("FINAL_STAGE_GATE_INVALID")
    reader = invocation.scope._active_reader()
    value = reader._load()
    if (
        value["ci"] is None or value["ci"]["status"] != "RESERVED"
        or any(v["status"] != "READY" for v in value["requests"].values())
        or invocation.candidate is None or invocation.report is None
        or invocation.approval is None or invocation._owner_graph is None
    ):
        _invalid()
    invocation._verified_b = _VerifiedEffectGate(
        request=request, ci_identity=value["ci"]["identity_digest"],
        attempt=value["ci"]["attempts"],
        candidate_digest=invocation.candidate["candidate_digest"],
        approval_digest=invocation.approval["approved_digest"],
        report_digest=invocation.report["record_digest"],
        owner_graph=invocation._owner_graph,
        frame_digest=wait.digest(invocation.scope.frame),
        deadline_at=reader.current_deadline,
    )
    invocation._observed_merge_sha = None


def _owner_ledger_writer(invocation: _OriginalInvocation, request: Any) -> Any:
    """Fixed typed adapter around the original final-effect Ledger writer."""
    from dataclasses import asdict

    from tools.agent_epic_delivery import Ledger

    class OwnedLedger(Ledger):
        def __init__(self) -> None:
            _owner_request_binding(invocation, request)
            super().__init__(invocation.state / "final-ledger", invocation.root)

        def load(self) -> dict[str, dict[str, object]]:
            _owner_request_binding(invocation, request)
            current = _owner_ledger(invocation)
            if current is None:
                if (
                    request.operation != "merge_roadmap_pr"
                    or invocation._new_ledger_request != request.operation_id
                ):
                    _invalid()
                # Only this invocation's fully admitted initial absent path may
                # supply the original writer's first empty map. Never a restart.
                return {}
            return current

        def save(self, value: Any) -> None:
            try:
                self._save_original(value)
            except BaseException:
                if getattr(invocation, "_a_witness", None) is not None:
                    _owner_poison_view(invocation)
                raise

        def _save_original(self, value: Any) -> None:
            local_witness = None
            new_intent = False
            _owner_request_binding(invocation, request)
            previous = self.load()
            if type(value) is not dict or set(value) != set(previous) | {
                request.operation_id,
            }:
                _invalid()
            for key, old in previous.items():
                if key != request.operation_id and value.get(key) != old:
                    _invalid()
                if old["status"] == "APPLIED" and value.get(key) != old:
                    _invalid()
            changed = value[request.operation_id]
            if (
                type(changed) is not dict
                or set(changed) != {"operation", "request", "status", "receipt"}
                or changed["request"] != asdict(request)
                or changed["operation"] != request.operation
                or changed["status"] not in {"INTENT", "UNKNOWN", "APPLIED"}
            ):
                _invalid()
            # The initial INTENT still requires fresh pre-effect facts here.
            # Later persistence records the one observed write/reconcile outcome;
            # it never retries an uncertain effect or rewrites an APPLIED entry.
            if not previous or request.operation_id not in previous:
                if changed["status"] != "INTENT" or changed["receipt"] is not None:
                    _invalid()
                if invocation._gate_a_request != request.operation_id:
                    _invalid()
                current = invocation.pre_merge_sources
                if current is None:
                    _invalid()
                from tools.agent_epic_final_delivery import authorization_code

                new_intent = True
                local_witness = _owner_a_intent_before(
                    invocation, request, previous, value,
                )
                code = authorization_code(request, current.authority)
                if code is not None:
                    raise PhaseBlocked(code)
                invocation.guard()
            elif previous[request.operation_id]["status"] in {"INTENT", "UNKNOWN"}:
                if changed["status"] not in {"UNKNOWN", "APPLIED"}:
                    _invalid()
                _owner_stage_guard(invocation)
                invocation.guard()
            super().save(value)
            persisted = _owner_ledger(invocation)
            if persisted != value:
                _invalid()
            if new_intent:
                _owner_a_intent_after(invocation, local_witness, persisted)
            invocation._new_ledger_request = None
            invocation._gate_a_request = None
            invocation.guard()

    return OwnedLedger()


def _owner_reconcile_merge(
    invocation: _OriginalInvocation, request: Any,
) -> tuple[str, dict[str, object] | None]:
    """Read-only reconciliation, including INTENT/UNKNOWN; never permits replay."""
    from tools import agent_final_preparation_records as records

    _owner_request_binding(invocation, request)
    _owner_stage_guard(invocation)
    current = invocation.pre_merge_sources
    if current is None or request.operation != "merge_roadmap_pr":
        _invalid()
    repo = request.repository

    def snapshot() -> dict[str, Any]:
        _owner_stage_guard(invocation)
        value = current._json(f"repos/{repo}/pulls/{request.pr}")
        if (
            type(value) is not dict or type(value.get("number")) is not int
            or value["number"] != request.pr
        ):
            _invalid()
        for key, ref in (("base", "main"), ("head", request.roadmap_ref)):
            item = value.get(key)
            if (
                type(item) is not dict or item.get("ref") != ref
                or type(item.get("repo")) is not dict
                or item["repo"].get("full_name") != repo
            ):
                _invalid()
        if value["head"].get("sha") != request.roadmap_sha:
            _invalid()
        refs = current.session.github.branches(repo)
        if refs.get(request.roadmap_ref) != request.roadmap_sha:
            _invalid()
        if value.get("state") == "open" and value.get("merged") is False:
            if (
                value["base"].get("sha") != request.main_sha
                or refs.get("main") != request.main_sha
                or value.get("merge_commit_sha") is not None
            ):
                _invalid()
            result = {"outcome": "NOT_APPLIED", "pr": value, "refs": refs}
        elif value.get("state") == "closed" and value.get("merged") is True:
            sha = records.hash_value(value.get("merge_commit_sha"), sha=True)
            if (
                sha in {request.main_sha, request.roadmap_sha}
                or value["base"].get("sha") not in {request.main_sha, sha}
                or refs.get("main") != sha
            ):
                _invalid()
            commit = current._json(f"repos/{repo}/git/commits/{sha}")
            if (
                type(commit) is not dict or commit.get("sha") != sha
                or type(commit.get("parents")) is not list
                or len(commit["parents"]) != 2
                or any(type(parent) is not dict for parent in commit["parents"])
                or [parent.get("sha") for parent in commit["parents"]]
                != [request.main_sha, request.roadmap_sha]
            ):
                _invalid()
            result = {
                "outcome": "APPLIED", "pr": value, "refs": refs,
                "commit": commit, "merge_sha": sha,
            }
        else:
            _invalid()
        _owner_stage_guard(invocation)
        invocation.guard()
        return result

    before = snapshot()
    if snapshot() != before:
        _invalid()
    if before["outcome"] == "APPLIED":
        return "APPLIED", {"merge_sha": before["merge_sha"]}
    return "NOT_APPLIED", None


def _owner_transport(invocation: _OriginalInvocation, request: Any) -> Any:
    """Original typed write implementation with fixed phase-aware source joins."""
    from dataclasses import asdict

    from tools import agent_epic_delivery as task_delivery
    from tools import agent_epic_final_delivery as final_delivery
    from tools.agent_epic_final_transport import GhFinalTransport

    def binding(actual: Any) -> None:
        if (
            type(actual) is not final_delivery.Request
            or asdict(actual) != asdict(request)
        ):
            _invalid()
        entries = _owner_ledger(invocation)
        if (
            entries is None or request.operation_id not in entries
            or entries[request.operation_id]["status"] != "INTENT"
        ):
            _invalid()
        reader = invocation.scope._active_reader()
        reader.owner_next_phase()
        _owner_effect_gate(invocation, actual)
        reader.owner_gate_completed()
        if _owner_ledger(invocation) != entries:
            _invalid()
        # Source checks and original mandate are repeated immediately before
        # the inherited exact write argv. No cached approval is permission.
        _owner_request_binding(invocation, actual)
        current = invocation.pre_merge_sources
        if current is None:
            _invalid()
        code = final_delivery.authorization_code(actual, current.authority)
        if code is not None:
            raise PhaseBlocked(code)
        _owner_nonci_guard(invocation)
        invocation.guard()

    class OwnerTransport(GhFinalTransport):
        def reconcile(self, actual: Any) -> tuple[str, dict[str, object] | None]:
            if (
                type(actual) is not final_delivery.Request
                or asdict(actual) != asdict(request)
            ):
                raise task_delivery.OutcomeUnknown
            if actual.operation == "merge_roadmap_pr":
                return _owner_reconcile_merge(invocation, actual)
            _owner_request_binding(invocation, actual)
            _owner_stage_guard(invocation)
            proof = _owner_merged_remote(invocation)
            if proof["merge_sha"] != actual.merge_sha:
                raise task_delivery.OutcomeUnknown
            result = super().reconcile(actual)
            _owner_stage_guard(invocation)
            proof = _owner_merged_remote(invocation)
            if proof["merge_sha"] != actual.merge_sha:
                raise task_delivery.OutcomeUnknown
            return result

    if invocation.session is None:
        _invalid()
    return OwnerTransport(
        invocation.root, before_write=binding,
        read=invocation.session._github_read,
    )


def _owner_nonci_guard(invocation: _OriginalInvocation) -> _VerifiedEffectGate:
    """Fresh local/owner/request guard; no current or historical CI acquisition."""
    from tools import agent_epic_final_delivery as final_delivery
    from tools import agent_final_preparation_records as records
    from tools.agent_owner_final_records import read_original_shapes

    ticket = invocation._verified_b
    if type(ticket) is not _VerifiedEffectGate:
        _invalid()
    if (
        invocation.normal_session is None
        or invocation.session is not invocation.normal_session
        or invocation.frames is None or invocation.pre_merge_sources is None
        or invocation.candidate is None or invocation.report is None
    ):
        _invalid()
    _owner_request_binding(invocation, ticket.request)
    history = _owner_ledger(invocation)
    if history is None or ticket.request.operation_id not in history:
        _invalid()
    _installed_sources(invocation)
    current = invocation.pre_merge_sources
    current.check()
    if (
        records.candidate(_preparation_record(
            invocation, "candidate-" + ticket.candidate_digest,
        )) != invocation.candidate
        or records.report(_preparation_record(
            invocation, "report-" + ticket.report_digest,
        )) != invocation.report
        or invocation.candidate["candidate_digest"] != ticket.candidate_digest
        or invocation.report["record_digest"] != ticket.report_digest
        or wait.digest(invocation.scope.frame) != ticket.frame_digest
        or invocation.scope._active_reader().current_deadline > ticket.deadline_at
    ):
        _invalid()
    namespace = _owner_namespace_identity(invocation)
    mandate = invocation.frames.sources["mandate"]
    graph = read_original_shapes(
        invocation.state, invocation.root, invocation.candidate,
        max_bytes=current.profile["max_artifact_bytes"],
        issued_at=mandate["issued_at"], expires_at=mandate["expires_at"],
    )
    if (
        graph is None or graph != ticket.owner_graph
        or graph.approval["approved_digest"] != ticket.approval_digest
        or _owner_namespace_identity(invocation) != namespace
    ):
        _invalid()
    code = final_delivery.authorization_code(ticket.request, current.authority)
    if code is not None:
        raise PhaseBlocked(code)
    _installed_sources(invocation)
    current.check()
    invocation.guard()
    return ticket


def _owner_nonci_read(scope: Any, argv: tuple[str, ...]) -> None:
    """Closed original reconcile endpoints, bound to the one source-created B."""
    invocation = _current_scope(scope)
    ticket = _owner_nonci_guard(invocation)
    request = ticket.request
    reader = scope._active_reader()
    value = reader._load()
    if (
        value["ci"] is None or value["ci"]["status"] != "READY"
        or value["ci"]["identity_digest"] != ticket.ci_identity
        or value["ci"]["attempts"] != ticket.attempt
    ):
        _invalid()
    repo = request.repository
    allowed = {
        ("gh", "api", f"repos/{repo}/pulls/{request.pr}"),
        ("gh", "api", "--paginate", "--slurp", f"repos/{repo}/branches?per_page=100"),
    }
    observed = invocation._observed_merge_sha
    if observed is not None:
        allowed.add(("gh", "api", f"repos/{repo}/git/commits/{observed}"))
    if request.operation == "close_epic":
        allowed.add((
            "gh", "issue", "view", str(request.epic), "--repo", repo,
            "--json", "number,state",
        ))
    if request.operation == "update_epic":
        allowed.add((
            "gh", "api", "--paginate", "--slurp",
            f"repos/{repo}/issues/{request.epic}/comments?per_page=100",
        ))
    if argv not in allowed:
        _invalid()


def _owner_nonci_observe(scope: Any, argv: tuple[str, ...], output: str) -> None:
    """Derive the only commit-read witness from the already returned exact PR."""
    import json

    from tools import agent_final_preparation_records as records

    invocation = _current_scope(scope)
    ticket = _owner_nonci_guard(invocation)
    request = ticket.request
    if argv != ("gh", "api", f"repos/{request.repository}/pulls/{request.pr}"):
        return
    invocation._observed_merge_sha = None
    try:
        value = json.loads(output, object_pairs_hook=guards._final_preseed_pairs)
        if (
            type(value) is not dict or type(value.get("number")) is not int
            or value["number"] != request.pr
        ):
            _invalid()
        for key, ref in (("base", "main"), ("head", request.roadmap_ref)):
            item = value.get(key)
            if (
                type(item) is not dict or item.get("ref") != ref
                or type(item.get("repo")) is not dict
                or item["repo"].get("full_name") != request.repository
            ):
                _invalid()
        if value["head"].get("sha") != request.roadmap_sha:
            _invalid()
        if value.get("state") == "open" and value.get("merged") is False:
            if request.operation != "merge_roadmap_pr":
                _invalid()
            if value["base"].get("sha") != request.main_sha:
                _invalid()
            return
        if value.get("state") != "closed" or value.get("merged") is not True:
            _invalid()
        sha = records.hash_value(value.get("merge_commit_sha"), sha=True)
        if (
            sha in {request.main_sha, request.roadmap_sha}
            or value["base"].get("sha") not in {request.main_sha, sha}
            or (request.merge_sha is not None and request.merge_sha != sha)
        ):
            _invalid()
        invocation._observed_merge_sha = sha
    except (ValueError, TypeError, KeyError, RecursionError):
        _invalid()
    _owner_nonci_guard(invocation)


def _owner_stage_guard(invocation: _OriginalInvocation) -> None:
    """Choose from actual original CI state, not a caller-selected read mode."""
    current = invocation.pre_merge_sources
    if current is None:
        _invalid()
    value = invocation.scope._active_reader()._load()
    if value["ci"] is not None and value["ci"]["status"] == "READY":
        _owner_nonci_guard(invocation)
        return
    current.stable_assessment()
    _current_owner_approval(invocation)
    invocation.guard()


def _owner_deliver(invocation: _OriginalInvocation, request: Any) -> Any:
    """Fixed schema8 A/INTENT/B delivery; old uncertain writes never replay."""
    from dataclasses import asdict

    from tools import agent_epic_delivery as delivery
    from tools import agent_epic_final_delivery as final_delivery

    _owner_request_binding(invocation, request)
    if not final_delivery.valid_request(request):
        _invalid()
    current = invocation.pre_merge_sources
    if current is None:
        _invalid()
    code = final_delivery.authorization_code(request, current.authority)
    if code is not None:
        raise PhaseBlocked(code)
    ledger = _owner_ledger_writer(invocation, request)
    entries = ledger.load()
    previous = entries.get(request.operation_id)
    transport = _owner_transport(invocation, request)
    if previous is not None:
        if previous["request"] != asdict(request):
            _invalid()
        if previous["status"] == "APPLIED":
            return delivery.Result("NO_OP", "ALREADY_APPLIED", previous["receipt"])
        if previous["status"] not in {"INTENT", "UNKNOWN"}:
            _invalid()
        # This branch is reconciliation only. In particular NOT_APPLIED cannot
        # fall through to an effect, even if a new source gate would now pass.
        try:
            state, receipt = transport.reconcile(request)
        except Exception:
            return delivery.Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
        if state != "APPLIED" or not receipt:
            return delivery.Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
        entries[request.operation_id] = final_delivery._entry(
            request, "APPLIED", receipt,
        )
        ledger.save(entries)
        return delivery.Result("NO_OP", "ALREADY_APPLIED", dict(receipt))
    if invocation._gate_a_request != request.operation_id:
        _invalid()
    if request.operation == "update_epic":
        candidate = invocation.candidate
        if candidate is None:
            _invalid()
        closed = _typed_owner_request(candidate, "close_epic", request.merge_sha)
        if entries.get(closed.operation_id, {}).get("status") != "APPLIED":
            raise PhaseBlocked("EPIC_NOT_CLOSED")
    entries[request.operation_id] = final_delivery._entry(request, "INTENT", None)
    ledger.save(entries)
    # Exact visible persisted reread is required before the original transport's
    # before_write callback acquires fresh B. No crash-durability claim follows.
    if ledger.load() != entries:
        _invalid()
    try:
        receipt = dict(transport.effect(request))
        if not receipt:
            raise delivery.OutcomeUnknown
    except Exception:
        entries[request.operation_id] = final_delivery._entry(
            request, "UNKNOWN", None,
        )
        ledger.save(entries)
        return delivery.Result("ESCALATE", "ESCALATE_UNKNOWN_OUTCOME", None)
    entries[request.operation_id] = final_delivery._entry(request, "APPLIED", receipt)
    ledger.save(entries)
    return delivery.Result("APPLIED", "OK", receipt)


def _owner_gate_a(invocation: _OriginalInvocation, request: Any) -> None:
    """Use this reserved attempt for A; pending consumes the same finite CI8."""
    reader = invocation.scope._active_reader()
    while True:
        checked = _owner_source_gate(invocation, request)
        if checked["status"] == "PASS":
            invocation._gate_a_request = request.operation_id
            return
        reader.owner_next_phase(pending=True)


def _continue_current(scope: Any) -> dict[str, Any]:
    """Fixed original schema8 owner continuation, with no legacy gate downgrade."""
    from tools import agent_final_preparation_records as records

    invocation = _current_scope(scope)
    if (
        invocation.normal_session is None
        or invocation.session is not invocation.normal_session
    ):
        _invalid()
    reader = scope._active_reader()
    final = records.state2(_preparation_record(invocation, "final-state"))
    phase = final["phase"]
    if phase == "WAITING_OWNER_FINAL_APPROVAL":
        # This first resume assessment is merge A, not an extra uncharged gate.
        checked = _assess_current(scope)
        if checked.get("status") != "PASS":
            raise PhaseBlocked("FINAL_STAGE_GATE_INVALID")
        if invocation.approval is None:
            return {
                "status": "WAITING_OWNER_FINAL_APPROVAL",
                "machine_code": "WAITING_OWNER_FINAL_APPROVAL",
                "report": invocation.report,
            }
        candidate = invocation.candidate
        current = invocation.pre_merge_sources
        if candidate is None or current is None:
            _invalid()
        merge = _typed_owner_request(candidate, "merge_roadmap_pr")
        from tools.agent_epic_final_delivery import authorization_code

        code = authorization_code(merge, current.authority)
        if code is not None:
            raise PhaseBlocked(code)
        if (
            final["pending_operation_id"] is not None
            or _owner_ledger(invocation) is not None
        ):
            _invalid()
        invocation._gate_a_request = merge.operation_id
        invocation._new_ledger_request = merge.operation_id
        _owner_publish(invocation, "MERGING", _pending_owner_identity(merge))
        result = _owner_deliver(invocation, merge)
        if result.status not in {"APPLIED", "NO_OP"}:
            return {"status": result.status, "machine_code": result.machine_code}
        _owner_publish(invocation, "MAIN_CI_PENDING", _pending_owner_identity(merge))
        reader.owner_next_phase()
    elif phase in {"MERGING", "MAIN_CI_PENDING"}:
        _owner_original_local(scope)
        candidate = invocation.candidate
        if candidate is None:
            _invalid()
        merge = _typed_owner_request(candidate, "merge_roadmap_pr")
        history = _owner_ledger(invocation)
        if history is None or merge.operation_id not in history:
            # A persisted pending phase can never reset absent history to {}.
            _invalid()
        if phase == "MERGING":
            if final["pending_operation_id"] != _pending_owner_identity(merge):
                _invalid()
            result = _owner_deliver(invocation, merge)
            if result.status not in {"APPLIED", "NO_OP"}:
                return {"status": result.status, "machine_code": result.machine_code}
            _owner_publish(
                invocation, "MAIN_CI_PENDING", _pending_owner_identity(merge),
            )
            reader.owner_next_phase()
    else:
        # DONE projection is deliberately not inferred from a neutral state row.
        # A separately source-closed terminal read route must precede reuse.
        _invalid()
    candidate = invocation.candidate
    if candidate is None:
        _invalid()
    history = _owner_ledger(invocation)
    if (
        history is None
        or history.get(merge.operation_id, {}).get("status") != "APPLIED"
    ):
        _invalid()
    sha = history[merge.operation_id]["receipt"]["merge_sha"]
    operations = ("close_epic", "update_epic")
    for index, operation in enumerate(operations):
        request = _typed_owner_request(candidate, operation, sha)
        history = _owner_ledger(invocation)
        if history is None:
            _invalid()
        previous = history.get(request.operation_id)
        state = records.state2(_preparation_record(invocation, "final-state"))
        if previous is not None:
            later = _typed_owner_request(candidate, "update_epic", sha)
            if state["pending_operation_id"] != _pending_owner_identity(request):
                if not (
                    operation == "close_epic" and previous["status"] == "APPLIED"
                    and state["pending_operation_id"] == _pending_owner_identity(later)
                ):
                    _invalid()
            if previous["status"] != "APPLIED":
                # Existing INTENT/UNKNOWN is read-only reconciliation only;
                # source admission never transforms NOT_APPLIED into a retry.
                result = _owner_deliver(invocation, request)
                if result.status not in {"APPLIED", "NO_OP"}:
                    return {
                        "status": result.status, "machine_code": result.machine_code,
                    }
        else:
            _owner_gate_a(invocation, request)
            _owner_publish(
                invocation, "MAIN_CI_PENDING", _pending_owner_identity(request),
            )
            result = _owner_deliver(invocation, request)
            if result.status not in {"APPLIED", "NO_OP"}:
                return {"status": result.status, "machine_code": result.machine_code}
        if index == 0 and previous is None:
            reader.owner_next_phase()
        if index == 1 and previous is not None:
            # A completed/reconciled old update is not effect permission. This
            # invocation still proves fresh current main CI before DONE.
            _owner_gate_a(invocation, request)
    _owner_publish(invocation, "DONE", None)
    return {"status": "ROADMAP_DONE", "machine_code": "OK"}


def _terminal_done(invocation: _OriginalInvocation) -> dict[str, Any]:
    """LOCAL historical completion only; no Reader, reservation or effect."""
    import time

    from tools import agent_final_preparation_delivery as preparation
    from tools import agent_final_preparation_records as records
    from tools.agent_epic_session_runtime import SessionRuntime
    from tools.agent_epic_wait_scope import Scope
    from tools.agent_owner_final_records import read_original_shapes

    session, frame_sources = invocation.normal_session, invocation.frames
    pending = session.wait_scope if type(session) is SessionRuntime else None
    if session is None:
        _invalid()
    if (
        type(pending) is not Scope or not pending._retained_owner
        or frame_sources is None or session.state != invocation.state
        or session.root != invocation.root
    ):
        _invalid()
    invocation.guard()
    _installed_sources(invocation)
    original = frame_sources._wait_original()
    if (
        original["ci"]["status"] != "READY"
        or original["ci"]["attempts"] > invocation.limits["max_ci_polls"]
        or any(row["status"] != "READY" for row in original["requests"].values())
    ):
        _invalid()
    sources = frame_sources.sources
    profile, mandate = sources["profile"], sources["mandate"]
    final = records.state2(_preparation_record(invocation, "final-state"))
    if (
        final["phase"] != "DONE" or final["calls_consumed"] != 1
        or final["owner_wait"] != frame_sources.anchor
        or final["pending_operation_id"] is not None
    ):
        _invalid()
    candidate = records.candidate(_preparation_record(
        invocation, "candidate-" + final["candidate_digest"],
    ))
    if (
        candidate["candidate_digest"] != final["candidate_digest"]
        or wait.digest({
            "repository": candidate["repository"], "epic": candidate["epic"],
            "roadmap_sha": candidate["roadmap_sha"],
            "main_sha": candidate["main_sha"], "merge_sha": None,
            "pr": candidate["pr"],
        }) != frame_sources.anchor["wait_identity_digest"]
    ):
        _invalid()
    for key in records.IDENTITY:
        expected = (sources["config"]["approved_launch_digest"]
                    if key == "launch_digest" else profile[key])
        if final[key] != expected or candidate[key] != expected:
            _invalid()
    for key in ("roadmap_ref", "policy_sha", "owner_identity"):
        if candidate[key] != profile[key]:
            _invalid()
    assessment = records.assessment(_preparation_record(
        invocation, "assessment-" + candidate["preparation_digest"],
    ))
    if (
        assessment["status"] != "READY" or assessment["machine_code"] != "OK"
        or assessment["record_digest"] != candidate["preparation_digest"]
    ):
        _invalid()
    request = records.request(records.seal({
        "schema_version": "1.0",
        **{key: candidate[key] for key in records.IDENTITY},
        "operation": "create_roadmap_pr",
        **{key: candidate[key] for key in (
            "roadmap_ref", "policy_sha", "main_sha", "roadmap_sha",
            "preparation_digest",
        )},
    }, self_digest="operation_id"))
    entries = preparation.entries(_preparation_record(invocation, "ledger"))
    if set(entries) != {request["operation_id"]}:
        _invalid()
    entry = entries[request["operation_id"]]
    expected_receipt = {
        "pr": candidate["pr"], "repository": candidate["repository"],
        "base_ref": "main", "head_ref": candidate["roadmap_ref"],
        "main_sha": candidate["main_sha"], "roadmap_sha": candidate["roadmap_sha"],
        "operation_id": request["operation_id"],
    }
    if (
        entry["status"] != "APPLIED" or entry["request"] != request
        or preparation.receipt(entry["receipt"], request) != expected_receipt
    ):
        _invalid()
    namespace = _owner_namespace_identity(invocation)
    graph = read_original_shapes(
        invocation.state, invocation.root, candidate,
        max_bytes=profile["max_artifact_bytes"],
        issued_at=mandate["issued_at"], expires_at=mandate["expires_at"],
    )
    if (
        graph is None or _owner_namespace_identity(invocation) != namespace
        or not graph.approval["approved_at"] <= time.time()
        < frame_sources.anchor["deadline_at"]
    ):
        _invalid()
    _demo_prose_guard(graph.demo)
    invocation.candidate = candidate
    history = _owner_ledger(invocation)
    merge = _typed_owner_request(candidate, "merge_roadmap_pr")
    if (
        history is None
        or history.get(merge.operation_id, {}).get("status") != "APPLIED"
    ):
        _invalid()
    sha = history[merge.operation_id]["receipt"]["merge_sha"]
    requests = [merge] + [
        _typed_owner_request(candidate, operation, sha)
        for operation in ("close_epic", "update_epic")
    ]
    if set(history) != {item.operation_id for item in requests} or any(
        entry["status"] != "APPLIED" for entry in history.values()
    ):
        _invalid()
    if (
        records.state2(_preparation_record(invocation, "final-state")) != final
        or records.candidate(_preparation_record(
            invocation, "candidate-" + candidate["candidate_digest"],
        )) != candidate
        or read_original_shapes(
            invocation.state, invocation.root, candidate,
            max_bytes=profile["max_artifact_bytes"],
            issued_at=mandate["issued_at"], expires_at=mandate["expires_at"],
        ) != graph
        or _owner_namespace_identity(invocation) != namespace
        or frame_sources._wait_original() != original
    ):
        _invalid()
    _installed_sources(invocation)
    invocation.guard()
    return {"status": "ROADMAP_DONE", "machine_code": "OK"}
