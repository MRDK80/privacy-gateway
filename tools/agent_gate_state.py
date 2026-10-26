"""Strict replay of typed pytest events, separate from durable storage."""

from __future__ import annotations

import math
from typing import Any

from tools.agent_gate_journal import GateBlocked, digest, encode

PHASES = ("setup", "call", "teardown")
OUTCOMES = ("passed", "failed", "skipped")
MAX_NODES = 20_000


def validate_plan(plan: dict[str, Any]) -> None:
    if set(plan) != {"inventory", "units", "allowed_skips", "review", "version"}:
        raise GateBlocked("PLAN_SHAPE")
    if (
        type(plan["version"]) is not int
        or plan["version"] != 1
        or not isinstance(plan["review"], str)
    ):
        raise GateBlocked("PLAN_REVIEW")
    if len(plan["review"]) != 64 or any(
        c not in "0123456789abcdef" for c in plan["review"]
    ):
        raise GateBlocked("PLAN_REVIEW")
    nodes = plan["inventory"]
    if not isinstance(nodes, list) or not 0 < len(nodes) <= MAX_NODES:
        raise GateBlocked("PLAN_INVENTORY")
    # Names must be explicitly reviewed synthetic labels. No attempt to detect PII
    # heuristically: the review binds these exact bytes and isolation assertions.
    if any(
        not isinstance(n, str)
        or not n
        or len(n.encode()) > 4096
        or any(ord(c) < 32 for c in n)
        for n in nodes
    ):
        raise GateBlocked("PLAN_NODE_INVALID")
    if len(set(nodes)) != len(nodes):
        raise GateBlocked("PLAN_NODE_DUPLICATE")
    units = plan["units"]
    if not isinstance(units, list) or not units:
        raise GateBlocked("PLAN_UNITS")
    flattened: list[str] = []
    for unit in units:
        if not isinstance(unit, list) or not unit:
            raise GateBlocked("PLAN_UNIT_EMPTY")
        flattened.extend(unit)
    if flattened != nodes:
        raise GateBlocked("PLAN_UNIT_COVERAGE")
    allowed = plan["allowed_skips"]
    if not isinstance(allowed, list) or len(set(allowed)) != len(allowed):
        raise GateBlocked("PLAN_SKIP_POLICY")
    if not set(allowed) <= set(nodes):
        raise GateBlocked("PLAN_SKIP_POLICY")
    # Preflight conservative encoded worst-case envelope before expensive tests.
    envelope = {
        n: {p: {"outcome": "skipped", "duration": 1e300} for p in PHASES} for n in nodes
    }
    if len(encode({"nodes": envelope, "plan": plan})) > 24 * 1024 * 1024:
        raise GateBlocked("PLAN_SNAPSHOT_CAPACITY")
    if len(nodes) * 8 > 90_000:
        raise GateBlocked("PLAN_JOURNAL_EVENT_CAPACITY")


