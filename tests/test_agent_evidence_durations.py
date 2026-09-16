"""Наблюдаемость длительностей прогона в записях evidence (#200)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tests.test_agent_orchestrator import (  # noqa: F401
    FakeAdapter,
    _contract,
    _passing_gate,
    _storage,
    orchestrator,
    repository,
)

EXECUTOR_DELAY_SECONDS = 0.20
CONTROLLER_DELAY_SECONDS = 0.12
MEASUREMENT_TOLERANCE_SECONDS = 0.10


class TimedAdapter(FakeAdapter):
    """Fake-адаптер с известной задержкой в каждой роли."""

    def execute(self, request: dict[str, Any], session_id: str) -> dict[str, Any]:
        time.sleep(EXECUTOR_DELAY_SECONDS)
        return super().execute(request, session_id)

    def review(
        self,
        request: dict[str, Any],
        trusted_policy: dict[str, str],
        session_id: str,
    ) -> dict[str, Any]:
        time.sleep(CONTROLLER_DELAY_SECONDS)
        return super().review(request, trusted_policy, session_id)


def _observed_run(root: Path) -> tuple[float, dict[str, Any], dict[str, Any]]:
    """Выполнить прогон, вернув наблюдённое время и обе записи.

    Приватное хранилище уникально для каждого теста: `tmp_path.parent`
    общий для всей сессии pytest, поэтому общий каталог накапливал бы
    записи разных прогонов.
    """
    contract = _contract(root)
    storage = _storage(root)
    memory = orchestrator.RetrospectiveStore(
        root.parent / f"{root.name}-duration-memory", root
    )

    started = time.monotonic()
    result = orchestrator.run(
        contract,
        root=root,
        storage=storage,
        adapter=TimedAdapter(["PASS"]),
        gate=_passing_gate,
        memory=memory,
    )
    observed = time.monotonic() - started

    assert result.status == "PASS"
    records = list(memory.records())
    assert len(records) == 1
    public = storage.load(orchestrator._run_key(contract))
    assert public is not None
    return observed, records[0], public


def test_persisted_total_duration_matches_observed_wall_clock(
    repository: Path,  # noqa: F811
) -> None:
    observed, record, _public = _observed_run(repository)
    durations = record["evidence"]["durations"]
    total = durations["total_seconds"]

    assert total is not None
    assert total <= observed + MEASUREMENT_TOLERANCE_SECONDS
    assert total >= EXECUTOR_DELAY_SECONDS + CONTROLLER_DELAY_SECONDS


def test_role_durations_reflect_injected_delays(
    repository: Path,  # noqa: F811
) -> None:
    observed, record, _public = _observed_run(repository)
    durations = record["evidence"]["durations"]

    assert durations["executor_seconds"] >= EXECUTOR_DELAY_SECONDS
    assert durations["controller_seconds"] >= CONTROLLER_DELAY_SECONDS
    assert durations["gate_seconds"] is not None
    assert durations["gate_seconds"] >= 0.0
    role_total = (
        durations["executor_seconds"]
        + durations["controller_seconds"]
        + durations["gate_seconds"]
    )
    assert role_total <= observed + MEASUREMENT_TOLERANCE_SECONDS
    assert (
        role_total
        <= durations["total_seconds"] + MEASUREMENT_TOLERANCE_SECONDS
    )


def test_integer_duration_seconds_agrees_with_measured_total(
    repository: Path,  # noqa: F811
) -> None:
    _observed, record, _public = _observed_run(repository)
    total = record["evidence"]["durations"]["total_seconds"]

    assert record["duration_seconds"] >= 0
    assert abs(record["duration_seconds"] - total) <= 1.0


def test_public_and_private_durations_agree(
    repository: Path,  # noqa: F811
) -> None:
    _observed, record, public = _observed_run(repository)
    private_total = record["evidence"]["durations"]["total_seconds"]
    public_evidence = public["evidence"]

    assert public["record_schema_version"] == (
        orchestrator.PUBLIC_RECORD_SCHEMA_VERSION
    )
    assert public_evidence["duration_seconds"] == private_total


def test_public_record_duration_does_not_leak_role_breakdown(
    repository: Path,  # noqa: F811
) -> None:
    _observed, _record, public = _observed_run(repository)
    public_evidence = public["evidence"]

    assert "durations" not in public_evidence
    assert set(public_evidence) == {
        "schema_version",
        "gate",
        "snapshot",
        "duration_seconds",
    }
