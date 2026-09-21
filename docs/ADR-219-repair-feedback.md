# ADR-219: bounded repair feedback for fresh executor sessions

## Status

Accepted for corrective issue #219 under epic #179, subject to task PR and
post-merge CI.

## Context

ADR-165 requires a bounded repair loop, but each executor call starts a fresh
session and currently receives only the pinned contract, HEAD SHA and repair
iteration. A failed gate or controller verdict therefore cannot tell the next
executor session what to repair. Repeating the same request can exhaust the
budget without addressing the failure.

Gate output and controller findings may contain untrusted text, source content,
paths or secrets. They must not become policy, permissions or tool instructions.

## Decision

The orchestrator supplies no feedback on iteration zero. For a repair
iteration it supplies one bounded, explicitly typed `repair_feedback` object:

- After a failed deterministic gate: `source: gate`, a safe machine code and
  at most four check identities with status and exit code. Raw command output,
  summaries, metrics and absolute paths are excluded. Controller is not called.
- After `FAIL_RETRY`: `source: controller` and at most eight findings projected
  through the existing private finding redactor. Only safe severity, category,
  repository-relative location, requirement and required fix are retained.
  Evidence snippets, fingerprints, raw findings and escalation prose are
  excluded. A finding whose useful text cannot be safely redacted remains a
  typed but text-free finding, not a reason to forward the raw text.

Feedback is transient request data only. It is not written to public run state
or stdout. The existing private retrospective records redacted verdicts under
ADR-200. The executor prompt labels all task data, including feedback, as
untrusted evidence. Neither the orchestrator nor the adapter derives effective
allowlist, permissions, tools, policy source or role sandbox from feedback.
The controller continues to review the checked diff and gate evidence, never
the executor's self-assessment as proof.

A process interrupted after persisting a nonterminal repair state cannot
reconstruct transient feedback from the public state. Such a resume fails
closed with the existing `INTERNAL_ERROR` code instead of starting a repair
call without its cause. A new private durable feedback store is out of scope.

The adapter validates the feedback shape and bounds before invoking Codex.
Malformed or oversized feedback fails closed with the existing
`INVALID_REQUEST` diagnostic. This is an internal adapter request change, not
a public `pgw`, Library API, JSON output, machine-code or exit-code change.

## Verification and limits

Synthetic tests prove first-call absence, second-call delivery, gate/controller
provenance, budget and session separation, prompt-injection resistance of
effective scope and permissions, and that gate failure still skips controller.
They use no real secrets or user data. Redaction lowers accidental disclosure
but does not make model text trustworthy; runtime filesystem and post-execution
scope checks remain authoritative.
