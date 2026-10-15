# ADR-299: durable bounded waits for concrete epic reads

Date2026-10-11. Task299, epic294. StatusOWNER APPROVED in operator chat on2026-10-11; independent read-only design review PASS with invocation/ordinal/guard details incorporated. This approval precedes production. No installation, activation, mandate issuance or #131 changes.

## Decision

Reuse the actual merged ADR290 implementation (main hotfix1affa4b and subsequent guarded binding wiring), including its closed read-only argv grammar, positively classified transport errors, finite120s request timeout and fixed DELAYS60,60,60,60,60,180,300,600,900,1200. Do not duplicate the classifier or silently include Git/network operations. Legacy Reader behavior and legacy config1/2/3 remain unchanged.

Opt-in epic config4.0 has the managed2.0 keys plus approved_wait_digest and approved_planning_digest. The latter is null for existing approved-recipe-only operation or the exact planning-profile digest enabling the same config3 planning rules. The whole config and runtime-adapter digest are bound by the existing owner-approved launch candidate. No new mandate grant/action or model-selectable wait policy. Owner-private wait-profile1.0 has exactly schema_version,repository,epic,roadmap_ref,policy_sha,mandate_digest,owner_identity,limits. limits has max_phase_seconds (1..86400), max_read_attempts (1..11), max_ci_polls (1..11), max_read_commands (1..512); fixed delays/classifier/output bound cannot be changed by profile/model. Expiry/task/followup/repair budgets remain their existing authority.

Wait stages are DISCOVERY, TASK:<task>:<phase>, SUCCESSOR:<predecessor>:<successor>, FINAL:<phase>, RECONCILE:<task-or-final>:<phase>. Stable identity includes repository,epic,policy SHA,owner-approved wait digest and stage; input/SHA drift cannot create a new stage budget. Per-stage nonblocking lock covers reservation, transport invocation and response persistence. Existing supervisor locks retain their roles; do not hold a new lock recursively through local guards or nested readers.

Closed wait-record1.0 has exactly schema_version,identity,started_at,deadline_at,last_seen_at,requests,ci. identity has repository,epic,policy_sha,wait_digest,stage. requests maps monotonic command ordinal to {command_digest,attempts,status,category,next_check_at}; ci is null or {identity_digest,attempts,status,next_check_at}. Status is RESERVED,WAITING,READY,FAILED; category is null,timeout,network. A new successful fresh read consumes a new command ordinal; completed success is distinct from retrying the same unfinished command. READY records are never used as cached authority. A pending WAITING command resumes its same ordinal and attempts only after matching its exact command digest; changed requests cannot inherit an unfinished ordinal or its remaining attempts; any orphan RESERVED blocks the stage. The stage command counter never resets, and profile max_read_commands plus the absolute deadline bound successful repeated reads too. Persist reservation and consumed attempt before each command/poll. Only a positively classified transient read failure or trusted exact-SHA CI_PENDING persists WAITING/next_check_at. Raw arguments/output/diagnostics/credentials are not saved. Successful JSON/identity/schema validation remains outside the transport retry classifier. Orphan RESERVED after interruption has no positively established transient outcome and requires owner disposition; it is never assumed safe to rerun. Attempts and pending evidence are never refunded or reset by restart/SHA drift/new model/profile replacement.

A private invocation-budget1.0 carries exactly schema_version,repository,epic,policy_sha,runtime_config_digest,command_digest,invocation_id,parent_frame_digest,started_at,deadline_at. The trusted parent CommandRuntimeAdapter reserves a separate frame before launching its fixed child command under existing supervisor ownership. A read-only inherited descriptor carries only this deadline frame, similar to the existing supervisor ownership descriptor; it grants no permission. Nested adapters clamp to the validated parent frame before issuing a child frame. Frames cannot overwrite one another. Child validates owner-private regular0600/nlink1 storage, current launch/config binding and exact fixed command digest before consuming it; issue/config/model input cannot choose a descriptor or executable. Each frame has a fresh parent-generated invocation_id; the trusted spawn clears incoming budget environment/FD values and supplies only its current exact frame/nonce. Reject unrelated/old descriptors or command/config/nonce mismatch. parent_frame_digest is null at root and exact validated parent digest when nested. Only fixed trusted runtime children consume the frame; model/executor and Git/GitHub effect subprocesses never inherit it. Unsupported containment/descriptor hosts fail closed. Stage deadline is first-start plus owner phase bound, clamped to original mandate expiry/max_duration, applicable recovery approval expiry and current trusted invocation deadline. It never increases on restart; insufficient remaining time truncates waiting/request timeout. Parent outer timeout remains enforced. Wall-clock rollback fails closed; foreground scheduling uses monotonic elapsed time while UTC next_check_at/deadline persist. All callbacks are finite and local during waiting, at least once per second and immediately before another read. Stop/pause/revocation/generation/expiry changes cancel waiting without updating authority. Unsupported host/private-lock readiness fails closed.

