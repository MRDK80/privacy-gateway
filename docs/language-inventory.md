# Language inventory

Baseline: main `5e1cebdeb8645ffebd391f3d578951bc4b9a1c32`, before #118.
This is a migration inventory, not a translation-completion report. Current
language records the presence of prose/identifiers, not semantic review status.
The [policy](LANGUAGE_POLICY.md) defines canonical precedence and migration rules.

| Tracked artifact | Audience | Baseline language | Target / treatment | Delivery |
| --- | --- | --- | --- | --- |
| `.github/CODEOWNERS` | CI / ownership | English / machine syntax | Machine syntax; English new prose | No behavioral migration in #118 |
| `.github/pull_request_template.md` | Package / contributors | Russian + English identifiers/prose | English prose; syntax unchanged | #124 |
| `.github/workflows/main-source-guard.yml` | CI / ownership | English / machine syntax | Machine syntax; English new prose | No behavioral migration in #118 |
| `.github/workflows/pre-commit.yml` | CI / ownership | English / machine syntax | Machine syntax; English new prose | No behavioral migration in #118 |
| `.github/workflows/publish-pypi.yml` | CI / ownership | English / machine syntax | Machine syntax; English new prose | No behavioral migration in #118 |
| `.github/workflows/tests.yml` | CI / ownership | Russian + English identifiers/prose | Machine syntax; English new prose | No behavioral migration in #118 |
| `AGENTS.md` | Contributors / agents | Russian + English identifiers/prose | English target; binding Russian until scoped review | Technical translation separately scoped |
| `CHANGELOG.md` | Release consumers | Russian + English identifiers/prose | New English entries; preserve history | Future release work |
| `CONTRIBUTING.md` | Contributors / agents | Russian + English identifiers/prose | English target; binding Russian until scoped review | Technical translation separately scoped |
| `README.md` | Users | Russian + English identifiers/prose | English + Russian companion | #119 |
| `SECURITY.md` | Users / security | English canonical + Russian companion | Delivered English/Russian | #121 |
| `docs/392-extraction-demo-plan.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/392-extraction-issue-allocation.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/392-extraction-resource-routes.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/392-extraction-sections.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/392-extraction-trust-binding.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-113-safe-doctor.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-114-trusted-publishing.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-150-cli-json-contract.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-151-cli-introspection.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-163-agent-verification-gate.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-165-agent-orchestrator.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-166-agent-learning-memory.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-167-agent-pr-checks.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-168-agent-workflow-pilot.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-185-executor-writable-scope.md` | Engineering / decisions | English / machine syntax | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-186-adapter-diagnostic-codes.md` | Engineering / decisions | English / machine syntax | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-189-scope-enforced-semantics.md` | Engineering / decisions | English / machine syntax | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-192-generation-schema-required.md` | Engineering / decisions | English / machine syntax | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-194-review-basis-trust-source.md` | Engineering / decisions | English / machine syntax | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-196-trusted-local-snapshot.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-197-complete-gate-evidence.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-200-amendment-204-finding-fidelity.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-200-amendment-208-finding-text.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-200-persist-gate-evidence.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-207-controller-review-payload.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-211-patch-review-delivery-gates.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-219-repair-feedback.md` | Engineering / decisions | English / machine syntax | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-221-executor-task-envelope.md` | Engineering / decisions | English / machine syntax | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-223-executor-delivery-partition.md` | Engineering / decisions | English / machine syntax | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-225-executor-external-sandbox.md` | Engineering / decisions | English / machine syntax | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-233-agent-coordinator-contract.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-234-agent-coordinator-discovery.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-235-agent-coordinator-branch-preflight.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-236-agent-coordinator-handover.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-237-agent-coordinator-delivery.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-238-agent-coordinator-resume.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-239-agent-coordinator-pilot.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-249-epic-runner-mandate.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-250-epic-runner-queue.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-251-epic-runner-handover.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-252-epic-task-delivery.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-253-idempotent-epic-loop.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-254-rate-limit-pause.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-255-consumer-demo.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-256-final-epic-gate.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-257-epic-runner-pilot.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-259-verified-prompt-lessons.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-274-amendment-bootstrap.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-274-amendment-epic-continuation.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-274-amendment-phase-wiring.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-274-amendment-policy-binding.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-274-epic-runner-runtime-adapter.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-284-bootstrap-recovery.md` | Engineering / decisions | English / machine syntax | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-290-bounded-github-read-retries.md` | Engineering / decisions | English / machine syntax | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-392-privacyagents-extraction-boundary.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-45-atomic-manifest-policy.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-64-no-clobber-publication.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-81-single-source-package-version.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ADR-86-spdx-license-metadata.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/ARCHITECTURE.md` | Engineering / release | Russian + English identifiers/prose | English new prose; existing Russian not yet translated | No bulk rewrite; release page #126 |
| `docs/DECISIONS.md` | Engineering / decisions | Russian + English identifiers/prose | Keep legacy; new decisions English | Historical, no bulk translation |
| `docs/DEPENDENCIES.md` | Users / installation | Russian + English identifiers/prose | English + Russian guidance; one executable | #120 |
| `docs/GITHUB_RULESETS.md` | Engineering / release | English / machine syntax | English new prose; existing Russian not yet translated | No bulk rewrite; release page #126 |
| `docs/INSTALLATION.md` | Users / installation | Russian + English identifiers/prose | English + Russian guidance; one executable | #120 |
| `docs/LIBRARY_API.md` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `docs/POST_PUBLICATION.md` | Engineering / release | Russian + English identifiers/prose | English new prose; existing Russian not yet translated | No bulk rewrite; release page #126 |
| `docs/RELEASE.md` | Engineering / release | Russian + English identifiers/prose | English new prose; existing Russian not yet translated | No bulk rewrite; release page #126 |
| `docs/SECURITY.md` | Users / security | English canonical + Russian companion | Delivered English/Russian | #121 |
| `docs/article-sync-31.md` | Russian article | Russian + English identifiers/prose | Russian | No translation |
| `docs/detection.md` | Users / security | English canonical + Russian companion | Delivered English/Russian | #121 |
| `docs/threat-model.md` | Users / security | English canonical + Russian companion | Delivered English/Russian | #121 |
| `docs/token-format.md` | Engineering / release | Russian + English identifiers/prose | English new prose; existing Russian not yet translated | No bulk rewrite; release page #126 |
| `examples/01_quickstart.py` | Users / installation | Russian + English identifiers/prose | English + Russian guidance; one executable | #120 |
| `examples/02_full_round_trip.py` | Users / installation | Russian + English identifiers/prose | English + Russian guidance; one executable | #120 |
| `examples/03_blocked_secret.py` | Users / installation | Russian + English identifiers/prose | English + Russian guidance; one executable | #120 |
| `examples/04_untrusted_response.py` | Users / installation | Russian + English identifiers/prose | English + Russian guidance; one executable | #120 |
| `examples/05_key_rotation.md` | Users / installation | Russian + English identifiers/prose | English + Russian guidance; one executable | #120 |
| `examples/06_cli_round_trip.md` | Users / installation | Russian + English identifiers/prose | English + Russian guidance; one executable | #120 |
| `examples/README.md` | Users / installation | Russian + English identifiers/prose | English + Russian guidance; one executable | #120 |
| `pyproject.toml` | Package / contributors | Russian + English identifiers/prose | English prose; syntax unchanged | #124 |
| `src/privacy_gateway/__init__.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/__main__.py` | CLI / implementation | Russian + English identifiers/prose | English human-facing contract; identifiers unchanged | #122, approval before runtime changes |
| `src/privacy_gateway/bootstrap.py` | CLI / implementation | Russian + English identifiers/prose | English human-facing contract; identifiers unchanged | #122, approval before runtime changes |
| `src/privacy_gateway/cli.py` | CLI / implementation | Russian + English identifiers/prose | English human-facing contract; identifiers unchanged | #122, approval before runtime changes |
| `src/privacy_gateway/context_trust.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/crypto.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/detector.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/doctor.py` | CLI / implementation | Russian + English identifiers/prose | English human-facing contract; identifiers unchanged | #122, approval before runtime changes |
| `src/privacy_gateway/exceptions.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/facade.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/input_parser.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/keystore.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/manifest.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/models.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/pipeline.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/publish.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/restore.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/routing.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/tokenizer.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |
| `src/privacy_gateway/validator.py` | Library / implementation | Russian + English identifiers/prose | English public docstrings; internal comments no bulk rewrite | #123 (public surface only) |

