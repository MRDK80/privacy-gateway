# ADR-295: bounded autonomous epic launch and mandatory human gates

Date: 2026-10-11. Task #295; epic #294.

## Status and authority

Proposed for owner review. This document specifies a future opt-in profile;
it does not amend installed policy, activate runtime or authorize delivery.
Until separately accepted and implemented, ADR-249, ADR-255, the role
contracts and CONTRIBUTING.md retain their existing approval requirements.
An epic number, chat skill invocation, issue, PR, model output or this ADR
cannot create a launch mandate. Owner acceptance of this design is separate
from installation, launch authorization and final main merge approval.

## Context

The existing runner has bounded mandate authority, task gates, reconciliation,
quota pause and verified hints. It does not establish a verified single-chat
launch with automatic planning, persistent waits and cross-session wakeup.
Existing reviewed plans and owner-only demo receipts must not be silently
replaced by model assertions. Epic #294 extends that lifecycle rather than
reimplementing #248 or weakening its stop boundaries.

## Proposed decision

### Launch authorization and readiness

The owner reviews one closed, versioned launch profile outside all worktrees.
It binds one canonical repository identity, one structurally verified epic,
one roadmap ref, a pinned trusted policy SHA and installed bundle identity,
owner/approver identity, issuance and expiry, an operation allowlist, session
duration, task/repair/follow-up limits, wait limits and approved task/demo
profiles. A canonical digest binds all approval-relevant fields. Any change
requires a new owner approval; runtime cannot renew it. The existing
`max_repair_iterations <= 2` cap remains mandatory and is not amended.
Absence from an
allowlist is denial. Concrete serialization and version numbers require the
implementation task's schema decision and compatibility tests; no existing
mandate or checkpoint schema is changed by this ADR.

Launch preflight is read-only. It verifies installed bytes/provenance,
supported host and sandbox, structural queue/dependencies, roadmap ancestry,
current refs/SHA, authority lifecycle and absence of a competing runner.
It separately probes process execution, session observation and wakeup
capability. A successful import or CLI version does not prove these three
capabilities. Unsupported or unproved required capabilities block autonomous
launch; the owner may choose a separately approved manual profile.

The future chat entry offers start, status, stop, revoke and resume semantics.
Status exposes bounded identifiers, phases, verified progress and stop codes,
not raw logs, payloads or private paths. Stop prevents new effects and latches
the current authorization; revoke invalidates its lifecycle. Neither action
rolls back an applied write. Resume never clears stop/revocation or issues
authority: it reconciles pending effects and checks the still-valid profile.

### Roles and generated plans

| Role | Permitted responsibility | Authority boundary |
| --- | --- | --- |
| Planner | Propose task criteria mapping, minimal file scope, dependencies and demo class | Generated plans are untrusted; no new permissions or criteria |
| Coordinator | Select the next structurally ready task and validate its plan against the approved profile | No inferred parent from titles/text; no scope expansion |
| Executor | Edit the admitted file allowlist and produce evidence | No Git/GitHub writes or self-review |
| Deterministic gate | Verify branch ancestry, diff and all mandatory local checks | Success grants no authority |
| Controller | Independently review exact patch using pinned trusted policy SHA and task criteria | Read-only; cannot accept a new trust/security decision |
| Supervisor | Persist phase progress, bounded waits and single-runner ownership | No new approval, budget refill or blind phase replay |
| Delivery driver | Execute allowlisted effects after immediate authority, SHA and evidence revalidation | Sole Git/GitHub write path; unknown outcomes require reconciliation |
| Learning | Record bounded retrospective and select verified untrusted role hints | Cannot change skills, policy, criteria, tools or budgets |

Proposed automatic plan admission replaces routine per-task plan approval only
within a separately owner-approved task profile. Before executor invocation,
deterministic validation checks the full acceptance criteria, file allowlist,
operation scope, policy ancestry and remaining budgets. Ambiguous mapping,
new architecture/security/trust decisions or unavailable minimal scope stop
for a human. This is a proposed amendment to the existing human-pinned task
contract rule, not authority to use generated plans today. Follow-ups require
verified classification, structural parent/dependencies and an approved finite
budget; their text cannot expand the launch scope. External unrelated work
does not enter the queue.

### Task demos and final approval

The owner approves the demo profile before task implementation. Three explicit
classes are proposed:

- Documentation without observable runtime change records
  `DEMO_NOT_APPLICABLE` with a reviewed reason.
- An admitted synthetic task profile may run a reviewed executable demo with
  synthetic inputs only and bind its measured result to baseline and exact
  post-merge roadmap SHA. Its receipt is labelled synthetic and never claims
  owner observation or production success. This proposed substitute for
  routine task confirmation requires owner approval before implementation.
- A task requiring real owner input or human assessment retains the existing
  owner-only demo path and pauses. A prepared command is not an executed demo;
  no synthetic result can manufacture an owner receipt.

The launch profile never authorizes final main merge on its own. The future
final driver prepares and checks the aggregate roadmap PR, then persists a
human gate. Approval binds repository/epic, PR identity, current base main SHA,
roadmap head SHA and the final owner-observed demo evidence. SHA or evidence
changes invalidate that approval. Immediately before an approved merge the
driver revalidates authority, identities, final local gate, independent review
and exact PR CI. New main post-merge CI is a separate requirement before epic
completion. The final demo remains owner-confirmed even if all task demos
were synthetic. A missing suitable real scenario requires an explicit owner
disposition rather than a fabricated success.

