#!/usr/bin/env python3
"""Проверка новых секретов без мутации отслеживаемого .secrets.baseline.

Скрипт копирует .secrets.baseline во временный каталог вне рабочего дерева
и запускает detect-secrets-hook против копии. Отслеживаемый файл инструменту
не передаётся, поэтому его мутация невозможна конструктивно.

Отслеживаемый .secrets.baseline исключается из списка проверяемых файлов:
фильтр detect_secrets.filters.common.is_baseline_file самоисключает только
тот путь, который передан через --baseline, поэтому при работе с копией
хеши внутри baseline иначе распознаются как новые секреты.

Коды возврата:
    0 - новых секретов нет;
    1 - detect-secrets-hook сообщил о новых секретах;
    2 - нарушено предусловие запуска;
    3 - отслеживаемый .secrets.baseline изменился (нарушение инварианта).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

BASELINE_NAME = ".secrets.baseline"
EXCLUDED_DIRS = frozenset(
    {".git", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)
BATCH_SIZE = 100

EXIT_OK = 0
EXIT_SECRETS = 1
EXIT_PRECONDITION = 2
EXIT_INVARIANT = 3


def sha256_of(path: Path) -> str:
    """Возвращает hex-дайджест SHA-256 для файла."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_excluded(relative_path: str) -> bool:
    """Проверяет, нужно ли исключить путь из списка сканируемых файлов."""
    parts = relative_path.replace("\\", "/").split("/")
    if len(parts) == 1 and parts[0] == BASELINE_NAME:
        return True
    for part in parts:
        if part in EXCLUDED_DIRS or part.endswith(".egg-info"):
            return True
    return False


def select_files(tracked: Iterable[str]) -> list[str]:
    """Фильтрует и сортирует список отслеживаемых путей."""
    return sorted(name for name in tracked if name and not is_excluded(name))


def batched(items: Sequence[str], size: int) -> Iterator[list[str]]:
    """Разбивает список на батчи, чтобы не превысить лимит командной строки."""
    if size < 1:
        raise ValueError("size must be >= 1")
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def tracked_files(root: Path) -> list[str]:
    """Возвращает пути файлов, отслеживаемых git."""
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in completed.stdout.split("\0") if name]


def run_hook(baseline: Path, files: Sequence[str], root: Path) -> int:
    """Запускает detect-secrets-hook против копии baseline."""
    worst = 0
    for batch in batched(files, BATCH_SIZE):
        completed = subprocess.run(
            ["detect-secrets-hook", "--baseline", str(baseline), *batch],
            cwd=str(root),
        )
        worst = max(worst, completed.returncode)
    return worst


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Проверка secret-drift без мутации .secrets.baseline",
    )
    parser.add_argument(
        "--temp-root",
        default=None,
        help="Каталог для временной копии baseline (по умолчанию системный).",
    )
    args = parser.parse_args(argv)

    root = Path.cwd()
    if not (root / ".git").exists():
        print(f"ОШИБКА: {root} не является корнем git-репозитория.", file=sys.stderr)
        return EXIT_PRECONDITION

    baseline = root / BASELINE_NAME
    if not baseline.is_file():
        print(f"ОШИБКА: не найден {BASELINE_NAME} в {root}.", file=sys.stderr)
        return EXIT_PRECONDITION

    if shutil.which("detect-secrets-hook") is None:
        print("ОШИБКА: detect-secrets-hook не найден в PATH.", file=sys.stderr)
        return EXIT_PRECONDITION

    temp_root: Path | None = None
    if args.temp_root:
        temp_root = Path(args.temp_root)
        if not temp_root.is_dir():
            print(f"ОШИБКА: каталог {temp_root} не существует.", file=sys.stderr)
            return EXIT_PRECONDITION

    try:
        tracked = tracked_files(root)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"ОШИБКА: не удалось получить git ls-files: {error}", file=sys.stderr)
        return EXIT_PRECONDITION

    files = select_files(tracked)
    if not files:
        print("ОШИБКА: список файлов для проверки пуст.", file=sys.stderr)
        return EXIT_PRECONDITION

    digest_before = sha256_of(baseline)

    with tempfile.TemporaryDirectory(
        dir=str(temp_root) if temp_root else None
    ) as temp_dir:
        baseline_copy = Path(temp_dir) / "secrets.baseline.copy"
        shutil.copy2(baseline, baseline_copy)
        hook_code = run_hook(baseline_copy, files, root)

    digest_after = sha256_of(baseline)
    if digest_before != digest_after:
        print(
            f"ОШИБКА ИНВАРИАНТА: {BASELINE_NAME} изменился во время проверки.",
            file=sys.stderr,
        )
        return EXIT_INVARIANT

    if hook_code != 0:
        print(
            "НАЙДЕНЫ новые секреты вне baseline "
            f"(detect-secrets-hook exit code {hook_code}).",
            file=sys.stderr,
        )
        return EXIT_SECRETS

    print(
        f"OK: новых секретов нет, проверено файлов: {len(files)}, "
        f"{BASELINE_NAME} не изменён."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
