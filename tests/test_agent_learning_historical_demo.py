"""Synthetic adapter predicates; original native Session supplies full source proof."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_delivery as delivery
from tools import agent_epic_loop as loop
from tools import agent_learning as learning
from tools import agent_learning_records as records
from tools import agent_learning_sources as sources
from tools.agent_continuation import digest
from tools.agent_epic_final_runtime import digest as completion_digest
from tools.agent_epic_session_runtime import SessionRuntime
from tools.agent_learning_evidence import SourceReader
from tools.agent_learning_store import Store
from tools.agent_orchestrate import TaskContract

from tests.learning_session_primary_fixture import ROADMAP, Primary
from tests.test_agent_consumer_demo import _plan
from tests.test_agent_learning import profile
from tests.test_agent_learning_records import observation

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX private historical source"
)


def fixture(tmp_path: Path, *, visible: bool = False) -> dict[str, Any]:
    root = tmp_path / "repo"
    root.mkdir()

    def git(*argv: str) -> str:
        result = subprocess.run(
            ["git", *argv],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip()

    git("init")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    git("commit", "--allow-empty", "-m", "baseline")
    baseline = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-m", "original merged task")
    merge = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-m", "successor merged task")
    current = git("rev-parse", "HEAD")
    git("update-ref", "refs/heads/main", baseline)
    git("update-ref", "refs/heads/" + ROADMAP, merge)
    state = tmp_path / "state"
    store = loop.CheckpointStore(state, root)
    saved: dict[str, Any] = {
        "schema_version": "3.0",
        "repository": "OWNER/repo",
        "epic": 294,
        "task": 303,
        "pr": 303,
        "base_ref": ROADMAP,
        "base_sha": baseline,
        "head_ref": "codex/303-synthetic",
        "head_sha": merge,
        "policy_sha": baseline,
        "phase": "DEMO",
        "completed_phases": list(loop.PHASES[1:6]),
        "pending_phase": "TASK_DONE",
        "merge_sha": merge,
        "status": "RUNNING",
        "rate_limit_pause": None,
    }
    store.save(saved)
    owner = profile() | {"policy_sha": baseline}
    reader = SourceReader.__new__(SourceReader)
    reader.root, reader.seed, reader.owner = root, state, owner
    reader.config = {"approved_order": [303], "task_iterations": 0}
    # These unit dependencies isolate the adapter; installed-policy/source CI
    # provenance is exercised by the separate original frozen native Session.
    setattr(reader, "guard", lambda: None)
    contract = TaskContract(
        issue=303,
        epic=294,
        acceptance_criteria=("synthetic criterion",),
        base_ref=ROADMAP,
        base_sha=baseline,
        head_ref=saved["head_ref"],
        allowed_paths=("code.py",),
        task_class="runtime",
    )
    setattr(reader, "contract", lambda task: contract)
    plan = _plan() | {
        "repository": "OWNER/repo",
        "epic": 294,
        "task": 303,
        "baseline_sha": baseline,
        "roadmap_ref": ROADMAP,
        "roadmap_sha": merge,
    }
    if not visible:
        plan.update(
            consumer_visible=False,
            not_applicable_reason="synthetic docs",
            scenario=None,
            parameters=[],
            old_argv=[],
            new_argv=[],
        )
    session = SessionRuntime.__new__(SessionRuntime)
    session.state, session.root, session.config = state, root, reader.config
    session.wait_scope = None
    setattr(session, "_recipe", lambda task: {"demo_plan": plan})
    setattr(
        session,
        "_fresh_authority",
        lambda *args: (
            {},
            "",
            delivery.MandateContext(200, "OWNER", 150, 0, 0),
        ),
    )
    remote = tmp_path / "primary.git"
    git("clone", "--bare", str(root), str(remote))
    api = Primary.__new__(Primary)
    api.root, api.refs, api.sources, api.reads = (
        remote,
        {"main": baseline, ROADMAP: merge},
        {},
        [],
    )
    engine = sources.historical_runtime(reader, session, 303)
    runtime: Any = getattr(engine, "runtime", engine)
    runtime.github._read = api.read
    runtime._github_read = api.read
    if visible:
        completion = {
            key: plan[key]
            for key in ("repository", "epic", "task", "baseline_sha", "roadmap_sha")
        }
        completion.update(
            schema_version="1.0",
            plan_digest=completion_digest(plan),
            owner_identity="OWNER",
            completed_by_owner=True,
        )
        path = state / "demo-completion.json"
        path.write_text(json.dumps(completion))
        path.chmod(0o600)
    original = dict(runtime._demo(require_completion=True))
    row = observation(delivery=True)
    row["identity"].update(
        {
            key: saved[key]
            for key in (
                "repository",
                "epic",
                "task",
                "policy_sha",
                "head_sha",
                "merge_sha",
            )
        }
    )
    row["source"]["demo_digest"] = digest(original)
    row["record_id"] = records.observation_id(row)
    row = records.observation(learning.seal(row))
    with reader.ownership(state):
        with Store(
            state / "learning", root, state, owner, validators=records.VALIDATORS
        ) as journal:
            journal.append("observation", row, "record_id")
    return dict(
        reader=reader,
        engine=engine,
        runtime=runtime,
        api=api,
        saved=saved,
        original=original,
        baseline=baseline,
        merge=merge,
        current=current,
        state=state,
        git=git,
        plan=plan,
    )


def assess(value: dict[str, Any]) -> dict[str, object]:
    reader, engine = value["reader"], value["engine"]
    with reader.ownership(value["state"]):
        with engine._stage():
            return dict(engine._demo(require_completion=True))


def advance(value: dict[str, Any]) -> None:
    value["git"]("update-ref", "refs/heads/" + ROADMAP, value["current"])
    value["api"].git("update-ref", "refs/heads/" + ROADMAP, value["current"])
    value["api"].refs[ROADMAP] = value["current"]


@pytest.mark.parametrize("visible", [False, True])
def test_historical_demo_survives_proven_roadmap_advancement(
    tmp_path: Path, visible: bool
) -> None:
    value = fixture(tmp_path, visible=visible)
    advance(value)
    assert assess(value) == value["original"]
    assert value["api"].refs[ROADMAP] == value["current"]
    assert (
        "gh",
        "api",
        f"repos/OWNER/repo/compare/{value['merge']}...{value['current']}",
    ) in value["api"].reads


def test_current_live_demo_keeps_original_path_without_observation(
    tmp_path: Path,
) -> None:
    value = fixture(tmp_path)
    (value["state"] / "learning/records.jsonl").unlink()
    assert assess(value) == value["original"]
    assert not any("/compare/" in argv[-1] for argv in value["api"].reads)


@pytest.mark.parametrize("corruption", ["missing", "digest", "identity", "duplicate"])
def test_historical_demo_requires_original_observation(
    tmp_path: Path, corruption: str
) -> None:
    value = fixture(tmp_path)
    advance(value)
    path = value["state"] / "learning/records.jsonl"
    if corruption == "missing":
        path.unlink()
    else:
        item = json.loads(path.read_text())
        row = item["record"]
        if corruption == "digest":
            row["source"]["demo_digest"] = "f" * 64
        elif corruption == "identity":
            row["identity"]["head_sha"] = value["baseline"]
        else:
            row["identity"]["run_id"] = "f" * 32
        row["record_id"] = records.observation_id(row)
        item["record"] = records.observation(learning.seal(row))
        path.write_text(
            (path.read_text() if corruption == "duplicate" else "")
            + json.dumps(item)
            + "\n"
        )
    with pytest.raises(learning.LearningError):
        assess(value)


@pytest.mark.parametrize(
    "corruption", ["identical", "behind", "diverged", "base", "merge_base"]
)
def test_historical_demo_rejects_conflicting_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    value = fixture(tmp_path)
    advance(value)
    api = value["api"]
    endpoint = api._endpoint

    def altered(suffix: str) -> Any:
        result = endpoint(suffix)
        if suffix.startswith("compare/"):
            if corruption in {"identical", "behind", "diverged"}:
                result["status"] = corruption
            else:
                result["base_commit" if corruption == "base" else "merge_base_commit"][
                    "sha"
                ] = value["baseline"]
        return result

    monkeypatch.setattr(api, "_endpoint", altered)
    with pytest.raises(learning.LearningError):
        assess(value)


@pytest.mark.parametrize(
    "corruption",
    ["baseline", "completion", "plan", "task", "epic", "repository", "object"],
)
def test_historical_demo_rejects_stale_original_binding(
    tmp_path: Path, corruption: str
) -> None:
    value = fixture(tmp_path, visible=True)
    advance(value)
    if corruption == "baseline":
        value["api"].refs["main"] = value["current"]
    elif corruption == "completion":
        path = value["state"] / "demo-completion.json"
        receipt = json.loads(path.read_text())
        receipt["roadmap_sha"] = value["current"]
        path.write_text(json.dumps(receipt))
    elif corruption == "plan":
        value["plan"]["scenario"] = "changed original plan"
    else:
        saved = value["saved"].copy()
        if corruption == "task":
            saved["task"] = 305
        elif corruption == "epic":
            saved["epic"] = 295
            before = value["engine"].store.path.read_bytes()
            with pytest.raises(loop.LoopError, match="^CHECKPOINT_INVALID$"):
                value["engine"].store.save(saved)
            assert value["engine"].store.path.read_bytes() == before
            return
        elif corruption == "repository":
            saved["repository"] = "FOREIGN/repo"
        else:
            sha = value["merge"]
            (value["reader"].root / ".git/objects" / sha[:2] / sha[2:]).unlink()
        value["engine"].store.save(saved)
    with pytest.raises((learning.LearningError, loop.PhaseBlocked)):
        assess(value)


def test_historical_lineage_does_not_require_current_remote_tip_locally(
    tmp_path: Path,
) -> None:
    value = fixture(tmp_path)
    advance(value)
    sha = value["current"]
    (value["reader"].root / ".git/objects" / sha[:2] / sha[2:]).unlink()
    assert assess(value) == value["original"]


@pytest.mark.parametrize(
    "corruption", ["missing", "owner", "digest", "integer", "false"]
)
def test_historical_visible_demo_requires_exact_original_owner_completion(
    tmp_path: Path, corruption: str
) -> None:
    value = fixture(tmp_path, visible=True)
    advance(value)
    path = value["state"] / "demo-completion.json"
    if corruption == "missing":
        path.unlink()
    else:
        completion = json.loads(path.read_text())
        if corruption == "owner":
            completion["owner_identity"] = "OTHER"
        elif corruption == "digest":
            completion["plan_digest"] = "sha256:" + "f" * 64
        elif corruption == "integer":
            completion["completed_by_owner"] = 1
        else:
            completion["completed_by_owner"] = False
        path.write_text(json.dumps(completion))
    with pytest.raises(learning.LearningError):
        assess(value)


@pytest.mark.parametrize("drift", ["roadmap", "checkpoint"])
def test_historical_demo_rejects_drift_during_primary_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    value = fixture(tmp_path)
    advance(value)
    api = value["api"]
    endpoint = api._endpoint

    def altered(suffix: str) -> Any:
        result = endpoint(suffix)
        if suffix.startswith("compare/"):
            if drift == "roadmap":
                api.refs[ROADMAP] = value["merge"]
            else:
                changed = value["saved"] | {"head_sha": value["baseline"]}
                value["engine"].store.save(changed)
        return result

    monkeypatch.setattr(api, "_endpoint", altered)
    with pytest.raises(learning.LearningError):
        assess(value)
