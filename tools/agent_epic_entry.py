"""Installed chat entry; no authority is inferred from a chat or candidate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools import agent_epic_install as install
from tools import agent_epic_loop as loop
from tools import agent_epic_runtime_cli as guards
from tools import agent_orchestrate as workflow


def _lock_directory(state: Path, root: Path, name: str) -> Path:
    path = state / name
    if path.is_symlink():
        raise guards.DeploymentError("UNSAFE_RUNTIME_CONFIG")
    path.mkdir(mode=0o700, exist_ok=True)
    return guards.secure_directory(path, root)


def prepare(root: Path, installation: Path, state: Path) -> dict[str, str]:
    """Materialize a reviewable candidate without signing or activating it."""
    state = guards.secure_directory(state, root)
    installed, _ = install._validate(root, installation)
    config = guards.private_json(state / "epic-runtime.json")
    if (
        set(config) != guards.EPIC_CONFIG_KEYS | {"approved_launch_digest"}
        or config.get("schema_version") != "2.0"
    ):
        raise guards.DeploymentError("LAUNCH_PROFILE_REQUIRED")
    mandate = guards.private_json(state / "mandate.json")
    guards.bootstrap_authority(config, mandate, epic=True)
    if (
        installed["repository"] != mandate["repository"]
        or installed["policy_sha"] != mandate["policy_sha"]
        or Path(config["policy_root"]) != installation / "policy"
    ):
        raise guards.DeploymentError("POLICY_PROVENANCE_MISMATCH")
    adapter = loop.CommandRuntimeAdapter.load(
        state / "runtime-adapter.json", root, state
    )
    command = adapter.epic_command
    if (
        command is None
        or command[2:]
        != (
            "epic_resume",
            "--state-dir",
            str(state),
            "--repository-root",
            str(root.resolve()),
        )
        or Path(command[1]).read_bytes() != (installation / "runtime.py").read_bytes()
    ):
        raise guards.DeploymentError("UNTRUSTED_RUNTIME_COMMAND")
    value = {
        "schema_version": "1.0",
        **{
            key: mandate[key]
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        },
        "installation_digest": guards.canonical_digest(installed),
        "runtime_config_digest": guards.launch_config_digest(
            config, guards.private_json(state / "runtime-adapter.json")
        ),
        "mandate_digest": config["approved_mandate_digest"],
    }
    with loop.RunnerLock(_lock_directory(state, root, "launch-control-lock")):
        if (state / "launch-control.json").exists() or (
            state / "launch-control.json"
        ).is_symlink():
            raise guards.DeploymentError("LAUNCH_ALREADY_ACTIVE")
        if (state / "launch.json").exists() or (state / "launch.json").is_symlink():
            if guards.private_json(state / "launch.json") != value:
                raise guards.DeploymentError("LAUNCH_CANDIDATE_CONFLICT")
        else:
            workflow.StateStore(state).save("launch", value)
    return {"status": "PREPARED", "candidate_digest": guards.canonical_digest(value)}


def control(
    root: Path, state: Path, desired: str, *, expected_generation: int | None = None
) -> dict[str, Any]:
    """Serialize owner control without resetting task progress or authority."""
    if desired not in {"RUNNING", "PAUSED", "STOPPED"}:
        raise guards.DeploymentError("LAUNCH_CONTROL_INVALID")
    state = guards.secure_directory(state, root)
    with loop.RunnerLock(_lock_directory(state, root, "launch-control-lock")):
        guards.managed_launch_guard(state, root, read_only=True, allow_initial=True)
        path = state / "launch-control.json"
        previous = guards.private_json(path) if path.exists() else None
        if (
            previous is not None
            and previous["state"] == "STOPPED"
            and desired != "STOPPED"
        ):
            raise guards.DeploymentError("OWNER_STOPPED")
        generation = previous["generation"] if previous is not None else -1
        if expected_generation is not None and expected_generation != generation:
            raise guards.DeploymentError("OWNER_CONTROL_CHANGED")
        value = {
            "schema_version": "1.0",
            "launch_digest": guards.private_json(state / "epic-runtime.json")[
                "approved_launch_digest"
            ],
            "state": desired,
            "generation": generation + 1,
        }
        workflow.StateStore(state).save("launch-control", value)
    return value


def discover(
    root: Path,
    repository: str,
    epic: int,
    registry: Path,
    *,
    github: Any = None,
    issues: Any = None,
) -> dict[str, Any]:
    """Read only explicit registry and canonical Git/GitHub structural facts."""
    import re
    import subprocess

    from tools import agent_coordinator_branch as branch
    from tools import agent_epic_queue as queue
    from tools.agent_epic_session import structural_queue

    if (
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None
        or type(epic) is not int
        or epic <= 0
    ):
        raise guards.DeploymentError("ISSUE_MISMATCH")
    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise guards.DeploymentError("REPOSITORY_IDENTITY_MISMATCH") from None
    if remote not in {
        f"https://github.com/{repository}",
        f"https://github.com/{repository}.git",
        f"git@github.com:{repository}.git",
        f"git@github.com:{repository}",
    }:
        raise guards.DeploymentError("REPOSITORY_IDENTITY_MISMATCH")
    guards.secure_directory(registry.parent, root)
    registered = guards.private_json(registry)
    if (
        set(registered) != {"schema_version", "runs"}
        or registered["schema_version"] != "1.0"
        or not isinstance(registered["runs"], list)
    ):
        raise guards.DeploymentError("RUN_REGISTRY_INVALID")
    matches = []
    for item in registered["runs"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"repository", "epic", "state_directory"}
            or not isinstance(item["repository"], str)
            or type(item["epic"]) is not int
            or not isinstance(item["state_directory"], str)
        ):
            raise guards.DeploymentError("RUN_REGISTRY_INVALID")
        if item["repository"] == repository and item["epic"] == epic:
            matches.append(item)
    if len(matches) != 1:
        raise guards.DeploymentError(
            "PRIVATE_RUN_MISSING" if not matches else "PRIVATE_RUN_AMBIGUOUS"
        )
    state = guards.secure_directory(Path(matches[0]["state_directory"]), root)
    client = github or branch.GitHubCLI()
    source = issues or queue.GhClient()
    try:
        repo = client.repository(repository)
        refs = client.branches(repository)
        choices = [ref for ref in refs if ref.startswith(f"roadmap/{epic}-")]
        default = repo.get("defaultBranchRef")
        if (
            repo.get("nameWithOwner") != repository
            or not isinstance(default, dict)
            or len(choices) != 1
        ):
            raise guards.DeploymentError("ROADMAP_AMBIGUOUS")
        roadmap = choices[0]
        if client.compare(repository, default["name"], roadmap) not in {
            "ahead",
            "identical",
        }:
            raise guards.DeploymentError("ROADMAP_STALE")
        parent = source.issue(repository, epic)
        children = parent.get("subIssues", {}).get("nodes")
        if (
            parent.get("number") != epic
            or parent.get("state") != "OPEN"
            or not isinstance(children, list)
        ):
            raise guards.DeploymentError("ISSUE_MISMATCH")
        order: list[int] = []
        for item in children:
            number = item.get("number") if isinstance(item, dict) else None
            if type(number) is not int or number <= 0:
                raise guards.DeploymentError("ISSUE_MISMATCH")
            order.append(number)
        if not order:
            raise guards.DeploymentError("ISSUE_MISMATCH")
        result = structural_queue(
            source, repository=repository, epic=epic, approved_order=order
        )
        mandate = guards.private_json(state / "mandate.json")
        if (
            mandate.get("repository") != repository
            or mandate.get("epic") != epic
            or mandate.get("roadmap_ref") != roadmap
        ):
            raise guards.DeploymentError("LAUNCH_IDENTITY_MISMATCH")
    except guards.DeploymentError:
        raise
    except Exception:
        raise guards.DeploymentError("DISCOVERY_FAILED") from None
    return {
        "schema_version": "1.0",
        "status": "DISCOVERED",
        "repository": repository,
        "epic": epic,
        "roadmap_ref": roadmap,
        "roadmap_sha": refs[roadmap],
        "queue_state": result.state,
        "selected_task": result.selected.number if result.selected else None,
        "issue_url": f"https://github.com/{repository}/issues/{epic}",
    }


def _active_store(state: Path, root: Path) -> loop.CheckpointStore:
    seed = loop.CheckpointStore(state, root)
    saved = seed.load()
    if saved is None:
        raise guards.DeploymentError("PINNED_CHECKPOINT_REQUIRED")
    path = state / "session-control" / "cursor.json"
    if not path.exists():
        return seed
    cursor = guards.private_json(path)
    task = cursor.get("current_task")
    if (
        type(task) is not int
        or task <= 0
        or any(
            cursor.get(key) != saved[key]
            for key in ("repository", "epic", "policy_sha")
        )
    ):
        raise guards.DeploymentError("SESSION_CURSOR_INVALID")
    return (
        seed
        if task == saved["task"]
        else loop.CheckpointStore(state / "tasks" / str(task), root)
    )


def status(root: Path, state: Path) -> dict[str, Any]:
    state = guards.secure_directory(state, root)
    guards.managed_launch_guard(state, root, read_only=True, allow_initial=True)
    launch = guards.private_json(state / "launch.json")
    saved = _active_store(state, root).load()
    if saved is None or any(
        saved[key] != launch[key] for key in ("repository", "epic", "policy_sha")
    ):
        raise guards.DeploymentError("CHECKPOINT_IDENTITY_MISMATCH")
    control_path = state / "launch-control.json"
    mode = (
        guards.private_json(control_path)["state"]
        if control_path.exists()
        else "NOT_STARTED"
    )
    links = [
        f"https://github.com/{launch['repository']}/issues/{launch['epic']}",
        f"https://github.com/{launch['repository']}/issues/{saved['task']}",
    ]
    if saved["pr"] is not None:
        links.append(f"https://github.com/{launch['repository']}/pull/{saved['pr']}")
    return {
        "schema_version": "1.0",
        "status": "STATUS",
        "machine_code": "OK",
        "repository": launch["repository"],
        "epic": launch["epic"],
        "task": saved["task"],
        "phase": saved["phase"],
        "checkpoint_status": saved["status"],
        "control_state": mode,
        "links": links,
    }


def run(root: Path, state: Path, action: str) -> dict[str, Any]:
    if action not in {"start", "resume"}:
        raise guards.DeploymentError("ENTRY_ACTION_INVALID")
    state = guards.secure_directory(state, root)
    with (
        loop.RunnerLock(_lock_directory(state, root, "entry-run")),
        loop.RunnerLock(_lock_directory(state, root, "session-control")) as supervisor,
    ):
        guards.managed_launch_guard(state, root, read_only=True, allow_initial=True)
        existing = state / "launch-control.json"
        before = guards.private_json(existing) if existing.exists() else None
        mode = before["state"] if before is not None else "NOT_STARTED"
        if mode == "STOPPED":
            raise guards.DeploymentError("OWNER_STOPPED")
        if action == "start" and mode != "NOT_STARTED":
            raise guards.DeploymentError(
                "OWNER_PAUSED" if mode == "PAUSED" else "LAUNCH_ALREADY_STARTED"
            )
        if action == "resume" and mode == "NOT_STARTED":
            raise guards.DeploymentError("LAUNCH_NOT_STARTED")
        config = guards.private_json(state / "epic-runtime.json")
        mandate = guards.private_json(state / "mandate.json")
        guards.bootstrap_authority(config, mandate, epic=True)
        installation = Path(config["policy_root"]).parent
        report = install.readiness(
            root,
            installation,
            state,
            epic=True,
            github_identity=config["owner_identity"],
        )
        if report["status"] != "READY":
            raise guards.DeploymentError("LAUNCH_READINESS_BLOCKED")
        active = _active_store(state, root)
        saved = active.load()
        assert saved is not None
        if saved["pending_phase"] is not None:
            read_adapter = loop.CommandRuntimeAdapter.load(
                active.directory / "runtime-adapter.json", root, active.directory
            )
            outcome, _ = read_adapter.reconcile(saved["pending_phase"])
            if outcome not in {"APPLIED", "NOT_APPLIED"}:
                raise guards.DeploymentError("ESCALATE_UNKNOWN_OUTCOME")
        adapter = loop.CommandRuntimeAdapter.load(
            state / "runtime-adapter.json", root, state
        )
        control(
            root,
            state,
            "RUNNING",
            expected_generation=before["generation"] if before is not None else -1,
        )
        adapter.supervisor_lock = supervisor
        result = adapter.resume_epic()
        output = status(root, state)
        mode = output["control_state"]
        output["status"] = result["status"] if mode == "RUNNING" else "BLOCKED"
        output["machine_code"] = (
            result["machine_code"] if mode == "RUNNING" else "OWNER_" + mode
        )
        return output


def execute(
    action: str,
    root: Path,
    state: Path,
    *,
    repository: str | None,
    epic: int | None,
    registry: Path | None,
    installation: Path | None,
    background: bool = False,
) -> dict[str, Any]:
    if background:
        raise guards.DeploymentError("BACKGROUND_WAKEUP_UNVERIFIED")
    if action == "discover":
        if repository is None or epic is None or registry is None:
            raise guards.DeploymentError("DISCOVERY_BINDING_REQUIRED")
        return discover(root, repository, epic, registry)
    if action == "prepare":
        if installation is None:
            raise guards.DeploymentError("INSTALLATION_BINDING_REQUIRED")
        return prepare(root, installation, state)
    if action == "status":
        return status(root, state)
    if action in {"pause", "stop"}:
        control(root, state, "PAUSED" if action == "pause" else "STOPPED")
        return status(root, state)
    if (
        action not in {"start", "resume"}
        or repository is None
        or epic is None
        or registry is None
    ):
        raise guards.DeploymentError("DISCOVERY_BINDING_REQUIRED")
    discover(root, repository, epic, registry)
    registered = guards.private_json(registry)
    match = [
        item
        for item in registered["runs"]
        if item["repository"] == repository and item["epic"] == epic
    ]
    if Path(match[0]["state_directory"]).resolve() != state.resolve():
        raise guards.DeploymentError("LAUNCH_IDENTITY_MISMATCH")
    return run(root, state, action)
