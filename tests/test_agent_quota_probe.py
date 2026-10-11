"""A synthetic host demonstration cannot authorize a real wake backend."""

from __future__ import annotations

import pytest
from tools import agent_quota_probe as probe
from tools.agent_quota import QuotaError
from tools.agent_quota_wait import bare_digest


def fixture() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "synthetic",
        "backend": "systemd_user",
        "runner_count": 1,
        "observed_at": 1000,
        **{
            key: "a" * 64
            for key in probe.KEYS
            - {
                "schema_version",
                "kind",
                "backend",
                "runner_count",
                "observed_at",
                "probe_digest",
            }
        },
    }
    value["restart_digest"] = "b" * 64
    value["probe_digest"] = bare_digest(value)
    return value


def test_synthetic_proof_cannot_be_promoted_to_host_readiness() -> None:
    value = fixture()
    assert probe.validate(value)["kind"] == "synthetic"
    with pytest.raises(QuotaError, match="QUOTA_WAKE_PROBE_SYNTHETIC"):
        probe.require_host(
            value, expected_digest=str(value["probe_digest"]), bindings={}
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("runner_count", True),
        ("runner_count", 2),
        ("budget_after_digest", "b" * 64),
        ("restart_digest", "a" * 64),
        ("raw_log", "private"),
        ("observed_at", True),
    ],
)
def test_probe_rejects_budget_reset_duplicates_and_unbounded_fields(
    key: str, value: object
) -> None:
    proof = fixture()
    proof[key] = value
    proof["probe_digest"] = bare_digest(
        {k: v for k, v in proof.items() if k != "probe_digest"}
    )
    with pytest.raises(QuotaError, match="QUOTA_WAKE_PROBE_INVALID"):
        probe.validate(proof)


def test_synthetic_origin_exit_and_fresh_process_retain_original_budget(
    tmp_path: object,
) -> None:
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    if os.name != "posix":
        pytest.skip("Private POSIX state")
    assert isinstance(tmp_path, Path)
    script = """
import json, sys
from pathlib import Path
from tests.test_agent_quota_wait import setup
from tools.agent_quota_wait import QuotaWait
from tools.agent_epic_loop import RunnerLock
clock = [int(sys.argv[2])]
reader, owner, identity = setup(Path(sys.argv[1]), clock)
with RunnerLock.supervisor(reader.directory / "control"):
    current = QuotaWait(reader, owner, identity, runtime_deadline=1090,
        now=lambda: clock[0], guard=lambda: None)
    result = current.poll(lambda: {"primary": {
        "usedPercent": 100 if clock[0] == 1000 else 0, "resetsAt": None
    }, "secondary": None})
    print(json.dumps({key: result[key] for key in (
        "status", "started_at", "deadline_at", "attempts", "original_wait_digest"
    )}))
"""
    from tools import agent_quota_wakeup as wake

    from tests.test_agent_quota_wakeup import setup as host_setup

    callback = tmp_path / "synthetic-wake.py"
    callback.write_text(script, encoding="utf-8")
    host_dir = tmp_path / "host"
    host_dir.mkdir(mode=0o700)
    host, original_candidate, _, _ = host_setup(host_dir)
    callback_argv = [
        sys.executable,
        "-c",
        "import runpy,sys;runpy.run_path(sys.argv.pop(1),run_name='__main__')",
        str(callback),
        str(tmp_path),
        "1010",
    ]
    service = wake.service_bytes(callback_argv)
    value = wake.candidate(
        {
            key: original_candidate[key]
            for key in (
                "repository",
                "epic",
                "policy_sha",
                "state_digest",
                "runtime_config_digest",
                "quota_profile_digest",
                "source_digest",
                "supervisor_sha256",
                "host_probe_digest",
            )
        },
        service,
        issued_at=1000,
        expires_at=1100,
    )
    host.install(value, service, approved_digest=value["candidate_digest"])
    host.arm(value, "c" * 64, 1010, deadline=1090)
    done = tmp_path / "origin-exited"
    # An independent synthetic host survives the origin, then consumes exactly
    # one rendered timer. Its clock is explicitly synthetic (1000 -> 1010).
    # This is not a real systemd proof and cannot authorize host readiness.
    host_program = r"""
import hashlib,json,shlex,subprocess,sys,time
from pathlib import Path
units=Path(sys.argv[1]); done=Path(sys.argv[2]); expected=json.loads(sys.argv[3])
service=(units/sys.argv[4]).read_bytes()
assert hashlib.sha256(service).hexdigest()==sys.argv[5]
argv=shlex.split(next(line[10:] for line in service.decode().splitlines()
    if line.startswith('ExecStart=')))
assert argv==expected+["--wake-arm-id", "%i"]
expires=time.monotonic()+5
while not done.exists():
    if time.monotonic()>=expires: raise SystemExit(2)
    time.sleep(.01)
timers=list(units.glob('*.timer')); assert len(timers)==1
text=timers[0].read_text(); assert 'OnCalendar=@1010\n' in text
assert 'Persistent=true\n' in text and 'OnUnitActiveSec' not in text
service_unit=next(line[5:] for line in text.splitlines() if line.startswith("Unit="))
instance=service_unit.split("@",1)[1].removesuffix(".service")
assert instance=="c"*64
argv[-1]=instance
result=subprocess.run(argv,capture_output=True,text=True,timeout=3,check=True)
print(result.stdout.strip())
"""
    daemon = subprocess.Popen(
        [
            sys.executable,
            "-c",
            host_program,
            str(host.units),
            str(done),
            json.dumps(callback_argv),
            value["unit_prefix"] + "@.service",
            value["service_sha256"],
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        origin = subprocess.run(
            [*callback_argv[:-1], "1000"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        before = json.loads(origin.stdout)
        # No user message or foreground role process is needed for the callback.
        done.write_text("synthetic origin reaped", encoding="utf-8")
        output, errors = daemon.communicate(timeout=5)
        assert daemon.returncode == 0, errors
        restarted = subprocess.CompletedProcess(
            callback_argv, daemon.returncode, output
        )
    finally:
        if daemon.poll() is None:
            daemon.kill()
        daemon.wait(timeout=3)
    after = json.loads(restarted.stdout)
    assert before["status"] == "WAITING" and after["status"] == "READY"
    assert before["attempts"] == 1 and after["attempts"] == 2
    for key in ("started_at", "deadline_at", "original_wait_digest"):
        assert before[key] == after[key]
    evidence = fixture()
    budget = {
        key: before[key]
        for key in ("started_at", "deadline_at", "original_wait_digest")
    }
    evidence.update(
        origin_exit_digest=bare_digest(
            {"exit_code": origin.returncode, "phase": "origin"}
        ),
        restart_digest=bare_digest(
            {"exit_code": restarted.returncode, "phase": "restart"}
        ),
        budget_before_digest=bare_digest(budget),
        budget_after_digest=bare_digest(budget),
    )
    evidence["probe_digest"] = bare_digest(
        {k: v for k, v in evidence.items() if k != "probe_digest"}
    )
    assert probe.validate(evidence)["kind"] == "synthetic"
    with pytest.raises(QuotaError, match="QUOTA_WAKE_PROBE_SYNTHETIC"):
        probe.require_host(
            evidence, expected_digest=str(evidence["probe_digest"]), bindings={}
        )
