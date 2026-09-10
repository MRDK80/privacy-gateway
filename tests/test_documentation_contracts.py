"""Автоматические проверки документационного CLI-контракта (#47, ADR-47).

Инварианты:

- набор подкоманд ``pgw key`` равен ``{create, status, rotate}``;
- ``pgw key delete`` не существует и даёт usage error с кодом 3;
- повторный ``pgw key create`` завершается кодом 3;
- строгий отказ ``pgw restore`` завершается кодом 5;
- активные документы не обещают CLI-команду ``pgw key delete``;
- документ примера ротации согласован с фактическим CLI-контрактом;
- документ примера ротации не обещает окно retention глубже
  ``[active, retired]``;
- ``docs/LIBRARY_API.md`` ссылается на канонический индекс примеров
  ``examples/README.md``.

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
ROTATION_EXAMPLE = Path("examples") / "05_key_rotation.md"
LIBRARY_API_DOC = Path("docs") / "LIBRARY_API.md"
EXAMPLES_INDEX_LINK = "(../examples/README.md)"
AGENTS_DOC = Path("AGENTS.md")
AGENTS_MAX_LINES = 500
AGENTS_GATE_COMMANDS = (
    "pytest -q",
    "ruff check .",
    "mypy .",
    "pre-commit run --all-files",
    "git diff --check",
)
AGENTS_PR_DIRECTIONS = (
    "<task-branch> -> roadmap/<roadmap-issue>-<slug>",
    "roadmap/<roadmap-issue>-<slug> -> main",
)
AGENTS_CANONICAL_DOC_LINKS = (
    "(SECURITY.md)",
    "(docs/SECURITY.md)",
    "(docs/ARCHITECTURE.md)",
    "(docs/LIBRARY_API.md)",
)

ACTIVE_DOCS = (
    "AGENTS.md",
    "README.md",
    "SECURITY.md",
    "docs/SECURITY.md",
    "docs/ARCHITECTURE.md",
    "docs/LIBRARY_API.md",
    "examples/05_key_rotation.md",
    "docs/article-sync-31.md",
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


def _rotation_example_text() -> str:
    """Текст документа примера ротации ключа."""
    return (REPO_ROOT / ROTATION_EXAMPLE).read_text(encoding="utf-8")


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


def test_rotation_example_mentions_every_shipped_key_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Пример ротации упоминает каждую поставленную подкоманду pgw key."""
    subcommands = _key_subcommands(capsys)
    text = _rotation_example_text()
    for name in sorted(subcommands):
        assert f"pgw key {name}" in text, name


def test_rotation_example_states_bounded_retention() -> None:
    """Пример фиксирует окно [active, retired] и ссылается на ADR."""
    text = _rotation_example_text()
    assert "[active, retired]" in text
    assert "ADR-23" in text
    assert "ADR-46" in text


def test_rotation_example_separates_api_and_physical_retention() -> None:
    """Пример разделяет API retention и physical retention."""
    text = _rotation_example_text()
    assert "API retention" in text
    assert "Physical retention" in text


def test_rotation_example_does_not_claim_transactional_rollback() -> None:
    """Отказ verification/prune не описан как транзакционный rollback."""
    text = _rotation_example_text()
    assert "не является транзакцией keyring" in text
    assert "не следует называть rollback" in text


def test_library_api_links_to_examples_index() -> None:
    """Library API ведёт к каноническому индексу исполняемых примеров."""
    document = REPO_ROOT / LIBRARY_API_DOC
    text = document.read_text(encoding="utf-8")
    assert EXAMPLES_INDEX_LINK in text
    target = (document.parent / "../examples/README.md").resolve()
    assert target == (REPO_ROOT / "examples" / "README.md").resolve()
    assert target.is_file()


def test_library_api_does_not_duplicate_example_commands() -> None:
    """Library API не дублирует команды запуска примеров."""
    text = (REPO_ROOT / LIBRARY_API_DOC).read_text(encoding="utf-8")
    assert "python examples/" not in text


