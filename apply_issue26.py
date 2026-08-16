#!/usr/bin/env python3
"""Автопатч для privacy-gateway #26 (ADR-29): usage error argparse -> код 3.

Запуск из корня репозитория на ветке fix/issue-26-argparse-exit-code:

    python apply_issue26.py            # применить
    python apply_issue26.py --dry-run  # только показать, что будет изменено

Скрипт правит:
  1. tests/test_cli_characterization.py — заменяет старый
     test_argparse_error_exits_with_code_2 на набор тестов нового контракта,
     добавляет import subprocess, обновляет докстринг модуля;
  2. docs/DECISIONS.md — добавляет ADR-29 в конец файла;
  3. CHANGELOG.md — запись в [Unreleased];
  4. src/privacy_gateway/cli.py — докстринг кодов завершения;
  5. README.md, docs/ARCHITECTURE.md, docs/token-format.md — строки таблиц
     кодов 2 и 3 (если шаблон не найден, скрипт сообщает и не трогает файл).

Ничего не перезаписывается вслепую: каждый шаг ищет точный якорь и
пропускается с предупреждением, если якорь отсутствует или патч уже применён.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path.cwd()
DRY_RUN = False
CHANGES: list[str] = []
SKIPPED: list[str] = []


def read(rel: str) -> str | None:
    path = ROOT / rel
    if not path.exists():
        SKIPPED.append(f"{rel}: файл не найден")
        return None
    return path.read_text(encoding="utf-8")


def write(rel: str, text: str, note: str) -> None:
    if DRY_RUN:
        CHANGES.append(f"[dry-run] {rel}: {note}")
        return
    (ROOT / rel).write_text(text, encoding="utf-8")
    CHANGES.append(f"{rel}: {note}")


# ---------------------------------------------------------------------------
# 1. tests/test_cli_characterization.py
# ---------------------------------------------------------------------------

NEW_TESTS = '''# ---------------------------------------------------------------------------
# argparse: usage error -> код 3 (#26, ADR-29)
# ---------------------------------------------------------------------------


_USAGE_ERROR_CASES = [
    pytest.param(("prepare",), id="missing-positional"),
    pytest.param(("restore", "REPLY"), id="missing-required-option"),
    pytest.param(("--unknown-option",), id="unknown-option"),
    pytest.param(("unknown-command",), id="unknown-command"),
]


@pytest.mark.parametrize("argv", _USAGE_ERROR_CASES)
def test_argparse_usage_error_exits_with_code_3(
    argv: tuple[str, ...],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ошибка разбора argv завершает CLI кодом 3, а не 2 (#26, ADR-29).

    Осознанная смена контракта: код 2 остаётся только за PENDING.
    """
    resolved = tuple(
        str(_input_file(tmp_path, SYNTH_LLM_REPLY)) if item == "REPLY" else item
        for item in argv
    )

    code = _run(*resolved)

    captured = capsys.readouterr()
    assert code == 3
    assert captured.out == ""
    assert captured.err.startswith("usage: pgw")
    assert "error:" in captured.err


