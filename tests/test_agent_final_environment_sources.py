"""Finite genuine aggregate producer environment-source regression draft."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tools import agent_epic_runtime_cli as guards

from tests.test_agent_epic_loop import (
    active_runtime_namespace as active_runtime_namespace,
)
from tests.test_agent_final_preparation_runtime import (
    NATIVE_DRIVER,
    native_factory_fixture,
    run_native_driver,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX managed source locks")
PUBLICATION_SETUP = r"""
import hashlib
from tools import agent_final_preparation_records as environment_records

publication_seen = []
original_write = storage.Store.write


def observe_completion(self, key, value):
    if key.startswith("reservation-") and value.get("status") == "COMPLETED":
        counter = runtime.RoleCounter(self, value["launch_digest"])
        sources = native_store.Store(self.directory / "sources", self.root)
        environment = environment_records.aggregate_environment(
            sources.load(
                "environment-" + runtime.digest(counter.identity),
                cap=self.owner["max_artifact_bytes"],
            )
        )
        source = sources.load(
            "review-" + runtime.digest(counter.identity),
            cap=self.owner["max_artifact_bytes"],
        )
        assert set(source) == {
            "identity",
            "request",
            "policy",
            "session_id",
            "output",
            "settings_source",
        }
        assert environment["review_source_digest"] == runtime.digest(source)
        assert (
            environment["launcher_executable_sha256"]
            == hashlib.sha256(
                Path(sys.executable).resolve(strict=True).read_bytes()
            ).hexdigest()
        )
        assert environment["input_digest"] == value["input_digest"]
        assert (
            environment["preparation_identity_digest"]
            == value["preparation_identity_digest"]
        )
        publication_seen.append(value["record_digest"])
    return original_write(self, key, value)


storage.Store.write = observe_completion
"""

ENVIRONMENT_READ_ONLY = r"""
from tools.agent_epic_session_runtime import SessionRuntime
from tools import agent_epic_runtime_cli as guards
from tools import agent_final_preparation_records as records
from tools import agent_final_preparation_review as review_module

