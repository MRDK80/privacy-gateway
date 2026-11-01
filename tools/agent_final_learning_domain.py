"""Fixed lexical FINAL planning coverage; no candidate authority returned."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, cast

from tools import agent_epic_planning as admission
from tools import agent_epic_runtime_cli as guards
from tools import agent_learning as learning
from tools.agent_continuation import digest
from tools.agent_epic_loop import CheckpointStore, PhaseBlocked, RunnerLock
from tools.agent_final_learning_planning_coverage import (
    PlanningCoverage,
    _planning_coverage,
)
from tools.agent_final_preparation_runtime import (
    HistoricalPlanningReader,
    HistoricalPlanningUse,
    _CurrentFinalSources,
    _OriginalSources,
    _OwnedCompletedSources,
)
from tools.agent_learning_evidence import SourceReader


def _final_domain_value(reader: SourceReader) -> tuple[tuple[int, ...], str]:
    seed = CheckpointStore(reader.seed, reader.root).load()
    if seed is None:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    owner = admission.profile(
        reader._json(reader.seed / "planning-profile.json"),
        approved=reader.config["approved_planning_digest"],
        mandate=reader.mandate,
    )
    order = tuple(reader.config["approved_order"])
    followups = tuple(
        sorted(int(key) for key in owner["tasks"] if int(key) not in order)
    )
    if (
        reader.config["follow_up_issues"] + len(followups)
        > reader.mandate["limits"]["max_follow_up_issues"]
    ):
        raise PhaseBlocked("MANDATE_FOLLOW_UP_LIMIT")
    tasks = tuple(sorted({seed["task"], *order, *followups}))
    for task in tasks:
        learning.integer(task, 2147483647, 1)
        if str(task) not in reader.mandate["task_grants"]:
            raise PhaseBlocked("PLANNING_TASK_NOT_APPROVED")
    return tasks, digest(
        {
            "seed": seed,
            "config": reader.config,
            "mandate": reader.mandate,
            "owner": owner,
        }
    )

class _FinalSourceReader(SourceReader):
    _packet_policy_active: dict[str, Any] | None
    _packet_policy_used: bool

    def __init__(self, session: Any) -> None:
        self._pins = _CurrentFinalSources(session)
        self._completed = _OwnedCompletedSources(session)
        self._session = session
        self._alive = True
        self._historical_session = session
        read = (
            self._historical_read if session.config.get("schema_version") == "8.0"
            else session._github_read
        )
        super().__init__(session.state, session.root, read=read)
        self._domain_baseline = self._derive_domain()
        self._source_tasks()
        if session.config.get("schema_version") == "8.0":
            from tools.agent_final_preparation_sources import (
                _register_historical_reader,
            )

            _register_historical_reader(session, self)

    def _verify_policy_source(self, policy_sha: str) -> None:
        if type(self) is _FinalSourceReader and getattr(
            self, "_packet_policy_active", None
        ) is not None:
            self._packet_policy_check(policy_sha)
            return
        SourceReader._verify_policy_source(self, policy_sha)

    def _packet_policy_check(self, policy_sha: str) -> None:
        bound = self._packet_policy_active
        if bound is None:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        session = bound["session"]
        scope = session.wait_scope
        if scope is not bound["scope"]:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        stage = scope.active_stage
        reader = scope._readers.get(stage)
        if (
            type(self) is not _FinalSourceReader or self._alive is not True
            or self._session is not session or self._historical_session is not session
            or scope is not bound["scope"] or stage != bound["stage"]
            or reader is not bound["reader"] or reader.guard is not bound["guard"]
            or self.config.get("schema_version") != "8.0"
            or session.config != self.config or digest(self.config) != bound["config"]
            or self.root.resolve() != bound["root"]
            or session.root.resolve() != bound["root"]
            or self.policy.resolve() != bound["policy"]
            or type(policy_sha) is not str or policy_sha != bound["sha"]
        ):
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")

    @contextmanager
    def _packet_policy_proof(self, session: Any) -> Iterator[None]:
        from tools.agent_epic_session_runtime import SessionRuntime

        if (
            type(session) is not SessionRuntime
            or type(self) is not _FinalSourceReader or self._session is not session
            or self._historical_session is not session or self._alive is not True
            or self.config.get("schema_version") != "8.0"
            or getattr(self, "_packet_policy_used", False)
            or getattr(self, "_packet_policy_active", None) is not None
        ):
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        self._packet_policy_used = True
        self._packet_policy_active = None
        scope = session.wait_scope
        if scope is None:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        stage = scope.active_stage
        if stage is None or not stage.startswith("FINAL:"):
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        reader = scope._readers.get(stage)
        if reader is None:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        policy_sha = self.mandate["policy_sha"]
        SourceReader._verify_policy_source(self, policy_sha)
        self._packet_policy_active = {
            "session": session, "scope": scope, "stage": stage, "reader": reader,
            "guard": reader.guard, "config": digest(self.config),
            "root": self.root.resolve(), "policy": self.policy.resolve(),
            "sha": policy_sha,
        }
        primary = None
        try:
            self._packet_policy_check(policy_sha)
            yield
        except BaseException as error:
            primary = error
            raise
        finally:
            secondary = None
            try:
                self._packet_policy_check(self.mandate["policy_sha"])
            except BaseException as error:
                secondary = error
            try:
                SourceReader._verify_policy_source(self, policy_sha)
            except BaseException as error:
                if secondary is None:
                    secondary = error
            finally:
                self._packet_policy_active = None
            if primary is None and secondary is not None:
                raise secondary

    def _historical_read(self, argv: Sequence[str]) -> str:
        if getattr(self, "_packet_policy_active", None) is not None:
            policy_sha = self.mandate["policy_sha"]
            self._packet_policy_check(policy_sha)
            SourceReader._verify_policy_source(self, policy_sha)
        return cast(str, self._historical_session._historical_read(self, argv))


    def _derive_domain(self) -> tuple[tuple[int, ...], str]:
        self._pins.check()
        self._completed.check()
        self.guard()
        return _final_domain_value(self)
    def _source_tasks(self) -> tuple[int, ...]:
        if not self._alive:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        current = self._derive_domain()
        if current != self._domain_baseline:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        return current[0]

    def close(self) -> None:
        self._alive = False


class _CurrentFinalSourceReader(_FinalSourceReader):
    """Fixed CURRENT owner-CI factory; all original local/domain guards remain."""

    def __init__(self, session: Any) -> None:
        self._pins = _CurrentFinalSources(session)
        self._completed = _OwnedCompletedSources(session)
        self._session = session
        self._alive = True
        SourceReader.__init__(
            self, session.state, session.root, read=session._github_read,
        )
        self._domain_baseline = self._derive_domain()
        self._source_tasks()


class _FinalPlanningReader(HistoricalPlanningReader):
    def __init__(self, session: Any) -> None:
        try:
            super().__init__(session)
        except BaseException:
            reader = getattr(self, "reader", None)
            if type(reader) is _FinalSourceReader:
                reader.close()
            raise

    def _source_reader(self, session: Any) -> _FinalSourceReader:
        reader = _FinalSourceReader(session)
        try:
            # Missing U roots are not no-calls, even though legacy retained skips.
            for task in reader._source_tasks():
                reader.task_directory(task)
            return reader
        except BaseException:
            reader.close()
            raise


def _read_planning_coverage(
    reader: _FinalPlanningReader | _PreAggregatePlanningReader, session: Any
) -> PlanningCoverage:
    if type(reader) not in {_FinalPlanningReader, _PreAggregatePlanningReader}:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    if reader.session is not session:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    entry, admissions = reader._domain_with_task_facts()
    domain = entry.domain
    values = tuple(reader.read(*key) for key in domain)
    final, final_admissions = reader._domain_with_task_facts()
    if final != entry or final_admissions != admissions:
        raise PhaseBlocked("SESSION_SCOPE_CHANGED")
    reader._guard()
    return _planning_coverage(admissions, values)


def _read_final_planning_uses(
    reader: _FinalPlanningReader | _PreAggregatePlanningReader, session: Any
) -> tuple[HistoricalPlanningUse, ...]:
    return _read_planning_coverage(reader, session).calls


def read_final_planning_uses_owned(session: Any) -> tuple[HistoricalPlanningUse, ...]:
    """Return neutral genuine attempt facts; never a reader, selector or packet PASS."""
    reader = _FinalPlanningReader(session)
    try:
        return _read_final_planning_uses(reader, session)
    finally:
        reader.reader.close()


class _PreAggregateContext:
    """Strict original zero admission, then one continuously owned read scope."""

    def __init__(self, session: Any) -> None:
        self.pins = _CurrentFinalSources(session)
        if self.pins.stage != "FINAL:FINAL_GATE":
            raise PhaseBlocked("WAIT_FRAME_REQUIRED")
        self.zero = guards.validate_final_preseed(session.state, session.root)
        if self.zero["calls_consumed"] != 0:
            raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
        self.original = _OriginalSources(
            session.state,
            session.root,
            RunnerLock.current_owned(session.state / "session-control"),
            RunnerLock.current_owned(session.state),
            session.wait_scope,
        )
        self.check()

    def check(self) -> None:
        self.pins.check()
        self.original.check()
        current = guards.final_preparation_original_sources(
            self.original.seed, self.original.root
        )["current"]
        if current != self.zero:
            raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")


class _PreAggregateFinalSourceReader(SourceReader):
    """Fixed admitted-zero reader; cannot survive reservation or select a mode."""

    def __init__(self, session: Any) -> None:
        self._context = _PreAggregateContext(session)
        self._alive = True
        self._historical_session = session
        read = (
            self._historical_read if session.config.get("schema_version") == "8.0"
            else session._github_read
        )
        super().__init__(session.state, session.root, read=read)
        self._domain_baseline = self._derive_domain()
        self._source_tasks()
        if session.config.get("schema_version") == "8.0":
            from tools.agent_final_preparation_sources import (
                _register_historical_reader,
            )

            _register_historical_reader(session, self)

    def _historical_read(self, argv: Sequence[str]) -> str:
        return cast(str, self._historical_session._historical_read(self, argv))


    def guard(self) -> None:
        if not self._alive:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        self._context.check()
        super().guard()

    def _derive_domain(self) -> tuple[tuple[int, ...], str]:
        self.guard()
        return _final_domain_value(self)

    def _source_tasks(self) -> tuple[int, ...]:
        current = self._derive_domain()
        if current != self._domain_baseline:
            raise PhaseBlocked("SESSION_SCOPE_CHANGED")
        return current[0]

    def close(self) -> None:
        self._alive = False


@contextmanager
def preaggregate_source_scope_owned(session: Any) -> Iterator[SourceReader]:
    """Fixed factory only; no packet/factory/model route is admitted by shape."""
    reader = _PreAggregateFinalSourceReader(session)
    try:
        reader._source_tasks()
        yield reader
        reader._source_tasks()
    finally:
        reader.close()


class _PreAggregatePlanningReader(HistoricalPlanningReader):
    def __init__(self, session: Any) -> None:
        try:
            super().__init__(session)
        except BaseException:
            reader = getattr(self, "reader", None)
            if type(reader) is _PreAggregateFinalSourceReader:
                reader.close()
            raise

    def _source_reader(self, session: Any) -> _PreAggregateFinalSourceReader:
        reader = _PreAggregateFinalSourceReader(session)
        try:
            for task in reader._source_tasks():
                reader.task_directory(task)
            return reader
        except BaseException:
            reader.close()
            raise


def read_preaggregate_planning_uses_owned(
    session: Any,
) -> tuple[HistoricalPlanningUse, ...]:
    """Fixed original-zero planning read; no generic context or caller mode."""
    reader = _PreAggregatePlanningReader(session)
    try:
        return _read_final_planning_uses(reader, session)
    finally:
        reader.reader.close()
