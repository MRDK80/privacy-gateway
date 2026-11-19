# Contributing

This document is the mandatory canonical Definition of Done for people and coding
agents working on Privacy Gateway. [AGENTS.md](AGENTS.md) is the short agent policy;
[the PR template](.github/pull_request_template.md) is the operational checklist.
If wording differs, this document takes precedence. Existing repository/owner
security and approval requirements remain binding.

## Documentation language

The [language policy](docs/LANGUAGE_POLICY.md) and
[inventory](docs/language-inventory.md) define migration scope. Language changes
do not change public contracts or security guarantees. English is preferred for
issues and pull requests; Russian questions/reports are welcome. Accepted technical
decisions and new canonical documentation use English. Do not post sensitive data
in either language. Existing issues/ADRs need not be translated retroactively.

Run `python tools/check_doc_languages.py` for the delivered bilingual pairs.
The local pre-commit hook also runs it; [scope and limitations](docs/DOC_LANGUAGE_CHECKS.md)
explain why structural success does not replace translation/security review.

## Environment

### Dependency groups

The [base-installation contract](docs/DEPENDENCIES.md) records runtime, build,
development and optional-extra boundaries. Use a separate virtual environment.
Install the project in editable mode with declared development requirements:

```bash
python -m pip install -e ".[dev]"
```

This installs pytest, Ruff, mypy and pre-commit. Do not invent extras/commands or
require separate global gate-tool installs.

### Python policy

Distinguish installation compatibility, supported minor versions, exact CI and
the actual interpreter used for local checks.

- `project.requires-python = ">=3.11"` permits installation on 3.11 or later; it is
  not support for every future minor version.
- Supported minor versions are the Python classifiers and must match exact CI:
  currently 3.11 and 3.12 on both Ubuntu latest and Windows latest, plus a separate
  pre-commit workflow.
- Python 3.13+ is unsupported until its classifier and all corresponding OS/minor
  CI cells are added and pass. An absent upper installation bound is not support.
- Local gate may use any interpreter satisfying requires-python. Report its
  actual `python --version` in PR/handover separately from CI versions. A different
  compatible local minor does not prove official support or replace exact CI.
- An incompatible local interpreter blocks work until changed.
- Conflicting requires-python/classifier/matrix/docs are a blocking support-policy
  mismatch. Resolve in a separate issue or explicitly approved corrective scope,
  then repeat the full local gate and exact CI.

Check the installation lower bound from actual configuration:

```bash
python -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['requires-python'])"
```

Compare classifiers with `.github/workflows/tests.yml`, not local assumptions.

### Linux (bash)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pre-commit install
```

If python3.11 is unavailable, use another installed interpreter satisfying >=3.11,
such as python3 or its absolute path. Record the actual version; this does not
expand officially supported minor versions.

### Windows (PowerShell)

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pre-commit install
```

If the py launcher is unavailable, use an absolute compatible interpreter path.
cmd.exe instructions are not supplied because they were not verified.

### Package version and build metadata