session = SessionRuntime(
    state=seed,
    root=root,
    config=guards.private_json(seed / "epic-runtime.json"),
    wait_scope=scope,
)
with RunnerLock.supervisor(seed / "session-control"), RunnerLock(seed):
    with scope.context("FINAL:FINAL_GATE"):
        original = runtime.read_completed_environment_owned(session)
        assert (
            original["launcher_python_version"]
            == review_module.platform.python_version()
        )
        counter = runtime.RoleCounter(
            runtime._ReadOnlyPreparationStore(
                seed,
                root,
                runtime._OwnedCompletedSources(session).profile,
                runtime._OwnedCompletedSources(session).check,
            ),
            session.config["approved_launch_digest"],
        )
        target = (
            seed
            / "final-preparation/sources"
            / ("environment-" + runtime.digest(counter.identity) + ".json")
        )
        saved = target.read_bytes()
        reservation = next((seed / "final-preparation").glob("reservation-*.json"))
        before = reservation.read_bytes()
        review_module.platform.python_version = lambda: "999.998.997"
        assert runtime.read_completed_environment_owned(session) == original
        assert runtime.read_completed_aggregate_owned(session) == result

        def forbidden(*args, **kwargs):
            raise AssertionError("replacement call/publication")

        runtime.RoleCounter.reserve = forbidden
        runtime._ReservedDelegate._call = forbidden
        agent_gate.run_profile = forbidden
        storage.Store.write = forbidden
        for attack in (
            "missing",
            "version",
            "hash",
            "identity",
            "input",
            "cache",
            "extra",
            "duplicate",
            "symlink",
            "hardlink",
            "oversize",
            "fifo",
            "directory",
            "mode",
            "integer",
            "kind",
            "digest",
            "schema",
            "missing-field",
            "repository",
            "launch",
            "mandate",
        ):
            alias = target.with_suffix(".alias")
            try:
                value = json.loads(saved)
                if attack == "missing":
                    target.unlink()
                elif attack == "symlink":
                    target.rename(alias)
                    target.symlink_to(alias)
                elif attack == "hardlink":
                    import os

                    os.link(target, alias)
                elif attack == "fifo":
                    target.unlink()
                    import os

                    os.mkfifo(target, 0o600)
                elif attack == "directory":
                    target.unlink()
                    target.mkdir(mode=0o700)
                elif attack == "mode":
                    target.chmod(0o644)
                elif attack == "integer":
                    value["epic"] = True
                    target.write_text(json.dumps(records.seal(value)))
                elif attack == "kind":
                    value["kind"] = "CALLER_ENVIRONMENT"
                    target.write_text(json.dumps(records.seal(value)))
                elif attack == "schema":
                    value["schema_version"] = "2.0"
                    target.write_text(json.dumps(records.seal(value)))
                elif attack == "missing-field":
                    del value["launcher_executable_sha256"]
                    target.write_text(json.dumps(records.seal(value)))
                elif attack in {"repository", "launch", "mandate"}:
                    key = {
                        "repository": "repository",
                        "launch": "launch_digest",
                        "mandate": "mandate_digest",
                    }[attack]
                    value[key] = (
                        "synthetic/other"
                        if attack == "repository"
                        else "sha256:" + "0" * 64
                    )
                    target.write_text(json.dumps(records.seal(value)))
                elif attack == "digest":
                    value["record_digest"] = "0" * 64
                    target.write_text(json.dumps(value))
                elif attack == "duplicate":
                    target.write_text(
                        saved.decode()[:-1] + ',"kind":"FINAL_AGGREGATE_ENVIRONMENT"}'
                    )
                elif attack == "oversize":
                    target.write_bytes(
                        b" " * (counter.store.owner["max_artifact_bytes"] + 1)
                    )
                else:
                    key = {
                        "version": "launcher_python_version",
                        "hash": "launcher_executable_sha256",
                        "identity": "preparation_identity_digest",
                        "input": "input_digest",
                        "cache": "review_source_digest",
                        "extra": "unexpected",
                    }[attack]
                    value[key] = (
                        "bad" if attack in ("version", "hash", "extra") else "0" * 64
                    )
                    target.write_text(json.dumps(records.seal(value)))
                # Both fixed admission and owned reconstruction must fail closed.
                for read in (
                    lambda: guards.validate_final_preseed(seed, root),
                    lambda: runtime.read_completed_environment_owned(session),
                    lambda: runtime.read_completed_aggregate_owned(session),
                ):
                    try:
                        read()
                    except Exception as error:
                        assert type(error).__name__ in {
                            "DeploymentError",
                            "PhaseBlocked",
                            "LoopError",
                            "ContinuationError",
                        }, (attack, type(error).__name__)
                    else:
                        raise AssertionError("accepted invalid original:" + attack)
                assert reservation.read_bytes() == before
            finally:
                if target.is_symlink():
                    target.unlink()
                if alias.exists():
                    alias.unlink()
                if target.is_dir():
                    target.rmdir()
                elif target.exists():
                    target.unlink()
                target.write_bytes(saved)
                target.chmod(0o600)
        assert runtime.read_completed_environment_owned(session) == original
        assert guards.validate_final_preseed(seed, root)["calls_consumed"] == 1
        # The real native command has already run exactly once.
        assert len(native_started) == 1
        assert len(publication_seen) == 1
        deadline = json.loads(before)["deadline_at"]
        time.sleep(max(0, deadline - time.time()) + 0.05)
        assert time.time() > deadline
        assert runtime.read_completed_environment_owned(session) == original
        assert runtime.read_completed_aggregate_owned(session) == result
        assert reservation.read_bytes() == before
"""


@pytest.mark.usefixtures("active_runtime_namespace")
def test_original_environment_and_historical_resume(
    completed_learning_readers: dict[str, str],
) -> None:
    assert completed_learning_readers['environment'] == 'PASS'


LAUNCHER_FAULT_SETUP = r"""
from tools import agent_final_preparation_review as review_module

original_read_bytes = Path.read_bytes
original_executable = Path(sys.executable).resolve(strict=True)
executable_reads = []


def fault_read_bytes(self):
    if self == original_executable:
        executable_reads.append(None)
        if mode == "capture-io" or (
            mode == "publish-io" and len(executable_reads) == 2
        ):
            raise OSError("synthetic executable path must not escape")
        if mode == "changed-executable" and len(executable_reads) == 2:
            return original_read_bytes(self) + b"synthetic change"
    return original_read_bytes(self)


Path.read_bytes = fault_read_bytes
if mode == "unknown-version":
    review_module.platform.python_version = lambda: "unknown"