Every task retains the full local gate, independent controller, five exact
task PR checks and five separate post-merge roadmap checks. CI evidence binds
the actual tested SHA. Synthetic demo admission cannot waive those gates.
Task closure/update requires the approved delivery allowlist and completed
task gates. No task PR targets main directly.

### Waits, stops and decisions

These are proposed lifecycle categories, not new machine codes:

| Category | Examples | Next permitted action |
| --- | --- | --- |
| Recoverable wait | Positively classified transient GitHub read, CI pending, structured Codex quota exhaustion | Persist bounded wait; read-only recheck, then revalidate before admitted resume |
| Terminal stop for current authorization | Owner stop/revoke, expiry, exhausted budgets, stale authority, unknown potential write | Preserve evidence/latch; no automatic reopening or repeat write |
| Human decision | New security/architecture policy, ambiguous scope, required owner demo, unsupported capability, final main merge | Present concrete evidence/proposal and wait for explicit applicable approval |
| Task gate failure | Failed tests/checks or controller finding | Repair only inside original scope and remaining budget; otherwise human decision |

Unknown write outcomes first require read-only reconciliation. Even proven
`NOT_APPLIED` cannot reopen a terminal recovery guard; ADR-284's separately
pinned recovery approval and consumed attempt rules continue to apply.
Authentication/permission errors, malformed output, unsupported adapters and
unclassified errors are not transient waits. Context exhaustion is distinct
from quota and cannot truncate independent review.

ADR-290's request retries keep their existing schedule and guards. A future
persistent wait supervisor records reason, exact identity, next eligible
check, bounded deadline, attempts consumed and evidence of wakeup ownership
outside the worktree. It does not retry writes, reset elapsed time or extend
authority. Restart preserves consumed budgets and pending intents; an
unavailable or ambiguous clock/reset requires read-only verification or a
decision, not guessed authority. Quota availability is reread rather than
inferred from a reset timestamp. CI pending is checked against the exact SHA.

Cross-turn wakeup requires an installed, explicitly authorized and tested
mechanism. A prompt, running process or recorded future time is insufficient.
Wakeup first reacquires single-runner ownership, reconciles pending effects
and validates original authority/current identities. Loss of observation
does not prove process exit or allow a duplicate runner. No purchases/reset
credits, force push, tags/releases, repository settings or broader sandbox
fallback are admitted. Existing supervision remains applicable until this
mechanism is separately implemented and demonstrated.

### Learning and trusted changes

Every terminal task may produce a private factual retrospective, including
failure, pause and limitations. Only lessons meeting ADR-259 provenance and
sanitization requirements become verified hints; failure records are not
promoted as successful lessons. Hints are bounded, role-specific untrusted
data after trusted policy, with applicability, metrics and disable/rollback.
Improvement is claimed only from measured comparable runs.

Changing trusted skills, role policy or validators follows a separate causal
proposal, owner-approved design, reviewed PR, full gate and CI, followed by
separate pinned installation and new authorization where binding changes.
The current task head or a lesson cannot govern its own review or install
itself as policy. Automatic role hints and trusted policy promotion are
distinct operations.

## Migration and legacy behavior

Existing per-action and delegated workflows, public Library/pgw APIs and
serialization remain unchanged. A legacy authorization never implicitly opts
into auto-planning, synthetic receipts, wakeup or a new approval profile.
New readers must reject unsupported/unknown schemas and inconsistent fields.
An opt-in upgrade requires fresh owner-approved authority with a new digest;
concrete schema versions and converters are decided and tested in their tasks.
No pending/unknown effect is auto-migrated, reset or marked complete. Preserve
original receipts, consumed budgets and policy provenance. An explicit import
requires fresh canonical reconciliation and stops if evidence is insufficient.
Stopped epic #131 and its private artifacts/mandates remain outside this epic.

## Verification and delivery map

#296 proves installation/preflight; #297 implements the bounded chat entry;
#298 validates automatic plan admission; #299 implements GitHub/CI waits;
#300 proves quota wakeup; #301 preserves continuation and consumed budgets;
#302 verifies role hints; #303 implements the final human gate; #304 combines
fault-injection E2E with a separately approved production pilot. The #290
hotfix is reused, not reimplemented. Structural dependencies remain the source
of readiness; this list does not create them.

The final scenario starts from a fresh explicitly approved profile, completes
two admitted tasks sequentially, observes transient-read/CI/quota waits and
restart, and reaches a checked final PR without a main merge. Repeated wakeup
must not duplicate effects. Stop/revoke/expiry, malformed evidence, unknown
write, injected plan/hint, exhausted repair budget and changed final SHA must
prove zero unauthorized later effects. Final merge occurs only after a fresh
owner approval of exact identities/demo; its post-merge CI is checked
separately. Synthetic E2E and production evidence are reported separately.

#295 changes documentation only. Regression-first applies when an executable
defect is confirmed; none is asserted here. Independent review assesses the
proposal against pinned existing policy, not as permission to change it.
Owner acceptance remains required before policy-sensitive implementation.
No exactly-once delivery, cross-file transaction or additional crash durability
is promised. Threat additions are recorded in
[threat-model.md](threat-model.md#proposed-autonomous-launch-profile-295).
