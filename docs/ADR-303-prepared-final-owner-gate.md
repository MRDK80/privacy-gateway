# ADR-303: prepare the final roadmap PR before a separate exact owner approval

Date: 2026-10-13. Task #303; epic #294.

## Status

Accepted: independent design review PASS; owner approved this design on
2026-10-13. Implementation remains subject to task delivery gates.
This is not installation, activation, a launch mandate, final demo confirmation or
permission to merge the epic into main. Task delivery permissions do not authorize
final effects. Stopped #131 and private payloads remain outside scope.

## Problem

The existing FinalRuntime obtains final-approval.json and an approved digest before
constructing even create_roadmap_pr. Consequently it cannot prepare a checked PR
and then wait for the owner's final decision. Its existing final approval also
lacks an exact PR/check-set/final-demo-source binding. Do not manufacture that
approval or label preparation ROADMAP READY FOR RELEASE to bypass the old driver.

## Opt-in and authority

Add internal epic config 8.0, composing the admitted predecessor config and a
separately owner-approved final-preparation profile 1.0. Config 1–7 and their
approval/ledger behavior remain unchanged. #302 integration is a prerequisite for
code integration where shared factories overlap; this initial task work is only
the separate design document. Do not import unreviewed #302 bytes into its branch.

Preparation profile keys exactly schema_version("1.0"), repository, epic,
roadmap_ref, policy_sha, mandate_digest, owner_identity, max_report_chars,
max_artifact_bytes, record_digest. SHA and tagged-digest formats match their
existing sources; bounded positive integer caps exclude bool. Config 8.0 binds
approved_final_preparation_digest in the original approved launch digest. It does
not pin an approval to merge that does not yet exist. Legacy final-approval paths
are not implicitly converted; no pending effect is migrated or reset.

A valid scoped original mandate must allow create_roadmap_pr. Preparation does not
add that operation to a mandate or grant merge_roadmap_pr, close_epic or update_epic.
All effects preserve original lifecycle, duration, iteration and follow-up limits.
The owner decision cannot renew or expand those limits. Stop/revoke/expiry/pending
UNKNOWN retain their original boundaries, including during resume.

## Preparation and persistent wait

Use fresh structurally verified children and main/roadmap refs. Require every
structural child closed, verified applicable task demo receipts and delivery/
post-merge evidence. Task synthetic/not-applicable modes stay exactly within the
approved existing task profile; human-only task demos still stop. No owner receipt
is generated from a synthetic result. Do not read real private demo input.

Run the aggregate full local gate on the exact roadmap snapshot and an independent
cumulative diff review against the pinned trusted policy. Retained lessons, where
used, require original source verification, privacy/injection negative checks and
negative-latch/tombstone enforcement. An empty approved hint catalog is reported as
no applied lessons; it is not a measured improvement or a forged verified lesson.
Cache an aggregate result only under its exact snapshot/policy/check identity; a
changed relevant input requires new evidence. No review input is truncated.

A distinct preparation-only driver accepts only create_roadmap_pr. It shares the
existing mandate validation, ledger and GitHub transport/reconciliation boundaries
but uses a new typed preparation assessment, not the old final-release assessment.
It cannot call a main-merge effect. Obtain fresh authority/refs immediately before
INTENT and effect. UNKNOWN creation is reconciled read-only; never blindly create
a duplicate PR. The request's deterministic operation ID binds repository, epic,
main/roadmap/policy SHAs and the preparation evidence identity.

Verify exact PR identity and the five required primary workflow checks at the
roadmap head, using original run/job/check-suite identities and pinned workflow
sources. CI_PENDING uses the existing bounded wait profile; unavailable/ambiguous
CI does not become success. Require fresh current base/head and no outstanding
children after CI. Preserve existing CI attempt/deadline counters across restart.

Persist a bounded private final-candidate 1.0 and a redacted report, then enter
WAITING_OWNER_FINAL_APPROVAL. This is a durable phase of the original run, not
success, a renewable mandate or a recoverable failure. A resume without approval
may reread/reconcile facts and present the same candidate, but performs no main
write and does not refill calls or reset deadlines. Existing supervisor status
exposes only bounded identifiers/status, not original private artifacts.

Candidate keys exactly schema_version("1.0"), repository, epic, roadmap_ref,
policy_sha, mandate_digest, launch_digest, owner_identity, pr, main_sha,
roadmap_sha, preparation_digest, pr_ci_digest, candidate_digest. Digests cover the
complete closed original evidence references. preparation_digest binds actual
aggregate snapshot/gate, independent review, structural child/task delivery/demo
facts and verified learning/negative checks. pr_ci_digest binds the five original
successful primary CI records. Digest equality is never a replacement for their
source reread. PR is a bounded positive integer. Snapshot facts remain private.
Records are owner-only, capped, reject unknown/duplicate keys and unsafe links;
partial/conflicting evidence is blocked. Atomic visibility is not a cross-file
transaction or an additional crash-durability guarantee.

## Separate final owner approval

