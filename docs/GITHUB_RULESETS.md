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

Before the roadmap exception was documented, GitHub rejected creation with
`GH013` because historical merge commit
`15cda38a171b3964fd32413a905523f29cb19588` violated required linear history.
After removing that rule only from roadmap protection, GitHub created the
roadmap branch and reported it as protected. The task branch was then created
from the roadmap branch and remains unprotected as intended.

Additional destructive smoke tests, including direct updates, force pushes,
and deletion attempts, require explicit owner approval and must record the
exact GitHub rejection output.
