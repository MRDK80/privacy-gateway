"""Synthetic mandatory original preseed sources; no initialization or effects."""

from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_runtime_cli as cli
from tools import agent_final_preseed_guard as guard

from tests.test_agent_epic_delivery import _mandate

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private sources")


def seal(value: dict[str, Any], field: str = "record_digest") -> dict[str, Any]:
    value = {k: v for k, v in value.items() if k != field}
    return {**value, field: cli.canonical_digest(value).removeprefix("sha256:")}


def write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    monkeypatch.setattr(time, "time", lambda: 150)
    root, seed = tmp_path / "repo", tmp_path / "seed"
    root.mkdir()
    seed.mkdir(mode=0o700)
    (seed / "final-initialization").mkdir(mode=0o700)
    (seed / "final-preparation").mkdir(mode=0o700)
    mandate: dict[str, Any] = dict(_mandate())
    mandate["schema_version"] = "3.0"
    mandate.update(overrides or {})
    mandate["approval"]["approved_by"] = mandate["owner_identity"]
    unsigned = copy.deepcopy(mandate)
    unsigned_approval = unsigned["approval"]
    assert isinstance(unsigned_approval, dict)
    unsigned_approval.pop("mandate_digest")
    mandate_digest = cli.canonical_digest(unsigned)
    approval_source = mandate["approval"]
    assert isinstance(approval_source, dict)
    approval_source["mandate_digest"] = mandate_digest
    profile = seal(
        {
            "schema_version": "1.0",
            "repository": mandate["repository"],
            "epic": mandate["epic"],
            "owner_identity": mandate["owner_identity"],
            "roadmap_ref": mandate["roadmap_ref"],
            "policy_sha": mandate["policy_sha"],
            "mandate_digest": mandate_digest,
            "max_report_chars": 20000,
            "max_artifact_bytes": 262144,
            "max_final_model_calls": 1,
        }
    )
    config: dict[str, Any] = {key: None for key in guard.CONFIG8_KEYS}
    config.update(
        schema_version="8.0",
        policy_root=str(tmp_path / "policy"),
        owner_identity=mandate["owner_identity"],
        started_at=150,
        task_iterations=0,
        follow_up_issues=0,
        approved_order=[],
        approved_mandate_digest=mandate_digest,
        approved_final_preparation_digest=cli.canonical_digest(profile),
    )
    adapter: dict[str, Any] = {"synthetic": "fixed-adapter"}
    launch = {
        "schema_version": "1.0",
        **{
            k: mandate[k]
            for k in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        },
        "installation_digest": "sha256:" + "1" * 64,
        "runtime_config_digest": cli.launch_config_digest(config, adapter),
        "mandate_digest": mandate_digest,
    }
    config["approved_launch_digest"] = cli.canonical_digest(launch)
    identity = {
        "repository": mandate["repository"],
        "epic": mandate["epic"],
        "mandate_digest": mandate_digest,
        "launch_digest": config["approved_launch_digest"],
    }
    zero = seal(
        {
            "schema_version": "1.0",
            **identity,
            "calls_consumed": 0,
            "pending_reservation_digest": None,
        }
    )
    shared = {**identity, "owner_identity": mandate["owner_identity"]}
    references = {
        "preparation_digest": config["approved_final_preparation_digest"],
        "counter_zero_digest": cli.canonical_digest(zero).removeprefix("sha256:"),
    }
    command = seal(
        {
            "schema_version": "1.0",
            **shared,
            **references,
            "kind": "OWNER_ORIGINAL_FINAL_COUNTER_PROVISIONING",
            "procedure": "ADR303_ORIGINAL_COUNTER_PROVISIONING_V1",
            "policy_sha": mandate["policy_sha"],
        }
    )
    command_digest = cli.canonical_digest(command).removeprefix("sha256:")
    approval = seal(
        {
            "schema_version": "1.0",
            **shared,
            **references,
            "owner_command_digest": command_digest,
            "approved_at": 150,
        },
        "approved_digest",
    )
    initialization = cli.canonical_digest(approval).removeprefix("sha256:")
    receipt = seal(
        {
            "schema_version": "1.0",
            **shared,
            **references,
            "kind": "OWNER_FINAL_COUNTER_INITIALIZATION",
            "approved_at": 150,
            "owner_command_digest": command_digest,
            "initialization_digest": initialization,
        }
    )
    pin = seal(
        {
            "schema_version": "1.0",
            **shared,
            "initialization_digest": initialization,
            "installation_receipt_digest": cli.canonical_digest(receipt).removeprefix(
                "sha256:"
            ),
        }
    )
    for name, value in (
        ("epic-runtime", config),
        ("mandate", mandate),
        ("runtime-adapter", adapter),
        ("launch", launch),
        ("final-preparation-profile", profile),
    ):
        write(seed / (name + ".json"), value)
    for name, value in (
        ("command-source", command),
        ("approval", approval),
        ("install-receipt", receipt),
        ("approval-pin", pin),
    ):
        write(seed / "final-initialization" / (name + ".json"), value)
    write(seed / "final-preparation" / "role-state.json", zero)
    return root, seed


