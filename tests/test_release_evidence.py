"""Synthetic release admission, tamper rejection and non-executing evidence."""

from __future__ import annotations

import base64
import copy
import csv
import hashlib
import io
import json
import tarfile
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tools import release_evidence as release

SOURCE = "1" * 40
POLICY = "2" * 40
BASE = f"/repos/{release.REPOSITORY}"


@pytest.fixture
def context() -> release.Context:
    return release.Context(
        release.REPOSITORY,
        "workflow_dispatch",
        "refs/heads/main",
        f"{release.REPOSITORY}/.github/workflows/publish-pypi.yml@refs/heads/main",
        POLICY,
        SOURCE,
        "publish",
        "v0.5.0",
        "123",
        "1",
    )


@pytest.fixture
def api() -> dict[str, dict[str, Any]]:
    return {
        BASE: {"full_name": release.REPOSITORY, "visibility": "public"},
        BASE + f"/commits/{SOURCE}": {"sha": SOURCE},
        BASE + "/git/ref/heads/main": {"object": {"sha": POLICY}},
        BASE + f"/compare/{SOURCE}...{POLICY}": {
            "merge_base_commit": {"sha": SOURCE},
            "status": "ahead",
        },
        BASE + "/git/ref/tags/v0.5.0": {"object": {"type": "commit", "sha": SOURCE}},
        BASE + "/environments/pypi": {
            "name": "pypi",
            "can_admins_bypass": False,
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": False,
                    "reviewers": [
                        {
                            "type": "User",
                            "reviewer": {"login": "MRDK80", "id": 53867710},
                        }
                    ],
                }
            ],
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            },
        },
        BASE + "/environments/pypi/deployment-branch-policies?per_page=100": {
            "total_count": 1,
            "branch_policies": [{"id": 11, "name": "main", "type": "branch"}],
        },
        BASE + "/actions/runs/123/attempts/1/jobs?per_page=100": {
            "jobs": [
                {
                    "name": "Verify release",
                    "run_id": 123,
                    "head_sha": POLICY,
                    "status": "in_progress",
                    "id": 456,
                }
            ]
        },
    }


@pytest.fixture
def candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    source, policy, raw = (tmp_path / name for name in ("source", "policy", "raw"))
    for p in (source, policy, raw):
        p.mkdir()
    package = source / "src/privacy_gateway"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        '__version__ = "0.5.0"\n'
        'raise RuntimeError("synthetic package must not execute")\n'
    )
    (package / "py.typed").write_bytes(b"")
    (source / "LICENSE").write_text("Synthetic MIT fixture\n")
    (source / "README.md").write_text("Synthetic package\n")
    (source / "pyproject.toml").write_text("""[project]
name = "privacy-gateway"
requires-python = ">=3.11"
dependencies = []
[project.scripts]
pgw = "privacy_gateway.bootstrap:main"
""")

    def git(root: Path, *args: str) -> str:
        if args == ("status", "--porcelain"):
            return ""
        assert args == ("rev-parse", "HEAD")
        return SOURCE if root == source else POLICY

    monkeypatch.setattr(release, "git_output", git)
    build_fixtures(source, raw)
    return source, policy, raw


