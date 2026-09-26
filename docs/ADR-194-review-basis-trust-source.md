# ADR-194: review basis trust source is validated as an invariant

## Status

Accepted.

## Context

After #192 the production controller adapter returns a canonically valid
`ControllerVerdict`, yet the orchestrated run on issue #118 failed with
`MALFORMED_OUTPUT`. Both roles exited with code 0, so no `adapter_diagnostic` line
was emitted: the refusal was raised by the orchestrator itself after reading valid
JSON.

`tools/agent_orchestrate.py`, `_validate_verdict`, required an exact dictionary
equality with `trust_source_kind: "base_sha"`. `tools/codex_adapter.py`,
`_apply_authority`, reports `local_read_only_bundle`, because the controller is
executed read-only over a temporary policy bundle materialized from the pinned
`base_sha`. The canonical schema
`docs/schemas/controller-verdict.schema.json` allows both values:
`enum: ["base_sha", "local_read_only_bundle"]`.

The manual check was therefore stricter than the canon and rejected a truthful
verdict. `local_read_only_bundle` is the more honest description: the controller
does not review inside a Git tree, it reviews a read-only bundle derived from the
pinned base SHA.

## Decision

The orchestrator keeps its independent manual check but validates trust boundary
invariants instead of one source string:

- `head_policy_applied` must be exactly `false`;
- `executor_self_assessment_treated_as_evidence_only` must be exactly `true`;
- `trust_source_kind` must be a member of the canonical enum;
- `review_basis` must contain exactly those three keys.

The accepted set is not a new hardcoded literal. It is read from the canonical
schema through `tools.schema_validate.load_schema`, resolved relative to the
orchestrator's own trusted checkout rather than the task head worktree, and cached
for the process lifetime. Booleans are compared by identity, so `0` and `1` are
rejected.

Fail-closed behaviour is preserved and extended: an unreadable canonical schema, a
missing enum, an empty enum or a non-string member raises the machine code
`TRUST_SCHEMA_UNAVAILABLE`. An unknown but well-formed `trust_source_kind` keeps
raising `MALFORMED_OUTPUT`. Process exit code 20 and the stdout JSON contract are
unchanged otherwise.

## Rejected alternatives

Report `base_sha` from the adapter. Rejected: it would be a false description of
the actual policy source, it degrades trust boundary diagnostics and it contradicts
`docs/codex-adapters.md`.

Drop the manual check and rely on canonical schema validation only. Rejected here:
the orchestrator deliberately does not trust the adapter and verifies identity and
authority fields independently. Removing that layer requires a separate
architectural decision.

## Consequences

The orchestrator accepts both canonically allowed trust sources without duplicating
the canon. Adding a source kind to the canonical schema no longer requires editing
the orchestrator, while unknown values still fail closed. `_validate_report` for the
executor was reviewed in the same scope: it never inspected `review_basis`, so no
divergence from the canon existed there; a characterization test now pins that fact.

Canonical schemas in `docs/schemas/` are unchanged and remain the single source of
truth.
