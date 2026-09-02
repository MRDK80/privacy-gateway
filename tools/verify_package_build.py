#!/usr/bin/env python3
"""Build gate: проверка версии и PEP 639 metadata (issues #85, #86).

Инструмент собирает sdist и wheel из одноразовой копии текущего HEAD,
устанавливает wheel в чистое виртуальное окружение и проверяет:

* единственный источник версии из ADR-81;
* SPDX license expression и license file из ADR-86;
* отсутствие устаревших license metadata и целевых warnings;
* корректное размещение LICENSE в sdist и wheel;
* фактический setuptools Generator в boundary-режиме;
* отсутствие изменений tracked checkout.

Все артефакты создаются только внутри каталога, переданного в --temp-root.
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from email import message_from_bytes
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
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
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


def probe_clean_venv(
    wheel_path: Path, venv_dir: Path, repo_root: Path
) -> dict[str, str]:
    """Установить wheel в чистое venv и вернуть наблюдаемые версии."""
    run([sys.executable, "-m", "venv", str(venv_dir)])
    python = venv_python(venv_dir)
    run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", "--quiet", str(wheel_path)])
    code = (
        "import importlib.metadata as md, json, privacy_gateway as pkg;"
        "print(json.dumps({"
        "'version': pkg.__version__,"
        f"'metadata': md.version({DIST_NAME!r}),"
        "'module_file': pkg.__file__}))"
    )
    payload = json.loads(run([str(python), "-c", code]).strip().splitlines()[-1])
    if not isinstance(payload, dict):
        raise BuildGateError("неожиданный формат ответа проверки")
    observed = {str(key): str(value) for key, value in payload.items()}
    if str(repo_root) in observed["module_file"]:
        raise BuildGateError("модуль импортирован из checkout, а не из чистого venv")
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

    observed = probe_clean_venv(wheel, temp_root / "clean", repo_root)
    print(f"clean venv __version__: {observed['version']}")
    print(f"clean venv metadata   : {observed['metadata']}")
    print(f"clean venv module     : {observed['module_file']}")
    if observed["version"] != expected:
        raise BuildGateError(f"__version__ {observed['version']!r} != {expected!r}")
    if observed["metadata"] != expected:
        raise BuildGateError(f"metadata {observed['metadata']!r} != {expected!r}")

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