def build_fixtures(source: Path, raw: Path, changed: str = "") -> None:
    version = "0.5.0"
    prefix = f"privacy_gateway-{version}"
    info = prefix + ".dist-info/"
    wheel = {
        "privacy_gateway/__init__.py": (
            source / "src/privacy_gateway/__init__.py"
        ).read_bytes(),
        "privacy_gateway/py.typed": b"",
        info + "METADATA": (
            b"Metadata-Version: 2.4\nName: privacy-gateway\nVersion: 0.5.0\n"
            b"Requires-Python: >=3.11\nLicense-Expression: MIT\n"
            b"License-File: LICENSE\n\n"
        ),
        info + "WHEEL": b"Wheel-Version: 1.0\nGenerator: setuptools\n",
        info + "RECORD": b"",
        info + "top_level.txt": b"privacy_gateway\n",
        info + "entry_points.txt": (
            b"[console_scripts]\npgw = privacy_gateway.bootstrap:main\n"
        ),
        info + "licenses/LICENSE": (source / "LICENSE").read_bytes(),
    }
    if changed == "module":
        wheel["privacy_gateway/__init__.py"] += b"# altered source\n"
    elif changed == "payload":
        wheel["injected.pth"] = b"import synthetic\n"
    elif changed == "dependency":
        wheel[info + "METADATA"] = wheel[info + "METADATA"].replace(
            b"\n\n", b"\nRequires-Dist: synthetic-unapproved\n\n"
        )
    elif changed == "entry":
        wheel[info + "entry_points.txt"] = b"[console_scripts]\npgw = synthetic:main\n"
    elif changed == "license":
        wheel[info + "licenses/LICENSE"] = b"changed license"
    record_stream = io.StringIO(newline="")
    writer = csv.writer(record_stream, lineterminator="\n")
    for name, content in sorted(wheel.items()):
        if name == info + "RECORD":
            writer.writerow([name, "", ""])
        else:
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(content).digest())
                .rstrip(b"=")
                .decode()
            )
            writer.writerow([name, "sha256=" + digest, str(len(content))])
    wheel[info + "RECORD"] = record_stream.getvalue().encode()
    with zipfile.ZipFile(raw / (prefix + "-py3-none-any.whl"), "w") as archive:
        for name, content in wheel.items():
            archive.writestr(name, content)
    with tarfile.open(raw / (prefix + ".tar.gz"), "w:gz") as archive:
        files = {
            str(p.relative_to(source)).replace("\\", "/"): p.read_bytes()
            for p in source.rglob("*")
            if p.is_file()
        }
        files["setup.cfg"] = b"[egg_info]\ntag_build = \ntag_date = 0\n"
        if changed == "setup":
            files["setup.py"] = b"raise RuntimeError('synthetic')\n"
        if changed == "build-metadata":
            files["pyproject.toml"] += b"# changed\n"
        for name, content in files.items():
            member = tarfile.TarInfo(prefix + "/" + name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def test_production_admission_and_evidence(
    candidate: tuple[Path, Path, Path],
    context: release.Context,
    api: dict[str, dict[str, Any]],
    tmp_path: Path,
) -> None:
    source, policy, raw = candidate
    output = tmp_path / "verified"
    digest = release.evidence(context, source, policy, raw, output, api.__getitem__)
    sums = (output / "SHA256SUMS").read_bytes()
    assert hashlib.sha256(sums).hexdigest() == digest
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["source_sha"] == SOURCE and manifest["policy_sha"] == POLICY
    assert manifest["verify_job_id"] == 456 and manifest["mode"] == "publish"
    assert len(manifest["artifacts"]) == 2
    for line in sums.decode().splitlines():
        expected, name = line.split("  ")
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == expected


def test_dry_run_never_queries_production_environment(
    candidate: tuple[Path, Path, Path],
    context: release.Context,
    api: dict[str, dict[str, Any]],
) -> None:
    calls: list[str] = []

    def get(path: str) -> dict[str, Any]:
        calls.append(path)
        assert "/environments/" not in path and "/git/ref/tags/" not in path
        return api[path]

    source, policy, _ = candidate
    result = release.admit(
        replace(context, mode="dry-run", tag="", ref="refs/heads/task"),
        source,
        policy,
        get,
    )
    assert result == ("0.5.0", "v0.5.0", 456)


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository", "other/repository"),
        ("event", "pull_request"),
        ("source_sha", "main"),
        ("mode", "automatic"),
        ("tag", "v0.6.0"),
        ("ref", "refs/heads/task"),
        ("policy_sha", "3" * 40),
        ("run_id", "01"),
        ("workflow_ref", "untrusted"),
    ],
)
def test_invalid_context_is_rejected(
    field: str,
    value: str,
    candidate: tuple[Path, Path, Path],
    context: release.Context,
    api: dict[str, dict[str, Any]],
) -> None:
    source, policy, _ = candidate
    with pytest.raises(release.ReleaseError):
        release.admit(
            replace(context, **{field: value}), source, policy, api.__getitem__
        )


