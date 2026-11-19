"""Offline positive/negative document-structure checks, not translation scoring."""

from __future__ import annotations

from pathlib import Path

import pytest
from tools import check_doc_languages as checker

PAIR = {"README.md": ("boundary", "cleanup")}


def make_pair(root: Path) -> tuple[Path, Path]:
    english = root / "README.md"
    russian = root / "README.ru.md"
    switch = "[English](README.md) | [Русский](README.ru.md)"
    english.write_text(
        "# English guide\n\n" + switch + "\n\n"
        "<!-- pgw-section: boundary -->\n## Boundary\nA synthetic boundary.\n\n"
        "<!-- pgw-section: cleanup -->\n## Cleanup\nExplicit synthetic cleanup.\n",
        "utf-8",
    )
    russian.write_text(
        "# Русское руководство\n\n" + switch + "\n\n"
        "Translation of: README.md  \nReviewed against commit: " + "1" * 40 + "  \n"
        "Translation status: current\n\n"
        "<!-- pgw-section: boundary -->\n## Граница\nСинтетическая граница.\n\n"
        "<!-- pgw-section: cleanup -->\n## Очистка\nЯвная очистка.\n",
        "utf-8",
    )
    return english, russian


def test_different_languages_pass_without_literal_text_equality(tmp_path: Path) -> None:
    make_pair(tmp_path)
    assert checker.check_repository(tmp_path, PAIR) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "switch",
        "record_target",
        "record_sha",
        "status",
        "section",
        "duplicate",
        "reorder",
        "empty",
        "fenced_marker",
        "file",
        "anchor",
        "reference",
        "unregistered",
        "external_space",
        "private",
        "escape",
        "encoded_traversal",
        "nul",
    ],
)
def test_broken_structural_invariants_fail(tmp_path: Path, mutation: str) -> None:
    english, russian = make_pair(tmp_path)
    if mutation == "missing":
        russian.unlink()
    elif mutation == "unregistered":
        (tmp_path / "unknown.ru.md").write_text("unregistered", "utf-8")
    else:
        target = (
            russian
            if mutation.startswith("record") or mutation == "status"
            else english
        )
        text = target.read_text("utf-8")
        changes = {
            "switch": ("[Русский](README.ru.md)", "[Русский](missing.ru.md)"),
            "record_target": ("Translation of: README.md", "Translation of: other.md"),
            "record_sha": ("1" * 40, "not-a-commit"),
            "status": ("Translation status: current", "Translation status: unknown"),
            "section": ("<!-- pgw-section: boundary -->", "<!-- omitted -->"),
            "empty": ("A synthetic boundary.", ""),
            "external_space": (
                "# English guide",
                "# English guide\n[bad](https://example.invalid/a b)",
            ),
        }
        if mutation in changes:
            before, after = changes[mutation]
            text = text.replace(before, after)
        elif mutation == "duplicate":
            text += "\n<!-- pgw-section: cleanup -->\n## Duplicate\nText.\n"
        elif mutation == "reorder":
            text = (
                text.replace("boundary -->", "temp -->")
                .replace("cleanup -->", "boundary -->")
                .replace("temp -->", "cleanup -->")
            )
        elif mutation == "fenced_marker":
            text = text.replace(
                "<!-- pgw-section: boundary -->",
                "```text\n<!-- pgw-section: boundary -->",
            )
            text = text.replace("A synthetic boundary.", "A synthetic boundary.\n```")
        else:
            links = {
                "file": "missing.md",
                "anchor": "README.md#absent",
                "reference": None,
                "private": ".agent-private/private.md",
                "escape": "../outside.md",
                "encoded_traversal": "%2e%2e/outside.md",
                "nul": "README.md%00",
            }
            text += (
                "\n[reference][missing]\n"
                if mutation == "reference"
                else f"\n[bad]({links[mutation]})\n"
            )
        target.write_text(text, "utf-8")
    assert checker.check_repository(tmp_path, PAIR)


def test_fenced_examples_are_not_live_links_or_contract_markers(tmp_path: Path) -> None:
    english, _ = make_pair(tmp_path)
    with english.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n````text\n```\n[not a live link](missing.md)\n"
            "<!-- pgw-section: cleanup -->\n```\n````\n"
        )
    assert checker.check_repository(tmp_path, PAIR) == []


def test_local_unicode_duplicate_and_explicit_anchors(tmp_path: Path) -> None:
    english, _ = make_pair(tmp_path)
    (tmp_path / "guide.md").write_text(
        "# API `prepare()`\n\n# API prepare\n\n## Пример данных\n\n"
        '<a id="stable-boundary"></a>\n',
        "utf-8",
    )
    with english.open("a", encoding="utf-8") as handle:
        for fragment in (
            "api-prepare",
            "api-prepare-1",
            "пример-данных",
            "stable-boundary",
        ):
            handle.write(f"\n[local](guide.md#{fragment})\n")
    assert checker.check_repository(tmp_path, PAIR) == []


