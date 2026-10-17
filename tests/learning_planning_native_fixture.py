"""Synthetic real Codex CLI planning processes under frozen config7 guards."""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from tools import agent_epic_runtime_cli as guards
from tools.agent_learning_planning_catalog import catalog_digest
from tools.agent_orchestrate import StateStore

from tests import test_agent_continuation_factory as factory
from tests.test_agent_learning import profile
from tests.test_agent_learning_planning_catalog import sample

NATIVE = r"""import hashlib, json, sys
from pathlib import Path

if "--version" in sys.argv:
    print("codex-cli 0.157.1")
    raise SystemExit(0)
assert sys.argv[1] == "exec"
assert (
    "--sandbox" in sys.argv and sys.argv[sys.argv.index("--sandbox") + 1] == "read-only"
)
raw = sys.stdin.read()
request = json.loads(
    raw.split("<untrusted-planning-data>\n", 1)[1].split(
        "\n</untrusted-planning-data>", 1
    )[0]
)
state = Path(__file__).resolve().parents[1]
identity = request["identity"]
task = identity["task"]
role = "planner" if request["proposal"] is None else "review"
saved = json.loads((state / "planning" / str(task) / "admission.json").read_text())
ordinal = saved["attempts"][role]
assert saved["pending"] == {
    "role": role,
    "attempt": ordinal,
    "request_digest": "sha256:"
    + hashlib.sha256(
        json.dumps(
            request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest(),
}
original = json.loads(
    (
        state
        / "planning"
        / str(task)
        / "learning-validation"
        / (
            ("planner" if role == "planner" else "planning_review")
            + "-"
            + str(ordinal)
            + "-request.json"
        )
    ).read_text()
)
assert original["payload"] == request
rows = []
for file in (state / "planning" / str(task) / "learning-invocations").glob("*.json"):
    if file.name.endswith((".problem.json", ".relevance.json")):
        continue
    row = json.loads(file.read_text())
    if row["status"] == "RESERVED" and row["role"] == (
        "planner" if role == "planner" else "planning_review"
    ):
        rows.append(row)
assert len(rows) == 1
row = rows[0]
problem = json.loads(
    (
        state
        / "planning"
        / str(task)
        / "learning-invocations"
        / (row["invocation_key"] + ".problem.json")
    ).read_text()
)
hinted = bool(request["untrusted_verified_hints"]["hints"])
assert (problem["source_kind"] == "PLANNING_VALIDATION") == hinted
if role == "planner":
    evidence = request["evidence"]
    plan = {
        "schema_version": "2.0",
        "repository": identity["repository"],
        "epic": identity["epic"],
        "task": task,
        "policy_sha": identity["policy_sha"],
        "criteria_source": {
            "kind": "github_issue",
            "issue": task,
            "state": "OPEN",
            "body_sha256": evidence["issue_body_digest"],
        },
        "acceptance_criteria": request["criteria"],
        "delivery_criterion_indices": [],
        "default_ref": "main",
        "default_sha": evidence["default_sha"],
        "base_ref": request["task_profile"]["demo_template"]["roadmap_ref"],
        "base_sha": evidence["base_sha"],
        "head_ref": "codex/" + str(task) + "-native",
        "causal_scope": request["grant"]["causal_scope"],
        "allowed_paths": request["grant"]["allowed_paths"],
        "changes_policy_or_architecture": False,
        "task_class": request["task_profile"]["task_class"],
        "budgets": {
            "max_minutes": 5,
            "max_repair_iterations": 2,
            "max_report_chars": 10000,
        },
    }
    output = {
        "schema_version": "1.0",
        "plan": plan,
        "demo_plan": request["task_profile"]["demo_template"],
        "criteria_mapping": [
            {
                "criterion_index": i,
                "allowed_paths": request["grant"]["allowed_paths"],
                "reason": "Synthetic scoped change",
            }
            for i in range(len(request["criteria"]))
        ],
    }
    if task == 302:
        output["criteria_mapping"] = []
else:
    output = {
        "schema_version": "1.0",
        "plan_digest": "sha256:"
        + hashlib.sha256(
            json.dumps(
                request["proposal"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
        "verdict": "PASS",
        "findings": [],
    }
    if task == 304:
        output["plan_digest"] = "sha256:" + "0" * 64
Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(output))
with (state / "native-planning-observations.jsonl").open("a") as stream:
    stream.write(
        json.dumps(
            {
                "task": task,
                "role": role,
                "invocation_key": row["invocation_key"],
                "hinted": hinted,
            }
        )
        + "\n"
    )
"""


