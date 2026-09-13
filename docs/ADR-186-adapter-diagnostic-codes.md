# ADR-186: redacted diagnostic channel for Codex role adapters

## Status

Accepted for task #186 under epic #179.

## Context

The first real executor call in the production pilot #182 stopped fail-closed
with `MODEL_UNAVAILABLE` and an empty orchestrator stderr. The actual cause was
an HTTP 400 from the API: the derived generation schema left `const` and `enum`
subschemas without a `type` key, which strict structured output modes reject.

Two properties of the current design made that invisible.

`tools/codex_adapter.py` prints its machine code to stderr, but
`tools/agent_orchestrate.py` runs the adapter with `capture_output` and never
re-emits it. The machine code `MODEL_UNAVAILABLE` also covered four different
situations at once: a missing binary, an `OSError` while launching Codex, a
timed out or failing `codex --version`, and any non-zero exit of `codex exec`.

Diagnosing the failure therefore required reproducing the adapter argv by hand
with a visible stderr. That is not acceptable for an unattended workflow.

## Decision

Split the overloaded code and forward a strictly redacted signal.

`CODEX_NOT_FOUND` is raised when the Codex binary cannot be launched at all
(`OSError`), both for the version probe and for the role call.
`VERSION_PROBE_FAILED` is raised when `codex --version` times out or exits
non-zero. `MODEL_UNAVAILABLE` keeps its exact previous meaning: a non-zero exit
of `codex exec`. The existing code name is not renamed or removed, so current
consumers keep working.

The adapter may attach a `detail` to a failure. The detail is built by
`_redacted_reason()` from a fixed vocabulary: the Codex exit code plus tokens
from a whitelist such as `invalid_json_schema` or `rate_limit_exceeded`. Raw
Codex stderr, prompts, chain-of-thought, absolute paths and credentials are
never forwarded. The adapter prints the machine code on the first stderr line
and the optional `detail=` line second.

The orchestrator re-emits that signal with `_report_adapter_diagnostic()`,
accepting only a known machine code and a character-filtered detail line.

## Consequences

Process contracts are unchanged. The adapter still exits with code 20 on every
failure, the orchestrator still raises `MODEL_UNAVAILABLE` for a failing role
call, and its stdout JSON keeps the same fields and values. Only stderr becomes
informative, which keeps ADR-150 intact.

A future failure of the same class is visible without manual argv
reproduction: the run prints
`adapter_diagnostic exit_code=20 machine_code=SCHEMA_DERIVE_UNSUPPORTED`
or, for a rejected schema, a `detail=codex_exit=1 tokens=invalid_json_schema`
line.

The whitelist is a maintenance point. An unknown remote error appears as
`codex_exit=<n>` without tokens, which is a deliberate trade: silence about
unknown text is preferred over leaking untrusted output into logs.

## Alternatives rejected

Forwarding Codex stderr verbatim was rejected: it carries prompts and
credentials. Adding a diagnostic field to the orchestrator stdout JSON was
rejected because that payload is a published CLI contract. Renaming
`MODEL_UNAVAILABLE` was rejected because it would break existing consumers and
tests without adding information.
