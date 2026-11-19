"""Read-only diagnostics with closed output; ADR-113."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import io
import os
import subprocess
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import NoReturn

from privacy_gateway import __version__

_CHECKS = ("PACKAGE", "PYTHON", "DEPENDENCIES", "KEYRING", "CONFIG", "OPTIONAL")
_PROBE_SECONDS = 5
_OUTPUT_CAP = 1024

# Only installed, trusted builtin backend predicates execute in this child.
# No keyring discovery, credential operation, collection or unlock is invoked.
_BACKEND_PROBE = r"""
import contextlib
import os
import socket
import sys

sys.dont_write_bytecode = True

def guard(event, args):
    if event == "open":
        flags = args[2]
        if isinstance(flags, int) and flags & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        ):
            raise PermissionError("read-only diagnostic")
    if event in {
        "os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.link", "os.symlink",
        "os.chmod", "os.chown", "os.truncate", "os.system", "os.posix_spawn",
        "os.exec", "subprocess.Popen", "socket.bind", "socket.getaddrinfo",
    }:
        raise PermissionError("read-only diagnostic")
    if event == "socket.__new__" and args[1] != getattr(socket, "AF_UNIX", None):
        raise PermissionError("external network forbidden")
    if event in {"socket.connect", "socket.sendto"}:
        if args[0].family != getattr(socket, "AF_UNIX", None):
            raise PermissionError("external network forbidden")

sys.addaudithook(guard)

class Discard:
    def write(self, text):
        return len(text)
    def flush(self):
        pass

status = b"WARNING"
with contextlib.redirect_stdout(Discard()), contextlib.redirect_stderr(Discard()):
    try:
        if sys.platform == "linux":
            from keyring.backends.SecretService import Keyring
        elif sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring as Keyring
        else:
            raise RuntimeError("unsupported platform")
        if Keyring.priority > 0:
            status = b"OK"
    except BaseException:
        pass
os.write(1, status)
"""


def keyring_capability() -> str:
    """Bound one builtin capability check; retain at most 1025 output bytes."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-X", "utf8", "-c", _BACKEND_PROBE],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except OSError:
        return "WARNING"
    output = bytearray()
    overflow = threading.Event()
    assert process.stdout is not None
    stream = process.stdout

    def collect() -> None:
        while chunk := stream.read(128):
            available = _OUTPUT_CAP + 1 - len(output)
            output.extend(chunk[: max(0, available)])
            if len(output) > _OUTPUT_CAP:
                overflow.set()
                try:
                    process.kill()
                except OSError:
                    pass
                return

    try:
        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
    except BaseException as exc:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()
        stream.close()
        if not isinstance(exc, Exception):
            raise
        return "WARNING"
    try:
        process.wait(timeout=_PROBE_SECONDS)
    except BaseException as exc:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()
        if not isinstance(exc, (subprocess.TimeoutExpired, OSError)):
            raise
        return "WARNING"
    finally:
        reader.join(timeout=1)
        if not reader.is_alive():
            stream.close()
    if reader.is_alive() or overflow.is_set() or process.returncode != 0:
        return "WARNING"
    return "OK" if output == b"OK" else "WARNING"


def diagnose(config: Path | None) -> dict[str, str]:
    """Report only constant statuses, never exception text or source values."""
    statuses = dict.fromkeys(_CHECKS, "OK")
    try:
        statuses["PACKAGE"] = (
            "OK"
            if importlib.metadata.version("privacy-gateway") == __version__
            else "FAILURE"
        )
    except Exception:
        statuses["PACKAGE"] = "FAILURE"
    statuses["PYTHON"] = (
        "OK" if sys.version_info[:2] in {(3, 11), (3, 12)} else "WARNING"
    )
    available: set[str] = set()
    for name in ("cryptography", "keyring", "yaml"):
        try:
            importlib.import_module(name)
        except Exception:
            statuses["DEPENDENCIES"] = "FAILURE"
        else:
            available.add(name)
    statuses["KEYRING"] = (
        keyring_capability() if "keyring" in available else "NOT_AVAILABLE"
    )
    statuses["CONFIG"] = "NOT_REQUESTED"
    if config is not None:
        statuses["CONFIG"] = "NOT_AVAILABLE"
        if "yaml" in available:
            try:
                from privacy_gateway.detector import load_config

                load_config(config)
            except Exception:
                statuses["CONFIG"] = "FAILURE"
            else:
                statuses["CONFIG"] = "OK"
    statuses["OPTIONAL"] = "NOT_AVAILABLE"
    statuses["SUMMARY"] = (
        "FAILURE"
        if "FAILURE" in statuses.values()
        else "WARNING"
        if "WARNING" in statuses.values()
        else "OK"
    )
    return statuses


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(3, "Invalid doctor arguments.\n")


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--entities-config",
        metavar="ENTITIES_CONFIG",
        type=Path,
        default=None,
        help="Проверить выбранную конфигурацию детектора.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="pgw doctor", allow_abbrev=False)
    configure_parser(parser)
    args = parser.parse_args(argv)
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        # Dependency/backend/config code must not export arbitrary diagnostics.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            statuses = diagnose(args.entities_config)
    except Exception:
        print("Doctor failed.", file=sys.stderr)
        return 1
    finally:
        sys.dont_write_bytecode = old
    for check, status in statuses.items():
        print(f"{check}: {status}")
    return 3 if statuses["SUMMARY"] == "FAILURE" else 0
