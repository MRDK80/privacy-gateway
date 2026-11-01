# ADR-303 prospective workflow-learning bindings

Status: accepted design, implementation/readiness separate. Owner accepted binding
sections1–3 via call_b74530adb1f34ad69e64b9297d06c220 and exact known-transitionv2
SHA4c4daeb68a7a969994a2ec8b34d41787a1c03aa91065035cc9790094a20c794c
via call_f74f9a02f8f34b21ba87ec1ff61d0522, after independent bounded review.
This internal prospective config8 source contract does not change config1–7,
restore input/prompt preimages, grant runtime mandate or authorize owner setup,
installation/activation/provider calls. Original pinned writer association is
recorded provenance, not independent reconstruction of raw input. Missing new
source stays blocked. No backfill, extra calls, refunds, session/prompt archive.

The normative accepted subset follows. InventoryN1/sections4–5 aggregate/report
policy, finalreader/READY and deployment are excluded and require separate work.
Shape/digest alone grants no source, owner or effect authority. Atomic visibility
is not cross-file crash durability; same-OS-owner malicious copying remains the
existing excluded boundary.

## 1. Fixed original writer boundary and lock protocol

Require original task RunnerLock and original Continuation context ownership already held by ReviewedWorkflow; assert ownership before every binding action. No binding-specific lock and no binding constructor creating other original namespaces. Original dispatch lock order remains task -> continuation/ownership. SettingsAdapter alone acquires seed role-settings/ownership while calling provider, releases before Continuation.complete; binding never holds metadata/store locks across provider work.

Reserve integration point is after original agent_orchestrate.reserve_role1608–1646 returns from learning_runtime.reserve, including its metadata RESERVED save AND retain(problem companion) successful reread; before adapter.execute/review. Original canonical consumed state and Continuation reservation/checkpoint already exist. After those functions release their local metadata/settings ownership, binding writer serially acquires settings/ownership, metadata-directory lock, then learning-store lock only if needed for validation; NEVER acquires outer continuation/task while holding an inner lock. It validates shared original payload association using same still-live safe payload and one-pass selection proof, writes RESERVED binding under existing task ownership, rereads original canonical pending/native checkpoint/reservation/metadata/companion/binding and original guards; only then dispatch allowed. Any failure means no dispatch, no budget refund, no second reservation or fallback. RESERVED association remains evidence of consumption, never completion.

Completion integration point is AFTER both Continuation.complete and learning_runtime.complete return successfully for that exact still-live call. Existing Runtime.complete171–230 validates native settings/reservation/prompt, writes metadata COMPLETED and appends invocation to Store. Binding COMPLETED requires metadata file == exact invocation record in that task's Store, matching original problem companion, completed native reservation/settings and unchanged stable metadata. Append failure or missing Store invocation leaves binding RESERVED; no positive completion. Acquire validation locks serially in fixed order settings/ownership -> metadata -> learning Store, with outer task/continuation already held; release them before any later provider. Revalidate original guards before replace and after reread. Fresh process may not complete RESERVED binding; UNKNOWN/stop/expiry/deadline take priority and no handoff/recovery synthesizes association. This explicit refusal is prospective config8 policy; its consequence may be blocked/reserved on interrupted calls.

## 2. Closed parser and digest rules

Binding fixed task-relative namespace workflow-learning-bindings; filename exactly <invocation_key>.json; binding task resolution from verified original domain only. Exact keys identical v1: schema_version,identity,role,ordinal,repair_iteration,invocation_key,stable_metadata_digest,native_invocation_digest,native_reservation_digest,checkpoint_digest,ordered_lessons,selection_digest,input_digest,prompt_digest,status,completion,record_digest. No optional/extra/duplicate JSON keys. schema_version literal1.0. Every digest in this new family is untagged lowercase64hex from existing tools.agent_continuation.digest, except prompt_digest is existing SHA256(_prompt(...).encode()) also64hex; no sha256: prefix. policy_sha inside identity is lowercase40hex. No float/bool accepted for integer fields.

identity exact existing invocation identity: repository validated existing repository function; epic/task positive <=2147483647 and distinct; policy_sha40hex; owner_profile_digest64hex; original_run_id existing nonce24/32/64hex and must equal actual canonical workflow run_id32hex. role executor/controller only; ordinal1..(contract.max_repair_iterations+1), repair_iteration0..contract.max_repair_iterations (existing maximum2); executor ordinal=repair+1; controller matches original chain/counters. invocation_key64hex equals existing records.invocation_id(original metadata) AND filename stem. stable_metadata_digest=digest(validated metadata minus exactly schema_version,record_digest,status); all other original fields retained. Remaining scalar digest fields match original metadata or source exactly; no null scalar digests.

