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


def _comparison_endpoint(endpoint: str) -> bool:
    match = re.fullmatch(
        r"repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/compare/"
        r"([A-Za-z0-9_.%-]+)\.\.\.([A-Za-z0-9_.%-]+)",
        endpoint,
    )
    if match is None or any(".." in part for part in match.groups()[:2]):
        return False
    for encoded in match.groups()[2:]:
        if len(encoded) > 765:
            return False
        parts = encoded.split("%2F")
        reference = "/".join(parts)
        if (
            not 1 <= len(reference) <= 255
            or reference.startswith("-")
            or any(
                re.fullmatch(r"[A-Za-z0-9_.-]+", part) is None
                or part.startswith(".")
                or part.endswith((".", ".lock"))
                or ".." in part
                for part in parts
            )
        ):
            return False
    return True


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
    if (
        len(args) == 3
        and re.fullmatch(r"repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args[2]) is not None
        and ".." not in args[2]
    ):
        return True
    endpoints = [arg for arg in args[2:] if not arg.startswith("-")]
    if len(endpoints) == 1 and re.match(
        r"repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/compare(?:/|$)", endpoints[0]
    ):
        return len(args) == 3 and _comparison_endpoint(endpoints[0])
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


def attempt(
    argv: Sequence[str],
    *,
    run: Callable[..., Any],
    timeout: float = 120,
    on_complete: Callable[[], None] | None = None,
) -> str:
    """One eligible complete command; classification never includes validation."""
    if not read_only(argv) or not 0 < timeout <= 120:
        raise ReadFailure
    try:
        try:
            result = run(
                list(argv), capture_output=True, text=True, check=False, timeout=timeout
            )
        finally:
            if on_complete is not None:
                on_complete()
    except subprocess.TimeoutExpired:
        raise ReadFailure("timeout") from None
    except OSError as error:
        category = (
            "network"
            if error.errno in {errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH}
            else "unknown"
        )
        raise ReadFailure(category) from None
    if result.returncode != 0:
        raise ReadFailure(_category(result.stderr))
    if not isinstance(result.stdout, str) or len(result.stdout) > 1_000_000:
        raise ReadFailure
    return result.stdout


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
        for index in range(len(DELAYS) + 1):
            completed_at = 0.0

            def complete() -> None:
                nonlocal completed_at
                completed_at = self.monotonic()

            try:
                return attempt(argv, run=self.run, on_complete=complete)
            except ReadFailure as error:
                category = error.category
            if category == "unknown" or index == len(DELAYS):
                raise ReadFailure(category) from None
            deadline = completed_at + DELAYS[index]
            while True:
                self.guard()
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    break
                self.sleep(min(1.0, remaining))
        raise AssertionError("unreachable")
