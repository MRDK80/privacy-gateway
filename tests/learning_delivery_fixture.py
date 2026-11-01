"""Fixed synthetic primary API data; not real GitHub/owner/CI evidence."""

from __future__ import annotations

import json
from typing import Any

from tools.agent_coordinator_delivery import REQUIRED_CHECKS


class PrimaryAPI:
    def __init__(self, base: str, head: str) -> None:
        self.base, self.head, self.merge = base, head, head
        self.calls: list[str] = []
        self.merged = False

    def rows(self, sha: str) -> list[dict[str, Any]]:
        result = []
        for i, name in enumerate(sorted(REQUIRED_CHECKS), 1):
            pre = name == "pre-commit"
            post = sha == self.merge and self.merge != self.head
            run = (200 if pre else 199) if post else (100 if pre else 99)
            result.append(
                {
                    "id": i,
                    "name": name,
                    "head_sha": sha,
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"id": 15368, "slug": "github-actions"},
                    "check_suite": {
                        "id": (151 if pre else 150) if post else (51 if pre else 50)
                    },
                    "details_url": f"https://github.com/OWNER/repo/actions/runs/{run}/job/{i}",
                }
            )
        return result

    def __call__(self, argv: tuple[str, ...]) -> str:
        suffix = argv[-1].removeprefix("repos/OWNER/repo/")
        self.calls.append(suffix)
        value: Any
        if suffix == "branches?per_page=100":
            value = [
                [
                    {"name": "main", "commit": {"sha": self.base}},
                    {
                        "name": "roadmap/294-autonomy",
                        "commit": {"sha": self.merge if self.merged else self.base},
                    },
                ]
            ]
        elif suffix.startswith("git/ref/heads/"):
            ref = suffix.removeprefix("git/ref/heads/")
            if ref not in {"main", "roadmap/294-autonomy"}:
                raise AssertionError("unsupported synthetic source ref")
            value = {"object": {"sha": self.base if ref == "main" else self.merge}}
        elif suffix == "pulls/7":
            value = {
                "number": 7,
                "merged": True,
                "merge_commit_sha": self.merge,
                "head": {"sha": self.head, "ref": "codex/301-test"},
                "base": {"ref": "roadmap/294-autonomy"},
            }
        elif suffix.startswith("commits/"):
            sha = suffix.split("/")[1]
            rows = self.rows(sha)
            if suffix.endswith("/check-suites?per_page=100"):
                suites = [
                    {
                        "id": identifier, "head_sha": sha,
                        "app": {"id": 15368},
                        "repository": {"full_name": "OWNER/repo"},
                    }
                    for identifier in sorted({r["check_suite"]["id"] for r in rows})
                ]
                value = {"total_count": len(suites), "check_suites": suites}
            elif suffix.endswith((
                "/check-runs", "/check-runs?per_page=100",
                "/check-runs?filter=all&per_page=100",
            )):
                value = {"total_count": len(rows), "check_runs": rows}
            else:
                raise AssertionError("unsupported synthetic commit endpoint")
        elif suffix.startswith("check-suites/"):
            suite = int(suffix.split("/")[1])
            pre = suite in {51, 151}
            sha = self.merge if suite >= 150 else self.head
            value = {
                "id": suite,
                "head_sha": sha,
                "app": {"id": 15368},
                "repository": {"full_name": "OWNER/repo"},
            }
        elif suffix.startswith("actions/workflows/"):
            filename = suffix.split("/")[-1]
            pre = filename == "pre-commit.yml"
            value = {
                "id": 101 if pre else 102,
                "name": "pre-commit" if pre else "CI",
                "path": ".github/workflows/" + filename,
                "state": "active",
            }
        elif suffix.startswith("actions/runs/"):
            parts = suffix.split("/")
            run = int(parts[2])
            pre = run in {100, 200}
            post = run >= 199
            sha = self.merge if post else self.head
            if len(parts) == 3:
                value = {
                    "id": run,
                    "head_sha": sha,
                    "event": "push" if post else "pull_request",
                    "status": "completed",
                    "conclusion": "success",
                    "run_attempt": 1,
                    "repository": {"full_name": "OWNER/repo"},
                    "workflow_id": 101 if pre else 102,
                    "name": "pre-commit" if pre else "CI",
                    "path": ".github/workflows/"
                    + ("pre-commit.yml" if pre else "tests.yml"),
                    "check_suite_id": (151 if pre else 150)
                    if post
                    else (51 if pre else 50),
                }
            else:
                jobs = [
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "run_id": run,
                        "head_sha": sha,
                        "run_attempt": 1,
                        "status": "completed",
                        "conclusion": "success",
                    }
                    for row in self.rows(sha)
                    if (row["name"] == "pre-commit") == pre
                ]
                value = {"total_count": len(jobs), "jobs": jobs}
        else:
            raise AssertionError("unsupported synthetic primary endpoint")
        return json.dumps(value)
