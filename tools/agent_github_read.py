"""Bounded retries of positively identified transient GitHub read failures."""

from __future__ import annotations

import errno
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Any

DELAYS = (60, 60, 60, 60, 60, 180, 300, 600, 900, 1200)


class ReadFailure(Exception):
    """Closed diagnostic category; never retain command/output/exception text."""

    def __init__(self, category: str = "unknown") -> None:
        self.category = category if category in {"timeout", "network"} else "unknown"
        super().__init__(self.category)


def read_only(argv: Sequence[str]) -> bool:
    """Accept only the concrete argument grammar used by the task read clients."""
    args = tuple(argv)
    if len(args) < 3 or args[0] != "gh":
        return False
    if args[1] in {"issue", "pr"}:
        verbs = {"view"} if args[1] == "issue" else {"view", "checks"}
        return (
            len(args) == 8
            and args[2] in verbs
            and args[3].isascii()
            and args[3].isdigit()
            and int(args[3]) > 0
            and args[4] == "--repo"
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args[5]) is not None
            and args[6] == "--json"
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9,]*", args[7]) is not None
        )
    if args[1] != "api":
        return False
    endpoints = [arg for arg in args[2:] if not arg.startswith("-")]
    return (
        len(endpoints) == 1
        and re.fullmatch(
            r"repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_./?=&:%-]+",
            endpoints[0],
        )
        is not None
        and ".." not in endpoints[0]
        and len(set(args[2:])) == len(args[2:])
        and all(
            arg == endpoints[0] or arg in {"--paginate", "--slurp"} for arg in args[2:]
        )
    )


def _category(stderr: object) -> str:
    if not isinstance(stderr, str) or len(stderr) > 1_000_000:
        return "unknown"
    lowered = stderr.lower()
    if any(
        marker in lowered
        for marker in (
            "http ",
            "status code",
            "permission",
            "forbidden",
            "unauthorized",
            "authentication",
            "credential",
            "token",
            "stale",
            "policy",
            "identity",
        )
    ):
        return "unknown"
    # Go transport diagnostics from gh; generic connectivity prose is not proof.
    if re.fullmatch(
        r'^(?:Get|Post) "[^"\n]+": net/http: TLS handshake timeout$', stderr.strip()
    ):
        return "timeout"
    if re.fullmatch(
        r'^(?:Get|Post) "[^"\n]+": (?:dial tcp|read tcp|write tcp) [^\n]+: '
        r"(?:i/o timeout|connect: connection refused|connect: network is unreachable|"
        r"connect: no route to host|read: connection reset by peer|"
        r"write: broken pipe)$",
        stderr.strip(),
    ):
        return "timeout" if "i/o timeout" in stderr else "network"
    return "unknown"


class Reader:
    def __init__(
        self,
        *,
        run: Callable[..., Any] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        guard: Callable[[], None] | None = None,
    ) -> None:
        self.run = run or subprocess.run
        self.monotonic = monotonic
        self.sleep = sleep
        self.guard = guard or (lambda: None)

    def command(self, argv: Sequence[str]) -> str:
        if not read_only(argv):
            raise ReadFailure
        for attempt in range(len(DELAYS) + 1):
            category = "unknown"
            try:
                result = self.run(
                    list(argv), capture_output=True, text=True, check=False, timeout=120
                )
            except subprocess.TimeoutExpired:
                completed_at = self.monotonic()
                category = "timeout"
            except OSError as error:
                completed_at = self.monotonic()
                if error.errno in {
                    errno.ENETDOWN,
                    errno.ENETUNREACH,
                    errno.EHOSTUNREACH,
                }:
                    category = "network"
            else:
                completed_at = self.monotonic()
                if result.returncode == 0:
                    if (
                        not isinstance(result.stdout, str)
                        or len(result.stdout) > 1_000_000
                    ):
                        raise ReadFailure
                    return result.stdout
                category = _category(result.stderr)
            if category == "unknown" or attempt == len(DELAYS):
                raise ReadFailure(category) from None
            deadline = completed_at + DELAYS[attempt]
            while True:
                self.guard()
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    break
                self.sleep(min(1.0, remaining))
        raise AssertionError("unreachable")
