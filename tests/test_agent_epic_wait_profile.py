"""Closed owner-approved wait profiles are validated before candidate imports."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_loop as loop
from tools import agent_epic_planning as admission
from tools import agent_epic_runtime_cli as guards
from tools import agent_epic_session_runtime as runtime


def profile(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    mandate = {
        "repository": "owner/repo",
        "epic": 248,
        "roadmap_ref": "roadmap/248-demo",
        "policy_sha": "a" * 40,
        "owner_identity": "synthetic-owner",
    }
    value = {
        "schema_version": "1.0",
        **mandate,
        "mandate_digest": "sha256:" + "b" * 64,
        "limits": {
            "max_phase_seconds": 600,
            "max_read_attempts": 11,
            "max_ci_polls": 11,
            "max_read_commands": 512,
        },
    }
    path = tmp_path / "wait-profile.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    config = {
        "schema_version": "4.0",
        "approved_wait_digest": guards.canonical_digest(value),
        "approved_mandate_digest": value["mandate_digest"],
        "approved_planning_digest": None,
    }
    return config, mandate


def test_config4_closed_keys_and_optional_planning() -> None:
    config = {"schema_version": "4.0", "approved_planning_digest": None}
    assert guards.epic_config_keys(config) == guards.EPIC_CONFIG_KEYS | {
        "approved_launch_digest",
        "approved_planning_digest",
        "approved_wait_digest",
    }
    assert not guards.planning_enabled(config)
    assert guards.planning_enabled(
        {**config, "approved_planning_digest": "sha256:" + "a" * 64}
    )
    assert guards.planning_enabled({"schema_version": "3.0"})
    for version in ("1.0", "2.0", "3.0"):
        assert guards.epic_config_keys({"schema_version": version}) == (
            guards.EPIC_CONFIG_KEYS
            | ({"approved_launch_digest"} if version != "1.0" else set())
            | ({"approved_planning_digest"} if version == "3.0" else set())
        )


@pytest.mark.parametrize(
    ("version", "planning_digest", "enabled"),
    [
        ("1.0", None, False),
        ("2.0", None, False),
        ("3.0", "sha256:" + "a" * 64, True),
        ("4.0", None, False),
        ("4.0", "sha256:" + "a" * 64, True),
    ],
)
def test_session_recipe_uses_optional_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    planning_digest: str | None,
    enabled: bool,
) -> None:
    session = runtime.SessionRuntime.__new__(runtime.SessionRuntime)
    session.state = tmp_path
    session.root = tmp_path
    session.config = {
        "schema_version": version,
        "approved_planning_digest": planning_digest,
    }
    monkeypatch.setattr(runtime.SessionRuntime, "_approved_recipe", lambda *args: None)
    calls: list[tuple[int, str | None]] = []

    def recipe(_: object, task: int, approved: str | None) -> dict[str, Any]:
        calls.append((task, approved))
        return {"synthetic": True}

    monkeypatch.setattr(admission.Admission, "recipe", recipe)
    if enabled:
        assert session._recipe(299) == {"synthetic": True}
        assert calls == [(299, planning_digest)]
    else:
        with pytest.raises(loop.PhaseBlocked, match="NEXT_TASK_PLAN_REQUIRED"):
            session._recipe(299)
        assert calls == []


@pytest.mark.skipif(os.name == "nt", reason="Owner-private runtime is POSIX-only")
def test_owner_wait_profile(tmp_path: Path) -> None:
    config, mandate = profile(tmp_path)
    result = guards.wait_profile_guard(tmp_path, config, mandate)
    assert result is not None
    assert result["limits"]["max_ci_polls"] == 11
    assert (
        guards.wait_profile_guard(tmp_path, {"schema_version": "3.0"}, mandate) is None
    )
    assert guards.planning_profile_guard(tmp_path, config, mandate) is None


@pytest.mark.skipif(os.name == "nt", reason="Owner-private runtime is POSIX-only")
@pytest.mark.parametrize(
    "change", ["digest", "identity", "unknown", "limit", "bool", "hardlink"]
)
def test_wait_profile_rejects_drift(tmp_path: Path, change: str) -> None:
    config, mandate = profile(tmp_path)
    path = tmp_path / "wait-profile.json"
    value = json.loads(path.read_text())
    if change == "digest":
        config["approved_wait_digest"] = "sha256:" + "c" * 64
    elif change == "identity":
        value["epic"] += 1
    elif change == "unknown":
        value["extra"] = True
    elif change == "limit":
        value["limits"]["max_read_attempts"] = 12
    elif change == "bool":
        value["limits"]["max_ci_polls"] = True
    else:
        os.link(path, tmp_path / "alias")
    path.write_text(json.dumps(value))
    if change != "digest":
        config["approved_wait_digest"] = guards.canonical_digest(value)
    with pytest.raises(guards.DeploymentError):
        guards.wait_profile_guard(tmp_path, config, mandate)
