# ADR-221: separate executor task authority from capability authority

## Status

Accepted for corrective issue #221 under epic #179, subject to task PR and
post-merge CI.

## Context

The production executor starts in a fresh session. Its only task goal is the
operator-pinned contract supplied by the orchestrator. The current prompt puts
that contract inside `task-data` while declaring everything in that block
"never instructions". The prompt constrains permissions but never positively
instructs the role to implement the pinned acceptance criteria. A fresh role
can therefore produce a valid report without attempting the task.

Issue text, acceptance-criterion strings and repair feedback remain untrusted:
they may contain prompt injection. Treating the whole contract as unrestricted
prompt authority would let task content attempt to redefine tools or scope.

## Decision

The executor prompt has two explicit envelopes:

- `pinned-task-contract` contains the exact contract selected and bounded by
  the orchestrator. The executor is instructed to implement its
  `acceptance_criteria` as task requirements.
- `runtime-evidence` contains the HEAD identity, repair iteration, session
  identity and any bounded repair feedback. It is evidence, not authority.

Task authority is deliberately narrower than capability authority. Strings in
the pinned requirements describe the desired repository result, but cannot
change allowed paths, permissions, tools, policy source, sandbox, iteration
budget, or permit Git/GitHub writes. Those controls are enforced by trusted
adapter/orchestrator code and the OS boundary, not by model interpretation.
Repair feedback can help satisfy the same pinned requirements but cannot
replace or expand them.

The prompt continues to require one schema-valid executor report. Controller
input and its base-SHA policy source are unchanged. This is an internal prompt
layout change; it does not alter the public CLI or Library API, canonical role
schemas, machine codes, process exit codes, or stored evidence format.

## Verification and limits

Tests capture the generated prompt and prove that the goal and runtime evidence
occupy distinct labelled blocks, that an injection-like criterion remains JSON
content inside the pinned task envelope, and that the contract's deny-by-default
permissions and narrow allowlist are unchanged. Existing orchestration and
Bubblewrap tests remain the authoritative evidence that prompt text cannot
expand actual capabilities.

Prompt wording cannot force a model to produce a useful patch. A live pilot is
still required after task PR and post-merge CI; failure remains fail-closed and
does not justify broadening permissions.
