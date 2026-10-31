"""Owned current-process composition; partial restarts never gain call authority."""

from __future__ import annotations

import stat
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from tools import agent_epic_delivery as delivery
from tools import agent_epic_runtime_cli as guards
from tools import agent_orchestrate as workflow
from tools.agent_continuation import digest
from tools.agent_continuation_backend import SettingsAdapter
from tools.agent_epic_loop import CheckpointStore, PhaseBlocked, RunnerLock
from tools.agent_epic_session import Session
from tools.agent_epic_wait_scope import Scope
from tools.agent_epic_workflow import codex_role_adapter
from tools.agent_final_preparation_review import (
    AggregateReview,
    _LearningAggregateReview,
    learning_request_digest_fact,
)
from tools.agent_final_preparation_role import RoleCounter
from tools.agent_final_preparation_storage import Store
from tools.agent_learning_evidence import SourceReader

if TYPE_CHECKING:
    from tools.agent_learning_planning_sources import PlanningCallFact


def _session_final_sources(
    state: Path, root: Path, sources: dict[str, Any]
) -> dict[str, Any]:
    seed = CheckpointStore(state, root).load()
    cursor = workflow.StateStore(state / "session-control").load("cursor")
    if seed is None or seed.get("schema_version") != "3.0" or cursor is None:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    Session._validate_cursor(cursor, seed)
    if any(
        seed.get(key) != sources["mandate"][key]
        for key in ("repository", "epic", "policy_sha")
    ):
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    if (
        cursor["mode"] != "FINAL"
        or cursor["pending_task"] is not None
        or cursor["current_task"] not in cursor["completed_tasks"]
    ):
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    directory = (
        state
        if cursor["current_task"] == seed["task"]
        else (state / "tasks" / str(cursor["current_task"]))
    )
    guards.secure_directory(directory, root)
    current = CheckpointStore(directory, root).load()
    if (
        current is None
        or current["task"] != cursor["current_task"]
        or current["phase"] != "NEXT_TASK"
        or current["status"] != "TASK_DONE"
        or current["pending_phase"] is not None
        or any(
            current.get(key) != sources["mandate"][key]
            for key in ("repository", "epic", "policy_sha")
        )
    ):
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    config = sources["config"]
    iterations = config["task_iterations"] + len(cursor["completed_tasks"])
    context = delivery.MandateContext(
        int(time.time()),
        config["owner_identity"],
        config["started_at"],
        iterations,
        config["follow_up_issues"],
    )
    code = delivery.mandate_lifecycle_code(
        sources["mandate"], context, at_task_start=False
    )
    if code is not None:
        raise PhaseBlocked(code)
    return {
        "seed": seed,
        "cursor": cursor,
        "current": current,
        "iterations": iterations,
    }


