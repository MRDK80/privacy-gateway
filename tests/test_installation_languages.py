"""Check bilingual installation/example links and unchanged executable blocks."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAIRS = (
    "docs/INSTALLATION.md",
    "docs/DEPENDENCIES.md",
    "examples/README.md",
    "examples/05_key_rotation.md",
    "examples/06_cli_round_trip.md",
)


@pytest.mark.parametrize("relative", ("examples/README.md", "examples/README.ru.md"))
def test_examples_index_preserves_synthetic_ipv6_literal(relative: str) -> None:
    text = (ROOT / relative).read_text(encoding="utf-8")
    assert "`2001:db8::1`" in text


@pytest.mark.parametrize(
    "relative", ("examples/05_key_rotation.md", "examples/05_key_rotation.ru.md")
)
def test_rotation_guide_preserves_manifest_digest_identifier(relative: str) -> None:
    text = (ROOT / relative).read_text(encoding="utf-8")
    assert "`manifest_sha256`" in text


@pytest.mark.parametrize("relative", PAIRS)
@pytest.mark.parametrize("russian", (False, True))
def test_guide_external_markdown_urls_have_no_whitespace(
    relative: str, russian: bool
) -> None:
    document = ROOT / relative
    if russian:
        document = document.with_suffix(".ru.md")
    text = document.read_text(encoding="utf-8")
    for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
        parsed = urlsplit(link)
        if parsed.scheme or parsed.netloc:
            assert not re.search(r"\s", link), link


@pytest.mark.parametrize("relative", PAIRS)
def test_guides_have_reciprocal_links_and_identical_fenced_blocks(
    relative: str,
) -> None:
    canonical = ROOT / relative
    companion = canonical.with_suffix(".ru.md")
    english = canonical.read_text(encoding="utf-8")
    russian = companion.read_text(encoding="utf-8")
    links = f"[English]({canonical.name}) | [Русский]({companion.name})"
    assert links in english.splitlines()[:5]
    assert links in russian.splitlines()[:5]
    assert f"Translation of: {canonical.name}" in russian
    assert re.search(r"Reviewed against commit: [0-9a-f]{40}(?:\s|$)", russian)
    assert "Translation status: current" in russian
    pattern = r"```[^\n]*\n.*?\n```"
    assert re.findall(pattern, english, re.DOTALL) == re.findall(
        pattern, russian, re.DOTALL
    )
    for document, text in ((canonical, english), (companion, russian)):
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = (document.parent / unquote(parsed.path)).resolve()
            assert target.is_relative_to(ROOT)
            assert target.is_file(), link


def test_quickstart_keeps_its_synthetic_fixture_and_public_flow() -> None:
    source = (ROOT / "examples/01_quickstart.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    constants = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    assert constants["SYNTHETIC_EMAIL"] == "user@example.com"
    assert constants["SYNTHETIC_HOST"] == "192.0.2.10"
    assert constants["SYNTHETIC_PHONE"] == "+7 900 000-00-00"
    assert ast.get_docstring(tree) is not None
    # The actual offline/fake-keyring execution and failure paths remain covered
    # by test_examples_quickstart.py, not inferred from these syntax assertions.