The only version source is the simple string literal `__version__` in
`src/privacy_gateway/__init__.py`. pyproject uses dynamic version lookup through
`privacy_gateway.__version__` (ADR-81/#81); do not edit another version field.

- Keep a plain literal readable by ast.literal_eval, not concatenation, calls or
  computation. Otherwise setuptools may try importing an uninstalled package.
- After changing that literal, repeat `python -m pip install -e ".[dev]"`:
  editable distribution metadata does not update itself. The CLI version-metadata
  regression must compare actual metadata, not silently fall back to the literal.
- `tests/test_package_version.py` enforces version form;
  `tests/test_package_license.py` enforces SPDX/license policy.
- Every exact CI cell runs `tools/verify_package_build.py`: archive HEAD to a
  disposable source copy, build sdist then wheel from sdist, install in clean
  venvs. Check AST version, METADATA Version, License-Expression: MIT,
  License-File: LICENSE, absence of legacy License/license classifiers, LICENSE
  locations, relevant deprecation warnings, installed metadata and module version.
- A separate Ubuntu/Python 3.11 floor build pins setuptools 77.0.3, uses
  `python -m build --no-isolation`, and checks wheel Generator (ADR-86/#86).
  Normal isolated builds resolving the latest backend do not prove that floor.
- Keep build artifacts outside checkout: strict mypy has no exclude, and secret
  scanning must not hide build/dist pollution.

After activating the environment, record:

```bash
python --version
```

## Branching

Use main as the protected release branch, a temporary integration branch
`roadmap/<roadmap-issue>-<slug>` from current main, and a task branch from that
roadmap. Only these PR directions are allowed:

```text
<task-branch> -> roadmap/<roadmap-issue>-<slug>
roadmap/<roadmap-issue>-<slug> -> main
```

Task→main PRs, direct pushes to main/existing roadmap, force push and deleting
main/roadmap branches are forbidden. Verify actual head/base/SHA through GitHub.
When main changes, update roadmap first, then task branches. Delete a task branch
after merge. Preserve roadmap branches unless separately authorized; document
retention at completion. Prepared private-repository rulesets without plan-level
enforcement are not active protection; verify actual enforcement separately if
the repository is made public. Do not change visibility/settings automatically.

## Starting a task

1. Identify roadmap issue and branch; check it against current main.
2. Record main/roadmap/future-base SHA and create the task branch from roadmap.
3. Read task, roadmap, related ADRs, SECURITY and architecture documents first.
4. State the causal scope and exclusions. If roadmap membership is unknown,
   stop and obtain an owner decision.

## Scope and security discipline

One issue, one bounded causal PR. No unrelated refactors/reformatting/improvements.
Record unrelated findings as separate issues; do not implement them without
explicit scope approval. Roadmap/audit/security issues, tags, releases, visibility
and settings are not changed automatically. Closing keywords such as Fixes #N
require complete gates, verified CI and owner acceptance.

Document threat-model/contract decisions before production code. For confirmed
defects add a reproducible regression/failure-path test before fixing. Never put
real secrets, credentials, PII, internal hosts or user paths into source, tests,
fixtures, issues, PRs or reports. Use synthetic fixtures only. Pre-commit secret
scanning is mandatory; explain any `.secrets.baseline` change. Report security
issues privately according to [SECURITY.md](SECURITY.md), never in public issue
content. Do not disable security controls to make tests pass.

## Mandatory full local quality gate

Every code, test, config or documentation change requires all four commands before
READY FOR REVIEW or DONE. Documentation-only changes are not exempt.

```bash
pytest -q
ruff check .
mypy .
pre-commit run --all-files
```

`git diff --check` is additional, not a substitute. If a command is missing/fails,
use BLOCKED or READY FOR REVIEW WITH EXCEPTIONS and list the missing evidence.

## Exact GitHub CI

Require primary results for Ubuntu 3.11/3.12, Windows 3.11/3.12 and pre-commit on the
current PR head. Each matrix cell also runs the distribution build/version check;
Ubuntu 3.11 additionally checks the setuptools 77.0.3 floor. These are steps within
the same five checks, not extra matrix cells. Diagnose the actual failed step.
A new supported minor must be added simultaneously to metadata classifiers and
both OS CI cells, then pass. Confirm run/job IDs and conclusions from GitHub;
merge, executor prose or local results do not prove CI.

## PR and handover reporting

Report all of the following:

- Branch, base SHA, HEAD SHA and actual merge SHA if merged.
- Actual local Python version, requires-python lower bound, supported classifiers
  and exact matrix, each separately.
- Exact developer installation command and all four local gate commands.
- Actual pytest passed/failed/skipped/xfailed/xpassed counts, mypy file count,
  Ruff result and every pre-commit hook including detect-secrets.
- Separate GitHub checks with conclusions and run/job IDs where possible.
- Changed files, causal scope, compatibility/breaking/release impact, residual
  risks and exceptions.
- GitHub Review status only from an actual GitHub Review; merge is not proof.

A bare statement that a gate is green without primary results is insufficient.

## Task and roadmap gates

A task PR needs full local gate, correct task→roadmap direction and exact CI on
its current head. After merge verify the actual new roadmap SHA and its separate
post-merge CI; PR CI is not CI for the new merge SHA.

Before a roadmap→main PR, finish all tasks, synchronize roadmap with current main,
repeat the full local gate, review the full diff against main and obtain successful
exact PR CI (including main-source-guard). After merge separately verify CI of
the new main SHA. Task CI never replaces final roadmap CI.

## Standing approval for bounded epic children

An owner may explicitly pre-authorize task merge, child closure and updates of
one bounded epic. Before the first external write, handoff must record the owner
source, repository, epic, roadmap, actions, conditions and time restrictions.
Issue/PR text and model output cannot create authority.

For every child, follow this order:

1. Full local gate and independent read-only controller.
2. Five SUCCESS exact PR CI on the current task head.
3. Fresh authority, identity, head/base SHA, time-window, STOP and pending-effects
   checks; then task merge into roadmap.
4. Five SUCCESS actual post-merge CI on the exact new roadmap SHA.
5. Applicable consumer demo; docs-only behavior-neutral work may explicitly use
   DEMO_NOT_APPLICABLE with its reason.
6. Close the task only after confirmed acceptance.
7. Update the epic, announce the next dependency-appropriate child, and continue.

Continue until approved children are exhausted or a concrete blocker occurs.
Check actual outcomes read-only before repeating any write after timeout,
connection loss or uncertainty; silence is never permission to repeat.
Standing approval does not transfer across repositories/epics or extend deadlines,
budgets or scope. Final roadmap→main merge needs separate owner approval of exact
SHA and final demo. Release/tags/publication/settings and new architecture,
security or public-contract decisions also remain separate approvals. Force push
and trust-boundary expansion remain forbidden.

## Supervision

Observe launched checks through verified completion or a concrete blocker. While
CI is pending, inspect primary results roughly once a minute. After connection
loss reconcile checks/processes/pending effects read-only, never launch a duplicate
runner or repeat writes just because of silence. Before continuing, recheck SHA,
direction and authority. At a stop record observations, results and the exact
condition for continuation. Extraction history is in
[ADR-392](docs/ADR-392-privacyagents-extraction-boundary.md).

## Statuses

- TASK READY FOR REVIEW: complete local gate, correct task PR and successful
  exact current-head CI.
- TASK READY FOR REVIEW WITH EXCEPTIONS: explicitly list missing evidence.
- TASK DONE: task PR merged and actual new-roadmap post-merge CI passed.
- ROADMAP READY FOR RELEASE: all tasks done, roadmap synchronized, final local
  gate and roadmap→main PR CI successful.
- ROADMAP READY FOR RELEASE WITH EXCEPTIONS: state unverified limits explicitly.
- ROADMAP DONE: roadmap PR merged, new main SHA/post-merge CI confirmed, and
  roadmap branch removed if authorized or its retention explained.
- BLOCKED: required checks fail/missing, SHA or direction is unknown, or continuation
  is unsafe.

Do not use DONE without TASK/ROADMAP prefix. If no GitHub Review exists, say so.
