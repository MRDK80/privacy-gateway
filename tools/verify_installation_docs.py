"""Run the documented source-install commands in disposable native environments."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

LABELS = frozenset(
    f"{prefix}{kind}-{platform}"
    for prefix in ("", "maintain-")
    for kind in ("cli", "library")
    for platform in ("linux", "windows")
)
SOURCE = re.compile(
    r"git\+https://github\.com/MRDK80/privacy-gateway\.git@[0-9a-f]{40}(?![0-9a-f])"
)
BLOCK = re.compile(
    r"<!-- pgw-install-smoke: ([a-z-]+) -->\n```(bash|powershell)\n(.*?)\n```",
    re.DOTALL,
)


class InstallationDocsError(RuntimeError):
    """A documented installation did not complete successfully."""


def extract_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for label, language, body in BLOCK.findall(text):
        if label in blocks or label not in LABELS:
            raise InstallationDocsError("duplicate or unknown installation block")
        expected = "powershell" if label.endswith("windows") else "bash"
        if language != expected or not body.strip():
            raise InstallationDocsError("invalid installation block language/body")
        blocks[label] = body
    if set(blocks) != LABELS:
        raise InstallationDocsError("missing installation blocks")
    sources = [
        SOURCE.findall(blocks[f"{kind}-{platform}"])
        for kind in ("cli", "library")
        for platform in ("linux", "windows")
    ]
    if any(len(values) != 1 for values in sources):
        raise InstallationDocsError("each installation requires one immutable source")
    if len({values[0] for values in sources}) != 1:
        raise InstallationDocsError("installation source SHAs differ")
    return blocks


def isolated_environment(root: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PIP_", "PIPX_", "PYTHON", "GIT_"))
    }
    directories = {
        "HOME": "home",
        "USERPROFILE": "home",
        "APPDATA": "appdata",
        "LOCALAPPDATA": "localappdata",
        "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache",
        "XDG_DATA_HOME": "data",
        "PIPX_HOME": "pipx",
        "PIPX_BIN_DIR": "bin",
        "PIPX_MAN_DIR": "man",
        "PIPX_SHARED_LIBS": "shared",
        "PIPX_LOG_DIR": "logs",
    }
    for key, relative in directories.items():
        directory = root / relative
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    env.update(
        PGW_PYTHON=sys.executable,
        PIP_CONFIG_FILE=os.devnull,
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        PIP_NO_INPUT="1",
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        PYTHONNOUSERSITE="1",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        PIPX_DEFAULT_PYTHON=sys.executable,
    )
    return env


def stop_tree(process: subprocess.Popen[str], windows: bool) -> str:
    """Best-effort cleanup of the original group; never claim Windows proof."""
    outcome = "WINDOWS_TREE_UNVERIFIED" if windows else "POSIX_GROUP_SIGNALED"
    if windows:
        if process.poll() is None:
            # If the leader already exited, its old PID must not be signalled.
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        # A descendant may retain the pipe after the session leader exited.
        kill_group = getattr(os, "killpg", None)
        kill_signal = getattr(signal, "SIGKILL", None)
        if not callable(kill_group) or not isinstance(kill_signal, int):
            raise InstallationDocsError("native process-group cleanup unavailable")
        try:
            kill_group(process.pid, kill_signal)
        except ProcessLookupError:
            outcome = "POSIX_GROUP_NOT_FOUND"
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    process.wait()
    return outcome


def run_block(script: str, directory: Path, windows: bool) -> None:
    directory.mkdir(parents=True)
    suffix = ".ps1" if windows else ".sh"
    path = directory / ("installation" + suffix)
    path.write_text(script, encoding="utf-8")
    executable = shutil.which("pwsh" if windows else "bash")
    if executable is None:
        raise InstallationDocsError("native shell unavailable")
    argv = (
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path)]
        if windows
        else [executable, "-e", str(path)]
    )
    process = subprocess.Popen(
        argv,
        cwd=directory,
        env=isolated_environment(directory),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=not windows,
    )
    try:
        output, _ = process.communicate(timeout=600)
    except BaseException as exc:
        outcome = "CLEANUP_UNVERIFIED"
        try:
            outcome = stop_tree(process, windows)
        finally:
            (directory / "cleanup-status.txt").write_text(outcome, encoding="utf-8")
        if isinstance(exc, subprocess.TimeoutExpired):
            raise InstallationDocsError(
                "installer wait expired; inspect cleanup-status; no automatic retry"
            ) from None
        raise
    (directory / "transcript.log").write_text(output, encoding="utf-8")
    if process.returncode:
        raise InstallationDocsError(
            "documented installer command failed; see transcript"
        )


def verify(document: Path, root: Path) -> None:
    blocks = extract_blocks(document.read_text(encoding="utf-8"))
    platform = "windows" if os.name == "nt" else "linux"
    if platform == "linux" and not sys.platform.startswith("linux"):
        raise InstallationDocsError("native installation proof supports Linux/Windows")
    for kind in ("cli", "library"):
        script = (
            blocks[f"{kind}-{platform}"] + "\n" + blocks[f"maintain-{kind}-{platform}"]
        )
        run_block(script, root / kind, platform == "windows")
        print(
            f"documented {kind}/{platform}: install, checks, reinstall, uninstall PASS"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    args = parser.parse_args()
    verify(args.document, args.temp_root)


if __name__ == "__main__":
    main()
