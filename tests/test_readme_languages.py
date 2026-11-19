"""README migration/link regressions; semantic equivalence requires review."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["README.md", "README.ru.md"])
def test_readme_language_links_and_local_targets(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    prefix = "https://github.com/MRDK80/privacy-gateway/blob/main/"
    switch = f"[English]({prefix}README.md) | [Русский]({prefix}README.ru.md)"
    assert switch in text.splitlines()[:5]
    links = re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)
    assert links
    for link in links:
        if link.startswith(prefix):
            link = link[len(prefix):]
        parsed = urlsplit(link)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        target = (ROOT / unquote(parsed.path)).resolve()
        assert target.is_relative_to(ROOT)
        assert target.is_file(), link
    fences = re.findall(r"^```[^\n]*$", text, re.MULTILINE)
    assert len(fences) % 2 == 0
    assert not re.search(r"(?<!git\+)pipx? install privacy-gateway(?:\s|$)", text)


def test_readme_examples_and_installation_pin_stay_in_sync() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    russian = (ROOT / "README.ru.md").read_text(encoding="utf-8")
    installation = (ROOT / "docs/INSTALLATION.md").read_text(encoding="utf-8")
    for text in (english, russian):
        pin = re.search(
            r"git\+https://github.com/MRDK80/privacy-gateway.git@[0-9a-f]{40}", text
        )
        assert pin is not None
        assert pin.group() in installation
    for language in ("python", "text", "bash"):
        def blocks(text: str) -> list[list[str]]:
            return [
                [line for line in body.splitlines() if not line.startswith("#")]
                for body in re.findall(rf"```{language}\n(.*?)\n```", text, re.DOTALL)
            ]

        assert blocks(english) == blocks(russian)


def test_russian_companion_has_reviewed_committed_source() -> None:
    russian = (ROOT / "README.ru.md").read_text(encoding="utf-8")
    assert "Translation of: README.md" in russian
    assert re.search(r"Reviewed against commit: [0-9a-f]{40}(?:\s|$)", russian)
    assert "Translation status: current" in russian
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    assert not re.search("[А-Яа-яЁё]", english.replace("Русский", ""))
