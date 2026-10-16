# ADR-302 addendum draft: original problem sources for role-specific hints

Status: owner-approved for implementation on 2026-10-13; independent design review PASS.
Installation/activation are not authorized. This resolves whole-task review #302
findings for planner/planning_review/controller. Existing owner-approved
learning/profile/config7, budgets, role transport/settings, public APIs and final main
approval remain unchanged.

## Controller
A successful current gate alone supplies no failure problem. For any controller invocation, including the first controller call after an executor/gate repair, classify using the immediately preceding original failed repository-full gate from this same canonical workflow run. Read its immutable original gate journal and full snapshot binding, original consumed ordinal/repair iteration, policy/contract/initial input identity. Confirm current successful gate is the correction of the same failed check predicates and scope. Select only the unique pinned controller rule matching those facts; otherwise empty. Any controller invocation without such retained failure has empty hints. A role payload, model feedback or caller-supplied historical gate cannot establish this source. Persist source binding in private selection reservation; reread before actual transport. No additional controller call is created.

## Planning
Planner/reviewer failures occur before executor workflow; do not relabel them as TERMINAL_WORKFLOW. Add a closed private bounded planning-validation journal with records under the canonical managed seed/planning/task lock. Each record binds original admission identity/policy/profile/grant/criteria/base/default, actual consumed role ordinal, exact original request digest, output digest and fixed deterministic validator outcome/code. Retain the original capped owner-only request/output artifacts so digest equality is checked against origin. The journal observes only existing calls and existing trusted validate_proposal/validate_review; it does not add provider calls or reset pending/attempts. Interrupted validation is UNKNOWN, never a completed failure/correction. No model findings or NEEDS_DECISION verdict by itself creates a trusted problem class.

Use a separate closed internal PLANNING_VALIDATION observation source, explicitly
distinct from workflow terminal observations. The schema accepts only the fixed
deterministic validator failure codes (e.g. PLANNING_CRITERIA_INCOMPLETE,
PLANNING_SCOPE_NOT_MINIMAL, PLANNING_REVIEW_INVALID); exact operation/role and original
checks determine applicability. No new machine/process/public codes are introduced.
Retained artifacts are capped by the existing profile record/store limits, immutable and
private, with fail-closed partial/conflict/missing source handling.

A planning rule's successful promotion requires a distinct later valid planning call for
equivalent approved original criteria/grant/initial workspace with matching
validator/check identity, followed by actual task full gate, independent controller,
exact PR/post CI and applicable demo. Its environment is planning-specific backend=null
only where pinned catalog explicitly permits unavailable settings. Planning validation
is read-only and never invokes a role. Catalog role planner maps to planner;
controller-class planning rules map only to planning_review. Workflow patch-controller
rules remain separate by environment/source kind.

Historical failure/correction promotion retains exact original
planning-profile/criteria/grant/initial HEAD/tree/base/default/policy equivalence.
Current planning selection instead uses the pinned deterministic
CURRENT_APPROVED_CRITERIA_AND_GRANT predicate described below; it does not require
historical base/HEAD equality. Task ID and consumed invocation identity remain distinct,
never rebound. A separately already-approved fresh task admission may receive hints when
exactly one pinned rule matches those inputs. Pending or rejected source Admission
remains blocked and immutable: this feature defines NO resolution, clearing or retry of
its pending reservation, and refunds no attempts. No new owner recovery effect is
introduced. Initial planning without such a source gets empty hints. Unknown/ambiguous
classifications stay empty. Historical lessons must pass exact
role/task/problem/environment/policy/catalog selection and tombstones.

## Reservations, verification and limits
The existing closed invocation1 stays unchanged. A separate closed private problem-binding1 companion binds invocation_key and fixed problem-source digest, not raw source text. Pending/UNKNOWN keeps exact selected bytes; no replacement. Current role transport/settings receipts remain unchanged. New private source/observation shapes and their validators are documented/contract-tested before wiring.

Positive synthetic tests use actual original reservations and synthetic process/API
calls: failure→corrected validated planning and delivery→nonempty
planner/review/controller prompt→actual role completion/settings where
supported→COMPLETED metadata→source measurements. Actual comparable
outcome/call/duration regression must persist latch/tombstones for every applied role ID
and block future selection across restart/full-store/I/O failure. Missing comparison
stays UNMEASURED. No extra baseline/model authority is assumed.

Before production changes: independent design review and owner approval. After
implementation: full local gate and independent whole-task controller; exact
PR/post-merge CI and demo. No real installation/activation or epic-main merge is
authorized by this draft.

## Exact private schemas (design, not implementation)
Git commit/policy/head/base/default SHAs remain lowercase hex40. All SHA256 digest fields are lowercase hex64; tagged owner-approved digests remain exactly as existing sources require. Integers exclude bool. Documents reject missing/extra keys, duplicate JSON keys, symlinks/hardlinks/non-owner files and partial writes. Source paths derive exclusively from verified managed seed + approved numeric task + consumed role/ordinal, never a supplied path.

