#!/usr/bin/env python3
"""Build gate: проверка версии и PEP 639 metadata (issues #85, #86).

Инструмент собирает sdist и wheel из одноразовой копии текущего HEAD,
устанавливает wheel и sdist в раздельные чистые окружения и проверяет:

* единственный источник версии из ADR-81;
* SPDX license expression и license file из ADR-86;
* отсутствие устаревших license metadata и целевых warnings;
* корректное размещение LICENSE в sdist и wheel;
* фактический setuptools Generator в boundary-режиме;
* состав пакета, установленный CLI и синтетический API round trip;
* отсутствие изменений tracked checkout.

Все артефакты создаются только внутри каталога, переданного в --temp-root.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tarfile
import textwrap
import tomllib
import zipfile
from dataclasses import dataclass
from email import message_from_bytes, message_from_string
from email.message import Message
from pathlib import Path, PurePosixPath

DIST_NAME = "privacy-gateway"
IMPORT_NAME = "privacy_gateway"
INIT_RELPATH = Path("src") / IMPORT_NAME / "__init__.py"
DEFAULT_BUILD_PIN = "1.6.0"
EXPECTED_LICENSE_EXPRESSION = "MIT"
EXPECTED_LICENSE_FILE = "LICENSE"
DEPRECATED_LICENSE_MARKERS = (
    "`project.license` as a TOML table is deprecated",
    "License classifiers are deprecated",
)


class BuildGateError(RuntimeError):
    """Нарушен инвариант build gate."""


@dataclass(frozen=True)
class WheelFacts:
    """Проверяемые поля WHEEL, METADATA и состав wheel."""

    generator: str
    version: str
    license_expression: str
    license_files: tuple[str, ...]
    legacy_licenses: tuple[str, ...]
    license_classifiers: tuple[str, ...]
    license_paths: tuple[str, ...]
    expected_license_path: str



def clean_python_environment() -> dict[str, str]:
    """Keep caller configuration from redirecting clean Python imports."""
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("PYTHON")
    }
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1")
    return env


def run(
    cmd: list[str],
    cwd: Path | None = None,
    transcript: list[str] | None = None,
) -> str:
    """Выполнить команду, показать вывод и вернуть stdout."""
    print("$ " + " ".join(cmd), flush=True)
    completed = subprocess.run(
        cmd,
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        env=clean_python_environment(),
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if transcript is not None:
        transcript.extend((completed.stdout, completed.stderr))
    if completed.stdout.strip():
        print(completed.stdout.rstrip(), flush=True)
    if completed.stderr.strip():
        print(completed.stderr.rstrip(), flush=True)
    if completed.returncode != 0:
        raise BuildGateError(f"exit code {completed.returncode}: {' '.join(cmd)}")
    return completed.stdout


def venv_python(venv_dir: Path) -> Path:
    """Путь к интерпретатору внутри venv на Linux и Windows."""
    for relative in (Path("bin") / "python", Path("Scripts") / "python.exe"):
        candidate = venv_dir / relative
        if candidate.exists():
            return candidate
    raise BuildGateError(f"интерпретатор venv не найден: {venv_dir}")


def canonical_version(init_path: Path) -> str:
    """Извлечь единственный литерал __version__ из AST."""
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        names: list[str] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            names = [
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            ]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value
        if "__version__" not in names or value is None:
            continue
        literal = ast.literal_eval(value)
        if not isinstance(literal, str):
            raise BuildGateError(f"__version__ не строковой литерал: {literal!r}")
        found.append(literal)
    if len(found) != 1:
        raise BuildGateError(f"ожидался один литерал __version__, найдено {found!r}")
    return found[0]


def tree_state(repo_root: Path) -> list[str]:
    """Снимок состояния рабочего дерева по git status --porcelain."""
    output = run(["git", "status", "--porcelain"], cwd=repo_root)
    return sorted(line for line in output.splitlines() if line.strip())


def ensure_unchanged(repo_root: Path, before: list[str]) -> None:
    """Проверить, что сборка не изменила рабочее дерево."""
    after = tree_state(repo_root)
    appeared = [line for line in after if line not in before]
    disappeared = [line for line in before if line not in after]
    if appeared or disappeared:
        raise BuildGateError(
            "сборка изменила рабочее дерево; "
            f"появилось: {appeared}; исчезло: {disappeared}"
        )
    run(["git", "diff", "--check"], cwd=repo_root)


def source_copy(repo_root: Path, destination: Path) -> Path:
    """Создать одноразовую копию HEAD вне рабочего дерева."""
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination.parent / "head.tar"
    run(["git", "archive", "--format=tar", "-o", str(archive), "HEAD"], cwd=repo_root)
    root = destination.resolve()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            resolved = (destination / member.name).resolve()
            if resolved != root and root not in resolved.parents:
                raise BuildGateError(f"путь вне одноразовой копии: {member.name}")
        tar.extractall(destination, filter="data")
    archive.unlink()
    if not (destination / INIT_RELPATH).is_file():
        raise BuildGateError("одноразовая копия не содержит ожидаемых файлов")
    return destination


def header_values(message: Message, name: str) -> tuple[str, ...]:
    """Вернуть все значения metadata header как очищенные строки."""
    return tuple(str(value).strip() for value in message.get_all(name, []))


def wheel_facts(wheel_path: Path) -> WheelFacts:
    """Прочитать проверяемые поля и пути из wheel."""
    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
        wheel_meta = [name for name in names if name.endswith(".dist-info/WHEEL")]
        core_meta = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(wheel_meta) != 1 or len(core_meta) != 1:
            raise BuildGateError(
                f"ожидались ровно один WHEEL и METADATA: {wheel_path.name}"
            )

        generator = ""
        for line in archive.read(wheel_meta[0]).decode("utf-8").splitlines():
            if line.startswith("Generator:"):
                generator = line.split(":", 1)[1].strip()
                break

        message = message_from_bytes(archive.read(core_meta[0]))
        version_values = header_values(message, "Version")
        expression_values = header_values(message, "License-Expression")
        classifiers = tuple(
            value
            for value in header_values(message, "Classifier")
            if value.startswith("License ::")
        )
        metadata_dir = core_meta[0].rsplit("/", 1)[0]
        expected_path = f"{metadata_dir}/licenses/{EXPECTED_LICENSE_FILE}"
        license_paths = tuple(
            sorted(
                name
                for name in names
                if PurePosixPath(name).name == EXPECTED_LICENSE_FILE
            )
        )

    if len(version_values) != 1:
        raise BuildGateError(
            f"ожидалось одно поле Version, найдено {version_values!r}"
        )
    if len(expression_values) > 1:
        raise BuildGateError(
            "ожидалось не более одного License-Expression, "
            f"найдено {expression_values!r}"
        )
    return WheelFacts(
        generator=generator,
        version=version_values[0],
        license_expression=expression_values[0] if expression_values else "",
        license_files=header_values(message, "License-File"),
        legacy_licenses=header_values(message, "License"),
        license_classifiers=classifiers,
        license_paths=license_paths,
        expected_license_path=expected_path,
    )


def validate_license_contract(facts: WheelFacts) -> None:
    """Fail closed при любом отклонении wheel от ADR-86."""
    if facts.license_expression != EXPECTED_LICENSE_EXPRESSION:
        raise BuildGateError(
            "License-Expression "
            f"{facts.license_expression!r} != {EXPECTED_LICENSE_EXPRESSION!r}"
        )
    if facts.license_files != (EXPECTED_LICENSE_FILE,):
        raise BuildGateError(
            f"License-File {facts.license_files!r} != {(EXPECTED_LICENSE_FILE,)!r}"
        )
    if facts.legacy_licenses:
        raise BuildGateError(
            f"устаревшее поле License не удалено: {facts.legacy_licenses!r}"
        )
    if facts.license_classifiers:
        raise BuildGateError(
            f"license classifiers не удалены: {facts.license_classifiers!r}"
        )
    if facts.license_paths != (facts.expected_license_path,):
        raise BuildGateError(
            "LICENSE в wheel размещён неверно: "
            f"{facts.license_paths!r}; ожидался {(facts.expected_license_path,)!r}"
        )


def verify_sdist_license(sdist_path: Path) -> str:
    """Проверить единственный LICENSE в корне sdist."""
    with tarfile.open(sdist_path) as archive:
        license_members = [
            member
            for member in archive.getmembers()
            if PurePosixPath(member.name).name == EXPECTED_LICENSE_FILE
        ]
    if len(license_members) != 1:
        raise BuildGateError(
            "ожидался один LICENSE в sdist, найдено "
            f"{[member.name for member in license_members]!r}"
        )
    member = license_members[0]
    parts = PurePosixPath(member.name).parts
    if not member.isfile() or len(parts) != 2 or parts[1] != EXPECTED_LICENSE_FILE:
        raise BuildGateError(f"LICENSE не в корне sdist: {member.name!r}")
    return member.name


def ensure_no_license_warnings(transcript: str) -> None:
    """Запретить два целевых setuptools deprecation warning."""
    found = [marker for marker in DEPRECATED_LICENSE_MARKERS if marker in transcript]
    if found:
        raise BuildGateError(f"build output содержит license warnings: {found!r}")


def artifacts(outdir: Path) -> tuple[Path, Path]:
    """Проверить, что собран ровно один sdist и ровно один wheel."""
    wheels = sorted(outdir.glob("*.whl"))
    sdists = sorted(outdir.glob("*.tar.gz"))
    if len(wheels) != 1:
        raise BuildGateError(f"ожидался один wheel, найдено {[p.name for p in wheels]}")
    if len(sdists) != 1:
        raise BuildGateError(f"ожидался один sdist, найдено {[p.name for p in sdists]}")
    return sdists[0], wheels[0]


def verify_artifact_contents(source: Path, sdist: Path, wheel: Path) -> None:
    """Require package sources and typing marker; reject local/private artifacts."""
    required = {
        path.relative_to(source / "src").as_posix()
        for path in (source / "src" / IMPORT_NAME).rglob("*")
        if path.is_file() and (path.suffix == ".py" or path.name == "py.typed")
    }
    required.update({f"{IMPORT_NAME}/__init__.py", f"{IMPORT_NAME}/py.typed"})
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = {
            item.filename for item in archive.infolist() if not item.is_dir()
        }
    with tarfile.open(sdist) as archive:
        members = archive.getmembers()
        if any(not (item.isfile() or item.isdir()) for item in members):
            raise BuildGateError("sdist contains a link or special file")
        validate_artifact_paths({item.name.rstrip("/") for item in members})
        files = [item.name for item in members if item.isfile()]
        validate_artifact_paths(set(files))
        if any(len(PurePosixPath(name).parts) < 2 for name in files):
            raise BuildGateError("sdist member is outside its root directory")
        roots = {PurePosixPath(name).parts[0] for name in files}
        if len(roots) != 1:
            raise BuildGateError("sdist must have one root directory")
        sdist_names = {
            PurePosixPath(*PurePosixPath(name).parts[1:]).as_posix()
            for name in files
        }
    for names in (wheel_names, sdist_names):
        validate_artifact_paths(names)
    if not required <= wheel_names or not {
        "src/" + name for name in required
    } <= sdist_names:
        raise BuildGateError("artifact omits a package module or py.typed")
    if not {"pyproject.toml", "README.md", "LICENSE"} <= sdist_names:
        raise BuildGateError("sdist omits build metadata, README or license")



def verify_public_metadata(source: Path, sdist: Path, wheel: Path) -> None:
    """Check both distribution descriptions against canonical source metadata."""
    project = tomllib.loads((source / "pyproject.toml").read_text("utf-8"))["project"]
    if project.get("readme") != "README.md":
        raise BuildGateError("public long description must use README.md")
    readme = (source / "README.md").read_text("utf-8").rstrip("\n")
    expected = {
        "Name": project["name"],
        "Summary": project["description"],
        "Requires-Python": project["requires-python"],
        "Description-Content-Type": "text/markdown",
        "Keywords": ",".join(project["keywords"]),
    }
    urls = sorted(f"{name}, {url}" for name, url in project["urls"].items())
    payloads: list[bytes] = []
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise BuildGateError("expected one wheel public metadata document")
        payloads.append(archive.read(names[0]))
    with tarfile.open(sdist) as archive:
        members = [
            m for m in archive.getmembers()
            if len(PurePosixPath(m.name).parts) == 2
            and PurePosixPath(m.name).name == "PKG-INFO" and m.isfile()
        ]
        if len(members) != 1:
            raise BuildGateError("expected one root sdist public metadata document")
        handle = archive.extractfile(members[0])
        if handle is None:
            raise BuildGateError("cannot read sdist public metadata")
        with handle:
            payloads.append(handle.read())
    for raw in payloads:
        message = message_from_string(raw.decode("utf-8").replace("\r\n", "\n"))
        for name, value in expected.items():
            if header_values(message, name) != (value,):
                raise BuildGateError(f"public metadata mismatch: {name}")
        if sorted(header_values(message, "Project-URL")) != urls:
            raise BuildGateError("public metadata mismatch: Project-URL")
        classifiers = sorted(project["classifiers"])
        if sorted(header_values(message, "Classifier")) != classifiers:
            raise BuildGateError("public metadata mismatch: Classifier")
        payload = message.get_payload()
        if not isinstance(payload, str) or payload.rstrip("\n") != readme:
            raise BuildGateError("public metadata does not embed canonical README")


def validate_artifact_paths(names: set[str]) -> None:
    forbidden = {
        ".git", ".venv", ".agent-private", "__pycache__", ".pytest_cache",
        ".mypy_cache", ".ruff_cache",
    }
    for name in names:
        path = PurePosixPath(name)
        if (
            not path.parts or path.as_posix() != name
            or path.is_absolute() or ".." in path.parts or "\\" in name
            or ":" in path.parts[0]
            or any(part in forbidden for part in path.parts)
            or any(part == ".env" or part.startswith(".env.") for part in path.parts)
            or path.suffix in {".pyc", ".pyo", ".key", ".pem"}
        ):
            raise BuildGateError(f"forbidden artifact member: {name}")


INSTALLED_SMOKE = textwrap.dedent(
    r"""
    import importlib.metadata as md
    import json
    import sys
    from pathlib import Path
    from unittest.mock import patch

    def reject_network(event, args):
        if event in {"socket.__new__", "socket.connect", "socket.bind",
                     "socket.getaddrinfo"}:
            raise AssertionError("network access during installed smoke")

    sys.addaudithook(reject_network)
    import keyring
    def reject_keyring(*args, **kwargs):
        raise AssertionError("system keyring access during installed smoke")
    keyring.get_keyring = reject_keyring

    import privacy_gateway as pkg
    from privacy_gateway import GatewayConfig, PrivacyGateway
    from privacy_gateway.crypto import generate_key

    module = Path(pkg.__file__).resolve()
    assert Path(sys.prefix).resolve() in module.parents
    assert module.with_name("py.typed").is_file()
    entry = [item for item in md.distribution("privacy-gateway").entry_points
             if item.group == "console_scripts" and item.name == "pgw"]
    assert len(entry) == 1 and entry[0].value == "privacy_gateway.bootstrap:main"

    root = Path.cwd()
    config = root / "entities.yaml"
    config.write_text("enabled_regex_types: [EMAIL]\n", encoding="utf-8")
    # Exercise the installed doctor with a synthetic capability, never host keyring.
    import contextlib
    import io
    from privacy_gateway import doctor
    report = io.StringIO()
    saved_argv = sys.argv
    sys.argv = ["pgw", "doctor", "--entities-config", str(config)]
    try:
        with patch.object(doctor, "keyring_capability", return_value="OK"):
            with contextlib.redirect_stdout(report):
                try:
                    entry[0].load()()
                except SystemExit as result:
                    assert result.code == 0
                else:
                    raise AssertionError("installed console doctor did not exit")
    finally:
        sys.argv = saved_argv
    assert "CONFIG: OK\n" in report.getvalue()
    assert "DEPENDENCIES: OK\n" in report.getvalue()
    workspace = root / "workspace"
    workspace.mkdir()
    gateway = PrivacyGateway(
        GatewayConfig(entities_config_path=config, workspace_dir=workspace, strict=True)
    )
    original = "Contact user@example.com"
    key = generate_key()
    with patch("privacy_gateway.keystore.get_key", return_value=key), patch(
        "privacy_gateway.restore.get_all_keys", return_value=[key]
    ):
        prepared = gateway.prepare(original, correlation_id="installed-smoke")
        try:
            assert "user@example.com" not in prepared.text
            assert prepared.token_count > 0
            restored = gateway.restore(prepared.text, context=prepared.context)
            assert restored.text == original and restored.tokens_missing == 0
        finally:
            gateway.discard(prepared.context)
    assert not any(workspace.iterdir())
    print(json.dumps({"version": pkg.__version__,
                      "metadata": md.version("privacy-gateway"),
                      "module_file": str(module), "roundtrip": True,
                      "console_doctor": True}))
    """
)


def probe_clean_venv(
    artifact_path: Path, venv_dir: Path, repo_root: Path
) -> dict[str, str]:
    """Install one artifact, then exercise installed API and actual console entry."""
    run([sys.executable, "-m", "venv", str(venv_dir)])
    python = venv_python(venv_dir)
    run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", "--quiet", str(artifact_path)])
    run([str(python), "-m", "pip", "check"])
    smoke = venv_dir.parent / (venv_dir.name + "-smoke")
    smoke.mkdir()
    env = clean_python_environment()
    env.update(HOME=str(smoke), USERPROFILE=str(smoke),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    completed = subprocess.run(
        [str(python), "-I", "-B", "-X", "utf8", "-c", INSTALLED_SMOKE],
        cwd=smoke, env=env, text=True, encoding="utf-8",
        capture_output=True, timeout=60, check=False,
    )
    if completed.returncode:
        raise BuildGateError("installed API smoke failed: " + completed.stderr)
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    if not isinstance(payload, dict) or payload.get("roundtrip") is not True:
        raise BuildGateError("invalid installed smoke result")
    observed = {str(key): str(value) for key, value in payload.items()}
    module = Path(observed["module_file"]).resolve()
    if (
        repo_root.resolve() in module.parents
        or venv_dir.resolve() not in module.parents
    ):
        raise BuildGateError("module imported outside the clean environment")
    script = python.parent / ("pgw.exe" if sys.platform == "win32" else "pgw")
    for argument in ("--help", "--describe"):
        result = subprocess.run(
            [str(script), argument], cwd=smoke, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=30, check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise BuildGateError("installed console entry failed: " + argument)
        if argument == "--describe":
            catalog = json.loads(result.stdout)
            if catalog.get("kind") != "cli_catalog" or catalog.get("program") != "pgw":
                raise BuildGateError("installed CLI catalog mismatch")
    return observed


def build_normal(
    temp_root: Path,
    source: Path,
    build_pin: str,
    transcript: list[str],
) -> Path:
    """Обычная изолированная сборка: sdist, затем wheel из sdist."""
    frontend = temp_root / "frontend"
    run([sys.executable, "-m", "venv", str(frontend)])
    python = venv_python(frontend)
    run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", "--quiet", f"build=={build_pin}"])
    run([str(python), "-m", "pip", "--version"])
    show_build = "import build; print('build frontend:', build.__version__)"
    run([str(python), "-c", show_build])
    outdir = temp_root / "dist"
    run(
        [str(python), "-m", "build", "--outdir", str(outdir), str(source)],
        transcript=transcript,
    )
    return outdir


def build_boundary(
    temp_root: Path,
    source: Path,
    build_pin: str,
    setuptools_pin: str,
    transcript: list[str],
) -> Path:
    """Boundary-сборка: пин backend и --no-isolation."""
    buildenv = temp_root / "buildenv"
    run([sys.executable, "-m", "venv", str(buildenv)])
    python = venv_python(buildenv)
    run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--quiet",
            f"setuptools=={setuptools_pin}",
            "wheel",
            f"build=={build_pin}",
        ]
    )
    show_backend = "import setuptools; print('backend:', setuptools.__version__)"
    run([str(python), "-c", show_backend])
    outdir = temp_root / "dist"
    run(
        [
            str(python),
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(outdir),
            str(source),
        ],
        transcript=transcript,
    )
    return outdir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build gate для issues #85 и #86")
    parser.add_argument("--temp-root", required=True, help="каталог для артефактов")
    parser.add_argument("--build-pin", default=DEFAULT_BUILD_PIN, help="версия build")
    parser.add_argument(
        "--setuptools-pin",
        default="",
        help="пин backend для boundary-сборки с --no-isolation",
    )
    args = parser.parse_args(argv)
    temp_root_arg: str = args.temp_root
    build_pin: str = args.build_pin
    setuptools_pin: str = args.setuptools_pin

    repo_root = Path.cwd().resolve()
    init_path = repo_root / INIT_RELPATH
    if not init_path.is_file():
        raise BuildGateError(f"не найден {INIT_RELPATH}")

    mode = "boundary" if setuptools_pin else "normal"
    temp_root = Path(temp_root_arg).resolve() / f"pgw-build-{mode}"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True)

    expected = canonical_version(init_path)
    print(f"режим                : {mode}")
    print(f"canonical version    : {expected}")
    print(f"каталог артефактов   : {temp_root}")

    before_state = tree_state(repo_root)
    if before_state:
        print("предупреждение: дерево изменено до сборки не этим шагом:")
        for line in before_state:
            print(f"  {line}")
    source = source_copy(repo_root, temp_root / "src")
    if canonical_version(source / INIT_RELPATH) != expected:
        raise BuildGateError("версия в одноразовой копии не совпала с canonical")

    transcript: list[str] = []
    if setuptools_pin:
        outdir = build_boundary(
            temp_root, source, build_pin, setuptools_pin, transcript
        )
    else:
        outdir = build_normal(temp_root, source, build_pin, transcript)

    ensure_no_license_warnings("\n".join(transcript))
    sdist, wheel = artifacts(outdir)
    sdist_license = verify_sdist_license(sdist)
    verify_artifact_contents(source, sdist, wheel)
    verify_public_metadata(source, sdist, wheel)
    facts = wheel_facts(wheel)
    validate_license_contract(facts)

    print(f"sdist                : {sdist.name}")
    print(f"wheel                : {wheel.name}")
    print(f"WHEEL Generator      : {facts.generator}")
    print(f"METADATA Version     : {facts.version}")
    print(f"License-Expression   : {facts.license_expression}")
    print(f"License-File         : {list(facts.license_files)}")
    print(f"LICENSE в sdist      : {sdist_license}")
    print(f"LICENSE в wheel      : {facts.expected_license_path}")

    if facts.version != expected:
        raise BuildGateError(
            f"METADATA Version {facts.version!r} != canonical {expected!r}"
        )

    if setuptools_pin:
        if not facts.generator.startswith("setuptools"):
            raise BuildGateError(
                f"Generator {facts.generator!r} не указывает setuptools: wheel собран "
                "compat-слоем пакета wheel, нижняя граница не подтверждена"
            )
        if setuptools_pin not in facts.generator:
            raise BuildGateError(
                f"Generator {facts.generator!r} не соответствует пину {setuptools_pin}"
            )

    for label, artifact in (("wheel", wheel), ("sdist", sdist)):
        observed = probe_clean_venv(artifact, temp_root / ("clean-" + label), repo_root)
        print(f"{label} clean venv __version__: {observed['version']}")
        print(f"{label} clean venv metadata   : {observed['metadata']}")
        print(f"{label} clean venv module     : {observed['module_file']}")
        if observed["version"] != expected or observed["metadata"] != expected:
            raise BuildGateError(f"{label}: installed version does not match canonical")

    if not setuptools_pin:
        run([
            sys.executable, str(source / "tools" / "verify_installation_docs.py"),
            "--document", str(source / "docs" / "INSTALLATION.md"),
            "--temp-root", str(temp_root / "documented-installations"),
        ])

        source_sha = run(["git", "rev-parse", "HEAD"], cwd=repo_root).strip()
        run([
            sys.executable, str(source / "tools" / "verify_published_package.py"),
            "--fixture-source", str(source), "--fixture-dist", str(outdir),
            "--source-sha", source_sha,
            "--temp-root", str(temp_root / "post-publication-LOCAL-fixture"),
        ])

    ensure_unchanged(repo_root, before_state)
    print(
        f"build gate ({mode}) пройден: версия {expected}, "
        f"SPDX {facts.license_expression}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildGateError as error:
        print(f"BUILD GATE FAILED: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from error
