"""Real preparation storage regression before config8 driver implementation."""

import os
import time
from pathlib import Path

import pytest
from tools import agent_coordinator_handover as handover_source
from tools import agent_epic_runtime_cli as source_guards
from tools import agent_final_preparation_delivery as original_delivery
from tools import agent_final_preparation_records as records
from tools import agent_final_preparation_runtime as original_runtime
from tools.agent_epic_loop import PhaseBlocked, RunnerLock
from tools.agent_final_preparation_storage import Store

from tests.test_agent_final_owner_wait_records import anchor, state
from tests.test_agent_final_preparation_records import original


@pytest.mark.skipif(os.name != "posix", reason="POSIX owned preparation storage")
@pytest.mark.parametrize(
    "case",
    [
        "schema2_store_dispatch", "crash_after_role_reservation",
        "crash_after_candidate_before_wait", "wait_published_last",
    ],
)
def test_prep_persist_crash_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Real persistence/order; source admission/transport are unit boundaries."""
    from contextlib import nullcontext
    from types import SimpleNamespace
    from typing import Any

    from tools import agent_final_preparation_driver as driver_module

    # This node already admits source context; actual factory proof is genuine.
    monkeypatch.setattr(
        driver_module, "_current_dependency_gate", lambda _: nullcontext(),
    )
    monkeypatch.setattr(
        driver_module, "_historical_capture_lifetime", lambda _: nullcontext(),
    )
    from tools.agent_final_preparation_role import RoleCounter

    root, seed = tmp_path / "repo", tmp_path / "seed"
    root.mkdir()
    seed.mkdir(mode=0o700)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        if case == "schema2_store_dispatch":
            waiting = state(anchor(), phase="WAITING_OWNER_FINAL_APPROVAL")
            store.write("final-state", waiting)
            assert store.read("final-state") == waiting
            return
        launch = original("state")["launch_digest"]
        counter = RoleCounter(store, launch)
        if case == "crash_after_role_reservation":
            store.write("role-state", records.seal({
                **original("role_state"), "calls_consumed": 0,
                "pending_reservation_digest": None,
            }))
            counter.reserve(
                preparation_identity_digest="c" * 64, input_digest="d" * 64,
                backend_digest="e" * 64, started_at=100, deadline_at=200,
            )
        else:
            store.write("final-state", state(None, phase="PREPARING"))
    session = SimpleNamespace(state=seed, root=root, config={
        "approved_launch_digest": launch, "started_at": 90,
    })
    driver: Any = driver_module.PreparationDriver.__new__(
        driver_module.PreparationDriver,
    )
    driver.session, driver.iterations = session, 1
    events: list[str] = []
    if case == "crash_after_role_reservation":
        before = {item.name: item.read_bytes() for item in store.directory.iterdir()}
        monkeypatch.setattr(driver, "_store", lambda: (
            store, counter, SimpleNamespace(check=lambda: None),
        ))
        monkeypatch.setattr(
            original_runtime, "structural_child_preflight", lambda _: None,
        )
        monkeypatch.setattr(
            original_runtime, "review_learning_owned",
            lambda *_args, **_kwargs: events.append("aggregate"),
        )
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            driver._complete_review()
        assert events == []
        assert {
            item.name: item.read_bytes() for item in store.directory.iterdir()
        } == before
        with RunnerLock(seed):
            consumed, reservation = counter.read()
            assert consumed is not None and reservation is not None
            assert consumed["calls_consumed"] == 1
            assert reservation["status"] == "RESERVED"
        return
    assessment = original("assessment")
    request = records.request(records.seal({
        **original("request"), "preparation_digest": assessment["record_digest"],
    }, self_digest="operation_id"))
    source = SimpleNamespace(
        profile=original("profile"), stable_assessment=lambda: assessment,
        request=lambda: request, check=lambda: None,
        authority=lambda: ({"issued_at": 90, "expires_at": 200}, None, None),
        binding=lambda _: None, effect=lambda _: None, reconcile=lambda _: None,
        ci_identity=lambda pr: {"pr": pr},
        primary_ci=lambda _: {"status": "PASS", "pr_ci_digest": "d" * 64},
        report_statuses=lambda: ("NONE_APPLIED", "UNMEASURED"),
    )
    driver.scope = SimpleNamespace(_active_reader=lambda: SimpleNamespace(
        poll_ci=lambda _identity, assess: assess(),
    ))
    monkeypatch.setattr(driver, "_complete_review", lambda: None)
    monkeypatch.setattr(driver_module, "CurrentPreparation", lambda _: source)
    monkeypatch.setattr(
        original_runtime, "_structural_children_current_owned",
        lambda _: [{"task": 1}, {"task": 2}],
    )
    monkeypatch.setattr(original_delivery, "deliver", lambda *_args, **_kwargs: (
        SimpleNamespace(status="APPLIED", receipt={
            "pr": 50, "repository": request["repository"], "base_ref": "main",
            "head_ref": request["roadmap_ref"], "main_sha": request["main_sha"],
            "roadmap_sha": request["roadmap_sha"],
            "operation_id": request["operation_id"],
        })
    ))
    write = Store.write

    def observed_write(self: Store, name: str, value: Any) -> None:
        events.append(name)
        write(self, name, value)

    monkeypatch.setattr(Store, "write", observed_write)

    def publication(_source: Any, _pr: int) -> dict[str, Any]:
        persisted = store.read("final-state")
        assert persisted is not None
        assert persisted["phase"] == "PREPARING"
        assert persisted["owner_wait"] is None
        assert any(name.startswith("candidate-") for name in events)
        assert any(name.startswith("report-") for name in events)
        if case == "crash_after_candidate_before_wait":
            raise KeyboardInterrupt
        events.append("anchor_verified")
        return anchor()

    monkeypatch.setattr(driver, "_anchor", publication)
    if case == "crash_after_candidate_before_wait":
        with pytest.raises(KeyboardInterrupt):
            driver.resume()
        with RunnerLock(seed):
            persisted = store.read("final-state")
            assert persisted is not None
            assert persisted["owner_wait"] is None
    else:
        result = driver.resume()
        assert result["status"] == "WAITING_OWNER_FINAL_APPROVAL"
        with RunnerLock(seed):
            persisted = store.read("final-state")
            assert persisted is not None
            assert persisted["phase"] == "WAITING_OWNER_FINAL_APPROVAL"
            assert persisted["owner_wait"] == anchor()
        assert events[-1] == "final-state"
        assert events.index("anchor_verified") < len(events) - 1



@pytest.mark.skipif(os.name != "posix", reason="POSIX owned preparation storage")
@pytest.mark.parametrize(
    "change", ["remove", "replace", "schema", "refund"],
    ids=[
        "anchor_removed", "anchor_replaced", "schema_downgrade",
        "consumed_count_refund",
    ],
)
def test_prep_wait_resume(tmp_path: Path, change: str) -> None:
    root, seed = tmp_path / "repo", tmp_path / "seed"
    root.mkdir()
    seed.mkdir(mode=0o700)
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        waiting = state(anchor(), phase="WAITING_OWNER_FINAL_APPROVAL")
        store.write("final-state", waiting)
        if change == "remove":
            changed = records.seal({**waiting, "owner_wait": None})
        elif change == "replace":
            changed = records.seal({
                **waiting, "owner_wait": {**anchor(), "deadline_at": 201},
            })
        elif change == "schema":
            changed = original("state")
        else:
            spent = records.seal({**waiting, "calls_consumed": 1})
            store.write("final-state", spent)
            waiting = spent
            changed = records.seal({**spent, "calls_consumed": 0})
        before = (seed / "final-preparation/final-state.json").read_bytes()
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            store.write("final-state", changed)
        assert (seed / "final-preparation/final-state.json").read_bytes() == before
        assert store.read("final-state") == waiting


def _historical_ci_source() -> tuple[object, object]:
    """Finite synthetic primary originals, with one common view per endpoint."""
    import copy
    import json
    from types import SimpleNamespace
    from typing import Any

    from tests.test_agent_learning_delivery import checks, ci_reader

    base, _seen = ci_reader()
    sha = "a" * 40
    sources: dict[str, Any] = {}
    for endpoint in (
        "actions/workflows/tests.yml", "actions/workflows/pre-commit.yml",
        "check-suites/50", "check-suites/51", "actions/runs/99", "actions/runs/100",
        "actions/runs/99/attempts/2/jobs?per_page=100",
        "actions/runs/100/attempts/2/jobs?per_page=100",
    ):
        sources[endpoint] = json.loads(base.read((
            "gh", "api", "repos/synthetic/repo/" + endpoint,
        )))
    sources["all_rows"] = checks(sha)["check_runs"]
    sources["latest_rows"] = copy.deepcopy(sources["all_rows"])
    sources["suite_ids"] = [50, 51]
    sources["seen"] = []

    def read(argv: tuple[str, ...]) -> str:
        assert argv[:2] == ("gh", "api")
        prefix = "repos/synthetic/repo/"
        assert argv[-1].startswith(prefix)
        endpoint = argv[-1].removeprefix(prefix)
        sources["seen"].append(endpoint)
        value: Any
        if endpoint == f"commits/{sha}/check-runs?filter=all&per_page=100":
            value = {
                "total_count": len(sources["all_rows"]),
                "check_runs": sources["all_rows"],
            }
            if sources.get("incomplete_checks"):
                value["total_count"] += 1
        elif endpoint == f"commits/{sha}/check-runs?per_page=100":
            value = {
                "total_count": len(sources["latest_rows"]),
                "check_runs": sources["latest_rows"],
            }
        elif endpoint == f"commits/{sha}/check-suites?per_page=100":
            values = [sources[f"check-suites/{sid}"] for sid in sources["suite_ids"]]
            value = {"total_count": len(values), "check_suites": values}
            if sources.get("incomplete_suites"):
                value["total_count"] += 1
        else:
            assert endpoint in sources, "unknown fixed synthetic source endpoint"
            value = sources[endpoint]
        return json.dumps(value)

    reader = SimpleNamespace(
        owner=copy.deepcopy(base.owner), config={"schema_version": "8.0"},
        guard=base.guard, _git=base._git, read=read,
    )
    return reader, sources


def _historical_add_generation(
    sources: object, offset: int, event: str, *, pending: bool = False,
) -> None:
    import copy
    from typing import Any, cast

    value = cast(dict[str, Any], sources)
    rows = copy.deepcopy(value["all_rows"][:5])
    for row in rows:
        old_run = 100 if row["name"] == "pre-commit" else 99
        old_suite = 51 if row["name"] == "pre-commit" else 50
        row["id"] += offset
        row["check_suite"]["id"] += offset
        row["details_url"] = (
            "https://github.com/synthetic/repo/actions/runs/"
            f"{old_run + offset}/job/{row['id']}"
        )
        if pending:
            row.update(status="queued", conclusion=None)
        run_endpoint = f"actions/runs/{old_run + offset}"
        if run_endpoint not in value:
            run = copy.deepcopy(value[f"actions/runs/{old_run}"])
            run.update(
                id=old_run + offset, event=event,
                check_suite_id=old_suite + offset,
            )
            if pending:
                run.update(status="queued", conclusion=None)
            value[run_endpoint] = run
            suite = copy.deepcopy(value[f"check-suites/{old_suite}"])
            suite["id"] += offset
            value[f"check-suites/{old_suite + offset}"] = suite
            value["suite_ids"].append(old_suite + offset)
            jobs = copy.deepcopy(value[
                f"actions/runs/{old_run}/attempts/2/jobs?per_page=100"
            ])
            for job in jobs["jobs"]:
                job["id"] += offset
                job["run_id"] += offset
                if pending:
                    job.update(status="queued", conclusion=None)
            jobs_endpoint = (
                f"actions/runs/{old_run + offset}/attempts/2/jobs?per_page=100"
            )
            value[jobs_endpoint] = jobs
    value["all_rows"].extend(rows)
    value["latest_rows"] = copy.deepcopy(rows)


@pytest.mark.parametrize("case", [
    "same_sha_old_push_and_new_pr_preserves_digest",
    "requested_pr_old_graph_preserved",
    "unselected_pending_pr_does_not_relabel",
    "duplicate_push_generation_stop",
    "incomplete_suite_listing",
    "incomplete_check_listing",
    "missing_requested_event",
    "unknown_run_event",
    "wrong_run_repository",
    "wrong_run_head_sha",
    "pinned_workflow_drift",
    "wrong_job_identity",
    "wrong_job_attempt",
    "wrong_suite_identity",
    "retained_digest_still_required",
])
def test_historical_ci_event_selection(
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    import copy
    from typing import Any, cast

    from tools import agent_learning as learning
    from tools.agent_continuation import digest
    from tools.agent_learning_delivery import DeliverySources

    reader_object, source_object = _historical_ci_source()
    reader = cast(Any, reader_object)
    source = cast(dict[str, Any], source_object)
    event = "pull_request" if case == "requested_pr_old_graph_preserved" else "push"
    for run_id in (99, 100):
        source[f"actions/runs/{run_id}"]["event"] = event
    old = DeliverySources(reader).ci("a" * 40, event=event)
    old_sources = copy.deepcopy({
        key: value for key, value in source.items()
        if key.startswith(("actions/runs/", "check-suites/"))
    })
    _historical_add_generation(
        source, 1000, "push" if event == "pull_request" else "pull_request",
        pending=case == "unselected_pending_pr_does_not_relabel",
    )
    if case == "duplicate_push_generation_stop":
        _historical_add_generation(source, 2000, "push")
    elif case == "incomplete_suite_listing":
        source["incomplete_suites"] = True
    elif case == "incomplete_check_listing":
        source["incomplete_checks"] = True
    elif case == "missing_requested_event":
        source["all_rows"] = source["all_rows"][5:]
    elif case == "unknown_run_event":
        source["actions/runs/1099"]["event"] = "workflow_dispatch"
    elif case == "wrong_run_repository":
        source["actions/runs/1099"]["repository"]["full_name"] = "other/repo"
    elif case == "wrong_run_head_sha":
        source["actions/runs/1099"]["head_sha"] = "c" * 40
    elif case == "pinned_workflow_drift":
        original_git = reader._git

        def changed_git(*argv: str) -> bytes:
            value: bytes = original_git(*argv)
            if argv[-1].endswith(":.github/workflows/tests.yml"):
                return value.replace(b"name: CI", b"name: untrusted", 1)
            return value

        reader._git = changed_git
    elif case in {"wrong_job_identity", "wrong_job_attempt"}:
        job = source["actions/runs/99/attempts/2/jobs?per_page=100"]["jobs"][0]
        job["run_id" if case == "wrong_job_identity" else "run_attempt"] += 1
    elif case == "wrong_suite_identity":
        source["check-suites/50"]["id"] = 999
    valid = {
        "same_sha_old_push_and_new_pr_preserves_digest",
        "requested_pr_old_graph_preserved",
        "unselected_pending_pr_does_not_relabel",
        "retained_digest_still_required",
    }
    if case not in valid:
        with pytest.raises(learning.LearningError):
            DeliverySources(reader).ci("a" * 40, event=event)
        return
    current = DeliverySources(reader).ci("a" * 40, event=event)
    assert current == old
    assert digest(current) == digest(old)
    assert all(source[key] == value for key, value in old_sources.items())
    assert f"commits/{'a' * 40}/check-runs?per_page=100" not in source["seen"]
    assert {row["run_id"] for row in current["checks"]} == {99, 100}
    if case == "retained_digest_still_required":
        from tests.test_agent_learning_verify import paired_verifier

        verifier, before, after, pinned = paired_verifier(monkeypatch)
        # Existing neutral workflow fixture isolates the ORIGINAL retained-digest
        # comparison; actual normalized CI above supplies the changed source.
        monkeypatch.setattr(verifier.reader, "ci", lambda *_args: current)
        after["source"]["pr_ci_digest"] = digest(current)
        after["source"]["post_ci_digest"] = digest(current)
        assert verifier.pair(before, learning.seal(after), pinned) is not None
        after["source"]["post_ci_digest"] = "0" * 64
        assert verifier.pair(before, learning.seal(after), pinned) is None


@pytest.mark.parametrize(
    "case",
    [
        "typed_request_type_mismatch", "typed_request_field_mismatch",
        "full_evaluation_digest_join", "planning_use_unmeasured",
        "regressed_measurement_unmeasured", "malformed_app_shape",
        "check_app_float_id", "suite_app_float_id", "completed_run_queued_job",
        "dirty_before_first_ref_effect",
    ],
)
def test_normal_preparation_source_join_regressions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Unit source joins only; mocked contexts do not prove Session authority."""
    import copy
    from contextlib import nullcontext
    from dataclasses import asdict, replace
    from types import SimpleNamespace
    from typing import Any, cast

    # Current dependency source envelope is admitted, not this unit's subject.
    monkeypatch.setattr(
        "tools.agent_final_preparation_sources._current_dependency_gate",
        lambda _: nullcontext(),
    )

    from tools import agent_coordinator_handover as handover
    from tools import agent_final_learning_domain as domain
    from tools import agent_final_preparation_delivery as preparation_delivery
    from tools import agent_learning as learning
    from tools.agent_continuation import digest
    from tools.agent_final_preparation_driver import PreparationDriver
    from tools.agent_final_preparation_sources import CurrentPreparation

    from tests.test_agent_final_preparation_ci import sources
    from tests.test_agent_learning import measurement

    if case.startswith("typed_request_"):
        current = original("request")
        expected = preparation_delivery.transport_request(current)
        source = cast(CurrentPreparation, SimpleNamespace(
            request=lambda: current, binding=lambda _: None, check=lambda: None,
        ))
        supplied = (SimpleNamespace(**asdict(expected))
                    if case == "typed_request_type_mismatch"
                    else replace(expected, epic=expected.epic + 1))
        with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
            CurrentPreparation._transport_binding(source, supplied)
        return
    if case in {
        "full_evaluation_digest_join", "planning_use_unmeasured",
        "regressed_measurement_unmeasured",
    }:
        evaluation = learning.compare(
            measurement(), measurement(
                hints=True, outcome=("FAIL_ESCALATE"
                                     if case == "regressed_measurement_unmeasured"
                                     else "PASS"),
            ),
        )
        packet: dict[str, Any] = {
            "calls": [{"invocation_key": "call", "task": 301,
                       "family": ("PLANNING" if case == "planning_use_unmeasured"
                                  else "WORKFLOW")}],
            "uses": [{"call_key": "call", "ordered_lessons": ["a" * 64]}],
            "measurements": [{
                "task": 301, "baseline_digest": evaluation["baseline_digest"],
                "measurement_digest": evaluation["hinted_digest"],
                "evaluation_digest": digest(evaluation),
            }],
        }
        closed: list[bool] = []
        historical = SimpleNamespace(
            _retained=lambda: ([{"kind": "evaluation", "record": evaluation}], []),
            _guard=lambda: None, reader=SimpleNamespace(
                close=lambda: closed.append(True),
            ),
        )
        monkeypatch.setattr(domain, "_FinalPlanningReader", lambda _: historical)
        source = cast(CurrentPreparation, SimpleNamespace(
            stable_assessment=lambda: None, packet=packet, session=object(),
        ))
        expected_status = ("VERIFIED_COMPARABLE"
                           if case == "full_evaluation_digest_join"
                           else "UNMEASURED")
        assert CurrentPreparation.report_statuses(source) == (
            "VERIFIED_APPLIED", expected_status,
        )
        if case == "full_evaluation_digest_join":
            packet["measurements"][0]["evaluation_digest"] = (
                evaluation["record_digest"]
            )
            assert CurrentPreparation.report_statuses(source) == (
                "VERIFIED_APPLIED", "UNMEASURED",
            )
        assert len(closed) == (0 if case == "planning_use_unmeasured" else
                               2 if case == "full_evaluation_digest_join" else 1)
        return
    if case == "dirty_before_first_ref_effect":
        reads: list[tuple[str, ...]] = []
        writes: list[str] = []

        def dirty(_directory: Path, *argv: str) -> str:
            reads.append(argv)
            assert argv == ("status", "--porcelain=v1", "-z")
            return "?? synthetic-dirty\0"

        monkeypatch.setattr(handover, "_git", dirty)
        dirty_driver = cast(PreparationDriver, SimpleNamespace(
            iterations=0, session=SimpleNamespace(
                root=tmp_path, _guard_mutation=lambda *a, **k: None,
                _sync_final=lambda *a, **k: writes.append("sync"),
            ),
        ))
        with pytest.raises(PhaseBlocked, match="^DIRTY_WORKTREE$"):
            PreparationDriver._synchronize_first(dirty_driver, original("profile"))
        assert reads == [("status", "--porcelain=v1", "-z")] and not writes
        return
    data = sources()
    check = data["check_runs"]["check_runs"][0]
    if case == "malformed_app_shape":
        check["app"] = []
    elif case == "check_app_float_id":
        check["app"]["id"] = 15368.0
    elif case == "suite_app_float_id":
        data["suites"][check["check_suite"]["id"]]["app"]["id"] = 15368.0
    else:
        assert case == "completed_run_queued_job"
        check.update(status="queued", conclusion=None)
        run_id = int(check["details_url"].split("/runs/")[1].split("/")[0])
        matching = next(j for j in data["jobs"][run_id]["jobs"]
                        if j["id"] == check["id"])
        matching.update(status="queued", conclusion=None)
        assert data["runs"][run_id]["status"] == "completed"
    repo, sha = "synthetic/repo", "a" * 40

    def read(endpoint: str) -> Any:
        suffix = endpoint.removeprefix("repos/" + repo + "/")
        if suffix == f"commits/{sha}/check-runs?per_page=100":
            return copy.deepcopy(data["check_runs"])
        if suffix.startswith("check-suites/"):
            return copy.deepcopy(data["suites"][int(suffix.split("/")[1])])
        if suffix.startswith("actions/workflows/"):
            return copy.deepcopy(data["workflows"][suffix.split("/")[-1]])
        parts = suffix.split("/")
        run = int(parts[2])
        return copy.deepcopy(data["runs"][run] if len(parts) == 3
                             else data["jobs"][run])

    def definition(_directory: Path, *argv: str) -> str:
        assert argv[0] == "show"
        return cast(bytes, data["definitions"][argv[1].split("/")[-1]]).decode()

    monkeypatch.setattr(handover, "_git", definition)
    unit: Any = SimpleNamespace(
        profile={"repository": repo, "policy_sha": "b" * 40},
        identity={"roadmap_sha": sha}, session=SimpleNamespace(root=tmp_path),
        binding=lambda _: None, request=lambda: {}, _pr=lambda _: {"pr": 9},
        _json=read,
    )
    unit._pending_sources = lambda *a: CurrentPreparation._pending_sources(unit, *a)
    expected_code = ("FINAL_STAGE_GATE_INVALID" if case == "suite_app_float_id"
                     else "FINAL_APPROVAL_INVALID")
    with pytest.raises(PhaseBlocked, match="^" + expected_code + "$"):
        CurrentPreparation.primary_ci(unit, 9)


