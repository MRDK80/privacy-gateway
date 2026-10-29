"""Original-launch aggregate role budget; CI/candidate identity cannot reset it."""

from __future__ import annotations

from typing import Any

from tools import agent_final_preparation_records as records
from tools.agent_continuation import digest
from tools.agent_epic_loop import PhaseBlocked
from tools.agent_final_preparation_storage import Store


class RoleCounter:
    """Requires the original preparation seed's RunnerLock throughout use."""

    def __init__(self, store: Store, launch_digest: str) -> None:
        self.store = store
        self.identity = {
            key: store.owner[key] for key in ("repository", "epic", "mandate_digest")
        }
        self.identity["launch_digest"] = records.hash_value(launch_digest, tagged=True)
        self.key = "reservation-" + digest(
            {"launch_digest": launch_digest, "ordinal": 1}
        )

    def read(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        self.store.assert_owned()
        state, reservation = self.store.read("role-state"), self.store.read(self.key)
        for value in (state, reservation):
            if value is not None and any(
                value[key] != expected for key, expected in self.identity.items()
            ):
                records.invalid()
        if state is None:
            # No implicit initialization: all local proof can be missing on
            # resume. Only fixed original-launch bootstrap may create zero.
            raise PhaseBlocked("FINAL_ROLE_STATE_MISSING")
        if state is not None:
            if state["calls_consumed"] == 0 and reservation is not None:
                records.invalid()
            if state["calls_consumed"] == 1 and state[
                "pending_reservation_digest"
            ] != self.key.removeprefix("reservation-"):
                records.invalid()
        return state, reservation

    def reserve(
        self,
        *,
        preparation_identity_digest: str,
        input_digest: str,
        backend_digest: str,
        started_at: int,
        deadline_at: int,
    ) -> dict[str, Any]:
        state, previous = self.read()
        if previous is not None or (state is not None and state["calls_consumed"]):
            raise PhaseBlocked("FINAL_ROLE_CALL_SPENT")
        reservation = records.reservation(
            records.seal(
                {
                    "schema_version": "1.0",
                    **self.identity,
                    "ordinal": 1,
                    "preparation_identity_digest": preparation_identity_digest,
                    "input_digest": input_digest,
                    "backend_digest": backend_digest,
                    "settings_digest": None,
                    "output_digest": None,
                    "started_at": started_at,
                    "deadline_at": deadline_at,
                    "status": "RESERVED",
                }
            )
        )
        consumed = records.role_state(
            records.seal(
                {
                    "schema_version": "1.0",
                    **self.identity,
                    "calls_consumed": 1,
                    "pending_reservation_digest": self.key.removeprefix("reservation-"),
                }
            )
        )
        self.store.write("role-state", consumed)
        self.store.write(self.key, reservation)
        return reservation

    def unknown(self) -> None:
        state, reservation = self.read()
        if state is None or state["calls_consumed"] != 1:
            records.invalid()
        if reservation is None or reservation["status"] != "RESERVED":
            raise PhaseBlocked("FINAL_ROLE_UNRESOLVED")
        self.store.write(self.key, records.seal({**reservation, "status": "UNKNOWN"}))
