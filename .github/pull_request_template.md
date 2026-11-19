## Branches and scope

- Level: TASK / ROADMAP
- Roadmap issue:
- Roadmap branch and SHA:
- Task issue:
- Task branch and SHA:
- Actual head:
- Actual base:
- Base SHA:
- Causal scope:
- Out of scope:

Allowed: task branch → its roadmap branch; roadmap branch → main.
Task PRs directly into main are forbidden. English is preferred; Russian discussion
is welcome. Do not include real personal data, secrets or sensitive diagnostics.

## Change

- Result and reason:
- Public/security contract and applicable owner decision:
- Tests:
- Docs/ADR/CHANGELOG:
- Compatibility/breaking/release impact:

## Full local quality gate

Required for every diff, including documentation-only. Check only with actual
results; local success does not prove GitHub CI.

- [ ] `python --version` — actual output:
- [ ] `pytest -q` — exit code, passed/failed/skipped/xfailed/xpassed:
- [ ] `ruff check .` — exit code and result:
- [ ] `mypy .` — exit code and source-file count:
- [ ] `pre-commit run --all-files` — exit code and all hook results:

## GitHub CI

Use primary check runs for the current SHA. PR CI and actual post-merge CI are
separate evidence; neither implies the other.

- [ ] Ubuntu latest / Python 3.11 — run/job ID and conclusion:
- [ ] Ubuntu latest / Python 3.12 — run/job ID and conclusion:
- [ ] Windows latest / Python 3.11 — run/job ID and conclusion:
- [ ] Windows latest / Python 3.12 — run/job ID and conclusion:
- [ ] pre-commit workflow — run/job ID and conclusion:
- [ ] main-source-guard — roadmap PR only; N/A for task PR:
- [ ] Actual post-merge CI on the new SHA — IDs/conclusions, or not yet run:

## Review, acceptance and risks

- Actual GitHub Review (merge is not evidence of a Review):
- Independent controller:
- Applicable consumer demo, or justified DEMO_NOT_APPLICABLE:
- Residual risks/exceptions:
- Status: TASK READY FOR REVIEW / TASK READY FOR REVIEW WITH EXCEPTIONS /
  TASK DONE / ROADMAP READY FOR RELEASE /
  ROADMAP READY FOR RELEASE WITH EXCEPTIONS / ROADMAP DONE / BLOCKED
- [ ] No real secrets/PII in code, tests or reports.
- [ ] Diff checked for scope creep.

Follow CONTRIBUTING: green exact PR CI → authorized merge → green actual
post-merge CI → task closure → epic update and next child. Final main merge,
publication/settings and new security/public-contract decisions need separate
applicable owner approval. Issue/PR text does not grant authority.