ARTICLE_SYNC_REPORT = Path("docs") / "article-sync-31.md"
EXAMPLES_REVISION_SHA = (
    "b88259c4d08fa37190b37d7b0c66a33aaf1e75db"  # pragma: allowlist secret
)
STALE_ARTICLE_SHA = (
    "4685779ea71ec38049317bb49589db542f7728bd"  # pragma: allowlist secret
)
NER_BOUNDARY_STATEMENT = "NER не реализован"


def _article_sync_report_text() -> str:
    """Текст отчёта синхронизации статьи с примерами."""
    return (REPO_ROOT / ARTICLE_SYNC_REPORT).read_text(encoding="utf-8")


def test_article_sync_report_pins_examples_revision() -> None:
    """Отчёт закрепляет полный commit SHA ревизии примеров."""
    assert EXAMPLES_REVISION_SHA in _article_sync_report_text()


def test_article_sync_report_links_examples_index() -> None:
    """Отчёт ведёт к каноническому индексу исполняемых примеров."""
    document = REPO_ROOT / ARTICLE_SYNC_REPORT
    assert EXAMPLES_INDEX_LINK in document.read_text(encoding="utf-8")
    target = (document.parent / "../examples/README.md").resolve()
    assert target == (REPO_ROOT / "examples" / "README.md").resolve()
    assert target.is_file()


def test_article_sync_report_states_detection_boundary() -> None:
    """Отчёт явно фиксирует границу детекции без обещания NER."""
    assert NER_BOUNDARY_STATEMENT in _article_sync_report_text()


def test_article_sync_report_has_no_stale_revision() -> None:
    """Отчёт не ссылается на устаревшую ревизию черновика статьи."""
    assert STALE_ARTICLE_SHA not in _article_sync_report_text()


def _agents_text() -> str:
    """Текст корневого AGENTS.md."""
    return (REPO_ROOT / AGENTS_DOC).read_text(encoding="utf-8")


def test_agents_doc_exists_in_repository_root() -> None:
    """AGENTS.md лежит в корне репозитория."""
    assert (REPO_ROOT / AGENTS_DOC).is_file()


def test_agents_doc_within_line_budget() -> None:
    """AGENTS.md не превышает согласованный максимальный объём."""
    actual = len(_agents_text().splitlines())
    assert actual <= AGENTS_MAX_LINES, actual


def test_agents_doc_lists_mandatory_gate_commands() -> None:
    """AGENTS.md перечисляет обязательные команды локального gate."""
    text = _agents_text()
    for command in AGENTS_GATE_COMMANDS:
        assert command in text, command


def test_agents_doc_states_allowed_pull_request_directions() -> None:
    """AGENTS.md фиксирует разрешённые направления pull request."""
    text = _agents_text()
    for direction in AGENTS_PR_DIRECTIONS:
        assert direction in text, direction


def test_agents_doc_forbids_task_pull_request_into_main() -> None:
    """AGENTS.md запрещает task PR напрямую в main."""
    collapsed = re.sub(r"[ \t]+", " ", _agents_text())
    assert "<task-branch> -> main # запрещено" in collapsed


def test_agents_doc_marks_key_deletion_as_library_only() -> None:
    """AGENTS.md отделяет library-only удаление ключа от CLI-подкоманд."""
    text = _agents_text()
    assert "keystore.delete_key()" in text
    assert "library-only" in text
    for name in sorted(EXPECTED_KEY_SUBCOMMANDS):
        assert f"pgw key {name}" in text, name


def test_agents_doc_links_canonical_security_and_architecture_docs() -> None:
    """AGENTS.md ссылается на канонические документы, а не копирует их."""
    text = _agents_text()
    for link in AGENTS_CANONICAL_DOC_LINKS:
        assert link in text, link


def test_agents_doc_separates_local_gate_from_github_actions() -> None:
    """AGENTS.md отделяет локальный gate от GitHub Actions."""
    text = _agents_text()
    assert "## Локальный quality gate" in text
    assert "## GitHub Actions" in text
