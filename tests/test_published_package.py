"""Post-publication checks never replace approved bytes with an index fallback."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tools import release_evidence as release
from tools import verify_published_package as published

VERSION = "0.6.0"
SOURCE = "1" * 40


def seal(bundle: Path, manifest: dict[str, Any]) -> str:
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    names = ["manifest.json"] + [
        "dist/" + item["name"] for item in manifest["artifacts"]
    ]
    sums = "".join(
        hashlib.sha256((bundle / name).read_bytes()).hexdigest() + "  " + name + "\n"
        for name in sorted(names)
    ).encode()
    (bundle / "SHA256SUMS").write_bytes(sums)
    return hashlib.sha256(sums).hexdigest()


@pytest.fixture
def approved(tmp_path: Path) -> tuple[Path, dict[str, Any], str, dict[str, bytes]]:
    bundle = tmp_path / "bundle"
    (bundle / "dist").mkdir(parents=True)
    data = {
        f"privacy_gateway-{VERSION}-py3-none-any.whl": b"synthetic wheel",
        f"privacy_gateway-{VERSION}.tar.gz": b"synthetic sdist",
    }
    records = []
    for name, body in data.items():
        (bundle / "dist" / name).write_bytes(body)
        records.append(
            {
                "name": name,
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    manifest = {
        "schema_version": 1,
        "repository": release.REPOSITORY,
        "policy_sha": "2" * 40,
        "source_sha": SOURCE,
        "version": VERSION,
        "tag": "v" + VERSION,
        "mode": "publish",
        "run_id": "123",
        "run_attempt": "1",
        "verify_job_id": 456,
        "artifacts": records,
    }
    return bundle, manifest, seal(bundle, manifest), data


def fake_fetch(manifest: dict[str, Any], data: dict[str, bytes]) -> published.Fetch:
    urls = [
        {
            "filename": item["name"],
            "size": item["size"],
            "digests": {"sha256": item["sha256"]},
            "yanked": False,
            "packagetype": "bdist_wheel" if item["name"].endswith(".whl") else "sdist",
            "url": "https://files.pythonhosted.org/packages/synthetic/" + item["name"],
        }
        for item in manifest["artifacts"]
    ]
    metadata = json.dumps(
        {"info": {"name": "privacy-gateway", "version": VERSION}, "urls": urls}
    ).encode()

    def fetch(url: str, limit: int) -> bytes:
        if url == f"https://pypi.org/pypi/privacy-gateway/{VERSION}/json":
            assert limit == 2 * 1024 * 1024
            return metadata
        name = url.rsplit("/", 1)[-1]
        assert limit == len(data[name])
        return data[name]

    return fetch


def test_independent_digest_is_required(approved: tuple[Any, ...]) -> None:
    bundle, _, digest, _ = approved
    assert (
        published.trusted_bundle(bundle, VERSION, SOURCE, digest)["version"] == VERSION
    )
    with pytest.raises(published.SmokeError):
        published.trusted_bundle(bundle, VERSION, SOURCE, "0" * 64)
    with pytest.raises(published.SmokeError):
        published.trusted_bundle(bundle, VERSION, "3" * 40, digest)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "dry-run"),
        ("tag", "v0.7.0"),
        ("repository", "other/project"),
        ("schema_version", True),
        ("verify_job_id", 0),
        ("run_id", "01"),
        ("policy_sha", "main"),
    ],
)
def test_invalid_manifest_does_not_become_authority(
    field: str,
    value: Any,
    approved: tuple[Any, ...],
) -> None:
    bundle, manifest, _, _ = approved
    manifest[field] = value
    digest = seal(bundle, manifest)
    with pytest.raises(published.SmokeError):
        published.trusted_bundle(bundle, VERSION, SOURCE, digest)


def test_bundle_tampering_is_rejected(approved: tuple[Any, ...]) -> None:
    bundle, _, digest, data = approved
    (bundle / "dist" / next(iter(data))).write_bytes(b"synthetic changed")
    with pytest.raises(published.SmokeError):
        published.trusted_bundle(bundle, VERSION, SOURCE, digest)


@pytest.mark.parametrize(
    "change", ["digest", "version", "missing", "yanked", "url", "type"]
)
def test_index_metadata_must_match_approved_release(
    change: str, approved: tuple[Any, ...]
) -> None:
    _, manifest, _, data = approved
    original = fake_fetch(manifest, data)
    body = json.loads(
        original(
            f"https://pypi.org/pypi/privacy-gateway/{VERSION}/json", 2 * 1024 * 1024
        )
    )
    if change == "digest":
        body["urls"][0]["digests"]["sha256"] = "0" * 64
    elif change == "version":
        body["info"]["version"] = "0.7.0"
    elif change == "missing":
        body["urls"].pop()
    elif change == "yanked":
        body["urls"][0]["yanked"] = True
    elif change == "url":
        body["urls"][0]["url"] = "https://example.invalid/package.whl"
    else:
        body["urls"][0]["packagetype"] = "unknown"
    with pytest.raises(published.SmokeError):
        published.published_files(manifest, lambda *a: json.dumps(body).encode())


def test_404_never_starts_an_installer(
    approved: tuple[Any, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _, digest, _ = approved

    def unavailable(url: str, limit: int) -> bytes:
        raise urllib.error.HTTPError(
            url, 404, "synthetic absent project", Message(), None
        )

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "installer must not start before metadata and bytes validation"
        )

    monkeypatch.setattr(published, "installed_checks", forbidden)
    root = tmp_path / "attempt"
    with pytest.raises(urllib.error.HTTPError):
        published.check(bundle, VERSION, SOURCE, digest, root, unavailable)
    assert json.loads((root / "blocked.json").read_text())["successful_smoke"] is False
    assert not (root / "wheel-venv").exists()


def test_bad_download_never_starts_an_installer(
    approved: tuple[Any, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, manifest, digest, data = approved
    original = fake_fetch(manifest, data)

    def corrupt(url: str, limit: int) -> bytes:
        value = original(url, limit)
        return value if url.endswith("/json") else value + b"changed"

    monkeypatch.setattr(
        published, "installed_checks", lambda *a: pytest.fail("unexpected installer")
    )
    with pytest.raises(published.SmokeError):
        published.check(bundle, VERSION, SOURCE, digest, tmp_path / "attempt", corrupt)


def test_injected_provider_is_explicitly_a_simulation(
    approved: tuple[Any, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, manifest, digest, data = approved
    monkeypatch.setattr(published, "installed_checks", lambda *a: {"fixture": True})
    report = published.check(
        bundle,
        VERSION,
        SOURCE,
        digest,
        tmp_path / "attempt",
        fake_fetch(manifest, data),
    )
    assert report["status"] == "LOCAL_SIMULATION_PASS"
    assert report["provider_fetch"] == "LOCAL_SIMULATION"
    assert len(report["checks"]) == 2


def test_pip_uses_only_the_checked_local_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / f"privacy_gateway-{VERSION}-py3-none-any.whl"
    artifact.write_bytes(b"synthetic")
    venv = tmp_path / "wheel-venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").touch()
    calls: list[list[str]] = []

    def run(label: str, argv: list[str], cwd: Path, timeout: int) -> str:
        calls.append(argv)
        if label.endswith("-synthetic"):
            return json.dumps(
                {
                    "module_file": str(venv / "lib/privacy_gateway/__init__.py"),
                    "version": VERSION,
                    "metadata": VERSION,
                    "roundtrip": True,
                    "console_doctor": True,
                }
            )
        if label.endswith("-describe"):
            return json.dumps({"commands": [{"id": "doctor"}]})
        return ""

    runner: Any = SimpleNamespace(run=run)
    published.installed_checks(artifact, "wheel", tmp_path, runner, VERSION)
    command = next(args for args in calls if "install" in args)
    assert command[-1] == str(artifact)
    assert "--isolated" in command and "--no-cache-dir" in command
    assert command[command.index("--index-url") + 1] == "https://pypi.org/simple"
    assert not any("privacy-gateway==" in arg for arg in command)


def test_global_pip_config_is_disabled(tmp_path: Path) -> None:
    runner = published.Runner(tmp_path)
    config = tmp_path / "synthetic-global.ini"
    config.write_text(
        "[global]\nextra-index-url=https://example.invalid/simple\nfind-links=synthetic\n"
    )
    script = """import sys
