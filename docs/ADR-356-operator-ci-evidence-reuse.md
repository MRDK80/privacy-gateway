# ADR-356: operator reuse of PR CI for an unchanged task-to-roadmap merge

Date: 2026-10-15. Task #356; epic #294.

## Scope and authority

The owner requested this bounded operator policy after #354. It applies only
once this policy change has passed its original local/controller/PR and actual
post-merge gates. #356 does not exempt itself. An issue, PR, candidate or model
report cannot grant merge/closure authority. Existing owner approval is still
required; a grant explicitly excluding reuse is not overridden by this ADR.

Only an operator's task-to-roadmap delivery evidence can use the exception.
Roadmap-to-main always requires actual separate post-merge CI on the new main
SHA, plus its other original gates and exact owner approval. Runtime schemas,
mandates, ledgers, readiness predicates and source verifiers are unchanged.
ADR-249/259 and existing runtime consumers still require actual post-merge
checks; a reuse receipt cannot be supplied as those checks or advance runtime
state. Supporting reuse there would need a separate approved implementation.

## Why equal branch names or equal trees alone are insufficient

A PR workflow using default checkout normally executes GitHub's synthetic PR
merge commit. A check associated with task head H is not by itself proof that
H was the checkout. Push has different event/ref/commit context. Scripts,
actions, permissions, credentials, services, artifacts and resolved dependency
inputs can behave differently even when repository files match.

Primary references: [GitHub workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)
and [actions/checkout](https://github.com/actions/checkout#checkout-pull-request-head-commit-instead-of-merge-commit).
Only facts established from actual run/job/checkout evidence are reusable.

## All conditions are required

1. The same repository, epic, roadmap and task PR are within the owner's
   operator grant. The PR is not an epic-to-main PR. Local final gate,
   independent controller, exact successful PR checks and applicable demo have
   passed. There are all five required cells, no skipped/cancelled/neutral,
   partial, stale or ambiguous check substituted for success.
2. Record B, the base SHA used for the successful PR runs, and H, their exact
   task head. Immediately before merge, primary GitHub reads prove the current
   base is still B and current head still H; the task incorporates B. A PR
   update, base advance (even with the same tree), rerun with different inputs,
   or unavailable original B invalidates the candidate receipt.
3. For every required job, primary workflow-run/job/checkout evidence binds
   its actual tested commit P and tree T to that run, H and B. Do not infer P
   from check-run head_sha alone. Retain Git commit objects/tree IDs and original
   checkout log provenance; unavailable or contradictory evidence means no
   reuse. The unmodified checkout is the source of the actual checks, not an
   unproved earlier checkout followed by source mutation.
4. After merge, primary reads bind actual merge M to parents B,H, unchanged
   current roadmap M and the same tree T tested by every required job.
   Merge conflict resolution, squash/rebase merge, altered parentage, different
   trees or a roadmap advance during verification require actual post-merge CI
   for the applicable SHA. No identity check is replaced by a branch name.
5. A reviewed execution-equivalence assessment covers every required job:
   workflow and transitive local action/script bytes; resolved action identities;
   matrix, commands, checkout behavior, relevant event/ref/SHA/history inputs;
   permissions, secrets availability (identities/policy only, never values),
   services, caches/artifacts, dependency resolution and target environment
   requirements. PR environment evidence must meet the unchanged target
   execution contract. Differences must be proven irrelevant to these checks,
   not assumed irrelevant because YAML looks similar. Any unknown, push-only
   check/effect or relevant PR/push behavior difference falls back to actual CI.
   This proves applicability of the observed PR runs, not equality to an
   imaginary future runner or unlimited freshness of floating dependencies.
6. An independent read-only controller verifies the complete source-bound
   assessment, including post-merge facts, before operator closure. It cannot
   certify its own producer's summary without original evidence. Approval,
   current authority, demo and other closure conditions are rechecked normally.

## Receipt and reporting

Store the bounded, redacted operator receipt outside the worktree with:
repository/epic/task/PR, approval source, B/H, per-job P/T, M/parents/tree,
workflow/action identities, job/matrix/environment/input assessment, exact
run/attempt/check-suite/job/check IDs, actual conclusions and original evidence
references, observation times, controller verdict and fallback reason if any.
This is an operator report, not a new machine-readable runtime contract.
No source, token, secret, raw environment or sensitive logs go into PR prose.

Use the explicit delivery mode PR_CI_REUSED and state which successful PR runs
are reused for which M. It is not POST_MERGE_CI_SUCCESS. Do not create a success
check/status on M, rewrite original results or claim CI actually ran there.
PR/body/handover contain PR results and merge equivalence separately; if actual
post-merge CI also finishes, report its real result separately.

Any missing condition uses POST_MERGE_CI_REQUIRED and waits for all five actual
successful checks on the applicable merge SHA. A known adverse result is never
hidden by reuse: failure/cancellation/unknown result in an already started
post-merge pipeline, or any terminal conclusion other than success, blocks
closure; a later adverse result invalidates reused readiness
and blocks subsequent delivery pending reconciliation. Known queued/running
jobs are pending, not an UNKNOWN terminal outcome: an otherwise valid reuse
receipt can remove the completion wait, without claiming those jobs passed.
Unknown observation state, missing identity or inability to reconcile is not
known pending. Do not silently edit past receipts or refund consumed budgets.

## Existing workflows and feasibility

The current tests/pre-commit workflows still trigger on roadmap pushes. This
policy neither disables/cancels them nor saves that compute: it can remove a
redundant operator delivery wait only after the complete proof above. Normal
supervision and supported handoff/wakeup rules remain; no unsupported promise
of background monitoring follows from reuse.

Current workflows use default PR checkout, mutable action tags, latest runners
and dependency resolution. None supplies a ready equivalence receipt. Therefore
no blanket eligibility is declared for this repository or previous PRs: the
operator audits the actual five jobs and effective inputs per merge, otherwise
waits for real CI. A policy wording alone does not establish these facts.

## Review cases (manual policy acceptance)

| Case | Required decision |
| --- | --- |
| B/H unchanged, all five original jobs and tested trees bound, M parents/tree match, execution contract independently proven | Operator reuse may satisfy task closure CI evidence under owner grant |
| Same branch name but B advanced, including a no-content commit | Actual post-merge CI |
| Check head matches H but actual checkout P is unknown | Actual post-merge CI |
| Equal trees but event/ref-sensitive script or push-only work differs | Actual post-merge CI |
| Floating action/dependency/environment inputs cannot be justified from original job evidence | Actual post-merge CI |
| Positively observed queued/running merge jobs, complete independent reuse proof, continued supervision | Operator closure may use PR_CI_REUSED; merge jobs remain pending, not successful |
| Unavailable pipeline observation or ambiguous state | Actual CI/reconciliation required; no reuse |
| Partial, failed, stale, ambiguous or cancelled checks, or any non-success terminal merge conclusion | Block; no success receipt |
| Existing runtime or verified-lesson source requests five merge-SHA checks | Actual post-merge CI; no substitution |
| Roadmap to main, even with equal trees | Actual main post-merge CI always |
