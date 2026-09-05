# GitHub repository rulesets

This document records the enforced branch policy for the public
`MRDK80/privacy-gateway` repository.

## Branch model

```text
<task-branch> -> roadmap/<roadmap-issue>-<slug> -> main
```

Task pull requests target their roadmap branch. Roadmap pull requests target
`main`. Direct task pull requests to `main` are rejected by
`main-source-guard`.

## Main protection

Ruleset `main-release-protection` (ID `22327966`) targets the default branch
and is active. It has no bypass actors.

The ruleset:

- blocks deletion and non-fast-forward updates;
- requires linear history;
- requires a pull request and resolution of review threads;
- requires the branch to be up to date;
- requires these GitHub Actions checks:
  - `test (ubuntu-latest, 3.11)`;
  - `test (ubuntu-latest, 3.12)`;
  - `test (windows-latest, 3.11)`;
  - `test (windows-latest, 3.12)`;
  - `pre-commit`;
  - `main-source-guard`.

Required approvals are currently zero. Merge, squash, and rebase are listed as
allowed methods, subject to the linear-history rule. Classic branch protection
is not configured; GitHub protection is provided by the repository ruleset.

## Roadmap protection

Ruleset `roadmap-integration-protection` (ID `22328092`) targets
`refs/heads/roadmap/*` and is active. It has no bypass actors.

The ruleset:

- blocks deletion and non-fast-forward updates;
- requires a pull request and resolution of review threads;
- requires the branch to be up to date;
- permits initial branch creation without pre-existing status checks;
- requires the four matrix test checks and `pre-commit`.

Required approvals are currently zero. Linear history is intentionally not
required for roadmap branches. The repository contains a historical merge
commit that caused GitHub to reject creation of a new roadmap branch when the
linear-history rule was enabled. Release history remains protected by the
separate linear-history rule for `main`.

## Bootstrap verification

The post-public verification used roadmap #105 and task #104. Both branches
were created from `main` commit
`ea4d13c48ac8c2a520d910ef7c5afebf2a5917dd`:

```text
roadmap/105-public-repository-protection
test/104-rulesets-enforcement
```

GitHub initially rejected roadmap creation with `GH013` because historical
merge commit `15cda38a171b3964fd32413a905523f29cb19588` violated required
linear history. Enabling `do_not_enforce_on_create` correctly exempted status
checks but did not exempt linear history. After removing only the
linear-history rule from roadmap protection, GitHub created the roadmap branch
and reported it as protected. The task branch was then created from the roadmap
branch and remains unprotected as intended.

## API evidence

The REST ruleset endpoints returned both rulesets without the pre-public HTTP
403 response. Both reported `enforcement: active`, an empty bypass list, and
`current_user_can_bypass: never`. The classic branch-protection endpoint for
`main` returned HTTP 404 `Branch not protected`, confirming that protection is
provided by repository rulesets rather than the classic mechanism.

Both status-check rules report `strict_required_status_checks_policy: true`.
The roadmap rule also reports `do_not_enforce_on_create: true`.

## Pull-request evidence

Task PR #106 uses the required direction:

```text
test/104-rulesets-enforcement
  -> roadmap/105-public-repository-protection
```

At head SHA `d17e633f525354dfa428619ceefcf8907f49a95f`, all five required
checks completed successfully:

| Check | Run | Job | Conclusion |
|---|---:|---:|---|
| `test (ubuntu-latest, 3.11)` | `33982879344` | `101351134276` | success |
| `test (ubuntu-latest, 3.12)` | `33982879344` | `101351134120` | success |
| `test (windows-latest, 3.11)` | `33982879344` | `101351134282` | success |
| `test (windows-latest, 3.12)` | `33982879344` | `101351134373` | success |
| `pre-commit` | `33982879244` | `101351133912` | success |

Negative draft PR #107 intentionally targeted `main` directly from the task
branch. `main-source-guard` failed as expected in run `33983074775`, job
`101351658527`. The PR remained blocked and was closed without merge.

## Ref-operation evidence

All probes started with `main` and the roadmap branch at
`ea4d13c48ac8c2a520d910ef7c5afebf2a5917dd`. No probe changed either ref.

| Probe | Target | Result |
|---|---|---|
| Direct update | `main` | HTTP 409; PR required; 6 of 6 checks expected |
| Direct update | `roadmap/105-public-repository-protection` | HTTP 409; PR required; 5 of 5 checks expected |
| Force update | `main` | HTTP 422; force push rejected; `main-source-guard` expected |
| Force update | `roadmap/105-public-repository-protection` | HTTP 422; force push rejected; PR required |
| Delete ref | `main` | HTTP 422; default branch deletion rejected |
| Delete ref | `roadmap/105-public-repository-protection` | HTTP 422; ruleset rejected deletion |

The force-update probes attempted to move each protected ref to historical
commit `15cda38a171b3964fd32413a905523f29cb19588`. Both refs remained at the
expected SHA. No recovery action was required.

## Remaining verification

After task PR #106 is merged and post-merge roadmap CI succeeds, create the
roadmap PR to `main`. Confirm that `main-source-guard` accepts the
`roadmap/* -> main` direction and that all six required checks succeed for the
current roadmap SHA. Merge and branch deletion still require explicit owner
approval.
