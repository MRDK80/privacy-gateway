"""Closed diagnostic reports and incomplete-installation bootstrap (ADR-113)."""

from __future__ import annotations

import importlib
import importlib.metadata
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from privacy_gateway import __version__, doctor

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.metadata, "version", lambda _: __version__)
    monkeypatch.setattr(doctor, "keyring_capability", lambda: "OK")


def test_success_report(healthy: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.main([]) == 0
    report = capsys.readouterr()
    python = "OK" if sys.version_info[:2] in {(3, 11), (3, 12)} else "WARNING"
    assert report.out == (
        "PACKAGE: OK\n"
        + f"PYTHON: {python}\n"
        + "DEPENDENCIES: OK\nKEYRING: OK\nCONFIG: NOT_REQUESTED\n"
        "OPTIONAL: NOT_AVAILABLE\n" + f"SUMMARY: {python}\n"
    )
    assert report.err == ""


@pytest.mark.parametrize("name", ["cryptography", "keyring", "yaml"])
def test_missing_required_dependency(
    name: str,
    healthy: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    original = importlib.import_module

    def missing(module: str) -> Any:
        if module == name:
            print("sensitive diagnostic")
            raise ImportError("private path or credential")
        return original(module)

    monkeypatch.setattr(importlib, "import_module", missing)
    assert doctor.main(["--entities-config", str(tmp_path / "private")]) == 3
    captured = capsys.readouterr()
    assert "DEPENDENCIES: FAILURE\n" in captured.out
    assert "SUMMARY: FAILURE\n" in captured.out
    if name == "yaml":
        assert "CONFIG: NOT_AVAILABLE\n" in captured.out
    if name == "keyring":
        assert "KEYRING: NOT_AVAILABLE\n" in captured.out
    assert "private" not in captured.out and "sensitive" not in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    "content,expected", [("{}", "OK"), ("[]", "FAILURE"), ("secret: [", "FAILURE")]
)
def test_explicit_config(
    content: str,
    expected: str,
    healthy: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "private-config.yaml"
    config.write_text(content)
    assert doctor.main(["--entities-config", str(config)]) == (
        0 if expected == "OK" else 3
    )
    captured = capsys.readouterr()
    assert f"CONFIG: {expected}\n" in captured.out
    assert "secret" not in captured.out and str(config) not in captured.out
    assert config.read_text() == content
    assert captured.err == ""


def test_backend_warning_is_not_failure(
    healthy: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(doctor, "keyring_capability", lambda: "WARNING")
    assert doctor.main([]) == 0
    assert "SUMMARY: WARNING\n" in capsys.readouterr().out


def test_metadata_failure(
    healthy: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(importlib.metadata, "version", lambda _: "other")
    assert doctor.main([]) == 3
    assert "PACKAGE: FAILURE\n" in capsys.readouterr().out


def test_unexpected_error_is_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken(_: Path | None) -> dict[str, str]:
        raise RuntimeError("private exception")

    monkeypatch.setattr(doctor, "diagnose", broken)
    before = sys.dont_write_bytecode
    assert doctor.main([]) == 1
    assert sys.dont_write_bytecode == before
    assert capsys.readouterr() == ("", "Doctor failed.\n")


def test_parser_error_is_closed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as result:
        doctor.main(["--private-secret"])
    assert result.value.code == 3
    assert capsys.readouterr() == ("", "Invalid doctor arguments.\n")


@pytest.mark.parametrize("mode", ["module", "console", "json"])
def test_incomplete_installation_entrypoints(mode: str, tmp_path: Path) -> None:
    # A fresh process rejects every base dependency before package import.
    script = r"""
import importlib.abc
import runpy
import sys
sys.path.insert(0, sys.argv[1])
mode = sys.argv[2]
class Missing(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'cryptography', 'keyring', 'yaml'}:
            raise ImportError('PRIVATE INPUT MUST NOT ESCAPE')
sys.meta_path.insert(0, Missing())
sys.argv = ['pgw'] + (['--json', 'doctor'] if mode == 'json' else ['doctor'])
if mode == 'module':
    runpy.run_module('privacy_gateway', run_name='__main__')
else:
    from privacy_gateway.bootstrap import main
    main()
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-X",
            "utf8",
            "-c",
            script,
            str(ROOT / "src"),
            mode,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert result.returncode == 3
    assert result.stderr == ""
    assert "PRIVATE" not in result.stdout
    if mode == "json":
        assert json.loads(result.stdout)["error"]["code"] == "unsupported_command"
    else:
        assert len(result.stdout.splitlines()) == 7
        assert "DEPENDENCIES: FAILURE\n" in result.stdout


@pytest.mark.parametrize(
    "payload,rc,expected",
    [
        (b"OK", 0, "OK"),
        (b"WARNING", 0, "WARNING"),
        (b"private", 0, "WARNING"),
        (b"OK", 1, "WARNING"),
        (b"x" * 2048, 0, "WARNING"),
    ],
)
def test_probe_protocol(
    payload: bytes,
    rc: int,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[bool] = []

    def spawn(argv: list[str], **kwargs: Any) -> Any:
        assert argv[:5] == [sys.executable, "-I", "-B", "-X", "utf8"]
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert not any(k.upper().startswith("PYTHON") for k in kwargs["env"])
        return SimpleNamespace(
            stdout=io.BytesIO(payload),
            returncode=rc,
            wait=lambda **_: rc,
            kill=lambda: killed.append(True),
        )

    monkeypatch.setattr(subprocess, "Popen", spawn)
    assert doctor.keyring_capability() == expected
    assert bool(killed) == (len(payload) > 1024)


def test_probe_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def wait(timeout: int | None = None) -> int:
        if timeout is not None:
            assert timeout == 5
            raise subprocess.TimeoutExpired("probe", timeout)
        calls.append("reaped")
        return -9

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: SimpleNamespace(
            stdout=io.BytesIO(b""),
            returncode=-9,
            wait=wait,
            kill=lambda: calls.append("killed"),
        ),
    )
    assert doctor.keyring_capability() == "WARNING"
    assert calls == ["killed", "reaped"]


@pytest.mark.parametrize("platform", ["linux", "win32", "other"])
def test_child_predicate_only(platform: str, tmp_path: Path) -> None:
    # Fake builtins prevent any access to a host keyring, D-Bus or network.
    setup = """
import sys, types
sys.platform = PLATFORM
for name in ('keyring', 'keyring.backends', 'keyring.backends.SecretService',
             'keyring.backends.Windows'):
    sys.modules[name] = types.ModuleType(name)
class Backend:
    priority = 1
    def __init__(self):
        raise AssertionError('must not instantiate backend')
sys.modules['keyring.backends.SecretService'].Keyring = Backend
sys.modules['keyring.backends.Windows'].WinVaultKeyring = Backend
""".replace("PLATFORM", repr(platform))
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", setup + doctor._BACKEND_PROBE],
        cwd=tmp_path,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == (b"WARNING" if platform == "other" else b"OK")
    assert result.stderr == b""


def test_probe_spawn_error_is_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: Any, **kwargs: Any) -> Any:
        raise OSError("private host error")

    monkeypatch.setattr(subprocess, "Popen", denied)
    assert doctor.keyring_capability() == "WARNING"


@pytest.mark.parametrize("effect", ["write", "network", "spawn"])
def test_child_rejects_forbidden_effects(effect: str, tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    setup = """
import sys, types
sys.platform = 'linux'
for name in ('keyring', 'keyring.backends', 'keyring.backends.SecretService'):
    sys.modules[name] = types.ModuleType(name)
class Meta(type):
    @property
    def priority(cls):
        EFFECT
        return 1
class Backend(metaclass=Meta):
    pass
sys.modules['keyring.backends.SecretService'].Keyring = Backend
"""
    actions = {
        "write": f"open({str(marker)!r}, 'w')",
        "network": "__import__('socket').socket()",
        "spawn": "__import__('subprocess').Popen([sys.executable, '-c', 'pass'])",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            setup.replace("EFFECT", actions[effect]) + doctor._BACKEND_PROBE,
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == b"WARNING"
    assert result.stderr == b""
    assert not marker.exists()


def test_lazy_public_exports(tmp_path: Path) -> None:
    source = """
import sys
sys.path.insert(0, sys.argv[1])
import privacy_gateway as p
assert 'privacy_gateway.facade' not in sys.modules
assert set(p.__all__) <= set(dir(p))
for name in p.__all__:
    assert getattr(p, name) is getattr(p, name)
try:
    p.nonexistent
except AttributeError:
    pass
else:
    raise AssertionError('missing attribute accepted')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", source, str(ROOT / "src")],
        cwd=tmp_path,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""


def test_probe_thread_start_failure_reaps_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    stream = io.BytesIO(b"")
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: SimpleNamespace(
            stdout=stream,
            returncode=-9,
            wait=lambda **_: calls.append("reaped"),
            kill=lambda: calls.append("killed"),
        ),
    )

    def denied(self: Any) -> None:
        raise RuntimeError("cannot create reader")

    import threading

    monkeypatch.setattr(threading.Thread, "start", denied)
    assert doctor.keyring_capability() == "WARNING"
    assert calls == ["killed", "reaped"]
    assert stream.closed


def test_interrupted_probe_reaps_child(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def wait(timeout: int | None = None) -> int:
        if timeout is not None:
            raise KeyboardInterrupt
        calls.append("reaped")
        return -9

    stream = io.BytesIO(b"")
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: SimpleNamespace(
            stdout=stream,
            returncode=-9,
            wait=wait,
            kill=lambda: calls.append("killed"),
        ),
    )
    with pytest.raises(KeyboardInterrupt):
        doctor.keyring_capability()
    assert calls == ["killed", "reaped"]
    assert stream.closed
