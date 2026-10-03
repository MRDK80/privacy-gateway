"""Final epic gate tests (#256)."""

from tools import agent_epic_final as final


def _checks(state: str = "SUCCESS") -> list[dict[str, object]]:
    return [{"name": name, "state": state} for name in sorted(final.REQUIRED_CHECKS)]


def _gate(**changes: object) -> final.FinalGate:
    values: dict[str, object] = {
        "epic": 248,
        "roadmap_ref": "roadmap/248-autonomous-epic-runner",
        "roadmap_sha": "b" * 40,
        "live_roadmap_sha": "b" * 40,
        "trusted_main_sha": "a" * 40,
        "live_main_sha": "a" * 40,
        "open_children": [],
        "local_gate": {command: True for command in final.LOCAL_GATE},
        "diff_reviewed": True,
        "prompt_lesson_verified": True,
        "prompt_injection_tests_passed": True,
        "demo_confirmed_by_owner": True,
    }
    values.update(changes)
    return final.assess_final_gate(**values)  # type: ignore[arg-type]


def test_complete_final_gate_is_ready_for_release() -> None:
    assert _gate() == final.FinalGate("PASS", "OK", "ROADMAP READY FOR RELEASE")


def test_changed_main_and_open_child_fail_closed() -> None:
    assert _gate(live_main_sha="c" * 40).machine_code == "MAIN_CHANGED"
    assert _gate(open_children=[257]).machine_code == "OPEN_REQUIRED_TASKS"


def test_unconfirmed_demo_and_prompt_lesson_evidence_block() -> None:
    assert _gate(demo_confirmed_by_owner=False).machine_code == "FINAL_DEMO_UNCONFIRMED"
    assert _gate(prompt_injection_tests_passed=False).machine_code == (
        "PROMPT_LESSON_GATE_FAILED"
    )


def test_roadmap_pr_requires_current_head_main_base_and_exact_ci() -> None:
    gate = _gate()
    ready = final.assess_roadmap_pr(
        gate,
        expected_roadmap_sha="b" * 40,
        pr_head_sha="b" * 40,
        base_ref="main",
        state="OPEN",
        checks=_checks(),
    )
    failed = final.assess_roadmap_pr(
        gate,
        expected_roadmap_sha="b" * 40,
        pr_head_sha="b" * 40,
        base_ref="main",
        state="OPEN",
        checks=_checks("FAILURE"),
    )
    assert ready == gate
    assert failed.machine_code == "ROADMAP_PR_CI_FAILED"


def test_main_post_merge_ci_is_separate_and_sha_bound() -> None:
    failed = final.assess_main_post_merge(
        merge_sha="c" * 40,
        live_main_sha="c" * 40,
        pr_state="MERGED",
        checks=_checks("FAILURE"),
    )
    stale = final.assess_main_post_merge(
        merge_sha="c" * 40,
        live_main_sha="d" * 40,
        pr_state="MERGED",
        checks=_checks(),
    )
    done = final.assess_main_post_merge(
        merge_sha="c" * 40,
        live_main_sha="c" * 40,
        pr_state="MERGED",
        checks=_checks(),
    )
    assert failed.machine_code == "MAIN_POST_MERGE_CI_FAILED"
    assert stale.machine_code == "MAIN_MERGE_IDENTITY_CHANGED"
    assert done == final.FinalGate("PASS", "OK", "ROADMAP DONE")