Explicit read dependency covers registry-bound discovery, structural queue/task provenance, successor admission/preparation, concrete task issue/check/ref and policy-binding reads, final assessment and all read-only reconciliation paths (including final transport.json). Discovery attaches wait state only after exact owner-private registry and local authority binding; no home search or untrusted path discovery. Guard callbacks perform finite local policy/config/mandate/lifecycle/checkpoint checks without network recursion, new readers, effects or unbounded subprocesses. All local/remote Git commands, executor/controller invocations and Git/GitHub writes retain their existing paths and are not retry-eligible.

CI waiting uses only trusted assessor state for exact required checks/current SHA. Failed/malformed/duplicate/missing checks cannot be hidden by another pending check. After a wait, all fresh refs/SHA/gates/authority checks execute before any effect; a read success is never permission. Finite CI polls use the same fixed delay schedule and stage deadline.

Restart continues a persisted WAITING read/CI state through the same guarded foreground entry/resume. It does not retry an entire phase or executor. Pending effect outcomes retain existing exact checkpoint/ledger reconciliation: APPLIED never repeats, NOT_APPLIED allows only the already-authorized bounded driver retry, UNKNOWN requires owner decision. A wait file cannot clear pending effects, owner stop, recovery generation, final main approval or repair budgets. An interrupted write is not classified as a read wait. The whole phase is never blindly replayed just because its last read became available.

This task establishes durable foreground wait continuation, not cross-turn scheduler wakeup. Real supported host wakeup is #300 and cannot be claimed from a timer or a saved resume string. Final main human approval remains mandatory for actual current SHA and demo.

## Verification and limitations

Regression-first synthetic fake-time tests cover initial+bounded attempts, elapsed request duration, deadline clamp/clock rollback, saved waits/restart, unclassified reservation crash, auth/schema/identity refusal, stop/pause/revoke/expiry, CI_PENDING vs any terminal failure, all named clients, Git exclusions, UNKNOWN writes with no duplicate effects, output bounds and zero unauthorized effects. Full local gate, independent controller, exact PR/post-merge CI and exact-tree synthetic demo separate. No public Library/pgw contracts, existing checkpoint/ledger/mandate/runtime-adapter schemas, release/dependency/workflow changes. New closed private records and config4 are internal opt-in. Atomic visibility does not imply crash durability, cross-file transaction or exactly-once delivery.

## Owner-approved corrective clarification

On 2026-10-11 the owner approved one bounded corrective cycle for four
independent-controller findings. Read-only discovery/live/reconciliation may
observe an existing PAUSED or STOPPED latch without changing it; active/effect
contexts still require RUNNING, and every existing wait cancels on control
state/generation change. The mode comes from the trusted fixed caller/command,
not a profile or model input. STOPPED remains terminal for effects.

Stage contexts are scoped to the actual saved task/cursor/phase or successor
preparation and restored after callbacks. Restart routes the same work to the
same stage and retained budget. Task-local checkpoint/policy and applicable
recovery generation/approval/expiry are checked locally before another read
and during waiting; a network-bearing admission callback is not a wait guard.

An unclassified RESERVED CI poll never resumes automatically merely because
another stage request is WAITING. A positively classified transient read within
the currently reserved trusted read-only poll saves CI WAITING and the read's
WAITING state/due time together in the existing closed wait record before
sleeping. Restart of this established WAITING path consumes a new bounded poll
attempt. An interruption before the assessor's own read remains unknown.
Counters are never reset or refunded. No fields, schemas, effect permissions,
runtime installation/activation or final main approval are added by this
clarification.

## Candidate addendum: exact repository metadata GET eligibility (#302)

Status: owner-approved in operator chat on 2026-10-14 after independent design/static PASS. No installation or activation is authorized.
The current bounded native Session reproduction reaches canonical
`GitHubCLI.repository(read=...)` during successor branch preflight. Its exact
`gh api repos/OWNER/repo` GET is rejected by both ADR-290 generic read grammar
and ADR-299 repository-scope eligibility before transport or reservation. The
synthetic primary fixture already supports this endpoint. This is an internal
read integration gap, not evidence of successful successor delivery.

