"""Consumer demo planning and safe script generation tests (#255)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tools import agent_consumer_demo as demo


def _plan() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "repository": "OWNER/repository",
        "epic": 248,
        "task": 255,
        "baseline_ref": "main",
        "baseline_sha": "a" * 40,
        "roadmap_ref": "roadmap/248-autonomous-epic-runner",
        "roadmap_sha": "b" * 40,
        "consumer_visible": True,
        "not_applicable_reason": None,
        "scenario": "compare the same local document",
        "parameters": [
            {"name": "mode", "prompt": "Mode", "secret": False},
        ],
        "old_argv": [
            "{python}",
            "show.py",
            "--input",
            "{input}",
            "--mode",
            "{param:mode}",
        ],
        "new_argv": [
            "{python}",
            "show.py",
            "--input",
            "{input}",
            "--mode",
            "{param:mode}",
        ],
    }


def test_consumer_visible_plan_generates_single_heredoc_command() -> None:
    result = demo.assess(_plan())
    script = demo.generate_command(_plan())

    assert result.status == "CONSUMER_DEMO_READY"
    assert result.baseline_sha == "a" * 40
    assert result.roadmap_sha == "b" * 40
    assert script.startswith("python - << 'PYEOF'\n")
    assert script.endswith("\nPYEOF")
    assert script.index("СТАРЫЙ СПОСОБ") < script.index("НОВЫЙ СПОСОБ")
    assert 'terminal.readline()' in script
    assert '"/dev/tty"' in script
    assert '"secret": false' in script
    assert "a" * 40 in script
    assert "b" * 40 in script


def test_non_consumer_visible_change_is_explicitly_not_applicable() -> None:
    plan = _plan() | {
        "consumer_visible": False,
        "not_applicable_reason": "documentation only; runtime behavior unchanged",
        "scenario": None,
        "parameters": [],
        "old_argv": [],
        "new_argv": [],
    }

    result = demo.assess(plan)

    assert result.status == "DEMO_NOT_APPLICABLE"
    assert result.reason == "documentation only; runtime behavior unchanged"
    with pytest.raises(demo.DemoError, match="DEMO_NOT_APPLICABLE"):
        demo.generate_command(plan)


@pytest.mark.parametrize(
    "change",
    [
        {"unexpected": "authority expansion"},
        {"baseline_sha": "main"},
        {"roadmap_ref": "main"},
        {"old_argv": ["cat", "{input}"]},
        {"new_argv": ["{python}", "show.py", "{param:missing}"]},
        {"parameters": [{"name": "input", "prompt": "bad", "secret": False}]},
        {"not_applicable_reason": "not allowed for visible changes"},
    ],
)
def test_invalid_or_unsafe_plan_fails_closed(change: dict[str, object]) -> None:
    plan = _plan()
    plan.update(change)

    with pytest.raises(demo.DemoError, match="DEMO_PLAN_INVALID"):
        demo.assess(plan)


def test_secret_parameter_uses_local_hidden_prompt() -> None:
    plan = _plan()
    plan["parameters"] = [
        {"name": "token", "prompt": "Local token", "secret": True},
    ]
    plan["old_argv"] = ["{python}", "show.py", "{input}", "{param:token}"]
    plan["new_argv"] = ["{python}", "show.py", "{input}", "{param:token}"]

    script = demo.generate_command(plan)

    assert "getpass.getpass(" in script
    assert "Local token" in script


def _commit(repository: Path, message: str) -> str:
    subprocess.run(["git", "add", "show.py"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Synthetic Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            message,
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_generated_command_runs_in_isolated_checkouts_on_same_synthetic_input(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    program = repository / "show.py"
    program.write_text(
        "import pathlib,sys\nprint('old:' + pathlib.Path(sys.argv[2]).read_text())\n",
        encoding="utf-8",
    )
    baseline = _commit(repository, "baseline")
    program.write_text(
        "import pathlib,sys\nprint('new:' + pathlib.Path(sys.argv[2]).read_text())\n",
        encoding="utf-8",
    )
    roadmap = _commit(repository, "roadmap")
    subprocess.run(
        ["git", "branch", "roadmap/248-autonomous-epic-runner", roadmap],
        cwd=repository,
        check=True,
    )
    input_path = tmp_path / "private-input.txt"
    input_path.write_text("synthetic-value", encoding="utf-8")
    plan = _plan() | {
        "baseline_sha": baseline,
        "roadmap_sha": roadmap,
        "parameters": [],
        "old_argv": ["{python}", "show.py", "--input", "{input}"],
        "new_argv": ["{python}", "show.py", "--input", "{input}"],
    }
    script = demo.generate_command(plan)
    command_body = script.removeprefix("python - << 'PYEOF'\n").removesuffix(
        "\nPYEOF"
    )
    answers = f"{repository}\n{input_path}\n"
    wrapper = (
        "import builtins\n"
        "real_open = builtins.open\n"
        f"answers = iter({answers!r}.splitlines(keepends=True))\n"
        "class DemoTerminal:\n"
        "    def write(self, value): return len(value)\n"
        "    def flush(self): pass\n"
        "    def readline(self): return next(answers, '')\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *args): return False\n"
        "terminal = DemoTerminal()\n"
        "def demo_open(path, *args, **kwargs):\n"
        "    if str(path) in {'/dev/tty', 'CONIN$'}:\n"
        "        return terminal\n"
        "    return real_open(path, *args, **kwargs)\n"
        "builtins.open = demo_open\n"
        + command_body
    )
    completed = subprocess.run(
        [sys.executable, "-c", wrapper],
        check=True,
        capture_output=True,
        text=True,
    )
    stdout = completed.stdout

    assert "СТАРЫЙ СПОСОБ\nold:synthetic-value" in stdout
    assert "НОВЫЙ СПОСОБ\nnew:synthetic-value" in stdout
    assert stdout.count("synthetic-value") == 2
    assert list(tmp_path.glob("pgw-demo-*")) == []


def test_cli_assess_emits_only_bounded_metadata(tmp_path: Path, capsys: object) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan()), encoding="utf-8")

    exit_code = demo.main(["assess", str(plan_path)])
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]

    assert exit_code == 0
    assert output == {
        "baseline_sha": "a" * 40,
        "roadmap_sha": "b" * 40,
        "reason": None,
        "status": "CONSUMER_DEMO_READY",
    }