def test_valid_preseed_zero_has_no_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed = fixture(tmp_path, monkeypatch)
    before = {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*.json")}
    assert guard.validate_preseed(seed, root)["calls_consumed"] == 0
    assert before == {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*.json")}


@pytest.mark.parametrize(
    "name", ["command-source", "approval", "approval-pin", "install-receipt"]
)
def test_missing_source_never_initializes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    root, seed = fixture(tmp_path, monkeypatch)
    (seed / "final-initialization" / (name + ".json")).unlink()
    with pytest.raises(cli.DeploymentError):
        guard.validate_preseed(seed, root)


@pytest.mark.parametrize("namespace", ["role-settings", "sources"])
@pytest.mark.parametrize("kind", ["empty", "ownership", "dangling", "file"])
def test_any_zero_native_namespace_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, namespace: str, kind: str
) -> None:
    root, seed = fixture(tmp_path, monkeypatch)
    path = seed / "final-preparation" / namespace
    if kind == "dangling":
        path.symlink_to(seed / "missing")
    elif kind == "file":
        path.write_bytes(b"inert")
    else:
        path.mkdir(mode=0o700)
        if kind == "ownership":
            (path / "ownership").mkdir(mode=0o700)
    with pytest.raises(cli.DeploymentError):
        guard.validate_preseed(seed, root)


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "spent",
        "bool",
        "extra",
        "duplicate",
        "oversize",
        "fifo",
        "link",
        "hardlink",
        "mode",
        "hash",
        "source-reference",
    ],
)
def test_closed_private_sources_fail_without_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    root, seed = fixture(tmp_path, monkeypatch)
    path = seed / "final-preparation" / "role-state.json"
    if failure == "source-reference":
        path = seed / "final-initialization" / "approval-pin.json"
    value = json.loads(path.read_text())
    if failure == "missing":
        path.unlink()
    elif failure in {"spent", "bool", "extra", "hash", "source-reference"}:
        if failure == "spent":
            value.update(calls_consumed=1, pending_reservation_digest="a" * 64)
        elif failure == "bool":
            value["calls_consumed"] = False
        elif failure == "extra":
            value["unexpected"] = 1
        elif failure == "hash":
            value["record_digest"] = "0" * 64
        else:
            value["initialization_digest"] = "0" * 64
        write(path, value if failure == "hash" else seal(value))
    elif failure == "duplicate":
        path.write_text('{"schema_version":"1.0", "schema_version":"1.0"}')
    elif failure == "oversize":
        path.write_bytes(b" " * 262145)
    elif failure == "mode":
        path.chmod(0o644)
    elif failure == "hardlink":
        os.link(path, seed / "copy")
    else:
        path.unlink()
        if failure == "fifo":
            getattr(os, "mkfifo")(path, 0o600)
        else:
            path.symlink_to(seed / "missing")
    with pytest.raises(cli.DeploymentError):
        guard.validate_preseed(seed, root)


def test_reservation_blocks_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed = fixture(tmp_path, monkeypatch)
    config = json.loads((seed / "epic-runtime.json").read_text())
    digest = cli.canonical_digest(
        {"launch_digest": config["approved_launch_digest"], "ordinal": 1}
    ).removeprefix("sha256:")
    (seed / "final-preparation" / ("reservation-" + digest + ".json")).write_bytes(
        b"inert"
    )
    with pytest.raises(cli.DeploymentError):
        guard.validate_preseed(seed, root)