def test_argparse_usage_error_stderr_is_byte_identical(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Переопределяется только код завершения: stderr argparse не меняется."""
    from privacy_gateway.cli import _build_parser

    with patch.object(sys, "argv", ["pgw", "prepare"]):
        parser = _build_parser()
        with pytest.raises(SystemExit) as raw_exc:
            parser.parse_args(["prepare"])
    raw_err = capsys.readouterr().err
    assert _exit_code(raw_exc.value) == 2

    code = _run("prepare")
    cli_err = capsys.readouterr().err

    assert code == 3
    assert cli_err == raw_err


def test_argparse_usage_error_code_3_in_subprocess(tmp_path: Path) -> None:
    """Код 3 виден вызывающему процессу, а не только через SystemExit."""
    proc = subprocess.run(
        [sys.executable, "-m", "privacy_gateway.cli", "prepare"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert proc.returncode == 3
    assert proc.stdout == ""
    assert "usage:" in proc.stderr


def test_help_exits_with_code_0(capsys: pytest.CaptureFixture[str]) -> None:
    """``--help`` не затронут перехватом: код 0 (страховка от широкого catch)."""
    code = _run("--help")

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
'''

OLD_DOC_BULLET = (
    "  - argparse завершает ошибку разбора кодом 2, "
    "совпадающим с PENDING (#26);\n"
)
DOC_ANCHOR = (
    "    хотя в описании #25 ожидался код 1 — расхождение зафиксировано тестом.\n"
)
DOC_ADDITION = (
    "\nИсправленный контракт:\n"
    "  - ошибка разбора argv завершает CLI кодом 3, код 2 остаётся за PENDING\n"
    "    (#26, ADR-29).\n"
)


def patch_tests() -> None:
    rel = "tests/test_cli_characterization.py"
    text = read(rel)
    if text is None:
        return
    if "test_argparse_usage_error_exits_with_code_3" in text:
        SKIPPED.append(f"{rel}: тесты нового контракта уже на месте")
        return

    lines = text.splitlines(keepends=True)

    header_idx = next(
        (i for i, ln in enumerate(lines) if "argparse: код 2 совпадает" in ln),
        None,
    )
    func_idx = next(
        (
            i
            for i, ln in enumerate(lines)
            if ln.startswith("def test_argparse_error_exits_with_code_2")
        ),
        None,
    )
    if header_idx is None or func_idx is None:
        SKIPPED.append(f"{rel}: не найден старый argparse-тест, правьте вручную")
        return

    start = header_idx
    if start > 0 and lines[start - 1].startswith("# ---"):
        start -= 1

    end = len(lines)
    for i in range(func_idx + 1, len(lines)):
        ln = lines[i]
        if ln.startswith(("def ", "@pytest", "class ", "# ---")):
            end = i
            break

    tail = "".join(lines[end:])
    new_text = "".join(lines[:start]) + NEW_TESTS
    if tail:
        new_text += "\n\n" + tail.lstrip("\n")

    if "import subprocess\n" not in new_text:
        new_text = new_text.replace("import re\n", "import re\nimport subprocess\n", 1)

    new_text = new_text.replace(OLD_DOC_BULLET, "", 1)
    if "Исправленный контракт:" not in new_text:
        new_text = new_text.replace(DOC_ANCHOR, DOC_ANCHOR + DOC_ADDITION, 1)

    write(rel, new_text, "argparse-секция заменена, добавлен import subprocess")


# ---------------------------------------------------------------------------
# 2. docs/DECISIONS.md — ADR-29
# ---------------------------------------------------------------------------

ADR_29 = """
---

## ADR-29: Ошибка использования CLI отображается в код 3  [#26]

**Контекст:** ADR-20 и ADR-21 определяют коды результатов обработки, где код 2
означает PENDING — требуется ручное одобрение. argparse по умолчанию завершает
процесс кодом 2 при синтаксической ошибке вызова. Внешний вызывающий код не мог
отличить корректно обработанный PENDING от неверно вызванной команды.

**Решение:** синтаксическая ошибка разбора командной строки отображается в код
3 — тот же код, что и ошибки входных данных, конфигурации и целостности. Код 2
резервируется исключительно за PENDING. Перехват выполняется только на границе
первичного разбора argv (`_parse_args` в `cli.py`): argparse к этому моменту уже
напечатал usage/error, поэтому stderr не изменяется побайтово. `--help` и help
подкоманд сохраняют код 0.

**Отвергнутые варианты:**

- только задокументировать совпадение кодов — двусмысленность сохраняется, а
  именно она и была причиной задачи;
- перенести PENDING на другой код — ломает контракт успешной обработки для
  потребителей 0.3.0 сильнее, чем правка usage error;
- подкласс `ArgumentParser` с переопределением `error()` — шире по охвату,
  требует доказательства распространения на все subparsers и рискует изменить
  формат stderr.

**Последствия:** изменение наблюдаемого поведения CLI, зафиксировано в
CHANGELOG. Команды, флаги и коды 0/1/4/5 не изменились. Расхождение `pgw key`
без подкоманды (код 0 через поздний help) остаётся предметом #30, ошибка записи
restore (код 1) — предметом #28.

**Статус:** принят. Уточняет ADR-20 и ADR-21, не заменяет их.
"""


def patch_decisions() -> None:
    rel = "docs/DECISIONS.md"
    text = read(rel)
    if text is None:
        return
    if "ADR-29" in text:
        SKIPPED.append(f"{rel}: ADR-29 уже присутствует")
        return
    if "ADR-28" not in text:
        SKIPPED.append(f"{rel}: не найден ADR-28, проверьте нумерацию вручную")
        return
    write(rel, text.rstrip("\n") + "\n" + ADR_29, "добавлен ADR-29")


# ---------------------------------------------------------------------------
# 3. CHANGELOG.md
# ---------------------------------------------------------------------------

CHANGELOG_ENTRY = (
    "- **Изменено (поведение CLI):** ошибка разбора аргументов командной строки\n"
    "  завершает процесс кодом 3 вместо 2; код 2 закреплён только за PENDING\n"
    "  (#26, ADR-29). Текст usage/error в stderr, команды, флаги и остальные\n"
    "  коды завершения не изменились; `--help` по-прежнему даёт код 0.\n"
)


def patch_changelog() -> None:
    rel = "CHANGELOG.md"
    text = read(rel)
    if text is None:
        return
    if "ADR-29" in text:
        SKIPPED.append(f"{rel}: запись про #26 уже есть")
        return
    match = re.search(r"^##\s*\[Unreleased\].*$", text, flags=re.MULTILINE)
    if match is None:
        SKIPPED.append(f"{rel}: секция [Unreleased] не найдена, добавьте вручную")
        return
    idx = match.end()
    new_text = text[:idx] + "\n\n" + CHANGELOG_ENTRY.rstrip("\n") + text[idx:]
    write(rel, new_text, "добавлена запись в [Unreleased]")


# ---------------------------------------------------------------------------
# 4-5. cli.py докстринг и таблицы кодов
# ---------------------------------------------------------------------------


def patch_cli_docstring() -> None:
    rel = "src/privacy_gateway/cli.py"
    text = read(rel)
    if text is None:
        return
    old = "Коды завершения (ADR-20, ADR-21):"
    if "ADR-29" in text and old not in text:
        SKIPPED.append(f"{rel}: докстринг уже обновлён")
        return
    if old not in text:
        SKIPPED.append(f"{rel}: строка кодов завершения не найдена")
        return
    new_text = text.replace(old, "Коды завершения (ADR-20, ADR-21, ADR-29):", 1)
    write(rel, new_text, "докстринг кодов завершения ссылается на ADR-29")


TABLE_FILES = ("README.md", "docs/ARCHITECTURE.md")


def patch_code_tables() -> None:
    for rel in TABLE_FILES:
        text = read(rel)
        if text is None:
            continue
        if "ADR-29" in text:
            SKIPPED.append(f"{rel}: таблица уже упоминает ADR-29")
            continue

        lines = text.splitlines(keepends=True)
        touched = False
        for i, ln in enumerate(lines):
            stripped = ln.strip()
            if not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) < 2:
                continue
            if cells[0] == "3" and "ADR-29" not in ln:
                lines[i] = ln.rstrip("\n").rstrip()
                lines[i] = (
                    lines[i][: lines[i].rfind("|")].rstrip()
                    + "; ошибка использования CLI (ADR-29) |\n"
                )
                touched = True
            elif cells[0] == "2" and "PENDING" in ln and "только" not in ln:
                lines[i] = ln.replace("PENDING", "PENDING (только PENDING)", 1)
                touched = True

        if touched:
            write(rel, "".join(lines), "таблица кодов 2 и 3 уточнена")
        else:
            SKIPPED.append(f"{rel}: строки таблицы с кодами 2/3 не распознаны")


def main() -> int:
    global DRY_RUN
    parser = argparse.ArgumentParser(description="Патч #26 / ADR-29")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    if not (ROOT / "src" / "privacy_gateway" / "cli.py").exists():
        print("Запускайте из корня privacy-gateway", file=sys.stderr)
        return 1

    patch_tests()
    patch_decisions()
    patch_changelog()
    patch_cli_docstring()
    patch_code_tables()

    print("Изменено:")
    for item in CHANGES or ["(нет изменений)"]:
        print(f"  + {item}")
    if SKIPPED:
        print("Пропущено (проверьте вручную):")
        for item in SKIPPED:
            print(f"  ! {item}")
    print("\nДалее: pytest -q && ruff check . && mypy . && pre-commit run --all-files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
