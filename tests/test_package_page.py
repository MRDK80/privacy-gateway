"""Package-page link context and retained offline containment checks (#126)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from tools import check_doc_languages as checker

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "https://github.com/MRDK80/privacy-gateway/blob/main/"


def test_canonical_package_page_has_absolute_repository_links_and_scope() -> None:
    text = (ROOT / "README.md").read_text("utf-8")
    prose = checker.without_comments(checker.without_fences(text))
    links = re.findall(r"\[[^\]]*\]\(([^)]+)\)", prose)
    assert links
    assert all(urlsplit(link).scheme == "https" for link in links)
    assert "individual users and small teams" in text
    assert "only declared optional extra is `dev`" in text
    assert "not guaranteed anonymization" in text
    assert "False negatives" in text


@pytest.mark.parametrize(
    "target",
    [
        "docs/missing.md",
        "docs/guide.md#missing",
        ".agent-private/evidence.md",
        "../outside.md",
        "%2e%2e/outside.md",
        "docs/%00.md",
        "docs/guide.md?unreviewed=1",
    ],
)
def test_official_main_links_preserve_target_guards(
    tmp_path: Path, target: str
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/guide.md").write_text("# Guide\n", "utf-8")
    (tmp_path / ".agent-private").mkdir()
    (tmp_path / ".agent-private/evidence.md").write_text("private fixture", "utf-8")
    with pytest.raises(checker.DocLanguageError):
        checker.check_links(
            tmp_path, tmp_path / "README.md", f"[target]({PREFIX}{target})"
        )


def test_official_main_link_checks_real_anchor_from_repository_root(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/guide.md").write_text(
        "# Guide\n\n## A boundary\nBody.\n", "utf-8"
    )
    checker.check_links(
        tmp_path, tmp_path / "README.md", f"[guide]({PREFIX}docs/guide.md#a-boundary)"
    )


def test_official_main_link_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.md"
    target.write_text("# Target\n", "utf-8")
    link = tmp_path / "link.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation not available for this OS identity")
    with pytest.raises(checker.DocLanguageError):
        checker.check_links(
            tmp_path, tmp_path / "README.md", f"[target]({PREFIX}link.md)"
        )


@pytest.mark.parametrize(
    "prefix",
    [
        PREFIX,
        "http://github.com/MRDK80/privacy-gateway/blob/main/",
        "https://github.com.example.invalid/MRDK80/privacy-gateway/blob/main/",
        "https://user@github.com/MRDK80/privacy-gateway/blob/main/",
        "https://github.com:443/MRDK80/privacy-gateway/blob/main/",
        "https://github.com/MRDK80/privacy-gateway/blob/other/",
    ],
)
def test_absolute_root_switch_accepts_only_exact_official_base(
    tmp_path: Path, prefix: str
) -> None:
    switch = f"[English]({prefix}README.md) | [Русский]({prefix}README.ru.md)"
    body = "<!-- pgw-section: boundary -->\n## Boundary\nVisible content.\n"
    (tmp_path / "README.md").write_text(f"# Guide\n\n{switch}\n\n{body}", "utf-8")
    record = (
        "Translation of: README.md\nReviewed against commit: "
        + "1" * 40
        + "\nTranslation status: current\n"
    )
    (tmp_path / "README.ru.md").write_text(
        f"# Руководство\n\n{switch}\n\n{record}\n{body}", "utf-8"
    )
    errors = checker.check_repository(tmp_path, {"README.md": ("boundary",)})
    if prefix == PREFIX:
        assert errors == []
    else:
        assert any("reciprocal top language links" in error for error in errors)
