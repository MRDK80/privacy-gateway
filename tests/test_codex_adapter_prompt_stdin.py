"""Regression tests for task #190: role prompt is passed on stdin."""

from __future__ import annotations

import errno
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "codex_adapter.py"
MAX_ARG_STRLEN = 131072
OVERSIZED_PROMPT_BYTES = 200000

FAKE_CODEX = """
import json
import sys
from pathlib import Path

argv = sys.argv[1:]
payload = sys.stdin.read()
Path("stdin-record.json").write_text(
    json.dumps({"argv": argv, "stdin_bytes": len(payload.encode("utf-8"))}),
    encoding="utf-8",
)
Path(argv[argv.index("-o") + 1]).write_text("{}", encoding="utf-8")
"""


def _load_adapter() -> Any:
    spec = importlib.util.spec_from_file_location("codex_adapter_stdin", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _failing_subprocess(errno_value: int, message: str) -> types.SimpleNamespace:
    def _run(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno_value, message)

    return types.SimpleNamespace(run=_run, TimeoutExpired=subprocess.TimeoutExpired)


def test_oversized_prompt_reaches_codex_through_stdin(tmp_path: Path) -> None:
    adapter = _load_adapter()
    fake = tmp_path / "fake_codex.py"
    fake.write_text(FAKE_CODEX, encoding="utf-8")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    output = tmp_path / "output.json"
    prompt = "x" * OVERSIZED_PROMPT_BYTES

    adapter._run_codex(
        command=[sys.executable, str(fake)],
        role="controller",
        workdir=workdir,
        prompt=prompt,
        schema_path=schema,
        output_path=output,
        model=None,
        timeout=120,
    )

    record = json.loads((workdir / "stdin-record.json").read_text(encoding="utf-8"))
    argv = record["argv"]
    assert record["stdin_bytes"] == len(prompt.encode("utf-8"))
    assert record["stdin_bytes"] > MAX_ARG_STRLEN
    assert argv[-1] == "-"
    assert prompt not in argv
    assert max(len(item.encode("utf-8")) for item in argv) < MAX_ARG_STRLEN


def test_e2big_is_not_reported_as_missing_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _load_adapter()
    monkeypatch.setattr(
        adapter,
        "subprocess",
        _failing_subprocess(errno.E2BIG, "Argument list too long"),
    )

    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter._run_codex(
            command=["codex"],
            role="controller",
            workdir=tmp_path,
            prompt="x",
            schema_path=tmp_path / "schema.json",
            output_path=tmp_path / "output.json",
            model=None,
            timeout=5,
        )

    reported = repr(excinfo.value)
    assert "PROMPT_TOO_LARGE" in reported
    assert "CODEX_NOT_FOUND" not in reported


def test_missing_binary_still_reports_codex_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _load_adapter()
    monkeypatch.setattr(adapter.shutil, "which", lambda _: "/fake/bwrap")
    allowed = tmp_path / "allowed.txt"
    allowed.write_text("allowed", encoding="utf-8")
    monkeypatch.setattr(
        adapter,
        "subprocess",
        _failing_subprocess(errno.ENOENT, "No such file or directory"),
    )

    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter._run_codex(
            command=["codex"],
            role="executor",
            workdir=tmp_path,
            prompt="x",
            schema_path=tmp_path / "schema.json",
            output_path=tmp_path / "output.json",
            model=None,
            timeout=5,
            allowed_paths=(allowed,),
            scratch_path=tmp_path,
        )

    assert "CODEX_NOT_FOUND" in repr(excinfo.value)


def test_executor_disables_inner_sandbox_only_inside_bubblewrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _load_adapter()
    captured: list[list[str]] = []

    def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(adapter.shutil, "which", lambda _: "/fake/bwrap")
    monkeypatch.setattr(
        adapter,
        "subprocess",
        types.SimpleNamespace(run=_run, TimeoutExpired=subprocess.TimeoutExpired),
    )
    allowed = tmp_path / "allowed.txt"
    allowed.write_text("allowed", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    adapter._run_codex(
        command=["codex"],
        role="executor",
        workdir=tmp_path,
        prompt="x",
        schema_path=tmp_path / "schema.json",
        output_path=tmp_path / "output.json",
        model=None,
        timeout=5,
        allowed_paths=(allowed,),
        scratch_path=scratch,
    )

    assert len(captured) == 1
    argv = captured[0]
    boundary = argv.index("--")
    assert argv[0] == "/fake/bwrap"
    assert "--ro-bind" in argv[:boundary]
    assert str(allowed) in argv[:boundary]
    inner = argv[boundary + 1 :]
    assert "--dangerously-bypass-approvals-and-sandbox" in inner
    assert "--sandbox" not in inner