The owner reviews the exact candidate/report and confirms the real final demo.
A closed final-approval 2.0 binds schema_version, owner_identity, approved_at,
repository, epic, pr, main_sha, roadmap_sha, policy_sha, mandate_digest,
launch_digest, candidate_digest, pr_ci_digest, final_demo_digest and
approved_digest. The original owner-only final demo receipt must be retained and
source-verified; synthetic task receipts cannot substitute for it.

A separately approved owner pin outside worktrees binds the exact approval digest
and original launch identity. The autonomous runtime only reads this pin; its
operation allowlist cannot create or change it. File ownership or a self-declared
approved flag alone does not prove human approval. Installing the pin is a
separate trusted owner action, subject to the same OS-owner boundary as existing
mandate installation. Issue text, model output and chat skill input cannot issue
it. This design requires no automatic owner-pin effect or wider sandbox.

After approval, revalidate original authority and allowlisted final operations,
source-verified final demo, all candidate/evidence links, primary PR CI, current
PR/base/head/main/roadmap and structural children immediately before merge intent
and effect. Any drift invalidates the candidate approval and returns to preparation
or the owner wait; an old pin never authorizes a new candidate. No automatic
reapproval is generated. Pending final effects are reconciled first and cannot be
rebound to a changed candidate.

Main merge requires merge_roadmap_pr separately present in the original mandate.
Close/update requires their own allowlist entries and a separate five-check
post-merge CI assessment of the actual new main merge SHA. Failed/pending main CI
cannot close the epic or be inferred from PR CI. UNKNOWN merge remains read-only
reconciliation; expiry/revoke/stop never permit a new write merely because a
reconciliation proves NOT_APPLIED. Preserve all original ledger entries/budgets.

## Verification and limits

Tests must exercise no main write before approval, repeated wait/resume/restart,
changed PR/main/roadmap/CI/demo/owner identity, outstanding child, task demo modes,
partial/forged/missing candidate/pin/source, exhausted/revoked/expired authority,
UNKNOWN PR creation/merge, failed post-merge CI and unchanged legacy behavior.
Positive synthetic tests cross real process boundaries where available and keep
synthetic evidence distinct from a separately authorized installed production
pilot in #304. No installation or pilot is part of this design acceptance.

Before production code: independent design review and explicit owner acceptance.
Then task full local gate, independent whole-task controller, exact PR and separate
post-merge roadmap CI plus applicable demo. Final epic→main approval remains a
separate owner decision on the actual final identities/demo.

## Closed contract clarification before implementation

These are internal opt-in contracts, not public pgw/Library API changes. All
records reject extra/missing/duplicate fields and booleans as integers. Git SHAs
are lowercase hex40. Content digest fields are hex64; approved profile, mandate
and launch references use existing sha256:hex64 canonical digests. A record's
self digest excludes only that self-digest field. Source rereads remain required.

Config8 keys exactly schema_version, policy_root, approved_mandate_digest,
owner_identity, started_at, task_iterations, follow_up_issues, approved_order,
approved_plans_digest, approved_final_digest, approved_launch_digest,
approved_planning_digest, approved_wait_digest, approved_quota_digest,
approved_continuation_digest, approved_learning_digest,
approved_final_preparation_digest. approved_final_digest must be null in this
version: the legacy preapproved-final route is unavailable. The approved final
pin below supplies only the later exact decision. All inherited field semantics
and nullable opt-in rules remain those of config7; no inferred upgrade occurs.

Preparation profile additionally includes max_final_model_calls, whose only
permitted value is 1, preserving the existing final contract's zero repair
iterations and single controller call. max_report_chars is 1..20000 and
max_artifact_bytes is 1..262144, further limited by original mandate/backend
caps. max cumulative diff remains 200000 characters. No preparation profile
increases original prompt/context/output/session bounds.

The final role counter belongs to the original launch, not the snapshot or
approval. Persist it before transport under fixed final-preparation/role-state.json:
keys schema_version, repository, epic, mandate_digest, launch_digest,
calls_consumed(0..1), pending_reservation_digest(nullable), record_digest. The
separate reservation keys are schema_version, repository, epic, mandate_digest,
launch_digest, ordinal(1), preparation_identity_digest, input_digest,
backend_digest, settings_digest(nullable before observed settings),
started_at, deadline_at, status(RESERVED/COMPLETED/UNKNOWN),
output_digest(nullable), record_digest. Derive its path from the original launch
and ordinal; no candidate-provided path is accepted. The existing fixed backend,
fresh session, original settings/output/completion receipts and continuation
bounds are required. An unresolved reservation cannot be repeated. Structured
quota/read waiting uses existing approved behavior without refunding this counter.

Cache a completed cumulative review only under repository/epic, main_sha,
roadmap_sha, tree/snapshot, policy, complete gate, actual review input/backend/
settings/output and source-verified completion. Same-source resume rereads it.
Changed diff/snapshot/policy needs a new review; if the single call is spent,
BLOCKED requires an owner disposition, not a new snapshot-based counter. CI-only
refresh may reuse that exact unchanged aggregate review but produces a new
candidate and invalidates old final approval. A final approval pin never renews
model authority or another preparation profile.