"""


@pytest.mark.usefixtures("active_runtime_namespace")
@pytest.mark.parametrize(
    "mode,consumed,code",
    [
        ("capture-io", 0, "FINAL_REVIEW_SOURCE_INVALID"),
        ("publish-io", 1, "FINAL_REVIEW_SOURCE_INVALID"),
        ("changed-executable", 1, "FINAL_REVIEW_SOURCE_DRIFT"),
        ("unknown-version", 0, "FINAL_APPROVAL_INVALID"),
    ],
)
def test_original_launcher_failure_never_completes_or_refunds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    consumed: int,
    code: str,
) -> None:
    root, seed, policy, driver = native_factory_fixture(tmp_path, monkeypatch)
    driver.write_text(
        NATIVE_DRIVER.replace(
            "native_started = []", LAUNCHER_FAULT_SETUP + "\nnative_started = []"
        )
    )
    result = run_native_driver(root, seed, policy, driver, mode)
    assert result == {"status": "BLOCKED", "code": code, "type": "PhaseBlocked"}
    state_path = seed / "final-preparation/role-state.json"
    state = guards.private_json(state_path)
    assert state["calls_consumed"] == consumed
    assert not list((seed / "final-preparation/sources").glob("environment-*.json"))
    reservations = list((seed / "final-preparation").glob("reservation-*.json"))
    if consumed:
        assert len(reservations) == 1
        before = reservations[0].read_bytes()
        value = guards.private_json(reservations[0])
        assert value["status"] == "RESERVED"
        assert value["ordinal"] == 1
        assert value["deadline_at"] > value["started_at"]
        with pytest.raises(guards.DeploymentError):
            guards.validate_final_preseed(seed, root)
        # Existing consumed originals cannot be repaired by invoking the producer again.
        driver.write_text(NATIVE_DRIVER)
        assert (
            run_native_driver(root, seed, policy, driver, "reuse")["status"]
            == "BLOCKED"
        )
        assert reservations[0].read_bytes() == before
        assert guards.private_json(state_path) == state
        assert (
            len(list((seed / "final-preparation/role-settings").glob("receipt-*.json")))
            == 1
        )
    else:
        assert not reservations
        # The original settings factory already created its namespace. Existing
        # strict zero admission stays unchanged; no rollback/reinitialization.
        with pytest.raises(guards.DeploymentError):
            guards.validate_final_preseed(seed, root)


@pytest.mark.usefixtures("active_runtime_namespace")
@pytest.mark.parametrize("point", ["environment-before", "environment-after"])
def test_environment_partial_publication_does_not_complete_or_refund(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
) -> None:
    root, seed, policy, driver = native_factory_fixture(tmp_path, monkeypatch)
    setup = r"""
previous_environment_save = native_store.Store.save

def environment_fault(self, key, value, **limits):
    if key.startswith("environment-") and mode == "environment-before":
        raise SyntheticFault(mode)
    previous_environment_save(self, key, value, **limits)
    if key.startswith("environment-") and mode == "environment-after":
        raise SyntheticFault(mode)

native_store.Store.save = environment_fault
"""
    driver.write_text(
        NATIVE_DRIVER.replace("native_started = []", setup + "\nnative_started = []")
    )
    assert run_native_driver(root, seed, policy, driver, point) == {
        "status": "FAULT",
        "point": point,
    }
    state_path = seed / "final-preparation/role-state.json"
    before = state_path.read_bytes()
    reservation = next((seed / "final-preparation").glob("reservation-*.json"))
    original_reservation = reservation.read_bytes()
    assert guards.private_json(state_path)["calls_consumed"] == 1
    assert guards.private_json(reservation)["status"] == "RESERVED"
    with pytest.raises(guards.DeploymentError):
        guards.validate_final_preseed(seed, root)
    driver.write_text(NATIVE_DRIVER)
    assert run_native_driver(root, seed, policy, driver, "reuse")["status"] == "BLOCKED"
    assert state_path.read_bytes() == before
    assert reservation.read_bytes() == original_reservation
    assert (
        len(list((seed / "final-preparation/role-settings").glob("receipt-*.json")))
        == 1
    )


@pytest.mark.usefixtures("active_runtime_namespace")
def test_caller_environment_at_original_zero_grants_no_call_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from tools import agent_final_preparation_records as records
    from tools.agent_continuation import digest

    root, seed, policy, driver = native_factory_fixture(tmp_path, monkeypatch)
    state_path = seed / "final-preparation/role-state.json"
    before = state_path.read_bytes()
    state = guards.private_json(state_path)
    identity = {key: state[key] for key in records.IDENTITY}
    sources = seed / "final-preparation/sources"
    sources.mkdir(mode=0o700)
    target = sources / ("environment-" + digest(identity) + ".json")
    # A caller-authored shape is a negative fixture, never historical gate proof.
    target.write_text(
        json.dumps(
            records.seal(
                {
                    "schema_version": "1.0",
                    "kind": "FINAL_AGGREGATE_ENVIRONMENT",
                    **identity,
                    "preparation_identity_digest": "0" * 64,
                    "input_digest": "0" * 64,
                    "review_source_digest": "0" * 64,
                    "launcher_python_version": "3.11.0",
                    "launcher_executable_sha256": "0" * 64,
                }
            )
        )
    )
    target.chmod(0o600)
    caller_bytes = target.read_bytes()
    with pytest.raises(guards.DeploymentError):
        guards.validate_final_preseed(seed, root)
    assert run_native_driver(root, seed, policy, driver, "run")["status"] == "BLOCKED"
    assert state_path.read_bytes() == before
    assert target.read_bytes() == caller_bytes
    assert not list((seed / "final-preparation").glob("reservation-*.json"))
    assert not list((seed / "final-preparation/role-settings").glob("receipt-*.json"))
