"""Regression checks for clean artifact installation gate (#112)."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from tools.verify_package_build import (
    INSTALLED_SMOKE,
    BuildGateError,
    clean_python_environment,
    validate_artifact_paths,
    verify_artifact_contents,
)


@pytest.mark.parametrize(
    "name",
    [
        ".env", "pkg/.env.local", ".agent-private/ledger.json",
        "pkg/__pycache__/x.pyc", "pkg/.venv/python", "../outside",
        "/outside", "C:/outside", "pkg\\outside", "pkg/key.pem",
        "pkg/.git/config", "pkg/.pytest_cache/cache",
    ],
)
def test_artifact_paths_reject_private_or_unsafe_members(name: str) -> None:
    with pytest.raises(BuildGateError, match="forbidden artifact member"):
        validate_artifact_paths({name})


def test_artifact_paths_accept_product_sources_and_typing_marker() -> None:
    validate_artifact_paths({
        "privacy_gateway/__init__.py",
        "privacy_gateway/py.typed",
        "privacy_gateway-0.5.0.dist-info/licenses/LICENSE",
    })


def make_artifacts(tmp_path: Path, missing: str = "") -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    package = source / "src/privacy_gateway"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.5.0"\n')
    (package / "py.typed").touch()
    (package / "facade.py").write_text("# synthetic module\n")
    sdist = tmp_path / "pkg.tar.gz"
    wheel = tmp_path / "pkg.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in ("__init__.py", "facade.py", "py.typed"):
            if missing != "wheel/" + name:
                archive.writestr("privacy_gateway/" + name, "")
    with tarfile.open(sdist, "w:gz") as archive:
        for name in (
            "src/privacy_gateway/__init__.py",
            "src/privacy_gateway/facade.py",
            "src/privacy_gateway/py.typed",
            "pyproject.toml", "README.md", "LICENSE",
        ):
            if missing == "sdist/" + name:
                continue
            content = b"synthetic"
            member = tarfile.TarInfo("pkg-0.5.0/" + name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return source, sdist, wheel


def test_artifacts_contain_required_sources_and_metadata(tmp_path: Path) -> None:
    verify_artifact_contents(*make_artifacts(tmp_path))


@pytest.mark.parametrize(
    "missing",
    [
        "wheel/py.typed", "wheel/facade.py", "sdist/src/privacy_gateway/py.typed",
        "sdist/src/privacy_gateway/facade.py", "sdist/pyproject.toml",
    ],
)
def test_artifacts_reject_missing_members(tmp_path: Path, missing: str) -> None:
    with pytest.raises(BuildGateError, match="artifact omits|sdist omits"):
        verify_artifact_contents(*make_artifacts(tmp_path, missing))


def test_sdist_rejects_symlink_before_installation(tmp_path: Path) -> None:
    source, sdist, wheel = make_artifacts(tmp_path)
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo("pkg-0.5.0/leak")
        member.type = tarfile.SYMTYPE
        member.linkname = "/outside"
        archive.addfile(member)
    with pytest.raises(BuildGateError, match="link or special file"):
        verify_artifact_contents(source, sdist, wheel)


def test_installed_smoke_payload_compiles() -> None:
    compile(INSTALLED_SMOKE, "<installed-smoke>", "exec", dont_inherit=True)


@pytest.mark.parametrize("prefix", ["../", ".agent-private/", "/outside/"])
def test_sdist_rejects_unsafe_root(tmp_path: Path, prefix: str) -> None:
    source, sdist, wheel = make_artifacts(tmp_path)
    with tarfile.open(sdist, "r:gz") as archive:
        original = [(member.name.split("/", 1)[1], archive.extractfile(member))
                    for member in archive if member.isfile()]
        data = [(name, stream.read()) for name, stream in original if stream]
    with tarfile.open(sdist, "w:gz") as archive:
        for name, body in data:
            member = tarfile.TarInfo(prefix + name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    with pytest.raises(BuildGateError, match="forbidden artifact member"):
        verify_artifact_contents(source, sdist, wheel)


def test_clean_environment_drops_python_import_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/synthetic/checkout")
    monkeypatch.setenv("PYTHONHOME", "/synthetic/python")
    monkeypatch.setenv("PYTHONUSERBASE", "/synthetic/user")
    monkeypatch.setenv("PGW_SYNTHETIC", "preserved")
    env = clean_python_environment()
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert "PYTHONUSERBASE" not in env
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PYTHONUTF8"] == "1"
    assert env["PGW_SYNTHETIC"] == "preserved"
