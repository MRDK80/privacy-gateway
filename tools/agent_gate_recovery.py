"""Owner-attested reconciliation, never an automatically inferred CLEAN flag."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from tools.agent_gate_journal import GateBlocked, Journal, digest, encode

if TYPE_CHECKING:
    from tools.agent_gate_state import State

KEYS = {
    "version",
    "genesis",
    "plan",
    "profile",
    "source",
    "frontier",
    "sequence",
    "boot",
    "deadline",
    "expires_at",
    "scope",
    "index",
    "previous_attempt",
    "next_attempt",
    "decision",
    "reconciliation_sha256",
    "owner_authorization_sha256",
}


def unresolved(state: State) -> tuple[str, int | str | None, int]:
    if state.collecting:
        return "inventory", None, state.collections
    if state.active is not None:
        return "unit", state.active, state.units[state.active]["attempt"]
    if state.quality_active is not None:
        name = state.quality_active
        return "quality", name, state.quality_attempts[name]
    raise GateBlocked("NO_UNRESOLVED_INTENT")


def validate(
    permit: dict[str, Any],
    *,
    launch: dict[str, Any],
    genesis: str,
    frontier: str,
    sequence: int,
    state: State,
    authorized_at: float,
) -> None:
    if (
        set(permit) != KEYS
        or type(permit["version"]) is not int
        or permit["version"] != 1
        or permit["decision"] != "retry_once"
    ):
        raise GateBlocked("RECOVERY_SHAPE")
    for name in (
        "genesis",
        "plan",
        "profile",
        "source",
        "frontier",
        "boot",
        "reconciliation_sha256",
        "owner_authorization_sha256",
    ):
        value = permit[name]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise GateBlocked("RECOVERY_DIGEST")
    scope, index, attempt = unresolved(state)
    if (
        permit["scope"] != scope
        or type(permit["index"]) is not type(index)
        or permit["index"] != index
        or type(permit["previous_attempt"]) is not int
        or permit["previous_attempt"] != attempt
        or type(permit["next_attempt"]) is not int
        or permit["next_attempt"] != attempt + 1
        or attempt >= 16
    ):
        raise GateBlocked("RECOVERY_INTENT_BINDING")
    if (
        type(permit["sequence"]) is not int
        or permit["sequence"] != sequence
        or permit["frontier"] != frontier
        or permit["genesis"] != genesis
        or permit["plan"] != digest(launch["plan"])
        or permit["profile"] != launch["profile"]
        or permit["source"] != launch["fingerprint"]
        or permit["boot"] != launch["boot"]
        or permit["deadline"] != launch["deadline"]
    ):
        raise GateBlocked("RECOVERY_ORIGINAL_BINDING")
    expiry = permit["expires_at"]
    if (
        type(expiry) not in (int, float)
        or not math.isfinite(expiry)
        or type(authorized_at) not in (int, float)
        or not math.isfinite(authorized_at)
        or authorized_at >= expiry
        or expiry > launch["expires_at"]
    ):
        raise GateBlocked("RECOVERY_EXPIRED")
    if (
        scope == "inventory"
        and state.reservation_expires_at is not None
        and authorized_at >= state.reservation_expires_at
    ):
        raise GateBlocked("PARENT_RESERVATION_EXPIRED")
    if state.collections >= launch["max_collections"]:
        raise GateBlocked("COLLECTION_BUDGET_EXHAUSTED")


def disposition_event(
    store: Journal,
    state: State,
    permit: dict[str, Any],
    approved_digest: str,
    now: float,
) -> dict[str, Any]:
    if digest(permit) != approved_digest:
        raise GateBlocked("RECOVERY_PIN_INVALID")
    validate(
        permit,
        launch=store.genesis["launch"],
        genesis=digest(store.genesis),
        frontier=store.frontier,
        sequence=store.sequence,
        state=state,
        authorized_at=now,
    )
    # Preserve the original quotas. Conservatively reserve a whole plan's
    # evidence envelope; no live cap extension or historical eviction.
    from tools.agent_gate_state import capacity_preflight

    launch = store.genesis["launch"]
    capacity_preflight(
        launch["plan"],
        launch["max_collections"],
        used_events=store.sequence,
        used_bytes=store.used_bytes,
        used_journal_bytes=store.offset,
        used_snapshot_bytes=len(encode(state.snapshot())),
    )
    return {
        "kind": "recovery_dispatch",
        "permit": permit,
        "permit_sha256": approved_digest,
        "authorized_at": now,
    }


def apply(state: State, event: dict[str, Any]) -> None:
    if set(event) != {"kind", "permit", "permit_sha256", "authorized_at"}:
        raise GateBlocked("RECOVERY_EVENT_SHAPE")
    permit = event["permit"]
    if not isinstance(permit, dict) or digest(permit) != event["permit_sha256"]:
        raise GateBlocked("RECOVERY_EVENT_DIGEST")
    scope, index, attempt = unresolved(state)
    if (
        permit["scope"],
        permit["index"],
        permit["previous_attempt"],
        permit["next_attempt"],
    ) != (scope, index, attempt, attempt + 1):
        raise GateBlocked("RECOVERY_EVENT_INTENT")
    # Consumption + next intent reservation is ONE event, never two replayable
    # steps. The old outcome remains history; it is not relabelled PASS/CLEAN.
    previous: Any
    if scope == "unit":
        assert isinstance(index, int)
        previous = state.units[index]
    elif scope == "inventory":
        previous = {
            "collected": state.collection_nodes,
            "session": state.collection_session,
        }
    else:
        assert isinstance(index, str)
        previous = state.quality[index]
    import json

    state.history.append(
        {
            "scope": scope,
            "index": index,
            "attempt": attempt,
            "previous": json.loads(
                encode({"outcome": previous, "process": state.current_process})
            ),
            "disposition": event["permit_sha256"],
        }
    )
    if scope == "unit":
        assert isinstance(index, int)
        state.units[index] = state.empty_unit(attempt + 1)
        state.reservation_expires_at = permit["expires_at"]
        state.recovery_pending = True
    elif scope == "inventory":
        state.collection_expires_at = permit["expires_at"]
        state.collections += 1
        state.collection_nodes = []
        state.collection_session = None
    else:
        assert isinstance(index, str)
        state.quality_attempts[index] += 1
        state.quality[index] = None
        state.reservation_expires_at = permit["expires_at"]
        state.recovery_pending = True
    state.current_process = None
