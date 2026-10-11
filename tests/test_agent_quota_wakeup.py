"""Synthetic host effects use the actual fixed unit renderer and child argv."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tools import agent_quota_wakeup as wake
from tools.agent_quota import QuotaError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX host adapter")


def setup(
    tmp_path: Path,
) -> tuple[wake.SystemdUser, dict[str, Any], bytes, list[list[str]]]:
    state, root, units = (tmp_path / name for name in ("state", "root", "units"))
    for path in (state, root, units):
        path.mkdir(mode=0o700)
    service = wake.service_bytes(
        ["/synthetic/python", "/synthetic/runtime.py", "quota_wake"]
    )
    bindings = {
        "repository": "OWNER/repo",
        "epic": 294,
        "policy_sha": "a" * 40,
        **{
            k: "b" * 64
            for k in (
                "state_digest",
                "runtime_config_digest",
                "quota_profile_digest",
                "source_digest",
                "supervisor_sha256",
                "host_probe_digest",
            )
        },
    }
    candidate = wake.candidate(bindings, service, issued_at=1000, expires_at=1100)
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert kwargs["timeout"] == 5
        calls.append(argv)
        effects = [call for call in calls if "start" in call or "stop" in call]
        state = b"inactive\n" if effects and "stop" in effects[-1] else b"active\n"
        if "--property=UnitPath" in argv:
            return subprocess.CompletedProcess(argv, 0, str(units).encode())
        if "--property=FragmentPath" in argv:
            return subprocess.CompletedProcess(argv, 0, str(units / argv[3]).encode())
        if "--property=DropInPaths" in argv:
            return subprocess.CompletedProcess(argv, 0, b"")
        if "--property=NeedDaemonReload" in argv:
            return subprocess.CompletedProcess(argv, 0, b"no")
        if "--property=LoadState" in argv:
            return subprocess.CompletedProcess(argv, 0, b"loaded")
        return subprocess.CompletedProcess(argv, 0, state if "show" in argv else b"")

    adapter = wake.SystemdUser(
        state, root, units, guard=lambda: None, now=lambda: 1000, run=run
    )
    return adapter, candidate, service, calls


def test_install_does_not_arm_and_requires_exact_approval(tmp_path: Path) -> None:
    adapter, candidate, service, calls = setup(tmp_path)
    with pytest.raises(QuotaError, match="QUOTA_WAKE_APPROVAL_REQUIRED"):
        adapter.install(candidate, service, approved_digest="a" * 64)
    assert calls == []
    adapter.install(candidate, service, approved_digest=candidate["candidate_digest"])
    assert list(adapter.units.glob("*.timer")) == []
    assert all("start" not in call for call in calls)


def test_single_shot_arm_is_bound_and_not_replayed(tmp_path: Path) -> None:
    adapter, candidate, service, calls = setup(tmp_path)
    adapter.install(candidate, service, approved_digest=candidate["candidate_digest"])
    armed = adapter.arm(candidate, "c" * 64, 1010, deadline=1090)
    count = len(calls)
    assert adapter.arm(candidate, "c" * 64, 1010, deadline=1090) == armed
    assert all("show" in call for call in calls[count:])
    unit = next(adapter.units.glob("*.timer")).read_text()
    assert "OnCalendar=@1010" in unit and "OnUnitActiveSec" not in unit
    adapter.cancel(candidate, "c" * 64, armed["unit_digest"])
    assert any("stop" in call for call in calls)


def test_conflicting_units_are_never_overwritten(tmp_path: Path) -> None:
    adapter, candidate, service, calls = setup(tmp_path)
    path = adapter.units / (candidate["unit_prefix"] + ".service")
    path.write_bytes(b"conflict")
    path.chmod(0o600)
    with pytest.raises(QuotaError, match="QUOTA_WAKE_INSTALL_CONFLICT"):
        adapter.install(
            candidate, service, approved_digest=candidate["candidate_digest"]
        )
    assert path.read_bytes() == b"conflict" and calls == []


def test_expiry_cancellation_uses_only_owned_unit(tmp_path: Path) -> None:
    adapter, candidate, service, calls = setup(tmp_path)
    adapter.install(candidate, service, approved_digest=candidate["candidate_digest"])
    armed = adapter.arm(candidate, "c" * 64, 1010, deadline=1090)
    adapter.now = lambda: 1200
    adapter.guard = lambda: (_ for _ in ()).throw(QuotaError("MANDATE_EXPIRED"))
    adapter.cancel_guard = lambda: None
    adapter.cancel(candidate, "c" * 64, armed["unit_digest"])
    stops = [call for call in calls if "stop" in call]
    assert len(stops) == 1
    assert stops[0][-1] == candidate["unit_prefix"] + "-" + "c" * 64 + ".timer"


def test_foreign_loaded_service_blocks_before_arm(tmp_path: Path) -> None:
    adapter, candidate, service, calls = setup(tmp_path)
    adapter.install(candidate, service, approved_digest=candidate["candidate_digest"])
    original = adapter.run

    def foreign(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if "--property=FragmentPath" in argv:
            return subprocess.CompletedProcess(argv, 0, b"/synthetic/foreign.service")
        assert original is not None
        return original(argv, **kwargs)

    adapter.run = foreign
    count = len(calls)
    with pytest.raises(QuotaError, match="QUOTA_WAKE_UNIT_BINDING_CHANGED"):
        adapter.arm(candidate, "d" * 64, 1010, deadline=1090)
    assert not any("start" in call for call in calls[count:])


def test_cancel_after_unknown_stop_reconciles_without_replay(tmp_path: Path) -> None:
    adapter, candidate, service, calls = setup(tmp_path)
    adapter.install(candidate, service, approved_digest=candidate["candidate_digest"])
    armed = adapter.arm(candidate, "c" * 64, 1010, deadline=1090)
    original = adapter.run
    failed = [False]

    def uncertain(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert original is not None
        result = original(argv, **kwargs)
        if "stop" in argv and not failed[0]:
            failed[0] = True
            raise OSError("synthetic lost reply")
        return result

    adapter.run = uncertain
    with pytest.raises(QuotaError):
        adapter.cancel(candidate, "c" * 64, armed["unit_digest"])
    count = sum("stop" in call for call in calls)
    adapter.cancel(candidate, "c" * 64, armed["unit_digest"])
    assert sum("stop" in call for call in calls) == count


def test_saved_arm_requires_live_timer_without_start_replay(tmp_path: Path) -> None:
    adapter, candidate, service, calls = setup(tmp_path)
    adapter.install(candidate, service, approved_digest=candidate["candidate_digest"])
    adapter.arm(candidate, "c" * 64, 1010, deadline=1090)
    original = adapter.run

    def inactive(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if "--property=ActiveState" in argv:
            return subprocess.CompletedProcess(argv, 0, b"inactive\n")
        assert original is not None
        return original(argv, **kwargs)

    adapter.run = inactive
    count = sum("start" in call for call in calls)
    with pytest.raises(QuotaError, match="QUOTA_WAKE_ARM_UNKNOWN"):
        adapter.arm(candidate, "c" * 64, 1010, deadline=1090)
    assert sum("start" in call for call in calls) == count


def test_arm_deadline_is_rechecked_after_loaded_unit_reads(tmp_path: Path) -> None:
    adapter, candidate, service, calls = setup(tmp_path)
    adapter.install(candidate, service, approved_digest=candidate["candidate_digest"])
    original = adapter.run
    now = [1000]
    adapter.now = lambda: now[0]

    def delayed(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert original is not None
        result = original(argv, **kwargs)
        if "--property=LoadState" in argv and argv[3].endswith(".timer"):
            now[0] = 1010
        return result

    adapter.run = delayed
    with pytest.raises(QuotaError, match="QUOTA_WAKE_DEADLINE_INVALID"):
        adapter.arm(candidate, "c" * 64, 1010, deadline=1090)
    assert not any("start" in call for call in calls)
