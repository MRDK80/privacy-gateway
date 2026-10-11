"""Shared synthetic test data, CLI invocation and opt-in sandbox.

The socket ban is opt-in, not an autouse guarantee for the whole suite:
* test_cli_characterization.py and test_examples_cli_round_trip.py run child
  interpreters; an in-process monkeypatch cannot isolate those children.
* test_keystore.py, test_key_rotation.py and test_key_retention_policy.py test
  backend selection/rotation with their own mocks. A socket ban is not a
  substitute for their explicit keyring isolation (OS backends may use IPC).
* test_examples_full_round_trip.py, test_examples_blocked_secret.py and
  test_examples_untrusted_response.py retain their own scoped network guards.

No exemption grants network access. Tests using sandbox get a socket ban,
private HOME/USERPROFILE and tempfile root; keyring must still be mocked at
its lookup site. The sandbox does not claim subprocess isolation.
"""

from __future__ import annotations

import re
import socket
import sys
import tempfile
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import pytest

from privacy_gateway.crypto import generate_key

KEY_MATERIAL_RE = re.compile(r"[A-Za-z0-9_\-]{43}=")


def exit_code(exc: SystemExit) -> int:
    """Normalize the CLI's SystemExit without masking a missing exit."""
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return int(code)


def run_cli(*args: str) -> int:
    """Invoke the real entry point and require its process-exit contract."""
    from privacy_gateway.cli import main

    with patch.object(sys, "argv", ["pgw", *args]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    return exit_code(exc_info.value)


@pytest.fixture()
def fernet_key() -> bytes:
    """Fresh synthetic key; never consult the system keyring."""
    return generate_key()


def input_file(tmp_path: Path, text: str) -> Path:
    """Create a UTF-8 CLI input under the caller's temporary directory."""
    source = tmp_path / "input.txt"
    source.write_text(text, encoding="utf-8")
    return source


def _no_network(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Test sandbox must not access the network.")


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate home, temporary files and sockets for requesting tests."""
    home = tmp_path / "home"
    temp = tmp_path / "temp"
    home.mkdir()
    temp.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    return temp
