"""Opt-in pytest reporter: only bounded typed metadata, never captured payload."""

from __future__ import annotations

import json
import os
import select
from typing import Any

import pytest

from tools.agent_gate_journal import EVENT_LIMIT, GateBlocked, encode


def _send(event: dict[str, Any]) -> None:
    fd = int(os.environ["PGW_GATE_EVENT_FD"])
    ack = int(os.environ["PGW_GATE_ACK_FD"])
    data = encode(event) + b"\n"
    if len(data) > EVENT_LIMIT:
        raise GateBlocked("REPORTER_EVENT_LIMIT")
    # Parent acknowledges only after strict validation + durable commit.
    cursor = 0
    while cursor < len(data):
        if not select.select([], [fd], [], 5)[1]:
            raise GateBlocked("REPORTER_WRITE_TIMEOUT")
        cursor += os.write(fd, data[cursor:])
    if not select.select([ack], [], [], 5)[0] or os.read(ack, 1) != b"+":
        raise GateBlocked("REPORTER_ACK_MISSING")


def pytest_collection_finish(session: pytest.Session) -> None:
    fd = int(os.environ["PGW_GATE_IDS_FD"])
    with os.fdopen(os.dup(fd), "rb") as stream:
        raw = stream.read(24 * 1024 * 1024 + 1)
    if len(raw) > 24 * 1024 * 1024:
        raise GateBlocked("REPORTER_INVENTORY_CAPACITY")
    allowed = json.loads(raw)
    observed = [item.nodeid for item in session.items]
    if observed != allowed:
        raise GateBlocked("REPORTER_COLLECTION_MISMATCH")
    for nodeid in observed:
        _send({"kind": "collected", "nodeid": nodeid})


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    _send({"kind": "started", "nodeid": nodeid})


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _send(
        {
            "kind": "report",
            "nodeid": report.nodeid,
            "when": report.when,
            "outcome": report.outcome,
            "duration": report.duration,
            "xfail": hasattr(report, "wasxfail"),
        }
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _send({"kind": "session", "exit_code": int(exitstatus)})