def consumed_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Any]:
    from tools.agent_continuation_backend import SettingsAdapter
    from tools.agent_epic_loop import RunnerLock
    from tools.agent_final_preparation_review import AggregateReview
    from tools.agent_final_preparation_role import RoleCounter
    from tools.agent_final_preparation_storage import Store

    from tests.test_agent_final_preparation_review import setup

    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    _, review = setup(native, monkeypatch)
    original = review.counter.store.owner
    sources = tmp_path / "sources"
    sources.mkdir(mode=0o700)
    _, seed = fixture(
        sources,
        monkeypatch,
        {
            key: original[key]
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "owner_identity",
            )
        },
    )
    root = review.adapter.root
    config = json.loads((seed / "epic-runtime.json").read_text())
    profile = json.loads((seed / "final-preparation-profile.json").read_text())
    continuation = {
        **review.adapter.profile,
        **{
            key: profile[key]
            for key in (
                "repository",
                "epic",
                "roadmap_ref",
                "policy_sha",
                "mandate_digest",
                "owner_identity",
            )
        },
    }
    backend = review.adapter.owner
    continuation["backend_digest"] = guard._content(backend)
    config["approved_continuation_digest"] = cli.canonical_digest(continuation)
    # Runtime configuration includes continuation approval; regenerate the entire
    # original setup with that fixed approval before consuming any authority.
    launch = json.loads((seed / "launch.json").read_text())
    launch["runtime_config_digest"] = cli.launch_config_digest(
        config, json.loads((seed / "runtime-adapter.json").read_text())
    )
    config["approved_launch_digest"] = cli.canonical_digest(launch)
    write(seed / "launch.json", launch)
    write(seed / "epic-runtime.json", config)
    zero_path = seed / "final-preparation" / "role-state.json"
    zero = json.loads(zero_path.read_text())
    zero["launch_digest"] = config["approved_launch_digest"]
    zero = seal(zero)
    write(zero_path, zero)
    initialization = seed / "final-initialization"
    command = json.loads((initialization / "command-source.json").read_text())
    command.update(
        launch_digest=config["approved_launch_digest"],
        counter_zero_digest=guard._content(zero),
    )
    command = seal(command)
    approval = json.loads((initialization / "approval.json").read_text())
    approval.update(
        launch_digest=config["approved_launch_digest"],
        counter_zero_digest=guard._content(zero),
        owner_command_digest=guard._content(command),
    )
    approval = seal(approval, "approved_digest")
    receipt = json.loads((initialization / "install-receipt.json").read_text())
    receipt.update(
        launch_digest=config["approved_launch_digest"],
        counter_zero_digest=guard._content(zero),
        owner_command_digest=guard._content(command),
        initialization_digest=guard._content(approval),
    )
    receipt = seal(receipt)
    pin = json.loads((initialization / "approval-pin.json").read_text())
    pin.update(
        launch_digest=config["approved_launch_digest"],
        initialization_digest=guard._content(approval),
        installation_receipt_digest=guard._content(receipt),
    )
    for name, value in (
        ("command-source", command),
        ("approval", approval),
        ("install-receipt", receipt),
        ("approval-pin", seal(pin)),
    ):
        write(initialization / (name + ".json"), value)
    write(seed / "continuation-profile.json", continuation)
    write(seed / "role-backend.json", backend)
    with RunnerLock(seed):
        store = Store(seed, root, profile, review.counter.store.guard)
    adapter = SettingsAdapter(
        review.adapter.delegate, backend, continuation, store.directory, store.guard
    )
    review = AggregateReview(
        RoleCounter(store, config["approved_launch_digest"]), adapter
    )
    with RunnerLock(seed):
        review.review(started_at=100, deadline_at=200)
    return root, seed, review


