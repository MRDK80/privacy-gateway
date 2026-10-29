"""Config7 is opt-in and inherited only through a verified managed seed."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_runtime_cli as guards
from tools.agent_learning_planning_catalog import catalog_digest
from tools.agent_orchestrate import StateStore

from tests.test_agent_learning import profile


def test_config7_closed_shape_keeps_legacy_keys() -> None:
    six = guards.epic_config_keys({"schema_version": "6.0"})
    seven = guards.epic_config_keys({"schema_version": "7.0"})
    assert seven == six | {"approved_learning_digest"}
    assert not guards.quota_enabled(
        {"schema_version": "7.0", "approved_quota_digest": None}
    )
    assert guards.quota_enabled(
        {"schema_version": "7.0", "approved_quota_digest": "sha256:" + "a" * 64}
    )


@pytest.mark.parametrize(
    "change", [None, "bool", "unknown", "unsafe_repo", "unbounded"]
)
def test_bootstrap_document_matches_closed_owner_profile(change: str | None) -> None:
    owner = profile()
    if change == "bool":
        owner["limits"]["max_records"] = True
    elif change == "unknown":
        owner["permissions"] = True
    elif change == "unsafe_repo":
        owner["repository"] = "../synthetic"
    elif change == "unbounded":
        owner["limits"]["max_store_bytes"] += 1
    if change:
        with pytest.raises(guards.DeploymentError):
            guards.learning_profile_document(owner)
    else:
        assert guards.learning_profile_document(owner) == owner


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner private profile")
@pytest.mark.parametrize("version", ["7.0", "8.0"])
def test_task3_inherits_exact_seed_and_catalog(tmp_path: Path, version: str) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    policy = state / "policy"
    schema = policy / "docs" / "schemas"
    schema.mkdir(mode=0o700, parents=True)
    catalog = {"schema_version": "1.0", "rules": []}
    StateStore(schema).save("agent-learning-catalog", catalog)
    StateStore(schema).save("agent-learning-planning-catalog", catalog)
    owner = profile()
    owner["catalog_digest"] = catalog_digest(catalog, catalog)
    config: dict[str, Any] = {
        key: None for key in guards.epic_config_keys({"schema_version": version})
    }
    config.update(
        schema_version=version,
        policy_root=str(policy),
        approved_learning_digest=guards.canonical_digest(owner),
        approved_mandate_digest=owner["mandate_digest"],
    )
    mandate = {
        k: owner[k]
        for k in ("repository", "epic", "roadmap_ref", "policy_sha", "owner_identity")
    }
    StateStore(state).save("epic-runtime", config)
    StateStore(state).save("learning-profile", owner)
    assert guards.learning_profile_guard(state, config, mandate) == owner
    tasks = state / "tasks"
    tasks.mkdir(mode=0o700)
    task = tasks / "302"
    task.mkdir(mode=0o700)
    child = {
        "schema_version": "3.0",
        "approved_handover_digest": "sha256:" + "e" * 64,
        "approved_continuation_digest": "sha256:" + "f" * 64,
        "approved_mandate_digest": owner["mandate_digest"],
        "policy_root": str(policy),
    }
    assert guards.learning_profile_guard(task, child, mandate) == owner
    StateStore(schema).save(
        "agent-learning-catalog", {"schema_version": "1.0", "rules": [{}]}
    )
    with pytest.raises(guards.DeploymentError, match="LEARNING_CATALOG_CHANGED"):
        guards.learning_profile_guard(task, child, mandate)