@pytest.mark.parametrize(
    "change",
    [
        "bypass",
        "missing-bypass",
        "extra-reviewer",
        "no-reviewer",
        "branch",
        "private",
        "tag",
        "ancestor",
        "job",
    ],
)
def test_primary_protection_and_source_fail_closed(
    change: str,
    candidate: tuple[Path, Path, Path],
    context: release.Context,
    api: dict[str, dict[str, Any]],
) -> None:
    data = copy.deepcopy(api)
    env = data[BASE + "/environments/pypi"]
    if change == "bypass":
        env["can_admins_bypass"] = True
    elif change == "missing-bypass":
        del env["can_admins_bypass"]
    elif change == "extra-reviewer":
        env["protection_rules"][0]["reviewers"] *= 2
    elif change == "no-reviewer":
        env["protection_rules"] = []
    elif change == "branch":
        data[BASE + "/environments/pypi/deployment-branch-policies?per_page=100"][
            "branch_policies"
        ][0]["name"] = "*"
    elif change == "private":
        data[BASE]["visibility"] = "private"
    elif change == "tag":
        data[BASE + "/git/ref/tags/v0.5.0"]["object"]["sha"] = POLICY
    elif change == "ancestor":
        data[BASE + f"/compare/{SOURCE}...{POLICY}"]["merge_base_commit"]["sha"] = (
            POLICY
        )
    else:
        data[BASE + "/actions/runs/123/attempts/1/jobs?per_page=100"]["jobs"] = []
    source, policy, _ = candidate
    with pytest.raises(release.ReleaseError):
        release.admit(context, source, policy, data.__getitem__)


@pytest.mark.parametrize(
    "changed",
    ["module", "payload", "dependency", "entry", "license", "setup", "build-metadata"],
)
def test_artifact_tampering_rejected(
    changed: str,
    candidate: tuple[Path, Path, Path],
) -> None:
    source, _, raw = candidate
    build_fixtures(source, raw, changed)
    with pytest.raises(release.ReleaseError):
        release.verify_archives(source, raw, "0.5.0")


@pytest.mark.parametrize(
    "change", ["external", "missing", "duplicate", "hash", "size", "self", "csv"]
)
def test_record_cannot_declare_unsafe_or_inconsistent_rows(
    change: str,
    candidate: tuple[Path, Path, Path],
) -> None:
    source, _, raw = candidate
    path = next(raw.glob("*.whl"))
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    record = "privacy_gateway-0.5.0.dist-info/RECORD"
    rows = list(csv.reader(io.StringIO(files[record].decode())))
    if change == "external":
        rows.append(["../../../synthetic-outside-wheel", "", ""])
    elif change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(rows[0])
    elif change == "hash":
        next(row for row in rows if row[0] != record)[1] = "sha256=incorrect"
    elif change == "size":
        next(row for row in rows if row[0] != record)[2] = "999999"
    elif change == "self":
        next(row for row in rows if row[0] == record)[2] = "0"
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    files[record] = output.getvalue().encode() if change != "csv" else b'"unclosed'
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    with pytest.raises(release.ReleaseError):
        release.verify_archives(source, raw, "0.5.0")


def test_main_keeps_unexpected_failure_diagnostics_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_evidence",
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

    def failed(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("synthetic private response body")

    monkeypatch.setattr(release, "evidence", failed)
    assert release.main() == 1
    assert capsys.readouterr() == (
        "",
        "Release verification failed; publication is blocked.\n",
    )
