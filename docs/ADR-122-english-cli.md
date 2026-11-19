# ADR-122: English-by-default CLI text

Status: Accepted by the owner on 2026-11-19 for issue #122 / epic #117.

## Context

Russian CLI prose was part of observable help, stdout, stderr and JSON error
messages. Internationalization needs a single English default without introducing
a localization framework or translating detection data.

## Decision

Use English for parser descriptions, help/usage display metavars, command catalog
summaries, success/error text, restoration warnings and JSON `error.message`.
The [approved old/new table](CLI_ENGLISH_MESSAGES.md) is implemented with
characterization tests. No locale
detection, gettext, language flag or runtime language choice is introduced.

Commands, options, argument names/types, token grammar, interpolated caller data,
JSON keys and schema versions, machine codes, classifications and exit codes stay
unchanged. FILE/DIRECTORY are help display metavars; their machine type lookup
changes together, preserving actual path argument types. Existing English doctor
statuses/check names and fixed text stay unchanged. Diagnostic detect/doctor do
not gain operational JSON support. Library exports, signatures, exception classes
and fixed facade/context-trust messages stay unchanged. Internal restore/key-store
causes can be rendered in English without adding a structured library contract.

## Compatibility and privacy

This breaks consumers matching Russian human prose, including JSON error.message
and catalog summaries. Use documented machine codes/structure and `pgw --describe`
for supported introspection, not message text. This prose-only change does not
increment either JSON schema version 1.0. Standard argparse whitespace/formatting
may differ across supported Python versions; invariant content is tested without
promising identical standard-library layout.

Never translate user input, synthetic fixture values, detector dictionaries or
regex data. Do not add plaintext or keys to errors. Preserve fail-closed behavior,
redaction, generic unexpected-error handling and existing interpolation boundaries.

## Verification

Characterize approved literals and actual help/catalog/error/bootstrap/doctor and
restore paths. Preserve machine contracts and privacy failure tests, synchronize
English/Russian documentation, and record the human-text compatibility break in
CHANGELOG. Full local gate, independent review and exact PR/post-merge CI are
required. No version bump, main merge, release or PyPI publication is authorized.
