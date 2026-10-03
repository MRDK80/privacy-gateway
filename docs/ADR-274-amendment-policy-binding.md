# ADR-274 amendment: pinned policy and moving delivery base

Дата: 2026-10-01. Epic: #248. Owner-approved local corrective scope #274.

## Decision

Introduce an internal policy-binding contract version `1.0`, separately from
the existing mandate, checkpoint, handover and delivery schemas. A binding
contains exactly `schema_version`, `repository`, `epic`, `roadmap_ref`,
`policy_sha`, and `base_sha`. Both SHAs are full immutable Git object IDs.
`policy_sha` selects the reviewed policy implementation; `base_sha` selects
the current task delivery baseline. Gate evidence continues to refer to the
actual delivery base and reviewed snapshot, not the policy revision.

A binding is valid only for the owner-approved repository, epic, roadmap and
policy SHA, with a freshly observed roadmap SHA equal to `base_sha` and a
trusted ancestry check proving `policy_sha` is an ancestor of `base_sha`.
Missing commits, unavailable facts, rewinds excluding the policy revision,
and ambiguous checks fail closed. An ancestry callback is trusted local
infrastructure, never supplied by an issue, task head or model response.

Migration from a legacy binding is pure: the legacy policy/base equality
produces an equal-SHA binding candidate. It does not sign, approve, install,
activate or persist a mandate. Moving the base requires a newly validated
binding, without changing the pinned policy or expanding operations/scope.

## Compatibility and deployment boundary

Integration uses mandate `3.0`, task plan `2.0`, coordinator handover `2.0`
and delivery request/checkpoint `3.0`. Mandate fields remain closed and unchanged;
the version is authority-bearing and changes the approval digest. A plan
adds `policy_sha`; a handover uses
`trusted_policy = {source: pinned_policy_sha, policy_sha: <SHA>}` and requires
mandate provenance `3.0`. A delivery request adds `policy_sha` and requires
fresh remote roadmap facts plus local repository/ancestry checks, including
base-to-merge ancestry for post-merge writes. Legacy versions cannot use
these new fields or consume a `3.0` mandate as an implicit upgrade.

Checkpoint `3.0` adds a pinned `policy_sha` outside the mutable PR/head
delivery binding. Explicit migration requires the legacy base SHA as the
policy SHA and no pending intent. It never writes the real checkpoint, resets
progress or downgrades a pinned checkpoint. Mandate migration returns a
candidate with invalidated approval; fresh owner identity, time and digest
approval are required. Plan migration cannot infer a different policy.

The bounded workflow loads policy files from the pinned SHA and gives the
controller the existing local read-only bundle. Delivery gate snapshots still
use the actual base. Review receipt `2.0` binds the policy SHA; old receipts
cannot authorize new-version publication. Private artifact keys and operation
identities include policy provenance; legacy ledger serialization is preserved.

This additive contract is not automatic opt-in for existing schemas. Legacy
mandate/delivery/handover equality checks remain intact. In particular,
the current runtime must not infer permission to remove those checks merely
because this module exists. A new mandate version and its owner approval are
required before production can use a moving base.

Local integration covers planning, handover validation, supervisor identity,
runtime requests, private artifact identity, fresh pre-effect/post-merge
binding checks and pinned coordinator policy materialization. Git commits
needed for ancestry proof must exist locally; the binding validator never
implicitly fetches or waives proof. The trusted runtime can explicitly import
the freshly confirmed merge object after authority and post-merge CI checks,
without tags, FETCH_HEAD or ref updates, before invoking that validator.
Owner-approved deployment,
next-task checkpoint rotation and final roadmap delivery remain separate
requirements. No production readiness or autonomous next-task completion is
claimed by these synthetic tests.

## Security consequences

The already owner-installed bootstrap entry validates the closed mandate,
separately pinned approval digest, current owner and lifecycle using only
stdlib code before loading any candidate policy modules. Byte identity is
checked only after that approval; it establishes provenance, not authority.
The bootstrap guard mirrors the canonical lifecycle rules, with parity tests;
the canonical driver revalidates fresh authority before each side effect.

Ancestry establishes a relationship between commits, not approval of newer
policy files. Policy always loads from the pinned SHA. Delivery snapshot
checks, independent controller review, exact PR CI, post-merge CI, lifecycle,
digest and operation allowlists remain mandatory and unchanged. The required
order remains PR CI → merge → post-merge CI → task closure → epic update.
No GitHub writes, real checkpoint changes or mandate activation belong to
this local migration step.
