"""Documentation contracts for the bounded epic-runner mandate (#249)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_adr_defines_bound_identity_lifecycle_and_default_mode() -> None:
    text = _read("docs/ADR-249-epic-runner-mandate.md")
    normalized = " ".join(text.split())
    required = (
        "Без валидного мандата действует прежний per-action режим",
        "repository identity",
        "trusted policy SHA",
        "`expires_at`",
        "digest всего содержимого",
        "состояние отзыва",
        "issue и task head",
        "`NEEDS_DECISION`",
    )
    for marker in required:
        assert marker in normalized, marker


def test_adr_preserves_roles_and_denylist() -> None:
    text = _read("docs/ADR-249-epic-runner-mandate.md")
    normalized = " ".join(text.split())
    required = (
        "Executor не получает Git/GitHub write",
        "controller остаётся независимым read-only",
        "force push",
        "tags, releases, PyPI",
        "repository settings",
        "расширение scope",
        "архитектурные либо security решения",
    )
    for marker in required:
        assert marker in normalized, marker


def test_state_machine_orders_separate_sha_bound_gates() -> None:
    text = _read("docs/ADR-249-epic-runner-mandate.md")
    states = (
        "PLAN",
        "RUN_TASK",
        "PR_CI",
        "MERGE",
        "POST_MERGE",
        "DEMO",
        "TASK_DONE",
        "NEXT_TASK",
        "FINAL_GATE",
        "ROADMAP_PR",
        "MAIN_POST_MERGE",
    )
    lines = text.splitlines()
    state_line = next(line for line in lines if line.startswith("PLAN ->"))
    state_line += next(line for line in lines if line.startswith("  -> TASK_DONE"))
    positions = [state_line.index(state) for state in states]
    assert positions == sorted(positions)
    normalized = " ".join(text.split())
    assert "пяти exact checks `SUCCESS` уже на новом roadmap merge SHA" in normalized
    assert "PR CI не переносится" in normalized
    assert "подтверждённой владельцем итоговой demo" in normalized


def test_stale_demo_and_unknown_outcome_fail_closed() -> None:
    text = _read("docs/ADR-249-epic-runner-mandate.md")
    normalized = " ".join(text.split())
    assert "Stale SHA" in text
    assert "Неготовая demo" in text
    assert "`ESCALATE_UNKNOWN_OUTCOME`" in text
    assert "операция не повторяется" in normalized
    assert "`APPLIED` или `NOT_APPLIED`" in normalized


def test_short_and_canonical_policies_reference_same_exception() -> None:
    agents = _read("AGENTS.md")
    contributing = _read("CONTRIBUTING.md")
    coordinator = _read("docs/agent-coordinator.md")
    for text in (agents, contributing, coordinator):
        assert "ADR-249" in text
        assert "per-action" in text
        assert "force push" in text
    assert "trusted delivery driver" in contributing
    assert "ESCALATE_UNKNOWN_OUTCOME" in coordinator
