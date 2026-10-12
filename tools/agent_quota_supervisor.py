"""Quota-paused session recovery with finite polling and separately installed wake."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from itertools import islice
from pathlib import Path
from typing import Any

from tools import agent_epic_loop as loop
from tools import agent_epic_runtime_cli as guards
from tools import agent_quota as quota
from tools import agent_quota_source as source
from tools import agent_rate_limit as rate
from tools.agent_quota_wait import QuotaWait, bare_digest
from tools.agent_quota_wakeup import (
    TIMER_TEMPLATE,
    SystemdUser,
    candidate,
    service_bytes,
    validate,
)


def prepare(
    state: Path, root: Path, config: dict[str, Any]
) -> tuple[dict[str, Any], bytes]:
    """Generate an inactive exact installation candidate without host effects."""
    if not guards.quota_enabled(config):
        raise quota.QuotaError("QUOTA_PROFILE_REQUIRED")
    mandate = guards.private_json(state / "mandate.json")
    owner = quota.profile(guards.quota_profile_guard(state, config, mandate))
    adapter = loop.CommandRuntimeAdapter.load(
        state / "runtime-adapter.json", root, state
    )
    command = adapter.epic_command
    if (
        command is None
        or command[2:]
        != (
            "epic_resume",
            "--state-dir",
            str(state),
            "--repository-root",
            str(root.resolve()),
        )
        or hashlib.sha256(Path(command[1]).read_bytes()).hexdigest()
        != owner["wakeup"]["supervisor_sha256"]
        or owner["wakeup"]["host_probe_digest"] is None
    ):
        raise quota.QuotaError("QUOTA_WAKE_BINDING_CHANGED")
    service = service_bytes([*command[:2], "quota_wake", *command[3:]])
    bindings = {
        "repository": mandate["repository"],
        "epic": mandate["epic"],
        "policy_sha": mandate["policy_sha"],
        "state_digest": bare_digest(str(state.resolve())),
        "runtime_config_digest": guards.launch_config_digest(
            config,
            guards.private_json(state / "runtime-adapter.json"),
        ).removeprefix("sha256:"),
        "quota_profile_digest": bare_digest(owner),
        "source_digest": bare_digest(owner["source"]),
        "supervisor_sha256": owner["wakeup"]["supervisor_sha256"],
        "host_probe_digest": owner["wakeup"]["host_probe_digest"],
    }
    value = candidate(
        bindings,
        service,
        issued_at=config["started_at"],
        expires_at=min(
            mandate["expires_at"],
            config["started_at"] + mandate["limits"]["max_duration_seconds"],
        ),
    )
    return value, service


def readiness(state: Path, root: Path, config: dict[str, Any]) -> None:
    if not guards.quota_enabled(config):
        return
    mandate = guards.private_json(state / "mandate.json")
    adapter = loop.CommandRuntimeAdapter.load(
        state / "runtime-adapter.json", root, state
    )
    guards.quota_deployment_guard(state, root, config, mandate, adapter.epic_command)


class Supervisor:
    """Resume only an already-paused exact phase; never replay unknown effects."""

    wake_arm_id: str | None = None

    def __init__(
        self,
        session: Any,
        *,
        host: SystemdUser,
        reconcile: Callable[[Mapping[str, Any]], str],
        live: Callable[[], Mapping[str, Any]],
        now: Callable[[], int] = lambda: int(time.time()),
        read: Callable[..., dict[str, Any]] = source.read,
    ) -> None:
        self.session, self.host, self.reconcile, self.live = (
            session,
            host,
            reconcile,
            live,
        )
        self.now, self.read = now, read
        self.state: Path = session.state
        self.root: Path = session.root
        self.config = dict(session.config)
        self.mandate = guards.private_json(self.state / "mandate.json")
        owner = guards.quota_profile_guard(self.state, self.config, self.mandate)
        self.owner = quota.profile(owner)
        self.candidate = validate(
            guards.private_json(self.state / "quota-wakeup-candidate.json")
        )
        self.scope = session.wait_scope
        if self.scope is None:
            raise quota.QuotaError("WAIT_FRAME_REQUIRED")
        self._binding()

    def _binding(self) -> None:
        self.scope.guard()
        expected, _service = prepare(self.state, self.root, self.config)
        if self.candidate != expected:
            raise quota.QuotaError("QUOTA_WAKE_BINDING_CHANGED")
        config = guards.private_json(self.state / "epic-runtime.json")
        mandate = guards.private_json(self.state / "mandate.json")
        if config != self.config or mandate != self.mandate:
            raise quota.QuotaError("QUOTA_BINDING_CHANGED")
        owner = guards.quota_profile_guard(self.state, config, mandate)
        if owner != self.owner:
            raise quota.QuotaError("QUOTA_APPROVAL_MISMATCH")
        expected = {
            "repository": mandate["repository"],
            "epic": mandate["epic"],
            "policy_sha": mandate["policy_sha"],
            "state_digest": bare_digest(str(self.state.resolve())),
            "runtime_config_digest": guards.launch_config_digest(
                config, self.scope.adapter
            ).removeprefix("sha256:"),
            "quota_profile_digest": bare_digest(owner),
            "source_digest": bare_digest(owner["source"]),
            "supervisor_sha256": owner["wakeup"]["supervisor_sha256"],
            "host_probe_digest": owner["wakeup"]["host_probe_digest"],
        }
        if any(self.candidate[key] != value for key, value in expected.items()):
            raise quota.QuotaError("QUOTA_WAKE_BINDING_CHANGED")
        if self.candidate["expires_at"] > min(
            mandate["expires_at"],
            config["started_at"] + mandate["limits"]["max_duration_seconds"],
        ):
            raise quota.QuotaError("QUOTA_WAKE_LIFECYCLE_INVALID")
        self.host.verify(self.candidate)

    def _interrupted(
        self, store: loop.CheckpointStore, saved: dict[str, Any]
    ) -> dict[str, Any]:
        from tools import agent_quota_role as transport
        from tools.agent_orchestrate import OrchestrationError

        value = guards.private_json(store.directory / "quota-interruption.json")
        if (
            set(value)
            != {
                "schema_version",
                "checkpoint_identity_digest",
                "phase",
                "workflow_key",
                "event",
            }
            or value["schema_version"] != "1.0"
            or value["checkpoint_identity_digest"]
            != bare_digest({key: saved[key] for key in loop.IDENTITY_KEYS})
            or value["phase"] != (saved["pending_phase"] or saved["phase"])
            or not isinstance(value["workflow_key"], str)
            or len(value["workflow_key"]) != 64
            or any(c not in "0123456789abcdef" for c in value["workflow_key"])
        ):
            raise quota.QuotaError("QUOTA_INTERRUPTION_BINDING_INVALID")
        record = guards.private_json(
            store.directory / "bounded-workflow" / (value["workflow_key"] + ".json")
        )
        try:
            event = transport.validate_event(
                value["event"],
                role=record["role_pending"],
                invocation_digest=record["invocation_digest"],
                backend_digest=bare_digest(self.owner["source"]),
            )
        except (KeyError, OrchestrationError):
            raise quota.QuotaError("QUOTA_INTERRUPTION_BINDING_INVALID") from None
        return {
            "checkpoint_identity_digest": value["checkpoint_identity_digest"],
            "phase": value["phase"],
            "operation_digest": event["invocation_digest"],
            "outcome": "UNKNOWN",
        }

    def _identity(self) -> dict[str, Any]:
        return {
            "repository": self.mandate["repository"],
            "epic": self.mandate["epic"],
            "policy_sha": self.mandate["policy_sha"],
            "mandate_digest": self.config["approved_mandate_digest"],
            "quota_profile_digest": bare_digest(self.owner),
            "wait_profile_digest": self.config["approved_wait_digest"],
            "runtime_config_digest": guards.launch_config_digest(
                self.config, self.scope.adapter
            ),
            "limit_id": self.owner["limit_id"],
            "source_digest": bare_digest(self.owner["source"]),
        }

    def _arm(self, current: QuotaWait) -> dict[str, Any]:
        reader = current.reader
        identity = current.identity
        with reader._locked():
            fresh = current._load()
            previous = fresh["wakeup"]
            if previous is not None:
                cancellation = self.host.state / (
                    "quota-cancel-" + previous["arm_id"] + ".json"
                )
                if cancellation.exists() or cancellation.is_symlink():
                    # The host adapter validates the exact receipt and reads the
                    # current inactive state; an uncertain stop is never replayed.
                    self.host.cancel(
                        self.candidate, previous["arm_id"], previous["unit_digest"]
                    )
                    previous["status"] = "CANCELLED"
                    current._save(fresh)
            if fresh["wakeup"] is not None and fresh["wakeup"]["status"] in {
                "RESERVED",
                "UNKNOWN",
            }:
                raise quota.QuotaError("QUOTA_WAKE_OUTCOME_UNKNOWN")
            if (
                fresh["wakeup"] is not None
                and fresh["wakeup"]["status"] == "ARMED"
                and fresh["wakeup"]["next_check_at"] == fresh["next_check_at"]
            ):
                self.host.arm(
                    self.candidate,
                    fresh["wakeup"]["arm_id"],
                    fresh["next_check_at"],
                    deadline=fresh["deadline_at"],
                )
                return {
                    "status": "PAUSED_RATE_LIMIT",
                    "machine_code": "QUOTA_WAKE_ARMED",
                }
            attempts = 1 if fresh["wakeup"] is None else fresh["wakeup"]["attempts"] + 1
            if attempts > self.owner["limits"]["max_wakeups"]:
                raise quota.QuotaError("QUOTA_WAKE_BUDGET_EXHAUSTED")
            arm_id = bare_digest(
                {
                    "identity": identity,
                    "original": fresh["original_wait_digest"],
                    "ordinal": attempts,
                }
            )
            unit = TIMER_TEMPLATE.format(
                at=fresh["next_check_at"],
                service=self.candidate["unit_prefix"] + "@" + arm_id + ".service",
            ).encode()
            fresh["wakeup"] = {
                "candidate_digest": self.candidate["candidate_digest"],
                "arm_id": arm_id,
                "attempts": attempts,
                "status": "RESERVED",
                "next_check_at": fresh["next_check_at"],
                "unit_digest": hashlib.sha256(unit).hexdigest(),
            }
            current._save(fresh)
            self.host.arm(
                self.candidate,
                arm_id,
                fresh["next_check_at"],
                deadline=fresh["deadline_at"],
            )
            fresh["wakeup"]["status"] = "ARMED"
            current._save(fresh)
        return {
            "status": "PAUSED_RATE_LIMIT",
            "machine_code": "QUOTA_WAKE_ARMED",
        }

    def _consume_wake(self, current: QuotaWait) -> None:
        arm_id = getattr(self, "wake_arm_id", None)
        if arm_id is None:
            return
        with current.reader._locked():
            fresh = current._load()
            arm = fresh["wakeup"]
            if (
                not isinstance(arm_id, str)
                or len(arm_id) != 64
                or any(c not in "0123456789abcdef" for c in arm_id)
                or arm is None
                or arm["arm_id"] != arm_id
                or arm["status"] != "ARMED"
                or arm["candidate_digest"] != self.candidate["candidate_digest"]
                or arm_id
                != bare_digest(
                    {
                        "identity": current.identity,
                        "original": fresh["original_wait_digest"],
                        "ordinal": arm["attempts"],
                    }
                )
                or fresh["status"] != "WAITING"
                or self.now() < arm["next_check_at"]
            ):
                raise quota.QuotaError("QUOTA_WAKE_STALE")
            self.host.verify_callback(self.candidate, arm)
            current._check(fresh)
            arm["status"] = "FIRED"
            current._save(fresh)

    def resume_final(self) -> dict[str, Any]:
        from tools import agent_orchestrate as workflow
        from tools import agent_quota_role as transport

        self._binding()
        path = self.state / "quota-final-pause.json"
        saved = guards.private_json(path)
        if (
            set(saved)
            != {
                "schema_version",
                "approval_digest",
                "reservation_key",
                "stage",
                "event",
                "status",
            }
            or saved["schema_version"] != "1.0"
            or saved["approval_digest"] != self.config["approved_final_digest"]
            or not isinstance(saved["stage"], str)
            or saved["stage"]
            not in {
                "FINAL:FINAL_GATE",
                "FINAL:PR_CI",
                "RECONCILE:FINAL:FINAL_GATE",
                "RECONCILE:FINAL:PR_CI",
            }
            or saved["reservation_key"]
            != hashlib.sha256(saved["approval_digest"].encode()).hexdigest()
            or saved["status"] not in {"PAUSED_RATE_LIMIT", "BLOCKED"}
        ):
            raise quota.QuotaError("QUOTA_FINAL_BINDING_INVALID")
        record = guards.private_json(
            self.state
            / "final-role-reservations"
            / (saved["reservation_key"] + ".json")
        )
        event = transport.validate_event(
            saved["event"],
            role="controller",
            invocation_digest=record["invocation_digest"],
            backend_digest=bare_digest(self.owner["source"]),
        )
        if record != {
            "schema_version": "1.0",
            "invocation_digest": event["invocation_digest"],
            "backend_digest": event["backend_digest"],
            "approval_digest": saved["approval_digest"],
            "calls": 1,
            "status": "INTERRUPTED",
            "interruption": event,
        }:
            raise quota.QuotaError("QUOTA_FINAL_BINDING_INVALID")
        # Review cannot have delivered an effect. Any delivery intent requires
        # canonical reconciliation and must never be silently discarded here.
        ledger = self.state / "final-ledger" / "delivery-ledger.json"
        if ledger.exists() and guards.private_json(ledger):
            raise quota.QuotaError("QUOTA_INTERRUPTED_OUTCOME_UNKNOWN")
        if saved["status"] == "BLOCKED":
            return {"status": "BLOCKED", "machine_code": "FINAL_ROLE_BUDGET_EXHAUSTED"}
        with self.scope.context(saved["stage"]) as reader:
            current = QuotaWait(
                reader,
                self.owner,
                self._identity(),
                runtime_deadline=self.candidate["expires_at"],
                now=self.now,
                guard=self._binding,
            )
            self._consume_wake(current)
            value = current.poll(
                lambda: self.read(
                    self.owner["source"],
                    self.owner["limit_id"],
                    guard=current.read_guard,
                    timeout_seconds=current.read_timeout(),
                )
            )
            if value["status"] == "WAITING":
                return self._arm(current)
            self._binding()
            # Final review has one original call and no repair allowance.
            # Quota becoming free does not refund its consumed reservation.
            workflow.StateStore(self.state).save(
                "quota-final-pause",
                {
                    **saved,
                    "status": "BLOCKED",
                },
            )
            return {"status": "BLOCKED", "machine_code": "FINAL_ROLE_BUDGET_EXHAUSTED"}

    def resume_paused(
        self,
        store: loop.CheckpointStore,
        *,
        resume_session: bool = True,
    ) -> dict[str, Any]:
        self._binding()
        saved = store.load()
        if saved is None or saved["status"] != "PAUSED_RATE_LIMIT":
            raise quota.QuotaError("RATE_LIMIT_PAUSE_NOT_FOUND")
        stage = f"TASK:{saved['task']}:{saved['pending_phase'] or saved['phase']}"
        with self.scope.context(stage) as reader:
            identity = self._identity()
            current = QuotaWait(
                reader,
                self.owner,
                identity,
                runtime_deadline=self.candidate["expires_at"],
                now=self.now,
                guard=self._binding,
            )
            self._consume_wake(current)
            interrupted = self._interrupted(store, saved)
            with reader._locked():
                fresh = current._load()
                if (
                    fresh["interrupted"] is not None
                    and fresh["interrupted"] != interrupted
                ):
                    raise quota.QuotaError("QUOTA_INTERRUPTION_BINDING_CHANGED")
                fresh["interrupted"] = interrupted
                current._save(fresh)
            observed: dict[str, Any] = {}

            def read() -> dict[str, Any]:
                snapshot = self.read(
                    self.owner["source"],
                    self.owner["limit_id"],
                    guard=current.read_guard,
                    timeout_seconds=current.read_timeout(),
                )
                observed.update(snapshot)
                return snapshot

            value = current.poll(read)
            if value["status"] == "WAITING":
                return self._arm(current)
            # READY quota is not authorization or proof of an interrupted write.
            outcome = self.reconcile(saved)
            if outcome not in {"APPLIED", "NOT_APPLIED"}:
                raise quota.QuotaError("QUOTA_INTERRUPTED_OUTCOME_UNKNOWN")
            self._binding()
            result = rate.resume(
                saved,
                live=self.live(),
                store=store,
                snapshot=observed,
                now=self.now(),
                max_poll_seconds=self.owner["limits"]["poll_seconds"],
            )
            if result.status != "CONTINUE":
                raise quota.QuotaError(result.machine_code)
        # Session performs fresh structural/live identity and canonical ledger checks.
        if resume_session:
            return dict(self.session.resume())
        return {"status": "CONTINUE", "machine_code": "QUOTA_RECHECK_READY"}


def cancel_owned(state: Path, root: Path) -> dict[str, str]:
    """Stop only exact installed one-shots, even after owner stop/revoke/expiry."""
    state = guards.secure_directory(state, root)
    root = root.resolve()
    config = guards.private_json(state / "epic-runtime.json")
    if not guards.quota_enabled(config):
        return {"status": "NO_OP", "machine_code": "QUOTA_NOT_ENABLED"}
    existing = state.glob("quota-arm-*.json")
    first = next(existing, None)
    if first is None:
        return {"status": "NO_OP", "machine_code": "QUOTA_NO_PENDING_WAKE"}
    mandate = guards.private_json(state / "mandate.json")
    owner = quota.profile(guards.quota_profile_guard(state, config, mandate))
    tasks = mandate["limits"]["max_task_iterations"]
    if type(tasks) is not int or tasks < 1:
        raise quota.QuotaError("QUOTA_WAKE_CANCEL_UNKNOWN")
    # Each admitted task wait and the final wait have their own original
    # allowance. Stop must cover owned timers from completed task stages too.
    maximum = (tasks + 1) * owner["limits"]["max_wakeups"]
    paths = [first, *islice(existing, maximum)]
    if len(paths) > maximum:
        raise quota.QuotaError("QUOTA_WAKE_CANCEL_UNKNOWN")
    expected, _service = prepare(state, root, config)
    candidate_value = validate(
        guards.private_json(state / "quota-wakeup-candidate.json")
    )
    if candidate_value != expected:
        raise quota.QuotaError("QUOTA_WAKE_BINDING_CHANGED")

    def guard() -> None:
        guards.managed_launch_guard(state, root, read_only=True)
        current, _service = prepare(
            state, root, guards.private_json(state / "epic-runtime.json")
        )
        if current != candidate_value:
            raise quota.QuotaError("QUOTA_WAKE_BINDING_CHANGED")
        if guards.private_json(state / "quota-install.json") != {
            "candidate": candidate_value,
            "status": "APPLIED",
        }:
            raise quota.QuotaError("QUOTA_WAKE_NOT_INSTALLED")

    host = SystemdUser(
        state,
        root,
        Path.home() / ".config" / "systemd" / "user",
        guard=guard,
        cancel_guard=guard,
        now=lambda: int(time.time()),
    )
    for path in paths:
        record = guards.private_json(path)
        if (
            set(record)
            != {"candidate_digest", "arm_id", "next_check_at", "unit_digest", "status"}
            or record["candidate_digest"] != candidate_value["candidate_digest"]
            or path.name != "quota-arm-" + str(record["arm_id"]) + ".json"
            or not isinstance(record["status"], str)
            or record["status"] not in {"RESERVED", "ARMED"}
        ):
            raise quota.QuotaError("QUOTA_WAKE_CANCEL_UNKNOWN")
        host.cancel(candidate_value, record["arm_id"], record["unit_digest"])
    return {"status": "CANCELLED", "machine_code": "QUOTA_WAKE_CANCELLED"}


def wake_deadline(session: Any) -> float:
    """A fresh invocation must not extend the original wait while acquiring its lock."""
    from tools import agent_epic_entry as entry

    scope = session.wait_scope
    if scope is None:
        raise quota.QuotaError("WAIT_FRAME_REQUIRED")
    cursor_path = session.state / "session-control" / "cursor.json"
    cursor = guards.private_json(cursor_path) if cursor_path.exists() else None
    if cursor is not None and cursor.get("mode") == "FINAL":
        paused = guards.private_json(session.state / "quota-final-pause.json")
        stage = paused.get("stage")
        if not isinstance(stage, str) or not stage.startswith(
            ("FINAL:", "RECONCILE:FINAL:")
        ):
            raise quota.QuotaError("QUOTA_FINAL_BINDING_INVALID")
    else:
        saved = entry._active_store(session.state, session.root).load()
        if saved is None:
            raise quota.QuotaError("CHECKPOINT_NOT_FOUND")
        stage = f"TASK:{saved['task']}:{saved['pending_phase'] or saved['phase']}"
    record = guards.private_json(scope.reader(stage).directory / "quota.json")
    original = record.get("deadline_at")
    if type(original) is not int or original < 0:
        raise quota.QuotaError("QUOTA_STATE_INVALID")
    # Full quota identity and counters are revalidated under runner ownership
    # before polling/arming. This read can only shorten the pre-effect wait.
    return min(float(original), float(scope.frame["deadline_at"]))


@contextmanager
def wake_lock(
    directory: Path,
    *,
    guard: Callable[[], None],
    deadline: float,
) -> Iterator[loop.RunnerLock]:
    """A one-shot callback may arrive while its originating turn still owns the lock."""
    owned = loop.RunnerLock.supervisor(directory)
    while True:
        guard()
        remaining = deadline - time.time()
        if remaining <= 0:
            raise quota.QuotaError("QUOTA_WAKE_LOCK_DEADLINE")
        try:
            owned.__enter__()
            break
        except loop.LoopError as error:
            if error.machine_code != "RUNNER_LOCKED":
                raise
        time.sleep(min(0.05, remaining))
    try:
        guard()
        yield owned
    finally:
        owned.__exit__()


def run(session: Any, *, wake: bool, wake_arm_id: str | None = None) -> dict[str, Any]:
    """The original entry and persistent callback share one verified supervisor lock."""
    from tools import agent_epic_entry as entry

    if not guards.quota_enabled(session.config):
        if wake:
            raise quota.QuotaError("QUOTA_PROFILE_REQUIRED")
        return dict(session.resume())
    if wake and wake_arm_id is None:
        raise quota.QuotaError("QUOTA_WAKE_STALE")
    control = session.state / "session-control"
    control.mkdir(mode=0o700, parents=True, exist_ok=True)
    ownership = (
        wake_lock(
            control,
            guard=session.wait_scope.guard,
            deadline=wake_deadline(session),
        )
        if wake and session.wait_scope is not None
        else loop.RunnerLock.supervisor(control)
    )
    with ownership as owned:
        store = entry._active_store(session.state, session.root)
        saved = store.load()
        if saved is None:
            raise quota.QuotaError("CHECKPOINT_NOT_FOUND")
        final_pause = session.state / "quota-final-pause.json"
        cursor_path = control / "cursor.json"
        cursor = guards.private_json(cursor_path) if cursor_path.exists() else None
        is_final = cursor is not None and cursor.get("mode") == "FINAL"
        if (
            wake
            and saved["status"] != "PAUSED_RATE_LIMIT"
            and not (is_final and final_pause.exists())
        ):
            return {"status": "BLOCKED", "machine_code": "QUOTA_WAKE_STALE"}
        if saved["status"] != "PAUSED_RATE_LIMIT" and not (
            is_final and final_pause.exists()
        ):
            result = dict(session.resume(supervisor_lock=owned))
            if result.get("status") != "PAUSED_RATE_LIMIT":
                return result
            store = entry._active_store(session.state, session.root)
        if session.wait_scope is None:
            raise quota.QuotaError("WAIT_FRAME_REQUIRED")
        scope = session.wait_scope
        host = SystemdUser(
            session.state,
            session.root,
            Path.home() / ".config" / "systemd" / "user",
            guard=scope.guard,
            now=lambda: int(time.time()),
        )

        def reconcile(checkpoint: Mapping[str, Any]) -> str:
            cursor = session.store.directory / "session-control" / "cursor.json"
            value = guards.private_json(cursor) if cursor.exists() else None
            completed = len(value["completed_tasks"]) if value is not None else 0
            adapter = session.task_adapter(
                store, session.config["task_iterations"] + completed
            )
            phase = checkpoint["pending_phase"]
            if not isinstance(phase, str):
                raise quota.QuotaError("QUOTA_INTERRUPTED_OUTCOME_UNKNOWN")
            outcome, _receipt = adapter.reconcile(phase)
            return str(outcome)

        def live() -> Mapping[str, Any]:
            cursor = session.state / "session-control" / "cursor.json"
            value = guards.private_json(cursor) if cursor.exists() else None
            completed = len(value["completed_tasks"]) if value is not None else 0
            facts = session.task_adapter(
                store,
                session.config["task_iterations"] + completed,
            ).live()
            return dict(facts)

        supervisor = Supervisor(session, host=host, reconcile=reconcile, live=live)
        supervisor.wake_arm_id = wake_arm_id if wake else None
        cursor = guards.private_json(cursor_path) if cursor_path.exists() else None
        if cursor is not None and cursor.get("mode") == "FINAL":
            return supervisor.resume_final()
        result = supervisor.resume_paused(store, resume_session=False)
        if result["status"] != "CONTINUE":
            return result
        return dict(session.resume(supervisor_lock=owned))
