"""Base installation and optional import boundaries for distribution task #111."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_base_dependencies_and_extras_match_installation_contract() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    assert project["dependencies"] == [
        "cryptography>=42",
        "keyring>=25",
        "PyYAML>=6",
    ]
    assert set(project["optional-dependencies"]) == {"dev"}
    assert config["build-system"]["requires"] == ["setuptools>=77.0.3", "wheel"]
    assert project["scripts"] == {"pgw": "privacy_gateway.bootstrap:main"}


def test_supported_python_versions_match_ci_matrix() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    prefix = "Programming Language :: Python :: "
    supported = {
        value.removeprefix(prefix)
        for value in config["project"]["classifiers"]
        if value.startswith(prefix) and "." in value.removeprefix(prefix)
    }
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    )
    matrix = workflow["jobs"]["test"]["strategy"]["matrix"]
    assert supported == set(matrix["python-version"]) == {"3.11", "3.12"}
    assert set(matrix["os"]) == {"ubuntu-latest", "windows-latest"}
    assert config["project"]["requires-python"] == ">=3.11"


def test_public_import_and_help_do_not_load_optional_tools(tmp_path: Path) -> None:
    """A fresh interpreter cannot reuse modules imported by the pytest process."""
    source = textwrap.dedent(
        """
        import importlib.abc
        import sys

        assert sys.flags.utf8_mode == 1

        forbidden = {
            "pytest", "pytest_cov", "ruff", "mypy", "pre_commit", "detect_secrets",
            "spacy", "transformers", "torch", "fastapi", "uvicorn",
            "openai", "anthropic",
        }

        class RejectOptional(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.partition(".")[0] in forbidden:
                    raise AssertionError("Optional import: " + fullname)
                return None

        sys.meta_path.insert(0, RejectOptional())
        sys.path.insert(0, sys.argv[1])

        def forbidden_effect(*args, **kwargs):
            raise AssertionError("Backend or network accessed during import/help")

        def reject_network(event, args):
            if event in {"socket.__new__", "socket.connect", "socket.bind",
                         "socket.getaddrinfo"}:
                forbidden_effect()

        sys.addaudithook(reject_network)
        import keyring
        keyring.get_keyring = forbidden_effect

        import privacy_gateway
        for name in privacy_gateway.__all__:
            assert hasattr(privacy_gateway, name)
        from privacy_gateway.cli import main
        sys.argv = ["pgw", "--help"]
        try:
            main()
        except SystemExit as result:
            assert result.code == 0
        else:
            raise AssertionError("Help did not exit")
        assert not forbidden.intersection(sys.modules)
        """
    )
    env = dict(os.environ)
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONUTF8="1")
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8", "-c", source, str(ROOT / "src")],
        cwd=tmp_path,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout.lower()
