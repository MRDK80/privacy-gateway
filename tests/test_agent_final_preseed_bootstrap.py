"""Config8 admission stays inside the original frozen bootstrap boundary."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tools import agent_epic_runtime_cli as guards
from tools import agent_final_preseed_guard as compatibility

from tests.test_agent_final_preseed_guard import fixture


def test_config8_closed_shape_inherits_config7() -> None:
    assert guards.epic_config_keys({"schema_version": "8.0"}) == (
        guards.epic_config_keys({"schema_version": "7.0"})
        | {"approved_final_preparation_digest"}
    )
    assert guards.planning_enabled(
        {"schema_version": "8.0", "approved_planning_digest": "approved"}
    )
    assert guards.quota_enabled(
        {"schema_version": "8.0", "approved_quota_digest": "approved"}
    )


def test_compatibility_exports_frozen_implementation() -> None:
    assert compatibility.validate_preseed is guards.validate_final_preseed
    assert compatibility.CONFIG8_KEYS is guards._final_preseed_CONFIG8_KEYS
    assert compatibility._content is guards._final_preseed_content


def test_frozen_validator_has_no_policy_import() -> None:
    module = ast.parse(Path(guards.__file__).read_text())
    functions = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and (
            node.name.startswith("_final_preseed_")
            or node.name == "validate_final_preseed"
        )
    ]
    assert functions
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        for function in functions
        for node in ast.walk(function)
    )


@pytest.mark.parametrize("version", ["1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0"])
def test_legacy_preseed_admission_does_not_require_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    monkeypatch.setattr(
        guards, "private_json", lambda path: {"schema_version": version}
    )
    guards.final_preseed_guard(tmp_path, tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX private runtime sources")
def test_config8_requires_closed_null_final_before_source_read(tmp_path: Path) -> None:
    config = dict.fromkeys(guards.epic_config_keys({"schema_version": "8.0"}))
    config.update(schema_version="8.0", approved_final_digest="legacy")
    path = tmp_path / "epic-runtime.json"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    with pytest.raises(guards.DeploymentError, match="RUNTIME_CONFIG_INVALID"):
        guards.final_preseed_guard(tmp_path, tmp_path)


@pytest.mark.parametrize("route", ["build_entry", "build_session", "build_runtime"])
@pytest.mark.parametrize(
    "damage",
    ["missing", "partial", "empty_settings", "empty_sources", "oversize", "link"],
)
@pytest.mark.skipif(os.name != "posix", reason="POSIX private runtime sources")
def test_installed_frozen_refuses_before_policy_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str, damage: str
) -> None:
    root, seed = fixture(tmp_path, monkeypatch)
    path = seed / "final-initialization" / "approval-pin.json"
    if damage == "missing":
        path.unlink()
    elif damage == "partial":
        path.write_text("{}")
    elif damage == "oversize":
        path.write_text(" " * 262145)
    elif damage == "link":
        path.unlink()
        path.symlink_to("approval.json")
    else:
        (
            seed
            / "final-preparation"
            / damage.removeprefix("empty_").replace("settings", "role-settings")
        ).mkdir(mode=0o700)
    state = seed
    if route == "build_runtime":
        tasks = seed / "tasks"
        tasks.mkdir(mode=0o700)
        state = tasks / "303"
        state.mkdir(mode=0o700)
        # Pre-import admission resolves the original seed before task factories.
        (state / "runtime-task.json").write_text(json.dumps({"schema_version": "3.0"}))
    installed = tmp_path / "runtime.py"
    installed.write_bytes(Path(guards.__file__).read_bytes())
    script = """
import runpy, sys
from pathlib import Path
runtime = runpy.run_path(sys.argv[1], run_name="frozen_runtime")
runtime["time"].time = lambda: 150
def prohibit(event, args):
    if event == "import" and (args[0] == "tools" or args[0].startswith("tools.")):
        raise AssertionError("candidate import occurred")
sys.addaudithook(prohibit)
try:
    runtime[sys.argv[2]](Path(sys.argv[3]), Path(sys.argv[4]))
except runtime["DeploymentError"] as error:
    assert str(error) == "FINAL_INITIALIZATION_SOURCE_INVALID", str(error)