`planning-artifact1` keys exactly schema_version("1.0"), identity,
role(planner/planning_review), ordinal(1..3 within original approved budget),
kind(REQUEST/RESPONSE), payload, payload_digest, record_digest. identity keys exactly
repository, epic, task, planning_digest, mandate_digest, policy_sha. payload is the
original existing closed planning request or response, preserving its existing
schemas/limits; no new semantics/owner fields. Bytes respect original
max_input_chars/max_output_chars and learning max_record_bytes; inability to fit blocks
retention/promotion and cannot invent a completed source. Private fixed path
planning/<task>/learning-validation/<role>-<ordinal>-request.json or -response.json.
Original input text stays private.

`planning-validation1` keys exactly schema_version("1.0"), identity(same exact shape),
role, ordinal, request_digest, response_digest, applicability_digest,
validator(PROPOSAL_V1/REVIEW_V1), outcome(PASS/FAIL), machine_code, check_identity,
record_digest. machine_code PASS is OK; FAIL fixed allowlist
PLANNING_CRITERIA_INCOMPLETE/PLANNING_SCOPE_NOT_MINIMAL/PLANNING_REVIEW_INVALID.
check_identity is digest of exact pinned validator name+policy_sha+criterion
count+normalized original applicability (criteria/grant/profile/initial workspace
tree/base/default); task number and invocation ordinal are excluded from this predicate
identity but remain bound by full source identity; it is not a test name inferred from
output. Fixed path same directory <role>-<ordinal>-validation.json. Append-only
immutable files, idempotent exact duplicate only. UNKNOWN has NO completed validation
record, and canonical pending remains. Source reader reruns the fixed read-only
validators on original private artifacts, with original
owner/mandate/context/body/base/default inputs bound to admission and checked against
pinned sources; missing historical inputs rejects verification. Validators do not run
models or mutate state.

`planning-observation1` (distinct from observation1) keys exactly schema_version("1.0"),
kind(PLANNING_VALIDATION), identity, role, ordinal, outcome(PASS/FAIL), machine_code,
request_digest, response_digest, validation_digest, applicability_digest, record_digest.
It contains no response body/model findings. Its fixed append-store kind is
planning_observation; source digest is checked against the actual immutable validation
record, original artifacts and canonical admission consumed counters. It cannot
masquerade as a terminal workflow.

`problem-binding1` keys exactly schema_version("1.0"), invocation_key, role,
source_kind(SAME_RUN_GATE/PLANNING_VALIDATION/NONE), source_digest(nullable only NONE),
problem_class(nullable only NONE), record_digest. Fixed companion path
learning-invocations/<invocation_key>.problem.json; planning uses its existing planning
invocation directory. Its bytes count against existing limits; enumeration distinguishes
this exact suffix, rejects unrecognized files, and cannot treat a companion as another
call. Source binding is written before transport alongside original metadata after
canonical consumption; unknown/pending cannot replace it. Completed invocation proof
also rereads companion and source; missing companion under this amended feature is
blocked, never downgraded to a claim of verified applicability.

`planning-rule1` is a distinct pinned closed catalog document at
docs/schemas/agent-learning-planning-catalog.json: keys schema_version("1.0"),
rules(0..128). Each rule has exactly rule_id, role, task_class, problem_class,
environment, symptom_code, diagnostic_steps, minimal_fix, applicability, predicates,
source_kind, validator, failure_code, check_identity_kind, current_predicate. It has NO
gate-only regression_checks. Shared fields keep catalog1 types/bounds. source_kind is
PLANNING_VALIDATION; validator is PROPOSAL_V1/REVIEW_V1; failure_code uses the fixed
allowlist; check_identity_kind is PINNED_VALIDATOR_INPUT. Its predicates include the
existing five successful delivery predicates and fixed PLANNING_VALIDATION_FAIL_PASS
instead of claiming an aggregate gate test is a planning validation. Role planner
requires PROPOSAL_V1; controller requires REVIEW_V1 and consumes only planning_review.
Planning environment explicitly backend_digest=null. Unknown codes/validator identities
are unpromotable. The existing learning profile catalog_digest binds the canonical
composite {workflow_catalog_digest,planning_catalog_digest}; this binding change
requires reapproved learning profile digest, not silent compatibility. Legacy
profile/configs1–6 and public schemas unchanged.

Internal candidate/verification for this new source family use separate
planning-candidate1/planning-verification1 with exactly their existing
candidate1/verification1 field sets plus source_kind(PLANNING_VALIDATION), validator and
check_identity_digest. Distinct validators/store kinds prevent workflow rules accepting
planning evidence. A verified planning record binds original failed observation,
corrected planning validation and subsequent corrected TASK_DELIVERY original source;
every linked digest is independently reread. Selection digest remains exactly
digest(hints), with unchanged invocation1 semantics; the separately validated companion
binds source provenance through invocation_key.

