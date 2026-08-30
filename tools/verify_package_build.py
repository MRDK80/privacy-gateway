#!/usr/bin/env python3
"""Build gate: сборка дистрибутива и сверка версии (issue #85, ADR-81).

Инструмент собирает sdist и wheel из одноразовой копии текущего HEAD,
устанавливает wheel в чистое виртуальное окружение и сверяет версию из
метаданных с каноническим литералом ``__version__``.

Все артефакты создаются только внутри каталога, переданного в --temp-root,
поэтому рабочее дерево репозитория не изменяется.

Режимы:
  * без --setuptools-pin: обычная изолированная сборка (default python -m build,
    то есть sdist, затем wheel из sdist);
  * с --setuptools-pin: boundary-сборка в отдельном окружении с пином backend и
    флагом --no-isolation, с проверкой поля Generator в метаданных wheel.
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
from pathlib import Path

DIST_NAME = "privacy-gateway"
IMPORT_NAME = "privacy_gateway"
INIT_RELPATH = Path("src") / IMPORT_NAME / "__init__.py"
DEFAULT_BUILD_PIN = "1.6.0"


class BuildGateError(RuntimeError):
    """Нарушен инвариант build gate."""


def run(cmd: list[str], cwd: Path | None = None) -> str:
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
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
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


def assert_clean(repo_root: Path, stage: str) -> None:
    """Проверить, что рабочее дерево не изменено."""
    status = run(["git", "status", "--porcelain"], cwd=repo_root).strip()
    if status:
        raise BuildGateError(f"{stage}: рабочее дерево изменено\n{status}")
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


def wheel_facts(wheel_path: Path) -> tuple[str, str]:
    """Вернуть Generator из WHEEL и Version из METADATA."""
    generator = ""
    version = ""
    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
        wheel_meta = [name for name in names if name.endswith(".dist-info/WHEEL")]
        core_meta = [name for name in names if name.endswith(".dist-info/METADATA")]
        if not wheel_meta or not core_meta:
            raise BuildGateError(f"в wheel нет WHEEL или METADATA: {wheel_path.name}")
        for line in archive.read(wheel_meta[0]).decode("utf-8").splitlines():
            if line.startswith("Generator:"):
                generator = line.split(":", 1)[1].strip()
        for line in archive.read(core_meta[0]).decode("utf-8").splitlines():
            if line.startswith("Version:"):
                version = line.split(":", 1)[1].strip()
                break
    if not version:
        raise BuildGateError(f"в METADATA нет поля Version: {wheel_path.name}")
    return generator, version


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
    """Установить wheel в чистое venv и вернуть наблюдаемые значения версии."""
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


def build_normal(temp_root: Path, source: Path, build_pin: str) -> Path:
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
    run([str(python), "-m", "build", "--outdir", str(outdir), str(source)])
    return outdir


def build_boundary(
    temp_root: Path, source: Path, build_pin: str, setuptools_pin: str
) -> Path:
    """Boundary-сборка: пин backend в отдельном venv и --no-isolation."""
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
        ]
    )
    return outdir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build gate для issue #85")
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

    assert_clean(repo_root, "до сборки")
    source = source_copy(repo_root, temp_root / "src")
    if canonical_version(source / INIT_RELPATH) != expected:
        raise BuildGateError("версия в одноразовой копии не совпала с canonical")

    if setuptools_pin:
        outdir = build_boundary(temp_root, source, build_pin, setuptools_pin)
    else:
        outdir = build_normal(temp_root, source, build_pin)

    sdist, wheel = artifacts(outdir)
    generator, metadata_version = wheel_facts(wheel)
    print(f"sdist                : {sdist.name}")
    print(f"wheel                : {wheel.name}")
    print(f"WHEEL Generator      : {generator}")
    print(f"METADATA Version     : {metadata_version}")
    if metadata_version != expected:
        raise BuildGateError(
            f"METADATA Version {metadata_version!r} != canonical {expected!r}"
        )

    if setuptools_pin:
        if not generator.startswith("setuptools"):
            raise BuildGateError(
                f"Generator {generator!r} не указывает setuptools: wheel собран "
                "compat-слоем пакета wheel, нижняя граница не подтверждена"
            )
        if setuptools_pin not in generator:
            raise BuildGateError(
                f"Generator {generator!r} не соответствует пину {setuptools_pin}"
            )

    observed = probe_clean_venv(wheel, temp_root / "clean", repo_root)
    print(f"clean venv __version__: {observed['version']}")
    print(f"clean venv metadata   : {observed['metadata']}")
    print(f"clean venv module     : {observed['module_file']}")
    if observed["version"] != expected:
        raise BuildGateError(f"__version__ {observed['version']!r} != {expected!r}")
    if observed["metadata"] != expected:
        raise BuildGateError(f"metadata {observed['metadata']!r} != {expected!r}")

    assert_clean(repo_root, "после сборки")
    print(f"build gate ({mode}) пройден: версия {expected} согласована")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildGateError as error:
        print(f"BUILD GATE FAILED: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from error
