"""Verify approved PyPI bytes, then smoke-test isolated wheel/sdist installs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.release_evidence import (  # noqa: E402
    MAX_ARTIFACT,
    REPOSITORY,
    SHA,
    VERSION,
    NoRedirect,
    verify_archives,
)
from tools.verify_installation_docs import isolated_environment, stop_tree  # noqa: E402
from tools.verify_package_build import INSTALLED_SMOKE, canonical_version, venv_python  # noqa: E402

DIGEST = re.compile(r"[0-9a-f]{64}\Z")
Fetch = Callable[[str, int], bytes]


class SmokeError(RuntimeError):
    """A publication check failed; no successful result can be inferred."""


def fetch_https(url: str, limit: int) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"pypi.org", "files.pythonhosted.org"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise SmokeError("untrusted download endpoint")
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        data = response.read(limit + 1)
    if not isinstance(data, bytes) or len(data) > limit:
        raise SmokeError("download bound exceeded")
    return data


def trusted_bundle(
    bundle: Path, version: str, source_sha: str, sums_digest: str
) -> dict[str, Any]:
    if (
        not VERSION.fullmatch(version)
        or not SHA.fullmatch(source_sha)
        or not DIGEST.fullmatch(sums_digest)
    ):
        raise SmokeError("invalid expected release identity")
    if bundle.is_symlink() or not bundle.is_dir():
        raise SmokeError("unsafe evidence root")
    if (bundle / "dist").is_symlink() or not (bundle / "dist").is_dir():
        raise SmokeError("unsafe distribution root")
    expected_names = {
        f"privacy_gateway-{version}-py3-none-any.whl",
        f"privacy_gateway-{version}.tar.gz",
    }
    if {path.name for path in bundle.iterdir()} != {
        "dist",
        "manifest.json",
        "SHA256SUMS",
    }:
        raise SmokeError("unexpected release evidence set")
    paths = [bundle / "manifest.json", bundle / "SHA256SUMS", bundle / "dist"]
    paths += list((bundle / "dist").iterdir())
    if any(
        path.is_symlink() or not path.resolve().is_relative_to(bundle.resolve())
        for path in paths
    ):
        raise SmokeError("release evidence escapes bundle")
    if {path.name for path in (bundle / "dist").iterdir()} != expected_names:
        raise SmokeError("unexpected approved distributions")
    sums = (bundle / "SHA256SUMS").read_bytes()
    if len(sums) > 4096 or hashlib.sha256(sums).hexdigest() != sums_digest:
        raise SmokeError("independent release digest mismatch")
    expected_paths = {"manifest.json"} | {"dist/" + name for name in expected_names}
    seen: set[str] = set()
    for line in sums.decode("ascii").splitlines():
        row = line.split("  ")
        if (
            len(row) != 2
            or not DIGEST.fullmatch(row[0])
            or row[1] not in expected_paths
            or row[1] in seen
        ):
            raise SmokeError("unsafe release checksum row")
        seen.add(row[1])
        path = bundle / row[1]
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_ARTIFACT:
            raise SmokeError("invalid approved file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != row[0]:
            raise SmokeError("approved bundle bytes differ")
    if seen != expected_paths:
        raise SmokeError("release checksum coverage mismatch")
    data = json.loads((bundle / "manifest.json").read_bytes())
    required = {
        "schema_version",
        "repository",
        "policy_sha",
        "source_sha",
        "version",
        "tag",
        "mode",
        "run_id",
        "run_attempt",
        "verify_job_id",
        "artifacts",
    }
    if (
        not isinstance(data, dict)
        or set(data) != required
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["repository"] != REPOSITORY
        or data["source_sha"] != source_sha
        or data["version"] != version
        or data["tag"] != "v" + version
        or data["mode"] != "publish"
        or not SHA.fullmatch(data["policy_sha"])
        or not re.fullmatch(r"[1-9][0-9]*", data["run_id"])
        or not re.fullmatch(r"[1-9][0-9]*", data["run_attempt"])
        or type(data["verify_job_id"]) is not int
        or data["verify_job_id"] <= 0
    ):
        raise SmokeError("release manifest identity mismatch")
    artifacts = data["artifacts"]
    if len(artifacts) != 2 or {item["name"] for item in artifacts} != expected_names:
        raise SmokeError("release manifest distribution set mismatch")
    for item in artifacts:
        file = bundle / "dist" / item["name"]
        if (
            set(item) != {"name", "size", "sha256"}
            or type(item["size"]) is not int
            or not 0 < item["size"] <= MAX_ARTIFACT
            or file.stat().st_size != item["size"]
            or not DIGEST.fullmatch(item["sha256"])
            or hashlib.sha256(file.read_bytes()).hexdigest() != item["sha256"]
        ):
            raise SmokeError("release manifest artifact mismatch")
    return data


def published_files(
    manifest: dict[str, Any], fetch: Fetch
) -> list[tuple[dict[str, Any], str]]:
    version = manifest["version"]
    url = f"https://pypi.org/pypi/privacy-gateway/{version}/json"
    metadata = json.loads(fetch(url, 2 * 1024 * 1024))
    if (
        metadata["info"]["version"] != version
        or re.sub(r"[-_.]+", "-", metadata["info"]["name"]).lower() != "privacy-gateway"
    ):
        raise SmokeError("published package identity mismatch")
    approved = {item["name"]: item for item in manifest["artifacts"]}
    records = metadata["urls"]
    if len(records) != 2 or {item["filename"] for item in records} != set(approved):
        raise SmokeError("published distribution set mismatch")
    result: list[tuple[dict[str, Any], str]] = []
    for item in records:
        expected = approved[item["filename"]]
        expected_type = "bdist_wheel" if item["filename"].endswith(".whl") else "sdist"
        if (
            item.get("packagetype") != expected_type
            or item.get("yanked") is not False
            or type(item.get("size")) is not int
            or item["size"] != expected["size"]
            or item["digests"]["sha256"] != expected["sha256"]
        ):
            raise SmokeError("published distribution digest or availability mismatch")
        target = urllib.parse.urlsplit(item["url"])
        if (
            target.scheme != "https"
            or target.hostname != "files.pythonhosted.org"
            or target.username is not None
            or target.password is not None
            or target.port not in {None, 443}
            or target.query
            or target.fragment
        ):
            raise SmokeError("untrusted distribution URL")
        result.append((expected, item["url"]))
    return result


def private_text(path: Path, text: str) -> None:
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
        encoding="utf-8",
    ) as stream:
        stream.write(text)


class Runner:
    def __init__(self, root: Path):
        self.root = root
        self.commands: list[dict[str, Any]] = []
        self.env = isolated_environment(root / "profile")
        self.logs = root / "logs"
        self.logs.mkdir(mode=0o700)

    def run(self, label: str, argv: list[str], cwd: Path, timeout: int) -> str:
        public: list[str] = []
        for arg in argv:
            if arg == INSTALLED_SMOKE:
                public.append(
                    "<installed-smoke-sha256:"
                    + hashlib.sha256(arg.encode()).hexdigest()
                    + ">"
                )
            elif arg == sys.executable:
                public.append("<host-python>")
            else:
                public.append(arg.replace(str(self.root), "<temporary-root>"))
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=os.name != "nt",
        )
        try:
            import datetime

            private_text(
                self.logs / (label + "-start.json"),
                json.dumps(
                    {
                        "phase": label,
                        "pid": process.pid,
                        "started_at_utc": datetime.datetime.now(
                            datetime.UTC
                        ).isoformat(),
                        "timeout_seconds": timeout,
                        "progress": "NOT_YET_PROVEN",
                    }
                )
                + "\n",
            )
            output, _ = process.communicate(timeout=timeout)
        except BaseException:
            outcome = "CLEANUP_UNVERIFIED"
            try:
                outcome = stop_tree(process, os.name == "nt")
            except Exception:
                pass
            try:
                private_text(self.logs / (label + "-cleanup.txt"), outcome)
            except OSError:
                pass
            raise
        private_text(self.logs / (label + ".log"), output)
        self.commands.append(
            {
                "phase": label,
                "argv_template": public,
                "exit_code": process.returncode,
                "cwd_template": "<temporary-root>/"
                + cwd.relative_to(self.root).as_posix(),
                "timeout_seconds": timeout,
            }
        )
        private_text(
            self.logs / (label + "-terminal.json"), json.dumps(self.commands[-1]) + "\n"
        )
        if process.returncode:
            raise SmokeError("isolated package command failed")
        return output


def installed_checks(
    artifact: Path, kind: str, root: Path, runner: Runner, version: str
) -> dict[str, Any]:
    venv = root / (kind + "-venv")
    work = root / (kind + "-work")
    work.mkdir(mode=0o700)
    runner.run(kind + "-venv", [sys.executable, "-m", "venv", str(venv)], root, 60)
    python = venv_python(venv)
    runner.run(
        kind + "-install",
        [
            str(python),
            "-m",
            "pip",
            "--isolated",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--retries",
            "0",
            "--timeout",
            "30",
            "install",
            "--index-url",
            "https://pypi.org/simple",
            "--quiet",
            str(artifact),
        ],
        work,
        600,
    )
    result = runner.run(
        kind + "-synthetic",
        [str(python), "-I", "-B", "-X", "utf8", "-c", INSTALLED_SMOKE],
        work,
        60,
    )
    payload = json.loads(result.strip().splitlines()[-1])
    module = Path(payload["module_file"]).resolve()
    if (
        payload.get("version") != version
        or payload.get("metadata") != version
        or payload.get("roundtrip") is not True
        or payload.get("console_doctor") is not True
        or not module.is_relative_to(venv.resolve())
    ):
        raise SmokeError("installed import, version or synthetic scenario mismatch")
    console = python.parent / ("pgw.exe" if os.name == "nt" else "pgw")
    runner.run(kind + "-help", [str(console), "--help"], work, 30)
    described = runner.run(kind + "-describe", [str(console), "--describe"], work, 30)
    catalog = json.loads(described)
    if "doctor" not in {item["id"] for item in catalog["commands"]}:
        raise SmokeError("installed CLI introspection mismatch")
    return {
        "version": version,
        "metadata": version,
        "import_inside_new_venv": True,
        "api_roundtrip": True,
        "console_doctor": True,
        "help": True,
        "describe": True,
    }


def check(
    bundle: Path,
    version: str,
    source_sha: str,
    sums_digest: str,
    root: Path,
    fetch: Fetch | None = None,
) -> dict[str, Any]:
    if sys.platform not in {"linux", "win32"} or sys.version_info[:2] not in {
        (3, 11),
        (3, 12),
    }:
        raise SmokeError("unsupported smoke platform/Python")
    manifest = trusted_bundle(bundle, version, source_sha, sums_digest)
    if root.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise SmokeError("smoke environments must be outside checkout")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        simulation = fetch is not None
        if fetch is None:
            fetch = fetch_https
        records = published_files(manifest, fetch)
        downloads = root / "downloaded"
        downloads.mkdir(mode=0o700)
        files: list[Path] = []
        for item, url in records:
            data = fetch(url, item["size"])
            if (
                len(data) != item["size"]
                or hashlib.sha256(data).hexdigest() != item["sha256"]
            ):
                raise SmokeError(
                    "downloaded distribution differs from approved release"
                )
            path = downloads / item["name"]
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            ) as stream:
                stream.write(data)
            files.append(path)
        runner = Runner(root)
        checks = {}
        for file in sorted(files):
            kind = "wheel" if file.suffix == ".whl" else "sdist"
            checks[kind] = installed_checks(file, kind, root, runner, version)
        evidence = {
            "schema_version": 1,
            "status": "LOCAL_SIMULATION_PASS" if simulation else "PASS",
            "project": "privacy-gateway",
            "version": version,
            "source_sha": source_sha,
            "policy_sha": manifest["policy_sha"],
            "release_run_id": manifest["run_id"],
            "release_run_attempt": manifest["run_attempt"],
            "verify_job_id": manifest["verify_job_id"],
            "platform": sys.platform,
            "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
            "artifacts": manifest["artifacts"],
            "commands": runner.commands,
            "checks": checks,
            "provider_fetch": "LOCAL_SIMULATION"
            if simulation
            else "PyPI HTTPS metadata and verified file bytes",
        }
        private_text(
            root / "evidence.json",
            json.dumps(evidence, sort_keys=True, indent=2) + "\n",
        )
        return evidence
    except BaseException:
        try:
            private_text(
                root / "blocked.json", '{"status":"BLOCKED","successful_smoke":false}\n'
            )
        except OSError:
            pass
        raise


def local_fixture(
    source: Path, dist: Path, source_sha: str, root: Path
) -> dict[str, Any]:
    """Developer-only simulation: no PyPI project query or publisher authority."""
    if not SHA.fullmatch(source_sha):
        raise SmokeError("invalid fixture source SHA")
    version = canonical_version(source / "src/privacy_gateway/__init__.py")
    artifacts = verify_archives(source, dist, version)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    bundle = root / "SIMULATED-release-bundle"
    (bundle / "dist").mkdir(mode=0o700, parents=True)
    payloads: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    for path in artifacts:
        data = path.read_bytes()
        payloads[path.name] = data
        (bundle / "dist" / path.name).write_bytes(data)
        records.append(
            {
                "name": path.name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    # Fabricated provider IDs: this bundle is never actual release authority.
    manifest = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "policy_sha": source_sha,
        "source_sha": source_sha,
        "version": version,
        "tag": "v" + version,
        "mode": "publish",
        "run_id": "1",
        "run_attempt": "1",
        "verify_job_id": 1,
        "artifacts": records,
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    names = ["manifest.json"] + ["dist/" + item["name"] for item in records]
    sums = "".join(
        hashlib.sha256((bundle / name).read_bytes()).hexdigest() + "  " + name + "\n"
        for name in sorted(names)
    ).encode()
    (bundle / "SHA256SUMS").write_bytes(sums)
    urls = [
        {
            "filename": item["name"],
            "size": item["size"],
            "digests": {"sha256": item["sha256"]},
            "yanked": False,
            "packagetype": "bdist_wheel" if item["name"].endswith(".whl") else "sdist",
            "url": "https://files.pythonhosted.org/packages/LOCAL-SIMULATION/"
            + item["name"],
        }
        for item in records
    ]
    metadata = json.dumps(
        {"info": {"name": "privacy-gateway", "version": version}, "urls": urls}
    ).encode()

    def fixture_fetch(url: str, limit: int) -> bytes:
        if url == f"https://pypi.org/pypi/privacy-gateway/{version}/json":
            return metadata
        prefix = "https://files.pythonhosted.org/packages/LOCAL-SIMULATION/"
        if not url.startswith(prefix):
            raise SmokeError("fixture attempted a real provider endpoint")
        return payloads[url.removeprefix(prefix)]

    result = check(
        bundle,
        version,
        source_sha,
        hashlib.sha256(sums).hexdigest(),
        root / "smoke",
        fixture_fetch,
    )
    if result["status"] != "LOCAL_SIMULATION_PASS":
        raise SmokeError("fixture cannot produce actual publication authority")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bundle", type=Path)
    mode.add_argument("--fixture-source", type=Path)
    parser.add_argument("--fixture-dist", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--sums-sha256")
    parser.add_argument("--temp-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.fixture_source is not None:
            if (
                args.fixture_dist is None
                or args.version is not None
                or args.sums_sha256 is not None
            ):
                raise SmokeError("invalid local fixture arguments")
            local_fixture(
                args.fixture_source, args.fixture_dist, args.source_sha, args.temp_root
            )
            print("POST_PUBLICATION: LOCAL_SIMULATION_PASS (provider simulated)")
        else:
            if (
                args.version is None
                or args.sums_sha256 is None
                or args.fixture_dist is not None
            ):
                raise SmokeError(
                    "actual smoke requires independently approved version and digest"
                )
            check(
                args.bundle,
                args.version,
                args.source_sha,
                args.sums_sha256,
                args.temp_root,
            )
            print("POST_PUBLICATION: PASS")
    except SmokeError as exc:
        print("POST_PUBLICATION: BLOCKED (" + str(exc) + ")", file=sys.stderr)
        return 1
    except Exception:
        print("POST_PUBLICATION: BLOCKED (no successful smoke result)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