Preparation request1 keys exactly schema_version("1.0"), operation_id,
operation("create_roadmap_pr"), repository, epic, roadmap_ref, main_sha,
roadmap_sha, policy_sha, mandate_digest, launch_digest, preparation_digest.
operation_id hashes the remaining request identity fields. Its typed ledger
entry keys exactly request, status(INTENT/UNKNOWN/APPLIED), receipt; receipt is
null before a proven effect or a closed object with pr, repository, base_ref,
head_ref, main_sha, roadmap_sha, operation_id. Receipts must match original
primary GitHub facts. Ledger entries are keyed by operation_id. Old final ledger
entries are preserved and reconciled; a changed identity never overwrites an
unknown original entry.

Preparation assessment1 keys exactly schema_version("1.0"), status(READY/BLOCKED),
machine_code, snapshot_digest, local_gate_digest, review_digest,
children_digest, task_delivery_digest, learning_digest, record_digest. Digests
refer to original bounded private evidence, never supplied success flags. A
READY assessment means ready to prepare the PR and wait; it is not the legacy
ROADMAP READY FOR RELEASE status. Reports contain only schema_version,
repository, epic, candidate_digest, pr, main_sha, roadmap_sha,
status("WAITING_OWNER_FINAL_APPROVAL"), closed counts(total/closed/open),
learning_status(NONE_APPLIED/VERIFIED_APPLIED), comparison_status
(UNMEASURED/VERIFIED_COMPARABLE), record_digest. No free-form raw model output,
private paths, payloads or logs are published. The report cap applies to complete
encoded content; oversize blocks rather than truncating evidence.

Final state1 keys exactly schema_version("1.0"), repository, epic,
mandate_digest, launch_digest, phase, candidate_digest(nullable),
pending_operation_id(nullable), calls_consumed(0..1), blocked_code(nullable),
record_digest. Phase enum is PREPARING, WAITING_OWNER_FINAL_APPROVAL, MERGING,
MAIN_CI_PENDING, DONE, BLOCKED. Candidate is required in the wait/merge/main-CI
phases; pending_operation_id must refer to the original effect ledger. This
record summarizes verified sources and cannot create an applied receipt.
PREPARING reaches owner wait only after exact PR CI and original evidence.
Missing approval stays in owner wait. Drift returns to PREPARING only when no
pending effect exists and original authority permits the required actions;
otherwise BLOCKED preserves pending identity. Approved merge reaches MAIN_CI_PENDING
only from a verified merge receipt; separate exact main CI and separately
allowlisted close/update reach DONE. No step auto-clears terminal launch guards.

Owner pin lookup is fixed: verified managed seed/final-owner/approval-pin.json,
approval document at final-owner/approval.json, installation receipt at
final-owner/install-receipt.json. Candidate/issue/model data cannot select or
redirect these paths. Before importing candidate code, bootstrap validates the
approved preparation profile and these closed shapes/private file safety whenever
present. Missing owner pin means owner wait; partial, conflicting or malformed
owner evidence blocks. The owner directory is outside worktrees and model sandbox
write mounts. Runtime has no owner-pin writer effect.

Pin1 keys exactly schema_version("1.0"), repository, epic, owner_identity,
mandate_digest, launch_digest, candidate_digest, approval_digest, approved_at,
installation_receipt_digest, record_digest. Installation receipt1 keys exactly
schema_version("1.0"), kind("OWNER_FINAL_APPROVAL_PIN"), repository, epic,
owner_identity, mandate_digest, launch_digest, candidate_digest,
approval_digest, approved_at, owner_command_digest, record_digest. A separate
trusted owner-install action receives the explicitly approved digest; its original
fixed arguments and receipt bind that digest, not a runtime-provided flag.
Both artifacts are source-checked against the exact owner-approved document,
original launch and candidate. Their self digests/file ownership alone are not
human approval. As with existing mandate installation, malicious processes with
that OS owner's private-state write access remain outside this boundary; no
cryptographic human identity or stronger protection is claimed. This action is
not in the autonomous operation allowlist and runs only on explicit owner approval.

For config8 a distinct learning assessment rereads original learning profiles,
retrospectives, applied lesson source proofs, negative tests and rollback state.
A source-proved empty approved catalog/no applied hints is NONE_APPLIED with
UNMEASURED comparison. Applied hints require VERIFIED_APPLIED and original
verification sources; measured improvement requires comparable source-proved
runs. The new preparation/final assessment does not call legacy
FinalRuntime._owner() or forge its verified-lesson.json. Legacy versions retain
that original requirement unchanged.

Stop/revoke/expiry may permit only existing fixed read-only reconciliation of
pending effect identity. APPLIED preserves its genuine receipt; UNKNOWN remains
blocked; NOT_APPLIED does not allow a new write without fresh applicable authority.
No model call or re-preparation write occurs merely to reconcile. Close/update
requires the genuine final PR merge receipt and exact actual main merge SHA's
five original primary CI checks, with fresh main identity and no outstanding
required child. PR CI and a self-reported checkpoint never replace main CI.
