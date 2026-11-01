"""Isolated native subprocess review/cache tests; gate commands are synthetic.

This does not prove installed config8/factory, full task acceptance, or production.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tools import agent_final_preparation_records as final_records
from tools import agent_gate
from tools import agent_orchestrate as workflow
from tools.agent_continuation import digest
from tools.agent_continuation_backend import SettingsAdapter
from tools.agent_epic_loop import PhaseBlocked, RunnerLock
from tools.agent_final_preparation_review import AggregateReview
from tools.agent_final_preparation_role import RoleCounter
from tools.agent_final_preparation_storage import Store

from tests.test_agent_continuation import owner as continuation_profile
from tests.test_agent_continuation_backend import document
from tests.test_agent_final_preparation_records import IDENTITY, original
from tests.test_agent_gate_profile import _runner
from tests.test_agent_role_app_server import child

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX native source")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    effective: str = "low",
    exit_code: int = 0,
) -> tuple[Path, AggregateReview]:
    root, seed = tmp_path / "repo", tmp_path / "seed"
    root.mkdir()
    seed.mkdir(mode=0o700)
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Synthetic")
    git(root, "config", "user.email", "synthetic@example.invalid")
    (root / "AGENTS.md").write_text("synthetic trusted policy\n")
    (root / "CONTRIBUTING.md").write_text("synthetic process\n")
    (root / "unit.py").write_text("VALUE = 1\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "synthetic base")
    base = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-c", "roadmap/294-test")
    (root / "unit.py").write_text("VALUE = 2\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "synthetic change")
    head = git(root, "rev-parse", "HEAD")
    native = child(seed)
    verdict = {
        "schema_version": "1.0",
        "role": "controller",
        "task_issue": 294,
        "base_sha": base,
        "head_sha": head,
        "review_basis": {
            "trust_source_kind": "local_read_only_bundle",
            "head_policy_applied": False,
            "executor_self_assessment_treated_as_evidence_only": True,
        },
        "verdict": "PASS",
        "repair_iteration": 0,
        "escalation_reason": None,
        "blocking_findings": [],
        "notes": [],
    }
    native.write_text(
        native.read_text()
        .replace(
            '"instructionSources": [], "sandbox": {"type": "readOnly"}}})',
            '"instructionSources": [], "model": "synthetic-model", "reasoningEffort": '
            + repr(effective)
            + ', "sandbox": {"type": "readOnly"}}})',
        )
        .replace('json.dumps({"synthetic": True})', "json.dumps(" + repr(verdict) + ")")
    )
    owner = document()
    wrapper = seed / "native-adapter.py"
    wrapper.write_text(
        "import json, os, sys\n"
        "sys.path.insert(0, " + repr(str(Path.cwd())) + ")\n"
        "from tools import agent_role_app_server as roles\n"
        "from tools.agent_continuation import digest\n"
        "from tools.agent_continuation_backend import completed_receipt\n"
        "request = json.load(sys.stdin)\nowner = " + repr(owner) + "\nobserved = {}\n"
        "output = roles.run("
        + repr([sys.executable, str(native), "--ro-bind"])
        + ", role='controller', workdir="
        "__import__('pathlib').Path("
        + repr(str(seed))
        + "), prompt='synthetic', schema={}, model='synthetic-model', "
        "timeout=3, output_limit=20000, invocation=digest(request), "
        "backend_digest=digest(owner), "
        "environment=dict(os.environ), guard=lambda: None, reasoning='low', "
        "on_settings=observed.update)\n"
        "receipt, terminal = completed_receipt(owner, observed, "
        "role='controller', invocation=digest(request), output=output)\n"
        "print(json.dumps({'role_output': output, 'settings_receipt': receipt, "
        "'terminal_fact': terminal}))\n"
        "sys.exit(" + str(exit_code) + ")\n"
    )
    continuation = continuation_profile()
    continuation["backend_digest"] = digest(owner)
    continuation["limits"]["max_prompt_bytes"] = 262144
    command = [sys.executable, str(wrapper)]

    def guard() -> None:
        pass

    profile = final_records.seal({**original("profile"), "policy_sha": base})
    with RunnerLock(seed):
        store = Store(seed, root, profile, guard)
        # Synthetic original-start fixture, not autonomous initialization.
        store.write(
            "role-state",
            final_records.seal(
                {
                    "schema_version": "1.0",
                    **IDENTITY,
                    "calls_consumed": 0,
                    "pending_reservation_digest": None,
                }
            ),
        )
    adapter = SettingsAdapter(
        workflow.CommandAdapter(
            command, command, root=root, timeout_seconds=5, output_limit=65536
        ),
        owner,
        continuation,
        store.directory,
        guard,
    )
    review = AggregateReview(
        RoleCounter(store, str(IDENTITY["launch_digest"])), adapter
    )
    monkeypatch.setattr(agent_gate, "verify_environment", lambda _: None)
    monkeypatch.setattr(agent_gate, "_default_runner", _runner())
    return seed, review


def test_native_review_and_same_source_restart_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, review = setup(tmp_path, monkeypatch)
    with RunnerLock(seed):
        source = review.review(started_at=100, deadline_at=200)
        assert source["output"]["verdict"] == "PASS"
        assert source["settings_source"]["receipt"]["effective"]["reasoning"] == "low"
        _, reservation = review.counter.read()
        assert reservation is not None and reservation["status"] == "COMPLETED"
    with RunnerLock(seed):
        restarted = AggregateReview(
            RoleCounter(review.counter.store, str(IDENTITY["launch_digest"])),
            review.adapter,
        )
        assert restarted.review(started_at=110, deadline_at=210) == source


@pytest.mark.parametrize(
    "attack",
    [
        "exit",
        "settings",
        "missing-settings",
        "output",
        "main",
        "roadmap",
        "gate",
        "missing-state",
    ],
)
def test_native_original_source_failures_do_not_refund(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    seed, review = setup(
        tmp_path,
        monkeypatch,
        effective="high" if attack == "settings" else "low",
        exit_code=1 if attack == "exit" else 0,
    )
    with RunnerLock(seed):
        if attack in {"exit", "settings"}:
            with pytest.raises((workflow.OrchestrationError, PhaseBlocked)):
                review.review(started_at=100, deadline_at=200)
        else:
            source = review.review(started_at=100, deadline_at=200)
            payload = {
                **source["request"],
                "trusted_policy": source["policy"],
                "session_id": source["session_id"],
            }
            if attack == "missing-settings":
                (
                    review.adapter.store.store.directory
                    / ("receipt-" + digest(payload) + ".json")
                ).unlink()
            elif attack == "missing-state":
                (review.counter.store.directory / "role-state.json").unlink()
            elif attack in {"main", "roadmap"}:
                root = review.adapter.root
                if attack == "main":
                    git(
                        root,
                        "update-ref",
                        "refs/heads/main",
                        git(root, "rev-parse", "HEAD"),
                    )
                else:
                    (root / "unit.py").write_text("VALUE = 3\n")
                    git(root, "add", ".")
                    git(root, "commit", "-m", "synthetic drift")
            else:
                if attack == "output":
                    source["output"]["notes"] = ["synthetic altered output"]
                else:
                    source["request"]["gate_evidence"]["checks"][0]["status"] = "failed"
                review._store().save(review.key, source, cap=262144)
        with pytest.raises((workflow.OrchestrationError, PhaseBlocked)):
            review.review(started_at=110, deadline_at=210)


def test_missing_counter_retained_native_source_never_calls_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, review = setup(tmp_path, monkeypatch)
    with RunnerLock(seed):
        review.review(started_at=100, deadline_at=200)
        receipts = tuple(review.adapter.store.store.directory.glob("receipt-*.json"))
        assert len(receipts) == 1
        (review.counter.store.directory / "role-state.json").unlink()
        (review.counter.store.directory / (review.counter.key + ".json")).unlink()
        shutil.rmtree(review.directory)
        with pytest.raises(PhaseBlocked, match="FINAL_ROLE_STATE_MISSING"):
            review.review(started_at=110, deadline_at=210)
        assert (
            tuple(review.adapter.store.store.directory.glob("receipt-*.json"))
            == receipts
        )


def test_original_guard_replacement_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, review = setup(tmp_path, monkeypatch)
    review.adapter.guard = lambda: None
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        AggregateReview(review.counter, review.adapter)


def test_external_settings_namespace_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, review = setup(tmp_path, monkeypatch)
    review.adapter.state = seed
    with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
        AggregateReview(review.counter, review.adapter)


def test_entire_preparation_loss_cannot_initialize_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, review = setup(tmp_path, monkeypatch)
    with RunnerLock(seed):
        review.review(started_at=100, deadline_at=200)
        original_profile = review.counter.store.owner
        guard = review.counter.store.guard
        shutil.rmtree(review.counter.store.directory)
        store = Store(seed, review.adapter.root, original_profile, guard)
        restored = AggregateReview(
            RoleCounter(store, str(IDENTITY["launch_digest"])), review.adapter
        )
        with pytest.raises(PhaseBlocked, match="FINAL_ROLE_STATE_MISSING"):
            restored.review(started_at=110, deadline_at=210)
