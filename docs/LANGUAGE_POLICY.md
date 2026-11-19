# Language policy

This policy implements task #118 in epic #117. It defines the migration target;
its adoption does not mean that existing material has already been translated.
The [inventory](language-inventory.md) records the starting state at main
`5e1cebdeb8645ffebd391f3d578951bc4b9a1c32`.

## Canonical language and scope

English is the target canonical language for the public product and new technical
decisions. Russian remains supported for the main user journeys. A translation
must preserve the meaning and limitations of the canonical contract; language
priority never authorizes changing security guarantees or executable behavior.
During migration, an untranslated Russian source remains the authoritative text
for that document until its English replacement has passed technical review.
Existing ADRs, code and contract tests continue to determine actual behavior.

| Material | Target | Delivery |
| --- | --- | --- |
| Root README | English + Russian | #119 |
| Installation, dependencies and examples/quickstart guidance | English + Russian | #120 |
| Root security policy, security guide, threat model, detection limitations | English + Russian | #121 |
| CLI help and human-facing messages | English only; no runtime language selector | #122, separate public-contract decision |
| Library API reference and public docstrings | English only | #123 |
| Package description, GitHub contribution/issue/PR templates | English only | #124 |
| New ADRs and engineering documents | English only | New work; legacy rules below |
| New CHANGELOG entries and release notes | English only | Future release work |
| First PyPI long description/release page | English only | #126; no publication authority |
| Habr article | Russian | Existing article; not an English product contract |

Bilingual coverage is deliberately bounded. Do not mirror every ADR, internal
module, workflow, test or engineering note into Russian. English-only material
may link to a Russian overview without implying that a complete translation exists.

## Paths and links

Keep the existing canonical path for English: `README.md`, `docs/INSTALLATION.md`,
`docs/SECURITY.md`, `examples/README.md`, and so on. Place its Russian companion
beside it with `.ru` before the final suffix: `README.ru.md`,
`docs/INSTALLATION.ru.md`, `SECURITY.ru.md`, `examples/README.ru.md`.
Preserve filename case. For a Markdown document that already has a suffix such
as `threat-model.md`, use `threat-model.ru.md`.

Executable examples remain single-source `.py` files. Translate their explanatory
text in #120 and retain the Russian explanations in the paired examples guide;
do not create a second executable merely to translate comments or identifiers.
Use relative links appropriate to each file's directory. Create reciprocal
`English | Русский` links only when both files exist. Do not create placeholder
translations or links to planned paths. Link checks and synchronization checks
are delivered in #125; a successful link check alone does not prove semantic parity.

## Synchronization and review

At initial translation, retain the original Russian content at its companion
path and review the English text against code, current contracts and ADRs. Do not
silently copy stale or stronger claims into either language.

Each bilingual companion carries a small visible synchronization record:

```text
Translation of: relative/path/to/canonical.md
Reviewed against commit: <full source commit SHA>
Translation status: current | needs-review
```

The SHA identifies the committed canonical content used for review, not the
future commit containing the translation. Initial migration can first commit the
canonical English text and then record that commit when adding/reviewing the
companion. This avoids a self-referential commit hash. A later PR updates both
languages and advances the record to the latest committed source it reviewed.
If the PR changes the canonical content again, review the final diff explicitly
and record that fact in the PR; the marker alone is not evidence of equivalence.

A user-facing behavior or security-document PR must list affected translations,
review both texts for equivalent meaning and identify any delayed translation.
If a companion cannot be synchronized, mark it `needs-review` and add a visible
notice linking to the canonical text. A known contradictory safety instruction
must be corrected or removed before delivery; a stale marker is not permission
to ship misleading security guidance. Do not label an unreviewed translation
`current`. Technical review remains mandatory for machine-assisted translation.

## Contracts and historical material

Do not translate command names, flags, JSON keys, machine codes, exit codes,
Python identifiers, token syntax, paths used by protocols, or fixture values.
`pgw --describe` remains the CLI syntax source; this policy is not a command
catalog. ADR-150 and ADR-151 remain the JSON/introspection references, and
`docs/LIBRARY_API.md` defines the public Python API.

#122 must separately define and approve changed human-facing stdout/stderr/help
text, document the decision and add characterization/contract tests before
runtime changes. This policy does not approve those new strings. #123 may
translate public docstrings without changing signatures or behavior. Internal
comments need no wholesale rewrite. The product name and package/import names
remain unchanged.

Do not rewrite old issues, PRs, published release notes, or accepted ADRs solely
for language consistency. Existing Russian ADRs remain historical evidence,
including still-applicable decisions: historical does not mean superseded. New
amendments use English and link to the original. `docs/DECISIONS.md` keeps its
legacy entries; new decisions use English. Existing extraction/agent records
and article synchronization notes are not claims about a newly translated product.
Root AGENTS/CONTRIBUTING policies remain binding in their present language until
a separately scoped technical translation is reviewed; language migration must
not change their authority or approval requirements.

## Glossary

| English | Russian | Required distinction |
| --- | --- | --- |
| pseudonymization | псевдонимизация | Reversible mapping; not anonymity |
| anonymization | анонимизация | Not a guarantee made by Privacy Gateway |
| protected text | защищённый текст | Only the intended protected text may cross the external boundary |
| plaintext | открытый текст | Source and restored values remain sensitive |
| secret | секрет | Credentials/key material; do not conflate with all PII |
| manifest | манифест | Encrypted mapping artifact; do not export it |
| token | токен | Structured replacement; do not translate its syntax |
| strict restore | строгое восстановление | Preserve the defined rejection rules; not a promise of perfect detection |
| trust boundary | граница доверия | Preserve which data may leave the local system |
| fail closed | отказ при невозможности подтвердить безопасность | No permissive fallback |
| atomic visibility | атомарная видимость | Does not imply crash durability or a multi-artifact transaction |
| best-effort cleanup | очистка с возможным отказом | Does not guarantee removal of temporary plaintext |

Security wording must preserve the distinctions in `SECURITY.md`,
`docs/SECURITY.md`, `docs/ARCHITECTURE.md` and `docs/threat-model.md`. Neither
translation nor English precedence strengthens detection, keyring, cleanup,
rotation, artifact-integrity or restored-text guarantees.

## Delivery sequence

#118 records this policy and inventory only. #119–#124 perform bounded migrations
under their own scopes; #125 checks links and synchronization structure; #126
prepares the English release page. Update the inventory when a class is migrated,
keep existing anchors or supply valid replacement links, and run the complete
local gate plus exact task and post-merge CI for every task, including docs-only.
No version bump, tag, release, PyPI publication or new localization dependency is
part of this policy.

## Contribution language — #124

English is preferred for issues and PR descriptions; Russian questions and
reports are welcome. New accepted technical decisions and canonical documentation
use English. This does not require retroactive translation of discussions or
permit sensitive data in either language.

## Structural checks — #125

The offline [language checker](DOC_LANGUAGE_CHECKS.md) enforces the explicit
registry of delivered pairs, reciprocal links, source-record format and ordered
critical section identifiers. Register new mandatory pairs when their migration
is actually delivered. Static structural success is not semantic equivalence,
review approval or authentication of a source commit.