@pytest.mark.parametrize("case", [
    "wrong_sha", "wrong_workflow_definition", "wrong_attempt_job_join",
    "foreign_run_repository",
])
def test_prep_ci_source_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Actual current-CI acquisition over an already-admitted source envelope."""
    from contextlib import nullcontext
    from types import SimpleNamespace
    from typing import Any, cast

    # Current dependency source envelope is admitted, not this unit's subject.
    monkeypatch.setattr(
        "tools.agent_final_preparation_sources._current_dependency_gate",
        lambda _: nullcontext(),
    )

    from tools.agent_final_preparation_sources import CurrentPreparation

    from tests.test_agent_final_preparation_ci import sources

    value = sources()
    if case == "wrong_sha":
        value["check_runs"]["check_runs"][0]["head_sha"] = "c" * 40
    elif case == "wrong_workflow_definition":
        value["definitions"]["tests.yml"] = b"name: unexpected\non: pull_request\n"
    elif case == "wrong_attempt_job_join":
        value["jobs"][90]["jobs"][0]["run_attempt"] = 3
    else:
        value["runs"][90]["repository"]["full_name"] = "other/repo"
    endpoints: dict[str, Any] = {
        f"repos/synthetic/repo/commits/{'a' * 40}/check-runs?per_page=100":
            value["check_runs"],
    }
    for identifier, row in value["suites"].items():
        endpoints[f"repos/synthetic/repo/check-suites/{identifier}"] = row
    for identifier, row in value["runs"].items():
        endpoints[f"repos/synthetic/repo/actions/runs/{identifier}"] = row
        endpoints[
            f"repos/synthetic/repo/actions/runs/{identifier}"
            "/attempts/2/jobs?per_page=100"
        ] = value["jobs"][identifier]
    for filename, row in value["workflows"].items():
        endpoints[f"repos/synthetic/repo/actions/workflows/{filename}"] = row
    seen: list[str] = []
    bound: list[object] = []
    request = object()

    def read(endpoint: str) -> Any:
        seen.append(endpoint)
        assert endpoint in endpoints
        return endpoints[endpoint]

    def git(root: Path, *argv: str) -> str:
        assert root == tmp_path and argv[0] == "show"
        policy, filename = argv[1].split(":.github/workflows/", 1)
        assert policy == "b" * 40
        return cast(bytes, value["definitions"][filename]).decode()

    monkeypatch.setattr("tools.agent_final_preparation_sources.handover._git", git)
    envelope = SimpleNamespace(
        profile={"repository": "synthetic/repo", "policy_sha": "b" * 40},
        identity={"roadmap_sha": "a" * 40}, session=SimpleNamespace(root=tmp_path),
        request=lambda: request, binding=lambda actual: bound.append(actual),
        _pr=lambda number: {"number": number, "state": "open"}, _json=read,
    )
    code = (
        "FINAL_APPROVAL_INVALID" if case == "wrong_sha"
        else "FINAL_STAGE_GATE_INVALID"
    )
    with pytest.raises(PhaseBlocked, match=code):
        CurrentPreparation.primary_ci(cast(Any, envelope), 50)
    assert bound == [request]
    assert seen and all(endpoint in endpoints for endpoint in seen)




@pytest.mark.parametrize("case", [
    "config_preparation_digest", "preseed_source_identity",
    "roadmap_or_main_identity", "structural_child_source",
    "local_gate_original", "aggregate_review_original",
])
def test_prep_original_sources_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Real preseed rejection or unit reread joins, not installed authority proof."""
    import copy
    import os
    from types import SimpleNamespace
    from typing import Any, cast

    from tools import agent_epic_runtime_cli as guards
    from tools import agent_final_preparation_runtime as runtime
    from tools.agent_continuation import digest
    from tools.agent_final_preparation_sources import CurrentPreparation

    if case in {"config_preparation_digest", "preseed_source_identity"}:
        if os.name != "posix":
            pytest.skip("POSIX original preseed admission")
        from tests.test_agent_final_preparation_runtime import original_sources
        from tests.test_agent_final_preseed_guard import write

        root, seed = original_sources(tmp_path, monkeypatch)
        if case == "config_preparation_digest":
            target = seed / "epic-runtime.json"
            config = guards.private_json(target)
            config["approved_final_preparation_digest"] = "sha256:" + "0" * 64
            write(target, config)
        else:
            target = seed / "final-initialization/approval-pin.json"
            target.unlink()
        before = {
            path: path.read_bytes() for path in seed.rglob("*.json")
            if path.is_file()
        }
        with pytest.raises(guards.DeploymentError):
            guards.final_preparation_original_sources(seed, root)
        assert before == {path: path.read_bytes() for path in before}
        if case == "preseed_source_identity":
            assert not target.exists()
        return

    if case == "roadmap_or_main_identity":
        request = original("request")
        checks: list[str] = []
        envelope = cast(CurrentPreparation, SimpleNamespace(
            check=lambda: checks.append("guard"), request=lambda: request,
            profile={"repository": request["repository"],
                     "roadmap_ref": request["roadmap_ref"]},
            identity={"main_sha": request["main_sha"],
                      "roadmap_sha": request["roadmap_sha"]},
            session=SimpleNamespace(github=SimpleNamespace(
                branches=lambda _: {
                    "main": "0" * 40,
                    request["roadmap_ref"]: request["roadmap_sha"],
                },
            )),
        ))
        assert request["main_sha"] != "0" * 40
        with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
            CurrentPreparation.binding(envelope, request)
        assert checks == ["guard"]
        return

    aggregate: dict[str, Any] = {
        "request": {"gate_evidence": {"snapshot": {"synthetic": True}}},
    }
    environment: dict[str, Any] = {"review_source_digest": digest(aggregate)}
    children: tuple[dict[str, Any], ...] = ({"task": 301},)
    reads = {"aggregate": 0, "environment": 0, "children": 0}

    def aggregate_read(_session: object) -> dict[str, Any]:
        reads["aggregate"] += 1
        result = copy.deepcopy(aggregate)
        if case == "aggregate_review_original" and reads["aggregate"] == 2:
            result["changed_original"] = True
        return result

    def environment_read(_session: object) -> dict[str, Any]:
        reads["environment"] += 1
        result = copy.deepcopy(environment)
        if case == "local_gate_original" and reads["environment"] == 2:
            result["changed_gate_environment"] = True
        return result

    def children_read(_session: object) -> tuple[dict[str, Any], ...]:
        reads["children"] += 1
        if case == "structural_child_source" and reads["children"] == 2:
            return ({"task": 303},)
        return copy.deepcopy(children)

    # Supply admitted source boundaries only; the real projection must reject
    # observed family drift. These doubles do not prove the original readers.
    pins = SimpleNamespace(check=lambda: None)
    monkeypatch.setattr(runtime, "_CurrentFinalSources", lambda _: pins)
    monkeypatch.setattr(runtime, "_OwnedCompletedSources", lambda _: pins)
    monkeypatch.setattr(runtime, "read_completed_aggregate_owned", aggregate_read)
    monkeypatch.setattr(runtime, "read_completed_environment_owned", environment_read)
    monkeypatch.setattr(runtime, "_structural_children_current_owned", children_read)
    with pytest.raises(PhaseBlocked, match="^FINAL_REVIEW_SOURCE_DRIFT$"):
        runtime.read_current_final_source_digests_owned(object())
    family = {
        "structural_child_source": "children", "local_gate_original": "environment",
        "aggregate_review_original": "aggregate",
    }[case]
    assert reads[family] == 2


