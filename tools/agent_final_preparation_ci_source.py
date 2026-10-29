"""Lower neutral success-only roadmap sources; never candidate admission."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from tools import agent_epic_runtime_cli as guards
from tools import agent_epic_wait as wait
from tools.agent_continuation import digest
from tools.agent_coordinator_delivery import REQUIRED_CHECKS
from tools.agent_final_preparation_ci import (
    Comparison,
    PrimaryCI,
    PrimaryCIError,
    normalize,
)
from tools.agent_final_preparation_runtime import (
    _CurrentFinalSources,
    _OwnedCompletedSources,
)

CAP = 1_000_000


def _require(value: bool) -> None:
    if not value:
        raise PrimaryCIError("FINAL_PRIMARY_CI_INVALID")


def _integer(value: Any, maximum: int = 2**63 - 1) -> int:
    _require(type(value) is int and 1 <= value <= maximum)
    return int(value)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(items)
    _require(len(result) == len(items))
    return result


@dataclass(frozen=True, repr=False)
class RoadmapSourceCollection:
    comparison: Comparison
    primary: PrimaryCI
    originals: dict[str, Any]


class _PollSources:
    def __init__(self, session: Any) -> None:
        self.pinned = _CurrentFinalSources(session)
        self.session = session
        self.reader = self.pinned.reader
        _require(type(self.reader) is wait.Reader)
        self.completed = _OwnedCompletedSources(session)
        self.depth = self.reader._depth
        _require(type(self.depth) is int and self.depth > 0)
        self.reservation: tuple[str, int] | None = None
        self.check()

    def check(self) -> None:
        self.pinned.check()
        self.completed.check()
        reader = self.reader
        _require(reader._active_poll is True)
        _require(type(reader._depth) is int and reader._depth == self.depth > 0)
        expected = (
            reader.state
            / "read-waits"
            / digest(reader.identity).removeprefix("sha256:")
        )
        _require(reader.state == self.session.state and reader.directory == expected)
        _require(reader.root.resolve() == self.session.root.resolve())
        _require(reader.limits == self.pinned.scope.profile["limits"])
        reader._storage(create=False)
        path = expected / "wait.json"
        reader._private(path.lstat())
        # Existing no-follow, single-link, duplicate-key and cap+1 bounded reader.
        state = reader._validate(guards.learning_private_json(path, cap=CAP))
        reader._check(state)
        ci = state["ci"]
        _require(isinstance(ci, dict) and ci["status"] == "RESERVED")
        attempts = _integer(ci["attempts"], reader.limits["max_ci_polls"])
        _require(
            not any(row["status"] == "RESERVED" for row in state["requests"].values())
        )
        current = (ci["identity_digest"], attempts)
        if self.reservation is None:
            self.reservation = current
        _require(current == self.reservation)
        self.pinned.check()

    def api(self, suffix: str) -> dict[str, Any]:
        self.check()
        try:
            try:
                text = self.session._github_read(
                    (
                        "gh",
                        "api",
                        f"repos/{self.repo}" + ("/" + suffix if suffix else ""),
                    )
                )
            except UnicodeError:
                raise PrimaryCIError("FINAL_PRIMARY_CI_INVALID") from None
        finally:
            self.check()
        _require(isinstance(text, str))
        try:
            _require(len(text.encode("utf-8")) <= CAP)
            result = json.loads(text, object_pairs_hook=_pairs)
        except (ValueError, TypeError, RecursionError):
            raise PrimaryCIError("FINAL_PRIMARY_CI_INVALID") from None
        _require(isinstance(result, dict))
        return dict(result)

    @property
    def repo(self) -> str:
        return str(self.completed.wait_scope.mandate["repository"])

    def git(self, *args: str) -> bytes:
        self.check()
        remaining = self.reader.current_deadline - self.reader.now()
        _require(remaining > 0)
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.session.root,
                capture_output=True,
                check=False,
                timeout=min(30.0, remaining),
            )
        except (OSError, subprocess.SubprocessError):
            raise PrimaryCIError("FINAL_PRIMARY_CI_INVALID") from None
        finally:
            self.check()
        _require(result.returncode == 0 and type(result.stdout) is bytes)
        _require(len(result.stdout) <= CAP)
        return bytes(result.stdout)

    def refs(self, roadmap: str, policy: str) -> tuple[str, str]:
        _require(self.api("").get("full_name") == self.repo)
        values = []
        for ref in ("main", roadmap):
            original = self.api("git/ref/heads/" + ref)
            obj = original.get("object")
            _require(isinstance(obj, dict))
            assert isinstance(obj, dict)
            sha = obj.get("sha")
            _require(
                isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) is not None
            )
            values.append(str(sha))
        main, head = values
        self.git("merge-base", "--is-ancestor", policy, head)
        self.git("merge-base", "--is-ancestor", main, head)
        return main, head

    def graph(self, comparison: Comparison) -> dict[str, Any]:
        raw = self.api(f"commits/{comparison.sha}/check-runs?per_page=100")
        items = raw.get("check_runs")
        _require(isinstance(items, list) and len(items) <= 100)
        assert isinstance(items, list)
        _require(
            type(raw.get("total_count")) is int and raw["total_count"] == len(items)
        )
        _require(all(isinstance(item, dict) for item in items))
        _require(all(isinstance(item.get("name"), str) for item in items))
        selected = [item for item in items if item.get("name") in REQUIRED_CHECKS]
        _require(
            len(selected) == 5
            and {item["name"] for item in selected} == REQUIRED_CHECKS
        )
        suites: dict[int, Any] = {}
        runs: dict[int, Any] = {}
        jobs: dict[int, Any] = {}
        for check in selected:
            suite = check.get("check_suite")
            _require(isinstance(suite, dict))
            suite_id = _integer(suite.get("id"))
            match = re.fullmatch(
                re.escape(f"https://github.com/{self.repo}/actions/runs/")
                + r"([1-9][0-9]*)/job/([1-9][0-9]*)",
                str(check.get("details_url")),
            )
            _require(match is not None)
            assert match is not None
            _require(len(match[1]) <= 19 and len(match[2]) <= 19)
            run_id = _integer(int(match[1]))
            _integer(int(match[2]))
            if suite_id not in suites:
                suites[suite_id] = self.api(f"check-suites/{suite_id}")
            if run_id not in runs:
                run = self.api(f"actions/runs/{run_id}")
                attempt = _integer(run.get("run_attempt"), 1000)
                runs[run_id] = run
                jobs[run_id] = self.api(
                    f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
                )
        definitions = {}
        workflows = {}
        for filename in ("tests.yml", "pre-commit.yml"):
            definitions[filename] = self.git(
                "show", f"{comparison.policy_sha}:.github/workflows/{filename}"
            )
            workflows[filename] = self.api("actions/workflows/" + filename)
        return {
            "check_runs": raw,
            "suites": suites,
            "runs": runs,
            "jobs": jobs,
            "definitions": definitions,
            "workflows": workflows,
        }


def collect_roadmap_sources_owned(session: Any) -> RoadmapSourceCollection:
    """Called only inside an already-reserved original CI assessment.

    Success proves neutral current-ref provenance, never PR/create/candidate join.
    All non-success states refuse; approved detailed classifiers are not included.
    """
    sources = _PollSources(session)
    mandate = sources.completed.wait_scope.mandate
    repo, roadmap, policy = sources.repo, mandate["roadmap_ref"], mandate["policy_sha"]
    _require(
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is not None
        and ".." not in repo
    )
    _require(
        isinstance(roadmap, str)
        and re.fullmatch(r"roadmap/[0-9]+-[a-z0-9]+(?:-[a-z0-9]+)*", roadmap)
        is not None
    )
    _require(roadmap.startswith(f"roadmap/{mandate['epic']}-"))
    _require(
        isinstance(policy, str) and re.fullmatch(r"[0-9a-f]{40}", policy) is not None
    )
    # Local origin remains the admitted Session root, not installed-code cwd.
    origin_bytes = sources.git("remote", "get-url", "origin")
    try:
        origin = origin_bytes.decode("utf-8", "strict")
    except UnicodeError:
        raise PrimaryCIError("FINAL_PRIMARY_CI_INVALID") from None
    origin = origin.strip()
    match = re.search(r"(?:github\.com[/:])([^/]+/[^/]+?)(?:\.git)?$", origin)
    _require(match is not None and match.group(1) == repo)
    # Repository metadata endpoint has no trailing slash, preserving fixed grammar.
    refs = sources.refs(roadmap, policy)
    comparison = Comparison(repo, refs[1], policy)
    first = sources.graph(comparison)
    primary = normalize(comparison=comparison, **first)
    second = sources.graph(comparison)
    _require(normalize(comparison=comparison, **second) == primary and first == second)
    _require(sources.refs(roadmap, policy) == refs)
    sources.check()
    return RoadmapSourceCollection(comparison, primary, first)
