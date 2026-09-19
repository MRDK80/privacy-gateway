"""Adapter level contract for the #192 generation schema and null pruning."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "docs" / "schemas"


def _controller_request() -> dict[str, Any]:
    base_sha = "0" * 40
    snapshot_commit = "1" * 40
    return {
        "issue": 192,
        "base_sha": base_sha,
        "head_sha": snapshot_commit,
        "acceptance_criteria": ["schema valid"],
        "contract": {
            "issue": 192,
            "epic": 179,
            "acceptance_criteria": ["schema valid"],
            "base_ref": "roadmap/179-codex-adapters",
            "base_sha": base_sha,
            "head_ref": "fix/192-controller-schema",
            "allowed_paths": ["docs/example.md"],
            "permissions": {
                "commit": False,
                "push": False,
                "create_pr": False,
                "comment": False,
                "merge": False,
            },
            "max_repair_iterations": 2,
            "remaining_repair_iterations": 2,
            "max_minutes": 60,
            "task_class": "implementation",
        },
        "repair_iteration": 0,
        "gate_evidence": {
            "snapshot": {"base_sha": base_sha, "snapshot_commit": snapshot_commit}
        },
        "reviewed_state": {"snapshot_commit": snapshot_commit},
        "trusted_policy": {"AGENTS.md": "trusted policy"},
    }


FAKE_CODEX = (
    "import json",
    "import os",
    "import sys",
    "argv = sys.argv[1:]",
    "if '--version' in argv:",
    "    print('codex-cli 0.154.0')",
    "    raise SystemExit(0)",
    "sys.stdin.read()",
    "schema = argv[argv.index('--output-schema') + 1]",
    "copy = os.environ['PGW_TEST_SCHEMA_COPY']",
    "text = open(schema, encoding='utf-8').read()",
    "open(copy, 'w', encoding='utf-8').write(text)",
    "verdict = {",
    "    'schema_version': 'tampered',",
    "    'role': 'executor',",
    "    'task_issue': 1,",
    "    'base_sha': 'x' * 40,",
    "    'head_sha': 'y' * 40,",
    "    'review_basis': {",
    "        'trust_source_kind': 'base_sha',",
    "        'head_policy_applied': True,",
    "        'executor_self_assessment_treated_as_evidence_only': False,",
    "    },",
    "    'verdict': 'FAIL_RETRY',",
    "    'repair_iteration': 1,",
    "    'escalation_reason': None,",
    "    'blocking_findings': [{",
    "        'severity': 'high',",
    "        'requirement': 'r',",
    "        'location': None,",
    "        'evidence': 'e',",
    "        'required_fix': 'f',",
    "    }],",
    "    'notes': [],",
    "}",
    "open(argv[argv.index('-o') + 1], 'w', encoding='utf-8').write(",
    "    json.dumps(verdict)",
    ")",
)


def _defects(node: Any, path: str = "$") -> list[str]:
    """Collect strict structured output defects of the derived schema (#192)."""
    defects: list[str] = []
    if isinstance(node, dict):
        if "properties" in node:
            names = sorted(node["properties"])
            required = sorted(str(item) for item in node.get("required", []))
            if node.get("additionalProperties") is not False:
                defects.append(path + ":open-object")
            if names != required:
                defects.append(path + ":partial-required")
        for key, child in node.items():
            defects += _defects(child, f"{path}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            defects += _defects(child, f"{path}[{index}]")
    return defects


def test_controller_response_is_pruned_and_schema_is_strict(tmp_path: Path) -> None:
    fake = tmp_path / "fake_codex.py"
    fake.write_text(chr(10).join(FAKE_CODEX) + chr(10), encoding="utf-8")
    schema_copy = tmp_path / "generation-schema.json"
    request = _controller_request()
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "codex_adapter.py"),
            "--role",
            "controller",
            "--codex-command",
            json.dumps([sys.executable, str(fake)]),
            "--schema-dir",
            str(SCHEMA_DIR),
            "--root",
            str(REPO_ROOT),
        ],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={**os.environ, "PGW_TEST_SCHEMA_COPY": str(schema_copy)},
    )
    assert completed.returncode == 0, completed.stderr

    derived = json.loads(schema_copy.read_text(encoding="utf-8"))
    assert _defects(derived) == []
    finding = derived["properties"]["blocking_findings"]["items"]
    assert finding["properties"]["location"]["type"] == ["object", "null"]

    payload = json.loads(completed.stdout)
    assert "location" not in payload["blocking_findings"][0]
    assert payload["escalation_reason"] is None
    assert payload["schema_version"] == "1.0"
    assert payload["role"] == "controller"
    assert payload["task_issue"] == 192
    assert payload["review_basis"]["head_policy_applied"] is False
    assert payload["review_basis"]["trust_source_kind"] == "local_read_only_bundle"


FAKE_CODEX_PASS_WITH_FINDINGS = tuple(
    line.replace("'FAIL_RETRY'", "'PASS'") for line in FAKE_CODEX
)


def test_pruning_does_not_weaken_canonical_validation(tmp_path: Path) -> None:
    fake = tmp_path / "fake_codex_pass.py"
    fake.write_text(
        chr(10).join(FAKE_CODEX_PASS_WITH_FINDINGS) + chr(10), encoding="utf-8"
    )
    request = _controller_request()
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "codex_adapter.py"),
            "--role",
            "controller",
            "--codex-command",
            json.dumps([sys.executable, str(fake)]),
            "--schema-dir",
            str(SCHEMA_DIR),
            "--root",
            str(REPO_ROOT),
        ],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={**os.environ, "PGW_TEST_SCHEMA_COPY": str(tmp_path / "schema.json")},
    )
    assert completed.returncode == 20
    assert completed.stderr.splitlines()[0] == "SCHEMA_VIOLATION"
    assert completed.stdout == ""
