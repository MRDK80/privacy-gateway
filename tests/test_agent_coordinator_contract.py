"""Documentation invariants for the agent coordinator contract (#233)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_coordinator_contract_preserves_role_and_trust_boundaries() -> None:
    text = (REPO_ROOT / "docs" / "agent-coordinator.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    required_markers = (
        "не является coordinator",
        "недоверенными данными",
        "pinned `base_sha`",
        "explicit file allowlist",
        "permissions deny-by-default",
        "Controller",
        "Независимо и",
        "read-only проверяет patch",
        "не принимает delivery-решения",
        "Raw logs",
        "AGENTS.md` и role policy автоматически не изменяются",
    )
    for marker in required_markers:
        assert marker in normalized, marker


def test_coordinator_contract_defines_state_and_approval_sequence() -> None:
    text = (REPO_ROOT / "docs" / "agent-coordinator.md").read_text(encoding="utf-8")
    states = (
        "`PLAN`",
        "`PLAN_APPROVAL`",
        "`BRANCH_PREFLIGHT`",
        "`HANDOVER`",
        "`RUN`",
        "`ASSESSMENT`",
        "`DELIVERY_APPROVAL`",
    )
    positions = [text.index(state) for state in states]
    assert positions == sorted(positions)
    assert "локальная task-ветка от подтверждённого roadmap SHA" in text
    assert "Stale либо изменившийся target отменяет approval" in text
    assert (
        "зелёный PR CI текущего head SHA -> отдельное решение merge -> зелёный\n"
        "post-merge CI нового roadmap SHA -> закрытие task -> обновление epic"
        in text
    )


def test_coordinator_contract_is_fail_closed_and_resumable() -> None:
    text = (REPO_ROOT / "docs" / "agent-coordinator.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    required_stops = (
        "неоднозначном активном",
        "неизвестном или противоречивом parent",
        "stale main/roadmap",
        "dirty tree",
        "конфликтующей ветке",
        "недоступном GitHub",
        "scope escape",
        "отсутствующем/failed CI",
    )
    for marker in required_stops:
        assert marker in normalized, marker
    assert "Resume идемпотентен" in text
    assert "Уже выполненный side effect не повторяется" in text
