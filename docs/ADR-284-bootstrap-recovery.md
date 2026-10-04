# ADR-284: bounded recovery of partial bootstrap

Date: 2026-10-03. Task #284, epic #248. Owner approved the design candidate
before production code. This document does not activate any private runtime.

## Decision

Recovery is an explicit one-task profile: runtime-task config `2.0` adds
`approved_recovery_digest` (null means no write approval). Existing config
`1.0`, checkpoint `1.0`–`3.0`, mandate and ledger serialization retain their
contracts. Whole-epic continuation cannot consume the recovery profile;
the owner must complete and review recovery through the one-task supervisor.

The owner supplies a closed `bootstrap-recovery-approval.json` version `1.0`
outside worktrees. Its canonical digest is pinned separately in runtime-task
config. Approval binds the original checkpoint identity/progress digest,
reviewed artifact digest excluding the final delivery binding, mandate
digest, current owner, issue/policy identity, exact operation IDs, expiry,
stop generation and finite per-operation attempt budgets. It narrows existing
mandate authority and cannot replace it. Runtime never issues this approval.

Runtime owns a separate `bootstrap-recovery.json` version `1.0`. A separate
process lock covers recovery effects, including direct bootstrap entry.
The state starts stopped. Only a separately pinned valid owner authorization
for its exact generation permits writes. Attempts are reserved durably before
effect and never refunded. Unfinished reservation after crash closes that
authorization. UNKNOWN, invalid response, stale identity or permission failure
stops the authorization and advances the generation. A later successful
read-only probe does not reopen it. An owner stop uses the same generation
boundary. No whole-phase NOT_APPLIED is inferred from a partial bootstrap.

Recovery requires an existing trusted reviewed artifact; it never invokes the
executor again. Local SnapshotCommit reconciliation verifies the exact
reviewed snapshot, ref, index and working tree. The same operation IDs and
canonical ledger are reused for commit, push and PR. APPLIED receipts must
agree with fresh canonical facts; NOT_APPLIED is required before an absent
or uncertain operation may execute. Conflicts, duplicates, malformed receipts
and missing evidence stop. Each effect retains fresh mandate/lifecycle,
policy-binding and SHA guards, including every snapshot mutation and the
remote head check immediately before PR POST.

The recovery supervisor path completes RUN_TASK only with a real full PR
binding and fresh live facts. Partial committed head is verified through
snapshot reconciliation rather than substituted into the old checkpoint.
Both direct CommandRuntimeAdapter effects and concrete runtime entry check
the recovery boundary. Legacy entry cannot bypass an existing recovery state.

Diagnostics are a closed category enum: timeout, network, permission,
stale_identity, invalid_adapter_response, unknown. Network requires explicit
transport evidence; arbitrary nonzero exit is unknown. Categories grant no
retry authority. No raw exception, command, output, credential or payload is
persisted. Atomic individual state writes are not a transaction across Git,
GitHub, ledger and checkpoint, and do not promise exactly-once delivery.

## Closed private schemas

Approval `1.0` requires exactly `schema_version`, `checkpoint_digest`,
`artifact_digest`, `mandate_digest`, `owner_identity`, `issued_at`, `expires_at`,
`generation`, `budgets`, `repository`, `epic`, `task`, `policy_sha`.
State `1.0` requires exactly `schema_version`, `generation`, `stopped`,
`category`, `authorization_digest`, `attempts`, `pending`, `binding_digest`.
The installed recovery module validates these private contracts; they are not
model output schemas. Unknown fields fail closed. Rate-limit pause remains a
stop before any recovery adapter invocation or checkpoint advancement.

## Deployment and stopped epic #131

No existing pending state is automatically migrated or reset. Integrate the
corrective policy through task→roadmap→main and exact CI, then owner-install
byte-identical policy and entry. The owner must separately approve a new
mandate/digest pinned to that policy SHA and an exact recovery authorization.
Old reviewed receipts retain their original policy provenance; they cannot
be edited to claim review under the new policy. A separate reviewed import
and fresh review of preserved task snapshot is required where provenance
differs. If exact evidence cannot be established, stop for owner decision.
Keep the stopped #131 state intact; activation and its eventual resume are
separate owner decisions. Tests establish synthetic paths only.

Before a future #131 activation, separately approve synchronization of its
roadmap with the new trusted policy. Verify policy ancestry against the task
base; approval cannot make a new policy an ancestor of an old snapshot. If
that check fails, the owner must choose a fresh plan/branch and minimal replay
of the preserved diff, followed by the full gate, controller review and a new
snapshot. Preserve the old commit and ledger as historical evidence; do not
synthesize APPLIED receipts or reuse requests with changed identity. Verify
the newly installed bytes, fresh mandate digest, owner approval digest and
canonical local/remote facts before the first bounded attempt. This procedure
is a deployment prerequisite, not authorization to mutate #131 now.
