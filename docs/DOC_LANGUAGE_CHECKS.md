# Offline language and link checks

Run from the developer checkout:

```bash
python tools/check_doc_languages.py
```

The local pre-commit hook `doc-language-checks` runs this command without filenames
on every full hook invocation. It uses system Python/stdlib, not a network service
or a new dependency. The existing pre-commit GitHub job executes the sixth hook;
the exact CI set remains four OS/Python cells plus pre-commit.

## Delivered-pair registry

`tools/check_doc_languages.py:REQUIRED_PAIRS` declares the ten delivered pairs:
root README, installation/dependencies, and examples index/rotation/CLI guides.
Missing either file fails, as do unregistered `.ru.md` companions in the supported
root/docs/examples directories. Deleting a companion cannot downgrade an existing
pair into an English-only document.

Security documents have not yet completed #121 at this stage. Their Russian
sources are not falsely treated as migrated English/Russian pairs. When #121
migrates them, register those four pairs and their section IDs in the same causal
change. The API reference is English-only per policy, not a missing translation.
Do not add a blanket exception for an already-delivered pair.

## Structural invariants

Both documents need reciprocal `English | Русский` links near the top. Russian
companions need exactly one source filename, full hexadecimal source SHA and known
translation status. A `needs-review` status requires a visible blockquote containing
`needs-review` and a link to the canonical filename. The canonical source record
identifies reviewed committed text, not a self-referential future commit. Add
canonical markers first in a normal commit, then rebind companion records to that
actual commit after reviewing the final diff.

Critical sections use unique `<!-- pgw-section: identifier -->` comments immediately
before headings. IDs must match the ordered registry in both languages; missing,
duplicate, reordered, heading-less or empty/comment-only sections fail. Translated
heading text need not match. Marker presence does not prove that the prose is
correct, complete or semantically equivalent.

## Links and anchors

The supported subset is inline Markdown links/images without optional titles,
relative file/directory targets, fragment-only links, simple ATX headings and
explicit `<a id="...">` / `<a name="...">` anchors. Simple heading slugs preserve
Unicode alphanumeric characters/underscore/hyphen, remove punctuation, lowercase
and replace whitespace with hyphens; duplicates gain numeric suffixes. Complex
GitHub/CommonMark heading constructs, Unicode normalization edge cases, setext
headings and arbitrary HTML are not fully modeled: use simple headings or explicit
anchors and verify GitHub rendering during review. Reference-link syntax is rejected
rather than silently treated as checked.

Fenced code blocks and HTML comments do not supply live links/headings. Longer
backtick/tilde fences are supported; unclosed fences or HTML comments fail.
Put literal unclosed-comment examples inside fenced code. Local anchors require
a readable UTF-8 Markdown target no larger than 2 MiB. Check logical and resolved containment before
reading a target; private/cache paths, NUL, encoded traversal and symlinks fail.
The checker does not crawl external URLs or prove availability of external services;
whitespace in a URL is still rejected. It reads only the declared document content
and local anchor targets, never a recursive private-state inventory.

## Limits and results

Exit0 means supported structural checks passed; exit1 means a structural failure.
The output explicitly does not infer semantic review or commit authenticity.
The static SHA-shaped field cannot prove the commit exists, that a reviewer approved
it, or that later prose changes are equivalent. Technical/security translation
review and the applicable exact CI/delivery gates remain mandatory. This is a
static-checkout developer tool, not an OS sandbox or product runtime contract.
Positive/mutation tests live in `tests/test_doc_language_checker.py`.

The #121 security migration also registers `SECURITY.md`, `docs/SECURITY.md`,
`docs/threat-model.md` and `docs/detection.md` with preserved Russian companions.

## Package-index root links — #126

Only exact HTTPS `github.com/MRDK80/privacy-gateway/blob/main/` links are mapped
to repository-root files for the same path/private/symlink/encoding/anchor checks.
Queries on these repository links fail. Root README language switches may use
this exact absolute base; other pairs retain the relative switch. Other external
URLs are not fetched or certified. This validates local candidate targets and
does not claim that candidate English documents already exist on remote main.