@pytest.mark.parametrize(
    "case",
    ["stopped_control", "revoked_mandate", "expired_mandate", "duration_limit"],
)
def test_prep_authority_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Actual lifecycle refuses before driver ref effects; sources admitted here."""
    from types import SimpleNamespace
    from typing import Any

    from tools import agent_epic_delivery as delivery
    from tools import agent_epic_session_runtime as runtime
    from tools import agent_final_preparation_driver as driver_module

    from tests.test_agent_epic_delivery import _mandate

    session: Any = runtime.SessionRuntime.__new__(runtime.SessionRuntime)
    session.state, session.root = tmp_path / "state", tmp_path / "repo"
    session.config = {"owner_identity": "OWNER", "started_at": 150,
                      "follow_up_issues": 0}
    effects: list[str] = []

    def forbidden_git(*_args: Any, **_kwargs: Any) -> str:
        effects.append("git")
        raise AssertionError("authority rejection reached ref synchronization")

    session.github = SimpleNamespace(branches=lambda _: effects.append("branches"))
    monkeypatch.setattr(handover_source, "_git", forbidden_git)
    expected = {
        "stopped_control": "OWNER_CONTROL_CHANGED",
        "revoked_mandate": "MANDATE_REVOKED",
        "expired_mandate": "MANDATE_EXPIRED",
        "duration_limit": "MANDATE_DURATION_LIMIT",
    }[case]
    if case == "stopped_control":
        def stopped(*_args: Any, **_kwargs: Any) -> None:
            raise PhaseBlocked("OWNER_CONTROL_CHANGED")

        # Managed launch/control acquisition is explicitly a rejected boundary;
        # this case proves its priority, not the underlying control JSON parser.
        monkeypatch.setattr(runtime, "managed_launch_guard", stopped)
    else:
        mandate = _mandate()
        mandate["schema_version"] = "3.0"
        if case == "revoked_mandate":
            mandate["revoked"] = True
        approved = delivery.mandate_digest(mandate)
        approval = mandate["approval"]
        assert isinstance(approval, dict)
        approval["mandate_digest"] = approved
        now = 1000 if case == "expired_mandate" else (
            800 if case == "duration_limit" else 200
        )
        context = delivery.MandateContext(now, "OWNER", 150, 1, 0)
        monkeypatch.setattr(session, "authority", lambda _: (
            mandate, approved, context,
        ))
    driver: Any = driver_module.PreparationDriver.__new__(
        driver_module.PreparationDriver,
    )
    driver.session, driver.iterations = session, 1
    with pytest.raises(PhaseBlocked, match=expected):
        driver._synchronize_first(original("profile"))
    assert effects == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX owned preparation storage")
@pytest.mark.parametrize(
    "case",
    [
        "zero_before_factory", "one_reserved_call", "unknown_reservation_no_replay",
        "resume_no_second_aggregate",
    ],
)
def test_prep_aggregate_conservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Actual driver/RoleCounter lock and call conservation, no native factory."""
    from contextlib import nullcontext
    from types import SimpleNamespace
    from typing import Any

    from tools import agent_final_preparation_driver as driver_module

    # This node already admits source context; actual factory proof is genuine.
    monkeypatch.setattr(
        driver_module, "_current_dependency_gate", lambda _: nullcontext(),
    )
    monkeypatch.setattr(
        driver_module, "_historical_capture_lifetime", lambda _: nullcontext(),
    )
    from tools.agent_final_preparation_role import RoleCounter

    seed, root = tmp_path / "seed", tmp_path / "repo"
    seed.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    launch = original("state")["launch_digest"]
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        counter = RoleCounter(store, launch)
        store.write("role-state", records.seal({
            **original("role_state"), "calls_consumed": 0,
            "pending_reservation_digest": None,
        }))
        if case in {"unknown_reservation_no_replay", "resume_no_second_aggregate"}:
            reserved = counter.reserve(
                preparation_identity_digest="c" * 64, input_digest="d" * 64,
                backend_digest="e" * 64, started_at=100, deadline_at=200,
            )
            if case == "unknown_reservation_no_replay":
                counter.unknown()
            else:
                store.write(counter.key, records.seal({
                    **reserved, "status": "COMPLETED",
                    "settings_digest": "f" * 64, "output_digest": "a" * 64,
                }))
    events: list[str] = []
    driver: Any = driver_module.PreparationDriver.__new__(
        driver_module.PreparationDriver,
    )
    driver.session = SimpleNamespace(state=seed, root=root, config={
        "approved_launch_digest": launch, "started_at": 90,
    })
    driver.iterations = 1
    driver.scope = SimpleNamespace(
        frame={"deadline_at": 200},
        _active_reader=lambda: SimpleNamespace(current_deadline=200),
    )
    monkeypatch.setattr(driver, "_store", lambda: (
        store, counter, SimpleNamespace(check=lambda: events.append("pins")),
    ))
    monkeypatch.setattr(
        original_runtime, "structural_child_preflight",
        lambda _: events.append("preflight"),
    )
    monkeypatch.setattr(
        original_runtime, "read_completed_learning_review_owned",
        lambda _: events.append("read_completed"),
    )
    monkeypatch.setattr(time, "time", lambda: 150)
    monkeypatch.setattr(source_guards, "private_json", lambda _: {
        "expires_at": 200, "limits": {"max_duration_seconds": 1000},
    })

    def synchronize(_profile: Any) -> None:
        assert seed.resolve() not in RunnerLock._held
        events.append("synchronize")

    monkeypatch.setattr(driver, "_synchronize_first", synchronize)

    def aggregate(_session: Any, **times: Any) -> None:
        assert seed.resolve() not in RunnerLock._held
        events.append("aggregate")
        # This admitted aggregate boundary uses the REAL counter under its own
        # lock, so an erroneous outer seed lock would fail instead of reentering.
        with RunnerLock(seed):
            zero, previous = counter.read()
            assert zero is not None
            assert zero["calls_consumed"] == 0 and previous is None
            persisted = store.read("final-state")
            assert persisted is not None
            assert persisted["phase"] == "PREPARING"
            assert persisted["calls_consumed"] == 0
            reserved = counter.reserve(
                preparation_identity_digest="c" * 64, input_digest="d" * 64,
                backend_digest="e" * 64, started_at=times["started_at"],
                deadline_at=times["deadline_at"],
            )
            spent, _row = counter.read()
            assert spent is not None
            assert spent["calls_consumed"] == 1
            events.append("factory_boundary")
            if case == "zero_before_factory":
                raise KeyboardInterrupt
            store.write(counter.key, records.seal({
                **reserved, "status": "COMPLETED",
                "settings_digest": "f" * 64, "output_digest": "a" * 64,
            }))

    monkeypatch.setattr(original_runtime, "review_learning_owned", aggregate)
    if case == "zero_before_factory":
        with pytest.raises(KeyboardInterrupt):
            driver._complete_review()
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            driver._complete_review()
        assert events.count("aggregate") == events.count("factory_boundary") == 1
    elif case == "unknown_reservation_no_replay":
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            driver._complete_review()
        assert "aggregate" not in events and "synchronize" not in events
    else:
        driver._complete_review()
        if case == "one_reserved_call":
            driver._complete_review()
            assert events.count("aggregate") == events.count("factory_boundary") == 1
            assert events.count("synchronize") == 1
        else:
            assert "aggregate" not in events and "synchronize" not in events
        assert events.count("read_completed") == 1
    with RunnerLock(seed):
        consumed, reservation = counter.read()
        assert consumed is not None and reservation is not None
        assert consumed["calls_consumed"] == 1
        assert reservation["status"] == (
            "RESERVED" if case == "zero_before_factory" else (
                "UNKNOWN" if case == "unknown_reservation_no_replay" else "COMPLETED"
            )
        )