No response-validation journal record clears Admission.pending. A FAIL recorded after
deterministic validation exception retains original pending and attempts. Recovery is
outside this design. Subsequent selection uses only independently authorized fresh task
admissions passing the fixed current-request relevance predicate; same-task recovery
remains BLOCKED. Full actual end-to-end tests must show this behavior as well as
nonempty planner/review/controller invocation metadata.

`planning-context1` keys exactly schema_version("1.0"), identity, role, ordinal, now,
owner_identity, started_at, task_iterations, follow_up_issues, request_digest,
record_digest. Capture from the actual trusted original MandateContext before original
validation, never from model/current clock; private fixed <role>-<ordinal>-context.json.
now/started_at nonnegative integer epoch seconds, started_at<=now, original mandate
lifetime and duration checked; owner_identity exact original owner;
task_iterations/follow_up_issues bounded by original mandate limits. Original
request/response/validation digests are linked to this immutable context companion.
Missing original context forbids historical validation/promotion.

## Current relevance after roadmap advancement
Historical failure/correction proof and baseline measurements keep their exact initial HEAD/tree/base/default equivalence. Current planning hint relevance is a separate owner-approved decision: allow a new already-authorized task on a freshly checked different roadmap base to consume a historical lesson when the pinned CURRENT_APPROVED_CRITERIA_AND_GRANT predicate succeeds. This is data relevance, not authority or proof of current failure/performance.

The new planning-rule field current_predicate is exactly
CURRENT_APPROVED_CRITERIA_AND_GRANT. It compares the fixed parsed original criterion
strings in order (digest), canonical original task-grant causal_scope/allowed_paths
(digest), approved planning profile digest, task_class, logical role, validator
operation, policy SHA, catalog identity and planning environment to the corresponding
independently verified corrected planning source. It excludes task number and Git
base/HEAD only for current relevance; full source identity and current authority retain
both. No arbitrary strings, executable predicates, model findings or inferred semantic
classes. Existing canonical criteria parser/profile validator supplies facts; raw issue
body is not a classification command. Unknown/multiple incompatible problem classes
yield empty hints.

Before selection, reread actual original current request, current admission identity and
approved task grant/profile; require no pending call, remaining original attempts and
exact current fresh HEAD/base/default/ref/policy checks by existing Admission guard. For
planning_review, validate the original proposed plan with the existing fixed proposal
validator first; then use the same current predicate scoped to REVIEW_V1/controller
catalog role. This does not label a passed request as failed. problem_class is the fixed
pinned catalog applicability class for that verified prior validator failure. The
problem companion PLANNING_VALIDATION source_digest binds a closed internal
relevance-source1: schema_version1.0, current_request_digest,
corrected_validation_digest, failure_validation_digest, current_predicate,
criteria_digest, grant_digest, profile_digest, environment_digest, policy_sha,
record_digest; all values reread against original sources. Store at fixed current
planning invocation directory <invocation_key>.relevance.json; count bytes/records
against existing bounds and require immutable exact duplicate only.

This choice explicitly relaxes historic Git equality only for current relevance. It
preserves live fresh SHA guards, strict promotion equivalence and exact paired
measurement equivalence; a different initial workspace cannot claim measured
improvement. Owner must approve this precise applicability change before implementation.

## Retention of the original planning workspace
The original HEAD/tree required above are retained before transport in a private
`planning-workspace1` companion, at the same fixed journal prefix with suffix
`-workspace.json`. Its exact fields are schema_version1.0, identity, role, ordinal,
head_sha, tree_hash, record_digest. This is an implementation binding for the already
approved strict initial-workspace predicate, not an additional applicability rule.
Its bytes/record count use the existing limits. The reader checks the original Git
commit/tree object bytes and uses a disposable checkout of that HEAD when replaying
path-sensitive validation; it does not replay against today's workspace. Missing or
corrupt historical objects block proof. No live refs are moved and no role is called.

## Original full-gate source retention
To reread the preceding failure for controller, retain the existing redacted full-gate
result in an immutable private `full-gate-source1`: schema_version1.0, run_id,
policy_sha, contract_digest, executor_ordinal, repair_iteration, gate, record_digest.
The managed path is learning-source/full-gates/<run_id>-<executor_ordinal>.json.
It shares existing learning caps. This records the already approved same-run gate
source, and does not add a check, role call, code execution or new gate semantics.
Rereading checks the consumed original executor completion/private snapshot, exact
policy/contract, and every existing immutable CheckResult journal row. Controller
requires the latest successful gate plus the last preceding complete failed gate from
that original run. A problem companion binds the digest of this verified source pair;
executor binds its original failed gate. Missing expected sources are blocked. Failed
gates are never passed to the terminal historical controller PASS API as successes.