def prepared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    source = tmp_path / "source"
    shutil.copytree(
        Path.cwd() / "tools",
        source / "tools",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(Path.cwd() / "docs/schemas", source / "docs/schemas")
    shutil.copytree(Path.cwd() / ".github/workflows", source / ".github/workflows")
    catalog = sample()
    first = catalog["rules"][0]
    first.update(
        rule_id="planning-criteria", task_class="feature", problem_class="criteria"
    )
    first["environment"]["python_minor"] = (
        f"{sys.version_info.major}.{sys.version_info.minor}"
    )
    second = copy.deepcopy(first)
    second.update(
        rule_id="planning-review",
        role="controller",
        problem_class="review",
        validator="REVIEW_V1",
        failure_code="PLANNING_REVIEW_INVALID",
        symptom_code="PLANNING_REVIEW_INVALID",
    )
    catalog["rules"].append(second)
    empty = {"schema_version": "1.0", "rules": []}
    (source / "docs/schemas/agent-learning-catalog.json").write_text(json.dumps(empty))
    (source / "docs/schemas/agent-learning-planning-catalog.json").write_text(
        json.dumps(catalog)
    )
    monkeypatch.setattr(factory, "SOURCE", source)
    value = factory.build(
        tmp_path / "fixture",
        extra_tasks=(302, 304, 303),
        delivery_enabled=True,
        with_demo=True,
        with_planning_policy=True,
    )
    state = Path(value["state"])
    store = StateStore(state)
    config = store.load("epic-runtime")
    adapter = store.load("runtime-adapter")
    launch = store.load("launch")
    mandate = store.load("mandate")
    plans = store.load("approved-plans")
    assert (
        config is not None
        and adapter is not None
        and launch is not None
        and mandate is not None
        and plans is not None
    )
    learning = profile()
    learning.update(
        policy_sha=value["sha"],
        mandate_digest=config["approved_mandate_digest"],
        catalog_digest=catalog_digest(empty, catalog),
    )
    store.save("learning-profile", learning)
    template = plans["tasks"]["301"]["demo_plan"]
    planning = {
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
        "mandate_digest": config["approved_mandate_digest"],
        "tasks": {
            str(task): {
                "criteria_sections": ["Acceptance"],
                "task_class": "feature",
                "demo_template": {**copy.deepcopy(template), "task": task},
            }
            for task in (301, 302, 304, 303)
        },
        "limits": {
            "max_planner_attempts": 2,
            "max_review_attempts": 2,
            "max_input_chars": 100000,
            "max_output_chars": 20000,
            "max_paths": 4,
        },
    }
    store.save("planning-profile", planning)
    config.update(
        schema_version="7.0",
        approved_learning_digest=guards.canonical_digest(learning),
        approved_planning_digest=guards.canonical_digest(planning),
    )
    launch["runtime_config_digest"] = guards.launch_config_digest(config, adapter)
    config["approved_launch_digest"] = guards.canonical_digest(launch)
    store.save("epic-runtime", config)
    store.save("launch", launch)
    control = store.load("launch-control")
    assert control is not None
    control["launch_digest"] = config["approved_launch_digest"]
    store.save("launch-control", control)
    binary = state / "planning-native-bin"
    binary.mkdir(mode=0o700)
    command = binary / "codex"
    command.write_text("#!" + sys.executable + "\n" + NATIVE)
    command.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    return value