class State:
    def __init__(self, plan: dict[str, Any]) -> None:
        validate_plan(plan)
        self.plan = plan
        self.units: dict[int, dict[str, Any]] = {}
        self.active: int | None = None
        self.paused = False
        self.collections = 0
        self.collecting = False
        self.collection_nodes: list[str] = []
        self.collection_session: int | None = None
        self.quality: dict[str, dict[str, Any] | None] = {}
        self.quality_active: str | None = None
        self.quality_attempts: dict[str, int] = {}
        self.history: list[dict[str, Any]] = []
        self.current_process: dict[str, Any] | None = None
        self.failures: list[dict[str, Any]] = []
        self.recovery_pending = False
        self.reservation_expires_at: float | None = None
        self.collection_expires_at: float | None = None

    @staticmethod
    def empty_unit(attempt: int) -> dict[str, Any]:
        return {
            "collected": [],
            "nodes": {},
            "session": None,
            "terminal": None,
            "next": 0,
            "current": None,
            "attempt": attempt,
        }

    def apply(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "failure_observation":
            from tools.agent_gate_recovery import unresolved

            expected_scope, expected_index, expected_attempt = unresolved(self)
            if (
                set(event)
                != {
                    "kind",
                    "scope",
                    "index",
                    "attempt",
                    "category",
                    "reason",
                    "observed",
                }
                or event["scope"] != expected_scope
                or type(event["index"]) is not type(expected_index)
                or event["index"] != expected_index
                or type(event["attempt"]) is not int
                or event["attempt"] != expected_attempt
                or event["category"]
                not in (
                    "GATE_BLOCKED",
                    "OS_ERROR",
                    "INVALID_REPORT",
                    "TIMEOUT",
                    "INTERRUPTED",
                    "OTHER",
                )
                or event["reason"]
                not in (
                    "REPORTER_ROLE_VIOLATION",
                    "RUN_EXPIRED",
                    "FINGERPRINT_CHANGED",
                    "CHILD_DEADLINE",
                    "CHILD_TERMINAL_OR_OWNERSHIP_UNKNOWN",
                    "REPORTER_EVENT_CAPACITY",
                    "REPORTER_EVENT_INVALID",
                    "REPORTER_ACK_TIMEOUT",
                    "UNEXPECTED_QUALITY_EVENT",
                    "OTHER",
                )
            ):
                raise GateBlocked("FAILURE_OBSERVATION_INVALID")
            facts = event["observed"]
            if not isinstance(facts, dict) or set(facts) != {
                "observed",
                "spawned",
                "pid",
                "exit_before_cleanup",
                "exit_after_cleanup",
                "direct_child_reaped",
                "group",
                "cleanup_attempt",
                "cleanup_error",
            }:
                raise GateBlocked("FAILURE_FACTS_SHAPE")
            if type(facts["observed"]) is not bool:
                raise GateBlocked("FAILURE_FACTS_TYPE")
            for key in ("spawned", "direct_child_reaped", "cleanup_error"):
                if facts[key] is not None and type(facts[key]) is not bool:
                    raise GateBlocked("FAILURE_FACTS_TYPE")
            for key in ("pid", "exit_before_cleanup", "exit_after_cleanup"):
                if facts[key] is not None and type(facts[key]) is not int:
                    raise GateBlocked("FAILURE_FACTS_TYPE")
            if facts["group"] not in (
                "EMPTY",
                "PRESENT",
                "UNKNOWN",
                "NOT_SPAWNED",
            ) or facts["cleanup_attempt"] not in (
                "NONE",
                "TERMINATE",
                "TERMINATE_THEN_KILL",
                "UNKNOWN",
            ):
                raise GateBlocked("FAILURE_FACTS_ENUM")
            self.failures.append(event)
            return
        if kind == "recovery_dispatch":
            from tools.agent_gate_recovery import apply

            apply(self, event)
            return
        if kind == "spawn":
            if (
                set(event) != {"kind", "pid", "group", "boot", "scope"}
                or self.current_process is not None
                or event["scope"]
                != (
                    "inventory"
                    if self.collecting
                    else "quality"
                    if self.quality_active is not None
                    else "unit"
                )
                or not (
                    self.collecting
                    or self.active is not None
                    or self.quality_active is not None
                )
                or type(event["pid"]) is not int
                or event["pid"] <= 0
                or event["group"] != event["pid"]
                or not isinstance(event["boot"], str)
                or len(event["boot"]) != 64
            ):
                raise GateBlocked("SPAWN_OBSERVATION_INVALID")
            self.current_process = event
            return
        if kind == "quality_dispatch":
            name = event.get("name")
            if (
                set(event) != {"kind", "name"}
                or name not in ("ruff", "mypy", "precommit")
                or self.active is not None
                or self.collecting
                or self.quality_active is not None
                or name in self.quality
                or len(self.units) != len(self.plan["units"])
            ):
                raise GateBlocked("QUALITY_DISPATCH_INVALID")
            self.reservation_expires_at = None
            self.quality_attempts[name] = 1
            self.quality[name] = None
            self.current_process = None
            self.quality_active = name
            return
        if kind == "quality_terminal":
            if (
                set(event) != {"kind", "name", "fingerprint"}
                or event["name"] != self.quality_active
                or self.quality_active is None
            ):
                raise GateBlocked("QUALITY_TERMINAL_INVALID")
            self.quality[self.quality_active] = event
            self.quality_active = None
            self.reservation_expires_at = None
            self.recovery_pending = False
            self.current_process = None
            return
        if kind in ("inventory_dispatch", "recovery_inventory_dispatch"):
            if (
                set(event) != {"kind", "attempt"}
                or self.collecting
                or (
                    kind == "inventory_dispatch"
                    and (self.active is not None or self.quality_active is not None)
                )
                or (kind == "recovery_inventory_dispatch" and not self.recovery_pending)
                or event["attempt"] != self.collections + 1
            ):
                raise GateBlocked("INVENTORY_DISPATCH_INVALID")
            self.current_process = None
            self.collection_expires_at = None
            self.collections += 1
            self.collecting = True
            self.collection_nodes = []
            self.collection_session = None
            return
        if self.collecting:
            if kind == "collected":
                index = len(self.collection_nodes)
                if (
                    set(event) != {"kind", "nodeid"}
                    or index >= len(self.plan["inventory"])
                    or event["nodeid"] != self.plan["inventory"][index]
                    or self.collection_session is not None
                ):
                    raise GateBlocked("INVENTORY_COLLECTION_INVALID")
                self.collection_nodes.append(event["nodeid"])
            elif kind == "session":
                if (
                    event != {"kind": "session", "exit_code": 0}
                    or self.collection_session is not None
                ):
                    raise GateBlocked("INVENTORY_SESSION_INVALID")
                self.collection_session = 0
            elif kind == "inventory_terminal":
                if (
                    event != {"kind": "inventory_terminal", "ownership": "CLEAN"}
                    or self.collection_session != 0
                    or self.collection_nodes != self.plan["inventory"]
                ):
                    raise GateBlocked("INVENTORY_INCOMPLETE")
                self.collecting = False
                self.collection_expires_at = None
                self.current_process = None
            else:
                raise GateBlocked("INVENTORY_EVENT_INVALID")
            return
        if kind == "dispatch":
            if set(event) != {"kind", "unit", "attempt"}:
                raise GateBlocked("EVENT_DISPATCH_SHAPE")
            unit = event["unit"]
            if type(unit) is not int or not 0 <= unit < len(self.plan["units"]):
                raise GateBlocked("EVENT_UNIT")
            if unit in self.units or self.active is not None or event["attempt"] != 1:
                raise GateBlocked("EVENT_DISPATCH_REPEATED")
            self.reservation_expires_at = None
            self.units[unit] = self.empty_unit(1)
            self.current_process = None
            self.active = unit
            self.paused = False
            return
        if kind == "pause":
            if (
                set(event) != {"kind"}
                or self.active is not None
                or self.collecting
                or self.quality_active is not None
            ):
                raise GateBlocked("EVENT_PAUSE_INVALID")
            self.paused = True
            return
        if self.active is None:
            raise GateBlocked("EVENT_WITHOUT_DISPATCH")
        unit = self.units[self.active]
        expected = self.plan["units"][self.active]
        if kind == "collected":
            if set(event) != {"kind", "nodeid"} or unit["nodes"] or unit["session"]:
                raise GateBlocked("EVENT_COLLECTION_ORDER")
            index = len(unit["collected"])
            if index >= len(expected) or event["nodeid"] != expected[index]:
                raise GateBlocked("EVENT_COLLECTION_MISMATCH")
            unit["collected"].append(event["nodeid"])
        elif kind == "started":
            if (
                set(event) != {"kind", "nodeid"}
                or unit["collected"] != expected
                or unit["current"] is not None
                or unit["session"] is not None
                or unit["next"] >= len(expected)
                or event["nodeid"] != expected[unit["next"]]
            ):
                raise GateBlocked("EVENT_START_ORDER")
            unit["current"] = event["nodeid"]
        elif kind == "report":
            if set(event) != {"kind", "nodeid", "when", "outcome", "duration", "xfail"}:
                raise GateBlocked("EVENT_REPORT_SHAPE")
            if unit["collected"] != expected or unit["session"] is not None:
                raise GateBlocked("EVENT_REPORT_ORDER")
            node = event["nodeid"]
            if node != unit["current"]:
                raise GateBlocked("EVENT_REPORT_WITHOUT_START")
            if unit["next"] >= len(expected) or node != expected[unit["next"]]:
                raise GateBlocked("EVENT_NODE_ORDER")
            when, outcome, duration = event["when"], event["outcome"], event["duration"]
            if (
                when not in PHASES
                or outcome not in OUTCOMES
                or event["xfail"] is not False
            ):
                raise GateBlocked("EVENT_PHASE_OR_XFAIL")
            if (
                type(duration) not in (int, float)
                or not math.isfinite(duration)
                or duration < 0
            ):
                raise GateBlocked("EVENT_DURATION")
            reports = unit["nodes"].setdefault(node, {})
            if when in reports or (when != "setup" and "setup" not in reports):
                raise GateBlocked("EVENT_PHASE_ORDER")
            if when == "call" and reports["setup"]["outcome"] != "passed":
                raise GateBlocked("EVENT_CALL_AFTER_SETUP_FAILURE")
            reports[when] = {"outcome": outcome, "duration": duration}
            if when == "teardown":
                unit["next"] += 1
                unit["current"] = None
        elif kind == "session":
            if set(event) != {"kind", "exit_code"} or unit["session"] is not None:
                raise GateBlocked("EVENT_SESSION_SHAPE")
            if type(event["exit_code"]) is not int:
                raise GateBlocked("EVENT_SESSION_CODE")
            unit["session"] = event["exit_code"]
        elif kind == "terminal":
            if set(event) != {"kind", "exit_code", "ownership", "fingerprint"}:
                raise GateBlocked("EVENT_TERMINAL_SHAPE")
            if event["ownership"] != "CLEAN" or event["exit_code"] != 0:
                raise GateBlocked("EVENT_TERMINAL_FAILURE")
            if unit["session"] != 0 or unit["next"] != len(expected):
                raise GateBlocked("EVENT_INCOMPLETE_SESSION")
            for node in expected:
                phases = unit["nodes"].get(node, {})
                if "setup" not in phases or "teardown" not in phases:
                    raise GateBlocked("EVENT_PHASE_MISSING")
                if phases["teardown"]["outcome"] != "passed":
                    raise GateBlocked("EVENT_TEARDOWN_FAILURE")
                setup = phases["setup"]["outcome"]
                call = phases.get("call", {}).get("outcome")
                skipped = setup == "skipped" or call == "skipped"
                if skipped:
                    if node not in self.plan["allowed_skips"]:
                        raise GateBlocked("EVENT_MANDATORY_SKIPPED")
                elif setup != "passed" or call != "passed":
                    raise GateBlocked("EVENT_NODE_FAILED")
            unit["terminal"] = event
            self.active = None
            self.reservation_expires_at = None
            self.recovery_pending = False
            self.current_process = None
        else:
            raise GateBlocked("EVENT_KIND")

    def remaining(self) -> list[int]:
        if (
            self.active is not None
            or self.collecting
            or self.quality_active is not None
        ):
            raise GateBlocked("UNIT_OUTCOME_UNKNOWN")
        return [i for i in range(len(self.plan["units"])) if i not in self.units]

    def snapshot(self) -> dict[str, Any]:
        return {
            "failures": self.failures,
            "reservation_expires_at": self.reservation_expires_at,
            "collection_expires_at": self.collection_expires_at,
            "recovery_pending": self.recovery_pending,
            "history": self.history,
            "current_process": self.current_process,
            "quality_attempts": self.quality_attempts,
            "quality": self.quality,
            "quality_active": self.quality_active,
            "collections": self.collections,
            "collecting": self.collecting,
            "collection_nodes": self.collection_nodes,
            "plan": digest(self.plan),
            "active": self.active,
            "units": {str(k): v for k, v in self.units.items()},
            "paused": self.paused,
        }


def capacity_preflight(
    plan: dict[str, Any],
    collections: int,
    *,
    used_events: int = 0,
    used_bytes: int = 0,
    used_journal_bytes: int = 0,
    used_snapshot_bytes: int = 0,
) -> None:
    """Count concrete worst-case transitions and encoded bytes, before dispatch."""
    from tools.agent_gate_journal import JOURNAL_LIMIT, MAX_EVENTS, TOTAL_LIMIT

    validate_plan(plan)
    if type(collections) is not int or not 1 <= collections <= 16:
        raise GateBlocked("PLAN_COLLECTION_CAPACITY")
    events = (
        5 * collections + 9 + (1 if used_events else 0)
    )  # collection3 + at most one pause per session.
    journal_bytes = events * 1024 + (16 * 1024 if used_events else 0)
    node_snapshot: dict[str, Any] = {}

    def record_size(event: dict[str, Any]) -> int:
        return (
            len(
                encode(
                    {
                        "sequence": MAX_EVENTS,
                        "previous": "f" * 64,
                        "genesis": "f" * 64,
                        "event": event,
                    }
                )
            )
            + 1
        )

    for node in plan["inventory"]:
        collected = {"kind": "collected", "nodeid": node}
        started = {"kind": "started", "nodeid": node}
        report_bytes = 0
        phases = {}
        for phase in PHASES:
            report = {
                "kind": "report",
                "nodeid": node,
                "when": phase,
                "outcome": "skipped",
                "duration": 1.7976931348623157e308,
                "xfail": False,
            }
            report_bytes += record_size(report)
            phases[phase] = {"outcome": "skipped", "duration": 1.7976931348623157e308}
        events += collections + 5
        journal_bytes += (collections + 1) * record_size(collected)
        journal_bytes += record_size(started) + report_bytes
        node_snapshot[node] = phases
    events += 4 * len(plan["units"])
    journal_bytes += 4 * len(plan["units"]) * 1024
    # Upper envelope contains the actual repeated IDs plus state metadata reserve.
    snapshot_bytes = len(encode({"nodes": node_snapshot, "plan": plan}))
    snapshot_bytes += 1024 * len(plan["units"]) + 4096 + used_snapshot_bytes
    genesis_bytes = len(encode(plan)) + 64 * 1024
    commit_bytes = events * 256
    if (
        events + used_events > MAX_EVENTS
        or journal_bytes + used_journal_bytes > JOURNAL_LIMIT
        or snapshot_bytes > 32 * 1024 * 1024
        or used_bytes
        + genesis_bytes
        + journal_bytes
        + commit_bytes
        + 3 * snapshot_bytes
        + 1024 * 1024
        > TOTAL_LIMIT
    ):
        raise GateBlocked("PLAN_TOTAL_EVIDENCE_CAPACITY")