ordered_lessons list length<=original owner max_hints_per_role and <=3; exact row {lesson_id,verification_digest,candidate_digest,before_digest,after_digest,pair_digest}, all64hex, unique lesson_id. Ordered bijection to actual original hint envelope; sorted lesson IDs equal metadata.lesson_ids. selection_digest equals digest(actual ordered hints), verified from live same safe payload at original reserve. Zero hints requires successful unchanged selection pass, digest([]),zero references, genuine metadata/native association. Not an absence fallback.

status literal RESERVED/COMPLETED. RESERVED completion=null; metadata originally RESERVED/native reservation RESERVED, after_snapshot/outcome null. COMPLETED completion exact {native_reservation_digest,settings_source_digest,settings_receipt_digest,terminal_digest,completed_metadata_digest}, all64hex. settings_source_digest=digest(full exact validated {receipt,terminal}); settings_receipt_digest=digest(validated receipt); terminal_digest=digest(validated terminal); completed_metadata_digest=digest(full validated sealed COMPLETED metadata). Important: receipt.receipt_digest is digest(receipt minus receipt_digest), so it is independently checked by existing validate_completion and MUST NOT be confused with digest(full receipt). Final native outcome_digest must equal validated receipt.receipt_digest. All recorded completion digests use full canonical objects as just specified.

Top-level native_reservation_digest binds original RESERVED form; final native completion is reversed exactly by status=RESERVED,snapshot_after=null,outcome_digest=null, justified by Continuation.complete205–247 copying every other field unchanged. Completion.native_reservation_digest binds full final COMPLETED form. checkpoint_digest matches original reservation.checkpoint_digest and full canonical checkpoint hash. record_digest=digest(full binding minus only record_digest); schema parser requires this exact equality, but self digest is no authority without original ownership/source joins. Per-file max_record_bytes from validated learning owner; per-task binding namespace max_store_bytes from same owner; whole original directory rejects symlink/hardlink/unowned/nonprivate entries by existing security predicates.

## 3. One-pass original selection proof seam

Internal result is exactly (actual_envelope, ordered_reference_rows), returned by the SAME Verifier.selection evaluation. Existing selection consumers still receive actual_envelope. During that single pass, at each actual selected.append(hint), retain that exact verified record,candidate,rule,after,before and successful recomputed pair whose existing checks confirmed it; project six row digests. Do not call selection again, reorder selected/verifications, choose another matching pair later, change duplicate suppression or cap. If selected is reset to[] by zero cap, references reset in the same branch. Require equal lengths and per-index hint.lesson_id/reference.lesson_id before returning. The selection code's nested after/before search must capture proof exactly when confirmed, not use a stale variable after another iteration. Every emitted hint gets one proof; no proof for suppressed duplicates.

Runtime.augment retains this result with its existing process-local prepared_digest; reserve matches digest(payload stripping session_id/trusted_policy/contract_object) to original prepared selection association as existing companion protocol does. It also checks the full live payload/envelope and metadata input/prompt digests. References must resolve to full original retained candidate/verification/observation records, original catalog rule and pair predicate at dispatch. Reader later recomputes pair using those ORIGINAL inputs while retaining tombstoned historical use neutrally. It never reselects current hints or exports envelopes. New trust is the original writer's contemporaneous input/prompt/selection-to-native association; independent reader proves recorded joins and original evidence, not preimages. If that invariant is unacceptable, reject this design.


## Exact internal witness shape and authority

Exact fields:
{runtime_identity,reader_identity,task,role,contract_digest,owner_digest,catalog_digest,config_digest,pre_canonical,first_state_neutral,retained_origins,selected_rows,prepared_digest,completed_prefix,handoff_prefix,lifecycle}.

Object identities are exact existing Runtime/reader references, not JSON or IDs supplied by caller. task/role and raw lowercase64 digests derive original validated source. All neutral objects remain process-local repr-suppressed; no public third tuple, JSON constructor, disk restoration, callback/boolean authority or raw prompt/session/full payload. Existing selection envelope/reference result unchanged. Existing owner byte/record/call bounds apply; no new quota. Source object comparisons use exact canonical bytes/representation, not Python numeric equality.

pre_canonical is original bounded validated workflow canonical object at SAME selection pass, nullable only when existing canonical() positively establishes no prior workflow. If nonnull first_state_neutral MUST null. If null first_state_neutral MUST be the closed nine-field object below; no defaults at reserve.

