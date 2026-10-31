"""Bounded untrusted owner input projections; no owner authority or artifact IO.

The external admitted human operation must supply original direct input and
independently validate installed code, namespaces, current candidate and sources.
This helper proves only closed shapes and internal consistency of supplied data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, NoReturn

from tools import agent_final_preparation_records as records
from tools import agent_owner_final_sources as shapes
from tools.agent_epic_loop import PhaseBlocked


class OwnerInputError(ValueError):
    """Safe closed error; never contains original owner prose."""


def _invalid() -> NoReturn:
    raise OwnerInputError("FINAL_APPROVAL_INVALID")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(items)
    if len(value) != len(items):
        _invalid()
    return value


def _constant(value: str) -> NoReturn:
    _invalid()


@dataclass(frozen=True, repr=False)
class UnverifiedOwnerInput:
    """Untrusted neutral shape values, not installed or source-authenticated.

    Nested dictionaries remain ordinary untrusted JSON; the future owner writer
    must revalidate originals/current sources rather than trust this container.
    """

    approval: dict[str, Any]
    demo: dict[str, Any]
    command: dict[str, Any]
    receipt: dict[str, Any]
    pin: dict[str, Any]


def parse_owner_input(
    raw: bytes,
    approved_digest: str,
    *,
    issued_at: int,
    expires_at: int,
    max_bytes: int,
) -> UnverifiedOwnerInput:
    """Parse supplied bytes; mandate times are comparison values, not proof.

    No stdin/file reader, artifact path, clock, callback, CLI, writer or procedure
    invocation exists here. No candidate or human-authorship claim is produced.
    """
    try:
        return _parse(raw, approved_digest, issued_at, expires_at, max_bytes)
    except OwnerInputError:
        raise
    except (PhaseBlocked, ValueError, TypeError, KeyError, RecursionError):
        pass
    # Raise outside the handler so parser exceptions cannot retain input prose
    # through this public error's context, even for malformed UTF-8/JSON.
    raise OwnerInputError("FINAL_APPROVAL_INVALID") from None


def _parse(
    raw: bytes, approved_digest: str, issued_at: int, expires_at: int, max_bytes: int
) -> UnverifiedOwnerInput:
    records.integer(max_bytes, 262144, 1)
    records.integer(issued_at)
    records.integer(expires_at)
    records.hash_value(approved_digest)
    if type(raw) is not bytes or len(raw) > max_bytes or issued_at >= expires_at:
        _invalid()
    value = json.loads(
        raw.decode("utf-8", "strict"),
        object_pairs_hook=_pairs,
        parse_constant=_constant,
    )
    if not isinstance(value, dict) or set(value) != {"approval", "demo"}:
        _invalid()
    approval, demo = records.approval(value["approval"]), shapes.demo(value["demo"])
    identity_keys = records.APPROVAL - {"approved_at", "final_demo_digest"}
    if (
        approval["approved_digest"] != approved_digest
        or approval["final_demo_digest"] != demo["record_digest"]
        or any(approval[key] != demo[key] for key in identity_keys)
        or not issued_at
        <= demo["started_at"]
        <= demo["completed_at"]
        <= approval["approved_at"]
        <= expires_at
    ):
        _invalid()
    identity = {key: approval[key] for key in identity_keys}
    command = shapes.command(
        records.seal(
            {
                "schema_version": "1.0",
                **identity,
                "kind": "OWNER_FINAL_CANDIDATE_APPROVAL",
                "procedure": "ADR303_FINAL_OWNER_APPROVAL_V1",
                "approval_digest": approved_digest,
                "final_demo_digest": demo["record_digest"],
                "approved_at": approval["approved_at"],
            }
        )
    )
    pin_identity = {
        key: approval[key]
        for key in records.IDENTITY
        | {"owner_identity", "candidate_digest", "approved_at"}
    }
    receipt = records.owner_receipt(
        records.seal(
            {
                "schema_version": "1.0",
                **pin_identity,
                "kind": "OWNER_FINAL_APPROVAL_PIN",
                "approval_digest": approved_digest,
                "owner_command_digest": command["record_digest"],
            }
        )
    )
    pin = records.owner_pin(
        records.seal(
            {
                "schema_version": "1.0",
                **pin_identity,
                "approval_digest": approved_digest,
                "installation_receipt_digest": receipt["record_digest"],
            }
        )
    )
    for document in (approval, demo, command, receipt, pin):
        if len(json.dumps(document, ensure_ascii=False).encode()) > max_bytes:
            _invalid()
    return UnverifiedOwnerInput(approval, demo, command, receipt, pin)