def test_completed_original_native_chain_is_readonly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed, _ = consumed_fixture(tmp_path, monkeypatch)
    before = {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*.json")}
    assert guard.validate_preseed(seed, root)["calls_consumed"] == 1
    assert before == {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*.json")}


@pytest.mark.parametrize(
    "attack",
    [
        "missing-init",
        "missing-settings",
        "output",
        "terminal",
        "settings",
        "input",
        "backend",
        "state",
        "RESERVED",
        "UNKNOWN",
        "gate",
        "snapshot",
        "policy",
        "session",
        "roadmap",
        "receipt-link",
        "receipt-oversize",
    ],
)
def test_consumed_native_chain_rejects_without_refund(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    root, seed, review = consumed_fixture(tmp_path, monkeypatch)
    cache_path = review.directory / (review.key + ".json")
    cache = json.loads(cache_path.read_text())
    reservation_path = review.counter.store.directory / (review.counter.key + ".json")
    reservation = json.loads(reservation_path.read_text())
    settings_path = review.adapter.store.store.directory / (
        "receipt-" + reservation["input_digest"] + ".json"
    )
    settings = json.loads(settings_path.read_text())
    if attack == "missing-init":
        (seed / "final-initialization" / "command-source.json").unlink()
    elif attack == "missing-settings":
        settings_path.unlink()
    elif attack == "receipt-link":
        settings_path.unlink()
        settings_path.symlink_to(cache_path)
    elif attack == "receipt-oversize":
        settings_path.write_bytes(b" " * 262145)
    elif attack in {"terminal", "settings"}:
        if attack == "terminal":
            settings["terminal"]["adapter_exit_code"] = 1
        else:
            settings["receipt"]["effective"]["reasoning"] = "high"
        write(settings_path, settings)
    elif attack in {"input", "backend", "RESERVED", "UNKNOWN"}:
        if attack in {"input", "backend"}:
            reservation[attack + "_digest"] = "0" * 64
        else:
            reservation["status"] = attack
        write(reservation_path, seal(reservation))
    elif attack == "state":
        (seed / "final-preparation" / "role-state.json").unlink()
    elif attack == "roadmap":
        from tests.test_agent_final_preparation_review import git

        git(
            root,
            "update-ref",
            "refs/heads/" + review.counter.store.owner["roadmap_ref"],
            cache["identity"]["main_sha"],
        )
    else:
        if attack == "output":
            cache["output"]["verdict"] = "FAIL_ESCALATE"
        elif attack == "gate":
            cache["request"]["gate_evidence"]["status"] = "failed"
        elif attack == "snapshot":
            cache["request"]["gate_evidence"]["snapshot"]["tree_hash"] = "0" * 40
        elif attack == "policy":
            cache["policy"]["AGENTS.md"] = "untrusted"
        else:
            cache["session_id"] = "final-controller-" + "0" * 32
        write(cache_path, cache)
    before = {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*.json")}
    with pytest.raises(cli.DeploymentError):
        guard.validate_preseed(seed, root)
    assert before == {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*.json")}


def test_consumed_preseed_policy_single_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    root, seed, _ = consumed_fixture(tmp_path, monkeypatch)
    original = subprocess.Popen
    starts: list[list[str]] = []

    def popen(argv: Any, **kwargs: Any) -> Any:
        if argv[0] == "/usr/bin/git" and (
            "--batch" in argv or (
                "show" in argv and any(
                    argv[-1].endswith(":" + path)
                    for path in cli._final_preseed_POLICY_FILES
                )
            )
        ):
            starts.append(list(argv))
        return original(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    before = {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*.json")}
    cli.validate_final_preseed(seed, root)
    assert len(starts) == 1 and starts[0][-2:] == ["cat-file", "--batch"]
    assert before == {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*.json")}


def test_consumed_preseed_policy_fresh_next_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed, _ = consumed_fixture(tmp_path, monkeypatch)
    cli.validate_final_preseed(seed, root)
    profile = json.loads((seed / "final-preparation-profile.json").read_text())
    # Remove the actual pinned commit object; no prior policy result can admit it.
    oid = profile["policy_sha"]
    path = root / ".git" / "objects" / oid[:2] / oid[2:]
    assert path.is_file()
    path.unlink()
    with pytest.raises(cli.DeploymentError):
        cli.validate_final_preseed(seed, root)


def test_consumed_policy_legacy_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    root, seed, _ = consumed_fixture(tmp_path, monkeypatch)
    original_consumed = cli._final_preseed_consumed
    original_popen = subprocess.Popen
    profile = json.loads((seed / "final-preparation-profile.json").read_text())
    expressions = [
        profile["policy_sha"] + ":" + path for path in cli._final_preseed_POLICY_FILES
    ]
    starts: list[list[str]] = []
    dispatch: list[str] = []

    def legacy_consumed(
        seed_fd: int,
        preparation_fd: int,
        root: Path,
        config: dict[str, Any],
        profile: dict[str, Any],
        current: dict[str, Any],
        identity: dict[str, Any],
        reservation_name: str,
    ) -> None:
        # Unit dispatch only: outer admission still validates original config8.
        # Forward the actual open FDs and every source record unchanged.
        dispatch.append(config["schema_version"])
        original_consumed(
            seed_fd, preparation_fd, root, {**config, "schema_version": "7.0"},
            profile, current, identity, reservation_name,
        )

    def popen(argv: Any, **kwargs: Any) -> Any:
        if argv[0] == "/usr/bin/git" and (
            "--batch" in argv
            or (
                list(argv[-4:-1]) == ["show", "--no-ext-diff", "--no-textconv"]
                and argv[-1] in expressions
            )
        ):
            starts.append(list(argv))
        return original_popen(argv, **kwargs)

    monkeypatch.setattr(cli, "_final_preseed_consumed", legacy_consumed)
    monkeypatch.setattr(subprocess, "Popen", popen)
    before = {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*.json")}
    cli.validate_final_preseed(seed, root)
    assert dispatch == ["8.0"]
    assert [argv[-4:] for argv in starts] == [
        ["show", "--no-ext-diff", "--no-textconv", expression]
        for expression in expressions
    ]
    assert len(starts) == 7
    assert not any("--batch" in argv for argv in starts)
    assert before == {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*.json")}


@pytest.mark.parametrize("case", [
    "partial_stream_success", "optional_exact_missing", "empty_blob_not_absent",
    "missing_mandatory_after_full_loop", "wrong_missing_expression",
    "malformed_header", "wrong_type", "noncanonical_size", "oversized_body",
    "truncated_body", "wrong_separator", "hash_mismatch", "trailing_stdout",
    "nonzero_exit", "request_write_timeout", "header_timeout",
    "last_separator_postread_timeout", "hash_decode_total_deadline",
    "prior_expiry_before_reset", "final_wait_return_after_deadline",
    "cleanup_failure_closed", "semantic_primary_preserved",
    "interruption_primary_preserved", "fixed_order_and_wire_cap",
])
def test_consumed_policy_batch_protocol(
    monkeypatch: pytest.MonkeyPatch, case: str, tmp_path: Path,
) -> None:
    import hashlib
    import selectors
    import subprocess
    from types import SimpleNamespace

    clock = [0.0]
    wire = bytearray()
    pending = bytearray()
    expressions: list[bytes] = []
    closed: list[str] = []
    cleanup_faults: list[str] = []
    child_cleanup: list[str] = []
    sha = "a" * 40
    paths = cli._final_preseed_POLICY_FILES

    class Pipe:
        def __init__(self, name: str, fd: int) -> None:
            self.name, self.fd = name, fd

        def fileno(self) -> int:
            return self.fd

        def close(self) -> None:
            closed.append(self.name)
            if self.name == "stdout" and case in {
                "cleanup_failure_closed", "semantic_primary_preserved",
                "interruption_primary_preserved",
            }:
                cleanup_faults.append(self.name)
                raise OSError("secondary close failure")

    class Child:
        def __init__(self) -> None:
            self.stdin, self.stdout = Pipe("stdin", 101), Pipe("stdout", 102)
            self.returncode: int | None = None

        def poll(self) -> int | None:
            child_cleanup.append("poll")
            return self.returncode

        def kill(self) -> None:
            child_cleanup.append("kill")
            self.returncode = -9

        def wait(self, timeout: float) -> int:
            child_cleanup.append("wait")
            assert 0 <= timeout <= 5
            if case == "final_wait_return_after_deadline":
                clock[0] = 5.0
            if self.returncode is None:
                self.returncode = 1 if case == "nonzero_exit" else 0
            assert self.returncode is not None
            return self.returncode

    child = Child()
    starts: list[Any] = []

    def popen(argv: Any, **kwargs: Any) -> Any:
        starts.append((argv, kwargs))
        return child

    def write(fd: int, data: Any) -> int:
        assert fd == 101
        chunk = bytes(data[:3])
        pending.extend(chunk)
        if case == "request_write_timeout":
            clock[0] += 4
        if pending.endswith(b"\n"):
            expression = bytes(pending[:-1])
            pending.clear()
            expressions.append(expression)
            body = b"  value\n"
            if case == "empty_blob_not_absent":
                body = b""
            oid = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
            header = f"{oid} blob {len(body)}\n".encode()
            if case in {"optional_exact_missing", "missing_mandatory_after_full_loop"}:
                wire.extend(expression + b" missing\n")
                return len(chunk)
            if case == "wrong_missing_expression":
                header, body = b"wrong missing\n", b""
            elif case == "malformed_header":
                header = b"broken\n"
            elif case == "wrong_type":
                header = header.replace(b"blob", b"tree")
            elif case == "noncanonical_size":
                header = f"{oid} blob 01\n".encode()
            elif case == "oversized_body":
                header = f"{oid} blob 262145\n".encode()
            elif case == "hash_mismatch":
                body = b"X" + body[1:]
            wire.extend(header + body)
            if case == "truncated_body":
                del wire[-1:]
            else:
                wire.extend(b"X" if case == "wrong_separator" else b"\n")
        return len(chunk)

    def read(fd: int, count: int) -> bytes:
        assert fd == 102
        size = min(count, 2, len(wire))
        result = bytes(wire[:size])
        del wire[:size]
        if case == "last_separator_postread_timeout" and result == b"\n" and not wire:
            clock[0] += 4
        if not result and case == "trailing_stdout":
            return b"X"
        return result

    class Selector:
        def register(self, fd: int, event: int) -> None:
            self.fd, self.event = fd, event

        def unregister(self, fd: int) -> None:
            assert self.fd == fd

        def select(self, timeout: float) -> list[Any]:
            assert 0 < timeout <= 5
            if case == "header_timeout" and self.fd == 102:
                clock[0] += 4
                return []
            return [(None, self.event)]

        def close(self) -> None:
            closed.append("selector")

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(os, "set_blocking", lambda *_: None)
    monkeypatch.setattr(os, "write", write)
    monkeypatch.setattr(os, "read", read)
    monkeypatch.setattr(selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(cli, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    if case == "hash_decode_total_deadline":
        original_sha1 = hashlib.sha1

        class Hasher:
            def __init__(self, data: bytes) -> None:
                self.value = original_sha1(data)

            def hexdigest(self) -> str:
                clock[0] += 5
                return self.value.hexdigest()

        monkeypatch.setattr(cli, "hashlib", SimpleNamespace(sha1=Hasher))
    successful = {"partial_stream_success", "optional_exact_missing",
                  "empty_blob_not_absent", "fixed_order_and_wire_cap"}
    primary = {"semantic_primary_preserved", "interruption_primary_preserved"}
    primary_error: BaseException | None = None
    if case == "semantic_primary_preserved":
        primary_error = cli.DeploymentError("FINAL_INITIALIZATION_SOURCE_INVALID")
    elif case == "interruption_primary_preserved":
        primary_error = KeyboardInterrupt()

    def exercise() -> None:
        with cli._FinalPreseedPolicyObjects(tmp_path) as stream:
            values = {}
            for path in paths:
                value = stream.policy(sha, path)
                if value is not None:
                    values[path] = value
                if case == "prior_expiry_before_reset":
                    clock[0] += 5
                if case in primary:
                    assert primary_error is not None
                    raise primary_error
            if case == "missing_mandatory_after_full_loop":
                cli._final_preseed_require("AGENTS.md" in values)
            if case == "empty_blob_not_absent":
                assert values == dict.fromkeys(paths, "")
            if case == "optional_exact_missing":
                assert values == {}

    if case in primary:
        assert primary_error is not None
        with pytest.raises(type(primary_error)) as caught:
            exercise()
        assert caught.value is primary_error
        if case == "semantic_primary_preserved":
            assert str(caught.value) == "FINAL_INITIALIZATION_SOURCE_INVALID"
        assert cleanup_faults == ["stdout"]
        assert child_cleanup == ["poll", "kill", "wait"]
        assert child.returncode == -9
    elif case in successful:
        exercise()
    else:
        with pytest.raises(cli.DeploymentError):
            exercise()
    assert len(starts) == 1
    assert starts[0][0][-2:] == ["cat-file", "--batch"]
    assert starts[0][1]["stderr"] == subprocess.DEVNULL
    assert {"stdin", "stdout", "selector"} <= set(closed)
    if case in successful or case == "missing_mandatory_after_full_loop":
        assert expressions == [(sha + ":" + path).encode() for path in paths]
