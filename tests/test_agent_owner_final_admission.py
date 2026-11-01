"""Existing-only ownership never provisions a missing owner source namespace."""

from __future__ import annotations

import importlib
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO, cast

import pytest
from tools import agent_final_preparation_runtime as original_runtime
from tools.agent_continuation import digest as source_digest
from tools.agent_epic_loop import LoopError, PhaseBlocked, RunnerLock
from tools.agent_learning_evidence import SourceReader

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX owner source")


def _admitted_owner_invocation(**fields: Any) -> Any:
    """Hashable real invocation envelope; original admission remains doubled."""
    from tools import agent_owner_final_admission as admission

    value = admission._OriginalInvocation.__new__(admission._OriginalInvocation)
    value.__dict__.update(fields)
    return value


@pytest.mark.parametrize("case", ["valid", "missing", "busy", "symlink", "same_object"])
def test_owner_borrowed_admission(tmp_path: Path, case: str) -> None:
    root, seed = tmp_path / "repo", tmp_path / "private"
    root.mkdir()
    seed.mkdir(mode=0o700)
    path = seed / "epic-loop.lock"
    if case != "missing":
        path.write_bytes(b"")
        path.chmod(0o600)
    if case == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(b"unchanged")
        path.unlink()
        path.symlink_to(outside)
    if case == "valid":
        identity = path.stat().st_dev, path.stat().st_ino
        with RunnerLock.owner_existing(seed, root) as lock:
            assert type(lock) is RunnerLock
            assert RunnerLock.current_owned(seed) is lock
            lock.assert_owned(seed)
            assert (path.stat().st_dev, path.stat().st_ino) == identity
        assert path.read_bytes() == b""
    elif case == "same_object":
        lock = RunnerLock.owner_existing(seed, root)
        with lock:
            outer_stream, outer_pid = lock._stream, lock._owner_pid
            with pytest.raises(LoopError, match="LOCK_FAILED"):
                lock.__enter__()
            assert lock._stream is outer_stream and lock._owner_pid == outer_pid
            assert outer_stream is not None and not outer_stream.closed
            assert RunnerLock.current_owned(seed) is lock
            lock.assert_owned(seed)
    elif case == "busy":
        with RunnerLock(seed):
            with pytest.raises(LoopError, match="LOCK_FAILED"):
                with RunnerLock.owner_existing(seed, root):
                    pytest.fail("existing owner lock was reentered")
    else:
        with pytest.raises(LoopError, match="LOCK_FAILED"):
            with RunnerLock.owner_existing(seed, root):
                pytest.fail("unsafe or absent original was admitted")
        if case == "missing":
            assert not path.exists()
        else:
            assert path.is_symlink()
            assert outside.read_bytes() == b"unchanged"


@pytest.mark.parametrize(
    "fault",
    [
        "enter_probe", "exit_unlock", "parent_close", "descriptor_close",
        "cleanup_interrupt",
    ],
)
def test_owner_uncertain_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    root, seed = tmp_path / "repo", tmp_path / "private"
    root.mkdir()
    seed.mkdir(mode=0o700)
    path = seed / "epic-loop.lock"
    path.write_bytes(b"")
    path.chmod(0o600)
    lock = RunnerLock.owner_existing(seed, root)
    streams: list[BinaryIO] = []
    original = lock._open_existing

    def capture() -> BinaryIO:
        stream = original()
        streams.append(stream)
        return stream

    monkeypatch.setattr(lock, "_open_existing", capture)
    if fault in {"parent_close", "descriptor_close"}:
        raw_close, raw_fdopen = os.close, os.fdopen
        closed: list[int] = []

        def fail_close(descriptor: int) -> None:
            raw_close(descriptor)
            closed.append(descriptor)
            raise OSError("synthetic close acknowledgement failure")

        def capture_fdopen(descriptor: int, mode: str) -> BinaryIO:
            if fault == "descriptor_close":
                raise OSError("synthetic stream transfer failure")
            stream = cast(BinaryIO, raw_fdopen(descriptor, mode))
            streams.append(stream)
            return stream

        monkeypatch.setattr(os, "close", fail_close)
        monkeypatch.setattr(os, "fdopen", capture_fdopen)
        with pytest.raises(LoopError, match="LOCK_FAILED"):
            lock.__enter__()
        assert len(closed) == (2 if fault == "descriptor_close" else 1)
    elif fault == "cleanup_interrupt":
        cleanup = lock._clear_existing

        def fail_primary(_directory: Path) -> None:
            raise LoopError("LOCK_FAILED")

        def interrupt_cleanup(stream: BinaryIO | None) -> None:
            cleanup(stream)
            raise KeyboardInterrupt

        monkeypatch.setattr(lock, "_assert_existing_owned", fail_primary)
        monkeypatch.setattr(lock, "_clear_existing", interrupt_cleanup)
        with pytest.raises(LoopError, match="LOCK_FAILED"):
            lock.__enter__()
    elif fault == "enter_probe":
        def fail_probe(_directory: Path) -> None:
            raise OSError("synthetic ownership probe failure")

        monkeypatch.setattr(lock, "_assert_existing_owned", fail_probe)
        with pytest.raises(LoopError, match="LOCK_FAILED"):
            lock.__enter__()
    else:
        lock.__enter__()

        def fail_unlock(_stream: Any) -> None:
            raise OSError("synthetic unlock failure")

        monkeypatch.setattr(lock, "_unlock", fail_unlock)
        with pytest.raises(LoopError, match="LOCK_FAILED"):
            lock.__exit__(None, None, None)
    assert seed.resolve() not in RunnerLock._held
    assert lock._stream is None and lock._owner_pid is None
    if fault == "descriptor_close":
        assert not streams
    else:
        assert streams and all(stream.closed for stream in streams)


