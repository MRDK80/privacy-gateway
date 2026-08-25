"""Автоматические проверки документационного CLI-контракта (#47, ADR-47).

Инварианты:

- набор подкоманд ``pgw key`` равен ``{create, status, rotate}``;
- ``pgw key delete`` не существует и даёт usage error с кодом 3;
- повторный ``pgw key create`` завершается кодом 3;
- строгий отказ ``pgw restore`` завершается кодом 5;
- активные документы не обещают CLI-команду ``pgw key delete``.

Тесты характеризуют ``main()`` через ``sys.argv`` и проверяют семантику
активных документов, а не полные markdown-строки. Реальный системный keyring
не используется: keystore-вызовы подменяются.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway.cli import main
from privacy_gateway.keystore import KeyExistsError
from privacy_gateway.models import RestoreStrictError

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_KEY_SUBCOMMANDS = frozenset({"create", "status", "rotate"})
USAGE_ERROR_EXIT = 3
DUPLICATE_CREATE_EXIT = 3
STRICT_RESTORE_EXIT = 5

ACTIVE_DOCS = (
    "README.md",
    "SECURITY.md",
    "docs/SECURITY.md",
    "docs/ARCHITECTURE.md",
    "docs/LIBRARY_API.md",
)


def _run(*args: str) -> int:
    """Запустить CLI через main() с подменой sys.argv и вернуть код завершения."""
    with (
        patch.object(sys, "argv", ["pgw", *args]),
        pytest.raises(SystemExit) as excinfo,
    ):
        main()
    code = excinfo.value.code
    return 0 if code is None else int(code)


def _key_subcommands(capsys: pytest.CaptureFixture[str]) -> frozenset[str]:
    """Набор подкоманд, фактически объявленный в help группы pgw key."""
    assert _run("key", "--help") == 0
    out = capsys.readouterr().out
    match = re.search(r"\{([a-z,]+)\}", out)
    assert match is not None, f"help не содержит списка подкоманд: {out!r}"
    return frozenset(match.group(1).split(","))


def test_key_help_lists_documented_subcommands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Help перечисляет ровно документированный набор подкоманд."""
    assert _key_subcommands(capsys) == EXPECTED_KEY_SUBCOMMANDS


def test_key_delete_is_not_a_cli_command(capsys: pytest.CaptureFixture[str]) -> None:
    """pgw key delete отсутствует и обслуживается usage-error контрактом."""
    assert "delete" not in _key_subcommands(capsys)
    capsys.readouterr()
    code = _run("key", "delete")
    captured = capsys.readouterr()
    assert code == USAGE_ERROR_EXIT
    assert captured.out == ""
    assert "invalid choice" in captured.err


def test_duplicate_create_exit_code_matches_docs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Повторный pgw key create завершается кодом 3 без пустого stderr."""
    with patch(
        "privacy_gateway.keystore.create_key",
        side_effect=KeyExistsError("Ключ уже существует."),
    ):
        code = _run("key", "create")
    captured = capsys.readouterr()
    assert code == DUPLICATE_CREATE_EXIT
    assert captured.err.strip()
    assert captured.out == ""


def test_strict_restore_exit_code_matches_docs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Строгий отказ по токенам завершается выделенным кодом 5 (ADR-21)."""
    reply = tmp_path / "reply.txt"
    reply.write_text("[EMAIL_1]", encoding="utf-8")
    with patch(
        "privacy_gateway.restore.restore_text",
        side_effect=RestoreStrictError("строгий отказ"),
    ):
        code = _run("restore", str(reply), "--route", str(tmp_path / "route.json"))
    captured = capsys.readouterr()
    assert code == STRICT_RESTORE_EXIT
    assert captured.out == ""


@pytest.mark.parametrize("relative_path", ACTIVE_DOCS)
def test_active_docs_do_not_promise_key_delete(relative_path: str) -> None:
    """Активные документы не обещают CLI-команду pgw key delete."""
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert "pgw key delete" not in text, relative_path


def test_active_docs_mention_every_shipped_key_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """README упоминает каждую поставленную подкоманду key."""
    subcommands = _key_subcommands(capsys)
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for name in sorted(subcommands):
        assert name in readme, name


def test_security_doc_documents_strict_code_five() -> None:
    """docs/SECURITY.md описывает строгий отказ кодом 5, а не 3."""
    text = (REPO_ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")
    assert "вызывает отказ с кодом 3" not in text
    assert "кодом 5" in text