class _OriginalSources:
    """An immutable comparison baseline under two continuously held real locks."""

    def __init__(
        self,
        seed: Path,
        root: Path,
        supervisor: RunnerLock,
        ownership: RunnerLock,
        wait_scope: Scope,
    ) -> None:
        self.seed, self.root = seed, root
        self.supervisor, self.ownership = supervisor, ownership
        if (
            type(wait_scope) is not Scope
            or wait_scope.state != seed
            or wait_scope.root.resolve() != root.resolve()
            or wait_scope.read_only
        ):
            raise PhaseBlocked("WAIT_FRAME_REQUIRED")
        if wait_scope.active_stage != "FINAL:FINAL_GATE":
            raise PhaseBlocked("WAIT_FRAME_REQUIRED")
        self.wait_scope = wait_scope
        self.reader = wait_scope.reader("FINAL:FINAL_GATE")
        self.stage_guard = self.reader.guard
        self.stage_deadline = self.reader.current_deadline
        self._owned()
        sources = guards.final_preparation_original_sources(seed, root)
        self.continuation, self.backend = self._native_sources(sources)
        self.baseline = self._baseline(sources)
        self.config = sources["config"]
        self.profile = sources["profile"]
        self.maximum_consumed = sources["current"]["calls_consumed"]
        self.session_baseline = digest(self._session_sources(sources))
        self.check()

    def _session_sources(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _session_final_sources(self.seed, self.root, sources)

    def _native_sources(
        self, sources: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        continuation = guards.continuation_profile_guard(
            self.seed, sources["config"], sources["mandate"]
        )
        if continuation is None:
            raise PhaseBlocked("CONTINUATION_PROFILE_REQUIRED")
        quota = guards.quota_profile_guard(
            self.seed, sources["config"], sources["mandate"]
        )
        return continuation, guards.role_backend_guard(self.seed, continuation, quota)

    def _baseline(self, sources: dict[str, Any]) -> str:
        continuation, backend = self._native_sources(sources)
        return digest(
            {
                "original": {k: v for k, v in sources.items() if k != "current"},
                "continuation": continuation,
                "backend": backend,
            }
        )

    def _owned(self) -> None:
        if (
            RunnerLock.current_owned(self.seed) is not self.ownership
            or RunnerLock.current_owned(self.seed / "session-control")
            is not self.supervisor
        ):
            raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")

    def _assert_stage(self) -> None:
        if (
            self.wait_scope.active_stage != "FINAL:FINAL_GATE"
            or self.wait_scope.reader("FINAL:FINAL_GATE") is not self.reader
            or self.reader.guard is not self.stage_guard
            or self.reader.current_deadline > self.stage_deadline
        ):
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")

    def check(self) -> None:
        self._owned()
        sources = guards.final_preparation_original_sources(self.seed, self.root)
        guards.managed_launch_guard(self.seed, self.root)
        self._assert_stage()
        self.stage_guard()
        if digest(self._session_sources(sources)) != self.session_baseline:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        if self._baseline(sources) != self.baseline:
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
        consumed = sources["current"]["calls_consumed"]
        if consumed < self.maximum_consumed:
            raise PhaseBlocked("FINAL_ROLE_CALL_SPENT")
        self.maximum_consumed = consumed

    def deadline(self) -> int:
        self.check()
        mandate = guards.private_json(self.seed / "mandate.json")
        return int(
            min(
                self.reader.current_deadline,
                self.wait_scope.frame["deadline_at"],
                mandate["expires_at"],
                self.config["started_at"] + mandate["limits"]["max_duration_seconds"],
            )
        )


class _ReservedDelegate:
    """Forward only the actual current controller payload's original reservation."""

    def __init__(
        self, native: Any, counter: RoleCounter, sources: _OriginalSources
    ) -> None:
        self.native, self.counter, self.sources = native, counter, sources
        self.executor_command = native.executor_command
        self.controller_command = native.controller_command
        self.root = native.root
        self.timeout_seconds = native.timeout_seconds
        self.output_limit = native.output_limit
        self.backend_digest = getattr(native, "backend_digest", None)
        self.quota_resume_enabled = getattr(native, "quota_resume_enabled", False)
        self.aggregate: AggregateReview | None = None
        self.forwarded = False
        self.native_guard = native.guard
        self.active_reservation: dict[str, Any] | None = None

    def _transport_guard(self) -> None:
        self.native_guard()
        self.counter.store.assert_owned()
        state, reservation = self.counter.read()
        if (
            self.active_reservation is None
            or reservation is None
            or reservation != self.active_reservation
            or state is None
            or state["calls_consumed"] != 1
            or not reservation["started_at"] <= time.time() < reservation["deadline_at"]
            or reservation["deadline_at"] > self.sources.deadline()
        ):
            raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")

    def _call(self, command: Sequence[str], payload: dict[str, Any]) -> dict[str, Any]:
        self.counter.store.assert_owned()
        state, reservation = self.counter.read()
        safe = {k: v for k, v in payload.items() if k != "contract_object"}
        aggregate = self.aggregate
        now = time.time()
        if (
            self.forwarded
            or aggregate is None
            or tuple(command) != tuple(self.controller_command)
            or type(safe.get("repair_iteration")) is not int
            or safe["repair_iteration"] != 0
            or "trusted_policy" not in safe
            or state is None
            or state["calls_consumed"] != 1
            or reservation is None
            or reservation["status"] != "RESERVED"
            or reservation["input_digest"] != digest(safe)
            or reservation["backend_digest"] != digest(aggregate.adapter.owner)
            or reservation["preparation_identity_digest"]
            != digest(aggregate._identity())
            or not reservation["started_at"]
            <= now
            < reservation["deadline_at"]
            <= self.sources.deadline()
        ):
            raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
        # This refusal-only process fact is not persisted, an approval or a refund.
        # Set before transport even if no original completion receipt is returned.
        self.forwarded = True
        self.active_reservation = reservation
        original_guard, original_timeout = (
            self.native.guard,
            self.native.timeout_seconds,
        )
        try:
            remaining = reservation["deadline_at"] - time.time()
            if remaining <= 0:
                raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
            self.native.guard = self._transport_guard
            self.native.timeout_seconds = min(float(original_timeout), remaining)
            self._transport_guard()
            result = self.native._call(command, payload)
            # Reject a late completion even if a delegate returned after its timeout.
            self._transport_guard()
            return result  # type: ignore[no-any-return]
        except Exception:
            if time.time() >= reservation["deadline_at"]:
                raise PhaseBlocked("FINAL_ROLE_UNRESOLVED") from None
            raise
        finally:
            self.native.guard = original_guard
            self.native.timeout_seconds = original_timeout
            self.active_reservation = None


class _PreparationStore(Store):
    """A first completion cannot be recorded after its original deadline.

    Historical identical COMPLETED cache validation does not renew a model call.
    """

    _completion_deadline: int | None = None

    def _replace(self, temporary: str, path: Path) -> None:
        if self._completion_deadline is not None:
            self.assert_owned()
            if time.time() >= self._completion_deadline:
                raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
        super()._replace(temporary, path)

    def write(self, key: str, value: dict[str, Any]) -> None:
        deadline = None
        if key.startswith("reservation-") and value.get("status") == "COMPLETED":
            previous = self.read(key)
            if previous is not None and previous["status"] == "RESERVED":
                deadline = previous["deadline_at"]
        self._completion_deadline = deadline
        try:
            if deadline is not None and time.time() >= deadline:
                raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
            super().write(key, value)
        finally:
            self._completion_deadline = None


class PreparedReview:
    def __init__(self, aggregate: AggregateReview, sources: _OriginalSources) -> None:
        self.aggregate, self.sources = aggregate, sources

    def review(self, *, started_at: int, deadline_at: int) -> dict[str, Any]:
        now = int(time.time())
        if (
            type(started_at) is not int
            or type(deadline_at) is not int
            or not 0 <= started_at <= now < deadline_at <= self.sources.deadline()
            or deadline_at - started_at > self.aggregate.adapter.timeout_seconds
        ):
            raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
        source = self.aggregate.review(started_at=started_at, deadline_at=deadline_at)
        self.sources.check()
        if (
            guards.validate_final_preseed(self.sources.seed, self.sources.root)[
                "calls_consumed"
            ]
            != 1
        ):
            raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
        return source


def _configured_aggregate(
    seed: Path, root: Path, wait_scope: Scope, sources: _OriginalSources,
    store: _PreparationStore, counter: RoleCounter,
) -> AggregateReview:
    guard = store.guard
    quota = guards.quota_enabled(sources.config)
    configured = codex_role_adapter(
        policy_root=Path(sources.config["policy_root"]),
        root=root,
        policy_sha=sources.profile["policy_sha"],
        continuation_state=seed,
        quota_state=seed if quota else None,
        wait_scope=wait_scope,
    )
    if (
        type(configured) is not SettingsAdapter
        or configured.owner != sources.backend
        or configured.profile != sources.continuation
    ):
        raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
    guard()
    delegate = _ReservedDelegate(configured.delegate, counter, sources)
    adapter = SettingsAdapter(
        delegate, configured.owner, configured.profile, store.directory, guard
    )
    aggregate = AggregateReview(counter, adapter)
    delegate.aggregate = aggregate
    return aggregate


@contextmanager
def aggregate_scope(
    seed: Path, root: Path, *, wait_scope: Scope
) -> Iterator[PreparedReview]:
    """Fixed lock order: existing session supervisor, then original seed lock.

    Strict restart admission always runs before preparation/settings factories.
    The ongoing reader is never substituted for this mandatory admission.
    """
    seed = guards.secure_directory(seed, root)
    supervisor = RunnerLock.current_owned(seed / "session-control")
    with RunnerLock(seed) as ownership:
        guards.validate_final_preseed(seed, root)
        sources = _OriginalSources(seed, root, supervisor, ownership, wait_scope)
        guard = sources.check
        store = _PreparationStore(seed, root, sources.profile, guard)
        counter = RoleCounter(store, sources.config["approved_launch_digest"])
        counter.read()
        aggregate = _configured_aggregate(
            seed, root, wait_scope, sources, store, counter
        )
        yield PreparedReview(aggregate, sources)
        guard()
        # Normal completion cannot leave a zero namespace or partial reservation.
        guards.validate_final_preseed(seed, root)


def structural_child_preflight(session: Any) -> tuple[dict[str, Any], ...]:
    """Strict original FINAL_GATE entry; all children require original sources."""
    from tools.agent_epic_session_runtime import SessionRuntime

    if type(session) is not SessionRuntime:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    scope = session.wait_scope
    if type(scope) is not Scope or scope.active_stage != "FINAL:FINAL_GATE":
        raise PhaseBlocked("WAIT_FRAME_REQUIRED")
    return _structural_children_current_owned(session)


class _OwnedCompletedSources(_OriginalSources):
    """Fresh source comparison within existing locks/current installed final stage."""

    def __init__(self, session: Any) -> None:
        from tools.agent_epic_session_runtime import SessionRuntime

        if type(session) is not SessionRuntime:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        self.seed, self.root = session.state, session.root
        self.supervisor = RunnerLock.current_owned(self.seed / "session-control")
        self.ownership = RunnerLock.current_owned(self.seed)
        scope = session.wait_scope
        if (
            type(scope) is not Scope
            or scope.state != self.seed
            or scope.root.resolve() != self.root.resolve()
            or scope.read_only
            or scope.active_stage is None
            or not scope.active_stage.startswith("FINAL:")
        ):
            raise PhaseBlocked("WAIT_FRAME_REQUIRED")
        self.wait_scope = scope
        self.stage = scope.active_stage
        self.reader = scope._active_reader()
        self.stage_guard = self.reader.guard
        self.stage_deadline = self.reader.current_deadline
        current = guards.validate_final_preseed(self.seed, self.root)
        if current["calls_consumed"] != 1:
            raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
        sources = guards.final_preparation_original_sources(self.seed, self.root)
        if session.config != sources["config"]:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        self.config, self.profile = sources["config"], sources["profile"]
        self.continuation, self.backend = self._native_sources(sources)
        self.baseline = self._baseline(sources)
        self.maximum_consumed = 1
        self.session_baseline = digest(self._session_sources(sources))
        self.completed_state = current
        self.check()

    def _assert_stage(self) -> None:
        if (
            self.wait_scope.active_stage != self.stage
            or self.wait_scope._active_reader() is not self.reader
            or self.reader.guard is not self.stage_guard
            or self.reader.current_deadline > self.stage_deadline
        ):
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")

    def check(self) -> None:
        super().check()
        if guards.validate_final_preseed(self.seed, self.root) != self.completed_state:
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")


class _ReadOnlyPreparationStore(Store):
    def write(self, key: str, value: dict[str, Any]) -> None:
        raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")


class _CompletedReview(AggregateReview):
    """The shared validator with fixed existing-source reads, no adapter factory."""

    def __init__(self, counter: RoleCounter, sources: _OwnedCompletedSources) -> None:
        self.counter, self.sources = counter, sources
        self.directory = counter.store.directory / "sources"
        self.key = "review-" + digest(counter.identity)
        for path in (self.directory, counter.store.directory / "role-settings"):
            guards.secure_directory(path, counter.store.root)
            if not path.is_dir():
                raise PhaseBlocked("FINAL_REVIEW_SETTINGS_MISSING")

    def _backend_original(self) -> dict[str, Any]:
        self.sources.check()
        return self.sources.backend

    def _settings_original(self, invocation: str) -> dict[str, Any] | None:
        from tools.agent_continuation_store import Store as SourceStore

        self.sources.check()
        return SourceStore(
            self.counter.store.directory / "role-settings", self.counter.store.root
        ).load(
            "receipt-" + invocation,
            cap=self.sources.continuation["limits"]["max_record_bytes"],
        )

    def review(self, *, started_at: int, deadline_at: int) -> dict[str, Any]:
        raise PhaseBlocked("FINAL_ROLE_CALL_SPENT")

    def completed(self) -> dict[str, Any]:
        self.sources.check()
        state, reservation = self.counter.read()
        if (
            state is None
            or state["calls_consumed"] != 1
            or reservation is None
            or reservation["status"] != "COMPLETED"
        ):
            raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
        raw = self._store().load(
            self.key, cap=self.counter.store.owner["max_artifact_bytes"]
        )
        if raw is None:
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
        source, derived = self._validate(raw, self._identity())
        if derived != reservation:
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
        self.sources.check()
        return source


def read_completed_aggregate_owned(session: Any) -> dict[str, Any]:
    """Already-owned complete originals only: no lock reentry/write/gate/model."""
    sources = _OwnedCompletedSources(session)
    guards.secure_directory(session.state / "final-preparation", session.root)
    store = _ReadOnlyPreparationStore(
        session.state, session.root, sources.profile, sources.check
    )
    counter = RoleCounter(store, sources.config["approved_launch_digest"])
    reader_type = (
        _CompletedLearningReview
        if sources.config.get("schema_version") == "8.0"
        else _CompletedReview
    )
    return reader_type(counter, sources).completed()


class HistoricalPlanningUse:
    """Immutable neutral source facts, deliberately not a hint envelope."""

    __slots__ = ("_values", "_lesson_references", "_call_fact")
    _values: tuple[Any, ...]
    _lesson_references: tuple[tuple[str, str, str, str, str, str], ...]
    _call_fact: PlanningCallFact

    def __init__(
        self,
        task: int,
        role: str,
        ordinal: int,
        source_digest: str,
        request_digest: str,
        validation_digest: str,
        metadata_digest: str,
        pair_digest: str | None,
        lesson_ids: tuple[str, ...],
        negative_record_digests: tuple[str, ...],
        lesson_references: tuple[tuple[str, str, str, str, str, str], ...],
        call_fact: PlanningCallFact,
    ) -> None:
        if (
            type(lesson_references) is not tuple
            or any(
                type(row) is not tuple
                or len(row) != 6
                or any(
                    type(value) is not str
                    or len(value) != 64
                    or any(char not in "0123456789abcdef" for char in value)
                    for value in row
                )
                for row in lesson_references
            )
            or tuple(row[0] for row in lesson_references) != lesson_ids
        ):
            raise PhaseBlocked("FINAL_REVIEW_SOURCE_INVALID")
        object.__setattr__(self, "_call_fact", call_fact)
        object.__setattr__(self, "_lesson_references", lesson_references)
        object.__setattr__(
            self,
            "_values",
            (
                task,
                role,
                ordinal,
                source_digest,
                request_digest,
                validation_digest,
                metadata_digest,
                pair_digest,
                lesson_ids,
                negative_record_digests,
            ),
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("immutable historical source facts")

    def facts(self) -> tuple[Any, ...]:
        return self._values

    def lesson_references(
        self,
    ) -> tuple[tuple[str, str, str, str, str, str], ...]:
        return self._lesson_references

    def call_fact(self) -> PlanningCallFact:
        return self._call_fact


@dataclass(frozen=True, slots=True, repr=False)
class PlanningAdmissionTaskFact:
    task: int
    planner_attempts: int
    planning_review_attempts: int


@dataclass(frozen=True, slots=True, repr=False)
class _PlanningAdmissionSnapshot:
    fingerprint: str
    domain: tuple[tuple[int, str, int], ...]
    tasks: tuple[int, ...]
    pending: tuple[tuple[tuple[int, str, int], str], ...]


@dataclass(slots=True, repr=False)
class _HistoricalReadScope:
    owner: HistoricalPlanningReader
    admission: _PlanningAdmissionSnapshot | None = None
    first_used: dict[tuple[int, str, int], str] = field(default_factory=dict)
    final_seen: set[tuple[int, str, int]] = field(default_factory=set)


class HistoricalPlanningReader:
    """Fixed already-owned final source reader; conveys no selection authority."""

    def __init__(self, session: Any) -> None:
        from tools.agent_epic_session_runtime import SessionRuntime
        from tools.agent_learning_verify import Verifier

        if type(session) is not SessionRuntime:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        self.session, self.seed, self.root = session, session.state, session.root
        self._read_scope: _HistoricalReadScope | None = None
        self.seed_lock = RunnerLock.current_owned(self.seed)
        self.supervisor = RunnerLock.current_owned(self.seed / "session-control")
        scope = session.wait_scope
        if (
            type(scope) is not Scope
            or scope.state != self.seed
            or scope.root.resolve() != self.root.resolve()
            or scope.read_only
            or scope.active_stage is None
            or not scope.active_stage.startswith("FINAL:")
        ):
            raise PhaseBlocked("WAIT_FRAME_REQUIRED")
        self.scope, self.stage = scope, scope.active_stage
        self.frame_reader = scope.reader(self.stage)
        self.stage_guard = self.frame_reader.guard
        self.stage_deadline = self.frame_reader.current_deadline
        self.reader = self._source_reader(session)
        if session.config != self.reader.config:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        self.verifier = Verifier(self.reader, session)
        self.session_baseline = digest(self._session_sources())
        self._guard()
        self._retained()

    def _source_reader(self, session: Any) -> Any:
        from tools.agent_learning_evidence import SourceReader

        return SourceReader(self.seed, self.root, read=session._github_read)

    def _session_sources(self) -> dict[str, Any]:
        return _session_final_sources(
            self.seed,
            self.root,
            {
                "config": self.reader.config,
                "mandate": self.reader.mandate,
            },
        )

    def _guard(self) -> None:
        if (
            RunnerLock.current_owned(self.seed) is not self.seed_lock
            or RunnerLock.current_owned(self.seed / "session-control")
            is not self.supervisor
            or self.scope.active_stage != self.stage
            or self.scope.reader(self.stage) is not self.frame_reader
            or self.frame_reader.guard is not self.stage_guard
            or self.frame_reader.current_deadline > self.stage_deadline
        ):
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        self.stage_guard()
        self.reader.guard()
        if (
            self.session.config != self.reader.config
            or digest(self._session_sources()) != self.session_baseline
        ):
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")

    def _retained(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        from tools.agent_learning_planning_promotion import retained

        self._guard()
        # Both original negative barriers are mandatory, including in recursion.
        return self.verifier.retained(), retained(self.reader)

    def domain(self) -> tuple[tuple[int, str, int], ...]:
        return self._domain_with_task_facts()[0].domain

    def _domain_with_task_facts(
        self,
    ) -> tuple[_PlanningAdmissionSnapshot, tuple[PlanningAdmissionTaskFact, ...]]:
        admission, task_facts = self._admission_domain_with_task_facts()
        self._namespace_inventory(
            admission.domain, admission.tasks, dict(admission.pending)
        )
        self._guard()
        return admission, task_facts

    def _admission_domain(self) -> _PlanningAdmissionSnapshot:
        return self._admission_domain_with_task_facts()[0]

    def _admission_domain_with_task_facts(
        self,
    ) -> tuple[_PlanningAdmissionSnapshot, tuple[PlanningAdmissionTaskFact, ...]]:
        from tools import agent_epic_planning as admission
        from tools import agent_learning as learning

        self._guard()
        path = self.seed / "planning"
        if not path.exists() and not path.is_symlink():
            return (
                _PlanningAdmissionSnapshot(digest({"planning": "absent"}), (), (), ()),
                (),
            )
        guards.secure_directory(path, self.root)
        entries = list(
            islice(path.iterdir(), self.reader.owner["limits"]["max_records"] + 1)
        )
        if len(entries) > self.reader.owner["limits"]["max_records"]:
            raise learning.LearningError("LEARNING_STORAGE_EXHAUSTED")
        owner = admission.profile(
            self.reader._json(self.seed / "planning-profile.json"),
            approved=self.reader.config["approved_planning_digest"],
            mandate=self.reader.mandate,
        )
        result: list[tuple[int, str, int]] = []
        tasks: list[int] = []
        task_facts: list[PlanningAdmissionTaskFact] = []
        pending_failures: dict[tuple[int, str, int], str] = {}
        # Ordered full-object commitment, without retaining raw admission copies.
        admission_chain = digest({"admissions": []})
        for directory in sorted(entries):
            if (
                not directory.name.isascii()
                or not directory.name.isdecimal()
                or str(int(directory.name)) != directory.name
                or int(directory.name) not in self.reader._source_tasks()
                or directory.is_symlink()
                or not directory.is_dir()
                or directory.name not in owner["tasks"]
            ):
                raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
            task = int(directory.name)
            tasks.append(task)
            source = admission.Admission(self.seed, self.root)._storage(task)
            original = self.reader._json(source / "admission.json")
            admission.exact(original, admission.RECORD_KEYS)
            for key, value in {
                "task": task,
                "repository": self.reader.owner["repository"],
                "epic": self.reader.owner["epic"],
                "mandate_digest": self.reader.owner["mandate_digest"],
                "policy_sha": self.reader.owner["policy_sha"],
                "planning_digest": guards.canonical_digest(owner),
            }.items():
                if type(original[key]) is not type(value) or original[key] != value:
                    raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
            admission.exact(original["attempts"], {"planner", "review"})
            validated_counts: dict[str, int] = {}
            for short, logical, limit in (
                ("planner", "planner", "max_planner_attempts"),
                ("review", "planning_review", "max_review_attempts"),
            ):
                count = learning.integer(
                    original["attempts"][short], min(3, owner["limits"][limit])
                )
                validated_counts[logical] = count
                result.extend(
                    (task, logical, ordinal) for ordinal in range(1, count + 1)
                )
            if original["pending"] is not None:
                pending = learning.exact(
                    original["pending"], {"role", "attempt", "request_digest"}
                )
                short_role = pending["role"]
                if (
                    original["status"] != "RESERVED"
                    or type(short_role) is not str
                    or short_role not in {"planner", "review"}
                ):
                    raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
                ordinal = learning.integer(
                    pending["attempt"], original["attempts"][short_role], 1
                )
                if ordinal != original["attempts"][short_role]:
                    raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
                request_digest = pending["request_digest"]
                if not isinstance(request_digest, str) or not request_digest.startswith(
                    "sha256:"
                ):
                    raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
                learning.hash_value(request_digest.removeprefix("sha256:"))
                logical_role = (
                    "planner" if short_role == "planner" else "planning_review"
                )
                # This is only an obligation to verify the exact original FAIL
                # below, never completion, admission or current retry authority.
                pending_failures[(task, logical_role, ordinal)] = request_digest
            task_facts.append(
                PlanningAdmissionTaskFact(
                    task,
                    validated_counts["planner"],
                    validated_counts["planning_review"],
                )
            )
            admission_chain = digest(
                {"previous": admission_chain, "task": task, "admission": original}
            )
        if len(result) > self.reader.owner["limits"]["max_records"]:
            raise learning.LearningError("LEARNING_STORAGE_EXHAUSTED")
        self._guard()
        domain = tuple(sorted(result))
        ordered_tasks = tuple(tasks)
        pending_entries = tuple(sorted(pending_failures.items()))
        fingerprint = digest(
            {
                "planning": "present",
                "profile": owner,
                "admissions": admission_chain,
                "tasks": ordered_tasks,
                "domain": domain,
                "pending": pending_entries,
            }
        )
        return (
            _PlanningAdmissionSnapshot(
                fingerprint, domain, ordered_tasks, pending_entries
            ),
            tuple(task_facts),
        )

    def _owned_read_scope(self) -> _HistoricalReadScope:
        from tools import agent_learning as learning

        scope = self._read_scope
        if (
            type(scope) is not _HistoricalReadScope
            or scope.owner is not self
            or type(scope.admission) is not _PlanningAdmissionSnapshot
        ):
            raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
        return scope

    def _read_domain(self) -> tuple[tuple[int, str, int], ...]:
        from tools import agent_learning as learning

        scope = self._owned_read_scope()
        current = self._admission_domain()
        if current != scope.admission:
            raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
        self._guard()
        return current.domain

    @staticmethod
    def _original_commitment(
        result: dict[str, Any],
        envelope: dict[str, Any],
        relevance: dict[str, Any] | None,
        normalized: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        return digest(
            {
                "result": result,
                "envelope": envelope,
                "relevance": relevance,
                "normalized": normalized,
                "metadata": metadata,
            }
        )

    def _used_commitment(self, key: tuple[int, str, int], value: str) -> None:
        from tools import agent_learning as learning

        scope = self._owned_read_scope()
        if key in scope.first_used:
            if scope.first_used[key] != value:
                raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
        else:
            if len(scope.first_used) >= self.reader.owner["limits"]["max_records"]:
                raise learning.LearningError("LEARNING_STORAGE_EXHAUSTED")
            scope.first_used[key] = value

    def _inventory_commitment(self, key: tuple[int, str, int], value: str) -> None:
        from tools import agent_learning as learning

        # Standalone domain() retains its original full inventory semantics.
        scope = self._read_scope
        if scope is None:
            return
        scope = self._owned_read_scope()
        if key in scope.first_used and scope.first_used[key] == value:
            if key not in scope.final_seen:
                if len(scope.final_seen) >= self.reader.owner["limits"]["max_records"]:
                    raise learning.LearningError("LEARNING_STORAGE_EXHAUSTED")
                scope.final_seen.add(key)

    def _namespace_names(self, path: Path, allowed: set[str], *, lock: bool) -> None:
        from tools import agent_learning as learning

        self._guard()
        if not path.exists() and not path.is_symlink():
            if allowed:
                raise learning.LearningError("LEARNING_SOURCE_MISSING")
            return
        guards.secure_directory(path, self.root)
        permitted = allowed | ({"epic-loop.lock"} if lock else set())
        seen: set[str] = set()
        # +1 detects overflow without consuming/materializing the remainder.
        for entry in islice(path.iterdir(), len(permitted) + 1):
            self._guard()
            if len(seen) >= len(permitted):
                raise learning.LearningError("LEARNING_STORAGE_EXHAUSTED")
            if entry.name not in permitted or entry.name in seen:
                raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
            info = entry.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != guards.owner_uid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise learning.LearningError("LEARNING_SOURCE_INVALID")
            if entry.name != "epic-loop.lock":
                self.reader._json(entry)
            seen.add(entry.name)
            self._guard()
        if seen - ({"epic-loop.lock"} if lock else set()) != allowed:
            raise learning.LearningError("LEARNING_SOURCE_MISSING")
        self._guard()

    def _namespace_inventory(
        self,
        domain: tuple[tuple[int, str, int], ...],
        tasks: tuple[int, ...],
        pending_failures: dict[tuple[int, str, int], str],
    ) -> None:
        from tools import agent_learning as learning
        from tools import agent_learning_planning_sources as sources

        verified_failures: set[tuple[int, str, int]] = set()
        for task in tasks:
            self._guard()
            validation: set[str] = set()
            invocations: set[str] = set()
            for original_task, role, ordinal in domain:
                if original_task != task:
                    continue
                self._guard()
                original, envelope, relevance, normalized, metadata = sources._original(
                    self.reader, task, role, ordinal
                )
                commitment = self._original_commitment(
                    original, envelope, relevance, normalized, metadata
                )
                failure_key = (task, role, ordinal)
                failure_digest = pending_failures.get(failure_key)
                if failure_digest is not None:
                    # Fixed full original response/validation/identity/replay
                    # proof; a missing or partial outcome never reaches here.
                    if (
                        original["validation"]["outcome"] != "FAIL"
                        or guards.canonical_digest(original["request"])
                        != failure_digest
                    ):
                        raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
                    verified_failures.add(failure_key)
                validation.update(
                    f"{role}-{ordinal}-{suffix}.json"
                    for suffix in (
                        "request",
                        "response",
                        "context",
                        "validation",
                        "workspace",
                    )
                )
                key = metadata["invocation_key"]
                invocations.update({key + ".json", key + ".problem.json"})
                if original["request"]["untrusted_verified_hints"]["hints"]:
                    invocations.add(key + ".relevance.json")
                self._guard()
                self._inventory_commitment(failure_key, commitment)
            base = self.seed / "planning" / str(task)
            self._namespace_names(base / "learning-validation", validation, lock=False)
            self._namespace_names(base / "learning-invocations", invocations, lock=True)
            self._guard()
        if verified_failures != set(pending_failures):
            raise learning.LearningError("LEARNING_SOURCE_MISMATCH")

    def _pair(self, proof: dict[str, Any]) -> dict[str, Any] | None:
        from tools import agent_learning_planning_promotion as promotion

        inputs = promotion._pair_inputs(
            self.reader,
            self.session,
            proof["failure"],
            proof["corrected"],
            proof["rule"],
        )
        if inputs is None:
            return None
        before, after, role = inputs
        first, _, _, _ = self._read_original(
            before["identity"]["task"], role, before["ordinal"]
        )
        last, _, _, _ = self._read_original(
            after["identity"]["task"], role, after["ordinal"]
        )
        return promotion._pair_complete(
            self.reader,
            self.session,
            before,
            after,
            proof["rule"],
            first,
            last,
        )

    def _read_original(
        self,
        task: int,
        role: str,
        ordinal: int,
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        str | None,
        tuple[tuple[str, str, str, str, str, str], ...],
    ]:
        return self._read_original_with_call_fact(task, role, ordinal)[0]

    def _read_original_with_call_fact(
        self,
        task: int,
        role: str,
        ordinal: int,
    ) -> tuple[
        tuple[
            dict[str, Any],
            dict[str, Any],
            str | None,
            tuple[tuple[str, str, str, str, str, str], ...],
        ],
        PlanningCallFact,
    ]:
        from tools import agent_learning as learning
        from tools import agent_learning_planning_history as history
        from tools import agent_learning_planning_selection as selection
        from tools import agent_learning_planning_sources as sources
        from tools.agent_learning_planning_predicates import current_equivalent

        self._guard()
        if (
            type(task) is not int
            or type(ordinal) is not int
            or (task, role, ordinal) not in self._read_domain()
        ):
            raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
        stack = self.reader.planning_replay_stack
        key = (task, role, ordinal)
        if key in stack or len(stack) >= self.reader.owner["limits"]["max_records"]:
            raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
        stack.add(key)
        try:
            self._retained()
            original_five, call_fact = sources._original_with_call_fact(
                self.reader,
                task,
                role,
                ordinal,
            )
            result, envelope, relevance, normalized, metadata = original_five
            commitment = self._original_commitment(
                result, envelope, relevance, normalized, metadata
            )
            paired_digest = None
            references: tuple[tuple[str, str, str, str, str, str], ...] = ()
            if relevance is not None:
                _, items = self._retained()
                proof = history._original_proof(
                    self.reader, role, envelope, relevance, items
                )
                if (
                    proof["rule"] not in self.reader.planning_catalog["rules"]
                    or proof["verified"]["catalog_digest"]
                    != self.reader.owner["catalog_digest"]
                ):
                    raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
                candidate = selection._original_records(proof, items)
                paired = self._pair(proof)
                selection._original_links(proof, candidate, paired)
                corrected, _, _, _ = self._read_original(
                    proof["corrected"]["identity"]["task"],
                    role,
                    proof["corrected"]["ordinal"],
                )
                if not current_equivalent(normalized, corrected["facts"]):
                    raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
                paired_digest = digest(paired)
                references = (
                    (
                        proof["verified"]["lesson_id"],
                        digest(proof["verified"]),
                        digest(candidate),
                        digest(proof["failure"]),
                        digest(proof["corrected"]),
                        paired_digest,
                    ),
                )
            if tuple(row[0] for row in references) != tuple(metadata["lesson_ids"]):
                raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
            self._retained()
            self._guard()
            self._used_commitment(key, commitment)
            return (result, metadata, paired_digest, references), call_fact
        finally:
            stack.remove(key)

    def read(self, task: int, role: str, ordinal: int) -> HistoricalPlanningUse:
        from tools import agent_learning as learning

        self._guard()
        if type(task) is not int or type(ordinal) is not int:
            raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
        if self._read_scope is not None:
            raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
        scope = _HistoricalReadScope(self)
        self._read_scope = scope
        try:
            entry = self._admission_domain()
            scope.admission = entry
            self._namespace_inventory(entry.domain, entry.tasks, dict(entry.pending))
            self._guard()
            (original, metadata, paired, references), call_fact = (
                self._read_original_with_call_fact(task, role, ordinal)
            )
            workflow_items, planning_items = self._retained()
            # Whole original admission domain includes zero-attempt namespaces.
            final = self._admission_domain()
            if final != scope.admission:
                raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
            scope.final_seen.clear()
            self._namespace_inventory(final.domain, final.tasks, dict(final.pending))
            self._guard()
            if len(scope.final_seen) != len(scope.first_used) or any(
                key not in scope.final_seen for key in scope.first_used
            ):
                raise learning.LearningError("LEARNING_SOURCE_MISMATCH")
            negatives = tuple(
                sorted(
                    digest(item["record"])
                    for item in workflow_items + planning_items
                    if item["kind"] in {"evaluation", "tombstone"}
                )
            )
            self._retained()
            self._guard()
            return HistoricalPlanningUse(
                task,
                role,
                ordinal,
                digest(original),
                digest(original["request"]),
                digest(original["validation"]),
                digest(metadata),
                paired,
                tuple(metadata["lesson_ids"]),
                negatives,
                references,
                call_fact,
            )
        finally:
            scope.first_used.clear()
            scope.final_seen.clear()
            scope.admission = None
            self._read_scope = None


def read_completed_environment_owned(session: Any) -> dict[str, Any]:
    """Original aggregate launcher fact; never observe the resume interpreter."""
    sources = _OwnedCompletedSources(session)
    guards.secure_directory(session.state / "final-preparation", session.root)
    store = _ReadOnlyPreparationStore(
        session.state, session.root, sources.profile, sources.check
    )
    counter = RoleCounter(store, sources.config["approved_launch_digest"])
    reader_type = (
        _CompletedLearningReview
        if sources.config.get("schema_version") == "8.0"
        else _CompletedReview
    )
    reader = reader_type(counter, sources)
    source = reader.completed()
    value = reader._environment(source, reader._identity())
    sources.check()
    return value


class _CurrentFinalSources:
    """Pin existing ownership, context and routing without provisioning a wait."""

    def __init__(self, session: Any) -> None:
        from tools.agent_epic_session_runtime import SessionRuntime

        if type(session) is not SessionRuntime:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        self.session = session
        self.supervisor = RunnerLock.current_owned(session.state / "session-control")
        self.owner = RunnerLock.current_owned(session.state)
        scope = session.wait_scope
        if (
            type(scope) is not Scope
            or scope.state != session.state
            or scope.root.resolve() != session.root.resolve()
            or scope.read_only
            or scope.config != session.config
            or scope.active_stage is None
            or not scope.active_stage.startswith("FINAL:")
        ):
            raise PhaseBlocked("WAIT_FRAME_REQUIRED")
        self.scope = scope
        self.stage = scope.active_stage
        self.reader = scope._active_reader()
        self.guard = self.reader.guard
        self.deadline = self.reader.current_deadline
        self.check()

    def check(self) -> None:
        session = self.session
        if (
            session.wait_scope is not self.scope
            or RunnerLock.current_owned(session.state / "session-control")
            is not self.supervisor
            or RunnerLock.current_owned(session.state) is not self.owner
            or self.scope.active_stage != self.stage
            or self.scope._active_reader() is not self.reader
            or self.reader.guard is not self.guard
            or self.reader.current_deadline > self.deadline
            or (session._read_stage or session._current_stage()) != self.stage
            or self.scope.config != session.config
        ):
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        self.guard()



class _HistoricalStructuralSourceReader(SourceReader):
    def __init__(self, session: Any) -> None:
        self._historical_session = session
        super().__init__(session.state, session.root, read=self._historical_read)
        from tools.agent_final_preparation_sources import _register_historical_reader

        _register_historical_reader(session, self)

    def _historical_read(self, argv: Sequence[str]) -> str:
        return cast(str, self._historical_session._historical_read(self, argv))

    def _source_tasks(self) -> tuple[int, ...]:
        from tools.agent_final_learning_domain import _final_domain_value

        return _final_domain_value(self)[0]


def _current_children_owned(session: Any) -> tuple[int, ...]:
    pinned = _CurrentFinalSources(session)

    reader = SourceReader(session.state, session.root, read=session._github_read)

    def authority() -> None:
        pinned.check()
        seed = CheckpointStore(session.state, session.root).load()
        cursor = workflow.StateStore(session.state / "session-control").load("cursor")
        if seed is None or cursor is None:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        Session._validate_cursor(cursor, seed)
        if cursor["mode"] != "FINAL":
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        session._fresh_authority(
            session.config["task_iterations"] + len(cursor["completed_tasks"]),
            at_task_start=False,
        )

    def children() -> tuple[int, ...]:
        authority()
        epic = session.issues.issue(reader.owner["repository"], reader.owner["epic"])
        authority()
        connection = epic.get("subIssues")
        nodes = connection.get("nodes") if isinstance(connection, dict) else None
        if (
            epic.get("number") != reader.owner["epic"]
            or not isinstance(connection, dict)
            or not isinstance(nodes, list)
        ):
            raise PhaseBlocked("ISSUE_MISMATCH")
        if (
            type(connection.get("totalCount")) is not int
            or connection["totalCount"] != len(nodes)
            or (
                connection.get("pageInfo") is not None
                and (
                    not isinstance(connection["pageInfo"], dict)
                    or connection["pageInfo"].get("hasNextPage") is not False
                )
            )
        ):
            raise PhaseBlocked("ISSUE_MISMATCH")
        if len(nodes) > reader.owner["limits"]["max_records"]:
            raise PhaseBlocked("FINAL_STAGE_GATE_INVALID")
        numbers: set[int] = set()
        for node in nodes:
            number = node.get("number") if isinstance(node, dict) else None
            if type(number) is not int or number <= 0 or number in numbers:
                raise PhaseBlocked("ISSUE_MISMATCH")
            numbers.add(number)
            authority()
            child = session.issues.issue(reader.owner["repository"], number)
            authority()
            parent = child.get("parent")
            if (
                child.get("number") != number
                or not isinstance(parent, dict)
                or parent.get("number") != reader.owner["epic"]
                or child.get("state") not in {"OPEN", "CLOSED"}
            ):
                raise PhaseBlocked("ISSUE_MISMATCH")
            if child["state"] == "OPEN":
                raise PhaseBlocked("OPEN_REQUIRED_TASKS")
        return tuple(sorted(numbers))

    return children()


def _structural_children_current_owned(session: Any) -> tuple[dict[str, Any], ...]:
    """Read Stage A in the genuine current FINAL context, without a new wait."""
    from dataclasses import asdict


    pinned = _CurrentFinalSources(session)

    def authority() -> None:
        pinned.check()
        seed = CheckpointStore(session.state, session.root).load()
        cursor = workflow.StateStore(session.state / "session-control").load("cursor")
        if seed is None or cursor is None:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        Session._validate_cursor(cursor, seed)
        if cursor["mode"] != "FINAL":
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        session._fresh_authority(
            session.config["task_iterations"] + len(cursor["completed_tasks"]),
            at_task_start=False,
        )

    authority()
    reader = (
        _HistoricalStructuralSourceReader(session)
        if session.config.get("schema_version") == "8.0"
        else SourceReader(session.state, session.root, read=session._github_read)
    )
    from tools.agent_epic_queue import GhClient

    issues = (
        GhClient(read=reader._historical_read)
        if type(reader) is _HistoricalStructuralSourceReader else session.issues
    )

    def children() -> tuple[int, ...]:
        authority()
        epic = issues.issue(reader.owner["repository"], reader.owner["epic"])
        authority()
        connection = epic.get("subIssues")
        nodes = connection.get("nodes") if isinstance(connection, dict) else None
        if (
            epic.get("number") != reader.owner["epic"]
            or not isinstance(connection, dict)
            or not isinstance(nodes, list)
        ):
            raise PhaseBlocked("ISSUE_MISMATCH")
        if (
            type(connection.get("totalCount")) is not int
            or connection["totalCount"] != len(nodes)
            or (
                connection.get("pageInfo") is not None
                and (
                    not isinstance(connection["pageInfo"], dict)
                    or connection["pageInfo"].get("hasNextPage") is not False
                )
            )
        ):
            raise PhaseBlocked("ISSUE_MISMATCH")
        if len(nodes) > reader.owner["limits"]["max_records"]:
            raise PhaseBlocked("FINAL_STAGE_GATE_INVALID")
        numbers: set[int] = set()
        for node in nodes:
            number = node.get("number") if isinstance(node, dict) else None
            if type(number) is not int or number <= 0 or number in numbers:
                raise PhaseBlocked("ISSUE_MISMATCH")
            numbers.add(number)
            authority()
            child = issues.issue(reader.owner["repository"], number)
            authority()
            parent = child.get("parent")
            if (
                child.get("number") != number
                or not isinstance(parent, dict)
                or parent.get("number") != reader.owner["epic"]
                or child.get("state") not in {"OPEN", "CLOSED"}
            ):
                raise PhaseBlocked("ISSUE_MISMATCH")
            if child["state"] == "OPEN":
                raise PhaseBlocked("OPEN_REQUIRED_TASKS")
        return tuple(sorted(numbers))

    numbers = children()
    result: list[dict[str, Any]] = []
    for number in numbers:
        authority()
        reader.guard()
        directory = reader.task_directory(number)
        with reader.ownership(directory):
            saved = CheckpointStore(directory, session.root).load()
            if (
                saved is None
                or saved["task"] != number
                or saved["phase"] != "NEXT_TASK"
                or saved["status"] != "TASK_DONE"
                or saved["pending_phase"] is not None
            ):
                raise PhaseBlocked("CHECKPOINT_INVALID")
            authority()
            fact = reader.workflow(number)
            authority()
            if fact.state["status"] != "PASS":
                raise PhaseBlocked("FINAL_STAGE_GATE_INVALID")
            delivery_source = reader.delivery(number)
            authority()
            pr_ci = reader.ci(number, "pr")
            authority()
            post_merge_ci = reader.ci(number, "post-merge")
            authority()
            demo = reader.demo(number, session)
            authority()
            item = {
                "task": number,
                "checkpoint_digest": digest(saved),
                "workflow_digest": digest(
                    {
                        "contract": asdict(fact.contract),
                        "state": fact.state,
                        "memory": fact.memory,
                    }
                ),
                "delivery": delivery_source,
                "pr_ci": pr_ci,
                "post_merge_ci": post_merge_ci,
                "demo": demo,
            }
            if CheckpointStore(directory, session.root).load() != saved:
                raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        authority()
        result.append(item)
    if children() != numbers:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    reader.guard()
    authority()
    return tuple(result)



def read_current_final_source_digests_owned(session: Any) -> dict[str, str]:
    """Five ephemeral projections only; no assessment or learning disposition.

    Repeated original reads reject observed drift; they are not an atomic snapshot.
    """
    pinned = _CurrentFinalSources(session)
    # The completed source guard additionally pins original configuration,
    # reservation/native/settings/cache sources for this whole composition.
    completed = _OwnedCompletedSources(session)

    def check() -> None:
        pinned.check()
        completed.check()
        pinned.check()

    check()
    aggregate = read_completed_aggregate_owned(session)
    check()
    environment = read_completed_environment_owned(session)
    check()
    children = _structural_children_current_owned(session)
    check()
    if environment["review_source_digest"] != digest(aggregate):
        raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
    # Re-admit each complete family, including the full child source chain.
    if read_completed_aggregate_owned(session) != aggregate:
        raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
    check()
    if read_completed_environment_owned(session) != environment:
        raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
    check()
    if _structural_children_current_owned(session) != children:
        raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
    check()
    evidence = aggregate["request"]["gate_evidence"]
    result = {
        "snapshot_digest": digest(evidence["snapshot"]),
        "local_gate_digest": digest(
            {"gate_evidence": evidence, "environment": environment}
        ),
        "review_digest": digest(aggregate),
        "children_digest": digest(
            {
                "repository": completed.wait_scope.mandate["repository"],
                "epic": completed.wait_scope.mandate["epic"],
                "children": [row["task"] for row in children],
            }
        ),
        "task_delivery_digest": digest(list(children)),
    }
    check()
    return result


class _CompletedLearningReview(_CompletedReview, _LearningAggregateReview):
    """Original read-only completed validator with retained request variant."""


def _completed_learning_review(
    session: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from tools.agent_final_learning_collector import read_learning_packet_owned

    sources = _OwnedCompletedSources(session)
    if sources.config.get("schema_version") != "8.0":
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    guards.secure_directory(session.state / "final-preparation", session.root)
    store = _ReadOnlyPreparationStore(
        session.state, session.root, sources.profile, sources.check
    )
    counter = RoleCounter(store, sources.config["approved_launch_digest"])
    source = _CompletedLearningReview(counter, sources).completed()
    commitment = learning_request_digest_fact(source["request"])
    # Only after native/request/input/settings/environment validation succeeds.
    packet = read_learning_packet_owned(session)
    if packet["source_digest"] != commitment:
        raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
    sources.check()
    return source, packet


def read_completed_learning_review_owned(session: Any) -> dict[str, Any]:
    """Fixed completed/restart read, no adapter factory, writes or second call."""
    source, _ = _completed_learning_review(session)
    return source


def review_learning_owned(
    session: Any, *, started_at: int, deadline_at: int,
) -> dict[str, Any]:
    """Collect before factories; close continuous zero reader before reserve."""
    from tools.agent_epic_session_runtime import SessionRuntime
    from tools.agent_final_learning_collector import _collect
    from tools.agent_final_learning_domain import _PreAggregatePlanningReader

    if type(session) is not SessionRuntime:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    if session.config.get("schema_version") != "8.0":
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    scope = session.wait_scope
    if type(scope) is not Scope or scope.active_stage != "FINAL:FINAL_GATE":
        raise PhaseBlocked("WAIT_FRAME_REQUIRED")
    seed, root = session.state, session.root
    seed = guards.secure_directory(seed, root)
    supervisor = RunnerLock.current_owned(seed / "session-control")
    with RunnerLock(seed) as ownership:
        guards.validate_final_preseed(seed, root)
        sources = _OriginalSources(seed, root, supervisor, ownership, scope)
        if session.config != sources.config:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        from tools.agent_final_preparation_sources import _aggregate_capture_segment

        with _aggregate_capture_segment(session):
            historical = _PreAggregatePlanningReader(session)
            try:
                packet = _collect(historical, session)
                store = _PreparationStore(seed, root, sources.profile, sources.check)
                counter = RoleCounter(store, sources.config["approved_launch_digest"])
                state, _ = counter.read()
                if state is not None and state["calls_consumed"]:
                    raise PhaseBlocked("FINAL_ROLE_CALL_SPENT")
                original = _configured_aggregate(
                    seed, root, scope, sources, store, counter
                )
                aggregate = _LearningAggregateReview(counter, original.adapter)
                # Both objects share original counter/adapter; no Settings factory,
                # source constructor or transport is repeated here.
                aggregate.adapter.delegate.aggregate = aggregate
                aggregate._learning_source_digest = packet["source_digest"]
                identity = aggregate._identity()
                prepared = aggregate._prepare_new_review(identity)
                historical._guard()
                sources.check()
            finally:
                historical.reader.close()
            # No preaggregate guard after this boundary: reserve changes zero state.
            source = aggregate._dispatch_review(
                identity, prepared, started_at=started_at, deadline_at=deadline_at
            )
            authenticated, fresh = _completed_learning_review(session)
            if authenticated != source or fresh != packet:
                raise PhaseBlocked("FINAL_REVIEW_SOURCE_DRIFT")
            sources.check()
            return source