@pytest.mark.parametrize("case", [
    "first_exact_create", "existing_exact_open_pr",
    "unknown_create_readonly_reconcile", "conflicting_or_multiple_pr",
])
def test_prep_create_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Real delivery/Store/transport joins; current-source envelope is admitted."""
    import copy
    import json
    import os
    from collections.abc import Sequence
    from types import SimpleNamespace
    from typing import Any, cast

    from tools import agent_epic_delivery as task_delivery
    from tools import agent_epic_final_delivery as final_delivery
    from tools import agent_final_preparation_delivery as preparation
    from tools.agent_epic_final_transport import GhFinalTransport
    from tools.agent_final_preparation_driver import _Ledger
    from tools.agent_final_preparation_sources import CurrentPreparation

    from tests.test_agent_epic_transport import metadata, report
    from tests.test_agent_final_preparation_delivery import Sources

    if os.name != "posix":
        pytest.skip("POSIX owned preparation Store and lock")
    source = Sources()
    request = source.request
    root, seed = tmp_path / "repo", tmp_path / "seed"
    root.mkdir()
    seed.mkdir(mode=0o700)
    profile = records.seal({
        **original("profile"),
        **{key: request[key] for key in (
            "repository", "epic", "mandate_digest", "roadmap_ref", "policy_sha",
        )},
    })
    pulls: list[int] = []
    effects: list[tuple[str, ...]] = []
    reads: list[tuple[str, ...]] = []
    prefix = "repos/" + request["repository"]

    def rest(number: int) -> dict[str, Any]:
        return {
            "number": number, "state": "open", "merged": False,
            "head": {"ref": request["roadmap_ref"], "sha": request["roadmap_sha"],
                     "repo": {"full_name": request["repository"]}},
            "base": {"ref": "main", "sha": request["main_sha"],
                     "repo": {"full_name": request["repository"]}},
        }

    def read(raw: Sequence[str]) -> str:
        argv = tuple(raw)
        reads.append(argv)
        if argv[:3] == ("gh", "pr", "view"):
            number = int(argv[3])
            assert number in pulls
            return json.dumps({
                "number": number, "state": "OPEN", "isCrossRepository": False,
                "baseRefName": "main", "baseRefOid": request["main_sha"],
                "headRefName": request["roadmap_ref"],
                "headRefOid": request["roadmap_sha"], "mergeCommit": None,
            })
        if argv == ("gh", "api", prefix + "/pulls/50"):
            assert 50 in pulls
            return json.dumps(rest(50))
        endpoint = (
            prefix + "/pulls?state=all&head=" + request["repository"].split("/")[0]
            + ":" + request["roadmap_ref"] + "&base=main&per_page=100"
        )
        assert argv == ("gh", "api", "--paginate", "--slurp", endpoint)
        return json.dumps([[rest(number) for number in pulls]])

    envelope = cast(CurrentPreparation, SimpleNamespace(
        check=lambda: None, request=lambda: request, profile=profile,
        identity={"main_sha": request["main_sha"],
                  "roadmap_sha": request["roadmap_sha"]},
        session=SimpleNamespace(
            root=root, _github_read=read,
            github=SimpleNamespace(branches=lambda _: {
                "main": request["main_sha"],
                request["roadmap_ref"]: request["roadmap_sha"],
            }),
        ),
    ))
    setattr(envelope, "binding", lambda value: CurrentPreparation.binding(
        envelope, value,
    ))
    setattr(envelope, "_json", lambda endpoint: CurrentPreparation._json(
        envelope, endpoint,
    ))
    setattr(envelope, "_pr", lambda number: CurrentPreparation._pr(envelope, number))
    setattr(
        envelope, "_transport_binding",
        lambda value: CurrentPreparation._transport_binding(envelope, value),
    )
    setattr(
        envelope, "factual_receipt",
        lambda number: CurrentPreparation.factual_receipt(envelope, number),
    )

    def before_write(value: Any) -> None:
        CurrentPreparation._transport_binding(envelope, value)
        code = final_delivery.authorization_code(value, source.authority)
        if code is not None:
            raise PhaseBlocked(code)

    transport = GhFinalTransport(root, read=read, reporting=report,
                                 before_write=before_write)
    setattr(envelope, "transport", lambda: transport)
    with RunnerLock(seed):
        store = Store(seed, root, profile, lambda: None)
        ledger = _Ledger(store)

        def command(raw: Sequence[str]) -> str:
            argv = tuple(raw)
            if argv[0] == "git":
                return metadata(argv)
            assert argv[:3] == ("gh", "api", prefix + "/pulls")
            assert argv[argv.index("--method") + 1] == "POST"
            assert ledger.load()[request["operation_id"]]["status"] == "INTENT"
            assert not pulls
            effects.append(argv)
            pulls.append(50)
            if case == "unknown_create_readonly_reconcile":
                raise task_delivery.OutcomeUnknown
            return json.dumps(rest(50))

        monkeypatch.setattr(transport, "command", command)
        if case in {"existing_exact_open_pr", "conflicting_or_multiple_pr"}:
            pulls.extend([50, 51] if case == "conflicting_or_multiple_pr" else [50])
            ledger.save({request["operation_id"]: {
                "request": copy.deepcopy(request), "status": "INTENT", "receipt": None,
            }})

        def deliver() -> Any:
            return preparation.deliver(
                request, authority=source.authority, binding=envelope.binding,
                assessment=source.assessment, ledger=ledger,
                effect=lambda value: CurrentPreparation.effect(envelope, value),
                reconcile=lambda value: CurrentPreparation.reconcile(envelope, value),
            )

        result = deliver()
        if case == "unknown_create_readonly_reconcile":
            assert result.status == "ESCALATE"
            assert ledger.load()[request["operation_id"]]["status"] == "UNKNOWN"
            source.now = 1001
            result = deliver()
        if case == "conflicting_or_multiple_pr":
            assert result.status == "ESCALATE" and effects == []
            assert ledger.load()[request["operation_id"]]["status"] == "INTENT"
        else:
            assert result.status == (
                "APPLIED" if case == "first_exact_create" else "NO_OP"
            )
            assert result.receipt is not None and result.receipt["pr"] == 50
            assert len(effects) == (0 if case == "existing_exact_open_pr" else 1)
            saved = ledger.load()[request["operation_id"]]
            assert saved["status"] == "APPLIED" and saved["receipt"] == result.receipt
        assert reads and all(argv[0] == "gh" for argv in reads)


def test_owner_view_default_fullgetter_conservation() -> None:
    """Default/unbound getters retain full checks outside the owner view."""
    from types import SimpleNamespace
    from typing import Any

    from tools.agent_final_preparation_sources import CurrentPreparation

    expected = original("request")
    events: list[str] = []

    def full_assessment() -> dict[str, Any]:
        events.append("full")
        return {"record_digest": expected["preparation_digest"]}

    def branches(repository: str) -> dict[str, str]:
        assert repository == expected["repository"]
        events.append("refs")
        return {
            "main": expected["main_sha"],
            expected["roadmap_ref"]: expected["roadmap_sha"],
        }

    source: Any = SimpleNamespace(
        profile=original("profile"),
        review={"identity": {key: expected[key] for key in records.IDENTITY}},
        identity={key: expected[key] for key in ("main_sha", "roadmap_sha")},
        baseline={"record_digest": expected["preparation_digest"]},
        packet={"calls": [], "uses": [], "measurements": []},
        stable_assessment=full_assessment,
        check=lambda: events.append("check"),
        session=SimpleNamespace(github=SimpleNamespace(branches=branches)),
    )
    source.request = lambda: CurrentPreparation.request(source)

    # Deliberately exercise the supported unbound receiver boundary, not a view.
    assert CurrentPreparation.request(source) == expected
    assert events == ["full"]
    events.clear()

    CurrentPreparation.binding(source, expected)
    assert events == ["check", "full", "refs", "check"]
    events.clear()

    assert CurrentPreparation.report_statuses(source) == (
        "NONE_APPLIED", "UNMEASURED",
    )
    assert events == ["full"]
