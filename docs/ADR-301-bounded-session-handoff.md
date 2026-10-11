# ADR-301: bounded redacted session handoff

Status: accepted. Task301, epic294. Independent design review PASS; owner
approved implementation on 2026-10-11. No runtime installation, activation,
mandate or main merge is granted here.
This task is structurally independent of300 (dependencies295/298 are closed).

## Opt-in and compatibility

Epic config6.0 extends existing config4 with approved_continuation_digest and
nullable approved_quota_digest. The latter is only a reference to the separately
approved #300 feature; it cannot enable quota behavior if its trusted adapter
is unavailable, and does not approve the pending #300 design. Config1/2/3/4
and #300 config5 retain their closed shapes. Final config6 may compose the
separately approved quota implementation after its merge; isolated301 with a
null quota digest must work without300. If the roadmap base changes, integration
and all gates are refreshed before task delivery.

Generated runtime-task config3.0 retains config2 keys and adds the same pinned
approved_continuation_digest. Existing task1/2 stay unchanged. Recovery guards
apply to3 whenever the existing recovery binding/state applies; continuation
cannot bypass recovery, task admission, launch or mandate validation. Whole
launch config/candidate digest binds these opt-ins before any new session.

Continuation profile1.0 is owner-only outside worktrees and has exactly
schema_version, repository, epic, roadmap_ref, policy_sha, mandate_digest,
owner_identity, backend_digest, limits. Its digest is approved separately.
backend_digest binds the existing pinned role command/model/backend contract,
not a model-chosen replacement. limits has exactly threshold_permille(1..950),
max_handoffs(1..32), max_prompt_bytes(1..262144), max_record_bytes(1..65536),
max_findings(1..128). Integers exclude booleans. Original max_minutes,
max_repair_iterations, max_report_chars, allowed_paths/tools/permissions and
mandate/lifecycle deadlines remain authoritative and cannot increase.

## Canonical evidence, not model memory

A separate closed handoff1.0 contains exactly schema_version, identity,
contract_digest, policy_digest, workspace, progress, pending, findings, gates,
consumed, source, status, record_digest. Unknown fields/types or oversized
records stop. No free prose, chain-of-thought, source payload/code, raw logs,
prompts, credentials or arbitrary paths are stored in this record.

identity is exactly repository, epic, task, run_id, policy_sha,
continuation_profile_digest, launch_config_digest. workspace is exactly
base_sha, head_sha, snapshot_sha, snapshot_digest; it names the mechanically
captured exact approved-scope workspace tree/commit, not a model statement.
Private unreviewed snapshot objects are captured in an owner-only private snapshot object store outside the worktree, without changing worktree Git objects/refs,
committing a task branch, or publishing them. An interrupted partial snapshot
is never a reviewed artifact or a delivery receipt.

progress is exactly role(executor/controller), phase, ordinal, checkpoint_digest.
phase is exactly EXECUTOR/GATE/CONTROLLER, not a free string.
pending is null or exactly kind(role/delivery), operation_digest,
reservation_digest, outcome(PENDING/APPLIED/NOT_APPLIED/UNKNOWN), snapshot_digest.
Its authoritative source remains the original role reservation or canonical
phase/operation ledger. A handoff cannot classify UNKNOWN on its own.

findings is a bounded list of exactly finding_digest, category, status(open/fixed),
source_review_digest. Categories are exactly security/contract/scope/correctness/quality/context/unknown; raw finding text is
not copied. Actual independent review evidence is reread from its original
private source and cannot be substituted by this list.
gates is a bounded list of exactly kind(local/controller/pr_ci/post_ci/demo),
sha, evidence_digest, outcome(PASS/FAIL/PENDING). Digests locate verified facts;
fresh required gates are assessed on exact SHA, not accepted from summary.

consumed is exactly started_at, deadline_at, repair_iterations, executor_calls,
controller_calls, handoffs, original_reasoning, repeat_reasoning,
reasoning_evidence_digest. Counts are nonnegative integers, bounded by original
contracts/limits; UTC seconds are finite integers. Reasoning enums reflect
supported verified API settings, never a claimed switch. Existing two automatic
repair increases and owner exceptions are retained; a new session or return to
ordinary Low does not reset them. Context handoff allowance is distinct from
repair, task and quota budgets. Original elapsed deadline cannot be extended.
source is exactly invocation_digest, backend_digest, checkpoint_digest,
reservation_digest. status is PREPARED/CONSUMED/FAILED. record_digest hashes all
other fields; source facts, not this digest alone, establish validity.

## Reservation and ownership

A separate closed role-reservation1.0 has exactly schema_version, identity,
contract_digest, role, ordinal, invocation_digest, checkpoint_digest,
started_at, deadline_at, consumed_digest, snapshot_before, snapshot_after,
status(RESERVED/COMPLETED/FAILED/UNKNOWN), outcome_digest. snapshot_after and
outcome_digest may be null. Persist ordinal/call consumption and reservation
before invoking the role, without refund on crash. Save verified completion
only after observing process exit, valid bounded role output and exact allowed
workspace facts. Per-record atomic visibility is not a cross-file transaction.
Conflicting/orphan state fails closed; a summary cannot reopen it.

