#!/usr/bin/env python3
"""Offline structural checks for delivered bilingual documentation, not semantics."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

REQUIRED_PAIRS: dict[str, tuple[str, ...]] = {
    "SECURITY.md": ("security-boundary",),
    "docs/SECURITY.md": ("security-boundary",),
    "docs/threat-model.md": ("security-boundary",),
    "docs/detection.md": ("security-boundary",),
    "README.md": ("protection-boundary", "installation", "library", "limitations"),
    "docs/INSTALLATION.md": (
        "cli-install",
        "library-install",
        "maintenance",
        "troubleshooting",
        "evidence",
    ),
    "docs/DEPENDENCIES.md": ("base-dependencies", "optional-boundary", "diagnostics"),
    "examples/README.md": ("provider-boundary", "quickstart", "rotation", "cli-flow"),
    "examples/05_key_rotation.md": ("side-effects", "retention", "failure-boundary"),
    "examples/06_cli_round_trip.md": (
        "provider-boundary",
        "strict-mode",
        "cleanup",
        "json-mode",
        "catalog",
    ),
}
OFFICIAL_MAIN = "https://github.com/MRDK80/privacy-gateway/blob/main/"
OFFICIAL_MAIN_PATH = "/MRDK80/privacy-gateway/blob/main/"

PRIVATE_PARTS = {
    ".git",
    ".agent-private",
    ".venv",
    ".aws",
    ".codex",
    ".agents",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
MARKER = re.compile(r"^<!-- pgw-section: ([a-z0-9-]+) -->$", re.MULTILINE)


class DocLanguageError(ValueError):
    """A supported structural invariant could not be verified."""


def safe_target(root: Path, document: Path, raw: str) -> Path:
    decoded = unquote(raw)
    if "\x00" in decoded or "\\" in decoded or decoded.startswith("/"):
        raise DocLanguageError("invalid local target")
    parts = PurePosixPath(decoded).parts
    if decoded != raw and ".." in parts:
        raise DocLanguageError("encoded traversal in local target")
    if any(p.casefold() in PRIVATE_PARTS or p.startswith(".env") for p in parts):
        raise DocLanguageError("private/cache target is not documentation")
    candidate = document.parent / decoded
    current = candidate
    while current != root and current.is_relative_to(root):
        if current.is_symlink():
            raise DocLanguageError("symlink documentation target")
        current = current.parent
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise DocLanguageError("local target escapes repository")
    if any(
        p.casefold() in PRIVATE_PARTS or p.startswith(".env")
        for p in resolved.relative_to(root).parts
    ):
        raise DocLanguageError("resolved private/cache target")
    if not (resolved.is_file() or resolved.is_dir()):
        raise DocLanguageError("missing local target")
    return resolved


def read_document(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise DocLanguageError("not a bounded Markdown file")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DocLanguageError("cannot read UTF-8 Markdown") from exc


def without_fences(text: str) -> str:
    lines: list[str] = []
    fence = ""
    size = 0
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if match and match[1][0] == fence and len(match[1]) >= size:
                if not match[2].strip():
                    fence = ""
            lines.append("")
        elif match:
            fence, size = match[1][0], len(match[1])
            lines.append("")
        else:
            lines.append(line)
    if fence:
        raise DocLanguageError("unclosed fenced code block")
    return "\n".join(lines)


def without_comments(text: str) -> str:
    clean = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    if "<!--" in clean:
        raise DocLanguageError("unclosed HTML comment")
    return clean


def heading_anchors(text: str) -> set[str]:
    clean = without_comments(without_fences(text))
    anchors: set[str] = set()
    for heading in re.findall(r"^ {0,3}#{1,6}\s+(.+)$", clean, re.MULTILINE):
        title = re.sub(r"\s+#+\s*$", "", heading)
        title = LINK.sub(lambda match: match[0].split("]", 1)[0].lstrip("!["), title)
        title = re.sub(r"<[^>]+>", "", title).replace("`", "").lower()
        base = "".join(c for c in title if c.isalnum() or c in "_-" or c.isspace())
        base = re.sub(r"\s", "-", base)
        candidate, suffix = base, 0
        while candidate in anchors:
            suffix += 1
            candidate = f"{base}-{suffix}"
        anchors.add(candidate)
    for explicit in re.findall(r"<a\s+(?:id|name)=[\"']([^\"']+)[\"']", clean):
        if explicit in anchors:
            raise DocLanguageError("duplicate explicit anchor")
        anchors.add(explicit)
    return anchors


def section_ids(text: str, expected: tuple[str, ...]) -> None:
    clean = without_fences(text)
    matches = list(MARKER.finditer(clean))
    found = [match[1] for match in matches]
    if len(found) != len(set(found)) or tuple(found) != expected:
        raise DocLanguageError("missing, duplicate or reordered contract sections")
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(clean)
        section = clean[match.end() : end].strip()
        if not re.match(r"#{1,6} ", section):
            raise DocLanguageError("section marker must precede a heading")
        visible = without_comments(section)
        if not any(
            line.strip() and not line.lstrip().startswith("#")
            for line in visible.splitlines()[1:]
        ):
            raise DocLanguageError("empty contract section")


def check_links(root: Path, document: Path, text: str) -> None:
    clean = without_comments(without_fences(text))
    clean = re.sub(r"`+[^`\n]*`+", "", clean)
    if re.search(r"^\s*\[[^\]]+\]:|\[[^\]]+\]\s*\[[^\]]*\]", clean, re.MULTILINE):
        raise DocLanguageError("reference links require explicit checker support")
    for match in LINK.finditer(clean):
        link = match[1].strip()
        if any(c.isspace() for c in link) or "\x00" in unquote(link):
            raise DocLanguageError("whitespace or NUL in Markdown target")
        parsed = urlsplit(link)
        official = (
            parsed.scheme == "https"
            and parsed.netloc == "github.com"
            and parsed.path.startswith(OFFICIAL_MAIN_PATH)
        )
        if official:
            if parsed.query:
                raise DocLanguageError("repository link has an unsupported query")
            target = safe_target(
                root, root / "README.md", parsed.path[len(OFFICIAL_MAIN_PATH) :]
            )
        else:
            if parsed.scheme or parsed.netloc:
                if parsed.scheme not in ("", "http", "https", "mailto"):
                    raise DocLanguageError("unsupported link scheme")
                continue
            target = safe_target(root, document, parsed.path or document.name)
        if parsed.fragment:
            if target.suffix.lower() != ".md":
                raise DocLanguageError("anchors supported only for Markdown")
            if unquote(parsed.fragment) not in heading_anchors(read_document(target)):
                raise DocLanguageError("missing local anchor")


def check_repository(
    root: Path, pairs: Mapping[str, tuple[str, ...]] | None = None
) -> list[str]:
    root = root.resolve()
    registry = REQUIRED_PAIRS if pairs is None else pairs
    errors: list[str] = []
    expected_companions = {
        str(PurePosixPath(name).with_suffix(".ru.md")) for name in registry
    }
    for directory in (".", "docs", "examples"):
        candidate = root / directory
        if not candidate.exists():
            continue
        try:
            safe = safe_target(root, root / "README.md", directory)
            for found_file in safe.glob("*.ru.md"):
                relative = found_file.relative_to(root).as_posix()
                if relative not in expected_companions:
                    errors.append(f"{relative}: unregistered bilingual companion")
        except (DocLanguageError, OSError, ValueError, RuntimeError):
            errors.append(f"{directory}: unsafe documentation directory")
    for relative, sections in registry.items():
        canonical = PurePosixPath(relative)
        companion = canonical.with_suffix(".ru.md")
        for name in (canonical, companion):
            try:
                path = safe_target(root, root / "README.md", name.as_posix())
                text = read_document(path)
                prose = without_comments(without_fences(text))
                switch = f"[English]({canonical.name}) | [Русский]({companion.name})"
                switches = {switch}
                if canonical.as_posix() == "README.md":
                    switches.add(
                        f"[English]({OFFICIAL_MAIN}README.md) | "
                        f"[Русский]({OFFICIAL_MAIN}README.ru.md)"
                    )
                if not switches.intersection(prose.splitlines()[:5]):
                    raise DocLanguageError("missing reciprocal top language links")
                section_ids(text, sections)
                check_links(root, path, text)
                if name == companion:
                    fields = (
                        (r"^Translation of: (.+?)\s*$", canonical.name),
                        (r"^Reviewed against commit: ([0-9a-f]{40})\s*$", None),
                        (r"^Translation status: (current|needs-review)\s*$", None),
                    )
                    values: list[str] = []
                    for pattern, expected in fields:
                        found = re.findall(pattern, prose, re.MULTILINE)
                        if (
                            len(found) != 1
                            or expected is not None
                            and found[0] != expected
                        ):
                            raise DocLanguageError("invalid translation source record")
                        values.append(found[0])
                    if values[-1] == "needs-review" and not any(
                        line.startswith("> ")
                        and "needs-review" in line
                        and f"]({canonical.name})" in line
                        for line in prose.splitlines()
                    ):
                        raise DocLanguageError(
                            "needs-review lacks visible source notice"
                        )
            except (DocLanguageError, OSError, ValueError, RuntimeError) as exc:
                detail = (
                    str(exc)
                    if isinstance(exc, DocLanguageError)
                    else "unreadable document"
                )
                errors.append(f"{name.as_posix()}: {detail}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    try:
        errors = check_repository(args.root)
    except (OSError, ValueError, RuntimeError):
        print("DOC_LANG_CHECK: invalid repository root", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"DOC_LANG_CHECK: {error}", file=sys.stderr)
        return 1
    print(
        "DOC_LANG_CHECK: structural PASS; semantic review and "
        "commit authenticity not inferred"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