No issue template is tracked at this baseline; any new template follows #124.
Tests and tools contain English identifiers and mixed Russian/English prose;
they are engineering artifacts, not a second public API. No mass comment or
fixture translation is required. LICENSE stays the original English legal text;
configuration JSON/YAML, workflow expressions and schemas retain machine keys.
New policy/inventory files in #118 are English engineering documentation.

## README migration — #119

`README.md` is now English and `README.ru.md` preserves the Russian user guide.
Both have reciprocal language links. The Russian companion records the committed
English source reviewed for synchronization. Other baseline rows remain a record
of the starting state, not a claim that those documents are translated.

## Installation/examples migration — #120

English installation/dependency guides and examples index/rotation/CLI guides
now have adjacent `.ru.md` companions with reciprocal links and reviewed source
commit records. Quickstart explanatory docstrings/comments are English; executable
AST, synthetic fixtures and expected outputs are unchanged. Other script fixtures
and existing CLI messages can intentionally contain Russian. Baseline inventory
rows above remain historical starting-state records.

## Public library migration — #123

`docs/LIBRARY_API.md` and the top-level/facade/public-exception docstrings are
English. The API reference is English-only per policy; Russian user scenarios
remain in the paired examples guides. No internal-comment or historical-ADR
translation is claimed, and runtime signatures/exception messages are unchanged.

## Metadata/contribution migration — #124

Package description/keywords, CONTRIBUTING and the PR template are English.
Two English issue templates accept Russian reports/questions and require
synthetic data/private vulnerability handling. Existing AGENTS authority and
all gate/owner-approval requirements remain binding. Metadata identity, version,
license, Python support and dependency requirements are unchanged.

## Structural validation — #125

The six delivered pairs have matching stable section markers and source records
reviewed against canonical marker commit `9f0f1a93203940b84c5aa48a442506011a243d30`.
The offline checker/pre-commit hook covers these pairs; pending #121 security
migration must register its pairs before claiming completion. Structural checks
do not certify translated meaning or the authenticity of a review record.

## Security migration — #121

Four English security guides retain Russian companions, reporting links and
structural source records. Private vulnerability reporting was explicitly
approved and verified enabled before documenting its intake. Detection remains
human-output only; no runtime behavior or security guarantee is expanded.

## CLI migration — #122

The owner-approved English CLI contract covers help, usage metavars, observable
messages and catalog/JSON message prose. Machine structure and exit codes remain
unchanged. Russian user guides quote the actual English CLI output. Synthetic
fixtures and internal/library errors outside the approved scope remain unchanged.