Use the existing nonblocking runner ownership boundary and a narrowly scoped
private role/handoff lock. No new session starts while the original process or
its ownership is still active/ambiguous. Loss of observation is not exit.
Stop/revoke/expiry is freshly checked before transfer and before every role or
effect. Existing canonical pending delivery reconciliation remains mandatory:
APPLIED avoids replay, NOT_APPLIED permits only an already-authorized bounded
attempt, UNKNOWN stops. Confirmed writes are never replayed from a handoff.

For interrupted executor work, capture/preserve the exact partial snapshot and
consumed reservation; do not fabricate a complete artifact or automatically
restart it at an arbitrary step. If no trusted completion or bounded safe
NOT_APPLIED proof exists, stop for an owner-approved recovery strategy. A
completed executor snapshot with pending controller review resumes at review,
not executor. Completed review/gates are reused only when their exact source
facts and SHA remain valid; required independent review is never truncated.

## Session lifetime and fresh-call capacity

Existing agent_orchestrate creates a fresh executor_session/controller_session
for every role call, and codex_adapter uses fresh ephemeral model sessions.
This design preserves that lifetime. It implements durable safe-boundary
recovery between worker processes plus full fresh-prompt preflight, not a new
persistent conversation backend or a claim that recreating an already-fresh
session increases capacity. A continued process runs only the next unfinished
already-authorized role in its normal fresh session.

Previous invocation context usage is never current usage for the next call.
Cumulative account/historic token counts are never used as current context.
Before first and subsequent calls, measure the complete serialized trusted
policy/contract and untrusted input with conservative UTF-8 byte accounting
against the approved max_prompt_bytes. threshold_permille defines the proactive
capacity warning/stop band of that explicit prompt budget; it is not a guessed
model context window. Below the warning band the normal fresh call proceeds.
At/above the band preserve a prepared handoff and stop before invoking the role
for reviewed capacity/recovery strategy; do not endlessly recreate fresh
sessions with the same oversized request or reset budgets. For indivisible
full review above the bound use explicit CONTEXT_REVIEW_TOO_LARGE. No silent
trim of policy, criteria, files, findings or controller diff is allowed.

Invocation-bound supported context metrics may additionally stop an active
call before further role work when a known window is approached; absence of
metrics is not invented capacity. Such an interruption preserves the consumed
reservation/exact partial snapshot and requires trusted completion/reconciliation
or owner-approved bounded recovery; it cannot automatically replay the role.
Real installed support for this active-context observation is probed separately
and remains BLOCKED when unavailable; pure synthetic metrics are not proof.

New-process resume rereads trusted policy/contract, exact workspace, original
checkpoint/reservation, live identity, admission and lifecycle. Redacted handoff
is delimited untrusted data after policy; it cannot grant actions. Normal fresh
role sessions preserve the existing pinned backend/model, sandbox and tool
allowlist. No full-access shell RPC or user-owned chat is created. Compaction,
restart/kill or stale summaries never refund a reservation. Mid-invocation
context overflow, output limit and quota remain distinct, including their
pending-outcome reconciliation requirements.

## Verification and boundaries

Regression-first for kill after call reservation, stale/corrupt handoff,
changed policy/contract/workspace SHA, active old ownership, stop/revoke/expiry,
reasoning/repair counts retained, context threshold and indivisible oversized
review. Frozen synthetic installed two-role/session restart tests verify no
executor or confirmed-write replay and exact evidence revalidation. Prove
actual context source support separately; fabricated metrics cannot yield real
readiness. Full local gate, independent controller, exact PR/post-merge CI and
exact merge-tree demo are separate. No public pgw/Library/JSON contract,
dependency/release changes, #131 activation, main merge or new authority.

Primary protocol source: https://learn.chatgpt.com/docs/app-server
(thread/tokenUsage/updated and structured ContextWindowExceeded). Installed
protocol schemas confirm invocation thread/turn binding; no real role inference
or compaction is invoked by this design document.

All SHA fields are lowercase40hex; new record digest fields are lowercase64hex
SHA256 of canonical bytes. References to existing prefixed source digests retain
their exact source format and validator rather than silently normalizing them.
Identifier strings follow their original bounded source validators; run_id is
the existing source UUID/hex identity, never free text. Dates/counts exclude
booleans and unknown/non-finite values. Reasoning enums are low/medium/high/
xhigh/max/ultra only when the actual pinned host/backend supports and verifies
them; missing evidence cannot be filled with a claimed Low. Reuse model and
source reasoning policy, never choose a new model from handoff data.

Original role-call upper bounds remain max_repair_iterations+1 per role in the
existing bounded executor/review loop. Handoff creation itself performs no role
inference and creates no extra repair or role-call allowance. An interrupted
role's consumed call stays consumed even when a safe later call is authorized;
exhausted role/time/repair budgets stop rather than reset. Private snapshot
capture uses the approved scope and safe regular-file semantics; symlink escape,
sensitive/instruction surfaces or noncanonical workspace state stop. Snapshots
are local evidence, never delivered externally or treated as reviewed code.