Proposed security decision: add only the exact three-argument command
`("gh", "api", "repos/<owner>/<repository>")`. Owner/repository components use
the existing ASCII `[A-Za-z0-9_.-]+` grammar and the existing `..` exclusion.
The durable Reader additionally requires exact case-sensitive equality to its
already approved repository identity. No optional suffix is added to the
existing descendant grammar; that grammar remains unchanged.

The new root form permits no query, trailing slash, URL, extra endpoint,
pagination/slurp/hostname/input flag, method override or body field. Foreign
repositories and prefix collisions remain ineligible. This is a narrow change
to a security read grammar and requires explicit design approval before
production code; current issue/fixture text and the prior repair exception do
not authorize it. It grants no Git/GitHub write, new repository scope or role
call. Existing guards, reservations, counters, attempts, deadlines, pending
outcomes and errors remain unchanged. A successful read is not authority.

Regression-first verification requires the exact canonical client request to
fail on the old code, then complete through the bounded Reader after approved
application with one retained command/attempt. Negative root/foreign/query/
flags/method/URL/traversal cases must reject before transport and reservation.
The original actual native Session must then reach authorized successor
execution/delivery/promotion under original budgets; fabricated completion is
not proof. Full local gate, independent whole-task controller, exact PR and
post-merge CI and demo remain separate requirements. No installation,
activation, provider purchase/reset, stopped #131 or main merge is included.

## Candidate addendum: closed compare reads and fresh synthetic capacity (#302)

Status: owner-approved in operator chat on 2026-10-14 after independent design/static PASS, including initial200 for NEW synthetic fixtures. No installation or activation is authorized.
The actual frozen native Session rejects canonical `gh api
repos/OWNER/repo/compare/main...roadmap%2F294-autonomy` before transport because
ADR-290's generic `..` prohibition also rejects the required `...` delimiter.
Root repository metadata eligibility was separately accepted on 2026-10-14.

Proposed security decision: reserve the API `compare` route for exactly three
arguments `(gh, api, repos/<owner>/<repository>/compare/<base>...<head>)`, with
exactly one delimiter and two nonempty canonical ASCII references. Only uppercase
`%2F` encodes slash; every other percent encoding, query, URL, flag, method or body
is ineligible. Decoded references are1..255 ASCII characters, each component uses
`[A-Za-z0-9_.-]+`, and cannot start `.`, end `.`/`.lock`, contain `..` or be empty;
a complete reference cannot start `-`. Encoded references are at most765 bytes.
Repository components retain existing ASCII grammar and `..` refusal. Durable
Reader retains its exact approved repository boundary, including prefix-collision
refusal. Other non-compare descendant grammar is unchanged; malformed comparison
routes that legacy grammar previously admitted now fail closed. The universal
traversal prohibition is not removed. Canonical clients keep their original ref
arguments; switching them to exact SHAs is deliberately outside this scope.

Separately approve initial max_read_commands100->200 only in the NEW synthetic
native factory, within initial wait-profile construction before dependent
runtime-config/launch digest binding and any role/read execution. The installed
policy candidate is constructed earlier and is not a dependent wait binding.
Retained compare-failure state has92 completed reads; a static forecast adds
compare1 +precreation guard5 +postcreation guard5 =103.200 provides97 finite
slots beyond that no-dependency path for
additional source/dependency validation. This is a conservative test allowance,
not timing or full-run success evidence. Production limits/schemas and real
mandates do not change. Original retained states/counters remain immutable;
READY reads still consume slots, UNKNOWN is not replayed, and no budget is
refunded. Original role, repair, planning, CI-poll and time budgets are unchanged.
Exhaustion still blocks;200 is not automatically enlarged.

Regression-first tests use the canonical client and unchanged source for RED,
then bounded Reader completion after approval. Test the complete canonical read
inventory and malformed/traversal/encoding/foreign/query/flags/method negatives.
Actual original fresh native Session delivery/planning/promotion must pass under
its explicitly approved initial capacity; source validation and guards cannot be
skipped to manufacture completion. Original planning/planning_review completion
and source-measurement accuracy remain to be demonstrated by the native run.
Missing comparisons retain accepted UNMEASURED/NO_MEASURED_COMPARISON behavior;
no extra baseline calls or simultaneous all-four-role pair is required. Actual
E/C paired regression remains separate primary evidence. Full gate/controller/exact CI/demo
and installation/main approvals remain separate. This candidate grants no
external write, provider activation, new repository or stopped #131 permission.
