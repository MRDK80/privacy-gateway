# ADR-298: bounded automatic task-plan admission

Date: 2026-10-11. Task #298, epic #294.
Status: owner approved this concrete design in chat on 2026-10-11 before production implementation. This approval does not install or activate runtime or issue a mandate.

## Decision

Keep existing approved recipes preferred and unchanged. Missing recipes may be derived only under an explicit closed owner-approved planning profile 1.0, pinned by a new optional whole-epic config version 3.0. Legacy whole-epic configurations 1.0/2.0 retain their existing closed shapes and behavior. Config 3.0 extends managed 2.0 with approved_planning_digest; its launch candidate therefore binds the profile digest through the existing complete config digest. The new profile cannot extend mandate 3.0 operations, task grants, paths, causal scope, deadline or budgets. The owner supplies this profile separately; runtime never issues approval.

Profile 1.0 has exactly schema_version, repository, epic, roadmap_ref, policy_sha, mandate_digest, owner_identity, tasks, limits. Each tasks entry is keyed by the exact positive task ID and has criteria_sections, task_class, demo_template. limits has max_planner_attempts, max_review_attempts, max_input_chars, max_output_chars, max_paths; all are positive bounded integers. The owner approves the complete canonical profile digest. No model/settings selection is supplied by this profile. Every task must already have an exact existing mandate task_grant. Paths selected by the planner are a minimal subset of that grant, not wildcards or model-derived new authority. Existing/new paths are checked by the trusted path validator against the fresh task base tree and worktree; symlinks, escapes, absent parent directories and instruction/configuration surfaces fail closed. An eligible task without a grant or a minimal safe scope requires owner action.

Structural parent/sub-issues and dependencies determine readiness; titles and prose do not. Recompute the queue after each verified task completion. A follow-up is deduplicated by canonical issue ID and may enter only if it has the same structural parent, satisfied dependencies, an explicit task grant and profile entry, and remaining mandate follow-up/task budget. Newly discovered ungranted issues require a new owner decision. No untrusted issue can silently create a grant.

Owner-approved dependency correction (2026-10-11): queue selection alone does
not establish readiness for later effects. The canonical structural dependency
parser supplies a sorted, bounded snapshot of exact positive issue IDs and
OPEN/CLOSED states (at most 200); malformed, oversized, duplicate, unresolved or
OPEN dependencies stop derived admission. Each structural blocker and resolved
issue must have an exact GitHub issue URL for the selected repository and number;
missing/foreign/ambiguous URL or contradictory supplied repository provenance
fails closed rather than aliasing a foreign issue to a local number. The actual
primary structural transport supplies these URLs. The parser retains complete
count/pagination checks when supplied, without persisting raw titles or node IDs.
Each dependency is resolved again by exact issue identity/state, including
dependencies outside the epic in the same repository. Both
read-only planning requests include the complete current snapshot as untrusted
data, bound by the existing complete request digest. Fresh readiness is checked
before admission, after each role, before local branch preparation and by every
admitted task authority check, including RUN_TASK and restart. Snapshot drift
during admission stops without refunding attempts or repeating pending roles.
Cached admission still requires fresh readiness. A CLOSED task requires the
existing exact close ledger guard and cannot bypass dependency readiness.
This correction preserves profile/config/admission-record/handover/mandate
schemas and authority; no persisted dependency snapshot grants permission.

Criteria extraction supports only a profile-selected nonempty list of unique exact level-two Markdown section headings. Each required heading must occur exactly once. In source order, every nonempty paragraph or top-level bullet (including its indented continuation) in those sections becomes one criterion, preserving its exact source text. Nested lists, embedded headings, code blocks and malformed/empty sections are unsupported and stop admission. No generated selection may omit or reorder these extracted criteria. The complete bounded issue body is also supplied to independent review; requirements outside the approved sections that affect delivery produce NEEDS_DECISION, not silently discarded coverage. The source identity binds the entire issue-body digest and exact extracted sequence. No claim of semantic completeness is based on substring matching alone.

A pinned read-only planner command receives bounded issue criteria/dependencies, mandate/profile facts and fresh repository identity/base information as untrusted input. It proposes the existing plan 2.0 shape plus a bounded criteria/path justification; it receives no executor or Git/GitHub write authority. A trusted validator verifies exact closed response shapes, repository/epic/task/ref/SHA identity, full acceptance-criteria coverage/provenance, policy ancestry, grant subset, permitted class, budgets/checks and demo template. Criteria sections that cannot be extracted completely and unambiguously stop rather than accept a subset. Unknown output, oversized input/output, new architecture/security choices or instruction-surface edits stop for owner review.

