"""Public English distribution metadata and contribution-path checks."""

from __future__ import annotations

import io
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest
from tools.verify_package_build import BuildGateError, verify_public_metadata

ROOT = Path(__file__).resolve().parents[1]


def test_package_language_metadata_preserves_identity_and_support() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert project["name"] == "privacy-gateway"
    assert project["requires-python"] == ">=3.11"
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["dynamic"] == ["version"]
    assert project["readme"] == "README.md"
    assert not re.search("[А-Яа-яЁё]", project["description"])
    assert "pseudonymization" in project["keywords"]
    assert "anonymization" not in project["keywords"]
    assert set(project["urls"]) == {
        "Repository", "Documentation", "Issues", "Changelog"
    }
    assert all(
        url.startswith("https://github.com/MRDK80/privacy-gateway")
        for url in project["urls"].values()
    )


def test_contribution_templates_preserve_gates_and_private_reporting_boundary() -> None:
    contribution = (ROOT / "CONTRIBUTING.md").read_text("utf-8")
    for command in (
        "pytest -q", "ruff check .", "mypy .", "pre-commit run --all-files"
    ):
        assert command in contribution
    assert "Final roadmap→main merge needs separate owner approval" in contribution
    assert "Russian questions/reports are welcome" in contribution
    for relative in (
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/bug_report.md",
        ".github/ISSUE_TEMPLATE/feature_request.md",
    ):
        text = (ROOT / relative).read_text("utf-8")
        assert not re.search("[А-Яа-яЁё]", text)
        assert "Russian" in text and "English" in text
        assert "sensitive" in text or "secrets" in text
    bug = (ROOT / ".github/ISSUE_TEMPLATE/bug_report.md").read_text("utf-8")
    assert "vulnerability details publicly" in bug
    assert "SECURITY.md" in bug


def _metadata_archives(tmp_path: Path, mutation: str) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    project = """[project]
name = "privacy-gateway"
description = "Local pseudonymization."
requires-python = ">=3.11"
readme = "README.md"
keywords = ["privacy", "pseudonymization"]
classifiers = ["Programming Language :: Python :: 3.11"]
[project.urls]
Repository = "https://github.com/MRDK80/privacy-gateway"
"""
    (source / "pyproject.toml").write_text(project, "utf-8")
    readme = "# Privacy Gateway\n\n[Русский](README.ru.md)\n"
    (source / "README.md").write_text(readme, "utf-8")
    metadata = (
        "Metadata-Version: 2.4\nName: privacy-gateway\nVersion: 0.5.0\n"
        "Summary: Local pseudonymization.\nRequires-Python: >=3.11\n"
        "Description-Content-Type: text/markdown\n"
        "Keywords: privacy,pseudonymization\n"
        "Classifier: Programming Language :: Python :: 3.11\n"
        "Project-URL: Repository, https://github.com/MRDK80/privacy-gateway\n\n"
        + readme
    )
    edits = {
        "summary": ("Local pseudonymization.\n", "Wrong summary.\n"),
        "python": ("Requires-Python: >=3.11", "Requires-Python: >=3.13"),
        "content_type": ("text/markdown", "text/plain"),
        "keywords": ("Keywords: privacy,pseudonymization", "Keywords: anonymization"),
        "url": ("Project-URL: Repository, https://github.com/", "Project-URL: X, https://github.com/"),
        "classifier": (
            "Classifier: Programming Language :: Python :: 3.11",
            "Classifier: Unsupported",
        ),
        "readme": ("# Privacy Gateway", "# Wrong long description"),
    }
    if mutation:
        before, after = edits[mutation]
        metadata = metadata.replace(before, after)
    data = metadata.encode("utf-8")
    wheel = tmp_path / "synthetic.whl"
    sdist = tmp_path / "synthetic.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("privacy_gateway-0.5.0.dist-info/METADATA", data)
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo("privacy_gateway-0.5.0/PKG-INFO")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    return source, sdist, wheel


def test_wheel_and_sdist_public_metadata_match_source(tmp_path: Path) -> None:
    verify_public_metadata(*_metadata_archives(tmp_path, ""))


@pytest.mark.parametrize(
    "mutation",
    ["summary", "python", "content_type", "keywords", "url", "classifier", "readme"],
)
def test_public_metadata_tampering_blocks_build(tmp_path: Path, mutation: str) -> None:
    with pytest.raises(BuildGateError, match="public metadata"):
        verify_public_metadata(*_metadata_archives(tmp_path, mutation))


def test_contributing_linux_bootstrap_keeps_real_interpreter_command() -> None:
    text = (ROOT / "CONTRIBUTING.md").read_text("utf-8")
    blocks = re.findall(r"```bash\n(.*?)\n```", text, re.DOTALL)
    linux = next(block for block in blocks if "source .venv/bin/activate" in block)
    assert linux.splitlines()[0] == "python3.11 -m venv .venv"
    assert "python 3.11" not in text
