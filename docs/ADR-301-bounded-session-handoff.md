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


# ADR-301 addendum: verified role settings for bounded continuation

Status: accepted; independent design review PASS and owner approved before transport/settings production code on 2026-10-11. Scope #301 / epic #294. No installation, activation, mandate or epic-to-main permission.

## Concrete gap

Current fixed legacy codex_adapter accepts optional model but neither pins reasoning effort nor receives effective-setting evidence. A claimed Low, owner-approved digest alone, argv or supported enum is insufficient for ADR301 consumed.reasoning_evidence_digest. Do not invent that evidence or silently weaken the accepted handoff.

## Proposed bounded solution

Opt-in config6 uses the existing #300 fixed App Server role transport, including when approved_quota_digest is null. Config1–5 retain their existing paths. This extends the transport selection to config6; null quota must not poll quota, install wakeup, emit PAUSED_RATE_LIMIT or activate #300 quota behavior. Native source and settings are separately pinned through the continuation profile backend_digest.

A new internal closed role-backend1.0 document outside worktree contains exactly schema_version, source, model, original_reasoning, repeat_reasoning_levels. source has exactly executable, executable_sha256, version_digest, request_timeout_seconds and reuses the existing strict #300 validators: absolute safe executable; lowercase64hex hashes; non-boolean timeout integer1..120. schema_version is 1.0; model is bounded nonempty owner-selected identifier; original_reasoning is low; repeat_reasoning_levels is exactly [medium,high]. The owner profile backend_digest is the canonical bare SHA256 of this whole document, binding source/model/settings. No model or checkpoint chooses a replacement. If the optional quota profile is present, its source must match this source exactly. Model support/effective settings must be proven for each invocation, not inferred from this document.

Before turn/start, initialize a fresh thread inside the existing Bubblewrap boundary and send explicit pinned model and config.model_reasoning_effort. Require ThreadStartResponse.model and reasoningEffort to match the requested values, together with existing exact cwd/sandbox/approval checks. Installed0.157.1 schemas expose these response fields. This is effective configured-thread evidence, not token usage or per-turn compute telemetry. Do not send model/effort override in turn/start; a fresh thread permits only one turn. Bind the resulting closed redacted settings receipt to the exact thread, subsequently accepted turn, request invocation, pinned backend and observed terminal outcome. Missing/null/mismatch or an unsupported model effort fails closed before provider work when the thread response fails; a later ambiguous invocation remains consumed/UNKNOWN. No raw prompt/output/reasoning is included in the receipt.

Preflight measures the entire fixed adapter prompt before starting the role. At threshold before any verified invocation settings exist, the bounded PREPARED handoff may be created only when prior matching settings evidence is available from a verified completed call; otherwise preserve the reservation/snapshot facts and return CONTINUATION_REASONING_UNVERIFIED plus explicit capacity blocker, without fabricating a conforming handoff. This limitation is visible, not a claimed completed transfer. Completed executor supplies verified settings for the controller-boundary handoff. Interrupted executor preserves consumed reservation and exact partial snapshot; UNKNOWN never automatically restarts. Two automatic repair reasoning increases remain governed by CONTRIBUTING; ordinary calls return Low without refunding iterations.

## Readiness and checks

Fullprompt/pre-call guard is assessed separately from safe-boundary recovery and optional active-context observation. Active context remains BLOCKED without a supported bound source. Native no-inference schema/settings support checks cannot prove real inference completion or host persistence. Frozen synthetic transport/E2E covers accepted configured-thread echo, malformed/mismatched/null settings, no override, crash before/after receipt, original budgets, no executor/write replay, config6 null/non-null quota composition and all existing sandbox/source guards. Real activation still requires separate owner installation/mandate and supported host readiness.

Threat boundaries: owner-approved private source/settings only; untrusted task/handoff cannot change source/model/effort; no inferred settings, quota authority or scope expansion; no CLI/Library public contract changes. Cross-file atomicity/crash durability is not strengthened.

## Closed settings receipt1.0

Exactly schema_version, role, invocation_digest, backend_digest, requested, effective, thread_digest, turn_digest, outcome, terminal_digest, receipt_digest. schema_version=1.0; role=executor/controller; all digest fields lowercase64hex canonical SHA256 except nullable turn_digest/terminal_digest below. requested and effective each contain exactly model and reasoning; model is the bounded owner-selected identifier (nonempty1..128 characters, no controls), reasoning low/medium/high and equal to the approved effort for this consumed call. Requested/effective pairs must be equal. thread_digest hashes the bounded native thread ID; turn_digest hashes the accepted native turn ID. outcome is CONFIGURED/COMPLETED/FAILED/UNKNOWN. CONFIGURED requires turn_digest=null and terminal_digest=null; it proves only accepted thread configuration. COMPLETED requires nonnull turn_digest and terminal_digest; FAILED/UNKNOWN never qualify for handoff authority. receipt_digest hashes all other fields.

terminal_digest binds a mechanically captured closed terminal fact with exactly role, invocation_digest, thread_digest, turn_digest, status, validated_output_digest, adapter_exit_code. status is completed/failed/unknown, validated_output_digest is nullable lowercase64hex of the validated bounded canonical role output (never output itself), adapter_exit_code is null or an integer0..255 excluding bool. COMPLETED is saved only after matching native turn/completed status completed, schema-valid role output and observed adapter process exit0, matching unchanged scope/HEAD and reservation. A configured receipt cannot be promoted from a model response. An unobserved/conflicting exit is UNKNOWN even if configured-thread evidence exists.

Only a source-reread COMPLETED receipt bound to the same owner backend/contract and original consumed reservation can supply handoff reasoning evidence. Its requested/effective effort must equal that call's verified ordinal/repair decision; the full original consumed repair/call chain is reread, not reset. Receipt hash or a valid-looking model-authored receipt does not establish origin. Store outside task worktree, save through trusted adapter/manager under role lock; task/model writes cannot create this source. Preserve cross-file conflict fail-closed behavior. Readiness without a qualifying completed receipt does not promise a first-call capacity handoff.