Planner proposal 1.0 has exactly schema_version, plan, demo_plan, criteria_mapping. plan uses the existing closed plan 2.0 shape; criteria_mapping has one entry per source criterion with criterion_index, allowed_paths and reason. Indices cover the whole extracted sequence exactly once; every selected path must appear in at least one mapping. demo_plan follows the existing validated contract/template. Justifications are bounded untrusted text, not permissions. Independent plan-review response 1.0 has exactly schema_version, plan_digest, verdict, findings; verdict is PASS or NEEDS_DECISION, findings is a bounded list of criterion_index/code objects. Only exact digest-bound PASS with no findings is admissible. A separate pinned read-only planner command and independent controller command use closed request/response schemas; this does not change existing executor/report/controller-verdict contracts.

An independent pinned read-only controller evaluates the complete bounded issue and proposal before any executor or branch preparation. Its exact plan digest and fresh issue-body digest are bound to an admission receipt; a PASS cannot create permission or replace task patch review. Validation and independent review occur even when model confidence is high. Planner/controller proposals remain untrusted. A read-only planner/controller invocation uses only installed pinned command construction; issue, plan and config cannot supply executables, arbitrary argv or tool permissions.

The stable attempt identity is repository/epic/task plus the exact owner-approved planning-profile digest. A per-identity nonblocking process lock covers admission, reservations, validation and persistence. Issue/base/default/policy evidence drift never creates a new budget identity. A different profile requires explicit owner approval and disposition of preserved pending state, not an automatic free retry.

Persist closed admission record 1.0 with exactly schema_version, repository, epic, task, planning_digest, mandate_digest, policy_sha, issue_body_digest, base_sha, default_sha, attempts, pending, status, proposal, review. attempts has planner and review nonnegative consumed counters. pending is null or a closed role/attempt/request_digest reservation; status is RESERVED, REJECTED or ADMITTED. proposal/review are null until exact closed validated responses exist and include their canonical digests. Reserve and persist the next consumed attempt plus pending before each role invocation. Successful receipt persistence clears only that exact reservation. A crash, malformed response, transport failure or orphaned reservation never refunds attempts. An unresolved pending role after restart blocks for owner disposition unless a complete exact persisted response proves completion; it cannot be called again blindly. Admission drift clears validity, not attempts or evidence. Partial admission cannot be treated as accepted. Before successor preparation, reread authority, profile, issue body, remote/local refs and receipt. Drift invalidates the admission; it never silently rewrites the approved criteria or resets budget. Existing pending preparation retains UNKNOWN semantics and must be reconciled rather than retried blindly.

Admitted plans use the existing prepare_handover validator and separate successor checkpoint/cursor. Policy stays pinned; the successor base equals the verified previous roadmap merge SHA. Automatic planning does not bypass full local gate, independent patch controller, task PR CI, separate post-merge CI or final main human gate. Planner/review attempt limits cannot exceed or reset the existing maximum of two automatic repair cycles. Proven model-caused repair/incomplete role review follows same-model reasoning escalation from pinned CONTRIBUTING; unavailable authorized profile selection remains an owner blocker.

Demo templates are owner-approved profile data and validated using existing demo-plan contracts. This task does not fabricate owner receipts or weaken existing real-owner demo completion. A synthetic-demo substitution, if implemented later, requires a separately reviewed concrete receipt/execution design; merely selecting a synthetic template does not establish execution or production success.

## Verification and scope

Synthetic failure tests: issue prompt injection and omitted criteria; absent recipe without opt-in; insufficient task grant; path/symlink/instruction-surface escapes; stale issue/profile/base/policy/dependencies; malformed or self-approved review; exhausted/reserved attempts; concurrent admission, crash before/after role invocation and drift without counter refill; duplicate/unrelated/ungranted follow-ups; and two-task Session.resume continuation through a genuinely admitted RUN_TASK, existing delivery guards, completion/update and next queue. Preserve old recipe-only behavior and schemas. Full local gate, independent controller and exact PR/post-merge CI remain mandatory.

No production installation, new mandate issuance, #131 mutation, Library/pgw API, dependency/release metadata, persistent waits or wakeup. Atomic private writes do not promise a cross-file transaction, crash durability or exactly-once execution.
