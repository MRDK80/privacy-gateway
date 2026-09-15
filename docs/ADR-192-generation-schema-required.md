# ADR-192: closed `required` in derived generation schemas

Status: accepted. Date: 2026-09-14. Issue: #192. Epic: #179.
Extends ADR-186.

## Context

`tools/codex_adapter.py` passes a derived, strictly weaker schema to
`codex exec --output-schema`. After #186 every subschema carries an explicit
`type`, and after #190 the controller prompt reaches Codex through stdin. The
first live controller call then failed on the API side:

```text
adapter_diagnostic exit_code=20 machine_code=MODEL_UNAVAILABLE
detail=codex_exit=1 tokens=invalid_json_schema,invalid_request_error
```

The derivation of `docs/schemas/controller-verdict.schema.json` contained two
objects whose `required` did not list every property: `blocking_findings.items`
omitted `location`, and `location` omitted `line`. Strict structured output
modes reject such a schema: every object must set `additionalProperties: false`
and list every property in `required`, and optionality has to be expressed as a
nullable union rather than by omission. No controller verdict existed in any
run, so the production pilot #182 was blocked.

## Decision

The derivation closes `required` over all properties and widens a canonically
optional property into a nullable union:

* `_close_generation_required` lists every property in `required`;
* `_nullable_generation_type` appends `"null"` to `type` and, for an `enum`,
  adds `null` to the options;
* an optional `const` cannot be widened without changing the contract and
  fails closed with `DeriveUnsupported("optional-const")`;
* `assert_generation_ready` additionally rejects `partial-required` and
  `open-object`, so the defect is caught locally before Codex is invoked;
* `prune_generation_nulls` removes exactly those nulls that the canonical
  schema does not require, before canonical validation of the response.

Canonical schemas in `docs/schemas/` are not modified, canonical validation is
not weakened, and fail-closed behaviour with exit code 20 is unchanged. Only
`controller-verdict.schema.json` produces a different derivation; the
derivations of `executor-report`, `task-contract`, `agent-promotion` and
`agent-retrospective` are byte-identical to the previous ones, because their
objects already required every property.

## Rejected alternative

Dropping optional properties from the derived schema would also satisfy the
strict mode and would be simpler. It was rejected: the controller would lose
the ability to report `location` for a blocking finding, which is precisely the
information a human needs when triaging a verdict. Diagnostic precision must
not be traded for a simpler generator.

## Consequences

* A role may answer `null` for an optional property, so every consumer must
  prune before canonical validation; the adapter is the single place that does
  so.
* `escalation_reason` keeps `type: ["string", "null"]` with `null` inside
  `enum`. That shape was not the construct which caused the rejection, and it
  stays verified only by a live controller run recorded in #192.
* The nullable union makes the generation schema slightly larger: the derived
  controller schema grows from 2969 to 3129 bytes when serialised with
  indent 2.

## Refinement after the first full gate run

The first local gate on the change set reported six failures in
`tests/test_schema_generation_type.py`, the suite that fixes the #186
derivation contract. Two consequences of the initial implementation were wrong
and were corrected here:

* a constant property must not be widened at all. Its only permitted value is
  always acceptable to the canonical schema, so it is listed in `required`
  unchanged instead of failing with `optional-const`;
* `assert_generation_ready` now checks child subschemas before the object level
  invariants, so a missing `type` is still reported at the precise child path
  established by #186 rather than being masked by `open-object` at the parent.

Three remaining expectations were genuinely obsolete and were updated: a
property that is not listed in `required` is now a nullable union, which is the
whole point of this ADR. Two of those fixtures declared no `required` at all
and were closed explicitly; the third asserts the new nullable union for a
conditionally required property. The derivations of all canonical schemas in
`docs/schemas/` are unaffected by this refinement: only
`controller-verdict.schema.json` differs from the pre-#192 output, at 3129
bytes with indent 2.
