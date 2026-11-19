"""Trusted, non-executing release admission and artifact evidence (ADR-114)."""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tomllib
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from email import message_from_bytes
from pathlib import Path, PurePosixPath
from typing import Any

# Resolve policy helpers, never modules from the source checkout or artifact.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.verify_package_build import (  # noqa: E402
    canonical_version,
    validate_license_contract,
    verify_artifact_contents,
    wheel_facts,
)

REPOSITORY = "MRDK80/privacy-gateway"
SHA = re.compile(r"[0-9a-f]{40}\Z")
VERSION = re.compile(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?\Z")
MAX_ARTIFACT = 50 * 1024 * 1024
GetJSON = Callable[[str], dict[str, Any]]


class ReleaseError(RuntimeError):
    """Closed release-admission failure; never include API or credential bodies."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise ReleaseError("API redirect is not admitted")


def api_get(path: str) -> dict[str, Any]:
    if not path.startswith(f"/repos/{REPOSITORY}/") and path != f"/repos/{REPOSITORY}":
        raise ReleaseError("API resource outside release repository")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request("https://api.github.com" + path, headers=headers)
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
        body = response.read(2 * 1024 * 1024 + 1)
    if len(body) > 2 * 1024 * 1024:
        raise ReleaseError("API response exceeds admission bound")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ReleaseError("invalid API response shape")
    return value


@dataclass(frozen=True)
class Context:
    repository: str
    event: str
    ref: str
    workflow_ref: str
    policy_sha: str
    source_sha: str
    mode: str
    tag: str
    run_id: str
    run_attempt: str

    @classmethod
    def environment(cls) -> Context:
        names = (
            "GITHUB_REPOSITORY",
            "GITHUB_EVENT_NAME",
            "GITHUB_REF",
            "GITHUB_WORKFLOW_REF",
            "POLICY_SHA",
            "SOURCE_SHA",
            "RELEASE_MODE",
            "RELEASE_TAG",
            "GITHUB_RUN_ID",
            "GITHUB_RUN_ATTEMPT",
        )
        return cls(*(os.environ.get(name, "") for name in names))


def git_output(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise ReleaseError("source Git verification failed")
    return result.stdout.strip()


def require_environment(get: GetJSON) -> None:
    env = get(f"/repos/{REPOSITORY}/environments/pypi")
    if env.get("name") != "pypi" or env.get("can_admins_bypass") is not False:
        raise ReleaseError("environment enforcement unavailable")
    rules = env.get("protection_rules", [])
    required = [rule for rule in rules if rule.get("type") == "required_reviewers"]
    if len(required) != 1 or required[0].get("prevent_self_review") is not False:
        raise ReleaseError("required reviewer policy unavailable")
    reviewers = required[0].get("reviewers", [])
    if (
        len(reviewers) != 1
        or reviewers[0].get("type") != "User"
        or reviewers[0].get("reviewer", {}).get("login") != "MRDK80"
        or reviewers[0].get("reviewer", {}).get("id") != 53867710
    ):
        raise ReleaseError("required owner approval unavailable")
    policy = env.get("deployment_branch_policy", {})
    if (
        policy.get("protected_branches") is not False
        or policy.get("custom_branch_policies") is not True
    ):
        raise ReleaseError("main-only deployment policy unavailable")
    branches = get(
        f"/repos/{REPOSITORY}/environments/pypi/deployment-branch-policies?per_page=100"
    )
    values = branches.get("branch_policies", [])
    if (
        type(branches.get("total_count")) is not int
        or branches.get("total_count") != 1
        or len(values) != 1
        or values[0].get("name") != "main"
        or values[0].get("type") != "branch"
    ):
        raise ReleaseError("main-only branch rule unavailable")


def admit(
    context: Context, source: Path, policy: Path, get: GetJSON
) -> tuple[str, str, int]:
    if (
        context.repository != REPOSITORY
        or context.event != "workflow_dispatch"
        or context.mode not in {"dry-run", "publish"}
        or not SHA.fullmatch(context.source_sha)
        or not SHA.fullmatch(context.policy_sha)
        or not re.fullmatch(r"[1-9][0-9]*", context.run_id)
        or not re.fullmatch(r"[1-9][0-9]*", context.run_attempt)
    ):
        raise ReleaseError("invalid release context")
    expected_workflow = f"{REPOSITORY}/.github/workflows/publish-pypi.yml@"
    if not context.workflow_ref.startswith(expected_workflow + "refs/heads/"):
        raise ReleaseError("untrusted workflow source")
    for root, sha in ((source, context.source_sha), (policy, context.policy_sha)):
        if git_output(root, "rev-parse", "HEAD") != sha or git_output(
            root, "status", "--porcelain"
        ):
            raise ReleaseError("checkout identity or cleanliness mismatch")
    init = source / "src/privacy_gateway/__init__.py"
    if init.is_symlink() or not init.is_file():
        raise ReleaseError("canonical version source unavailable")
    version = canonical_version(init)
    if not VERSION.fullmatch(version):
        raise ReleaseError("unsupported release version")
    tag = context.tag or "v" + version
    if tag != "v" + version:
        raise ReleaseError("release tag/version mismatch")
    repo = get(f"/repos/{REPOSITORY}")
    if repo.get("full_name") != REPOSITORY or repo.get("visibility") != "public":
        raise ReleaseError("repository enforcement unsupported")
    if (
        get(f"/repos/{REPOSITORY}/commits/{context.source_sha}").get("sha")
        != context.source_sha
    ):
        raise ReleaseError("source commit unavailable")
    if context.mode == "publish":
        if (
            context.ref != "refs/heads/main"
            or context.workflow_ref != expected_workflow + "refs/heads/main"
        ):
            raise ReleaseError("production workflow must originate from main")
        main = get(f"/repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"]
        if main != context.policy_sha:
            raise ReleaseError("production policy is no longer current main")
        comparison = get(f"/repos/{REPOSITORY}/compare/{context.source_sha}...{main}")
        if comparison.get("merge_base_commit", {}).get(
            "sha"
        ) != context.source_sha or comparison.get("status") not in {
            "ahead",
            "identical",
        }:
            raise ReleaseError("release source is not an ancestor of main")
        obj = get(f"/repos/{REPOSITORY}/git/ref/tags/{tag}")["object"]
        for _ in range(4):
            if obj.get("type") != "tag":
                break
            if not SHA.fullmatch(str(obj.get("sha", ""))):
                raise ReleaseError("invalid annotated tag identity")
            obj = get(f"/repos/{REPOSITORY}/git/tags/{obj['sha']}")["object"]
        if obj.get("type") != "commit" or obj.get("sha") != context.source_sha:
            raise ReleaseError("release tag does not identify source")
        require_environment(get)
    jobs = get(
        f"/repos/{REPOSITORY}/actions/runs/{context.run_id}/attempts/{context.run_attempt}/jobs?per_page=100"
    )
    own = [
        job
        for job in jobs.get("jobs", [])
        if job.get("name") == "Verify release"
        and job.get("run_id") == int(context.run_id)
        and job.get("head_sha") == context.policy_sha
        and job.get("status") == "in_progress"
    ]
    if len(own) != 1 or type(own[0].get("id")) is not int or own[0]["id"] <= 0:
        raise ReleaseError("current verification job identity unavailable")
    return version, tag, own[0]["id"]


def verify_record(archive: zipfile.ZipFile, name: str) -> None:
    members = set(archive.namelist())
    seen: set[str] = set()
    try:
        reader = csv.reader(
            io.StringIO(archive.read(name).decode("utf-8"), newline=""), strict=True
        )
        for row in reader:
            if len(row) != 3:
                raise ReleaseError("invalid wheel RECORD row")
            path, digest, size = row
            if (
                path not in members
                or path in seen
                or "\\" in path
                or path.startswith("/")
                or PurePosixPath(path).as_posix() != path
                or any(part in {".", ".."} for part in PurePosixPath(path).parts)
            ):
                raise ReleaseError("unsafe wheel RECORD path")
            seen.add(path)
            if path == name:
                if digest or size:
                    raise ReleaseError("invalid wheel RECORD self row")
            else:
                data = archive.read(path)
                expected = (
                    base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                    .rstrip(b"=")
                    .decode("ascii")
                )
                if digest != "sha256=" + expected or size != str(len(data)):
                    raise ReleaseError("wheel RECORD hash or size mismatch")
        if seen != members:
            raise ReleaseError("wheel RECORD coverage mismatch")
    except (UnicodeError, csv.Error) as exc:
        raise ReleaseError("invalid wheel RECORD encoding or CSV") from exc


def verify_archives(source: Path, raw: Path, version: str) -> tuple[Path, Path]:
    names = {
        f"privacy_gateway-{version}-py3-none-any.whl",
        f"privacy_gateway-{version}.tar.gz",
    }
    files = list(raw.iterdir())
    if {path.name for path in files} != names or len(files) != 2:
        raise ReleaseError("unexpected release artifact set")
    for path in files:
        if (
            path.is_symlink()
            or not path.is_file()
            or not 0 < path.stat().st_size <= MAX_ARTIFACT
        ):
            raise ReleaseError("unsafe release artifact")
    wheel = raw / f"privacy_gateway-{version}-py3-none-any.whl"
    sdist = raw / f"privacy_gateway-{version}.tar.gz"
    with zipfile.ZipFile(wheel) as archive:
        members = archive.infolist()
        if (
            len(members) > 2000
            or sum(item.file_size for item in members) > MAX_ARTIFACT
        ):
            raise ReleaseError("wheel expansion bound exceeded")
    with tarfile.open(sdist) as tar_bound:
        total = 0
        seen: set[str] = set()
        for i, item in enumerate(tar_bound):
            total += item.size
            if item.name in seen or i >= 5000 or total > MAX_ARTIFACT:
                raise ReleaseError("sdist expansion bound exceeded")
            seen.add(item.name)
    verify_artifact_contents(source, sdist, wheel)
    facts = wheel_facts(wheel)
    validate_license_contract(facts)
    if facts.version != version:
        raise ReleaseError("wheel version mismatch")
    modules = {
        p.relative_to(source / "src").as_posix(): p
        for p in (source / "src/privacy_gateway").rglob("*")
        if p.is_file() and (p.suffix == ".py" or p.name == "py.typed")
    }
    with zipfile.ZipFile(wheel) as archive, tarfile.open(sdist) as tar:
        root = tar.getmembers()[0].name.split("/")[0]
        metadata_dir = f"privacy_gateway-{version}.dist-info/"
        allowed_metadata = {
            metadata_dir + name
            for name in (
                "METADATA",
                "WHEEL",
                "RECORD",
                "entry_points.txt",
                "top_level.txt",
                "licenses/LICENSE",
            )
        }
        if set(archive.namelist()) != set(modules) | allowed_metadata:
            raise ReleaseError("unexpected wheel payload")
        verify_record(archive, metadata_dir + "RECORD")
        metadata = message_from_bytes(archive.read(metadata_dir + "METADATA"))
        project_data = tomllib.loads(
            (source / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        expected_requires = list(project_data.get("dependencies", []))
        extras = project_data.get("optional-dependencies", {})
        for extra, requirements in extras.items():
            expected_requires.extend(req + f";extra=='{extra}'" for req in requirements)

        def normalize(value: str) -> str:
            return "".join(value.split()).replace('"', "'").lower()

        if (
            metadata.get("Name") != "privacy-gateway"
            or metadata.get("Requires-Python") != project_data["requires-python"]
            or sorted(map(normalize, metadata.get_all("Requires-Dist", [])))
            != sorted(map(normalize, expected_requires))
            or sorted(metadata.get_all("Provides-Extra", [])) != sorted(extras)
        ):
            raise ReleaseError("wheel dependency metadata differs from source")
        entry = configparser.ConfigParser(interpolation=None)
        entry.read_string(
            archive.read(metadata_dir + "entry_points.txt").decode("utf-8")
        )
        if (
            entry.sections() != ["console_scripts"]
            or dict(entry["console_scripts"]) != project_data["scripts"]
        ):
            raise ReleaseError("wheel entry points differ from source")
        if (
            archive.read(metadata_dir + "licenses/LICENSE")
            != (source / "LICENSE").read_bytes()
        ):
            raise ReleaseError("wheel license differs from source")
        for member_info in tar.getmembers():
            relative = "/".join(member_info.name.split("/")[1:])
            if member_info.isfile() and relative.endswith(".py"):
                original = source / relative
                content = tar.extractfile(member_info)
                if (
                    not original.is_file()
                    or original.is_symlink()
                    or not original.resolve().is_relative_to(source.resolve())
                    or content is None
                    or content.read() != original.read_bytes()
                ):
                    raise ReleaseError("sdist executable source differs from checkout")
        setup = tar.extractfile(root + "/setup.cfg")
        if setup is not None:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(setup.read().decode("utf-8"))
            if parser.sections() != ["egg_info"] or dict(parser["egg_info"]) != {
                "tag_build": "",
                "tag_date": "0",
            }:
                raise ReleaseError("unexpected generated build configuration")
        wheel_modules = {name for name in archive.namelist() if name.endswith(".py")}
        if wheel_modules != {name for name in modules if name.endswith(".py")}:
            raise ReleaseError("unexpected executable module in wheel")
        for name, path in modules.items():
            if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
                raise ReleaseError("source module escapes checkout")
            member = tar.extractfile(root + "/src/" + name)
            if (
                member is None
                or archive.read(name) != path.read_bytes()
                or member.read() != path.read_bytes()
            ):
                raise ReleaseError("artifact module differs from source")
        project = tar.extractfile(root + "/pyproject.toml")
        if (
            project is None
            or project.read() != (source / "pyproject.toml").read_bytes()
        ):
            raise ReleaseError("sdist build metadata differs from source")
    return wheel, sdist


def evidence(
    context: Context,
    source: Path,
    policy: Path,
    raw: Path,
    output: Path,
    get: GetJSON = api_get,
) -> str:
    version, tag, job_id = admit(context, source, policy, get)
    files = verify_archives(source, raw, version)
    output.mkdir(parents=True, exist_ok=False)
    dist = output / "dist"
    dist.mkdir()
    records: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    for path in sorted(files):
        data = path.read_bytes()
        (dist / path.name).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        records.append({"name": path.name, "size": len(data), "sha256": digest})
        hashes["dist/" + path.name] = digest
    manifest = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "policy_sha": context.policy_sha,
        "source_sha": context.source_sha,
        "version": version,
        "tag": tag,
        "mode": context.mode,
        "run_id": context.run_id,
        "run_attempt": context.run_attempt,
        "verify_job_id": job_id,
        "artifacts": records,
    }
    content = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    (output / "manifest.json").write_bytes(content)
    hashes["manifest.json"] = hashlib.sha256(content).hexdigest()
    sums = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(hashes.items())
    ).encode()
    (output / "SHA256SUMS").write_bytes(sums)
    return hashlib.sha256(sums).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "policy", "raw", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        context = Context.environment()
        digest = evidence(context, args.source, args.policy, args.raw, args.output)
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            stream.write(
                f"sums_sha256={digest}\n"
                f"publish_ready={str(context.mode == 'publish').lower()}\n"
            )
    except Exception:
        print("Release verification failed; publication is blocked.", file=sys.stderr)
        return 1
    print("Release evidence verified; no publication performed by this step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
