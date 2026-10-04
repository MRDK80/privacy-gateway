"""Synthetic fake-time failure paths for owner-approved hotfix #290."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from tools import agent_epic_loop as loop


def adapter(tmp_path: Path, value: dict[str, Any]) -> loop.CommandRuntimeAdapter:
    item = loop.CommandRuntimeAdapter(
        root=tmp_path,
        live_command=("synthetic",),
        phase_commands={"RUN_TASK": ("synthetic",)},
        reconcile_commands={},
        timeout_seconds=3600,
        output_limit=1024,
    )
    setattr(item, "_run", lambda _: value)
    return item


@pytest.mark.parametrize("entry", ["live", "effect"])
@pytest.mark.parametrize("status", ["BLOCKED", "ESCALATE"])
def test_valid_runtime_error_preserves_original_code(
    tmp_path: Path, entry: str, status: str
) -> None:
    item = adapter(
        tmp_path,
        {"status": status, "machine_code": "LIVE_FACTS_UNAVAILABLE", "receipt": None},
    )
    error = loop.PhaseBlocked if status == "BLOCKED" else loop.LoopError
    with pytest.raises(error) as caught:
        item.live() if entry == "live" else item.effect("RUN_TASK")
    assert isinstance(caught.value, (loop.LoopError, loop.PhaseBlocked))
    assert caught.value.machine_code == "LIVE_FACTS_UNAVAILABLE"


@pytest.mark.parametrize(
    "value",
    [
        {"status": "ESCALATE", "machine_code": "unsafe sentinel", "receipt": None},
        {"status": "ESCALATE", "machine_code": "SAFE", "receipt": {}},
        {"status": "ESCALATE", "machine_code": "SAFE", "receipt": None, "extra": 1},
        {"status": "UNKNOWN", "machine_code": "SAFE", "receipt": None},
    ],
)
def test_malformed_error_envelope_stays_invalid(
    tmp_path: Path, value: dict[str, Any]
) -> None:
    with pytest.raises(loop.LoopError, match="RUNTIME_ADAPTER_INVALID"):
        adapter(tmp_path, value).live()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def runner(
    clock: Clock, results: list[Any], starts: list[float], guard: Any = None
) -> Any:
    from tools import agent_github_read as read

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        import subprocess

        assert kwargs["timeout"] == 120
        starts.append(clock.now)
        clock.now += 7  # request duration must not shorten the next delay
        result = results.pop(0)
        if isinstance(result, BaseException):
            raise result
        code, stdout, stderr = result
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    return read.Reader(
        run=run, monotonic=lambda: clock.now, sleep=clock.sleep, guard=guard
    )


TRANSIENT = (
    1,
    "synthetic stdout sentinel",
    'Get "https://example.invalid": net/http: TLS handshake timeout',
)
COMMAND = ("gh", "api", "repos/OWNER/repository/issues/290")


def test_exact_schedule_count_exhaustion_and_sanitized_failure() -> None:
    from tools import agent_github_read as read

    clock = Clock()
    starts: list[float] = []
    item = runner(clock, [TRANSIENT] * 11, starts)
    with pytest.raises(read.ReadFailure) as caught:
        item.command(COMMAND)
    assert len(starts) == 11
    assert [starts[i + 1] - starts[i] - 7 for i in range(10)] == [
        60,
        60,
        60,
        60,
        60,
        180,
        300,
        600,
        900,
        1200,
    ]
    assert sum(clock.sleeps) == 3480
    assert max(clock.sleeps) <= 1
    assert caught.value.category == "timeout"
    assert "sentinel" not in str(caught.value) + repr(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("failures", [0, 1, 3, 10])
def test_early_success_stops_immediately(failures: int) -> None:
    clock = Clock()
    starts: list[float] = []
    item = runner(clock, [TRANSIENT] * failures + [(0, '{"number":290}', "")], starts)
    assert item.command(COMMAND) == '{"number":290}'
    assert len(starts) == failures + 1
    assert sum(clock.sleeps) == sum(
        (60, 60, 60, 60, 60, 180, 300, 600, 900, 1200)[:failures]
    )


@pytest.mark.parametrize(
    "message",
    [
        "HTTP 401 authentication required",
        "HTTP 403 forbidden; TLS handshake timeout",
        "permission denied; net/http: TLS handshake timeout",
        "stale SHA; net/http: TLS handshake timeout",
        "policy mismatch; net/http: TLS handshake timeout",
        "identity mismatch; net/http: TLS handshake timeout",
        "unknown timeout",
        "error connecting to api.github.com",
        "HTTP 500",
    ],
)
def test_non_transient_errors_never_wait(message: str) -> None:
    from tools import agent_github_read as read

    clock = Clock()
    starts: list[float] = []
    with pytest.raises(read.ReadFailure):
        runner(clock, [(1, "sentinel", message)], starts).command(COMMAND)
    assert len(starts) == 1 and not clock.sleeps


@pytest.mark.parametrize("kind", ["timeout", "network", "permission", "unsupported"])
def test_subprocess_exception_evidence(kind: str) -> None:
    import errno
    import subprocess

    from tools import agent_github_read as read

    errors = {
        "timeout": subprocess.TimeoutExpired("synthetic sensitive sentinel", 120),
        "network": OSError(errno.ENETUNREACH, "synthetic sensitive sentinel"),
        "permission": PermissionError("synthetic sensitive sentinel"),
        "unsupported": FileNotFoundError("synthetic sensitive sentinel"),
    }
    clock = Clock()
    starts: list[float] = []
    item = runner(clock, [errors[kind], (0, "{}", "")], starts)
    if kind in {"timeout", "network"}:
        assert item.command(COMMAND) == "{}"
        assert len(starts) == 2 and sum(clock.sleeps) == 60
    else:
        with pytest.raises(read.ReadFailure):
            item.command(COMMAND)
        assert len(starts) == 1 and not clock.sleeps


@pytest.mark.parametrize(
    "code", ["RECOVERY_STOPPED", "MANDATE_EXPIRED", "LIVE_IDENTITY_CHANGED"]
)
def test_guard_interrupts_wait_before_next_request(code: str) -> None:
    clock = Clock()
    starts: list[float] = []

    def guard() -> None:
        if clock.now >= 12:
            raise loop.LoopError(code)

    with pytest.raises(loop.LoopError, match=code):
        runner(clock, [TRANSIENT, (0, "{}", "")], starts, guard).command(COMMAND)
    assert len(starts) == 1
    assert sum(clock.sleeps) == 5


def test_cancellation_is_not_retried() -> None:
    clock = Clock()
    starts: list[float] = []

    def cancel() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        runner(clock, [TRANSIENT], starts, cancel).command(COMMAND)
    assert len(starts) == 1 and not clock.sleeps


@pytest.mark.parametrize(
    "command",
    [
        ("gh", "api", "repos/OWNER/repository/pulls", "--method", "POST"),
        ("gh", "api", "repos/OWNER/repository/pulls", "-f", "body=sentinel"),
        ("gh", "pr", "merge", "291"),
        ("gh", "issue", "close", "290"),
        ("git", "push", "origin", "HEAD"),
        ("executor",),
        ("controller",),
        ("gh", "api", "graphql"),
    ],
)
def test_read_runner_refuses_writes_and_unknown_adapters(
    command: tuple[str, ...],
) -> None:
    from tools import agent_github_read as read

    clock = Clock()
    starts: list[float] = []
    with pytest.raises(read.ReadFailure):
        runner(clock, [], starts).command(command)
    assert not starts and not clock.sleeps


@pytest.mark.parametrize("body", ["invalid-json-sentinel", "[]"])
def test_malformed_success_is_not_retried(body: str) -> None:
    from tools import agent_epic_queue as queue

    clock = Clock()
    starts: list[float] = []
    item = runner(clock, [(0, body, "")], starts)
    with pytest.raises(queue.GitHubError):
        queue.GhClient(read=item.command).issue("OWNER/repository", 290)
    assert len(starts) == 1 and not clock.sleeps


def test_reconciliation_read_uses_retries_but_write_timeout_is_single_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from tools import agent_epic_delivery as delivery
    from tools import agent_epic_transport as transport

    from tests.test_agent_epic_transport import request

    clock = Clock()
    starts: list[float] = []
    item = runner(clock, [TRANSIENT, (0, "[]", "")], starts)
    client = transport.GhTaskTransport(tmp_path, read=item.command)
    assert client.read_json(COMMAND) == []
    assert len(starts) == 2 and sum(clock.sleeps) == 60
    writes: list[Sequence[str]] = []

    def write(argv: Sequence[str], **kwargs: Any) -> Any:
        writes.append(argv)
        raise subprocess.TimeoutExpired("synthetic sensitive sentinel", 120)

    monkeypatch.setattr(subprocess, "run", write)
    with pytest.raises(delivery.OutcomeUnknown):
        client.write_json(
            ("gh", "api", "repos/OWNER/repository/pulls", "--method", "POST"),
            request("create_task_pr"),
        )
    assert len(writes) == 1 and len(starts) == 2


@pytest.mark.parametrize("change", ["expiry", "revocation", "identity", "owner_stop"])
def test_concrete_runtime_guard_stops_retries_and_prevents_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    from tools import agent_coordinator_delivery as checks
    from tools import agent_epic_delivery as delivery
    from tools import agent_epic_queue as queue
    from tools import agent_epic_recovery as recovery
    from tools import agent_github_read as read

    from tests.test_agent_epic_runtime import build

    engine, _ = build(tmp_path, monkeypatch, "RUN_TASK")
    clock = Clock()
    starts: list[float] = []
    current, _, _ = engine.authority()
    mandate = dict(current)
    if change == "expiry":
        mandate["expires_at"] = 212
        approval = mandate["approval"]
        assert isinstance(approval, dict)
        approval["mandate_digest"] = delivery.mandate_digest(mandate)
    approved = delivery.mandate_digest(mandate)
    engine.authority = lambda: (
        mandate,
        approved,
        delivery.MandateContext(200 + int(clock.now), "OWNER", 150, 0, 0),
    )
    control = recovery.Control(engine.store.directory, engine.store.repository_root)
    if change == "owner_stop":
        control.save(control.load() | {"stopped": False})

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.now == 12:
            if change == "revocation":
                mandate["revoked"] = True
            elif change == "identity":
                saved = engine.store.load()
                assert saved is not None
                engine.store.save(saved | {"head_sha": "d" * 40})
            elif change == "owner_stop":
                control.stop()

    item = runner(clock, [TRANSIENT, (0, "{}", "")], starts)
    item.sleep = sleep

    def factory(*, guard: Any) -> Any:
        item.guard = guard
        return item

    monkeypatch.setattr(read, "Reader", factory)
    engine.github = checks.GhClient(read=engine._github_read)
    engine.issues = queue.GhClient(read=engine._github_read)
    effects: list[object] = []
    monkeypatch.setattr(engine.transport, "command", lambda argv: effects.append(argv))
    saved = engine.store.load()
    assert saved is not None
    request = delivery.Request(
        operation_id="synthetic-create",
        operation="create_task_pr",
        schema_version="2.0",
        **{key: saved[key] for key in loop.IDENTITY_KEYS},
    )
    expected = {
        "expiry": "MANDATE_EXPIRED",
        "revocation": "MANDATE_APPROVAL_MISMATCH",
        "identity": "LIVE_IDENTITY_CHANGED",
        "owner_stop": "RECOVERY_STOPPED",
    }[change]
    with pytest.raises(loop.LoopError, match=expected):
        engine.transport.write_command(("git", "push", "origin", "HEAD"), request)
    assert len(starts) == 1 and sum(clock.sleeps) == 5 and not effects
    if change == "owner_stop":
        before = control.path.read_bytes()
        assert control.load()["stopped"] and control.path.read_bytes() == before


@pytest.mark.parametrize("change", ["expiry", "extension", "unpinned"])
def test_recovery_approval_expiry_cannot_be_extended_by_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    import json
    from types import SimpleNamespace

    from tools import agent_epic_delivery as delivery
    from tools import agent_epic_recovery as recovery
    from tools import agent_github_read as read

    from tests.test_agent_epic_recovery import approved_control
    from tests.test_agent_epic_runtime import build

    engine, _ = build(tmp_path, monkeypatch, "RUN_TASK")
    control, approval, _ = approved_control(tmp_path)
    approval["expires_at"] = 212
    pinned: str | None = recovery.digest(approval)
    path = control.directory / "bootstrap-recovery-approval.json"
    path.write_text(json.dumps(approval), encoding="utf-8")
    path.chmod(0o600)
    engine.recovery = SimpleNamespace(approved=lambda: pinned)
    clock = Clock()
    starts: list[float] = []
    mandate, digest, _ = engine.authority()
    engine.authority = lambda: (
        mandate,
        digest,
        delivery.MandateContext(200 + int(clock.now), "OWNER", 150, 0, 0),
    )
    before = control.path.read_bytes()

    def sleep(seconds: float) -> None:
        nonlocal pinned
        clock.sleep(seconds)
        if clock.now == 12 and change != "expiry":
            if change == "extension":
                approval["expires_at"] = 500
                path.write_text(json.dumps(approval), encoding="utf-8")
                pinned = recovery.digest(approval)
            else:
                pinned = None

    item = runner(clock, [TRANSIENT, (0, "{}", "")], starts)
    item.sleep = sleep

    def factory(*, guard: Any) -> Any:
        item.guard = guard
        return item

    monkeypatch.setattr(read, "Reader", factory)
    with pytest.raises(loop.LoopError, match="RECOVERY_STOPPED"):
        engine._github_read(COMMAND)
    assert len(starts) == 1 and sum(clock.sleeps) == 5
    assert control.path.read_bytes() == before


@pytest.mark.parametrize("status", [[], {}, 1, None])
def test_non_string_error_status_is_controlled_invalid(
    tmp_path: Path, status: object
) -> None:
    with pytest.raises(loop.LoopError, match="RUNTIME_ADAPTER_INVALID"):
        adapter(
            tmp_path, {"status": status, "machine_code": "SAFE", "receipt": None}
        ).live()


def test_blocked_live_envelope_stops_supervisor_without_effects(tmp_path: Path) -> None:
    from tests.test_agent_epic_loop import _checkpoint

    store = loop.CheckpointStore(tmp_path / "state", tmp_path / "repo")
    store.save(_checkpoint())
    item = adapter(
        tmp_path,
        {"status": "BLOCKED", "machine_code": "MANDATE_EXPIRED", "receipt": None},
    )
    result = loop.resume(store=store, adapter=item)
    assert result.status == "BLOCKED" and result.machine_code == "MANDATE_EXPIRED"


@pytest.mark.parametrize(
    "diagnostic",
    [
        'Get "https://example.invalid": dial tcp 192.0.2.1:443: i/o timeout',
        'Get "https://example.invalid": dial tcp 192.0.2.1:443: '
        "connect: connection refused",
        'Get "https://example.invalid": read tcp 192.0.2.1:443: '
        "read: connection reset by peer",
    ],
)
def test_proven_transport_network_diagnostics(diagnostic: str) -> None:
    clock = Clock()
    starts: list[float] = []
    assert (
        runner(clock, [(1, "", diagnostic), (0, "{}", "")], starts).command(COMMAND)
        == "{}"
    )
    assert len(starts) == 2 and sum(clock.sleeps) == 60


def test_unknown_diagnostic_beside_timeout_does_not_establish_transience() -> None:
    from tools import agent_github_read as read

    clock = Clock()
    starts: list[float] = []
    result = (1, "", "unknown adapter failure\n" + TRANSIENT[2])
    with pytest.raises(read.ReadFailure):
        runner(clock, [result], starts).command(COMMAND)
    assert len(starts) == 1 and not clock.sleeps


def test_exhausted_read_preserves_code_and_durably_stops_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import agent_epic_queue as queue
    from tools import agent_epic_recovery as recovery
    from tools import agent_github_read as read

    from tests.test_agent_epic_recovery import approved_control
    from tests.test_agent_epic_runtime import build

    engine, _ = build(tmp_path, monkeypatch, "RUN_TASK")
    control, _, _ = approved_control(tmp_path)
    clock = Clock()
    starts: list[float] = []
    reader = runner(clock, [TRANSIENT] * 11, starts)

    def factory(*, guard: Any) -> Any:
        reader.guard = guard
        return reader

    monkeypatch.setattr(read, "Reader", factory)
    engine.issues = queue.GhClient(read=engine._github_read)
    item = adapter(tmp_path, {})
    item.state_directory = engine.store.directory
    item.root = engine.store.repository_root
    # Existing approval entry itself is covered by recovery tests; isolate the
    # effect error/stop path here, using the same concrete live read failure.
    monkeypatch.setattr(recovery, "command_entry", lambda *_: None)

    def envelope(_: Sequence[str]) -> dict[str, Any]:
        try:
            engine.live()
        except loop.LoopError as error:
            return {
                "status": "ESCALATE",
                "machine_code": error.machine_code,
                "receipt": None,
            }
        pytest.fail("exhausted read cannot reach effect")

    setattr(item, "_run", envelope)
    with pytest.raises(loop.LoopError, match="LIVE_FACTS_UNAVAILABLE"):
        item.effect("RUN_TASK")
    assert len(starts) == 11 and sum(clock.sleeps) == 3480
    restarted = recovery.Control(engine.store.directory, engine.store.repository_root)
    assert restarted.load()["stopped"] and restarted.load()["generation"] == 1
    with pytest.raises(recovery.RecoveryError, match="RECOVERY_STOPPED"):
        restarted.check_entry()


def test_delay_is_measured_from_completion_before_diagnostic_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import agent_github_read as read

    clock = Clock()
    starts: list[float] = []
    classify = read._category

    def diagnostic(stderr: object) -> str:
        clock.now += 3
        return classify(stderr)

    monkeypatch.setattr(read, "_category", diagnostic)
    assert runner(clock, [TRANSIENT, (0, "{}", "")], starts).command(COMMAND) == "{}"
    assert starts == [0, 67]


@pytest.mark.parametrize("change", ["state_missing", "approval_without_state"])
def test_missing_recovery_state_does_not_bypass_retry_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    import json
    from types import SimpleNamespace

    from tools import agent_epic_delivery as delivery
    from tools import agent_epic_recovery as recovery
    from tools import agent_github_read as read

    from tests.test_agent_epic_recovery import approved_control
    from tests.test_agent_epic_runtime import build

    engine, _ = build(tmp_path, monkeypatch, "RUN_TASK")
    control, approval, _ = approved_control(tmp_path)
    approval["expires_at"] = 212
    pinned = recovery.digest(approval)
    path = control.directory / "bootstrap-recovery-approval.json"
    path.write_text(json.dumps(approval), encoding="utf-8")
    path.chmod(0o600)
    engine.recovery = SimpleNamespace(approved=lambda: pinned)
    if change == "approval_without_state":
        control.path.unlink()
    clock = Clock()
    starts: list[float] = []
    mandate, digest, _ = engine.authority()
    engine.authority = lambda: (
        mandate,
        digest,
        delivery.MandateContext(200 + int(clock.now), "OWNER", 150, 0, 0),
    )

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.now == 12 and change == "state_missing":
            control.path.unlink()

    item = runner(clock, [TRANSIENT, (0, "{}", "")], starts)
    item.sleep = sleep

    def factory(*, guard: Any) -> Any:
        item.guard = guard
        return item

    monkeypatch.setattr(read, "Reader", factory)
    with pytest.raises(loop.LoopError, match="RECOVERY_STOPPED"):
        engine._github_read(COMMAND)
    assert len(starts) == 1 and sum(clock.sleeps) == 5
