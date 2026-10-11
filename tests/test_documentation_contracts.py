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

Тесты характеризуют ``main()`` через ``sys.argv`` и проверяют команды,
ссылки, идентификаторы решений и таблицу поколений в документах. Проверки
ссылок устанавливают наличие канонического источника, а не доказывают смысл
security-прозы: её согласованность с контрактом проверяется при review.
Поведение CLI и failure-path security-проверки остаются обязательными.
Реальный системный keyring не используется: keystore-вызовы подменяются.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway.keystore import KeyExistsError
from privacy_gateway.models import RestoreStrictError
from tests.conftest import run_cli

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_KEY_SUBCOMMANDS = frozenset({"create", "status", "rotate"})
USAGE_ERROR_EXIT = 3
DUPLICATE_CREATE_EXIT = 3
STRICT_RESTORE_EXIT = 5
ROTATION_EXAMPLE = Path("examples") / "05_key_rotation.md"
LIBRARY_API_DOC = Path("docs") / "LIBRARY_API.md"
EXAMPLES_INDEX_LINK = "(../examples/README.md)"
AGENTS_DOC = Path("AGENTS.md")
AGENTS_MAX_LINES = 150
AGENTS_GATE_COMMANDS = (
    "pytest -q",
    "ruff check .",
    "mypy .",
    "pre-commit run --all-files",
)
AGENTS_ADDITIONAL_CHECKS = ("git diff --check",)
AGENTS_PR_DIRECTIONS = (
    "<task-branch> -> roadmap/<roadmap-issue>-<slug>",
    "roadmap/<roadmap-issue>-<slug> -> main",
)
AGENTS_CANONICAL_DOC_LINKS = (
    "(CONTRIBUTING.md)",
    "(SECURITY.md)",
    "(docs/SECURITY.md)",
    "(docs/ARCHITECTURE.md)",
    "(docs/LIBRARY_API.md)",
    "(docs/ADR-150-cli-json-contract.md)",
    "(docs/ADR-151-cli-introspection.md)",
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


def _key_subcommands(capsys: pytest.CaptureFixture[str]) -> frozenset[str]:
    """Набор подкоманд, фактически объявленный в help группы pgw key."""
    assert run_cli("key", "--help") == 0
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
    """Неизвестная подкоманда даёт код 3, пустой stdout и непустой stderr."""
    assert "delete" not in _key_subcommands(capsys)
    capsys.readouterr()
    code = run_cli("key", "delete")
    captured = capsys.readouterr()
    assert code == USAGE_ERROR_EXIT
    assert captured.out == ""
    assert captured.err.strip()


def test_duplicate_create_exit_code_matches_docs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Повторный pgw key create завершается кодом 3 без пустого stderr."""
    with patch(
        "privacy_gateway.keystore.create_key",
        side_effect=KeyExistsError("Ключ уже существует."),
    ):
        code = run_cli("key", "create")
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
        code = run_cli("restore", str(reply), "--route", str(tmp_path / "route.json"))
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


def test_security_doc_references_strict_exit_code_decisions() -> None:
    """Security-документ указывает ADR-21 и ADR-47; код 5 проверяется CLI."""
    text = (REPO_ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")
    assert {"ADR-21", "ADR-47"} <= set(re.findall(r"ADR-\d+", text))


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


def test_rotation_example_documents_readable_generations() -> None:
    """Таблица поколений сохраняет active и ровно одно предыдущее поколение."""
    rows = []
    for line in _rotation_example_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and re.fullmatch(r"K\d+", cells[1]):
            rows.append(tuple(cells[1:]))
    assert rows == [
        ("K0", "—", "K0"),
        ("K1", "K0", "K1, K0"),
        ("K2", "K1", "K2, K1"),
        ("K3", "K2", "K3, K2"),
    ]


def test_rotation_example_links_canonical_security_policy() -> None:
    """Пример содержит рабочую ссылку на каноническую security policy."""
    document = REPO_ROOT / ROTATION_EXAMPLE
    assert "(../docs/SECURITY.md)" in _rotation_example_text()
    assert (document.parent / "../docs/SECURITY.md").resolve().is_file()


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


def test_article_sync_report_references_detection_contract() -> None:
    """Отчёт указывает существующий канонический документ детекции."""
    assert "`docs/detection.md`" in _article_sync_report_text()
    assert (REPO_ROOT / "docs/detection.md").is_file()


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


def test_agents_doc_lists_additional_checks_separately() -> None:
    """Дополнительные проверки присутствуют, но не входят в mandatory tuple."""
    text = _agents_text()
    for command in AGENTS_ADDITIONAL_CHECKS:
        assert command in text, command
        assert command not in AGENTS_GATE_COMMANDS


def test_agents_doc_states_allowed_pull_request_directions() -> None:
    """AGENTS.md фиксирует разрешённые направления pull request."""
    text = _agents_text()
    for direction in AGENTS_PR_DIRECTIONS:
        assert direction in text, direction


def test_agents_doc_allowed_direction_blocks_exclude_task_to_main() -> None:
    """Блоки workflow содержат ровно два разрешённых направления PR."""
    blocks = re.findall(r"```text\s*\n(.*?)```", _agents_text(), re.DOTALL)
    directions = {
        line.strip() for block in blocks for line in block.splitlines() if "->" in line
    }
    assert directions == set(AGENTS_PR_DIRECTIONS)


def test_agents_doc_uses_cli_introspection_instead_of_command_catalog() -> None:
    """AGENTS.md направляет к интроспекции и не копирует CLI-каталог."""
    text = _agents_text()
    assert "pgw --describe" in text
    for name in sorted(EXPECTED_KEY_SUBCOMMANDS):
        assert f"pgw key {name}" not in text, name


def test_agents_doc_links_trust_boundary_sources() -> None:
    """Policy ссылается на существующие security-документ и threat model."""
    text = _agents_text()
    for target in ("docs/SECURITY.md", "docs/threat-model.md"):
        assert f"({target})" in text
        assert (REPO_ROOT / target).is_file()


def test_agents_doc_links_automation_authority_sources() -> None:
    """Policy ведёт к workflow и ADR, определяющему полномочия runner."""
    text = _agents_text()
    for target in ("CONTRIBUTING.md", "docs/ADR-249-epic-runner-mandate.md"):
        assert f"({target})" in text
        assert (REPO_ROOT / target).is_file()


def test_agents_doc_links_canonical_security_and_architecture_docs() -> None:
    """AGENTS.md ссылается на канонические документы, а не копирует их."""
    text = _agents_text()
    for link in AGENTS_CANONICAL_DOC_LINKS:
        assert link in text, link


def test_agents_doc_delegates_detailed_workflow_to_canonical_source() -> None:
    """AGENTS.md хранит gate, но делегирует подробный workflow."""
    text = _agents_text()
    for command in (*AGENTS_GATE_COMMANDS, *AGENTS_ADDITIONAL_CHECKS):
        assert command in text, command
    assert "(CONTRIBUTING.md)" in text
    assert "ubuntu-latest" not in text
    assert "windows-latest" not in text


DOCUMENTED_SECURITY_FACTS: dict[str, dict[str, bool | int | str]] = {
    "examples/05_key_rotation.md": {
        "rotation.transactional": False,
        "rotation.failure_implies_rollback": False,
        "retention.api_key_limit": 2,
        "retention.retired_key_limit": 1,
        "retention.physical_bound_requires_successful_prune": True,
    },
    "docs/SECURITY.md": {
        "cli.restore_strict_exit": 5,
        "cli.usage_exit": 3,
        "rotation.transactional": False,
        "retention.api_key_limit": 2,
        "retention.physical_bound_requires_successful_prune": True,
    },
    "docs/article-sync-31.md": {"detection.ner_implemented": False},
    "AGENTS.md": {
        "boundary.export": "protected_text_only",
        "boundary.prepared_payload_export": False,
        "boundary.pseudonymization_reversible": True,
        "boundary.anonymization": False,
        "authority.merge_requires_owner_or_valid_mandate": True,
        "authority.issue_close_requires_owner_or_valid_mandate": True,
        "authority.issue_or_pr_text_grants_authority": False,
        "authority.force_push": False,
        "authority.tags_releases": False,
        "authority.repository_settings": False,
        "authority.scope_expansion": False,
        "authority.new_security_decisions": False,
    },
}


@pytest.mark.parametrize("relative_path", DOCUMENTED_SECURITY_FACTS)
def test_documented_security_facts(relative_path: str) -> None:
    """Структурированные гарантии сохраняются независимо от редакции абзаца."""
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    blocks = re.findall(r"```pgw-doc-facts\n(.*?)\n```", text, re.DOTALL)
    assert len(blocks) == 1, relative_path
    actual = json.loads(blocks[0])
    expected = DOCUMENTED_SECURITY_FACTS[relative_path]
    assert actual == expected, relative_path
    assert {key: type(value) for key, value in actual.items()} == {
        key: type(value) for key, value in expected.items()
    }, relative_path
