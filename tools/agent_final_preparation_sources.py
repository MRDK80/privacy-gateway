"""Fixed current-owned preparation sources; supplied rows never grant readiness."""

from __future__ import annotations

import json
import os
import re
import stat
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any, NoReturn, cast

from tools import agent_coordinator_handover as handover
from tools import agent_epic_runtime_cli as guards
from tools import agent_final_preparation_ci as ci
from tools import agent_final_preparation_delivery as preparation_delivery
from tools import agent_final_preparation_records as records
from tools import agent_final_preparation_runtime as original
from tools.agent_continuation import digest
from tools.agent_coordinator_delivery import REQUIRED_CHECKS
from tools.agent_epic_final_transport import GhFinalTransport
from tools.agent_epic_loop import PhaseBlocked, RunnerLock
from tools.agent_epic_session_runtime import SessionRuntime
from tools.agent_final_learning_collector import read_learning_packet_owned


def _require(condition: bool) -> None:
    if not condition:
        raise PhaseBlocked("FINAL_APPROVAL_INVALID")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(items)
    _require(len(result) == len(items))
    return result


class CurrentPreparation:
    """Only a real owned config8 Session can construct this source envelope."""

    def __init__(self, session: SessionRuntime) -> None:
        _require(type(session) is SessionRuntime)
        _require(session.config.get("schema_version") == "8.0")
        self.session = session
        self.pins = original._CurrentFinalSources(session)
        _require(self.pins.stage == "FINAL:FINAL_GATE")
        self.completed = original._OwnedCompletedSources(session)
        self.profile = records.profile(self.completed.profile)
        self.iterations = self.completed._session_sources(
            guards.final_preparation_original_sources(session.state, session.root)
        )["iterations"]
        self.launch = self.completed.config["approved_launch_digest"]
        self.review = original.read_completed_learning_review_owned(session)
        self.identity = dict(self.review["identity"])
        self.baseline = self.assessment()
        self.check()

    def check(self) -> None:
        self.pins.check()
        self.completed.check()
        self.pins.check()

    def authority(self) -> Any:
        self.check()
        return self.session.authority(self.iterations)

    def assessment(self) -> dict[str, Any]:
        self.pins.check()
        projection = original.read_current_final_source_digests_owned(self.session)
        packet = read_learning_packet_owned(self.session)
        reviewed = original.read_completed_learning_review_owned(self.session)
        _require(reviewed == self.review)
        # The collector proves the complete approved domain, not a filtered list.
        # These labels describe retained use/comparability, never improvement.
        self.packet = packet
        result = records.assessment(records.seal({
            "schema_version": "1.0", "status": "READY", "machine_code": "OK",
            **projection, "learning_digest": packet["source_digest"],
        }))
        self.pins.check()
        return result

    def stable_assessment(self) -> dict[str, Any]:
        self.check()
        value = self.assessment()
        _require(value == self.baseline)
        self.check()
        return value

    def report_statuses(self) -> tuple[str, str]:
        self.stable_assessment()
        calls = {row["invocation_key"]: row for row in self.packet["calls"]}
        used = [calls[row["call_key"]] for row in self.packet["uses"]
                if row["ordered_lessons"]]
        if not used:
            return "NONE_APPLIED", "UNMEASURED"
        # Neutral measurement rows are WORKFLOW-only. A planning applied use
        # therefore cannot be promoted by those rows, even with matching hashes.
        if any(row["family"] != "WORKFLOW" for row in used):
            return "VERIFIED_APPLIED", "UNMEASURED"
        from tools import agent_learning_records
        from tools.agent_final_learning_domain import _FinalPlanningReader

        historical = _FinalPlanningReader(self.session)
        try:
            retained, _planning = historical._retained()
            historical._guard()
            evaluations = {
                digest(item["record"]): agent_learning_records.evaluation(
                    item["record"]
                ) for item in retained if item["kind"] == "evaluation"
            }
            measurements = {row["task"]: row for row in self.packet["measurements"]}
            comparable = True
            for task in {row["task"] for row in used}:
                row = measurements.get(task)
                if row is None or row["baseline_digest"] is None:
                    comparable = False
                    break
                evaluation = evaluations.get(row["evaluation_digest"])
                if evaluation is None or (
                    evaluation["hinted_digest"] != row["measurement_digest"]
                    or evaluation["baseline_digest"] != row["baseline_digest"]
                    or evaluation["comparison"]["status"] not in {
                        "NO_MEASURED_CHANGE", "MEASURED_IMPROVEMENT",
                    }
                    or evaluation["comparison"]["outcome_regressed"] is not False
                ):
                    comparable = False
                    break
            historical._guard()
            self.stable_assessment()
        finally:
            historical.reader.close()
        return (
            "VERIFIED_APPLIED",
            "VERIFIED_COMPARABLE" if comparable else "UNMEASURED",
        )

    def request(self) -> dict[str, Any]:
        self.stable_assessment()
        return records.request(records.seal({
            "schema_version": "1.0",
            **{key: self.review["identity"][key] for key in records.IDENTITY},
            "operation": "create_roadmap_pr",
            "repository": self.profile["repository"],
            "epic": self.profile["epic"],
            "roadmap_ref": self.profile["roadmap_ref"],
            "policy_sha": self.profile["policy_sha"],
            "main_sha": self.identity["main_sha"],
            "roadmap_sha": self.identity["roadmap_sha"],
            "preparation_digest": self.baseline["record_digest"],
        }, self_digest="operation_id"))

    def binding(self, request: Any) -> None:
        self.check()
        _require(records.request(dict(request)) == self.request())
        refs = self.session.github.branches(self.profile["repository"])
        _require(refs.get("main") == self.identity["main_sha"])
        _require(
            refs.get(self.profile["roadmap_ref"]) == self.identity["roadmap_sha"]
        )
        self.check()

    def _json(self, endpoint: str) -> Any:
        self.check()
        raw = self.session._github_read(("gh", "api", endpoint))
        try:
            result = json.loads(raw, object_pairs_hook=_pairs)
        except (ValueError, TypeError, RecursionError):
            raise PhaseBlocked("FINAL_STAGE_GATE_INVALID") from None
        self.check()
        return result

    def _pr(self, pr: int) -> dict[str, Any]:
        records.integer(pr, 2147483647, 1)
        repo = self.profile["repository"]
        value = self._json(f"repos/{repo}/pulls/{pr}")
        _require(isinstance(value, dict) and value.get("number") == pr)
        _require(value.get("state") == "open")
        _require(value.get("merged") is False)
        for key, ref, sha in (
            ("base", "main", self.identity["main_sha"]),
            ("head", self.profile["roadmap_ref"], self.identity["roadmap_sha"]),
        ):
            item = value.get(key)
            _require(isinstance(item, dict))
            assert isinstance(item, dict)
            _require(item.get("ref") == ref and item.get("sha") == sha)
            _require(isinstance(item.get("repo"), dict))
            _require(item["repo"].get("full_name") == repo)
        assert isinstance(value, dict)
        return value

    def transport(self) -> GhFinalTransport:
        def report() -> dict[str, Any]:
            self.stable_assessment()
            environment = original.read_completed_environment_owned(self.session)
            return {
                "gate": self.review["request"]["gate_evidence"],
                "review_verdict": self.review["output"]["verdict"],
                "environment": {
                    "python_version": environment["launcher_python_version"],
                },
            }

        def before_write(request: Any) -> None:
            self._transport_binding(request)
            from tools.agent_epic_final_delivery import authorization_code

            code = authorization_code(request, self.authority)
            if code is not None:
                raise PhaseBlocked(code)

        return GhFinalTransport(
            self.session.root, reporting=report, before_write=before_write,
            read=self.session._github_read,
        )

    def factual_receipt(self, pr: int) -> dict[str, Any]:
        self._pr(pr)
        request = self.request()
        self.binding(request)
        return {
            "pr": pr, "repository": request["repository"], "base_ref": "main",
            "head_ref": request["roadmap_ref"], "main_sha": request["main_sha"],
            "roadmap_sha": request["roadmap_sha"],
            "operation_id": request["operation_id"],
        }

    def _transport_binding(self, request: Any) -> None:
        current = self.request()
        self.binding(current)
        expected = preparation_delivery.transport_request(current)
        _require(type(request) is type(expected))
        actual_fields, expected_fields = asdict(request), asdict(expected)
        _require(actual_fields == expected_fields)
        _require(all(type(actual_fields[k]) is type(v)
                     for k, v in expected_fields.items()))
        self.check()

    def effect(self, request: Any) -> dict[str, Any]:
        self._transport_binding(request)
        raw = self.transport().effect(request)
        _require(type(raw.get("pr")) is int)
        pr = raw["pr"]
        assert type(pr) is int
        return self.factual_receipt(pr)

    def reconcile(self, request: Any) -> Any:
        self._transport_binding(request)
        status, receipt = self.transport().reconcile(request)
        if status != "APPLIED" or receipt is None:
            return status, None
        pr = receipt["pr"]
        assert type(pr) is int
        return "APPLIED", self.factual_receipt(pr)

    def ci_identity(self, pr: int) -> dict[str, Any]:
        return {
            "repository": self.profile["repository"], "epic": self.profile["epic"],
            "roadmap_sha": self.identity["roadmap_sha"],
            "main_sha": self.identity["main_sha"], "merge_sha": None, "pr": pr,
        }

    def primary_ci(self, pr: int) -> dict[str, Any]:
        """Fixed original endpoint reader, bracketed by current PR/ref sources."""
        with _current_dependency_gate(self.session):
            self.binding(self.request())
            before = self._pr(pr)
            repo, sha = self.profile["repository"], self.identity["roadmap_sha"]
            checks = self._json(f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
            _require(isinstance(checks, dict))
            rows = checks.get("check_runs")
            _require(isinstance(rows, list) and len(rows) <= 100)
            _require(type(checks.get("total_count")) is int)
            _require(checks["total_count"] == len(rows))
            selected = [r for r in rows if isinstance(r, dict)
                        and r.get("name") in REQUIRED_CHECKS]
            _require(len(selected) == len(REQUIRED_CHECKS))
            _require({r["name"] for r in selected} == set(REQUIRED_CHECKS))
            _require(all(type(r.get("id")) is int for r in selected))
            _require(len({r["id"] for r in selected}) == len(selected))
            suites: dict[int, Any] = {}
            runs: dict[int, Any] = {}
            jobs: dict[int, Any] = {}
            for row in selected:
                _require(type(row.get("id")) is int and row["id"] > 0)
                _require(row.get("head_sha") == sha)
                app = row.get("app")
                _require(isinstance(app, dict))
                assert isinstance(app, dict)
                records.integer(app.get("id"), 2**63 - 1, 1)
                _require(app.get("id") == 15368 and app.get("slug") == "github-actions")
                suite = row.get("check_suite")
                _require(isinstance(suite, dict))
                assert isinstance(suite, dict)
                sid = suite.get("id")
                _require(type(sid) is int and sid > 0)
                assert type(sid) is int
                match = re.fullmatch(
                    re.escape(f"https://github.com/{repo}/actions/runs/")
                    + r"([1-9][0-9]*)/job/([1-9][0-9]*)", row.get("details_url", "")
                )
                _require(match is not None)
                assert match is not None
                rid, jid = map(int, match.groups())
                _require(jid == row["id"])
                if sid not in suites:
                    suites[sid] = self._json(f"repos/{repo}/check-suites/{sid}")
                if rid not in runs:
                    run = self._json(f"repos/{repo}/actions/runs/{rid}")
                    _require(isinstance(run, dict))
                    attempt = run.get("run_attempt")
                    records.integer(attempt, 1000, 1)
                    runs[rid] = run
                    jobs[rid] = self._json(
                        f"repos/{repo}/actions/runs/{rid}/attempts/{attempt}"
                        "/jobs?per_page=100"
                    )
            definitions, workflows = {}, {}
            for filename in ("tests.yml", "pre-commit.yml"):
                definitions[filename] = handover._git(
                    self.session.root, "show",
                    f"{self.profile['policy_sha']}:.github/workflows/{filename}",
                ).encode()
                workflows[filename] = self._json(
                    f"repos/{repo}/actions/workflows/{filename}"
                )
            if any(row.get("status") != "completed" for row in selected):
                self._pending_sources(
                    selected, suites, runs, jobs, definitions, workflows,
                )
                _require(self._pr(pr) == before)
                self.binding(self.request())
                return {"status": "pending", "machine_code": "CI_PENDING"}
            try:
                normalized = ci.normalize(
                    comparison=ci.Comparison(repo, sha, self.profile["policy_sha"]),
                    check_runs=checks, suites=suites, runs=runs, jobs=jobs,
                    definitions=definitions, workflows=workflows,
                )
            except ci.PrimaryCIError:
                raise PhaseBlocked("FINAL_STAGE_GATE_INVALID") from None
            _require(self._pr(pr) == before)
            self.binding(self.request())
            return {
                "status": "PASS", "machine_code": "OK",
                "pr_ci_digest": digest({
                    "identity": self.ci_identity(pr), "primary": asdict(normalized),
                }),
            }

    def _pending_sources(
        self, checks: list[Any], suites: dict[int, Any], runs: dict[int, Any],
        jobs: dict[int, Any], definitions: dict[str, bytes],
        workflows: dict[str, Any],
    ) -> None:
        """Pending has its own complete identity proof, never a normalize fallback."""
        repo, sha = self.profile["repository"], self.identity["roadmap_sha"]
        known = {"queued", "in_progress", "completed"}
        try:
            normalized = {
                filename: ci._workflow(
                    definitions[filename], workflows[filename], filename,
                ) for filename in definitions
            }
        except ci.PrimaryCIError:
            raise PhaseBlocked("FINAL_STAGE_GATE_INVALID") from None
        for check in checks:
            _require(type(check.get("status")) is str)
            _require(check.get("status") in known)
            if check["status"] == "completed":
                _require(check.get("conclusion") == "success")
            else:
                _require(check.get("conclusion") is None)
            sid = check["check_suite"]["id"]
            suite = suites[sid]
            _require(isinstance(suite, dict))
            _require(type(suite.get("id")) is int)
            _require(suite.get("id") == sid and suite.get("head_sha") == sha)
            _require(isinstance(suite.get("repository"), dict))
            _require(suite["repository"].get("full_name") == repo)
            _require(isinstance(suite.get("app"), dict))
            records.integer(suite["app"].get("id"), 2**63 - 1, 1)
            _require(suite["app"].get("id") == 15368)
            run_id = int(check["details_url"].split("/runs/")[1].split("/")[0])
            run = runs[run_id]
            _require(isinstance(run, dict))
            _require(type(run.get("id")) is int)
            _require(run.get("id") == run_id and run.get("head_sha") == sha)
            _require(run.get("event") == "pull_request")
            _require(isinstance(run.get("repository"), dict))
            _require(run["repository"].get("full_name") == repo)
            _require(run.get("check_suite_id") == sid)
            _require(type(run.get("check_suite_id")) is int)
            _require(type(run.get("status")) is str)
            _require(run.get("status") in known)
            if run["status"] == "completed":
                _require(run.get("conclusion") == "success")
            else:
                _require(run.get("conclusion") is None)
            workflow = normalized[
                "pre-commit.yml" if check["name"] == "pre-commit" else "tests.yml"
            ]
            for key in ("path", "name"):
                _require(run.get(key) == workflow[key])
            records.integer(run.get("workflow_id"), 2**63 - 1, 1)
            records.integer(run.get("run_attempt"), 1000, 1)
            _require(run.get("workflow_id") == workflow["id"])
            listing = jobs[run_id]
            _require(isinstance(listing, dict))
            values = listing.get("jobs")
            _require(isinstance(values, list) and len(values) <= 100)
            _require(type(listing.get("total_count")) is int)
            _require(listing["total_count"] == len(values))
            _require(all(isinstance(r, dict) for r in values))
            found = [r for r in values if r.get("id") == check["id"]]
            _require(len(found) == 1)
            job = found[0]
            for key in ("id", "run_id"):
                records.integer(job.get(key), 2**63 - 1, 1)
            records.integer(job.get("run_attempt"), 1000, 1)
            _require(type(job.get("status")) is str)
            _require(job.get("status") == check["status"])
            _require(job.get("conclusion") == check.get("conclusion"))
            if run["status"] == "completed":
                _require(job["status"] == "completed")
            elif run["status"] == "queued":
                _require(job["status"] == "queued")
            for key, value in {
                "name": check["name"], "head_sha": sha, "run_id": run_id,
                "run_attempt": run["run_attempt"],
            }.items():
                _require(job.get(key) == value)
            _require(job.get("status") in known)
            if job["status"] == "completed":
                _require(job.get("conclusion") == "success")
            else:
                _require(job.get("conclusion") is None)


# Fixed source-owned historical FINAL lifetimes; never a current-read cache.
_CAPTURES: dict[int, _HistoricalCapture] = {}


def _capture_invalid() -> NoReturn:
    raise PhaseBlocked("FINAL_APPROVAL_INVALID") from None


def _close_capture_fds(fds: list[int], primary: BaseException | None) -> None:
    failure = primary
    for fd in reversed(fds):
        try:
            os.close(fd)
        except BaseException as error:
            if failure is None:
                failure = error
    if primary is None and failure is not None:
        _capture_invalid()


class _HistoricalCapture:
    MAX_ENTRIES = 45
    MAX_RESPONSE = 1_000_000

    def __init__(self, session: SessionRuntime) -> None:
        _require(type(session) is SessionRuntime)
        _require(session.config.get("schema_version") == "8.0")
        self.session = session
        self.scope = session.wait_scope
        _require(self.scope is not None)
        assert self.scope is not None
        _require(self.scope.active_stage == "FINAL:FINAL_GATE")
        self.reader = self.scope._active_reader()
        self.supervisor = RunnerLock.current_owned(session.state / "session-control")
        self.identity = (
            self.scope, self.reader, self.reader.guard, digest(self.scope.frame),
            digest(session.config), session.state.resolve(), session.root.resolve(),
        )
        self.values: dict[tuple[str, ...], bytes] = {}
        self.consumers: weakref.WeakValueDictionary[int, Any] = (
            weakref.WeakValueDictionary()
        )
        self._registering_consumer: Any | None = None
        self._registration_pulls: set[tuple[str, ...]] = set()
        self.allowed: set[tuple[str, ...]] = set()
        self.domain: str | None = None
        self.pins: Any = None
        self.segment: str | None = None
        self.previous: str | None = None
        self.initialized = False
        self.closed = False
        self.last_seen: float | None = None
        self.seed_identity: tuple[int, int] | None = None
        self._seen_wait: dict[str, Any] | None = None

    def _context(self) -> None:
        _require(not self.closed and _CAPTURES.get(id(self.session)) is self)
        _require(self.pins is not None and self.segment is not None)
        # The original full check already verifies active Reader and fresh guard.
        self.pins.check()
        self._identity_check()

    def _identity_check(self) -> None:
        scope = self.session.wait_scope
        _require(scope is self.scope and scope is not None)
        assert scope is not None
        stage = scope.active_stage
        _require(stage == "FINAL:FINAL_GATE" and stage in scope._contexts)
        assert stage is not None
        _require(scope._readers.get(stage) is self.reader)
        current = (
            scope, self.reader, self.reader.guard, digest(scope.frame),
            digest(self.session.config), self.session.state.resolve(),
            self.session.root.resolve(),
        )
        _require(current == self.identity and scope.config == self.session.config)
        _require(self.pins is not None and self.segment is not None)
        _require(
            RunnerLock.current_owned(self.session.state / "session-control")
            is self.supervisor is self.pins.supervisor
        )
        _require(RunnerLock.current_owned(self.session.state) is self.pins.owner)
        _require(self.reader.guard is self.pins.guard)
        _require(self.reader.current_deadline <= self.pins.deadline)
        _require((self.session._read_stage or self.session._current_stage()) == stage)

    @contextmanager
    def _segment(self, name: str) -> Iterator[None]:
        transitions = {
            None: {"preflight", "owner"}, "preflight": {"aggregate", "prepare"},
            "aggregate": {"postaggregate"}, "postaggregate": {"prepare"},
        }
        _require(not self.closed and self.segment is None)
        _require(name in transitions.get(self.previous, set()))
        ownership = RunnerLock.current_owned(self.session.state)
        pins = original._CurrentFinalSources(self.session)
        _require(pins.owner is ownership and pins.supervisor is self.supervisor)
        info = self.session.state.stat()
        identity = info.st_dev, info.st_ino
        _require(self.seed_identity is None or self.seed_identity == identity)
        self.seed_identity = identity
        self.pins, self.segment = pins, name
        try:
            self._context()
            yield
            self._context()
        finally:
            self.pins, self.segment = None, None
            self.previous = name

    def _existing_wait_record(self) -> dict[str, Any]:
        """Existing-only bounded raw wait read; no Reader storage/initial fallback."""
        fds: list[int] = []
        primary: BaseException | None = None
        try:
            state = self.session.state
            _require(state.is_absolute())
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY") | getattr(os, "O_NOFOLLOW")

            def directory(parent: int, name: str) -> int:
                fd = os.open(name, flags, dir_fd=parent)
                fds.append(fd)
                return fd

            first = os.open(state.anchor, flags)
            fds.append(first)
            seed = first
            for part in state.parts[1:]:
                seed = directory(seed, part)
            guards._final_preseed_private(seed, directory=True)
            base = directory(seed, "read-waits")
            guards._final_preseed_private(base, directory=True)
            name = digest(self.reader.identity).removeprefix("sha256:")
            stage = directory(base, name)
            guards._final_preseed_private(stage, directory=True)
            _require(self.reader.directory == state / "read-waits" / name)
            directories = [(fd, os.fstat(fd)) for fd in (seed, base, stage)]
            fd = os.open(
                "wait.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW")
                | getattr(os, "O_NONBLOCK"), dir_fd=stage,
            )
            fds.append(fd)

            def identity(info: os.stat_result) -> tuple[int, ...]:
                return (
                    info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                    info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                )

            before = os.fstat(fd)
            _require(
                stat.S_ISREG(before.st_mode)
                and before.st_uid == getattr(os, "getuid")()
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and before.st_size <= self.MAX_RESPONSE
            )
            _require(identity(before) == identity(os.stat(
                "wait.json", dir_fd=stage, follow_symlinks=False,
            )))
            raw = bytearray()
            while len(raw) <= self.MAX_RESPONSE:
                chunk = os.read(fd, min(65536, self.MAX_RESPONSE + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            _require(len(raw) <= self.MAX_RESPONSE)
            _require(identity(os.fstat(fd)) == identity(before))
            _require(identity(os.stat(
                "wait.json", dir_fd=stage, follow_symlinks=False,
            )) == identity(before))
            again = os.open(state.anchor, flags)
            fds.append(again)
            for part in state.parts[1:]:
                again = directory(again, part)
            reopened = [again]
            again = directory(again, "read-waits")
            reopened.append(again)
            reopened.append(directory(again, name))
            for (old_fd, old), new_fd in zip(directories, reopened, strict=True):
                new, retained = os.fstat(new_fd), os.fstat(old_fd)
                wanted = old.st_dev, old.st_ino, old.st_mode, old.st_uid
                _require((new.st_dev, new.st_ino, new.st_mode, new.st_uid) == wanted)
                _require((retained.st_dev, retained.st_ino,
                          retained.st_mode, retained.st_uid) == wanted)
            _require(identity(os.stat(
                "wait.json", dir_fd=reopened[-1], follow_symlinks=False,
            )) == identity(before))
            parse_failed = False
            value: Any = None
            try:
                value = json.loads(
                    raw.decode("utf-8", "strict"),
                    object_pairs_hook=guards._final_preseed_pairs,
                )
            except (ValueError, UnicodeError, TypeError, RecursionError):
                parse_failed = True
            if parse_failed:
                _capture_invalid()
            _require(type(value) is dict)
            assert isinstance(value, dict)
            return value
        except OSError as error:
            primary = error
        except BaseException as error:
            primary = error
            raise
        finally:
            _close_capture_fds(fds, primary)
        _capture_invalid()  # OS failure conversion occurs outside its except context.

    def _post_hit(self) -> dict[str, Any]:
        _require(not self.closed and _CAPTURES.get(id(self.session)) is self)
        value = self.reader._validate(self._existing_wait_record())
        _require(all(v["status"] == "READY" for v in value["requests"].values()))
        if self.reader._owner_original:
            _require(
                value["ci"] is not None and value["ci"]["status"] == "RESERVED"
                and self.reader._owner_assessing and self.reader._active_poll
            )
        # Reader._check advances only its in-memory argument, not persisted bytes.
        persisted = {
            **value, "requests": {k: dict(v) for k, v in value["requests"].items()},
            "ci": None if value["ci"] is None else dict(value["ci"]),
        }
        current = self.reader._check(value)  # Fresh original guard AFTER decode/read.
        _require(self.last_seen is None or current >= self.last_seen)
        self.last_seen = current
        old = getattr(self, "_seen_wait", None)
        if old is not None:
            _require(
                persisted["started_at"] == old["started_at"]
                and persisted["deadline_at"] <= old["deadline_at"]
                and persisted["last_seen_at"] >= old["last_seen_at"]
                and (old["ci"] is None or (
                    persisted["ci"] is not None
                    and persisted["ci"]["identity_digest"]
                    == old["ci"]["identity_digest"]
                    and persisted["ci"]["attempts"] >= old["ci"]["attempts"]
                ))
                and all(persisted["requests"].get(key) == item
                        for key, item in old["requests"].items())
            )
        self._identity_check()  # Direct predicates, no duplicate active-reader guard.
        self._seen_wait = persisted
        return persisted

    def register(self, consumer: Any) -> None:
        from tools.agent_final_learning_domain import (
            _final_domain_value,
            _FinalSourceReader,
            _PreAggregateFinalSourceReader,
        )
        from tools.agent_learning_delivery import DeliverySources

        try:
            _require(type(consumer) in {
                original._HistoricalStructuralSourceReader,
                _FinalSourceReader,
                _PreAggregateFinalSourceReader,
            })
            _require(consumer._historical_session is self.session)
            _require(self._registering_consumer is None)
            self._registering_consumer = consumer
            self._registration_pulls = set()
            self._context()
            tasks = _final_domain_value(consumer)[0]
            shas: set[str] = set()
            refs: set[str] = set()
            sources = []
            local = {}
            for task in tasks:
                _contract, item = DeliverySources(consumer)._ledger_local(task)
                _require(type(item["pr"]) is int and 0 < item["pr"] <= 2**63 - 1)
                local[task] = item
                shas.update((item["head_sha"], item["merge_sha"]))
                plan = self.session._recipe(task)["demo_plan"]
                refs.update((plan["baseline_ref"], plan["roadmap_ref"]))
                sources.append((task, item["head_sha"], item["merge_sha"],
                                item["pr"], item["source_digest"],
                                plan["baseline_ref"], plan["roadmap_ref"]))
            domain = digest((sources, consumer.owner))
            _require(self.domain is None or self.domain == domain)
            repo = consumer.owner["repository"]
            allowed: set[tuple[str, ...]] = {
                ("gh", "api", f"repos/{repo}/actions/workflows/{filename}")
                for filename in ("tests.yml", "pre-commit.yml")
            }
            pulls: set[tuple[str, ...]] = {
                ("gh", "api", f"repos/{repo}/pulls/{item['pr']}")
                for item in local.values()
            }
            allowed.update(pulls)
            for sha in shas:
                records.hash_value(sha, sha=True)
                for suffix in ("check-suites?per_page=100",
                               "check-runs?filter=all&per_page=100"):
                    allowed.add(("gh", "api", f"repos/{repo}/commits/{sha}/{suffix}"))
            for task in (*tasks, consumer.owner["epic"]):
                allowed.add((
                    "gh", "issue", "view", str(task), "--repo", repo, "--json",
                    "number,title,state,url,parent,subIssues,blockedBy",
                ))
            for ref in refs:
                _require(type(ref) is str and len(ref) <= 256)
                allowed.add(("gh", "api", f"repos/{repo}/git/ref/heads/{ref}"))
            self.allowed.update(allowed)
            self.consumers[id(consumer)] = consumer
            self.shas, self.refs, self.repository = shas, refs, repo

            self._registering_consumer = consumer
            self._registration_pulls = pulls
            for task in tasks:
                _require(consumer.delivery(task) == local[task])
            self._context()
            self.domain, self.tasks = domain, tasks
            self._registering_consumer = None
            self._registration_pulls = set()
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

    def _learn(self, argv: tuple[str, ...], raw: bytes) -> None:
        parse_failed = False
        value: Any = None
        try:
            value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs)
        except (ValueError, UnicodeError, TypeError, RecursionError):
            parse_failed = True
        if parse_failed:
            _capture_invalid()
        if len(argv) != 3:
            return
        endpoint = argv[2]
        repo = self.repository
        if "/commits/" in endpoint:
            _require(type(value) is dict)
            rows = value.get("check_runs", value.get("check_suites"))
            _require(type(rows) is list and len(rows) <= 100)
            _require(type(value.get("total_count")) is int)
            _require(value["total_count"] == len(rows))
            for row in rows:
                _require(type(row) is dict)
                if "check_runs" not in value:
                    continue
                name = row.get("name")
                _require(type(name) is str)
                if name not in REQUIRED_CHECKS:
                    continue
                _require(row.get("head_sha") in self.shas)
                suite = row.get("check_suite")
                _require(type(suite) is dict)
                sid = suite.get("id")
                records.integer(sid, 2**63 - 1, 1)
                url = row.get("details_url")
                _require(type(url) is str and len(url) <= 512)
                match = re.fullmatch(
                    re.escape(f"https://github.com/{repo}/actions/runs/")
                    + r"([1-9][0-9]*)/job/([1-9][0-9]*)", url,
                )
                _require(match is not None)
                assert match is not None
                rid, jid = map(int, match.groups())
                records.integer(rid, 2**63 - 1, 1)
                _require(type(row.get("id")) is int and row["id"] == jid)
                self.allowed.add(("gh", "api", f"repos/{repo}/check-suites/{sid}"))
                self.allowed.add(("gh", "api", f"repos/{repo}/actions/runs/{rid}"))
        elif "/actions/runs/" in endpoint and "/jobs?" not in endpoint:
            _require(type(value) is dict)
            rid = records.integer(value.get("id"), 2**63 - 1, 1)
            attempt = records.integer(value.get("run_attempt"), 1000, 1)
            _require(endpoint == f"repos/{repo}/actions/runs/{rid}")
            self.allowed.add((
                "gh", "api",
                f"repos/{repo}/actions/runs/{rid}/attempts/{attempt}/jobs?per_page=100",
            ))
        elif "/git/ref/heads/" in endpoint:
            _require(type(value) is dict and type(value.get("object")) is dict)
            current = records.hash_value(value["object"].get("sha"), sha=True)
            for sha in self.shas:
                self.allowed.add((
                    "gh", "api", f"repos/{repo}/compare/{sha}...{current}",
                ))

    def read(self, consumer: Any, argv: Any) -> str:
        self._context()
        _require(self.consumers.get(id(consumer)) is consumer)
        _require(consumer._historical_session is self.session)
        _require(type(argv) in {list, tuple} and len(argv) <= 8)
        _require(all(type(v) is str and len(v) <= 512 for v in argv))
        key = tuple(argv)
        _require(key in self.allowed)
        _require(
            self._registering_consumer is None
            or (consumer is self._registering_consumer
                and key in self._registration_pulls)
        )
        previous = None
        if key not in self.values:
            _require(len(self.values) < self.MAX_ENTRIES)
            if self.initialized:
                previous = self._post_hit()
            output = self.session._github_read(key)
            _require(type(output) is str and len(output) <= self.MAX_RESPONSE)
            encode_failed = False
            raw = b""
            try:
                raw = output.encode("utf-8", "strict")
            except UnicodeError:
                encode_failed = True
            if encode_failed:
                _capture_invalid()
            _require(len(raw) <= self.MAX_RESPONSE)
            self._learn(key, raw)
            after = self._post_hit()
            if previous is not None:
                _require(
                    after["started_at"] == previous["started_at"]
                    and after["deadline_at"] <= previous["deadline_at"]
                    and after["last_seen_at"] >= previous["last_seen_at"]
                    and after["ci"] == previous["ci"]
                    and len(after["requests"]) == len(previous["requests"]) + 1
                    and all(after["requests"].get(k) == v
                            for k, v in previous["requests"].items())
                )
            self.values[key] = raw
            self.initialized = True
        output = self.values[key].decode("utf-8", "strict")
        self._post_hit()
        return output

    def close(self) -> None:
        self.closed = True
        self.pins, self.segment = None, None
        self.values.clear()
        self.consumers.clear()
        self.allowed.clear()
        self.domain = None
        self._registering_consumer = None
        self._registration_pulls = set()
        if _CAPTURES.get(id(self.session)) is self:
            del _CAPTURES[id(self.session)]


@contextmanager
def _historical_capture_lifetime(session: SessionRuntime) -> Iterator[None]:
    _require(id(session) not in _CAPTURES)
    capture = _HistoricalCapture(session)
    _CAPTURES[id(session)] = capture
    try:
        yield
    finally:
        capture.close()


@contextmanager
def _capture_segment(session: SessionRuntime, name: str) -> Iterator[None]:
    capture = _CAPTURES.get(id(session))
    if capture is None:
        yield  # Local-only consumers outside a lifetime gain no remote fallback.
    else:
        with capture._segment(name):
            yield


def _register_historical_reader(session: SessionRuntime, consumer: Any) -> None:
    capture = _CAPTURES.get(id(session))
    if capture is not None:
        capture.register(consumer)


def _read_historical(session: SessionRuntime, consumer: Any, argv: Any) -> str:
    capture = _CAPTURES.get(id(session))
    _require(capture is not None)
    assert capture is not None
    return capture.read(consumer, argv)


@contextmanager
def _driver_preflight_segment(session: SessionRuntime) -> Iterator[None]:
    with _capture_segment(session, "preflight"):
        yield


@contextmanager
def _aggregate_capture_segment(session: SessionRuntime) -> Iterator[None]:
    with _capture_segment(session, "aggregate"):
        yield


@contextmanager
def _driver_postaggregate_segment(session: SessionRuntime) -> Iterator[None]:
    with _capture_segment(session, "postaggregate"):
        yield


@contextmanager
def _driver_preparation_segment(session: SessionRuntime) -> Iterator[None]:
    with _capture_segment(session, "prepare"):
        yield


@contextmanager
def _owner_capture_segment(session: SessionRuntime) -> Iterator[None]:
    with _capture_segment(session, "owner"):
        yield


def _is_final_historical_reader(consumer: Any, session: Any) -> bool:
    from tools.agent_final_learning_domain import (
        _FinalSourceReader,
        _PreAggregateFinalSourceReader,
    )

    return (
        type(session) is SessionRuntime
        and session.config.get("schema_version") == "8.0"
        and type(consumer) in {
            original._HistoricalStructuralSourceReader,
            _FinalSourceReader,
            _PreAggregateFinalSourceReader,
        }
        and consumer._historical_session is session
    )


@contextmanager
def _historical_demo_stage(consumer: Any, session: Any) -> Iterator[None]:
    _require(_is_final_historical_reader(consumer, session))
    capture = _CAPTURES.get(id(session))
    _require(capture is not None)
    assert capture is not None
    _require(capture.consumers.get(id(consumer)) is consumer)
    capture._context()
    consumer.guard()
    yield
    consumer.guard()
    capture._post_hit()


@contextmanager
def _current_dependency_gate(session: SessionRuntime) -> Iterator[None]:
    before = original._current_children_owned(session)
    capture = _CAPTURES.get(id(session))
    if capture is not None and capture.domain is not None:
        _require(before == capture.tasks)
    yield
    if capture is not None and capture.domain is not None:
        _require(before == capture.tasks)
    _require(original._current_children_owned(session) == before)


class _OwnerGateView:
    """Source-owned lexical proof; ordinary CurrentPreparation stays unchanged."""

    kind = "INVALID"

    def __init__(self, invocation: Any, request: Any = None) -> None:
        self.invocation, self.request = invocation, request
        self.source: CurrentPreparation | None = None
        self.phase = "NEW"
        self.optimized = False
        self.facts: dict[str, Any] = {}
        self.bindings: dict[str, Any] = {}
        self.approval_read: dict[str, Any] | None = None
        self.approval_graph: Any = None
        self.admitted_approval: Any = None
        self.result: dict[str, Any] | None = None
        self.derivations = _OwnerGateDerivations(self)

    def __enter__(self) -> _OwnerGateView:
        from copy import deepcopy

        from tools import agent_owner_final_admission as owner

        owner._owner_view_begin(self)
        try:
            self.source = CurrentPreparation(self.invocation.session)
            self.facts = deepcopy({
                key: getattr(self.source, key)
                for key in ("profile", "identity", "review", "baseline", "packet")
            })
            owner._owner_view_ready(self)
            return self
        except BaseException as error:
            owner._owner_view_finish(self, error)
            raise

    def __exit__(self, _kind: Any, error: Any, _traceback: Any) -> None:
        from tools import agent_owner_final_admission as owner

        owner._owner_view_finish(self, error)


class _HumanGateView(_OwnerGateView):
    kind = "HUMAN"


class _MergeAGateView(_OwnerGateView):
    kind = "MERGE_A"


class _OwnerAGateView(_OwnerGateView):
    kind = "OWNER_A"


class _OwnerBGateView(_OwnerGateView):
    kind = "OWNER_B"


class _OwnerGateDerivations:
    """Fixed read-only receiver for original getter bodies, never a transport."""

    def __init__(self, view: _OwnerGateView) -> None:
        self._view = view

    def check(self) -> None:
        from tools import agent_owner_final_admission as owner

        owner._owner_view_derivation(self._view, self)

    def _fact(self, name: str) -> Any:
        from copy import deepcopy

        self.check()
        return deepcopy(self._view.facts[name])

    @property
    def session(self) -> SessionRuntime:
        self.check()
        source = self._view.source
        _require(source is not None)
        assert source is not None
        return source.session

    @property
    def profile(self) -> dict[str, Any]:
        return cast(dict[str, Any], self._fact("profile"))

    @property
    def identity(self) -> dict[str, Any]:
        return cast(dict[str, Any], self._fact("identity"))

    @property
    def review(self) -> dict[str, Any]:
        return cast(dict[str, Any], self._fact("review"))

    @property
    def baseline(self) -> dict[str, Any]:
        return cast(dict[str, Any], self._fact("baseline"))

    @property
    def packet(self) -> dict[str, Any]:
        return cast(dict[str, Any], self._fact("packet"))

    def stable_assessment(self) -> dict[str, Any]:
        return self.baseline

    def request(self) -> dict[str, Any]:
        self.check()
        return CurrentPreparation.request(cast(Any, self))

    def binding(self, request: Any) -> None:
        self.check()
        CurrentPreparation.binding(cast(Any, self), request)

    def ci_identity(self, pr: int) -> dict[str, Any]:
        self.check()
        return CurrentPreparation.ci_identity(cast(Any, self), pr)

    def _json(self, endpoint: str) -> Any:
        self.check()
        source = self._view.source
        _require(source is not None)
        assert source is not None
        value = source._json(endpoint)
        self.check()
        return value

    def _pr(self, pr: int) -> dict[str, Any]:
        self.check()
        return CurrentPreparation._pr(cast(Any, self), pr)

    def _pending_sources(self, *args: Any, **kwargs: Any) -> None:
        self.check()
        CurrentPreparation._pending_sources(cast(Any, self), *args, **kwargs)

    def primary_ci(self, pr: int) -> dict[str, Any]:
        self.check()
        return CurrentPreparation.primary_ci(cast(Any, self), pr)

    def report_statuses(self) -> tuple[str, str]:
        self.check()
        # Executes the original retained-evaluation join and original finally.
        return CurrentPreparation.report_statuses(cast(Any, self))