## R1 — explicit first_state_neutral

Exact keys {run_id,started,repair_iterations,executor_calls,controller_calls,findings,verdicts,failures,continuation_handoffs}. Capture internally from the fixed original orchestrator locals BEFORE augment under original task+continuation ownership, bound to exact Runtime/reader/contract/task. Not accepted from external caller or reconstructed after reserve.

run_id is original lowercase32hex. started retains original finite nonnegative JSON int/float representation, bool excluded; Unix wall-clock seconds. First-state requires repair_iterations=0,executor_calls=0,controller_calls=0,findings=0,continuation_handoffs=0,verdicts=[],failures=[]. All counters strict int/nonbool and original contract/profile bound; verdict/failure values use existing original workflow validators, not free prose. No new run ID/time generated by witness. First-state is executor only. Compare run/start and complete expected replacement to first actual canonical/checkpoint; original reservation started_at uses int(started) as actual Continuation.reserve does, not exact numeric equality with timestamp representation. Native reservation deadline remains original min(int(started)+contract.max_minutes*60,outer_deadline).

For nonnull pre_canonical, use v1 exact executor/controller replacement table: selected role count+1, opposite unchanged, run/start/repair/findings/verdicts/failures/handoffs unchanged; terminal=false,status=running,machine_code=ROLE_RESERVED,role_pending=current role, invocation=live safe digest, executor boundary=null/controller boundary=verified live original executor gate. Repair increments are already persisted before augment. Old optional completed_executor/continuation_gate are omitted by reserve_role replacement, not spuriously required to survive. Exact closed reserved key set is the actual reserve_role construction (:1608–1639), not union of old and new fields.

## R2 — explicit completed_prefix source commitment

completed_prefix is a list sorted lexicographically by(role,ordinal), no duplicate (role,ordinal) or native invocation. Each row EXACT:
{role,ordinal,native_invocation_digest,native_reservation_digest,checkpoint_digest,metadata_digest,problem_digest,settings_source_digest,controller_review_digest}.

role executor/controller; ordinal strict1..contract.max_repair_iterations+1. Every digest raw lowercase64hex except controller_review_digest=null for executor and required64hex for controller. Hashes bind full original validated sealed canonical objects, not their record_digest fields; settings_source_digest hashes full exact {receipt,terminal}, NOT receipt selfdigest. Native reservation is actual COMPLETED full object. metadata is exact COMPLETED metadata matching Store invocation; problem is original required companion. Controller review is original continuation review-<inv>.json verified by existing chain. No raw snapshots/payload captured: reservation/checkpoint commitment binds their original references, and original snapshot validators remain mandatory on fresh reread.

Derive rows ONLY from successful original pre-selection _source_complete and _completed_role_chain for current task; match original COMPLETED metadata one-to-one on role/ordinal/run/task/repair/catalog plus problem companion. Exact filename derivations:
continuation/reservation-<native>.json; continuation/checkpoint-<native>.json; continuation/review-<native>.json for controller; seed/role-settings/receipt-<native>.json; learning-invocations/<records.invocation_id(metadata)>.json and <key>.problem.json. No arbitrary path or digest-only task assignment. Validate full original contents/identity/private protections/caps before hashing. Prefix set equals both canonical pre-call counters and original native+metadata completed-call sets; no missing/extra/pending row. First-state prefix empty only after original absence/zero-call validation, not empty fallback. Existing call bound is 2*(max_repairs+1); per-file owner limits unchanged.

At dispatch fresh reread/validate every row and require canonical digest equality before AND under inner current-task locks, retaining original snapshot/settings/controller predicates. All prior complete sources remain unchanged. Only exactly one new original RESERVED reservation/checkpoint+metadata/problem is outside this set; current_pending_prefix checks that native prefix but never supplies retained completeness/terminal authority. Reserved input/prompt and native invocation are checked from still-live actual safe input; no saved full input.

## Exact independent handoff_prefix

handoff_prefix is null iff original fixed deterministic path is positively absent under original task+continuation ownership. Otherwise exact {key,source,source_digest}; key='handoff-'+digest({contract:original _contract_fields(contract),run_id:original run_id}); source is full bounded original validated neutral handoff; source_digest=digest(full source). No caller path. Original source shape excludes raw prompt/session/input; existing handoff limits apply.