else:
    raise AssertionError("preseed admitted")
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            str(installed),
            route,
            str(state),
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not (seed / "launch-control.json").exists()
    assert not (seed / "entry-run").exists()


@pytest.mark.parametrize(
    "action",
    [None, "start", "resume", "prepare", "discover", "status", "pause", "stop"],
)
@pytest.mark.skipif(os.name != "posix", reason="POSIX private runtime sources")
def test_frozen_entry_classifies_only_fixed_safe_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str | None
) -> None:
    root, seed = fixture(tmp_path, monkeypatch)
    (seed / "final-initialization" / "approval-pin.json").unlink()

    # Keep the real closed config and preseed validator; stop at the unchanged
    # original authority boundary to observe fixed classification before imports.
    def original_authority(*args: object, **kwargs: object) -> None:
        raise guards.DeploymentError("SYNTHETIC_ORIGINAL_AUTHORITY")

    monkeypatch.setattr(guards, "bootstrap_authority", original_authority)
    # validate_final_preseed also checks original authority, so source admission
    # classification is recorded by a non-authorizing rejecting sentinel here.
    calls = []

    def reject_preseed(state: Path, repository: Path) -> None:
        calls.append((state, repository))
        raise guards.DeploymentError("SYNTHETIC_PRESEED_REQUIRED")

    monkeypatch.setattr(guards, "validate_final_preseed", reject_preseed)
    safe = action in {"status", "pause", "stop"}
    with pytest.raises(
        guards.DeploymentError,
        match=(
            "SYNTHETIC_ORIGINAL_AUTHORITY" if safe else "SYNTHETIC_PRESEED_REQUIRED"
        ),
    ):
        guards.build_entry(seed, root, action=action)
    assert bool(calls) is not safe


@pytest.mark.parametrize("desired", ["RUNNING", "PAUSED", "STOPPED"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX private runtime sources")
def test_direct_control_keeps_safe_modes_without_preseed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desired: str
) -> None:
    from tools import agent_epic_entry as entry

    root, seed = fixture(tmp_path, monkeypatch)
    (seed / "final-initialization" / "approval-pin.json").unlink()
    observed = []

    def original_control(state: Path, repository: Path, **options: object) -> None:
        observed.append(options)

    monkeypatch.setattr(guards, "managed_launch_guard", original_control)
    if desired == "RUNNING":
        with pytest.raises(
            guards.DeploymentError, match="FINAL_INITIALIZATION_SOURCE_INVALID"
        ):
            entry.control(root, seed, desired)
        assert not (seed / "launch-control.json").exists()
    else:
        assert entry.control(root, seed, desired)["state"] == desired
    assert observed == [{"read_only": True, "allow_initial": True}]
    assert not (seed / "final-preparation" / "role-settings").exists()
    assert not (seed / "final-preparation" / "sources").exists()


@pytest.mark.parametrize("action", ["start", "resume"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX private runtime sources")
def test_direct_execute_refuses_before_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    from tools import agent_epic_entry as entry

    root, seed = fixture(tmp_path, monkeypatch)
    (seed / "final-initialization" / "approval-pin.json").unlink()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("discovery/factory entered")

    monkeypatch.setattr(entry, "discover", forbidden)
    with pytest.raises(
        guards.DeploymentError, match="FINAL_INITIALIZATION_SOURCE_INVALID"
    ):
        entry.execute(
            action,
            root,
            seed,
            repository="synthetic/repo",
            epic=303,
            registry=seed / "registry.json",
            installation=None,
        )
    assert not (seed / "entry-run").exists()


def test_config8_parser_rejects_legacy_final_approval() -> None:
    with pytest.raises(guards.DeploymentError, match="RUNTIME_CONFIG_INVALID"):
        guards.epic_config_keys(
            {"schema_version": "8.0", "approved_final_digest": "legacy"}
        )


@pytest.mark.skipif(os.name != "nt", reason="Native Windows unsupported-host refusal")
@pytest.mark.parametrize("route", ["build_entry", "build_session", "build_runtime"])
def test_native_windows_refuses_before_state_or_factory_namespace(
    tmp_path: Path, route: str
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(guards.DeploymentError, match="RUNTIME_ADAPTER_UNSUPPORTED"):
        getattr(guards, route)(state, tmp_path / "repository")
    assert list(state.iterdir()) == []
    assert not (tmp_path / "repository").exists()