@pytest.mark.parametrize(
    "case",
    [
        "oversized_before_precheck", "growth_between_precheck_and_helper",
        "unknown_owner_sibling", "changed_owner_namespace_identity",
    ],
)
def test_owner_fivegraph_bounded_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Isolate real five-file/namespace guards, not installed human authority."""
    from tools import agent_final_preparation_owner as owner
    from tools import agent_owner_final_admission as admission

    from tests.test_agent_owner_final_records import fixture

    seed, root, candidate = fixture(tmp_path)
    directory = seed / "final-owner"
    invocation = _admitted_owner_invocation(
        state=seed, root=root, candidate=candidate,
        frames=SimpleNamespace(
            sources={"profile": {"max_artifact_bytes": 262144},
                     "mandate": {"issued_at": 90, "expires_at": 120}},
            anchor={"deadline_at": 120},
        ),
        pre_merge_sources=SimpleNamespace(stable_assessment=lambda: None),
        guard=lambda: None,
    )
    # Installation and assessment are explicit mocked boundaries in this unit.
    monkeypatch.setattr(admission, "_installed_sources", lambda _: None)
    monkeypatch.setattr("time.time", lambda: 115)
    original_read = owner.read_current_owner_approval
    helper_calls: list[str] = []

    def read_at_boundary(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        helper_calls.append(case)
        if case == "growth_between_precheck_and_helper":
            (directory / "unexpected.json").write_bytes(b"{}")
            (directory / "unexpected.json").chmod(0o600)
        value = original_read(*args, **kwargs)
        if case == "changed_owner_namespace_identity":
            retained = seed / "retained-owner"
            directory.rename(retained)
            directory.mkdir(mode=0o700)
            for source in retained.iterdir():
                copied = directory / source.name
                copied.write_bytes(source.read_bytes())
                copied.chmod(0o600)
        return value

    monkeypatch.setattr(owner, "read_current_owner_approval", read_at_boundary)
    consumed: list[int] = []
    if case == "oversized_before_precheck":
        raw_scandir = os.scandir

        class CountedScan:
            def __init__(self, parent: int) -> None:
                self.entries = raw_scandir(parent)

            def __iter__(self) -> CountedScan:
                return self

            def __next__(self) -> os.DirEntry[str]:
                entry = next(self.entries)
                consumed.append(1)
                return entry

            def close(self) -> None:
                self.entries.close()

            def __enter__(self) -> CountedScan:
                return self

            def __exit__(self, *exc: object) -> None:
                self.close()

        for number in range(12):
            path = directory / f"unexpected-{number}.json"
            path.write_bytes(b"{}")
            path.chmod(0o600)
        monkeypatch.setattr(os, "scandir", CountedScan)
    elif case == "unknown_owner_sibling":
        (directory / "command-source.json").rename(directory / "unexpected.json")
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        admission._current_owner_approval(invocation)
    assert len(helper_calls) == (
        0 if case in {"oversized_before_precheck", "unknown_owner_sibling"} else 1
    )
    if case == "oversized_before_precheck":
        assert consumed == [1] * 6
    if case == "changed_owner_namespace_identity":
        assert len(list((seed / "retained-owner").iterdir())) == 5
        assert len(list(directory.iterdir())) == 5


@pytest.mark.parametrize(
    "case",
    [
        "validation_plus_close_oserror", "keyboardinterrupt_plus_close_oserror",
        "sole_close_failure", "invalid_scan_plus_close_interrupt",
    ],
)
def test_owner_fivegraph_resource_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Real descriptor cleanup preserves a primary failure and closes siblings."""
    from tools import agent_final_preparation_owner as owner

    if case == "invalid_scan_plus_close_interrupt":
        observed: list[int] = []
        closed: list[bool] = []

        class Entries:
            def __iter__(self) -> Entries:
                return self

            def __next__(self) -> SimpleNamespace:
                number = len(observed)
                observed.append(number)
                return SimpleNamespace(name=f"entry-{number}")

            def close(self) -> None:
                closed.append(True)
                raise KeyboardInterrupt

        monkeypatch.setattr(os, "scandir", lambda _: Entries())
        with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
            owner._current_owner_names(-1)
        assert observed == list(range(6)) and closed == [True]
        return
    path = tmp_path / "descriptor-source"
    path.write_bytes(b"synthetic")
    descriptors = (os.open(path, os.O_RDONLY), os.open(path, os.O_RDONLY))
    raw_close = os.close
    attempted: list[int] = []
    primary: BaseException = (
        KeyboardInterrupt() if case == "keyboardinterrupt_plus_close_oserror"
        else PhaseBlocked("SYNTHETIC_PRIMARY")
    )

    def close_with_uncertain_ack(descriptor: int) -> None:
        attempted.append(descriptor)
        raw_close(descriptor)
        raise OSError("synthetic close acknowledgement failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "close", close_with_uncertain_ack)
        if case == "sole_close_failure":
            with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
                owner._current_owner_close(descriptors)
        else:
            with pytest.raises(type(primary)) as caught:
                try:
                    raise primary
                finally:
                    owner._current_owner_close(descriptors)
            assert caught.value is primary
    assert attempted == list(descriptors)
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize(
    "case",
    [
        "candidate_link", "approval_digest_link", "installation_receipt_link",
        "command_demo_link", "owner_identity", "approval_demo_time_order",
    ],
)
def test_owner_original_rejections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Real five-original joins reject before the mocked installed admission."""
    import json
    from types import SimpleNamespace

    from tools import agent_final_preparation_records as final_records
    from tools import agent_owner_final_admission as admission

    from tests.test_agent_owner_final_records import NAMES, fixture, write

    seed, root, candidate = fixture(tmp_path)
    directory = seed / "final-owner"
    values: dict[str, dict[str, Any]] = {
        name: json.loads((directory / name).read_bytes()) for name in NAMES
    }
    if case == "candidate_link":
        candidate = final_records.seal(
            {**candidate, "roadmap_sha": "0" * 40}, self_digest="candidate_digest",
        )
    elif case == "approval_digest_link":
        values["approval-pin.json"]["approval_digest"] = "0" * 64
        values["approval-pin.json"] = final_records.seal(values["approval-pin.json"])
    elif case == "installation_receipt_link":
        values["approval-pin.json"]["installation_receipt_digest"] = "0" * 64
        values["approval-pin.json"] = final_records.seal(values["approval-pin.json"])
    elif case == "command_demo_link":
        values["command-source.json"]["final_demo_digest"] = "0" * 64
        values["command-source.json"] = final_records.seal(
            values["command-source.json"],
        )
    elif case == "owner_identity":
        values["approval.json"]["owner_identity"] = "different-synthetic-owner"
        values["approval.json"] = final_records.seal(
            values["approval.json"], self_digest="approved_digest",
        )
    else:
        assert case == "approval_demo_time_order"
        # One logical time mutation; propagate all digest links consistently.
        values["final-demo-source.json"]["completed_at"] = 111
        demo = final_records.seal(values["final-demo-source.json"])
        approval = final_records.seal(
            {**values["approval.json"], "final_demo_digest": demo["record_digest"]},
            self_digest="approved_digest",
        )
        command = final_records.seal({
            **values["command-source.json"],
            "approval_digest": approval["approved_digest"],
            "final_demo_digest": demo["record_digest"],
        })
        receipt = final_records.seal({
            **values["install-receipt.json"],
            "approval_digest": approval["approved_digest"],
            "owner_command_digest": command["record_digest"],
        })
        pin = final_records.seal({
            **values["approval-pin.json"],
            "approval_digest": approval["approved_digest"],
            "installation_receipt_digest": receipt["record_digest"],
        })
        values.update({
            "approval.json": approval, "approval-pin.json": pin,
            "install-receipt.json": receipt, "command-source.json": command,
            "final-demo-source.json": demo,
        })
        assert demo["completed_at"] > approval["approved_at"]
    for name in NAMES:
        write(directory / name, values[name])
    invocation = _admitted_owner_invocation(
        state=seed, root=root, candidate=candidate,
        frames=SimpleNamespace(
            sources={"profile": {"max_artifact_bytes": 262144},
                     "mandate": {"issued_at": 90, "expires_at": 120}},
            anchor={"deadline_at": 120},
        ),
        pre_merge_sources=SimpleNamespace(stable_assessment=lambda: None),
        guard=lambda: None,
    )
    reached: list[bool] = []

    def installed(_invocation: Any) -> None:
        reached.append(True)
        pytest.fail("malformed original graph reached installed admission")

    monkeypatch.setattr(admission, "_installed_sources", installed)
    monkeypatch.setattr("time.time", lambda: 115)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        admission._current_owner_approval(invocation)
    assert not reached
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


@pytest.mark.parametrize(
    "case",
    ["approval_absent", "pin_absent", "receipt_absent", "command_absent",
     "demo_absent"],
)
def test_owner_partial_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Four remaining originals never become a newly created owner graph."""
    from types import SimpleNamespace

    from tools import agent_owner_final_admission as admission

    from tests.test_agent_owner_final_records import fixture

    seed, root, candidate = fixture(tmp_path)
    directory = seed / "final-owner"
    missing = {
        "approval_absent": "approval.json", "pin_absent": "approval-pin.json",
        "receipt_absent": "install-receipt.json",
        "command_absent": "command-source.json",
        "demo_absent": "final-demo-source.json",
    }[case]
    (directory / missing).unlink()
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    invocation = _admitted_owner_invocation(
        state=seed, root=root, candidate=candidate,
        frames=SimpleNamespace(
            sources={"profile": {"max_artifact_bytes": 262144},
                     "mandate": {"issued_at": 90, "expires_at": 120}},
            anchor={"deadline_at": 120},
        ),
        pre_merge_sources=SimpleNamespace(stable_assessment=lambda: None),
        guard=lambda: None,
    )
    reached: list[bool] = []

    def installed(_invocation: Any) -> None:
        reached.append(True)
        pytest.fail("partial graph reached installed source admission")

    monkeypatch.setattr(admission, "_installed_sources", installed)
    monkeypatch.setattr("time.time", lambda: 115)
    with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
        admission._current_owner_approval(invocation)
    assert len(before) == 4 and not reached
    assert not (directory / missing).exists()
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


@pytest.mark.parametrize(
    "case",
    [
        "own_bytes_before_import", "runtime_bytes_before_import",
        "support_inventory_before_effect_import", "explicit_digest_mismatch",
        "stdin_oversize", "secret_marker_refused_without_logging",
    ],
)
def test_owner_bootstrap_source_privacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture,
) -> None:
    """Negative public/bootstrap boundaries; no positive installed authority."""
    import builtins
    import hashlib
    import json
    import sys
    from types import SimpleNamespace

    from tools import agent_final_preparation_records as final_records
    from tools import agent_owner_final_admission as admission
    from tools import agent_owner_final_approve as procedure
    from tools.agent_owner_final_input import OwnerInputError, parse_owner_input

    from tests.test_agent_owner_final_input import explicit_input
    from tests.test_agent_owner_final_records import fixture

    if case == "explicit_digest_mismatch":
        value = explicit_input()
        with pytest.raises(OwnerInputError, match="FINAL_APPROVAL_INVALID"):
            parse_owner_input(
                json.dumps(value).encode(), "0" * 64,
                issued_at=90, expires_at=120, max_bytes=262144,
            )
        return
    if case == "stdin_oversize":
        path = tmp_path / "bounded-input"
        path.write_bytes(b"x" * 9)
        with path.open("rb") as stream, monkeypatch.context() as patch:
            patch.setattr(sys, "stdin", SimpleNamespace(buffer=stream))
            patch.setattr("time.time", lambda: 115)
            with pytest.raises(procedure._Invalid, match="FINAL_APPROVAL_INVALID"):
                procedure._stdin(8, 150)
        return
    if case == "secret_marker_refused_without_logging":
        seed, root, candidate = fixture(tmp_path)
        directory = seed / "final-owner"
        demo = json.loads((directory / "final-demo-source.json").read_bytes())
        marker = "SYNTHETIC-OWNER-SECRET-DO-NOT-RETAIN"
        demo["observations"][0]["observation"] = marker
        demo = final_records.seal(demo)
        approval = json.loads((directory / "approval.json").read_bytes())
        approval = final_records.seal(
            {**approval, "final_demo_digest": demo["record_digest"]},
            self_digest="approved_digest",
        )
        supplied = parse_owner_input(
            json.dumps({"approval": approval, "demo": demo}).encode(),
            approval["approved_digest"], issued_at=90, expires_at=120,
            max_bytes=262144,
        )
        invocation = _admitted_owner_invocation(
            state=seed, root=root, candidate=candidate, report={},
            frames=SimpleNamespace(
                sources={"profile": {"max_artifact_bytes": 262144},
                         "mandate": {"issued_at": 90, "expires_at": 120}},
                anchor={"deadline_at": 120},
            ),
            pre_merge_sources=SimpleNamespace(check=lambda: None),
            guard=lambda: None,
        )
        monkeypatch.setattr(admission, "_installed_sources", lambda _: None)
        monkeypatch.setattr("time.time", lambda: 115)
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$") as caught:
            admission._write_owner_originals(invocation, supplied)
        assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
        captured = capsys.readouterr()
        assert marker not in captured.out + captured.err + caplog.text
        assert marker not in str(caught.value)
        return
    seed, root = tmp_path / "seed", tmp_path / "repo"
    seed.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    policy = Path(procedure.__file__).resolve().parents[1]
    mandate: dict[str, Any] = {"policy_sha": "a" * 40, "approval": {}}
    commitment = "sha256:" + hashlib.sha256(json.dumps(
        mandate, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    mandate["approval"]["mandate_digest"] = commitment
    config = {
        "schema_version": "8.0", "approved_final_digest": None,
        "policy_root": str(policy), "approved_mandate_digest": commitment,
    }
    read_paths: list[str] = []
    imported: list[str] = []
    effect_imports: list[str] = []
    inventory: list[bool] = []

    def private_json(path: Path) -> dict[str, Any]:
        assert path.parent == seed
        return config if path.name == "epic-runtime.json" else mandate

    def private_bytes(path: Path, maximum: int) -> bytes:
        assert maximum == 4 * 1024 * 1024
        relative = str(path.relative_to(policy))
        read_paths.append(relative)
        changed = (
            case == "own_bytes_before_import"
            and relative == "tools/agent_owner_final_approve.py"
        ) or (
            case == "runtime_bytes_before_import"
            and relative == "tools/agent_epic_runtime_cli.py"
        )
        return b"modified source" if changed else b"pinned source"

    def source_blob(_root: Path, _sha: str, _relative: str) -> bytes:
        assert _root == root and _sha == mandate["policy_sha"]
        return b"pinned source"

    def verify_inventory(*args: Any, **kwargs: Any) -> None:
        inventory.append(True)
        raise procedure._Invalid("FINAL_APPROVAL_INVALID")

    def forbidden_guard(*args: Any, **kwargs: Any) -> None:
        pytest.fail("invalid inventory reached later admission")

    def runtime_import(name: str) -> Any:
        imported.append(name)
        assert name == "tools.agent_epic_runtime_cli"
        return SimpleNamespace(
            __file__=str(policy / "tools/agent_epic_runtime_cli.py"),
            verify_policy_config8_batch=verify_inventory,
            managed_launch_guard=forbidden_guard,
            bootstrap_authority=forbidden_guard, secure_directory=forbidden_guard,
        )

    original_import = builtins.__import__

    def imports(name: str, *args: Any, **kwargs: Any) -> Any:
        if name in {
            "tools.agent_epic_session_runtime", "tools.agent_owner_final_admission",
            "tools.agent_owner_final_input",
        }:
            effect_imports.append(name)
            raise AssertionError("effect import before accepted bootstrap")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(procedure, "_private_json", private_json)
    monkeypatch.setattr(procedure, "_private_bytes", private_bytes)
    monkeypatch.setattr(procedure, "_git_blob", source_blob)
    monkeypatch.setattr(importlib, "import_module", runtime_import)
    monkeypatch.setattr(builtins, "__import__", imports)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.setattr(sys, "argv", [
        "owner-procedure", "--state-dir", str(seed), "--repository-root", str(root),
        "--approved-digest", "a" * 64,
    ])
    assert procedure.main() == 2
    assert not effect_imports
    if case == "support_inventory_before_effect_import":
        assert imported == ["tools.agent_epic_runtime_cli"] and inventory == [True]
        assert read_paths == [
            "tools/__init__.py", "tools/agent_epic_runtime_cli.py",
            "tools/agent_owner_final_approve.py",
        ]
    else:
        assert not imported and not inventory
        assert read_paths[-1] == (
            "tools/agent_owner_final_approve.py" if case == "own_bytes_before_import"
            else "tools/agent_epic_runtime_cli.py"
        )
    output = capsys.readouterr()
    assert output.out == "" and output.err == "FINAL_APPROVAL_INVALID\n"


@pytest.mark.parametrize(
    "case",
    [
        "initial_whole_absence_only", "partial_initial_directory_rejected",
        "merging_missing_ledger_stop", "unknown_not_applied_no_repeat",
        "applied_request_collision", "residual_delivery_temporary_stop",
    ],
)
def test_owner_ledger_phase_conservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Real typed-history guards; admitted envelope mocks are not authority."""
    import json
    from dataclasses import asdict

    from tools import agent_epic_final_delivery as final
    from tools import agent_owner_final_admission as admission

    from tests.test_agent_final_preparation_records import original

    seed, root = tmp_path / "seed", tmp_path / "repo"
    seed.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    candidate = original("candidate")
    request = admission._typed_owner_request(candidate, "merge_roadmap_pr")
    descriptor = os.open(seed, os.O_RDONLY | getattr(os, "O_DIRECTORY"))
    source = _admitted_owner_invocation(
        state=seed, root=root, candidate=candidate, guard=lambda: None,
        frames=SimpleNamespace(
            _seed=descriptor, sources={"profile": {"max_artifact_bytes": 262144}},
        ),
        pre_merge_sources=SimpleNamespace(authority=lambda: None),
        _new_ledger_request=None, _gate_a_request=None,
    )
    directory = seed / "final-ledger"
    try:
        if case in {"initial_whole_absence_only", "merging_missing_ledger_stop"}:
            assert admission._owner_ledger(source) is None
            # Pending/request admission is deliberately outside this unit. The
            # actual adapter must still reject missing history without its fixed
            # initial-only source ticket; a restart cannot turn None into {}.
            monkeypatch.setattr(admission, "_owner_request_binding", lambda *_: None)
            ledger = admission._owner_ledger_writer(source, request)
            with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
                ledger.load()
            if case == "initial_whole_absence_only":
                source._new_ledger_request = request.operation_id
                assert ledger.load() == {}
            else:
                from tools import agent_final_preparation_records as records

                from tests.test_agent_final_owner_wait_records import anchor, state

                # Exercise the actual MERGING dispatch too. Only its already
                # admitted invocation/original-local boundary is mocked; the
                # real source reader still establishes absent typed history.
                final_state = records.seal({
                    **state(anchor(), phase="MERGING"),
                    "candidate_digest": candidate["candidate_digest"],
                    "pending_operation_id": admission._pending_owner_identity(request),
                })
                session = object()
                source.normal_session = source.session = cast(Any, session)
                scope = SimpleNamespace(_active_reader=lambda: SimpleNamespace())
                events: list[str] = []
                monkeypatch.setattr(admission, "_current_scope", lambda _: source)
                monkeypatch.setattr(
                    admission, "_preparation_record", lambda *_: final_state,
                )
                monkeypatch.setattr(admission, "_owner_original_local", lambda _: {})

                def unexpected_delivery(*_args: Any) -> None:
                    events.append("delivery")
                    raise AssertionError("missing MERGING history reached delivery")

                monkeypatch.setattr(admission, "_owner_deliver", unexpected_delivery)
                with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
                    admission._continue_current(scope)
                assert events == []
            assert not directory.exists()
            return
        if case == "unknown_not_applied_no_repeat":
            unknown = {request.operation_id: {
                "operation": request.operation, "request": asdict(request),
                "status": "UNKNOWN", "receipt": None,
            }}
            events = []

            class History:
                def load(self) -> dict[str, Any]:
                    return unknown

                def save(self, _value: Any) -> None:
                    events.append("save")

            class Transport:
                def reconcile(self, _request: Any) -> tuple[str, None]:
                    events.append("reconcile")
                    return "NOT_APPLIED", None

                def effect(self, _request: Any) -> dict[str, object]:
                    events.append("effect")
                    raise AssertionError("uncertain effect repeated")

            monkeypatch.setattr(admission, "_owner_request_binding", lambda *_: None)
            monkeypatch.setattr(final, "authorization_code", lambda *_: None)
            monkeypatch.setattr(admission, "_owner_ledger_writer", lambda *_: History())
            monkeypatch.setattr(admission, "_owner_transport", lambda *_: Transport())
            result = admission._owner_deliver(source, request)
            assert result.status == "ESCALATE"
            assert result.machine_code == "ESCALATE_UNKNOWN_OUTCOME"
            assert events == ["reconcile"]
            assert unknown[request.operation_id]["status"] == "UNKNOWN"
            return
        directory.mkdir(mode=0o700)
        if case == "partial_initial_directory_rejected":
            with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
                admission._owner_ledger(source)
            assert list(directory.iterdir()) == []
            return
        entry = {
            "operation": request.operation, "request": asdict(request),
            "status": "APPLIED", "receipt": {"merge_sha": "f" * 40},
        }
        if case == "applied_request_collision":
            entry["request"] = {**asdict(request), "pr": request.pr + 1}
        path = directory / "delivery-ledger.json"
        path.write_text(json.dumps({request.operation_id: entry}))
        path.chmod(0o600)
        if case == "residual_delivery_temporary_stop":
            residue = directory / ".delivery-ledger-prior.tmp"
            residue.write_bytes(b"unchanged")
            residue.chmod(0o600)
        before = {item.name: item.read_bytes() for item in directory.iterdir()}
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            admission._owner_ledger(source)
        assert {item.name: item.read_bytes() for item in directory.iterdir()} == before
    finally:
        os.close(descriptor)




@pytest.mark.parametrize("case", [
    "unpinned_procedure", "wrong_alias_bytes", "changed_runtime_adapter",
    "model_write_reach_unknown", "owner_namespace_inside_worktree",
])
def test_owner_pinned_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Actual installation predicates, with prior policy admission mocked only."""
    import json
    import sys

    from tools import agent_epic_loop as loop
    from tools import agent_epic_runtime_cli as guards
    from tools import agent_owner_final_admission as admission

    original_tools = Path(admission.__file__).resolve().parent
    root = tmp_path / "repo"
    root.mkdir(mode=0o700)
    seed = (root / "owner" if case == "owner_namespace_inside_worktree"
            else tmp_path / "owner")
    seed.mkdir(mode=0o700)
    policy = tmp_path / "policy"
    policy.mkdir(mode=0o700)
    tools = policy / "tools"
    tools.mkdir(mode=0o700)
    aliases = seed / "adapter-bin"
    aliases.mkdir(mode=0o700)
    runtime = b"# synthetic runtime identity; never executed\n"
    code = {
        "agent_epic_runtime_cli.py": runtime,
        "agent_owner_final_approve.py": b"# synthetic original procedure\n",
        **{name: (original_tools / name).read_bytes()
           for name in admission._MOUNT_SOURCE_SHA256},
    }
    if case == "model_write_reach_unknown":
        code["codex_adapter.py"] += b"\n# unrecognized model write implementation\n"
    for name, raw in code.items():
        target = tools / name
        target.write_bytes(raw)
        target.chmod(0o600)
    alias = aliases / "runtime.py"
    alias.write_bytes(runtime if case != "wrong_alias_bytes" else
                      runtime.replace(b"synthetic", b"SYNTHETIC"))
    alias.chmod(0o600)
    command = [sys.executable, str(alias)]
    adapter: dict[str, Any] = {
        "schema_version": "2.0", "timeout_seconds": 300,
        "output_limit": 10000, "live_command": [*command, "live"],
        "phase_commands": {phase: [*command, "phase", phase]
                           for phase in loop.PHASES[1:]},
        "reconcile_commands": {phase: [*command, "reconcile", phase]
                               for phase in loop.PHASES[1:]},
        "epic_command": [*command, "epic_resume", "--state-dir", str(seed),
                         "--repository-root", str(root.resolve())],
    }
    if case == "changed_runtime_adapter":
        adapter["epic_command"][2] = "epic_status"
    adapter_path = seed / "runtime-adapter.json"
    adapter_path.write_text(json.dumps(adapter), encoding="utf-8")
    adapter_path.chmod(0o600)
    # This is a unit admitted-envelope double, not an installed capability.
    # All file/FD/mount/alias/adapter and namespace predicates below are real.
    policy_checks: list[bool] = []

    def already_admitted(*args: object, **kwargs: object) -> None:
        policy_checks.append(True)

    monkeypatch.setattr(guards, "verify_policy_config8_batch", already_admitted)
    if case != "unpinned_procedure":
        monkeypatch.setattr(admission, "__file__", str(
            tools / "agent_owner_final_admission.py",
        ))
    before = {path: path.read_bytes() for path in (
        *[tools / name for name in code], alias, adapter_path,
    )}
    seed_fd = os.open(seed, os.O_RDONLY | getattr(os, "O_DIRECTORY"))
    try:
        invocation = _admitted_owner_invocation(
            state=seed, root=root, guard=lambda: None,
            frames=SimpleNamespace(_seed=seed_fd, sources={
                "config": {"policy_root": str(policy)},
                "mandate": {"policy_sha": "a" * 40},
            }),
        )
        if case == "owner_namespace_inside_worktree":
            with pytest.raises(guards.DeploymentError,
                               match="^UNSAFE_RUNTIME_CONFIG$"):
                admission._installed_sources(invocation)
        else:
            with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
                admission._installed_sources(invocation)
        assert policy_checks == ([] if case == "unpinned_procedure" else [True])
        assert before == {path: path.read_bytes() for path in before}
        assert os.fstat(seed_fd).st_ino == seed.stat().st_ino
    finally:
        os.close(seed_fd)


@pytest.mark.parametrize(
    "case",
    [
        "expired_original_deadline", "clock_rollback", "ci_attempts_exhausted",
    ],
)
def test_owner_time_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Real refusal methods with explicitly admitted local envelope boundaries."""
    from contextlib import nullcontext

    from tools import agent_epic_wait as wait
    from tools import agent_epic_wait_frame as frames
    from tools import agent_owner_final_admission as admission

    if case in {"expired_original_deadline", "clock_rollback"}:
        source = cast(frames.OwnerFrameSources, SimpleNamespace(
            _wait_original=lambda: {},
            anchor={"stage_started_at": 100, "deadline_at": 200},
            sources={
                "mandate": {"expires_at": 300,
                            "limits": {"max_duration_seconds": 500}},
                "config": {"started_at": 90},
            },
        ))
        monkeypatch.setattr(time, "time", lambda: (
            201 if case == "expired_original_deadline" else 99
        ))
        with pytest.raises(frames.FrameError, match="WAIT_INVOCATION_EXPIRED"):
            frames.OwnerFrameSources._guard(source)
        return
    if case == "ci_attempts_exhausted":
        session = object()
        admitted = SimpleNamespace(normal_session=session, session=session)
        monkeypatch.setattr(admission, "_current_scope", lambda _: admitted)
        value = {"ci": {"status": "READY", "attempts": 8}, "requests": {}}
        saved: list[Any] = []
        reader = cast(wait.Reader, SimpleNamespace(
            _owner_original=True, _owner_assessing=False, _owner_scope=object(),
            _locked=nullcontext, _load=lambda: value, _check=lambda _: None,
            limits={"max_ci_polls": 8}, _save=saved.append,
        ))
        with pytest.raises(wait.WaitError, match="WAIT_OUTCOME_UNKNOWN"):
            wait.Reader.owner_continue_once(reader)
        assert value == {"ci": {"status": "READY", "attempts": 8}, "requests": {}}
        assert saved == []
        return


def _owner_frame_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Path]:
    """Synthetic admitted envelope for refusal-method tests, never a factory."""
    from tools import agent_epic_wait as wait
    from tools import agent_epic_wait_frame as frames

    seed, root = tmp_path / "frame-seed", tmp_path / "frame-repo"
    seed.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    namespace = seed / "invocations"
    namespace.mkdir(mode=0o700)
    source = frames.OwnerFrameSources.__new__(frames.OwnerFrameSources)
    source.state, source.root = seed, root
    source._seed = os.open(seed, os.O_RDONLY | getattr(os, "O_DIRECTORY"))
    source._namespace = os.open(namespace, os.O_RDONLY | getattr(os, "O_DIRECTORY"))
    source._namespace_identity = source._identity(os.fstat(source._namespace))
    source._bytes = source._entries = 0
    source._records = {}
    source._writing_temp = None
    source.sources = {
        "mandate": {"repository": "OWNER/repo", "epic": 294,
                    "policy_sha": "a" * 40},
    }
    source.anchor = {"runtime_config_digest": "sha256:" + "b" * 64}
    source.frame = {
        "schema_version": "1.0", "repository": "OWNER/repo", "epic": 294,
        "policy_sha": "a" * 40,
        "runtime_config_digest": source.anchor["runtime_config_digest"],
        "command_digest": wait.digest(["DISCOVERY", "LOCAL"]),
        "invocation_id": "a" * 32, "parent_frame_digest": None,
        "started_at": 100, "deadline_at": 200,
    }
    source.parent = None
    monkeypatch.setattr(source, "_guard", lambda: None)
    return source, namespace


@pytest.mark.parametrize(
    "case",
    [
        "enumeration_4097", "single_frame_16385", "global_reread_64mib_plus_one",
        "enumeration_counter_not_reset", "duplicate_matching_parent",
        "missing_parent", "wrong_command_association",
        "unqualified_direct_root", "unsupported_deeper_parent",
        "readonly_publication", "frame_replacement",
    ],
)
def test_owner_frame_source_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Bounded frame refusals; source admission is an explicit unit boundary."""
    import json

    from tools import agent_epic_runtime_cli as guards
    from tools import agent_epic_wait as wait
    from tools import agent_epic_wait_frame as frames
    from tools.agent_epic_session_runtime import SessionRuntime
    from tools.agent_epic_wait_scope import Scope, command_identity

    if case in {"readonly_publication", "unqualified_direct_root"}:
        session: Any = SessionRuntime.__new__(SessionRuntime)
        scope = Scope.__new__(Scope)
        scope.read_only = case == "readonly_publication"
        scope._owner_original = False
        scope._context_stack = ["FINAL:FINAL_GATE"]
        scope._session_origin = (
            "QUOTA_WAKE" if case == "readonly_publication" else None
        )
        session.wait_scope = scope
        # The real writer must reject before initializing any frame/source graph.
        entered: list[str] = []
        monkeypatch.setattr(
            frames.OwnerFrameSources, "_initialize",
            lambda *_: entered.append("initialize"),
        )
        with pytest.raises(frames.FrameError, match="FINAL_APPROVAL_INVALID"):
            frames.OwnerFrameSources.for_publication(session, {})
        assert entered == []
        return
    source, namespace = _owner_frame_unit(tmp_path, monkeypatch)
    with source:
        if case in {"enumeration_4097", "enumeration_counter_not_reset"}:
            passes: list[range] = (
                [range(4097)] if case == "enumeration_4097"
                else [range(4095), range(4095, 4097)]
            )
            observations: list[int] = []
            reads: list[str] = []

            class Entries:
                def __init__(self) -> None:
                    self.current = iter(passes.pop(0))

                def __iter__(self) -> Entries:
                    return self

                def __next__(self) -> SimpleNamespace:
                    index = next(self.current)
                    observations.append(index)
                    return SimpleNamespace(name=f"{index:032x}.json")

                def __enter__(self) -> Entries:
                    return self

                def __exit__(self, *_args: Any) -> None:
                    return None

            def read(name: str) -> tuple[dict[str, Any], tuple[int, ...]]:
                reads.append(name)
                return {**source.frame, "invocation_id": name[:-5]}, (1,)

            monkeypatch.setattr(os, "scandir", lambda _: Entries())
            monkeypatch.setattr(source, "_read", read)
            if case == "enumeration_counter_not_reset":
                source._scan()
                assert source._entries == 4095
            with pytest.raises(frames.FrameError, match="WAIT_FRAME_INVALID"):
                source._scan()
            assert source._entries == len(observations) == 4097
            assert len(reads) == 4096
            return
        if case in {
            "duplicate_matching_parent", "missing_parent",
            "wrong_command_association", "unsupported_deeper_parent",
        }:
            parent = dict(source.frame)
            parent["invocation_id"] = "b" * 32
            if case == "unsupported_deeper_parent":
                parent["parent_frame_digest"] = "sha256:" + "c" * 64
            command = ["python", "runtime.py", "epic_resume"]
            source.frame = {
                **source.frame, "parent_frame_digest": wait.digest(parent),
                "command_digest": wait.digest(list(command_identity(command))),
            }
            source._records = {"b" * 32: (parent, (1,))}
            if case == "duplicate_matching_parent":
                source._records["c" * 32] = (dict(parent), (2,))
            elif case == "missing_parent":
                source._records = {}
            elif case == "wrong_command_association":
                source.frame["command_digest"] = "sha256:" + "d" * 64
            monkeypatch.setattr(
                guards, "_final_preseed_read", lambda *_: {"epic_command": command},
            )
            with pytest.raises(frames.FrameError, match="WAIT_FRAME_BINDING_CHANGED"):
                source._association()
            return
        name = source.frame["invocation_id"] + ".json"
        path = namespace / name
        encoded = json.dumps(source.frame, sort_keys=True).encode()
        path.write_bytes(b"x" * 16385 if case == "single_frame_16385" else encoded)
        path.chmod(0o600)
        source._namespace_identity = source._identity(os.fstat(source._namespace))
        if case == "single_frame_16385":
            with pytest.raises(frames.FrameError, match="WAIT_FRAME_INVALID"):
                source._read(name)
            assert source._bytes == 0
            return
        identity = source._identity(path.stat())
        if case == "global_reread_64mib_plus_one":
            assert source.MAX_TOTAL_BYTES == 64 * 1024 * 1024
            # A seeded earlier-byte prefix is explicit; this unit does not
            # claim to read the entire64MiB physically. Actual initial scan and
            # both later revalidation passes use this same source/counters.
            source._bytes = source.MAX_TOTAL_BYTES - 2 * len(encoded) - 1
            source._scan()
            assert source._entries == 1
            assert source._bytes == source.MAX_TOTAL_BYTES - len(encoded) - 1
        else:
            source._records = {
                source.frame["invocation_id"]: (dict(source.frame), identity),
            }
        monkeypatch.setattr(
            guards, "_final_preseed_seed", lambda *_: os.dup(source._seed),
        )
        monkeypatch.setattr(
            guards, "_final_preseed_directory", lambda *_: os.dup(source._namespace),
        )
        if case == "frame_replacement":
            replacement = namespace / "replacement"
            replacement.write_bytes(encoded)
            replacement.chmod(0o600)
            replacement.replace(path)
            with pytest.raises(frames.FrameError, match="WAIT_FRAME_BINDING_CHANGED"):
                source.revalidate()
            return
        assert case == "global_reread_64mib_plus_one"
        # One physical history covers the shared global exhaustion/no-reset
        # obligations. Actual phase/factory reuse remains composition proof.
        source.revalidate()
        assert source._bytes == source.MAX_TOTAL_BYTES - 1
        with pytest.raises(frames.FrameError, match="WAIT_FRAME_INVALID"):
            source.revalidate()
        assert source._bytes == source.MAX_TOTAL_BYTES


def _owner_wait_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Any, dict[str, Any], Path]:
    """Private real files plus an explicitly admitted invocation/lock envelope."""
    import json

    from tools import agent_epic_runtime_cli as guards
    from tools import agent_epic_wait as wait
    from tools import agent_owner_final_admission as admission

    frames, _namespace = _owner_frame_unit(tmp_path, monkeypatch)
    limits = {"max_phase_seconds": 1000, "max_read_attempts": 2,
              "max_ci_polls": 8, "max_read_commands": 512}
    frames.sources["config"] = {"approved_wait_digest": "sha256:" + "b" * 64}
    identity = {
        **frames.sources["mandate"], "wait_digest": "sha256:" + "b" * 64,
        "stage": "FINAL:FINAL_GATE",
    }
    base = frames.state / "read-waits"
    base.mkdir(mode=0o700)
    directory = base / wait.digest(identity).removeprefix("sha256:")
    directory.mkdir(mode=0o700)
    lock = directory / "epic-loop.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)
    value: dict[str, Any] = {
        "schema_version": "1.0", "identity": identity,
        "started_at": 100, "last_seen_at": 140, "deadline_at": 200,
        "requests": {}, "ci": {
            "identity_digest": "sha256:" + "c" * 64, "attempts": 1,
            "status": "READY", "next_check_at": None,
        },
    }
    frames.anchor.update(
        stage_started_at=100, deadline_at=200,
        wait_identity_digest=value["ci"]["identity_digest"],
    )
    path = directory / "wait.json"
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
    path.chmod(0o600)
    invocation = SimpleNamespace(
        frames=frames, directory=directory, guard=frames._wait_original,
    )
    monkeypatch.setattr(admission, "_current_scope", lambda _: invocation)
    monkeypatch.setattr(guards, "wait_profile_guard", lambda *_: {"limits": limits})
    monkeypatch.setattr(RunnerLock, "current_owned", lambda *_: object())
    monkeypatch.setattr(time, "time", lambda: 150)
    reader = wait.Reader.__new__(wait.Reader)
    reader.directory, reader.root, reader.state = directory, frames.root, frames.state
    reader.identity, reader.limits = identity, limits
    reader.current_deadline = 200
    reader._owner_scope = object()
    reader._owner_original = True
    reader._owner_wait_identity = frames._identity(path.stat())
    reader.now = lambda: 150
    reader.guard = frames._wait_original
    return frames, reader, value, path


@pytest.mark.parametrize(
    "case",
    [
        "prior_temp_sibling", "unknown_sibling", "missing_canonical_wait",
        "symlink_canonical_wait", "replacement_before_guard", "short_write",
        "fsync_failure", "replace_failure", "postreplace_uncertainty",
        "exact_reread_mismatch", "dispatch_uses_fresh_deadline",
        "fifo_read_nonblocking_refusal",
    ],
)
def test_owner_wait_persistence_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Real no-create namespace/writer mechanics, not installed authority."""
    import json
    import subprocess
    from contextlib import nullcontext

    from tools import agent_epic_wait as wait
    from tools import agent_epic_wait_frame as frame_module

    if case == "dispatch_uses_fresh_deadline":
        clock = [150.0]
        events: list[str] = []
        value: dict[str, Any] = {
            "last_seen_at": 140.0, "deadline_at": 200.0,
            "ci": {"status": "RESERVED"}, "requests": {},
        }
        observed: list[float] = []
        reader: Any = SimpleNamespace(
            _owner_assessing=True, _active_poll=True, _owner_continuing=False,
            _locked=nullcontext, _load=lambda: value,
            limits={"max_read_commands": 512}, now=lambda: clock[0],
            guard=lambda: None,
        )
        reader._check = lambda item: wait.Reader._check(reader, item)

        def save(_value: Any) -> None:
            events.append("save")
            clock[0] = 190.0

        def run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
            events.append("run")
            observed.append(options["timeout"])
            assert value["requests"]["1"]["status"] == "RESERVED"
            return subprocess.CompletedProcess(argv, 0, "{}", "")

        reader._save, reader.run = save, run
        assert wait.Reader._owner_command(reader, ("gh", "api", "rate_limit")) == "{}"
        assert observed == [10.0]
        assert events == ["save", "run", "save"]
        assert value["requests"]["1"]["status"] == "READY"
        return
    frames, reader, value, path = _owner_wait_unit(tmp_path, monkeypatch)
    with frames:
        old = path.read_bytes()
        if case in {
            "prior_temp_sibling", "unknown_sibling", "missing_canonical_wait",
            "symlink_canonical_wait",
        }:
            if case in {"prior_temp_sibling", "unknown_sibling"}:
                extra = path.parent / (
                    ".owner-wait-" + "d" * 32 + ".tmp"
                    if case == "prior_temp_sibling" else "unknown.json"
                )
                extra.write_bytes(b"unchanged")
                extra.chmod(0o600)
            elif case == "missing_canonical_wait":
                path.unlink()
            else:
                outside = tmp_path / "outside-wait.json"
                outside.write_bytes(old)
                outside.chmod(0o600)
                path.unlink()
                path.symlink_to(outside)
            before = sorted(item.name for item in path.parent.iterdir())
            with pytest.raises((frame_module.FrameError, wait.WaitError, OSError)):
                frames._wait_original()
            assert sorted(item.name for item in path.parent.iterdir()) == before
            if case in {"prior_temp_sibling", "unknown_sibling"}:
                assert extra.read_bytes() == b"unchanged"
            if case == "symlink_canonical_wait":
                assert path.is_symlink() and outside.read_bytes() == old
            return
        if case == "fifo_read_nonblocking_refusal":
            path.unlink()
            getattr(os, "mkfifo")(path, mode=0o600)
            with pytest.raises(wait.WaitError, match="WAIT_STORAGE_UNSAFE"):
                reader._owner_load()
            assert path.is_fifo()
            return
        changed = {**value, "last_seen_at": 150}
        if case == "replacement_before_guard":
            replacement = path.parent / "replacement"
            replacement.write_bytes(old)
            replacement.chmod(0o600)
            replacement.replace(path)
            with pytest.raises(wait.WaitError, match="WAIT_STATE_INVALID"):
                reader._owner_save(changed)
            assert path.read_bytes() == old
            assert sorted(item.name for item in path.parent.iterdir()) == [
                "epic-loop.lock", "wait.json",
            ]
            return
        raw_write = os.write
        writes: list[int] = []
        if case == "short_write":
            def short(descriptor: int, data: Any) -> int:
                written = raw_write(descriptor, data[:7])
                writes.append(written)
                return written

            monkeypatch.setattr(os, "write", short)
        elif case == "fsync_failure":
            def fail_fsync(_descriptor: int) -> None:
                raise OSError("synthetic fsync failure")

            monkeypatch.setattr(os, "fsync", fail_fsync)
        elif case == "replace_failure":
            def fail_replace(*_args: Any, **_kwargs: Any) -> None:
                raise OSError("synthetic replace failure")

            monkeypatch.setattr(os, "replace", fail_replace)
        elif case == "postreplace_uncertainty":
            def fail_confirm(_parent: int, _expected: bytes) -> None:
                raise wait.WaitError("WAIT_STATE_INVALID")

            monkeypatch.setattr(reader, "_owner_confirm", fail_confirm)
        elif case == "exact_reread_mismatch":
            confirm, raw_read = reader._owner_confirm, os.read

            def mismatched(parent: int, expected: bytes) -> None:
                def altered(descriptor: int, maximum: int) -> bytes:
                    block = raw_read(descriptor, maximum)
                    return (b"!" + block[1:]) if block else block

                with monkeypatch.context() as scoped:
                    scoped.setattr(os, "read", altered)
                    confirm(parent, expected)

            monkeypatch.setattr(reader, "_owner_confirm", mismatched)
        if case == "short_write":
            reader._owner_save(changed)
            assert len(writes) > 1 and all(0 < item <= 7 for item in writes)
        else:
            expected = OSError if case in {"fsync_failure", "replace_failure"} else (
                wait.WaitError
            )
            with pytest.raises(expected):
                reader._owner_save(changed)
        # Every write-phase fault preserves the exact old or observed replacement;
        # no rollback/default record or prior/foreign temporary is invented.
        actual = json.loads(path.read_bytes())
        if case in {"fsync_failure", "replace_failure"}:
            assert path.read_bytes() == old
        else:
            assert actual == changed
        assert frames._writing_temp is None
        assert sorted(item.name for item in path.parent.iterdir()) == [
            "epic-loop.lock", "wait.json",
        ]




@pytest.mark.parametrize("case", [
    "set_inheritable_failure", "constructor_failure", "fdopen_transfer_failure",
    "exit_then_fd_number_reused", "failed_adoption_then_fd_number_reused",
    "held_object_reentry",
])
def test_normal_retained_fd_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Real shared flock/FD lifecycle after a mocked source-qualified route."""
    import fcntl

    from tools import agent_epic_loop as loop
    from tools import agent_owner_final_admission as admission

    root, seed = tmp_path / "repo", tmp_path / "private"
    root.mkdir(mode=0o700)
    seed.mkdir(mode=0o700)
    control = seed / "session-control"
    control.mkdir(mode=0o700)
    path = control / "epic-loop.lock"
    parent = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    getattr(fcntl, "flock")(
        parent, getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"),
    )
    duplicate = os.dup(parent)
    child: RunnerLock | None = None
    replacements: set[int] = set()
    monkeypatch.setenv(loop.SUPERVISOR_LOCK_FD_ENV, str(duplicate))
    # Source admission has its own scenarios. This node isolates ownership of
    # the inherited duplicate and never claims positive installed admission.
    monkeypatch.setattr(admission, "_normal_retained_command", lambda *a, **k: True)

    def assert_closed(descriptor: int) -> None:
        with pytest.raises(OSError):
            os.fstat(descriptor)

    def parent_still_owned() -> None:
        assert os.fstat(parent).st_ino == path.stat().st_ino
        probe = os.open(path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                getattr(fcntl, "flock")(
                    probe, getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"),
                )
        finally:
            os.close(probe)

    def fail(*args: object, **kwargs: object) -> Any:
        raise OSError("synthetic descriptor transfer failure")

    try:
        if case in {"set_inheritable_failure", "constructor_failure"}:
            with monkeypatch.context() as patch:
                if case == "set_inheritable_failure":
                    patch.setattr(os, "set_inheritable", fail)
                else:
                    patch.setattr(RunnerLock, "__init__", fail)
                with pytest.raises(LoopError, match="^LOCK_FAILED$"):
                    RunnerLock.normal_retained_child(seed, root)
            assert_closed(duplicate)
            assert loop.SUPERVISOR_LOCK_FD_ENV not in os.environ
            parent_still_owned()
            return
        child = RunnerLock.normal_retained_child(seed, root)
        assert not os.get_inheritable(duplicate)
        assert loop.SUPERVISOR_LOCK_FD_ENV not in os.environ
        if case in {"fdopen_transfer_failure", "failed_adoption_then_fd_number_reused"}:
            with monkeypatch.context() as patch:
                patch.setattr(os, "fdopen", fail)
                with pytest.raises(LoopError, match="^LOCK_FAILED$"):
                    child.__enter__()
            assert child._inherited_fd is None and child._stream is None
            assert_closed(duplicate)
        else:
            child.__enter__()
            if case == "held_object_reentry":
                stream, owner_pid = child._stream, child._owner_pid
                with pytest.raises(LoopError, match="^LOCK_FAILED$"):
                    child.__enter__()
                assert child._stream is stream and child._owner_pid == owner_pid
                assert stream is not None and not stream.closed
                assert RunnerLock.current_owned(control) is child
                child._assert_existing_owned(control)
            child.__exit__(None, None, None)
            assert_closed(duplicate)
        parent_still_owned()
        if case in {
            "exit_then_fd_number_reused", "failed_adoption_then_fd_number_reused",
        }:
            replacement = os.open(
                tmp_path / "replacement", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600,
            )
            replacements.add(replacement)
            os.dup2(replacement, duplicate)
            replacements.add(duplicate)
            identity = os.fstat(duplicate).st_dev, os.fstat(duplicate).st_ino
            with pytest.raises(LoopError, match="^LOCK_FAILED$"):
                child.__enter__()
            child.__exit__(None, None, None)
            assert (os.fstat(duplicate).st_dev, os.fstat(duplicate).st_ino) == identity
            parent_still_owned()
        assert control not in RunnerLock._held
    finally:
        if child is not None:
            child.__exit__(None, None, None)
            if child._inherited_fd is not None:
                os.close(child._inherited_fd)
                child._inherited_fd = None
        for descriptor in replacements:
            os.close(descriptor)
        os.close(parent)




@pytest.mark.parametrize("case", [
    "valid_configured_child_parent_lock_preserved", "missing_control_no_create",
    "wrong_canonical_inode", "separate_unlocked_descriptor", "wrong_configured_argv",
    "wrong_policy_runtime_bytes", "standalone_marker_rejected",
    "missing_original_frame_no_create", "missing_wait_namespace_no_create",
    "adoption_verification_interrupt", "quota_wake_no_cleanup_arm_or_frame",
])
def test_normal_retained_source_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], case: str,
) -> None:
    """Real route predicates/ownership; outer source admission remains a double."""
    import fcntl
    import json
    import sys

    from tools import agent_epic_loop as loop
    from tools import agent_epic_runtime_cli as guards
    from tools import agent_epic_wait as wait
    from tools import agent_epic_wait_frame as frames
    from tools import agent_owner_final_admission as admission
    from tools.agent_epic_wait_scope import Scope

    monkeypatch.delenv(frames.FD_ENV, raising=False)
    monkeypatch.delenv(frames.NONCE_ENV, raising=False)
    monkeypatch.delenv(loop.SUPERVISOR_LOCK_FD_ENV, raising=False)
    if case == "missing_original_frame_no_create":
        source, namespace = _owner_frame_unit(tmp_path, monkeypatch)
        with source:
            source.anchor.update(
                invocation_id=source.frame["invocation_id"],
                frame_digest=wait.digest(source.frame),
            )
            os.close(source._namespace)
            source._namespace = -1
            with pytest.raises(frames.FrameError, match="^FINAL_APPROVAL_INVALID$"):
                source._open_graph()
            assert list(namespace.iterdir()) == []
        return
    root, seed = tmp_path / "repo", tmp_path / "private"
    root.mkdir(mode=0o700)
    seed.mkdir(mode=0o700)
    if case == "missing_wait_namespace_no_create":
        control = seed / "session-control"
        control.mkdir(mode=0o700)
        for directory in (seed, control):
            target = directory / "epic-loop.lock"
            target.write_bytes(b"")
            target.chmod(0o600)
        sources = {
            "config": {"schema_version": "8.0",
                       "approved_wait_digest": "sha256:" + "a" * 64},
            "mandate": {"repository": "OWNER/repo", "epic": 294,
                        "policy_sha": "b" * 40},
        }
        monkeypatch.setattr(guards, "final_preparation_original_sources",
                            lambda *_: sources)
        monkeypatch.setattr(guards, "wait_profile_guard", lambda *_: {
            "limits": {"max_ci_polls": 8},
        })
        with pytest.raises((FileNotFoundError, LoopError, guards.DeploymentError)):
            with admission._OriginalInvocation(seed, root):
                pytest.fail("missing original wait namespace admitted")
        assert not (seed / "read-waits").exists()
        assert set(seed.iterdir()) == {seed / "epic-loop.lock", control}
        assert (seed / "epic-loop.lock").read_bytes() == b""
        assert (control / "epic-loop.lock").read_bytes() == b""
        assert seed not in RunnerLock._held and control not in RunnerLock._held
        return
    if case == "quota_wake_no_cleanup_arm_or_frame":
        quota_config: dict[str, Any] = dict.fromkeys(
            guards.epic_config_keys({"schema_version": "8.0"}),
        )
        quota_config.update(schema_version="8.0", policy_root=str(
            Path(guards.__file__).resolve().parent.parent,
        ))
        for name, value in (
            ("epic-runtime", quota_config), ("mandate", {"policy_sha": "a" * 40}),
        ):
            target = seed / (name + ".json")
            target.write_text(json.dumps(value), encoding="utf-8")
            target.chmod(0o600)
        monkeypatch.setattr(guards, "bootstrap_authority", lambda *a, **k: None)
        monkeypatch.setattr(guards, "managed_launch_guard", lambda *a, **k: None)
        monkeypatch.setattr(guards, "verify_policy", lambda *a, **k: None)
        monkeypatch.setattr(admission, "_retained_sources",
                            lambda *a: {"retained": True})
        monkeypatch.setattr(guards, "build_quota_cleanup",
                            lambda *a: pytest.fail("cleanup constructed"))
        monkeypatch.setattr(guards, "build_session",
                            lambda *a, **k: pytest.fail("ordinary session constructed"))
        monkeypatch.setattr(Scope, "for_root",
                            lambda *a, **k: pytest.fail("new wait/frame constructed"))
        monkeypatch.setattr(sys, "path", list(sys.path))
        monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
        monkeypatch.setattr(sys, "argv", [
            "runtime", "quota_wake", "--state-dir", str(seed),
            "--repository-root", str(root), "--wake-arm-id", "a" * 64,
        ])
        before = {path: path.read_bytes() for path in seed.iterdir()}
        assert guards.main() == 0
        result = capsys.readouterr()
        assert not result.err
        assert json.loads(result.out) == {
            "status": "BLOCKED", "machine_code": "QUOTA_WAKE_STALE",
        }
        assert before == {path: path.read_bytes() for path in seed.iterdir()}
        return

    policy = tmp_path / "policy"
    policy.mkdir(mode=0o700)
    tools = policy / "tools"
    tools.mkdir(mode=0o700)
    aliases = seed / "adapter-bin"
    aliases.mkdir(mode=0o700)
    runtime = b"# synthetic alias source, not executed\n"
    for path in (tools / "agent_epic_runtime_cli.py", aliases / "runtime.py"):
        path.write_bytes(runtime)
        path.chmod(0o600)
    configured = [str(Path(sys.executable).absolute()), str(aliases / "runtime.py"),
                  "epic_resume", "--state-dir", str(seed),
                  "--repository-root", str(root.resolve())]
    config = {"schema_version": "8.0", "policy_root": str(policy)}
    for name, value in (
        ("epic-runtime", config), ("runtime-adapter", {"epic_command": configured}),
        ("launch-control", {"state": "RUNNING"}),
    ):
        path = seed / (name + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
    retained = {"sources": {"config": config, "mandate": {"policy_sha": "a" * 40}}}
    monkeypatch.setattr(admission, "_retained_sources", lambda *a: retained)
    monkeypatch.setattr(guards, "managed_launch_guard", lambda *a, **k: None)
    monkeypatch.setattr(guards, "bootstrap_authority", lambda *a, **k: None)
    monkeypatch.setattr(guards, "verify_policy_config8_batch", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", configured[1:])
    if case in {"wrong_configured_argv", "wrong_policy_runtime_bytes",
                "standalone_marker_rejected"}:
        command = tuple(configured)
        if case == "wrong_configured_argv":
            command = (*command[:2], "epic_status", *command[3:])
        elif case == "wrong_policy_runtime_bytes":
            (tools / "agent_epic_runtime_cli.py").write_bytes(runtime.upper())
        else:
            monkeypatch.setenv(frames.FD_ENV, "123")
        with pytest.raises(PhaseBlocked, match="^FINAL_APPROVAL_INVALID$"):
            admission._normal_retained_command(seed, root, command, child=True)
        return
    control = seed / "session-control"
    if case == "missing_control_no_create":
        with pytest.raises((FileNotFoundError, guards.DeploymentError)):
            RunnerLock.normal_retained_child(seed, root)
        assert not control.exists()
        return
    control.mkdir(mode=0o700)
    canonical = control / "epic-loop.lock"
    parent = os.open(canonical, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    getattr(fcntl, "flock")(
        parent, getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"),
    )
    duplicate = (os.open(canonical, os.O_RDWR) if case == "separate_unlocked_descriptor"
                 else os.dup(parent))
    held_path = canonical
    child: RunnerLock | None = None
    try:
        if case == "wrong_canonical_inode":
            held_path = control / "old-lock"
            canonical.rename(held_path)
            canonical.write_bytes(b"")
            canonical.chmod(0o600)
        monkeypatch.setenv(loop.SUPERVISOR_LOCK_FD_ENV, str(duplicate))
        child = RunnerLock.normal_retained_child(seed, root)
        if case == "adoption_verification_interrupt":
            failure = KeyboardInterrupt()

            def interrupt(*args: object) -> None:
                raise failure

            monkeypatch.setattr(child, "_assert_existing_owned", interrupt)
            with pytest.raises(KeyboardInterrupt) as captured:
                child.__enter__()
            assert captured.value is failure
        elif case in {"wrong_canonical_inode", "separate_unlocked_descriptor"}:
            with pytest.raises(LoopError, match="^LOCK_FAILED$"):
                child.__enter__()
        else:
            assert case == "valid_configured_child_parent_lock_preserved"
            with child:
                assert RunnerLock.current_owned(control) is child
            assert child._stream is None and child._inherited_fd is None
        assert control not in RunnerLock._held
        probe = os.open(held_path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                getattr(fcntl, "flock")(
                    probe, getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"),
                )
        finally:
            os.close(probe)
    finally:
        if child is not None:
            child.__exit__(None, None, None)
            if child._inherited_fd is not None:
                os.close(child._inherited_fd)
                child._inherited_fd = None
        else:
            try:
                os.close(duplicate)
            except OSError:
                pass
        os.close(parent)


@pytest.mark.parametrize(
    "case",
    [
        "actual_push_main_success", "wrong_head_branch", "pull_request_not_push",
        "wrong_ordered_parents", "third_merged_pr_base",
        "advanced_local_main_no_fallback", "pending_main_ci", "foreign_main_graph",
    ],
)
def test_owner_main_push_source_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Actual graph predicates with explicit original-source admission doubles."""
    import copy
    import subprocess
    from dataclasses import asdict

    from tools import agent_epic_wait as wait
    from tools import agent_final_preparation_ci as ci
    from tools import agent_final_preparation_records as records
    from tools import agent_final_preparation_sources as source_module
    from tools import agent_owner_final_admission as admission

    from tests.test_agent_final_owner_wait_records import anchor, state
    from tests.test_agent_final_preparation_ci import sources
    from tests.test_agent_final_preparation_records import original

    if case == "advanced_local_main_no_fallback":
        root, seed = tmp_path / "repo", tmp_path / "seed"
        root.mkdir(mode=0o700)
        seed.mkdir(mode=0o700)

        def git(*argv: str) -> str:
            result = subprocess.run(
                ["git", *argv], cwd=root, capture_output=True, text=True, check=False,
            )
            assert result.returncode == 0
            return result.stdout.strip()

        git("init", "--initial-branch=main")
        git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
            "commit", "--allow-empty", "-m", "original")
        old = git("rev-parse", "refs/heads/main")
        git("branch", "roadmap/294-test")

        observed_local: list[str] = []

        def measured_local() -> dict[str, Any]:
            observed_local.append(git("rev-parse", "refs/heads/main"))
            return records.assessment(records.seal({
                **original("assessment"),
                "snapshot_digest": wait.digest({
                    "local_main": git("rev-parse", "refs/heads/main"),
                }).removeprefix("sha256:"),
            }))

        actual_class = source_module.CurrentPreparation
        current: Any = actual_class.__new__(actual_class)
        current.profile = original("profile")
        current.baseline = measured_local()
        monkeypatch.setattr(current, "check", lambda: None)
        monkeypatch.setattr(current, "assessment", measured_local)
        candidate = records.seal({
            **original("candidate"), "main_sha": old, "roadmap_sha": old,
        }, self_digest="candidate_digest")
        current.identity = {"main_sha": old, "roadmap_sha": old}
        admitted_request = records.request(records.seal({
            **original("request"), "main_sha": old, "roadmap_sha": old,
            "preparation_digest": candidate["preparation_digest"],
        }, self_digest="operation_id"))
        # Exact upstream request/create admission is an explicit boundary;
        # the intended real guard below remains the measured local comparison.
        monkeypatch.setattr(current, "request", lambda: admitted_request)
        retained_anchor = {
            **anchor(), "wait_identity_digest": wait.digest(
                actual_class.ci_identity(current, candidate["pr"]),
            ),
        }
        final = records.seal({
            **state(retained_anchor, phase="MAIN_CI_PENDING"),
            "candidate_digest": candidate["candidate_digest"],
        })
        invocation = _admitted_owner_invocation(
            state=seed, root=root, session=object(), guard=lambda: None,
            frames=SimpleNamespace(anchor=retained_anchor),
        )
        monkeypatch.setattr(admission, "_current_scope", lambda _: invocation)
        monkeypatch.setattr(admission, "_installed_sources", lambda _: None)
        monkeypatch.setattr(admission, "_original_create_receipt", lambda *_: None)
        monkeypatch.setattr(admission, "_preparation_record", lambda _inv, name: (
            final if name == "final-state" else candidate
        ))
        monkeypatch.setattr(source_module, "CurrentPreparation", lambda _: current)
        monkeypatch.setattr(admission, "_owner_prepare_current", lambda _: current)
        git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
            "commit", "--allow-empty", "-m", "advanced")
        advanced = git("rev-parse", "refs/heads/main")
        assert advanced != old
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            admission._owner_original_local(object())
        assert observed_local == [old, advanced]
        assert git("rev-parse", "refs/heads/main") == advanced
        assert git("rev-parse", "refs/heads/roadmap/294-test") == old
        return
    if case in {"wrong_ordered_parents", "third_merged_pr_base"}:
        candidate = original("candidate")
        request = admission._typed_owner_request(candidate, "merge_roadmap_pr")
        merge_sha = "f" * 40
        history = {request.operation_id: {
            "operation": request.operation, "request": asdict(request),
            "status": "APPLIED", "receipt": {"merge_sha": merge_sha},
        }}
        pr: dict[str, Any] = {
            "number": candidate["pr"], "state": "closed", "merged": True,
            "merge_commit_sha": merge_sha,
            "head": {"ref": candidate["roadmap_ref"],
                     "sha": candidate["roadmap_sha"],
                     "repo": {"full_name": candidate["repository"]}},
            "base": {"ref": "main", "sha": candidate["main_sha"],
                     "repo": {"full_name": candidate["repository"]}},
        }
        commit: dict[str, Any] = {"sha": merge_sha, "parents": [
            {"sha": candidate["main_sha"]}, {"sha": candidate["roadmap_sha"]},
        ]}
        if case == "wrong_ordered_parents":
            commit["parents"] = list(reversed(commit["parents"]))
        else:
            pr["base"]["sha"] = "e" * 40
        repo = candidate["repository"]
        endpoints = {
            f"repos/{repo}/pulls/{candidate['pr']}": pr,
            f"repos/{repo}/git/commits/{merge_sha}": commit,
        }
        current = SimpleNamespace(
            session=SimpleNamespace(github=SimpleNamespace(branches=lambda _: {
                "main": merge_sha, candidate["roadmap_ref"]: candidate["roadmap_sha"],
            })), _json=lambda endpoint: copy.deepcopy(endpoints[endpoint]),
        )
        invocation = _admitted_owner_invocation(
            candidate=candidate, pre_merge_sources=current, guard=lambda: None,
        )
        monkeypatch.setattr(admission, "_owner_ledger", lambda _: history)
        monkeypatch.setattr(admission, "_owner_stage_guard", lambda _: None)
        with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
            admission._owner_merged_remote(invocation)
        return
    graph = sources()
    for run in graph["runs"].values():
        run.update(event="push", head_branch="main")
    if case == "wrong_head_branch":
        graph["runs"][90]["head_branch"] = "roadmap/294-test"
    elif case == "pull_request_not_push":
        graph["runs"][90]["event"] = "pull_request"
    elif case == "foreign_main_graph":
        graph["runs"][90]["repository"] = {"full_name": "foreign/repo"}
    elif case == "pending_main_ci":
        check = next(row for row in graph["check_runs"]["check_runs"]
                     if row["check_suite"]["id"] == 50)
        check.update(status="queued", conclusion=None)
        graph["runs"][90].update(status="in_progress", conclusion=None)
        job = next(row for row in graph["jobs"][90]["jobs"]
                   if row["id"] == check["id"])
        job.update(status="queued", conclusion=None)
        before = copy.deepcopy(graph)
        admission._main_ci_pending(
            graph["comparison"], graph["check_runs"]["check_runs"],
            graph["suites"], graph["runs"], graph["jobs"],
            graph["definitions"], graph["workflows"],
        )
        assert graph == before
        return
    before = copy.deepcopy(graph)
    if case == "actual_push_main_success":
        result = admission._main_ci_normalize(**graph)
        assert result.event == "push"
        assert all(row["head_branch"] == "main" for row in graph["runs"].values())
        assert len(result.checks) == 5
    else:
        with pytest.raises(ci.PrimaryCIError):
            admission._main_ci_normalize(**graph)
    assert graph == before


def _owner_effect_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> tuple[Any, list[str], dict[str, Any], int]:
    """Actual typed persistence/transport, synthetic admitted source acquisition."""
    import copy
    import json
    import subprocess
    from dataclasses import asdict

    from tools import agent_epic_final_delivery as final
    from tools import agent_final_preparation_records as records
    from tools import agent_owner_final_admission as admission
    from tools.agent_epic_delivery import Ledger
    from tools.agent_final_preparation_storage import Store

    from tests.test_agent_final_owner_wait_records import anchor, state
    from tests.test_agent_final_preparation_records import original

    seed, root = tmp_path / "seed", tmp_path / "repo"
    seed.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    candidate = original("candidate")
    merge = admission._typed_owner_request(candidate, "merge_roadmap_pr")
    merged = case in {"exact_main_ci_before_close", "close_before_update_and_done_last"}
    graph: dict[str, Any] = {
        "merged": merged, "closed": False, "comments": [], "writes": [],
        "ci": {"identity_digest": "sha256:" + "d" * 64,
               "attempts": 1, "status": "RESERVED"},
    }
    events: list[str] = []
    sha = "f" * 40
    if merged:
        Ledger(seed / "final-ledger", root).save({merge.operation_id: {
            "operation": merge.operation, "request": asdict(merge),
            "status": "APPLIED", "receipt": {"merge_sha": sha},
        }})
    with RunnerLock(seed):
        store = Store(seed, root, original("profile"), lambda: None)
        store.write("final-state", records.seal({
            **state(anchor(), phase="MAIN_CI_PENDING" if merged else "MERGING"),
            "candidate_digest": candidate["candidate_digest"],
            "pending_operation_id": admission._pending_owner_identity(merge),
        }))
        store.write("candidate-" + candidate["candidate_digest"], candidate)
    descriptor = os.open(seed, os.O_RDONLY | getattr(os, "O_DIRECTORY"))

    def endpoint(name: str) -> Any:
        if name.endswith(f"/pulls/{candidate['pr']}"):
            return {
                "number": candidate["pr"],
                "state": "closed" if graph["merged"] else "open",
                "merged": graph["merged"],
                "merge_commit_sha": sha if graph["merged"] else None,
                "head": {"ref": candidate["roadmap_ref"],
                         "sha": candidate["roadmap_sha"],
                         "repo": {"full_name": candidate["repository"]}},
                "base": {"ref": "main", "sha": candidate["main_sha"],
                         "repo": {"full_name": candidate["repository"]}},
            }
        if name.endswith("/git/commits/" + sha):
            return {"sha": sha, "parents": [
                {"sha": candidate["main_sha"]}, {"sha": candidate["roadmap_sha"]},
            ]}
        if name.endswith("/comments?per_page=100"):
            return [copy.deepcopy(graph["comments"])]
        raise AssertionError("unexpected synthetic source endpoint")

    def read(argv: Any) -> str:
        if tuple(argv[:3]) == ("gh", "issue", "view"):
            return json.dumps({"number": candidate["epic"],
                               "state": "CLOSED" if graph["closed"] else "OPEN"})
        return json.dumps(endpoint(argv[-1]))

    session = SimpleNamespace(_github_read=read, github=SimpleNamespace(
        branches=lambda _: {
            "main": sha if graph["merged"] else candidate["main_sha"],
            candidate["roadmap_ref"]: candidate["roadmap_sha"],
        },
    ))
    current = SimpleNamespace(
        profile=original("profile"), authority=lambda: None, session=session,
        _json=endpoint,
    )
    reader = SimpleNamespace(
        _load=lambda: {"ci": graph["ci"], "requests": {}}, current_deadline=200,
    )
    scope = SimpleNamespace(frame={"invocation_id": "a" * 32},
                            _active_reader=lambda: reader)
    invocation = _admitted_owner_invocation(
        state=seed, root=root, candidate=candidate, pre_merge_sources=current,
        session=session, normal_session=session, scope=scope, guard=lambda: None,
        frames=SimpleNamespace(_seed=descriptor, anchor=anchor(), sources={
            "profile": {"max_artifact_bytes": 262144},
        }),
        report={"record_digest": "e" * 64},
        approval={"approved_digest": "f" * 64}, _owner_graph={"admitted": True},
        _new_ledger_request=merge.operation_id if not merged else None,
        _gate_a_request=merge.operation_id if not merged else None,
        _verified_b=None, _observed_merge_sha=None,
    )

    def reserve(**_options: Any) -> None:
        events.append("reserve_ci")
        graph["ci"]["attempts"] += 1
        graph["ci"]["status"] = "RESERVED"
        invocation._verified_b = None

    def completed() -> None:
        assert type(invocation._verified_b) is admission._VerifiedEffectGate
        graph["ci"]["status"] = "READY"
        events.append("B_ready")

    reader.owner_next_phase, reader.owner_gate_completed = reserve, completed
    monkeypatch.setattr(admission, "_current_scope", lambda _: invocation)
    monkeypatch.setattr(admission, "_owner_original_local", lambda _: {})
    monkeypatch.setattr(admission, "_assess_current", lambda _: {"status": "PASS"})
    monkeypatch.setattr(admission, "_owner_full_premerge", lambda _: {"status": "PASS"})
    monkeypatch.setattr(admission, "_owner_full_local", lambda _: {})
    monkeypatch.setattr(
        admission, "_current_owner_approval", lambda _: invocation.approval,
    )
    monkeypatch.setattr(admission, "_owner_stage_guard", lambda _: None)
    monkeypatch.setattr(
        admission, "_owner_nonci_guard", lambda _: invocation._verified_b,
    )
    monkeypatch.setattr(final, "authorization_code", lambda *_: None)

    def main_ci(_invocation: Any, _request: Any) -> dict[str, Any]:
        events.append("main_ci")
        return {"status": "PASS", "merge_sha": sha}

    monkeypatch.setattr(admission, "_owner_main_ci", main_ci)
    write = Store.write

    def persisted(self: Store, name: str, value: Any) -> None:
        write(self, name, value)
        if name == "final-state":
            events.append("state:" + value["phase"])

    monkeypatch.setattr(Store, "write", persisted)

    def run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        history = admission._owner_ledger(invocation)
        assert history is not None
        ticket = invocation._verified_b
        assert type(ticket) is admission._VerifiedEffectGate
        assert history[ticket.request.operation_id]["status"] == "INTENT"
        assert graph["ci"]["status"] == "READY"
        graph["writes"].append(tuple(argv))
        if tuple(argv[:3]) == ("gh", "pr", "merge"):
            graph["merged"] = True
            events.append("write:merge")
            if case == "unknown_merge_reconcile_no_duplicate":
                raise subprocess.TimeoutExpired(argv, options["timeout"])
        elif tuple(argv[:3]) == ("gh", "issue", "close"):
            graph["closed"] = True
            events.append("write:close")
        else:
            assert tuple(argv[:2]) == ("gh", "api")
            assert argv[2].endswith(f"/issues/{candidate['epic']}/comments")
            assert argv[3:6] == ["--method", "POST", "-f"]
            assert argv[6].startswith("body=")
            graph["comments"].append({"body": argv[6][5:]})
            events.append("write:update")
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(subprocess, "run", run)
    return invocation, events, graph, descriptor


@pytest.mark.parametrize(
    "case",
    [
        "exact_merge_intent_applied", "exact_main_ci_before_close",
        "close_before_update_and_done_last", "unknown_merge_reconcile_no_duplicate",
    ],
)
def test_owner_effect_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Original typed Ledger/transport sequencing; fresh source joins admitted."""
    from tools import agent_owner_final_admission as admission

    invocation, events, graph, descriptor = _owner_effect_unit(
        tmp_path, monkeypatch, case,
    )
    candidate = invocation.candidate
    merge = admission._typed_owner_request(candidate, "merge_roadmap_pr")
    try:
        if case == "close_before_update_and_done_last":
            with RunnerLock(invocation.state):
                result = admission._continue_current(invocation.scope)
            assert result == {"status": "ROADMAP_DONE", "machine_code": "OK"}
            assert events.index("write:close") < events.index("write:update")
            assert events[-1] == "state:DONE"
            assert events.count("main_ci") == 4
            assert len(graph["writes"]) == 2
            history = admission._owner_ledger(invocation)
            assert history is not None and len(history) == 3
            assert all(row["status"] == "APPLIED" for row in history.values())
            return
        request = merge
        if case == "exact_main_ci_before_close":
            request = admission._typed_owner_request(candidate, "close_epic", "f" * 40)
            admission._owner_gate_a(invocation, request)
            with RunnerLock(invocation.state):
                admission._owner_publish(
                    invocation, "MAIN_CI_PENDING",
                    admission._pending_owner_identity(request),
                )
        result = admission._owner_deliver(invocation, request)
        if case == "unknown_merge_reconcile_no_duplicate":
            assert result.status == "ESCALATE"
            unknown = admission._owner_ledger(invocation)
            assert unknown is not None
            assert unknown[merge.operation_id]["status"] == "UNKNOWN"
            result = admission._owner_deliver(invocation, merge)
            assert result.status == "NO_OP"
            assert events.count("write:merge") == events.count("B_ready") == 1
        else:
            assert result.status == "APPLIED"
        history = admission._owner_ledger(invocation)
        assert history is not None
        assert history[request.operation_id]["status"] == "APPLIED"
        assert len(graph["writes"]) == 1
        if case == "exact_main_ci_before_close":
            assert events.count("main_ci") == 2
            assert events.index("main_ci") < events.index("B_ready")
            assert events.index("B_ready") < events.index("write:close")
            assert graph["writes"][0] == (
                "gh", "issue", "close", str(candidate["epic"]), "--repo",
                candidate["repository"], "--reason", "completed",
            )
        else:
            assert graph["writes"][0] == (
                "gh", "pr", "merge", str(candidate["pr"]), "--repo",
                candidate["repository"], "--merge", "--match-head-commit",
                candidate["roadmap_sha"],
            )
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("case", [
    "exact_cap_short_reads_complete", "size_cap_plus_one_stop",
    "multiple_links_stop", "public_mode_stop", "symlink_stop", "fifo_stop",
    "named_identity_replaced_during_read_stop",
    "missing_record_no_initialization",
    "cleanup_preserves_primary_and_attempts_all_fds",
])
def test_fixed_historical_wait_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Actual bounded primitive; source-owned invocation admission is a unit input."""
    import os
    from types import SimpleNamespace

    from tools import agent_final_preparation_sources as sources
    from tools.agent_epic_wait import digest

    root, state = tmp_path / "repo", tmp_path / "state"
    root.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    identity = {"synthetic": "bounded-read-unit"}
    base = state / "read-waits"
    base.mkdir(mode=0o700)
    directory = base / digest(identity).removeprefix("sha256:")
    directory.mkdir(mode=0o700)
    path = directory / "wait.json"
    path.write_text('{"x":1}')
    path.chmod(0o600)
    capture = cast(Any, object.__new__(sources._HistoricalCapture))
    capture.session = SimpleNamespace(state=state, root=root)
    capture.reader = SimpleNamespace(identity=identity, directory=directory)
    original_read, original_close = os.read, os.close
    calls: list[int] = []
    if case == "exact_cap_short_reads_complete":
        path.write_bytes(b'{"x":1}' + b" " * (1_000_000 - 7))

        def short_read(fd: int, count: int) -> bytes:
            calls.append(count)
            return original_read(fd, min(count, 17000))

        monkeypatch.setattr(os, "read", short_read)
        assert capture._existing_wait_record() == {"x": 1}
        assert len(calls) > 2 and calls[-1] == 1
        return
    if case == "size_cap_plus_one_stop":
        path.write_bytes(b" " * 1_000_001)
    elif case == "multiple_links_stop":
        os.link(path, directory / "second-link")
    elif case == "public_mode_stop":
        path.chmod(0o644)
    elif case == "symlink_stop":
        target = directory / "original"
        path.rename(target)
        path.symlink_to(target)
    elif case == "fifo_stop":
        path.unlink()
        getattr(os, "mkfifo")(path, 0o600)
    elif case == "missing_record_no_initialization":
        path.unlink()
    elif case == "named_identity_replaced_during_read_stop":
        changed = False

        def replace_after_read(fd: int, count: int) -> bytes:
            nonlocal changed
            value = original_read(fd, count)
            if not changed:
                changed = True
                path.unlink()
                path.write_text('{"x":1}')
                path.chmod(0o600)
            return value

        monkeypatch.setattr(os, "read", replace_after_read)
    elif case == "cleanup_preserves_primary_and_attempts_all_fds":
        primary = ValueError("synthetic primary")
        opened: list[int] = []
        original_open = os.open

        def track_open(*args: Any, **kwargs: Any) -> int:
            fd = original_open(*args, **kwargs)
            opened.append(fd)
            return fd

        monkeypatch.setattr(os, "open", track_open)

        def fail_read(fd: int, count: int) -> bytes:
            raise primary

        def interrupt_close(fd: int) -> None:
            calls.append(fd)
            original_close(fd)
            if len(calls) == 1:
                raise KeyboardInterrupt

        monkeypatch.setattr(os, "read", fail_read)
        monkeypatch.setattr(os, "close", interrupt_close)
        with pytest.raises(ValueError) as caught:
            capture._existing_wait_record()
        assert caught.value is primary
        assert opened and sorted(calls) == sorted(opened)
        assert len(calls) == len(set(calls))
        return
    with pytest.raises(PhaseBlocked):
        capture._existing_wait_record()
    if case == "missing_record_no_initialization":
        assert not path.exists()
    elif case == "size_cap_plus_one_stop":
        assert path.stat().st_size == 1_000_001


def _capture_unit_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Any, list[tuple[str, ...]], Path, Path]:
    """Real capture/wait primitives; installed source envelope is explicitly doubled."""
    import json

    from tools import agent_epic_wait as wait
    from tools import agent_final_preparation_sources as sources
    from tools.agent_epic_session_runtime import SessionRuntime

    root, state = tmp_path / "repo", tmp_path / "state"
    root.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    (state / "session-control").mkdir(mode=0o700)
    policy = root / "policy-unit"
    policy.write_bytes(b"original")
    clock = [50.0]
    reader = object.__new__(wait.Reader)
    reader.identity = {
        "repository": "OWNER/repo", "epic": 294, "policy_sha": "a" * 40,
        "wait_digest": "sha256:" + "b" * 64, "stage": "FINAL:FINAL_GATE",
    }
    reader.limits = {**wait.MAXIMA, "max_ci_polls": 8}
    reader.current_deadline = 100.0
    reader.now = lambda: clock[0]
    reader._owner_original = False
    scope = SimpleNamespace(frame={"source_unit": "frame"})
    scope._active_reader = lambda: reader
    scope.active_stage = "FINAL:FINAL_GATE"
    scope._contexts = {scope.active_stage}
    scope._readers = {scope.active_stage: reader}
    config = {"schema_version": "8.0", "policy_unit": "original"}
    observed: list[tuple[str, ...]] = []
    response = ["{}"]

    def current_read(argv: Any) -> str:
        observed.append(tuple(argv))
        # Admitted command boundary models its one READY persisted reservation.
        value = json.loads(path.read_text())
        value["requests"][str(len(value["requests"]) + 1)] = {
            "command_digest": wait.digest(tuple(argv)), "attempts": 1,
            "status": "READY", "category": None, "next_check_at": None,
        }
        value["last_seen_at"] = clock[0]
        path.write_text(json.dumps(value))
        return response[0]

    scope.config = config
    session: Any = object.__new__(SessionRuntime)
    session.root, session.state, session.config = root, state, config
    session.wait_scope, session._read_stage = scope, "FINAL:FINAL_GATE"
    # Execute unchanged SessionRuntime._github_read; only command I/O is admitted.
    monkeypatch.setattr(reader, "command", current_read)
    capture = cast(Any, object.__new__(sources._HistoricalCapture))
    capture.session, capture.scope, capture.reader = session, scope, reader
    capture.values, capture.allowed, capture.consumers = {}, set(), {}
    capture.domain = None
    capture._registering_consumer = None
    capture._registration_pulls = set()
    capture.pins = capture.segment = capture.previous = None
    capture.closed = capture.initialized = False
    capture.last_seen = capture.seed_identity = None
    class ConsumerUnit:
        pass

    consumer: Any = ConsumerUnit()
    consumer._historical_session = session
    capture.consumers[id(consumer)] = consumer
    capture.repository = "OWNER/repo"
    capture.shas, capture.refs = set(), set()

    def source_pins(actual: Any) -> Any:
        assert actual is session
        owner = RunnerLock.current_owned(state)
        supervisor = RunnerLock.current_owned(state / "session-control")

        def check() -> None:
            assert RunnerLock.current_owned(state) is owner
            assert RunnerLock.current_owned(state / "session-control") is supervisor
            if policy.read_bytes() != b"original":
                raise PhaseBlocked("FINAL_APPROVAL_INVALID")

        return SimpleNamespace(
            owner=owner, supervisor=supervisor, check=check,
            guard=reader.guard, deadline=reader.current_deadline,
        )

    monkeypatch.setattr(original_runtime, "_CurrentFinalSources", source_pins)
    monkeypatch.setitem(sources._CAPTURES, id(session), capture)
    reader.guard = lambda: None
    capture.identity = (
        scope, reader, reader.guard, source_digest(scope.frame), source_digest(config),
        state.resolve(), root.resolve(),
    )
    base = state / "read-waits"
    base.mkdir(mode=0o700)
    reader.directory = base / wait.digest(reader.identity).removeprefix("sha256:")
    reader.directory.mkdir(mode=0o700)
    path = reader.directory / "wait.json"
    path.write_text(json.dumps({
        "schema_version": "1.0", "identity": reader.identity,
        "started_at": 10, "deadline_at": 100, "last_seen_at": 20,
        "requests": {}, "ci": None,
    }))
    path.chmod(0o600)
    capture.unit_clock, capture.unit_response = clock, response
    return capture, consumer, observed, policy, path


@pytest.mark.parametrize("case", [
    "historical_repeat_one_capture_current_graph_fresh",
    "current_pr_same_url_fresh", "current_ref_same_url_fresh",
    "current_issue_same_argv_fresh", "foreign_session_refused",
    "stale_invocation_refused",
])
def test_fixed_historical_consumer_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """One source-doubled lifecycle; actual locks/cache and current transport counts."""

    capture, consumer, observed, _policy, _path = _capture_unit_source(
        tmp_path, monkeypatch,
    )
    session = capture.session
    key: tuple[str, ...] = (
        "gh", "issue", "view", "301", "--repo", "OWNER/repo", "--json",
        "number,title,state,url,parent,subIssues,blockedBy",
    )
    if case == "current_pr_same_url_fresh":
        # A valid overlap used by the current PR graph: pinned workflow metadata.
        key = ("gh", "api", "repos/OWNER/repo/actions/workflows/tests.yml")
    elif case == "current_ref_same_url_fresh":
        key = ("gh", "api", "repos/OWNER/repo/git/ref/heads/main")
        capture.unit_response[0] = '{"object":{"sha":"' + "a" * 40 + '"}}'
    capture.allowed.add(key)
    with RunnerLock(session.state / "session-control") as supervisor:
        capture.supervisor = supervisor
        if case == "stale_invocation_refused":
            # No active source-owned seed segment: a gap is not a rebind request.
            with pytest.raises(PhaseBlocked):
                capture.read(consumer, key)
            assert observed == []
            return
        if case == "foreign_session_refused":
            foreign = object.__new__(original_runtime._HistoricalStructuralSourceReader)
            foreign._historical_session = object()
            with pytest.raises(PhaseBlocked):
                capture.register(foreign)
            assert observed == []
            return
        if case == "historical_repeat_one_capture_current_graph_fresh":
            owners, pins = [], []
            expected = None

            def advancing_now() -> float:
                capture.unit_clock[0] += 0.1
                return float(capture.unit_clock[0])

            capture.reader.now = advancing_now
            for segment in ("preflight", "aggregate", "postaggregate", "prepare"):
                with RunnerLock(session.state) as owner:
                    with capture._segment(segment):
                        owners.append(owner)
                        pins.append(capture.pins)
                        capture.unit_clock[0] += 1.0
                        before_hit = _path.read_bytes()
                        was_hit = key in capture.values
                        value = capture.read(consumer, key)
                        if was_hit:
                            assert _path.read_bytes() == before_hit
                        assert capture.last_seen > capture._seen_wait["last_seen_at"]
                        expected = value if expected is None else expected
                        assert value == expected
                        # The fixed current family stays live.
                        session._github_read(key)
                assert capture.pins is None and capture.segment is None
            assert len({id(owner) for owner in owners}) == 4
            assert len({id(pin) for pin in pins}) == 4
            assert len(capture.values) == 1 and len(observed) == 5
            assert capture.previous == "prepare"
            return
        with RunnerLock(session.state):
            with capture._segment("preflight"):
                historical = capture.read(consumer, key)
                original_count = len(observed)
                if case == "current_ref_same_url_fresh":
                    capture.unit_response[0] = '{"object":{"sha":"' + "b" * 40 + '"}}'
                else:
                    capture.unit_response[0] = '{"current":2}'
                if case == "current_pr_same_url_fresh":
                    from tools import agent_final_learning_domain as domain
                    from tools import agent_owner_final_admission as admission

                    # Only lower local source admission is doubled. The fixed
                    # owner-CI factory and inherited original domain joins run.
                    checked: list[str] = []
                    monkeypatch.setattr(domain, "_CurrentFinalSources", lambda _: (
                        SimpleNamespace(check=lambda: checked.append("pins"))
                    ))
                    monkeypatch.setattr(domain, "_OwnedCompletedSources", lambda _: (
                        SimpleNamespace(check=lambda: checked.append("completed"))
                    ))
                    monkeypatch.setattr(
                        domain, "_final_domain_value", lambda _: ((301,), "domain"),
                    )

                    def admit_reader(
                        actual: Any, state: Path, root: Path, *, read: Any,
                    ) -> None:
                        assert state == session.state and root == session.root
                        actual.read = read

                    monkeypatch.setattr(SourceReader, "__init__", admit_reader)
                    monkeypatch.setattr(
                        SourceReader, "guard", lambda _: checked.append("guard"),
                    )
                    current = admission._owner_current_ci_reader(session)
                    assert type(current) is domain._CurrentFinalSourceReader
                    assert current._source_tasks() == (301,)
                    assert current.read is not None
                    assert current.read(key) != historical
                    assert capture.read(consumer, key) == historical
                    current.read(key)
                    assert {"pins", "completed", "guard"} <= set(checked)
                    assert id(current) not in capture.consumers
                    current.close()
                else:
                    assert session._github_read(key) != historical
                    assert capture.read(consumer, key) == historical
                    session._github_read(key)
                assert len(observed) == original_count + 2
                assert capture.values[key].decode() == historical
                assert source_digest(capture.scope.frame) == capture.identity[3]


@pytest.mark.parametrize("case", [
    "entry_45_plus_one_stop", "raw_character_cap_plus_one_stop",
    "utf8_byte_cap_plus_one_stop", "unknown_endpoint_stop",
])
def test_fixed_historical_capture_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """One bounded capture history, with no installed-source authority claim."""
    capture, consumer, observed, _policy, _path = _capture_unit_source(
        tmp_path, monkeypatch,
    )
    session = capture.session
    key = ("gh", "api", "repos/OWNER/repo/synthetic-unit")
    if case != "unknown_endpoint_stop":
        capture.allowed.add(key)
    if case == "raw_character_cap_plus_one_stop":
        capture.unit_response[0] = "a" * 1_000_001
    elif case == "utf8_byte_cap_plus_one_stop":
        capture.unit_response[0] = "я" * 500001
    with RunnerLock(session.state / "session-control") as supervisor:
        capture.supervisor = supervisor
        with RunnerLock(session.state):
            with capture._segment("preflight"):
                if case == "entry_45_plus_one_stop":
                    # One accumulating domain, then its single entry46 refusal.
                    keys = [("gh", "api", f"repos/OWNER/repo/unit/{n}")
                            for n in range(46)]
                    capture.allowed.update(keys)
                    for item in keys[:45]:
                        assert capture.read(consumer, item) == "{}"
                    assert len(capture.values) == len(observed) == 45
                    with pytest.raises(PhaseBlocked):
                        capture.read(consumer, keys[45])
                    assert len(capture.values) == len(observed) == 45
                else:
                    with pytest.raises(PhaseBlocked):
                        capture.read(consumer, key)
                    assert capture.values == {}
                    expected = 0 if case == "unknown_endpoint_stop" else 1
                    assert len(observed) == expected


@pytest.mark.parametrize("case", [
    "historical_local_record_changed", "policy_source_changed", "config_changed",
    "original_frame_changed", "deadline_expired", "unknown_read_reservation_stop",
])
def test_fixed_historical_capture_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Real cache/context/wait rejection; local assessment projection is admitted."""
    import json

    from tools import agent_final_preparation_sources as sources
    from tools.agent_epic_wait import digest

    capture, consumer, observed, policy, path = _capture_unit_source(
        tmp_path, monkeypatch,
    )
    session = capture.session
    key = ("gh", "api", "repos/OWNER/repo/synthetic-unit")
    capture.allowed.add(key)
    saved_config, saved_frame = dict(session.config), dict(capture.scope.frame)
    saved_wait = path.read_bytes()
    with RunnerLock(session.state / "session-control") as supervisor:
        capture.supervisor = supervisor
        with RunnerLock(session.state):
            with capture._segment("preflight"):
                assert capture.read(consumer, key) == "{}"
                if case == "historical_local_record_changed":
                    record = session.root / "historical-unit.json"
                    record.write_text('{"source":"original"}')
                    current = cast(Any, object.__new__(sources.CurrentPreparation))
                    current.check = lambda: None
                    current.baseline = {
                        "record_digest": digest(json.loads(record.read_text())),
                    }
                    current.assessment = lambda: {
                        "record_digest": digest(json.loads(record.read_text())),
                    }
                    assert current.stable_assessment() == current.baseline
                    record.write_text('{"source":"changed"}')
                    # Lookup itself is not a universal filesystem snapshot.
                    assert capture.read(consumer, key) == "{}"
                    with pytest.raises(PhaseBlocked):
                        current.stable_assessment()
                    assert len(observed) == 1
                    return
                if case == "policy_source_changed":
                    policy.write_bytes(b"changed")
                elif case == "config_changed":
                    session.config["policy_unit"] = "changed"
                elif case == "original_frame_changed":
                    capture.scope.frame["source_unit"] = "changed"
                elif case == "deadline_expired":
                    capture.unit_clock[0] = 101.0
                else:
                    value = json.loads(path.read_text())
                    value["requests"]["1"] = {
                        "command_digest": digest(key), "attempts": 1,
                        "status": "RESERVED", "category": None, "next_check_at": None,
                    }
                    path.write_text(json.dumps(value))
                with pytest.raises(PhaseBlocked):
                    capture.read(consumer, key)
                assert len(observed) == 1
                session.config.clear()
                session.config.update(saved_config)
                capture.scope.frame.clear()
                capture.scope.frame.update(saved_frame)
                policy.write_bytes(b"original")
                capture.unit_clock[0] = 50.0
                path.write_bytes(saved_wait)


def test_fixed_historical_observed_ci_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One observed CI history progresses, then rejects a count rollback."""
    import json

    capture, _consumer, observed, _policy, path = _capture_unit_source(
        tmp_path, monkeypatch,
    )
    session = capture.session
    with RunnerLock(session.state / "session-control") as supervisor:
        capture.supervisor = supervisor
        with RunnerLock(session.state):
            with capture._segment("preflight"):
                assert capture._post_hit()["ci"] is None
                value = json.loads(path.read_text())
                value["ci"] = {
                    "identity_digest": "sha256:" + "c" * 64,
                    "attempts": 1, "status": "RESERVED",
                    "next_check_at": None,
                }
                path.write_text(json.dumps(value))
                assert capture._post_hit()["ci"]["attempts"] == 1
                value["ci"]["status"] = "READY"
                path.write_text(json.dumps(value))
                assert capture._post_hit()["ci"]["status"] == "READY"
                value["ci"].update(status="RESERVED", attempts=2)
                path.write_text(json.dumps(value))
                assert capture._post_hit()["ci"]["attempts"] == 2
                value["ci"]["attempts"] = 1
                path.write_text(json.dumps(value))
                with pytest.raises(PhaseBlocked, match="FINAL_APPROVAL_INVALID"):
                    capture._post_hit()
                assert capture._seen_wait["ci"]["attempts"] == 2
                assert observed == []


def _registration_cycle_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Any, list[tuple[str, ...]], Path]:
    """Original local ledger/contract/remote proof; installation envelope doubled."""
    import json
    from dataclasses import asdict

    from tools import agent_coordinator_handover as handover
    from tools import agent_epic_delivery as delivery
    from tools import agent_epic_loop as loop
    from tools import agent_epic_runtime_cli as guards
    from tools import agent_final_learning_domain as domain

    from tests.test_agent_coordinator_handover import _value

    capture, _unused, observed, _policy, path = _capture_unit_source(
        tmp_path, monkeypatch,
    )
    capture.consumers.clear()
    session = capture.session
    consumer: Any = object.__new__(original_runtime._HistoricalStructuralSourceReader)
    consumer._historical_session = session
    consumer.seed, consumer.root = session.state, session.root
    consumer.config = session.config
    consumer.owner = {
        "repository": "OWNER/repo", "epic": 294, "policy_sha": "a" * 40,
        "roadmap_ref": "roadmap/294-autonomy",
        "mandate_digest": "sha256:" + "b" * 64,
        "limits": {"max_records": 100, "max_record_bytes": 100000},
    }
    consumer.mandate = {"task_grants": {"301": {"allowed_paths": ["target.py"]}}}
    consumer.read = consumer._historical_read
    consumer.guard = lambda: None
    monkeypatch.setattr(domain, "_final_domain_value", lambda actual: (
        (301,), "admitted-original-domain",
    ))
    monkeypatch.setattr(session, "_recipe", lambda task: {"demo_plan": {
        "baseline_ref": "main", "roadmap_ref": "roadmap/294-autonomy",
    }}, raising=False)
    monkeypatch.setattr(guards, "bootstrap_authority", lambda *a, **k: None)
    monkeypatch.setattr(guards, "learning_profile_guard", lambda *a, **k: None)
    value = _value("c" * 40)
    value.update({
        "schema_version": "2.0", "repository": "OWNER/repo", "epic": 294,
        "task": 301, "base_ref": "roadmap/294-autonomy",
        "head_ref": "codex/301-unit", "head_sha": "d" * 40,
        "trusted_policy": {"source": "pinned_policy_sha", "policy_sha": "a" * 40},
        "mandate_provenance": {
            "schema_version": "3.0", "digest": consumer.owner["mandate_digest"],
            "policy_sha": "a" * 40,
        },
    })
    approved = handover.handover_digest(value)
    value["approval"] = {"plan_digest": approved}
    checkpoint = {
        "schema_version": "3.0", "policy_sha": "a" * 40,
        "repository": "OWNER/repo", "epic": 294, "task": 301, "pr": 7,
        "base_ref": "roadmap/294-autonomy", "base_sha": "c" * 40,
        "head_ref": "codex/301-unit", "head_sha": "d" * 40,
        "phase": "NEXT_TASK", "completed_phases": list(loop.PHASES[1:]),
        "pending_phase": None, "merge_sha": "e" * 40,
        "status": "TASK_DONE", "rate_limit_pause": None,
    }
    receipts: dict[str, Any] = {
        "commit_task": {"head_sha": "d" * 40, "tree_hash": "f" * 40},
        "push_task": {"head_sha": "d" * 40},
        "create_task_pr": {"pr": 7, "head_sha": "d" * 40},
        "merge_task_pr": {"merge_sha": "e" * 40},
        "close_task": {"task": 301, "merge_sha": "e" * 40},
        "update_epic": {"comment_id": 9, "merge_sha": "e" * 40},
    }
    rows = {}
    for operation, receipt in receipts.items():
        request = delivery.Request(
            operation_id="op-" + operation, operation=operation,
            repository="OWNER/repo", epic=294, task=301, pr=7,
            base_ref="roadmap/294-autonomy", base_sha="c" * 40,
            head_ref="codex/301-unit", head_sha="d" * 40,
            merge_sha=(
                "e" * 40 if operation in delivery.POST_MERGE_OPERATIONS else None
            ),
            schema_version="3.0", policy_sha="a" * 40,
        )
        rows[request.operation_id] = {
            "operation": operation, "request": asdict(request),
            "status": "APPLIED", "receipt": receipt,
        }
    for name, content in {
        "delivery-ledger.json": rows, "epic-loop-checkpoint.json": checkpoint,
        "handover.json": value, "runtime-task.json": {
            "schema_version": "3.0", "approved_handover_digest": approved,
        },
    }.items():
        target = session.state / name
        target.write_text(json.dumps(content))
        target.chmod(0o600)
    capture.unit_response[0] = json.dumps({
        "number": 7, "merged": True, "merge_commit_sha": "e" * 40,
        "head": {"sha": "d" * 40, "ref": "codex/301-unit"},
        "base": {"ref": "roadmap/294-autonomy"},
    })
    return capture, consumer, observed, path


@pytest.mark.parametrize("case", [
    "registration_success", "unknown_consumer_rejected", "failure_poison_cleanup",
])
def test_historical_registration_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Old register reaches its real circular PR read; no local/remote proof stub."""
    import json
    from contextlib import nullcontext

    from tools import agent_final_preparation_sources as sources
    from tools import agent_learning as learning

    capture, consumer, observed, path = _registration_cycle_source(
        tmp_path, monkeypatch,
    )
    session = capture.session
    before = json.loads(path.read_text())
    with RunnerLock(session.state / "session-control") as supervisor:
        capture.supervisor = supervisor
        # Poisoned registration also invalidates the enclosing source segment.
        expected_exit = (
            pytest.raises(PhaseBlocked)
            if case != "registration_success" else nullcontext()
        )
        completed_checks = False
        with expected_exit:
            with RunnerLock(session.state), sources._driver_preflight_segment(session):
                if case == "unknown_consumer_rejected":
                    with pytest.raises(PhaseBlocked):
                        capture.register(SimpleNamespace(_historical_session=session))
                    assert observed == []
                elif case == "failure_poison_cleanup":
                    value = json.loads(capture.unit_response[0])
                    value["merged"] = False
                    capture.unit_response[0] = json.dumps(value)
                    with pytest.raises(learning.LearningError):
                        capture.register(consumer)
                    assert len(observed) == 1
                else:
                    capture.register(consumer)
                    facts = consumer.delivery(301)
                    assert facts["pr"] == 7 and facts["merge_sha"] == "e" * 40
                    domain, values = capture.domain, dict(capture.values)
                    capture.register(consumer)
                    assert capture.domain == domain and capture.values == values
                    assert observed == [("gh", "api", "repos/OWNER/repo/pulls/7")]
                    assert not capture.closed and capture._registering_consumer is None
                    assert capture._registration_pulls == set()
                    return
                assert capture.closed
                assert (
                    not capture.allowed and not capture.consumers and not capture.values
                )
                assert id(session) not in sources._CAPTURES
                after = json.loads(path.read_text())
                assert after["started_at"] == before["started_at"]
                assert after["deadline_at"] == before["deadline_at"]
                assert len(after["requests"]) == len(observed)
                assert all(
                    row["status"] == "READY" for row in after["requests"].values()
                )
                completed_checks = True
        assert completed_checks


def _owner_mainci_group_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> dict[str, Any]:
    """Actual main-CI span; installed/local admission and primary I/O doubled."""
    import copy
    import time as clock_module
    from contextlib import nullcontext
    from dataclasses import asdict
    from types import SimpleNamespace

    from tools import agent_coordinator_handover as handover
    from tools import agent_epic_final_delivery as final_delivery
    from tools import agent_final_preparation_owner as owner_reader
    from tools import agent_final_preparation_records as records
    from tools import agent_final_preparation_sources as source_module
    from tools import agent_learning_delivery as learning_delivery
    from tools import agent_owner_final_admission as admission
    from tools import agent_owner_final_records as graph_reader

    from tests.test_agent_final_owner_wait_records import anchor, state
    from tests.test_agent_final_preparation_ci import sources
    from tests.test_agent_final_preparation_records import original

    counts = {"main_ci_stable": 0, "all_stable": 0, "checks": 0}
    events: list[str] = []
    in_span = [False]
    changed = [False]
    assessment = original("assessment")
    candidate = records.seal({
        **original("candidate"), "preparation_digest": assessment["record_digest"],
    }, self_digest="candidate_digest")
    report = records.seal({
        **original("report"), "candidate_digest": candidate["candidate_digest"],
    })
    final = records.seal({
        **state(anchor(), phase="MAIN_CI_PENDING"),
        "candidate_digest": candidate["candidate_digest"],
    })
    merge_sha = "f" * 40
    merge = admission._typed_owner_request(candidate, "merge_roadmap_pr")
    request = admission._typed_owner_request(candidate, "close_epic", merge_sha)
    history = {merge.operation_id: {
        "operation": merge.operation, "request": asdict(merge), "status": "APPLIED",
        "receipt": {"merge_sha": merge_sha},
    }}
    saved_wait: dict[str, Any] = {
        "started_at": 100, "last_seen_at": 140, "deadline_at": 200,
        "ci": {"status": "RESERVED", "attempts": 4,
               "identity_digest": "sha256:" + "d" * 64}, "requests": {},
    }
    observed_now = [145.0]

    def reader_check(value: dict[str, Any]) -> float:
        # Model Reader._check's local mutation and observed-time return contract.
        from tools.agent_epic_wait import WaitError

        now = observed_now[0]
        if now < value["last_seen_at"]:
            raise WaitError("WAIT_CLOCK_ROLLBACK")
        if now >= value["deadline_at"]:
            raise WaitError("WAIT_DEADLINE_EXCEEDED")
        reader.guard()
        value["last_seen_at"] = now
        return now

    reader: Any = SimpleNamespace(
        guard=lambda: None, _load=lambda: copy.deepcopy(saved_wait),
        _check=reader_check, current_deadline=200,
    )
    active_reader = [reader]
    scope: Any = SimpleNamespace(
        frame={"invocation_id": "a" * 32}, config={"schema_version": "8.0"},
        mandate={}, active_stage="FINAL:FINAL_GATE",
        _active_reader=lambda: active_reader[0],
    )
    root, seed = tmp_path / "repo", tmp_path / "seed"
    root.mkdir(mode=0o700)
    seed.mkdir(mode=0o700)
    current: Any = source_module.CurrentPreparation.__new__(
        source_module.CurrentPreparation,
    )
    session: Any = SimpleNamespace(wait_scope=scope)
    current.session, current.baseline = session, assessment
    approval = {"approved_digest": "e" * 64, "approved_at": 130}
    frames: Any = SimpleNamespace(
        anchor=anchor(), sources={
            "profile": {"max_artifact_bytes": 262144},
            "mandate": {"issued_at": 0, "expires_at": 200},
        },
    )
    graph: Any = SimpleNamespace(
        demo={"started_at": 110, "completed_at": 120}, approval=approval,
        pin={}, receipt={},
    )
    invocation: Any = admission._OriginalInvocation.__new__(
        admission._OriginalInvocation,
    )
    invocation.state, invocation.root = seed, root
    invocation.scope, invocation.session = scope, session
    invocation.normal_session = session
    invocation.pre_merge_sources = current
    invocation.candidate, invocation.report = candidate, report
    invocation.approval, invocation.frames = approval, frames
    invocation._owner_graph = None
    invocation.guard = lambda: None
    rows = {
        "final-state": final,
        "candidate-" + candidate["candidate_digest"]: candidate,
        "report-" + report["record_digest"]: report,
        "assessment-" + assessment["record_digest"]: assessment,
    }

    def stable() -> dict[str, Any]:
        counts["all_stable"] += 1
        if in_span[0]:
            counts["main_ci_stable"] += 1
            ordinal = counts["main_ci_stable"]
            if case == "entry_proof_failure" and ordinal == 1:
                raise PhaseBlocked("FINAL_APPROVAL_INVALID")
            if case == "exit_proof_failure" and ordinal == 7:
                raise PhaseBlocked("FINAL_APPROVAL_INVALID")
        return copy.deepcopy(assessment)

    def check() -> None:
        counts["checks"] += 1

    current.stable_assessment, current.check = stable, check
    current.authority = lambda: ({}, "sha256:" + "a" * 64, None)
    monkeypatch.setattr(admission, "_current_scope", lambda _: invocation)
    monkeypatch.setattr(admission, "_installed_sources", lambda _: None)
    monkeypatch.setattr(admission, "_owner_ledger", lambda _: copy.deepcopy(history))
    monkeypatch.setattr(admission, "_preparation_record",
                        lambda _, name: copy.deepcopy(rows[name]))
    monkeypatch.setattr(admission, "_owner_namespace_identity", lambda _: (1, 2, 3))
    monkeypatch.setattr(admission, "_demo_prose_guard", lambda _: None)
    monkeypatch.setattr(graph_reader, "read_original_shapes",
                        lambda *args, **kwargs: copy.deepcopy(graph))
    monkeypatch.setattr(clock_module, "time", lambda: observed_now[0])
    monkeypatch.setattr(source_module, "_current_dependency_gate",
                        lambda _: nullcontext())

    def authorization(_request: Any, _authority: Any) -> str | None:
        if case == "expiry_stop_authority_refusal" and changed[0]:
            return "MANDATE_EXPIRED"
        return None

    monkeypatch.setattr(final_delivery, "authorization_code", authorization)

    def owner_originals(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Fixed five-original acquisition envelope only; callback composition real.
        kwargs["installed_guard"]()
        kwargs["installed_guard"]()
        assert kwargs["source_verifier"](
            candidate, graph.approval, graph.pin, graph.receipt,
        ) is True
        return cast(dict[str, Any], graph.approval)

    monkeypatch.setattr(owner_reader, "read_current_owner_approval", owner_originals)
    monkeypatch.setattr(admission, "_owner_original_local", lambda _: {
        "candidate": candidate, "approval": approval, "final": final,
    })
    monkeypatch.setattr(admission, "_owner_full_local", lambda _: {
        "candidate": candidate, "approval": approval, "final": final,
    })
    primary = sources()

    def rewrite(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("synthetic/repo", candidate["repository"]).replace(
                "a" * 40, merge_sha,
            )
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    primary = rewrite(primary)
    for primary_run in primary["runs"].values():
        primary_run.update(event="push", head_branch="main")
    pr = {
        "number": candidate["pr"], "state": "closed", "merged": True,
        "merge_commit_sha": merge_sha,
        "head": {"ref": candidate["roadmap_ref"],
                 "sha": candidate["roadmap_sha"],
                 "repo": {"full_name": candidate["repository"]}},
        "base": {"ref": "main", "sha": candidate["main_sha"],
                 "repo": {"full_name": candidate["repository"]}},
    }
    commit = {"sha": merge_sha, "parents": [
        {"sha": candidate["main_sha"]}, {"sha": candidate["roadmap_sha"]},
    ]}
    endpoints: dict[str, Any] = {
        f"repos/{candidate['repository']}/pulls/{candidate['pr']}": pr,
        f"repos/{candidate['repository']}/git/commits/{merge_sha}": commit,
    }
    for key, prefix in (("suites", "check-suites"), ("runs", "actions/runs")):
        for ident, value in primary[key].items():
            endpoints[f"repos/{candidate['repository']}/{prefix}/{ident}"] = value
    for ident, value in primary["jobs"].items():
        endpoints[
            f"repos/{candidate['repository']}/actions/runs/{ident}"
            "/attempts/2/jobs?per_page=100"
        ] = value
    for filename, value in primary["workflows"].items():
        endpoints[
            f"repos/{candidate['repository']}/actions/workflows/{filename}"
        ] = value

    def read(endpoint: str) -> Any:
        events.append(endpoint)
        if not changed[0]:
            changed[0] = True
            if case == "body_primary_preserved_exit_attempted":
                raise LookupError("primary")
            if case in {"binding_failure_still_full_exit", "candidate_record_drift"}:
                key = "candidate-" + candidate["candidate_digest"]
                rows[key] = records.seal({
                    **candidate, "preparation_digest": "f" * 64,
                }, self_digest="candidate_digest")
            elif case == "five_original_graph_drift":
                graph.demo["completed_at"] = 121
            elif case == "applied_merge_ledger_drift":
                history[merge.operation_id]["receipt"]["merge_sha"] = "e" * 40
            elif case == "reader_or_scope_swap":
                active_reader[0] = SimpleNamespace()
            elif case == "attempt_identity_rollback":
                # Durable140 stays fixed: only actual observed145 -> 144 rolls back.
                observed_now[0] = 144.0
            elif case == "same_object_reentry":
                # Same actual group object cannot re-enter or unregister its owner.
                group = admission._MAIN_CI_GROUPS[invocation]
                with pytest.raises(PhaseBlocked):
                    group.__enter__()
                assert admission._MAIN_CI_GROUPS[invocation] is group
            elif case == "registry_cleanup_after_failure":
                raise LookupError("cleanup")
        return copy.deepcopy(endpoints[endpoint])

    current._json = read
    session.github = SimpleNamespace(branches=lambda _: {
        "main": merge_sha, candidate["roadmap_ref"]: candidate["roadmap_sha"],
    })
    monkeypatch.setattr(admission, "_owner_current_ci_reader", lambda _: (
        SimpleNamespace(close=lambda: None)
    ))
    monkeypatch.setattr(learning_delivery.DeliverySources, "_event_checks",
                        lambda *_: copy.deepcopy(primary["check_runs"]))
    monkeypatch.setattr(handover, "_git", lambda _root, _show, spec: (
        primary["definitions"][spec.rsplit("/", 1)[1]].decode()
    ))
    actual_main = admission._owner_main_ci

    def span(*args: Any, **kwargs: Any) -> Any:
        previous = in_span[0]
        in_span[0] = True
        try:
            return actual_main(*args, **kwargs)
        finally:
            in_span[0] = previous

    monkeypatch.setattr(admission, "_owner_main_ci", span)

    def run() -> dict[str, Any]:
        return admission._owner_source_gate(invocation, request)

    return {"run": run, "counts": counts, "events": events,
            "invocation": invocation, "request": request, "wait": saved_wait,
            "primary": primary, "reader": reader, "scope": scope,
            "observed_now": observed_now}


@pytest.mark.parametrize("case", [
    "mainci_fullproof_count", "fresh_current_endpoints_retained",
    "entry_proof_failure", "exit_proof_failure",
    "body_primary_preserved_exit_attempted", "binding_failure_still_full_exit",
    "candidate_record_drift", "five_original_graph_drift",
    "applied_merge_ledger_drift", "reader_or_scope_swap",
    "attempt_identity_rollback", "expiry_stop_authority_refusal",
    "same_object_reentry", "registry_cleanup_after_failure",
    "outside_group_stageguard_full", "merge_legacy_paths_unchanged",
])
def test_owner_mainci_group_cadence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    import copy

    from tools import agent_owner_final_admission as admission

    unit = _owner_mainci_group_unit(tmp_path, monkeypatch, case)
    counts, invocation = unit["counts"], unit["invocation"]
    before = copy.deepcopy(unit["wait"])
    if case == "merge_legacy_paths_unchanged":
        # Unit merge dispatch only; original full-proof admission is doubled.
        merge = admission._typed_owner_request(
            invocation.candidate, "merge_roadmap_pr",
        )
        full_checks: list[Any] = []

        def full_check(scope: Any) -> dict[str, Any]:
            assert scope is invocation.scope
            assert invocation not in getattr(admission, "_MAIN_CI_GROUPS", {})
            full_checks.append(scope)
            invocation.pre_merge_sources.stable_assessment()
            return {"status": "PASS"}

        def unexpected_main_ci(*args: Any, **kwargs: Any) -> Any:
            pytest.fail("merge dispatch entered main-CI group")

        monkeypatch.setattr(admission, "_assess_current", full_check)
        monkeypatch.setattr(admission, "_owner_full_premerge", lambda inv: (
            full_check(inv.scope)
        ))
        monkeypatch.setattr(admission, "_owner_main_ci", unexpected_main_ci)
        result = admission._owner_source_gate(invocation, merge)
        assert result["status"] == "PASS"
        assert full_checks == [invocation.scope]
        assert counts["all_stable"] == 6  # Full branch plus real approval composition.
        assert counts["main_ci_stable"] == 0
        assert unit["events"] == [] and unit["wait"] == before
        assert invocation not in getattr(admission, "_MAIN_CI_GROUPS", {})
        return
    failures = {
        "entry_proof_failure", "exit_proof_failure",
        "binding_failure_still_full_exit", "candidate_record_drift",
        "five_original_graph_drift", "applied_merge_ledger_drift",
        "reader_or_scope_swap", "attempt_identity_rollback",
        "expiry_stop_authority_refusal",
    }
    if case in {
        "body_primary_preserved_exit_attempted", "registry_cleanup_after_failure",
    }:
        with pytest.raises(LookupError):
            unit["run"]()
        assert counts["main_ci_stable"] == 12
    elif case in failures:
        with pytest.raises(PhaseBlocked):
            unit["run"]()
        if case == "binding_failure_still_full_exit":
            assert counts["main_ci_stable"] == 12
        elif case == "attempt_identity_rollback":
            assert unit["observed_now"] == [144.0]
            assert unit["wait"] == before
            assert unit["wait"]["last_seen_at"] == 140
            assert unit["wait"]["ci"]["attempts"] == 4
            assert unit["wait"]["ci"]["identity_digest"] == "sha256:" + "d" * 64
            assert counts["main_ci_stable"] == 12
    else:
        result = unit["run"]()
        assert result["status"] == "PASS"
        assert counts["main_ci_stable"] == 12
        assert unit["wait"] == before
    assert invocation not in getattr(admission, "_MAIN_CI_GROUPS", {})
    if case == "fresh_current_endpoints_retained":
        # Two independent before/after remote joins, four live snapshots total.
        pr = f"repos/{invocation.candidate['repository']}/pulls/50"
        assert unit["events"].count(pr) == 4
        assert any("/attempts/2/jobs?" in value for value in unit["events"])
        assert any("/actions/workflows/" in value for value in unit["events"])
    elif case == "outside_group_stageguard_full":
        previous = counts["all_stable"]
        admission._owner_stage_guard(invocation)
        assert counts["all_stable"] - previous == 6


def _logical_gate_constructor_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Real CurrentPreparation constructor; original acquisition and I/O doubled."""
    import copy
    import json
    from contextlib import nullcontext
    from dataclasses import asdict

    from tools import agent_epic_runtime_cli as runtime_guards
    from tools import agent_final_preparation_ci as ci
    from tools import agent_final_preparation_records as records
    from tools import agent_final_preparation_report as reporting
    from tools import agent_final_preparation_sources as sources_module
    from tools import agent_owner_final_admission as admission
    from tools import agent_owner_final_records as graph_reader
    from tools.agent_epic_session_runtime import SessionRuntime
    from tools.agent_epic_wait_scope import Scope

    from tests.test_agent_final_owner_wait_records import anchor, state
    from tests.test_agent_final_preparation_ci import sources as primary_sources
    from tests.test_agent_final_preparation_records import original

    root, seed = tmp_path / "root", tmp_path / "seed"
    root.mkdir(mode=0o700)
    seed.mkdir(mode=0o700)
    (seed / "session-control").mkdir(mode=0o700)
    counters = {"packets": 0, "constructors": 0, "checks": 0}
    profile = original("profile")
    identity = {key: original("candidate")[key] for key in records.IDENTITY}
    identity.update(main_sha="b" * 40, roadmap_sha="c" * 40)
    config = {
        "schema_version": "8.0",
        "approved_final_digest": None,
        "approved_launch_digest": identity["launch_digest"],
    }
    review = {"identity": identity, "request": {}, "output": {"verdict": "PASS"}}
    packet = {"source_digest": "c" * 64, "calls": [], "uses": [], "measurements": []}
    projection = {
        key: "c" * 64
        for key in records.ASSESSMENT
        if key not in {"status", "machine_code", "learning_digest"}
    }
    assessment = records.assessment(
        records.seal(
            {
                "schema_version": "1.0",
                "status": "READY",
                "machine_code": "OK",
                **projection,
                "learning_digest": packet["source_digest"],
            }
        )
    )
    candidate = records.seal(
        {
            **original("candidate"),
            "preparation_digest": assessment["record_digest"],
        },
        self_digest="candidate_digest",
    )
    primary = primary_sources()

    def rewrite(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("synthetic/repo", profile["repository"]).replace(
                "a" * 40,
                "c" * 40,
            )
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    primary = rewrite(primary)
    primary["comparison"] = ci.Comparison(
        profile["repository"],
        "c" * 40,
        profile["policy_sha"],
    )
    ci_identity = {
        "repository": profile["repository"],
        "epic": profile["epic"],
        "roadmap_sha": "c" * 40,
        "main_sha": "b" * 40,
        "merge_sha": None,
        "pr": 50,
    }
    candidate = records.seal(
        {
            **candidate,
            "pr_ci_digest": source_digest(
                {
                    "identity": ci_identity,
                    "primary": asdict(ci.normalize(**primary)),
                }
            ),
        },
        self_digest="candidate_digest",
    )
    anchored = anchor()
    anchored["wait_identity_digest"] = "sha256:" + source_digest(ci_identity)
    final = records.seal(
        {
            **state(anchored, phase="WAITING_OWNER_FINAL_APPROVAL"),
            "candidate_digest": candidate["candidate_digest"],
        }
    )
    report = reporting.prepare_report(
        profile,
        candidate,
        assessment,
        child_numbers=(301, 303),
        learning_status="NONE_APPLIED",
        comparison_status="UNMEASURED",
    )
    create = records.request(
        records.seal(
            {
                "schema_version": "1.0",
                **{key: identity[key] for key in records.IDENTITY},
                "operation": "create_roadmap_pr",
                "repository": profile["repository"],
                "epic": profile["epic"],
                "roadmap_ref": profile["roadmap_ref"],
                "policy_sha": profile["policy_sha"],
                "main_sha": "b" * 40,
                "roadmap_sha": "c" * 40,
                "preparation_digest": assessment["record_digest"],
            },
            self_digest="operation_id",
        )
    )
    rows = {
        "final-state": final,
        "assessment-" + assessment["record_digest"]: assessment,
        "candidate-" + candidate["candidate_digest"]: candidate,
        "report-" + report["record_digest"]: report,
        "ledger": {
            create["operation_id"]: {
                "request": create,
                "status": "APPLIED",
                "receipt": {
                    "pr": 50,
                    "repository": profile["repository"],
                    "base_ref": "main",
                    "head_ref": profile["roadmap_ref"],
                    "main_sha": "b" * 40,
                    "roadmap_sha": "c" * 40,
                    "operation_id": create["operation_id"],
                },
            }
        },
    }
    saved_wait: dict[str, Any] = {
        "started_at": 100,
        "last_seen_at": 140,
        "deadline_at": 200,
        "ci": {
            "status": "RESERVED",
            "attempts": 2,
            "identity_digest": anchored["wait_identity_digest"],
        },
        "requests": {},
    }
    reader: Any = SimpleNamespace(
        guard=lambda: None,
        current_deadline=200,
        _load=lambda: copy.deepcopy(saved_wait),
        _check=lambda _: 140,
        _owner_original=True,
        _owner_assessing=True,
        _active_poll=True,
        _owner_continuing=False,
        now=lambda: 140,
    )
    scope: Any = Scope.__new__(Scope)
    scope.state, scope.root = seed, root
    scope.config, scope.mandate = config, {"issued_at": 0, "expires_at": 200}
    scope.frame, scope.control = {"invocation_id": "a" * 32}, {}
    scope.read_only = False
    scope._context_stack = ["FINAL:FINAL_GATE"]
    scope._active_reader = lambda: reader
    session: Any = SessionRuntime.__new__(SessionRuntime)
    session.state, session.root, session.config = seed, root, config
    session.wait_scope = scope
    session._read_stage = "FINAL:FINAL_GATE"
    session._fresh_authority = lambda *args, **kwargs: ({}, "", None)
    session.authority = lambda _: ({}, "", None)
    session.github = SimpleNamespace(
        branches=lambda _: {
            "main": "b" * 40,
            profile["roadmap_ref"]: "c" * 40,
        }
    )
    inv: Any = admission._OriginalInvocation(seed, root)
    inv.scope, inv.session = scope, session
    inv.normal_session = None
    inv.guard = lambda: None
    inv.frames = SimpleNamespace(
        anchor=anchored,
        frame=scope.frame,
        sources={"profile": profile, "mandate": scope.mandate, "config": config},
    )
    inv.original = {
        **copy.deepcopy(saved_wait),
        "ci": {
            **saved_wait["ci"],
            "status": "READY",
            "attempts": 1,
        },
    }

    def check() -> None:
        counters["checks"] += 1

    class Completed:
        def __init__(self, _session: Any) -> None:
            counters["constructors"] += 1
            self.profile, self.config = profile, config

        def _session_sources(self, _sources: Any) -> dict[str, int]:
            return {"iterations": 2}

        def check(self) -> None:
            check()

    def packets(_session: Any) -> dict[str, Any]:
        counters["packets"] += 1
        return copy.deepcopy(packet)

    def completed_review(_session: Any) -> dict[str, Any]:
        packets(_session)
        return copy.deepcopy(review)

    monkeypatch.setattr(
        original_runtime,
        "_CurrentFinalSources",
        lambda _: SimpleNamespace(stage="FINAL:FINAL_GATE", check=check),
    )
    monkeypatch.setattr(original_runtime, "_OwnedCompletedSources", Completed)
    monkeypatch.setattr(
        original_runtime,
        "read_current_final_source_digests_owned",
        lambda _: copy.deepcopy(projection),
    )
    monkeypatch.setattr(
        original_runtime, "read_completed_learning_review_owned", completed_review
    )
    monkeypatch.setattr(
        original_runtime,
        "_structural_children_current_owned",
        lambda _: ({"task": 301}, {"task": 303}),
    )
    monkeypatch.setattr(sources_module, "read_learning_packet_owned", packets)
    monkeypatch.setattr(
        sources_module, "_current_dependency_gate", lambda _: nullcontext()
    )
    monkeypatch.setattr(
        runtime_guards,
        "final_preparation_original_sources",
        lambda *_: {"config": config, "profile": profile},
    )
    monkeypatch.setattr(admission, "_current_scope", lambda _: inv)
    monkeypatch.setattr(admission, "_installed_sources", lambda _: None)
    monkeypatch.setattr(
        admission, "_preparation_record", lambda _, name: copy.deepcopy(rows[name])
    )
    monkeypatch.setattr(admission, "_owner_ledger", lambda _: None)
    monkeypatch.setattr(graph_reader, "read_original_shapes", lambda *a, **k: None)
    monkeypatch.setattr(time, "time", lambda: 140)
    endpoints: dict[str, Any] = {
        f"repos/{profile['repository']}/pulls/50": {
            "number": 50,
            "state": "open",
            "merged": False,
            "head": {
                "ref": profile["roadmap_ref"],
                "sha": "c" * 40,
                "repo": {"full_name": profile["repository"]},
            },
            "base": {
                "ref": "main",
                "sha": "b" * 40,
                "repo": {"full_name": profile["repository"]},
            },
        },
        (f"repos/{profile['repository']}/commits/{'c' * 40}/"
         "check-runs?per_page=100"): primary["check_runs"],
    }
    for family, prefix in (("suites", "check-suites"), ("runs", "actions/runs")):
        for ident, value in primary[family].items():
            endpoints[f"repos/{profile['repository']}/{prefix}/{ident}"] = value
    for ident, value in primary["jobs"].items():
        endpoints[
            f"repos/{profile['repository']}/actions/runs/{ident}"
            "/attempts/2/jobs?per_page=100"
        ] = value
    for filename, value in primary["workflows"].items():
        endpoints[f"repos/{profile['repository']}/actions/workflows/{filename}"] = value
    session._github_read = lambda argv: json.dumps(endpoints[argv[-1]])
    from tools import agent_coordinator_handover as handover

    monkeypatch.setattr(
        handover,
        "_git",
        lambda _root, _show, spec: primary["definitions"][
            spec.rsplit("/", 1)[1]
        ].decode(),
    )

    def run() -> dict[str, Any]:
        with RunnerLock(seed / "session-control"), RunnerLock(seed):
            return admission._assess_current(scope)

    return {
        "run": run,
        "counters": counters,
        "invocation": inv,
        "session": session,
        "scope": scope,
        "reader": reader,
        "rows": rows,
        "packet": packet,
        "primary": primary,
        "saved_wait": saved_wait,
    }


def test_owner_logical_gate_constructor_causal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = _logical_gate_constructor_unit(tmp_path, monkeypatch)
    result = unit["run"]()
    assert result["status"] == "PASS"
    assert unit["counters"]["constructors"] == 1
    assert unit["counters"]["packets"] == 5


def _logical_gate_protocol_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Real constructor and typed local records; native acquisition doubled."""
    import copy
    import json
    from contextlib import contextmanager

    from tools import agent_coordinator_handover as handover
    from tools import agent_epic_final_delivery as final_delivery
    from tools import agent_final_preparation_records as records
    from tools import agent_learning_delivery as learning_delivery
    from tools import agent_owner_final_admission as admission
    from tools import agent_owner_final_records as graph_reader
    from tools.agent_final_preparation_storage import Store

    from tests.test_agent_final_preparation_ci import sources as primary_sources
    from tests.test_agent_final_preparation_records import original
    from tests.test_agent_owner_final_sources import document

    original_shapes, original_ledger = (
        graph_reader.read_original_shapes,
        admission._owner_ledger,
    )
    unit = _logical_gate_constructor_unit(tmp_path, monkeypatch)
    inv, session, scope, reader = (
        unit[key]
        for key in (
            "invocation",
            "session",
            "scope",
            "reader",
        )
    )
    candidate = unit["rows"][
        "candidate-" + unit["rows"]["final-state"]["candidate_digest"]
    ]
    profile = inv.frames.sources["profile"]
    now = [140.0]
    effects = {"merged": False, "closed": False}
    events: list[str] = []
    stores: list[Store] = []
    with RunnerLock(inv.state / "session-control"), RunnerLock(inv.state):
        store = Store(inv.state, inv.root, profile, inv.guard)
        for key, value in unit["rows"].items():
            store.write(key, value)
        stores.append(store)

    def read_record(_inv: Any, name: str) -> dict[str, Any]:
        value = store.read(name)
        assert value is not None
        return value

    monkeypatch.setattr(admission, "_preparation_record", read_record)
    monkeypatch.setattr(admission, "_owner_ledger", original_ledger)
    monkeypatch.setattr(graph_reader, "read_original_shapes", original_shapes)
    monkeypatch.setattr(final_delivery, "authorization_code", lambda *_: None)
    monkeypatch.setattr(time, "time", lambda: now[0])

    def check_wait(value: dict[str, Any]) -> float:
        if now[0] < value["last_seen_at"] or now[0] >= value["deadline_at"]:
            raise PhaseBlocked("FINAL_APPROVAL_INVALID")
        value["last_seen_at"] = now[0]
        return now[0]

    reader._check = check_wait
    reader.now = lambda: now[0]

    def advance(**_options: Any) -> None:
        value = unit["saved_wait"]["ci"]
        assert value["status"] in {"READY", "RESERVED"} and value["attempts"] < 8
        value.update(status="RESERVED", attempts=value["attempts"] + 1)
        inv._verified_b = None
        inv._observed_merge_sha = None
        events.append("reserve")

    def complete() -> None:
        assert type(inv._verified_b) is admission._VerifiedEffectGate
        assert inv._verified_b.attempt == unit["saved_wait"]["ci"]["attempts"]
        unit["saved_wait"]["ci"]["status"] = "READY"
        unit["saved_wait"]["last_seen_at"] = now[0]
        events.append("READY")

    reader.owner_next_phase, reader.owner_gate_completed = advance, complete
    graph_path = inv.state / "final-owner"

    def owner_graph() -> None:
        graph_path.mkdir(mode=0o700)
        shared = set(candidate) - {"schema_version"}

        def bound(value: dict[str, Any]) -> dict[str, Any]:
            return {**value, **{key: candidate[key] for key in set(value) & shared}}

        demo = records.seal(bound(document("demo")))
        approval = records.seal(
            {
                **bound(original("approval")),
                "approved_at": 110,
                "final_demo_digest": demo["record_digest"],
            },
            self_digest="approved_digest",
        )
        command = records.seal(
            {
                **bound(document("command")),
                "approved_at": 110,
                "approval_digest": approval["approved_digest"],
                "final_demo_digest": demo["record_digest"],
            }
        )
        receipt = records.seal(
            {
                **bound(original("owner_receipt")),
                "approved_at": 110,
                "approval_digest": approval["approved_digest"],
                "owner_command_digest": command["record_digest"],
            }
        )
        pin = records.seal(
            {
                **bound(original("owner_pin")),
                "approved_at": 110,
                "approval_digest": approval["approved_digest"],
                "installation_receipt_digest": receipt["record_digest"],
            }
        )
        for name, value in zip(
            (
                "approval",
                "approval-pin",
                "install-receipt",
                "command-source",
                "final-demo-source",
            ),
            (approval, pin, receipt, command, demo),
            strict=True,
        ):
            path = graph_path / (name + ".json")
            path.write_text(json.dumps(value))
            path.chmod(0o600)

    merge_sha = "f" * 40
    merge = admission._typed_owner_request(candidate, "merge_roadmap_pr")
    close = admission._typed_owner_request(candidate, "close_epic", merge_sha)
    update = admission._typed_owner_request(candidate, "update_epic", merge_sha)
    base_read = session._github_read
    main_graph = primary_sources()

    def rewrite(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("synthetic/repo", profile["repository"]).replace(
                "a" * 40,
                merge_sha,
            )
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    main_graph = rewrite(main_graph)
    for run in main_graph["runs"].values():
        run.update(event="push", head_branch="main")
    main_endpoints: dict[str, Any] = {}
    for family, prefix in (("suites", "check-suites"), ("runs", "actions/runs")):
        for ident, value in main_graph[family].items():
            main_endpoints[f"repos/{profile['repository']}/{prefix}/{ident}"] = value
    for ident, value in main_graph["jobs"].items():
        main_endpoints[
            f"repos/{profile['repository']}/actions/runs/{ident}"
            "/attempts/2/jobs?per_page=100"
        ] = value
    for filename, value in main_graph["workflows"].items():
        main_endpoints[
            f"repos/{profile['repository']}/actions/workflows/{filename}"
        ] = value

    def read(argv: Any) -> str:
        endpoint = argv[-1]
        events.append(endpoint)
        if not effects["merged"]:
            return cast(str, base_read(argv))
        if endpoint.endswith("/pulls/50"):
            return json.dumps(
                {
                    "number": 50,
                    "state": "closed",
                    "merged": True,
                    "merge_commit_sha": merge_sha,
                    "head": {
                        "ref": profile["roadmap_ref"],
                        "sha": "c" * 40,
                        "repo": {"full_name": profile["repository"]},
                    },
                    "base": {
                        "ref": "main",
                        "sha": "b" * 40,
                        "repo": {"full_name": profile["repository"]},
                    },
                }
            )
        if endpoint.endswith("/git/commits/" + merge_sha):
            return json.dumps(
                {
                    "sha": merge_sha,
                    "parents": [
                        {"sha": "b" * 40},
                        {"sha": "c" * 40},
                    ],
                }
            )
        return json.dumps(main_endpoints[endpoint])

    session._github_read = read
    session.github.branches = lambda _: {
        "main": merge_sha if effects["merged"] else "b" * 40,
        profile["roadmap_ref"]: "c" * 40,
    }
    monkeypatch.setattr(
        admission,
        "_owner_current_ci_reader",
        lambda _: SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(
        learning_delivery.DeliverySources,
        "_event_checks",
        lambda *_: copy.deepcopy(main_graph["check_runs"]),
    )
    monkeypatch.setattr(
        handover,
        "_git",
        lambda _root, _show, spec: main_graph["definitions"][
            spec.rsplit("/", 1)[1]
        ].decode(),
    )

    @contextmanager
    def owned() -> Any:
        with RunnerLock(inv.state / "session-control"), RunnerLock(inv.state):
            fd = os.open(inv.state, os.O_RDONLY | getattr(os, "O_DIRECTORY"))
            inv.frames._seed = fd
            try:
                yield
            finally:
                os.close(fd)

    def merge_a() -> None:
        inv.normal_session = session
        reader._owner_continuing = True
        inv.original["ci"]["attempts"] = 2
        unit["saved_wait"]["ci"]["attempts"] = 3
        checked = admission._assess_current(scope)
        assert checked["status"] == "PASS"
        inv._gate_a_request = merge.operation_id
        inv._new_ledger_request = merge.operation_id

    def intent(request: Any) -> Any:
        phase = (
            "MERGING" if request.operation == "merge_roadmap_pr" else "MAIN_CI_PENDING"
        )
        admission._owner_publish(inv, phase, admission._pending_owner_identity(request))
        ledger = admission._owner_ledger_writer(inv, request)
        history = ledger.load()
        history[request.operation_id] = final_delivery._entry(request, "INTENT", None)
        ledger.save(history)
        return ledger

    def b(request: Any) -> None:
        advance()
        admission._owner_effect_gate(inv, request)
        assert type(inv._verified_b) is admission._VerifiedEffectGate
        complete()

    def applied(request: Any, ledger: Any) -> None:
        history = ledger.load()
        receipt = (
            {"merge_sha": merge_sha}
            if request.operation == "merge_roadmap_pr"
            else ({"epic": candidate["epic"], "merge_sha": merge_sha})
        )
        history[request.operation_id] = final_delivery._entry(
            request, "APPLIED", receipt
        )
        ledger.save(history)
        if request.operation == "merge_roadmap_pr":
            effects["merged"] = True
            admission._owner_publish(
                inv, "MAIN_CI_PENDING", admission._pending_owner_identity(merge)
            )
        elif request.operation == "close_epic":
            effects["closed"] = True

    def close_a() -> Any:
        merge_a()
        first = intent(merge)
        b(merge)
        applied(merge, first)
        advance()
        admission._owner_gate_a(inv, close)
        return close

    def update_a() -> Any:
        close_a()
        second = intent(close)
        b(close)
        applied(close, second)
        advance()
        admission._owner_gate_a(inv, update)
        return update

    unit.update(
        owned=owned,
        owner_graph=owner_graph,
        merge_a=merge_a,
        intent=intent,
        b=b,
        applied=applied,
        close_a=close_a,
        update_a=update_a,
        candidate=candidate,
        merge=merge,
        close=close,
        update=update,
        store=store,
        now=now,
        effects=effects,
        events=events,
        main_graph=main_graph,
        graph_path=graph_path,
        advance=advance,
    )
    return unit


@pytest.mark.parametrize(
    "case",
    [
        "mergeA_fresh_wait",
        "mergeB_new_intent",
        "closeA_previous_merge_pending",
        "closeB_new_intent",
        "updateA_previous_close_pending",
        "updateB_new_intent",
        "human_complete_graph_full_default",
        "legacy_default_full",
        "CI_pending_disables_view",
        "restart_MERGING_full",
        "restart_MAIN_CI_full",
        "old_INTENT_readonly",
        "old_UNKNOWN_readonly",
        "wrong_session_reader_refusal",
        "pending_default_B_without_optimized_marker_full",
        "fresh_optimized_B_missing_marker_stop",
        "fresh_optimized_B_corrupt_marker_stop",
        "request_original_record_equality",
        "create_receipt_active_two_callers",
        "currentCI_sameURL_remains_current",
        "no_lessons_status",
        "workflow_applied_real_evaluation_join",
        "forged_evaluation_refused",
        "planning_applied_unmeasured",
        "returned_fact_copy_mutation_no_authority",
        "A_publish_exact_transition",
        "A_INTENT_exact_added_entry",
        "wrong_prior_pending_refused",
        "partial_publish_poison_no_adopt",
        "partial_INTENT_poison_no_replay",
        "marker_consumed_once",
        "B_before_verified_ticket_and_dispatch",
        "durable_vs_observed_clock_floor_across_gates",
        "counter_attempt_identity_nonreset",
        "body_primary_full_exit_attempted",
        "binding_failure_full_exit_attempted",
        "constructor_failure_no_output",
        "exit_proof_failure_no_A_or_B",
        "sameobject_reentry_preserves_outer",
        "nested_mainCI_default12_outsideview",
        "nested_mainCI_exact_active_light_hooks",
        "original_UNKNOWN_persistence_attempt_unchanged",
        "first_approval_prior_None_callbacks_staged",
        "observed_shape_not_admitted_approval",
        "active_wrong_nested_view_reject_not_default",
        "B_rawjoin_no_factory_reentry",
    ],
)
def test_owner_logical_gate_views(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Real constructor/local records; native admission and primary I/O doubled."""
    import copy
    from dataclasses import asdict

    from tools import agent_epic_delivery as delivery
    from tools import agent_epic_final_delivery as final_delivery
    from tools import agent_final_preparation_records as records
    from tools import agent_final_preparation_sources as sources_module
    from tools import agent_owner_final_admission as admission

    unit = _logical_gate_protocol_unit(tmp_path, monkeypatch)
    inv, session, reader = (unit[key] for key in ("invocation", "session", "reader"))
    counts, store = unit["counters"], unit["store"]
    merge, close = unit["merge"], unit["close"]
    original_read = session._github_read

    def assert_closed() -> None:
        assert inv not in admission._OWNER_VIEWS
        assert inv._a_witness is None and inv._intent_lineage is None
        assert inv._verified_b is None

    def fail_read(_argv: Any) -> str:
        raise LookupError("unit-primary")

    def merge_applied() -> Any:
        unit["merge_a"]()
        ledger = unit["intent"](merge)
        unit["b"](merge)
        unit["applied"](merge, ledger)
        return ledger

    if case == "legacy_default_full":
        from tools import agent_epic_session_runtime as session_module

        seen: list[Any] = []

        class Selected(BaseException):
            pass

        class Legacy:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                seen.append((args, kwargs))
                raise Selected

        # Fixed original legacy dispatch; this is an admitted unit envelope.
        session.config = {
            **session.config,
            "schema_version": "7.0",
            "approved_final_digest": "sha256:" + "a" * 64,
        }
        session._adapter = lambda *_: None
        monkeypatch.setattr(session_module, "FinalRuntime", Legacy)
        with pytest.raises(Selected):
            session._final_resume(2)
        assert len(seen) == 1 and counts["constructors"] == 0
        assert inv not in admission._OWNER_VIEWS
        return

    if case == "wrong_session_reader_refusal":
        session.wait_scope = object()
        with unit["owned"](), pytest.raises(PhaseBlocked):
            admission._assess_current(unit["scope"])
        assert counts["constructors"] == 0
        assert_closed()
        return

    if case in {
        "body_primary_full_exit_attempted",
        "binding_failure_full_exit_attempted",
        "constructor_failure_no_output",
        "exit_proof_failure_no_A_or_B",
    }:
        exits: list[Any] = []
        stable = sources_module.CurrentPreparation.stable_assessment

        def exit_probe(current: Any) -> dict[str, Any]:
            view = admission._OWNER_VIEWS.get(inv)
            if view is not None and view.phase == "EXITING":
                exits.append(current)
                if case == "exit_proof_failure_no_A_or_B":
                    raise LookupError("unit-exit")
            return stable(current)

        monkeypatch.setattr(
            sources_module.CurrentPreparation, "stable_assessment", exit_probe
        )
        if case == "body_primary_full_exit_attempted":
            session._github_read = fail_read
        elif case == "binding_failure_full_exit_attempted":

            def swapped(argv: Any) -> str:
                session.wait_scope = object()
                return cast(str, original_read(argv))

            session._github_read = swapped
        elif case == "constructor_failure_no_output":

            def absent_review(_session: Any) -> Any:
                raise LookupError("unit-constructor")

            monkeypatch.setattr(
                original_runtime, "read_completed_learning_review_owned", absent_review
            )
        expected = (
            PhaseBlocked
            if case == "binding_failure_full_exit_attempted"
            else LookupError
        )
        with unit["owned"](), pytest.raises(expected):
            admission._assess_current(unit["scope"])
        assert len(exits) == (0 if case == "constructor_failure_no_output" else 1)
        assert inv.pre_merge_sources is None and inv.candidate is None
        assert_closed()
        return

    if case in {
        "request_original_record_equality",
        "currentCI_sameURL_remains_current",
        "no_lessons_status",
        "returned_fact_copy_mutation_no_authority",
        "sameobject_reentry_preserves_outer",
    }:
        observed: list[Any] = []
        reads: list[str] = []

        def observed_read(argv: Any) -> str:
            view = admission._OWNER_VIEWS[inv]
            reads.append(argv[-1])
            if not observed:
                assert type(view.source) is sources_module.CurrentPreparation
                if case == "request_original_record_equality":
                    proposed = view.derivations.request()
                    expected_request = next(iter(unit["rows"]["ledger"].values()))[
                        "request"
                    ]
                    assert proposed == expected_request
                    observed.append(proposed)
                elif case == "no_lessons_status":
                    observed.append(view.derivations.report_statuses())
                elif case == "returned_fact_copy_mutation_no_authority":
                    packet = view.derivations.packet
                    packet["source_digest"] = "0" * 64
                    assert view.derivations.packet["source_digest"] == "c" * 64
                    observed.append(True)
                elif case == "sameobject_reentry_preserves_outer":
                    with pytest.raises(PhaseBlocked):
                        view.__enter__()
                    assert admission._OWNER_VIEWS[inv] is view
                    assert view.phase == "DERIVING" and not inv._view_poisoned
                    observed.append(view)
                else:
                    observed.append(view)
            return cast(str, original_read(argv))

        session._github_read = observed_read
        session._historical_read = fail_read
        with unit["owned"]():
            assert admission._assess_current(unit["scope"])["status"] == "PASS"
            assert counts["constructors"] == 1 and counts["packets"] == 5
            if case == "request_original_record_equality":
                assert inv.pre_merge_sources.request() == observed[0]
                assert counts["packets"] == 7
        assert observed
        if case == "no_lessons_status":
            assert observed == [("NONE_APPLIED", "UNMEASURED")]
        if case == "currentCI_sameURL_remains_current":
            pr = f"repos/{unit['candidate']['repository']}/pulls/50"
            assert reads.count(pr) >= 2
            assert any("/actions/workflows/" in endpoint for endpoint in reads)
        assert inv not in admission._OWNER_VIEWS
        return

    unit["owner_graph"]()
    with unit["owned"]():
        if case == "human_complete_graph_full_default":
            result = admission._assess_current(unit["scope"])
            assert result["status"] == "PASS"
            assert inv._view_flow == "FULL_DEFAULT"
            assert counts["constructors"] == 1 and counts["packets"] > 5
            assert inv._a_witness is None
            return

        positive = {
            "mergeA_fresh_wait": (1, 5),
            "mergeB_new_intent": (2, 10),
            "closeA_previous_merge_pending": (3, 15),
            "closeB_new_intent": (4, 20),
            "updateA_previous_close_pending": (5, 25),
            "updateB_new_intent": (6, 30),
        }
        if case in positive:
            if case.startswith("update"):
                request = unit["update_a"]()
            elif case.startswith("close"):
                request = unit["close_a"]()
            else:
                unit["merge_a"]()
                request = merge
            if "B_" in case:
                unit["intent"](request)
                unit["b"](request)
                assert inv._intent_lineage is None
                assert type(inv._verified_b) is admission._VerifiedEffectGate
            else:
                assert inv._a_witness.request == request
                assert inv._a_witness.phase == "CREATED"
            assert (counts["constructors"], counts["packets"]) == positive[case]
            assert inv not in admission._OWNER_VIEWS
            return

        if case in {
            "first_approval_prior_None_callbacks_staged",
            "observed_shape_not_admitted_approval",
        }:
            from tools import agent_final_preparation_owner as owner_reader

            original_approval = owner_reader.read_current_owner_approval
            original_return = admission._owner_approval_reader_return
            staged: list[tuple[Any, str]] = []
            returned: list[str] = []

            def reader_return(invocation: Any, result: Any) -> None:
                original_return(invocation, result)
                view = admission._OWNER_VIEWS[invocation]
                assert view.approval_read is not None
                returned.append(view.approval_read["stage"])

            monkeypatch.setattr(
                admission, "_owner_approval_reader_return", reader_return,
            )

            def approval_read(*args: Any, **kwargs: Any) -> Any:
                installed = kwargs["installed_guard"]
                verifier = kwargs["source_verifier"]

                def checked() -> None:
                    view = admission._OWNER_VIEWS[inv]
                    staged.append((inv.approval, view.approval_read["stage"]))
                    installed()

                def verified(*values: Any) -> Any:
                    view = admission._OWNER_VIEWS[inv]
                    assert view.approval_read["stage"] == "OBSERVED"
                    if case == "observed_shape_not_admitted_approval":
                        raise LookupError("unit-verifier")
                    return verifier(*values)

                return original_approval(
                    *args,
                    **{
                        **kwargs,
                        "installed_guard": checked,
                        "source_verifier": verified,
                    },
                )

            monkeypatch.setattr(
                owner_reader, "read_current_owner_approval", approval_read
            )
            if case == "observed_shape_not_admitted_approval":
                with pytest.raises(LookupError):
                    unit["merge_a"]()
                assert inv.approval is None and inv._owner_graph is None
                assert_closed()
            else:
                unit["merge_a"]()
                assert staged and staged[0] == (None, "OBSERVED")
                assert returned and all(stage == "RETURNED" for stage in returned)
                assert inv.approval is not None and inv._owner_graph is not None
                assert counts["packets"] == 5
            return

        if case == "create_receipt_active_two_callers":
            receipt = admission._original_create_receipt
            spans: list[tuple[str, int]] = []

            def receipt_read(invocation: Any, current: Any, candidate: Any) -> Any:
                before = counts["packets"]
                result = receipt(invocation, current, candidate)
                final = store.read("final-state")
                assert final is not None
                spans.append((final["phase"], counts["packets"] - before))
                return result

            monkeypatch.setattr(admission, "_original_create_receipt", receipt_read)
            unit["close_a"]()
            assert {phase for phase, _ in spans} >= {
                "WAITING_OWNER_FINAL_APPROVAL",
                "MERGING",
                "MAIN_CI_PENDING",
            }
            assert all(delta == 0 for _, delta in spans)
            return

        if case in {
            "A_publish_exact_transition",
            "A_INTENT_exact_added_entry",
            "partial_publish_poison_no_adopt",
            "partial_INTENT_poison_no_replay",
            "fresh_optimized_B_missing_marker_stop",
            "fresh_optimized_B_corrupt_marker_stop",
            "marker_consumed_once",
            "B_before_verified_ticket_and_dispatch",
            "durable_vs_observed_clock_floor_across_gates",
            "counter_attempt_identity_nonreset",
            "B_rawjoin_no_factory_reentry",
        }:
            unit["merge_a"]()
            if case == "A_publish_exact_transition":
                prior = store.read("final-state")
                admission._owner_publish(
                    inv, "MERGING", admission._pending_owner_identity(merge)
                )
                after = store.read("final-state")
                assert after == records.seal(
                    {
                        **{
                            key: value
                            for key, value in prior.items()
                            if key != "record_digest"
                        },
                        "phase": "MERGING",
                        "pending_operation_id": admission._pending_owner_identity(
                            merge
                        ),
                    }
                )
                assert inv._a_witness.phase == "PUBLISHED"
                assert admission._owner_ledger(inv) is None
                return
            if case == "partial_publish_poison_no_adopt":
                write = type(store).write

                def partial(current: Any, name: str, value: Any) -> None:
                    write(current, name, value)
                    if name == "final-state" and value["phase"] == "MERGING":
                        raise OSError("unit-postwrite")

                monkeypatch.setattr(type(store), "write", partial)
                with pytest.raises(OSError):
                    unit["intent"](merge)
                assert store.read("final-state")["phase"] == "MERGING"
                assert inv._view_poisoned
                assert_closed()
                with pytest.raises(PhaseBlocked):
                    unit["intent"](merge)
                return
            if case == "partial_INTENT_poison_no_replay":
                ledger_read = admission._owner_ledger
                observed_intent: list[bool] = []

                def uncertain(invocation: Any) -> Any:
                    value = ledger_read(invocation)
                    if (
                        value is not None
                        and value[merge.operation_id]["status"] == "INTENT"
                    ):
                        observed_intent.append(True)
                        raise OSError("unit-reread")
                    return value

                monkeypatch.setattr(admission, "_owner_ledger", uncertain)
                with pytest.raises(OSError):
                    unit["intent"](merge)
                monkeypatch.setattr(admission, "_owner_ledger", ledger_read)
                assert observed_intent
                written = ledger_read(inv)
                assert written is not None
                assert written[merge.operation_id]["status"] == "INTENT"
                assert inv._view_poisoned
                assert_closed()
                with pytest.raises(PhaseBlocked):
                    unit["b"](merge)
                return
            if case == "durable_vs_observed_clock_floor_across_gates":
                unit["now"][0] = 145.0
                inv._a_witness.check()
                unit["now"][0] = 146.0
                inv._a_witness.check()
                assert unit["saved_wait"]["last_seen_at"] == 140
                assert inv._view_clock["observed"] == 146.0
            ledger = unit["intent"](merge)
            persisted = copy.deepcopy(ledger.load())
            if case == "A_INTENT_exact_added_entry":
                assert persisted == {
                    merge.operation_id: final_delivery._entry(
                        merge,
                        "INTENT",
                        None,
                    )
                }
                assert (
                    inv._a_witness is None and inv._intent_lineage["request"] == merge
                )
                assert inv._gate_a_request is None and inv._new_ledger_request is None
                assert inv._verified_b is None
                return
            if case in {
                "fresh_optimized_B_missing_marker_stop",
                "fresh_optimized_B_corrupt_marker_stop",
                "durable_vs_observed_clock_floor_across_gates",
                "counter_attempt_identity_nonreset",
            }:
                if case == "fresh_optimized_B_missing_marker_stop":
                    inv._intent_lineage = None
                elif case == "fresh_optimized_B_corrupt_marker_stop":
                    inv._intent_lineage["ledger"] = "0" * 64
                elif case == "durable_vs_observed_clock_floor_across_gates":
                    unit["now"][0] = 145.5
                else:
                    unit["saved_wait"]["ci"]["identity_digest"] = "sha256:" + "0" * 64
                before = counts["constructors"]
                with pytest.raises(PhaseBlocked):
                    unit["b"](merge)
                assert counts["constructors"] == before
                assert ledger.load() == persisted
                assert unit["saved_wait"]["ci"]["attempts"] == 4
                assert inv._view_poisoned
                assert_closed()
                return
            if case == "B_before_verified_ticket_and_dispatch":
                finish = sources_module._OwnerBGateView.__exit__
                order: list[str] = []

                def finished(view: Any, *args: Any) -> Any:
                    assert inv._verified_b is None
                    result = finish(view, *args)
                    assert inv not in admission._OWNER_VIEWS
                    assert inv._verified_b is None
                    order.append("full-exit")
                    return result

                completed = reader.owner_gate_completed

                def ready() -> None:
                    assert type(inv._verified_b) is admission._VerifiedEffectGate
                    order.append("READY")
                    completed()

                monkeypatch.setattr(
                    sources_module._OwnerBGateView, "__exit__", finished
                )
                reader.owner_gate_completed = ready
                transport = admission._owner_transport(inv, merge)
                transport.before_write(merge)
                assert order == ["full-exit", "READY"]
                assert unit["effects"] == {"merged": False, "closed": False}
                return
            if case == "B_rawjoin_no_factory_reentry":

                def unexpected(*_args: Any, **_kwargs: Any) -> Any:
                    raise AssertionError("raw B reentered public factory")

                monkeypatch.setattr(admission, "_assess_current", unexpected)
                monkeypatch.setattr(admission, "_owner_source_gate", unexpected)
            unit["b"](merge)
            assert counts["constructors"] == 2 and counts["packets"] == 10
            if case == "marker_consumed_once":
                before = copy.deepcopy(unit["saved_wait"])
                with pytest.raises(PhaseBlocked):
                    admission._owner_b_source_gate(inv, merge)
                assert unit["saved_wait"] == before
                assert ledger.load() == persisted
                assert counts["constructors"] == 2
            return

        if case in {
            "workflow_applied_real_evaluation_join",
            "forged_evaluation_refused",
            "planning_applied_unmeasured",
        }:
            from tools import agent_final_learning_domain as domain
            from tools import agent_final_preparation_report as reporting
            from tools import agent_learning

            from tests.test_agent_learning import measurement

            evaluation = agent_learning.compare(measurement(), measurement(hints=True))
            evaluation_events: list[str] = []

            class Retained:
                def __init__(self, actual_session: Any) -> None:
                    assert actual_session is session
                    evaluation_events.append("open")
                    self.reader = SimpleNamespace(
                        close=lambda: evaluation_events.append("close")
                    )

                def _retained(self) -> Any:
                    evaluation_events.append("retained")
                    return [{"kind": "evaluation", "record": evaluation}], []

                def _guard(self) -> None:
                    evaluation_events.append("guard")

            monkeypatch.setattr(domain, "_FinalPlanningReader", Retained)
            unit["packet"].update(
                calls=[
                    {
                        "invocation_key": "unit",
                        "task": 301,
                        "family": "PLANNING"
                        if case == "planning_applied_unmeasured"
                        else "WORKFLOW",
                    }
                ],
                uses=[{"call_key": "unit", "ordered_lessons": ["a" * 64]}],
                measurements=[
                    {
                        "task": 301,
                        "evaluation_digest": (
                            "0" * 64
                            if case == "forged_evaluation_refused"
                            else source_digest(evaluation)
                        ),
                        "baseline_digest": evaluation["baseline_digest"],
                        "measurement_digest": evaluation["hinted_digest"],
                    }
                ],
            )
            comparison = (
                "VERIFIED_COMPARABLE"
                if case == ("workflow_applied_real_evaluation_join")
                else "UNMEASURED"
            )
            expected_report = reporting.prepare_report(
                inv.frames.sources["profile"],
                unit["candidate"],
                unit["rows"]["assessment-" + unit["candidate"]["preparation_digest"]],
                child_numbers=(301, 303),
                learning_status="VERIFIED_APPLIED",
                comparison_status=comparison,
            )
            store.write("report-" + expected_report["record_digest"], expected_report)
            unit["merge_a"]()
            assert inv.report == expected_report
            assert counts["packets"] == 5
            if case == "planning_applied_unmeasured":
                assert evaluation_events == []
            else:
                assert evaluation_events == [
                    "open",
                    "retained",
                    "guard",
                    "guard",
                    "close",
                ]
            return

        if case in {
            "CI_pending_disables_view",
            "pending_default_B_without_optimized_marker_full",
        }:
            merge_applied()
            unit["advance"]()
            graph = unit["main_graph"]
            check = graph["check_runs"]["check_runs"][0]
            run_id = int(check["details_url"].split("/runs/")[1].split("/")[0])
            job = next(
                item
                for item in graph["jobs"][run_id]["jobs"]
                if item["id"] == check["id"]
            )
            run = graph["runs"][run_id]
            before = copy.deepcopy((check, job, run))
            check.update(status="queued", conclusion=None)
            job.update(status="queued", conclusion=None)
            run.update(status="in_progress", conclusion=None)
            result = admission._owner_source_gate(inv, close)
            assert result["status"] == "CI_PENDING"
            assert inv._view_flow == "DISABLED_DEFAULT"
            assert inv._a_witness is None and inv._intent_lineage is None
            assert inv not in admission._OWNER_VIEWS
            if case == "pending_default_B_without_optimized_marker_full":
                check.update(before[0])
                job.update(before[1])
                run.update(before[2])
                unit["advance"]()
                admission._owner_gate_a(inv, close)
                unit["intent"](close)
                constructors = counts["constructors"]
                assert inv._intent_lineage is None
                unit["b"](close)
                assert inv._view_flow == "DISABLED_DEFAULT"
                assert counts["constructors"] > constructors
                assert inv._verified_b is not None
            return

        if case == "wrong_prior_pending_refused":
            merge_applied()
            unit["advance"]()
            final = store.read("final-state")
            assert final is not None
            store.write(
                "final-state",
                records.seal(
                    {
                        **{
                            key: value
                            for key, value in final.items()
                            if key != "record_digest"
                        },
                        "pending_operation_id": admission._pending_owner_identity(
                            close
                        ),
                    }
                ),
            )
            before = counts["constructors"]
            with pytest.raises(PhaseBlocked):
                admission._owner_gate_a(inv, close)
            assert counts["constructors"] == before
            assert inv._intent_lineage is None
            return

        if case in {
            "nested_mainCI_default12_outsideview",
            "nested_mainCI_exact_active_light_hooks",
            "active_wrong_nested_view_reject_not_default",
        }:
            merge_applied()
            unit["advance"]()
            before = counts["packets"]
            if case == "nested_mainCI_default12_outsideview":
                result = admission._owner_main_ci(inv, close)
                assert result["status"] == "PASS"
                assert counts["packets"] - before == 24
            elif case == "nested_mainCI_exact_active_light_hooks":
                full = admission._MainCiProofGroup._full
                hooks: list[str] = []

                def light(group: Any) -> None:
                    view = admission._OWNER_VIEWS[inv]
                    hooks.append(view.kind)
                    full(group)

                monkeypatch.setattr(admission._MainCiProofGroup, "_full", light)
                admission._owner_gate_a(inv, close)
                assert hooks == ["OWNER_A", "OWNER_A"]
                assert counts["packets"] - before == 5
            else:
                full = admission._MainCiProofGroup._full
                entered: list[bool] = []

                def wrong(group: Any) -> None:
                    view = admission._OWNER_VIEWS[inv]
                    entered.append(True)
                    view.request = merge
                    full(group)

                monkeypatch.setattr(admission._MainCiProofGroup, "_full", wrong)
                with pytest.raises(PhaseBlocked):
                    admission._owner_gate_a(inv, close)
                assert entered == [True]
                assert counts["packets"] - before == 5
                assert inv._view_poisoned
                assert_closed()
            assert inv not in admission._MAIN_CI_GROUPS
            return

        if case in {
            "old_INTENT_readonly",
            "old_UNKNOWN_readonly",
            "restart_MERGING_full",
            "restart_MAIN_CI_full",
        }:
            unit["merge_a"]()
            ledger = unit["intent"](merge)
            if case == "restart_MAIN_CI_full":
                unit["b"](merge)
                unit["applied"](merge, ledger)
            elif case == "old_UNKNOWN_readonly":
                history = ledger.load()
                history[merge.operation_id] = final_delivery._entry(
                    merge, "UNKNOWN", None
                )
                ledger.save(history)
            # A new source-owned invocation has no optimized lineage. This
            # models entry selection, without adopting the prior admission.
            inv._view_flow = "UNSELECTED"
            inv._a_witness = None
            inv._intent_lineage = None
            inv._view_clock = None
            inv._view_last_epoch = None
            unit["advance"]()
            persisted = copy.deepcopy(admission._owner_ledger(inv))
            assert persisted is not None
            calls: list[str] = []

            class ReadOnly:
                def reconcile(self, request: Any) -> Any:
                    assert request == merge
                    calls.append("reconcile")
                    return "NOT_APPLIED", None

                def effect(self, request: Any) -> Any:
                    raise AssertionError("old uncertain operation replayed")

            monkeypatch.setattr(admission, "_owner_transport", lambda *_: ReadOnly())
            if case.startswith("old_"):
                result = admission._owner_deliver(inv, merge)
                assert result.status == "ESCALATE"
                assert calls == ["reconcile"]
            elif case == "restart_MERGING_full":
                before = counts["constructors"]
                result = admission._continue_current(unit["scope"])
                assert result["status"] == "ESCALATE"
                assert inv._view_flow == "FULL_DEFAULT"
                assert counts["constructors"] == before + 1
                assert calls == ["reconcile"]
            else:

                class NextA(BaseException):
                    pass

                def next_a(invocation: Any, request: Any) -> None:
                    assert invocation is inv and request == close
                    assert inv._view_flow == "FULL_DEFAULT"
                    assert inv not in admission._OWNER_VIEWS
                    calls.append("next-A")
                    raise NextA

                monkeypatch.setattr(admission, "_owner_gate_a", next_a)
                before = counts["constructors"]
                with pytest.raises(NextA):
                    admission._continue_current(unit["scope"])
                assert calls == ["next-A"]
                assert counts["constructors"] == before + 1
            assert admission._owner_ledger(inv) == persisted
            assert inv._intent_lineage is None
            return

        if case == "original_UNKNOWN_persistence_attempt_unchanged":
            unit["merge_a"]()
            admission._owner_publish(
                inv, "MERGING", admission._pending_owner_identity(merge)
            )
            original_transport = admission._owner_transport(inv, merge)
            effects: list[str] = []

            class Uncertain:
                def reconcile(self, request: Any) -> Any:
                    assert request == merge
                    return "NOT_APPLIED", None

                def effect(self, request: Any) -> Any:
                    original_transport.before_write(request)
                    effects.append("attempted-unit-transport")
                    raise delivery.OutcomeUnknown

            monkeypatch.setattr(admission, "_owner_transport", lambda *_: Uncertain())
            result = admission._owner_deliver(inv, merge)
            assert result.status == "ESCALATE"
            history = admission._owner_ledger(inv)
            assert history is not None
            assert history[merge.operation_id]["status"] == "UNKNOWN"
            assert history[merge.operation_id]["request"] == asdict(merge)
            assert effects == ["attempted-unit-transport"]
            assert inv._a_witness is None and inv._intent_lineage is None
            again = admission._owner_deliver(inv, merge)
            assert again.status == "ESCALATE"
            assert admission._owner_ledger(inv) == history
            assert effects == ["attempted-unit-transport"]
            return

        raise AssertionError("unimplemented approved matrix case: " + case)




@pytest.mark.parametrize(
    "case", [
        "cap_before_read", "remaining_request_clipped_partial",
        "exact_body_budget_EOF_refusal", "spare_byte_EOF_success",
    ],
)
def test_owner_frame_read_capacity_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Seeded byte prefixes and synthetic reads, not64MiB/native admission."""
    import json

    from tools import agent_epic_wait_frame as frames

    source, namespace = _owner_frame_unit(tmp_path, monkeypatch)
    with source:
        assert source.MAX_TOTAL_BYTES == 64 * 1024 * 1024
        assert source.MAX_FRAME_BYTES == 16384 and source.MAX_ENTRIES == 4096
        encoded = json.dumps(source.frame, sort_keys=True).encode()
        assert len(encoded) < 1024
        name = source.frame["invocation_id"] + ".json"
        path = namespace / name
        path.write_bytes(encoded)
        path.chmod(0o600)
        if case == "cap_before_read":
            source._bytes = source.MAX_TOTAL_BYTES
        elif case == "remaining_request_clipped_partial":
            source._bytes = source.MAX_TOTAL_BYTES - 1
        elif case == "exact_body_budget_EOF_refusal":
            source._bytes = source.MAX_TOTAL_BYTES - len(encoded)
        else:
            assert case == "spare_byte_EOF_success"
            source._bytes = source.MAX_TOTAL_BYTES - len(encoded) - 1
        original_open, original_read = os.open, os.read
        active: list[int] = []
        requests: list[int] = []
        chunks = [encoded, b""]

        def observed_open(*args: Any, **kwargs: Any) -> int:
            descriptor = original_open(*args, **kwargs)
            if args[0] == name and kwargs.get("dir_fd") == source._namespace:
                active.append(descriptor)
            return descriptor

        def synthetic_read(descriptor: int, count: int) -> bytes:
            if descriptor not in active:
                return original_read(descriptor, count)
            assert 0 < count <= source.MAX_TOTAL_BYTES - source._bytes
            requests.append(count)
            if case == "remaining_request_clipped_partial":
                assert requests == [1]
                return b"{"
            assert chunks
            chunk = chunks.pop(0)
            assert len(chunk) <= count
            return chunk

        monkeypatch.setattr(os, "open", observed_open)
        monkeypatch.setattr(os, "read", synthetic_read)
        if case == "spare_byte_EOF_success":
            value, _identity = source._read(name)
            assert value == source.frame
            assert requests == [len(encoded) + 1, 1]
            assert chunks == []
            assert source._bytes == source.MAX_TOTAL_BYTES - 1
        else:
            with pytest.raises(frames.FrameError, match="WAIT_FRAME_INVALID"):
                source._read(name)
            assert source._bytes == source.MAX_TOTAL_BYTES
            expected = ([] if case == "cap_before_read" else [1]
                        if case == "remaining_request_clipped_partial"
                        else [len(encoded)])
            assert requests == expected
        assert len(active) == 1
        assert source._bytes <= source.MAX_TOTAL_BYTES
