"""Frozen readiness checks metadata without importing or running candidate Python."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_runtime_cli as guards
from tools import agent_quota_wakeup as wake
from tools.agent_quota_wait import bare_digest

from tests.test_agent_quota_probe import fixture
from tests.test_agent_quota_wait import setup
from tests.test_codex_adapter import active_bubblewrap  # noqa: F401

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX wake backend")


def private(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def test_missing_or_synthetic_probe_never_reads_native_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    owner: dict[str, Any] = {"wakeup": {"host_probe_digest": None}}
    monkeypatch.setattr(guards, "quota_profile_guard", lambda *args: owner)
    monkeypatch.setattr(
        guards, "_quota_read", lambda *args: pytest.fail("no native read")
    )
    with pytest.raises(guards.DeploymentError, match="QUOTA_WAKE_PROBE_REQUIRED"):
        guards.quota_deployment_guard(
            state, tmp_path, {"schema_version": "5.0"}, {}, None
        )
    proof = fixture()
    private(state / "quota-host-probe.json", proof)
    owner["wakeup"]["host_probe_digest"] = str(proof["probe_digest"])
    with pytest.raises(guards.DeploymentError, match="QUOTA_WAKE_PROBE_SYNTHETIC"):
        guards.quota_deployment_guard(
            state, tmp_path, {"schema_version": "5.0"}, {}, None
        )


def test_exact_frozen_metadata_checks_do_not_execute_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, owner, _ = setup(tmp_path, [1000])
    state, root = reader.directory, reader.root
    script = state / "runtime.py"
    script.write_text("raise AssertionError('candidate must never execute')\n")
    script.chmod(0o600)
    native = tmp_path / "native"
    native.write_bytes(b"synthetic pinned native source\n")
    native.chmod(0o700)
    owner["source"]["executable"] = str(native)
    owner["source"]["executable_sha256"] = hashlib.sha256(
        native.read_bytes()
    ).hexdigest()
    owner["source"]["version_digest"] = hashlib.sha256(b"codex-cli 0.157.1").hexdigest()
    owner["wakeup"]["supervisor_sha256"] = hashlib.sha256(
        script.read_bytes()
    ).hexdigest()
    command = (
        sys.executable,
        str(script),
        "epic_resume",
        "--state-dir",
        str(state),
        "--repository-root",
        str(root.resolve()),
    )
    service = wake.service_bytes([*command[:2], "quota_wake", *command[3:]])
    home = tmp_path / "home"
    units = home / ".config" / "systemd" / "user"
    units.mkdir(parents=True, mode=0o700)
    for parent in (home, home / ".config", home / ".config" / "systemd"):
        parent.chmod(0o700)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(time, "time", lambda: 1000)
    host = bare_digest(
        {
            "backend": "systemd_user",
            "uid": units.stat().st_uid,
            "units_digest": bare_digest(str(units)),
            "manager_version_digest": hashlib.sha256(b"synthetic-systemd").hexdigest(),
        }
    )
    proof = fixture()
    proof.update(
        {
            "kind": "host",
            "host_digest": host,
            "source_digest": bare_digest(owner["source"]),
            "supervisor_sha256": owner["wakeup"]["supervisor_sha256"],
            "service_sha256": hashlib.sha256(service).hexdigest(),
            "timer_template_sha256": hashlib.sha256(
                wake.TIMER_TEMPLATE.encode()
            ).hexdigest(),
        }
    )
    proof["probe_digest"] = bare_digest(
        {k: v for k, v in proof.items() if k != "probe_digest"}
    )
    owner["wakeup"]["host_probe_digest"] = proof["probe_digest"]
    config = {"schema_version": "5.0", "started_at": 1000}
    mandate = {
        "repository": owner["repository"],
        "epic": owner["epic"],
        "policy_sha": owner["policy_sha"],
        "expires_at": 1100,
        "limits": {"max_duration_seconds": 100},
    }
    adapter: dict[str, Any] = {}
    value = wake.candidate(
        {
            "repository": owner["repository"],
            "epic": owner["epic"],
            "policy_sha": owner["policy_sha"],
            "state_digest": bare_digest(str(state.resolve())),
            "runtime_config_digest": guards.launch_config_digest(
                config, adapter
            ).removeprefix("sha256:"),
            "quota_profile_digest": bare_digest(owner),
            "source_digest": bare_digest(owner["source"]),
            "supervisor_sha256": owner["wakeup"]["supervisor_sha256"],
            "host_probe_digest": proof["probe_digest"],
        },
        service,
        issued_at=1000,
        expires_at=1100,
    )
    for name, document in {
        "runtime-adapter": adapter,
        "quota-host-probe": proof,
        "quota-wakeup-candidate": value,
        "quota-install": {"candidate": value, "status": "APPLIED"},
    }.items():
        private(state / (name + ".json"), document)
    unit = units / (value["unit_prefix"] + ".service")
    unit.write_bytes(service)
    unit.chmod(0o600)
    monkeypatch.setattr(guards, "quota_profile_guard", lambda *args: owner)
    dropins = [b""]

    def read(argv: list[str]) -> bytes:
        if argv == [str(native), "--version"]:
            return b"codex-cli 0.157.1\n"
        responses = {
            "UnitPath": str(units).encode(),
            "FragmentPath": str(unit).encode(),
            "DropInPaths": dropins[0],
            "NeedDaemonReload": b"no",
            "LoadState": b"loaded",
            "Version": b"synthetic-systemd",
        }
        assert argv[:3] == ["/usr/bin/systemctl", "--user", "show"]
        return responses[
            next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--property="))
        ]

    monkeypatch.setattr(guards, "_quota_read", read)
    namespace_calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        guards,
        "_quota_namespace_probe",
        lambda directory, executable: namespace_calls.append((directory, executable)),
    )
    guards.quota_deployment_guard(state, root, config, mandate, command)
    assert namespace_calls == [(state, sys.executable)]
    dropins[0] = b"/synthetic/override.conf"
    with pytest.raises(guards.DeploymentError, match="QUOTA_WAKE_UNIT_BINDING_CHANGED"):
        guards.quota_deployment_guard(state, root, config, mandate, command)


def test_namespace_probe_rejects_false_or_unavailable_write_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def read(argv: list[str]) -> bytes:
        calls.append(argv)
        return b'{"allowed":true,"protected":true,"scratch":true}'

    monkeypatch.setattr(guards, "_quota_read", read)
    monkeypatch.setattr("shutil.which", lambda name: "/synthetic/bwrap")
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    with pytest.raises(guards.DeploymentError, match="QUOTA_NAMESPACE_UNSUPPORTED"):
        guards._quota_namespace_probe(state, sys.executable)
    assert calls and "-I" in calls[0]
    assert not list(state.iterdir())


@pytest.mark.usefixtures("active_bubblewrap")
def test_real_namespace_write_probe(tmp_path: Path) -> None:
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    guards._quota_namespace_probe(state, sys.executable)
    assert not list(state.iterdir())
