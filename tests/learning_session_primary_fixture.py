"""Synthetic primary endpoints for original multi-task delivery integration.

Callers retain original GhTaskTransport.effect/reconcile, delivery
ledger, authority, wait frames and source readers. No actual remote service.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote

from tools.agent_coordinator_delivery import REQUIRED_CHECKS

REPOSITORY = "OWNER/repo"
ROADMAP = "roadmap/294-autonomy"


@dataclass(frozen=True)
class Pull:
    number: int
    task: int
    head_ref: str
    head_sha: str
    base_ref: str
    base_sha: str
    merge_sha: str | None = None

    def rest(self) -> dict[str, Any]:
        repository = {"full_name": REPOSITORY}
        return {
            "number": self.number,
            "state": "closed" if self.merge_sha else "open",
            "merged": self.merge_sha is not None,
            "merge_commit_sha": self.merge_sha,
            "head": {
                "ref": self.head_ref,
                "sha": self.head_sha,
                "repo": repository,
            },
            "base": {
                "ref": self.base_ref,
                "sha": self.base_sha,
                "repo": repository,
            },
        }

    def view(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "url": f"https://github.com/{REPOSITORY}/pull/{self.number}",
            "state": "MERGED" if self.merge_sha else "OPEN",
            "baseRefName": self.base_ref,
            "baseRefOid": self.base_sha,
            "headRefName": self.head_ref,
            "headRefOid": self.head_sha,
            "files": [{"path": "code.py"}],
            "mergeCommit": {"oid": self.merge_sha} if self.merge_sha else None,
            "isCrossRepository": False,
        }


class Primary:
    """Observe fixture writes and serve exact historical per-SHA CI records.

    command() is a synthetic I/O boundary only. Bind it as transport.command and
    bind read() as primary reader. Keep transport.write_command/write_json so
    original before_write is invoked. Do not replace transport.effect/reconcile.
    """

    def __init__(
        self,
        root: Path,
        remote: Path,
        *,
        policy_sha: str,
        tasks: Sequence[int] = (301, 302, 304, 303, 305),
        initialize: bool = True,
    ) -> None:
        assert remote.parent.exists()
        assert not remote.exists() if initialize else remote.is_dir()
        self.root, self.remote = root, remote
        self.policy_sha = policy_sha
        self.refs = {"main": policy_sha, ROADMAP: policy_sha}
        self.tasks = tuple(tasks)
        self.issues: dict[int, dict[str, Any]] = {
            number: {
                "number": number,
                "title": f"Synthetic task {number}",
                "url": f"https://github.com/{REPOSITORY}/issues/{number}",
                "state": "OPEN",
                "parent": {"number": 294},
                "body": "synthetic criterion\n"
                if number == 301
                else "## Acceptance\n- synthetic criterion\n",
                "blockedBy": {"nodes": []},
            }
            for number in self.tasks
        }
        self.pulls: dict[int, Pull] = {}
        self.comments: list[dict[str, Any]] = []
        self.sources: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []
        self.reads: list[tuple[str, ...]] = []
        if initialize:
            self.git("init", "--bare", str(remote))
            self.git("push", "--", str(remote), f"{policy_sha}:refs/heads/main")
            self.git("push", "--", str(remote), f"{policy_sha}:refs/heads/{ROADMAP}")
        for filename, name, identifier in (
            ("tests.yml", "CI", 102),
            ("pre-commit.yml", "pre-commit", 101),
        ):
            self._retain(
                "actions/workflows/" + filename,
                {
                    "id": identifier,
                    "name": name,
                    "path": ".github/workflows/" + filename,
                    "state": "active",
                },
            )

    def git(self, *args: str) -> str:
        assert all(
            not any(
                marker in argument
                for marker in ("https://", "http://", "ssh://", "git@")
            )
            for argument in args
        ), "fixture Git cannot access network"
        if args and args[0] in {"push", "fetch", "ls-remote"}:
            assert str(self.remote) in args, "fixture Git remote must be local bare"
        return subprocess.check_output(
            ["git", *args],
            cwd=self.root,
            text=True,
            stderr=subprocess.PIPE,
            timeout=15,
            env={
                **os.environ,
                "GIT_AUTHOR_DATE": "1970-01-01T00:00:00Z",
                "GIT_COMMITTER_DATE": "1970-01-01T00:00:00Z",
            },
        ).strip()

    def _retain(self, key: str, value: Any) -> None:
        if key in self.sources:
            assert self.sources[key] == value, "historical primary identity changed"
        else:
            self.sources[key] = copy.deepcopy(value)

    def _ci(self, pull: Pull, *, post: bool) -> None:
        sha = pull.merge_sha if post else pull.head_sha
        assert sha is not None
        # Unique immutable IDs for each actual task and phase; names are pinned.
        prefix = pull.task * 1000 + (100 if post else 0)
        rows = []
        for index, name in enumerate(sorted(REQUIRED_CHECKS), 1):
            pre = name == "pre-commit"
            run = prefix + (20 if pre else 10)
            suite = prefix + (40 if pre else 30)
            job = prefix + index
            rows.append(
                {
                    "id": job,
                    "name": name,
                    "head_sha": sha,
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"id": 15368, "slug": "github-actions"},
                    "check_suite": {"id": suite},
                    "details_url": (
                        f"https://github.com/{REPOSITORY}/actions/runs/{run}/job/{job}"
                    ),
                }
            )
        self._retain(
            f"commits/{sha}/check-runs?per_page=100",
            {"total_count": 5, "check_runs": rows},
        )
        for pre in (False, True):
            run = prefix + (20 if pre else 10)
            suite = prefix + (40 if pre else 30)
            filename = "pre-commit.yml" if pre else "tests.yml"
            name = "pre-commit" if pre else "CI"
            self._retain(
                f"check-suites/{suite}",
                {
                    "id": suite,
                    "head_sha": sha,
                    "app": {"id": 15368},
                    "repository": {"full_name": REPOSITORY},
                },
            )
            self._retain(
                f"actions/runs/{run}",
                {
                    "id": run,
                    "head_sha": sha,
                    "event": "push" if post else "pull_request",
                    "status": "completed",
                    "conclusion": "success",
                    "run_attempt": 1,
                    "repository": {"full_name": REPOSITORY},
                    "workflow_id": 101 if pre else 102,
                    "name": name,
                    "path": ".github/workflows/" + filename,
                    "check_suite_id": suite,
                },
            )
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
                for row in rows
                if (row["name"] == "pre-commit") == pre
            ]
            self._retain(
                f"actions/runs/{run}/attempts/1/jobs?per_page=100",
                {"total_count": len(jobs), "jobs": jobs},
            )

    def owner_close_failed(self, task: int) -> None:
        """Synthetic external owner disposition, never Admission state recovery."""
        assert task in {302, 304} and self.issues[task]["state"] == "OPEN"
        self.issues[task]["state"] = "CLOSED"
        self.events.append({"kind": "owner-close-failed", "task": task})

    def _issue(self, task: int) -> dict[str, Any]:
        if task == 294:
            return {
                "number": 294,
                "title": "Synthetic epic",
                "state": "OPEN",
                "subIssues": {"nodes": [{"number": n} for n in self.tasks]},
            }
        return copy.deepcopy(self.issues[task])

    def _endpoint(self, suffix: str) -> Any:
        if suffix.endswith("/check-runs"):
            suffix += "?per_page=100"
        if suffix in self.sources:
            return copy.deepcopy(self.sources[suffix])
        if suffix == "":
            return {"full_name": REPOSITORY, "default_branch": "main"}
        if suffix == "branches?per_page=100":
            return [
                [
                    {"name": ref, "commit": {"sha": sha}}
                    for ref, sha in sorted(self.refs.items())
                ]
            ]
        if suffix.startswith("git/ref/heads/"):
            return {"object": {"sha": self.refs[suffix.removeprefix("git/ref/heads/")]}}
        if suffix.startswith("compare/"):
            base, head = suffix.removeprefix("compare/").split("...")

            def resolve(value: str) -> str:
                decoded = unquote(value)
                if len(decoded) == 40 and all(
                    ch in "0123456789abcdef" for ch in decoded
                ):
                    assert self.git("rev-parse", decoded + "^{commit}") == decoded
                    return decoded
                return self.refs[decoded]

            before, after = resolve(base), resolve(head)
            self.git("merge-base", "--is-ancestor", before, after)
            common = self.git("merge-base", before, after)
            return {
                "status": "identical" if before == after else "ahead",
                "base_commit": {"sha": before},
                "merge_base_commit": {"sha": common},
            }
        if suffix.startswith("pulls?"):
            query = parse_qs(suffix.split("?", 1)[1])
            head = query["head"][0].removeprefix("OWNER:")
            base = query["base"][0]
            return [
                [
                    pull.rest()
                    for pull in self.pulls.values()
                    if pull.head_ref == head and pull.base_ref == base
                ]
            ]
        if suffix.startswith("pulls/"):
            return self.pulls[int(suffix.split("/")[1])].rest()
        if suffix == "issues/294/comments?per_page=100":
            return [copy.deepcopy(self.comments)]
        raise AssertionError(f"unsupported synthetic primary endpoint: {suffix}")

    def read(self, argv: Sequence[str]) -> str:
        argv = tuple(argv)
        self.reads.append(argv)
        value: Any
        assert argv[0] == "gh"
        if argv[1:3] == ("issue", "view"):
            value = self._issue(int(argv[3]))
        elif argv[1:3] == ("pr", "view"):
            value = self.pulls[int(argv[3])].view()
        elif argv[1:3] == ("pr", "checks"):
            pull = self.pulls[int(argv[3])]
            rows = self.sources[f"commits/{pull.head_sha}/check-runs?per_page=100"]
            value = [
                {"name": row["name"], "state": "SUCCESS", "link": row["details_url"]}
                for row in rows["check_runs"]
            ]
        elif argv[1] == "api":
            endpoint = next(item for item in argv[2:] if item.startswith("repos/"))
            assert endpoint == f"repos/{REPOSITORY}" or endpoint.startswith(
                f"repos/{REPOSITORY}/"
            )
            suffix = endpoint.removeprefix(f"repos/{REPOSITORY}").lstrip("/")
            value = self._endpoint(suffix)
        else:
            raise AssertionError(f"unsupported synthetic primary read: {argv[:3]}")
        return json.dumps(value)

    def command(self, argv: Sequence[str]) -> str:
        """Native command boundary; called after original transport write guard."""
        argv = tuple(argv)
        if argv[0] == "git":
            args = list(argv[1:])
            remote = f"https://github.com/{REPOSITORY}.git"
            if remote in args:
                args[args.index(remote)] = str(self.remote)
                assert args[0] in {"push", "fetch", "ls-remote"}
                result = self.git(*args)
                if args[0] == "push":
                    sha, ref = args[-1].split(":", 1)
                    assert ref.startswith("refs/heads/codex/")
                    self.refs[ref.removeprefix("refs/heads/")] = sha
                    self.events.append({"kind": "push", "head_sha": sha, "ref": ref})
                return result
            return self.git(*args)
        assert argv[0] == "gh"
        if argv[1:3] == ("pr", "merge"):
            number = int(argv[3])
            pull = self.pulls[number]
            assert pull.merge_sha is None
            assert argv[argv.index("--match-head-commit") + 1] == pull.head_sha
            assert self.refs[ROADMAP] == pull.base_sha
            tree = self.git("rev-parse", pull.head_sha + "^{tree}")
            merge = self.git(
                "commit-tree",
                tree,
                "-p",
                pull.base_sha,
                "-p",
                pull.head_sha,
                "-m",
                "synthetic primary merge",
            )
            self.git("push", "--", str(self.remote), f"{merge}:refs/heads/{ROADMAP}")
            self.refs[ROADMAP] = merge
            pull = replace(pull, merge_sha=merge)
            self.pulls[number] = pull
            self._ci(pull, post=True)
            self.events.append({"kind": "merge", "task": pull.task, "merge_sha": merge})
            return ""
        if argv[1:3] == ("issue", "close"):
            task = int(argv[3])
            pull = self.pulls[task]
            assert pull.merge_sha is not None and self.issues[task]["state"] == "OPEN"
            self.issues[task]["state"] = "CLOSED"
            self.events.append({"kind": "close", "task": task})
            return ""
        if argv[1] == "api" and "--method" in argv:
            assert argv[argv.index("--method") + 1] == "POST"
            endpoint = next(item for item in argv[2:] if item.startswith("repos/"))
            fields = dict(
                item.split("=", 1)
                for index, item in enumerate(argv)
                if index and argv[index - 1] == "-f"
            )
            if endpoint == f"repos/{REPOSITORY}/pulls":
                head, base = fields["head"], fields["base"]
                task = int(head.split("/")[1].split("-")[0])
                assert task in {301, 303, 305} and task not in self.pulls
                assert self.issues[task]["state"] == "OPEN" and base == ROADMAP
                pull = Pull(task, task, head, self.refs[head], base, self.refs[base])
                self.pulls[task] = pull
                self._ci(pull, post=False)
                self.events.append(
                    {
                        "kind": "create-pr",
                        "task": task,
                        "head_sha": pull.head_sha,
                        "base_sha": pull.base_sha,
                    }
                )
                return json.dumps(pull.rest())
            assert endpoint == f"repos/{REPOSITORY}/issues/294/comments"
            comment = {"id": len(self.comments) + 1, "body": fields["body"]}
            self.comments.append(comment)
            self.events.append({"kind": "update-epic", "comment_id": comment["id"]})
            return json.dumps(comment)
        return self.read(argv)