For controller when pre_canonical has completed_executor and actual original path requires consumption: source MUST PREPARED and existing Continuation.verify_handoff(pre_canonical) must succeed BEFORE capture. Actual consume_handoff validates the same original pre-reserve state before changing source. Afterward require exact expected object {**source,status:CONSUMED,record_digest:digest(all fields except record_digest)}. Every other field/representation identical. Existing verify_handoff accepts PREPARED only; do not falsely call it on CONSUMED or fabricate a PREPARED file. Validate consumed shape with existing records.handoff and exact captured-object transition, original fixed key and guards. No new/absent handoff may appear. Controller without consume condition requires source unchanged, including null. Executor requires source unchanged. Handoff exception never waives completed_prefix equality; count remains unchanged by consume_handoff (:539–556).

## Retained/proof commitments and permitted live delta

retained_origins is exact ordered list {task,store_kind,records_digest,record_identities}; store_kind fixes original workflow/planning store domain, identity rows exactly {kind,key,record_digest} sorted(kind,key), hashes full validated records. Capture all original selection stores/negative barriers and aggregate cap proof, not only selected references. selected_rows is exact approved ordered six-digest projection, captured at actual successful pair/selected.append within SAME pass; unchanged dedup/caps/zero branch/final max_hint_bytes check. prepared_digest is original augment safe commitment; existing companion strips newly added session_id/trusted_policy, while full live input/prompt/native digest is separately checked.

Before inner locks, freshly validate all extant stores/ensure_usable/negative barriers and require complete membership/origin equality to witness. Resolve original selected references and unchanged pair/terminal proofs under their original outer ownership one task at a time. No generic retained() on pending current task or _retained_for_capture waiver. New predicate combines validated old prefix with exact known single-call delta only. Current new canonical/checkpoint/native/metadata/problem must match v1 table plus the commitments here. No Store append at reserve; Store rows therefore unchanged. Stop on any source/tombstone/latch/owner/deadline/config drift, even unrelated store changes; no reselection/default zero hints.

After cross-task proof, inner locks only settings ownership→metadata→currentStore under already-held task/continuation outer locks; no cross-task acquisition/generic pair under inner, no recursive settings ownership. Recheck fixed current prefix/delta/handoff/guards; write+reread binding RESERVED; release inner before provider. Original original-source guards and read budgets apply. No cross-store atomic snapshot claim.

## Fail-invalidate lifecycle

lifecycle exact internal CREATED→CONSUMING→CONSUMED or INVALID; never persisted/admitted from caller. CREATED only after complete same-pass capture. Set CONSUMING BEFORE first reserve attempt; any preflight/reserve/native/metadata/companion/binding/check/dispatch-admission error invalidates immediately in finally. No retry while false/unused, no second reserve, refund or recovery. Successful binding write+reread transfers the single same-live-call completion capability to the existing Runtime call and makes witness CONSUMED; it cannot approve a second dispatch. Provider uncertainty/stop/expiry invalidates completion capability; abandoned binding remains RESERVED. A new process/Runtime/handoff cannot restore either witness or completion capability. Completion still requires original native+settings+metadata+Store success per approved sections1–3.



## Fixed source correction: live role-specific contract projection

Implementation correction proposal only, requiring the separately reviewed repair
exception; no change to the accepted writer trust boundary. The complete
TaskContract canonical digest still binds the original witness/context. A live
executor payload contains the existing full `_contract_fields(contract)` map.
A live controller payload contains the distinct closed original controller
projection, including remaining_repair_iterations = original contract maximum
minus the source-backed current canonical repair count. It omits the other
executor-only full dataclass fields; those are not invented as controller input.

Extract the existing exact controller map and its conditional delivery/policy
fields into one fixed private producer helper, used by both the original
controller producer and Binding.capture. Keep every final payload key/value,
outer field, condition and witness/input/prompt digest rule unchanged. Capture
requires strict-int repair_iteration equal to zero for the original first
executor, or to the exact freshly matched previous canonical repair count,
bounded by the original contract. No caller repair count, subset/intersection,
unknown optional field, projection callback or normalization of actual payload
is accepted. Counterfactual remaining/delivery/policy/unknown/bool inputs refuse.

This correction does not waive either complete non-current original proof when
its Store is absent, or full current completed-prefix/native/metadata/Store
bijection at capture and fresh dispatch, including zero hints and reverse
current-run orphan refusal. Original settings->metadata->current Store order
and no outer/recursive Store acquisition remain mandatory. The source-backed
initial301 fixture prerequisite uses the original task_adapter writer before
302 capture; missing runtime-task is never a no-canonical fallback. Staged
causal baselines and their original120/caps/history limits require explicit
owner execution scope; no repair/application/test authority follows here.