from pip._internal import configuration as c
config_kinds=tuple(c.get_configuration_files())
c.get_configuration_files=lambda:{kind:[sys.argv[1]] for kind in config_kinds}
config=c.Configuration(isolated=True)
config.load()
assert not list(config.items()), 'global or extra-index configuration leaked'
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(config)],
        env=runner.env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert runner.env["PIP_CONFIG_FILE"] == os.devnull


def test_audit_write_failure_still_reaps_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = published.Runner(tmp_path)
    stopped: list[Any] = []
    child = SimpleNamespace(pid=12345)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: child)

    def stop(proc: Any, windows: bool) -> str:
        stopped.append(proc)
        return "UNVERIFIED"

    monkeypatch.setattr(published, "stop_tree", stop)

    def write(path: Path, text: str) -> None:
        if path.name.endswith("-start.json"):
            raise OSError("synthetic audit failure")

    monkeypatch.setattr(published, "private_text", write)
    with pytest.raises(OSError):
        runner.run("synthetic", ["unused"], tmp_path, 30)
    assert stopped == [child]


def test_verifier_summary_exports_only_validated_nonsecret_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = release.Context(
        release.REPOSITORY,
        "workflow_dispatch",
        "refs/heads/main",
        "workflow",
        "2" * 40,
        SOURCE,
        "publish",
        "v" + VERSION,
        "123",
        "1",
    )
    monkeypatch.setattr(release.Context, "environment", lambda: context)
    monkeypatch.setattr(release, "evidence", lambda *a: "a" * 64)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release",
            "--source",
            "source",
            "--policy",
            "policy",
            "--raw",
            "raw",
            "--output",
            "output",
        ],
    )
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    assert release.main() == 0
    summary = (tmp_path / "summary").read_text()
    assert "a" * 64 in summary and SOURCE in summary and "2" * 40 in summary
    assert "Bearer" not in summary


def test_summary_failure_remains_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    context = release.Context(
        release.REPOSITORY,
        "workflow_dispatch",
        "refs/heads/main",
        "workflow",
        "2" * 40,
        SOURCE,
        "publish",
        "v" + VERSION,
        "123",
        "1",
    )
    monkeypatch.setattr(release.Context, "environment", lambda: context)
    monkeypatch.setattr(release, "evidence", lambda *a: "a" * 64)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release",
            "--source",
            "source",
            "--policy",
            "policy",
            "--raw",
            "raw",
            "--output",
            "output",
        ],
    )
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "absent/summary"))
    assert release.main() == 1
    assert capsys.readouterr() == (
        "",
        "Release verification failed; publication is blocked.\n",
    )