def test_needs_review_requires_visible_source_notice(tmp_path: Path) -> None:
    _, russian = make_pair(tmp_path)
    text = russian.read_text("utf-8").replace("status: current", "status: needs-review")
    russian.write_text(text, "utf-8")
    assert checker.check_repository(tmp_path, PAIR)
    russian.write_text(
        text + "\n> needs-review: consult [English](README.md).\n", "utf-8"
    )
    assert checker.check_repository(tmp_path, PAIR) == []


def test_private_target_is_rejected_before_reading_its_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    english, _ = make_pair(tmp_path)
    private = tmp_path / ".agent-private/private.md"
    private.parent.mkdir()
    private.write_text("synthetic private body", "utf-8")
    english.write_text(
        english.read_text("utf-8") + "\n[bad](.agent-private/private.md#x)\n", "utf-8"
    )
    original = Path.read_text

    def guarded(path: Path, *args: object, **kwargs: object) -> str:
        assert path != private
        return original(path, encoding="utf-8")

    monkeypatch.setattr(Path, "read_text", guarded)
    assert checker.check_repository(tmp_path, PAIR)


def test_symlink_target_cannot_escape_scoped_docs(tmp_path: Path) -> None:
    english, _ = make_pair(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside.md")
    outside.write_text("synthetic outside document", "utf-8")
    link = tmp_path / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Host does not allow creating a symlink")
    english.write_text(english.read_text("utf-8") + "\n[bad](link.md#x)\n", "utf-8")
    assert checker.check_repository(tmp_path, PAIR)


def test_repository_delivered_pairs_pass_structural_check() -> None:
    assert checker.check_repository(Path(__file__).resolve().parents[1]) == []


def test_commented_out_contract_body_is_not_visible_content(tmp_path: Path) -> None:
    english, _ = make_pair(tmp_path)
    text = english.read_text("utf-8").replace(
        "A synthetic boundary.", "<!--\ncommented out\n-->"
    )
    english.write_text(text, "utf-8")
    assert checker.check_repository(tmp_path, PAIR)


@pytest.mark.parametrize("wrapper", ["```text\n{}\n```", "<!--\n{}\n-->"])
def test_hidden_needs_review_notice_is_rejected(tmp_path: Path, wrapper: str) -> None:
    _, russian = make_pair(tmp_path)
    text = russian.read_text("utf-8").replace("status: current", "status: needs-review")
    notice = "> needs-review: consult [English](README.md)."
    russian.write_text(text + "\n" + wrapper.format(notice) + "\n", "utf-8")
    assert checker.check_repository(tmp_path, PAIR) == [
        "README.ru.md: needs-review lacks visible source notice"
    ]


@pytest.mark.parametrize("wrapper", ["```text\n{}\n```", "<!--\n{}\n-->"])
def test_hidden_translation_source_record_is_rejected(
    tmp_path: Path, wrapper: str
) -> None:
    _, russian = make_pair(tmp_path)
    text = russian.read_text("utf-8")
    record = text[text.index("Translation of:") : text.index("<!-- pgw-section:")]
    russian.write_text(text.replace(record, wrapper.format(record) + "\n\n"), "utf-8")
    assert checker.check_repository(tmp_path, PAIR) == [
        "README.ru.md: invalid translation source record"
    ]


@pytest.mark.parametrize("wrapper", ["```text\n{}\n```", "<!--\n{}\n-->"])
@pytest.mark.parametrize("language", ["english", "russian"])
def test_hidden_top_language_switch_is_rejected(
    tmp_path: Path, wrapper: str, language: str
) -> None:
    english, russian = make_pair(tmp_path)
    target = english if language == "english" else russian
    switch = "[English](README.md) | [Русский](README.ru.md)"
    text = target.read_text("utf-8").replace(switch, wrapper.format(switch))
    target.write_text(text, "utf-8")
    assert checker.check_repository(tmp_path, PAIR) == [
        f"{target.name}: missing reciprocal top language links"
    ]


def test_unclosed_comment_cannot_hide_required_review_notice(tmp_path: Path) -> None:
    _, russian = make_pair(tmp_path)
    text = russian.read_text("utf-8").replace("status: current", "status: needs-review")
    russian.write_text(
        text + "\n<!--\n> needs-review: consult [English](README.md).\n", "utf-8"
    )
    assert checker.check_repository(tmp_path, PAIR) == [
        "README.ru.md: unclosed HTML comment"
    ]


@pytest.mark.parametrize("scan", ["anchors", "links", "section"])
def test_scans_reject_unclosed_comments(tmp_path: Path, scan: str) -> None:
    text = "<!-- pgw-section: boundary -->\n## Boundary\n<!--\nHidden body.\n"
    with pytest.raises(checker.DocLanguageError, match="unclosed HTML comment"):
        if scan == "anchors":
            checker.heading_anchors(text)
        elif scan == "links":
            checker.check_links(tmp_path, tmp_path / "README.md", text)
        else:
            checker.section_ids(text, ("boundary",))
