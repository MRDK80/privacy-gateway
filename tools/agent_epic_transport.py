"""Concrete narrow argv transport, invoked only by mandate-checked delivery."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from tools import agent_epic_delivery as delivery
from tools import agent_gate


class GhTaskTransport:
    """No shell, force push, admin merge, credentials or approval fallback."""

    def __init__(
        self,
        root: Path,
        *,
        reporting: Callable[[], Mapping[str, Any]] | None = None,
        before_write: Callable[[Any], None] | None = None,
        read: Callable[[Sequence[str]], str] | None = None,
    ) -> None:
        self.root = root
        self.reporting = reporting
        self.before_write = before_write
        self.read = read

    def write_command(self, argv: Sequence[str], request: Any) -> str:
        if self.before_write is not None:
            self.before_write(request)
        return self.command(argv)

    def write_json(self, argv: Sequence[str], request: Any) -> Any:
        if self.before_write is not None:
            self.before_write(request)
        return self.json(argv)

    def pr_body(
        self,
        *,
        base_ref: str,
        base_sha: str,
        head_ref: str,
        head_sha: str,
        epic: int,
        task: int | None,
    ) -> str:
        """No model prose or raw diagnostics are used as factual reporting."""
        try:
            if self.reporting is None:
                raise ValueError
            artifact = self.reporting()
            gate = artifact["gate"]
            if not agent_gate.gate_evidence_is_passing(gate):
                raise ValueError
            snapshot = gate["snapshot"]
            if snapshot["base_sha"] != base_sha:
                raise ValueError
            if snapshot["snapshot_commit"] != head_sha and (
                task is not None
                or self.command(("git", "rev-parse", f"{head_sha}^{{tree}}")).strip()
                != snapshot["tree_hash"]
            ):
                raise ValueError
            python = artifact["environment"]["python_version"]
            if (
                not isinstance(python, str)
                or re.fullmatch(r"\d+\.\d+\.\d+", python) is None
            ):
                raise ValueError
            project = tomllib.loads(
                self.command(("git", "show", f"{head_sha}:pyproject.toml"))
            )["project"]
            requires = project["requires-python"]
            if (
                not isinstance(requires, str)
                or re.fullmatch(r"[<>=!~0-9., ]+", requires) is None
            ):
                raise ValueError
            supported = sorted(
                item.rsplit(" :: ", 1)[-1]
                for item in project["classifiers"]
                if re.fullmatch(r"Programming Language :: Python :: \d+\.\d+", item)
            )
            workflow = yaml.safe_load(
                self.command(("git", "show", f"{head_sha}:.github/workflows/tests.yml"))
            )
            matrix = workflow["jobs"]["test"]["strategy"]["matrix"]
            if (
                sorted(matrix["python-version"]) != supported
                or set(matrix["os"]) != {"ubuntu-latest", "windows-latest"}
                or not supported
            ):
                raise ValueError
            checks = {item["id"]: item for item in gate["checks"]}
            pytest = checks["pytest"]["metrics"]
            mypy = checks["mypy"]["metrics"]["source_files"]
            passed, skipped = pytest["passed"], pytest.get("skipped") or 0
            errors = checks["ruff"]["metrics"].get("errors")
            if type(errors) is not int or errors != 0:
                raise ValueError
            if any(
                type(value) is not int or value < 0 for value in (passed, skipped, mypy)
            ):
                raise ValueError
            hooks = checks["pre-commit"]["metrics"]["hooks"]
            if not hooks or any(item["outcome"] != "passed" for item in hooks):
                raise ValueError
            review = artifact.get("review_receipt", {}).get(
                "verdict", artifact.get("review_verdict")
            )
            if review not in {"PASS", "PASS_WITH_NOTES"}:
                raise ValueError
            paths = self.command(
                ("git", "diff", "--name-only", base_sha, head_sha)
            ).splitlines()
            if not paths or any("\x00" in path for path in paths):
                raise ValueError
            label = "Task #" + str(task) if task is not None else "Roadmap"
            hook_results = ", ".join(item["id"] + ": passed" for item in hooks)
            body = (
                f"{label}; epic #{epic}.\n\n"
                f"base: {base_ref} @ {base_sha}\nhead: {head_ref} @ {head_sha}\n"
                f"Tested snapshot: {snapshot['snapshot_commit']}\n\n"
                f"Local environment: Python {python}; requires-python: {requires}.\n"
                f"Supported minors: {', '.join(supported)}; "
                "exact CI: each on Ubuntu/Windows latest, plus pre-commit.\n"
                'Dev installation: `python -m pip install -e ".[dev]"`; '
                "developer hooks per CONTRIBUTING.md.\n\n"
                f"pytest -q: {passed} passed, {skipped} skipped.\n"
                f"ruff check .: passed; errors {errors}.\n"
                f"mypy .: passed; {mypy} source files.\n"
                f"pre-commit run --all-files: {hook_results}.\n"
                f"Independent controller: {review}; GitHub Review absent.\n\n"
                "PR CI: pending; five primary GitHub check runs "
                "on the exact head are required.\n"
                "post-merge CI: pending; separate five checks "
                "on the actual merge SHA are required.\n"
                "No success is inferred from local results.\n\n"
                "Causal scope: only the approved task/roadmap diff and file grant.\n"
                "Changed files:\n" + "\n".join("- " + path for path in paths) + "\n\n"
                "Compatibility/release impact: no API/support or release authority "
                "is implied by this delivery. Review the exact diff; "
                "no compatibility guarantee is inferred from tests. "
                "No tag/release/publication occurs.\n"
                "Risks/exceptions: applicable demo completion, post-merge gates "
                "and owner-approved deployment remain separate. "
                "Synthetic tests are not production-pilot "
                "or real-input demo evidence.\n"
            )
            if len(body) > 80_000:
                raise ValueError
            return body
        except Exception:
            raise delivery.OutcomeUnknown from None

    def command(self, argv: Sequence[str]) -> str:
        try:
            result = subprocess.run(
                list(argv),
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise delivery.OutcomeUnknown from error
        if result.returncode != 0 or len(result.stdout) > 1_000_000:
            # A nonzero write exit is not proof that no side effect occurred.
            raise delivery.OutcomeUnknown
        return result.stdout

    def json(self, argv: Sequence[str]) -> Any:
        try:
            return json.loads(self.command(argv))
        except ValueError as error:
            raise delivery.OutcomeUnknown from error

    def read_json(self, argv: Sequence[str]) -> Any:
        if self.read is None:
            return self.json(argv)
        from tools.agent_github_read import ReadFailure

        try:
            return json.loads(self.read(argv))
        except (ValueError, ReadFailure) as error:
            raise delivery.OutcomeUnknown from error

    @staticmethod
    def comment(request: delivery.Request) -> str:
        digest = hashlib.sha256(request.operation_id.encode()).hexdigest()
        return (
            f"Task #{request.task}: post-merge CI verified at {request.merge_sha}.\n"
            f"<!-- epic-runner-operation:{digest} -->"
        )

    def effect(self, request: delivery.Request) -> dict[str, object]:
        if not delivery._valid_request(request):
            raise delivery.OutcomeUnknown
        repo = request.repository
        if request.operation == "push_task":
            self.write_command(
                (
                    "git",
                    "push",
                    "--",
                    f"https://github.com/{repo}.git",
                    f"{request.head_sha}:refs/heads/{request.head_ref}",
                ),
                request,
            )
        elif request.operation == "create_task_pr":
            self.write_json(
                (
                    "gh",
                    "api",
                    f"repos/{repo}/pulls",
                    "--method",
                    "POST",
                    "-f",
                    f"base={request.base_ref}",
                    "-f",
                    f"head={request.head_ref}",
                    "-f",
                    f"title=Task #{request.task}",
                    "-f",
                    "body="
                    + self.pr_body(
                        base_ref=request.base_ref,
                        base_sha=request.base_sha,
                        head_ref=request.head_ref,
                        head_sha=request.head_sha,
                        epic=request.epic,
                        task=request.task,
                    ),
                ),
                request,
            )
        elif request.operation == "merge_task_pr":
            self.write_command(
                (
                    "gh",
                    "pr",
                    "merge",
                    str(request.pr),
                    "--repo",
                    repo,
                    "--merge",
                    "--match-head-commit",
                    request.head_sha,
                ),
                request,
            )
        elif request.operation == "close_task":
            self.write_command(
                (
                    "gh",
                    "issue",
                    "close",
                    str(request.task),
                    "--repo",
                    repo,
                    "--reason",
                    "completed",
                ),
                request,
            )
        elif request.operation == "update_epic":
            self.write_json(
                (
                    "gh",
                    "api",
                    f"repos/{repo}/issues/{request.epic}/comments",
                    "--method",
                    "POST",
                    "-f",
                    f"body={self.comment(request)}",
                ),
                request,
            )
        else:
            raise delivery.OutcomeUnknown
        state, receipt = self.reconcile(request)
        if state != "APPLIED" or receipt is None:
            raise delivery.OutcomeUnknown
        return receipt

    def reconcile(
        self, request: delivery.Request
    ) -> tuple[str, dict[str, object] | None]:
        if not delivery._valid_request(request):
            return "UNKNOWN", None
        repo = request.repository
        try:
            if request.operation == "push_task":
                output = self.command(
                    (
                        "git",
                        "ls-remote",
                        "--",
                        f"https://github.com/{repo}.git",
                        f"refs/heads/{request.head_ref}",
                    )
                )
                lines = output.splitlines()
                if not lines:
                    return "NOT_APPLIED", None
                if lines == [f"{request.head_sha}\trefs/heads/{request.head_ref}"]:
                    return "APPLIED", {"head_sha": request.head_sha}
            elif request.operation == "create_task_pr":
                value = self.read_json(
                    (
                        "gh",
                        "api",
                        "--paginate",
                        "--slurp",
                        f"repos/{repo}/pulls?state=all&head={repo.split('/')[0]}:{request.head_ref}&base={request.base_ref}&per_page=100",
                    )
                )
                if not isinstance(value, list) or not all(
                    isinstance(page, list) for page in value
                ):
                    return "UNKNOWN", None
                matches = [item for page in value for item in page]
                if not matches:
                    return "NOT_APPLIED", None
                if len(matches) != 1:
                    return "UNKNOWN", None
                item = matches[0]
                if not isinstance(item, dict):
                    return "UNKNOWN", None
                head, base = item.get("head"), item.get("base")
                if not isinstance(head, dict) or not isinstance(base, dict):
                    return "UNKNOWN", None
                if (
                    item.get("state") == "open"
                    and isinstance(item.get("number"), int)
                    and not isinstance(item["number"], bool)
                    and item["number"] > 0
                    and head.get("sha") == request.head_sha
                    and head.get("ref") == request.head_ref
                    and isinstance(head.get("repo"), dict)
                    and head["repo"].get("full_name") == repo
                    and base.get("sha") == request.base_sha
                    and base.get("ref") == request.base_ref
                    and isinstance(base.get("repo"), dict)
                    and base["repo"].get("full_name") == repo
                ):
                    return "APPLIED", {
                        "pr": item["number"],
                        "head_sha": request.head_sha,
                    }
            elif request.operation == "merge_task_pr":
                value = self.read_json(
                    (
                        "gh",
                        "pr",
                        "view",
                        str(request.pr),
                        "--repo",
                        repo,
                        "--json",
                        "state,headRefOid,baseRefName,headRefName,mergeCommit",
                    )
                )
                if (
                    not isinstance(value, dict)
                    or value.get("headRefOid") != request.head_sha
                    or value.get("baseRefName") != request.base_ref
                    or value.get("headRefName") != request.head_ref
                ):
                    return "UNKNOWN", None
                if value.get("state") == "OPEN":
                    return "NOT_APPLIED", None
                merge = value.get("mergeCommit")
                sha = merge.get("oid") if isinstance(merge, dict) else None
                if (
                    value.get("state") == "MERGED"
                    and isinstance(sha, str)
                    and delivery.SHA_RE.fullmatch(sha)
                ):
                    return "APPLIED", {"merge_sha": sha}
            elif request.operation == "close_task":
                value = self.read_json(
                    (
                        "gh",
                        "issue",
                        "view",
                        str(request.task),
                        "--repo",
                        repo,
                        "--json",
                        "number,state,parent",
                    )
                )
                if (
                    not isinstance(value, dict)
                    or value.get("number") != request.task
                    or not isinstance(value.get("parent"), dict)
                    or value["parent"].get("number") != request.epic
                ):
                    return "UNKNOWN", None
                if value.get("state") == "CLOSED":
                    return "APPLIED", {
                        "task": request.task,
                        "merge_sha": request.merge_sha,
                    }
                if value.get("state") == "OPEN":
                    return "NOT_APPLIED", None
            elif request.operation == "update_epic":
                pages = self.read_json(
                    (
                        "gh",
                        "api",
                        "--paginate",
                        "--slurp",
                        f"repos/{repo}/issues/{request.epic}/comments?per_page=100",
                    )
                )
                if not isinstance(pages, list) or not all(
                    isinstance(page, list) for page in pages
                ):
                    return "UNKNOWN", None
                matches = [
                    item
                    for page in pages
                    for item in page
                    if isinstance(item, dict)
                    and item.get("body") == self.comment(request)
                ]
                if not matches:
                    return "NOT_APPLIED", None
                if len(matches) == 1 and isinstance(matches[0].get("id"), int):
                    return "APPLIED", {
                        "comment_id": matches[0]["id"],
                        "merge_sha": request.merge_sha,
                    }
        except delivery.OutcomeUnknown:
            pass
        return "UNKNOWN", None
