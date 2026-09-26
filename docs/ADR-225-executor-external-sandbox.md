# ADR-225: Executor uses the external Bubblewrap sandbox

## Status

Accepted.

## Context

ADR-185 introduced an outer Linux Bubblewrap boundary for executor calls. It
mounts `/` read-only, makes the ephemeral role scratch directory writable and
bind-mounts only the effective file allowlist as writable. The adapter still
started `codex exec --sandbox workspace-write` inside that namespace.

A repeated production pilot and a separate synthetic write probe showed that
this nesting is not viable. The inner Codex Linux sandbox needs to create its
mount-registry lock outside the outer writable set. It therefore failed on the
read-only filesystem before it could inspect or edit the allowed file. The role
returned a schema-valid blocked report, the repository gate passed the unchanged
tree and controller review exhausted the repair budget.

OpenAI's non-interactive Codex documentation permits broad internal access only
in a controlled isolated environment. The installed CLI also documents
`--dangerously-bypass-approvals-and-sandbox` as intended specifically for
environments that are externally sandboxed. In this workflow the outer
Bubblewrap namespace is that environment:
<https://learn.chatgpt.com/docs/non-interactive-mode>.

## Decision

The executor continues to require the outer Bubblewrap namespace and a non-empty
validated file allowlist. Inside that namespace only, the adapter invokes:

```text
codex exec --dangerously-bypass-approvals-and-sandbox
```

This disables the redundant inner Linux sandbox; it does not remove the outer
OS boundary. `/` remains read-only, the role scratch directory remains private
and writable, the existing `auth.json` remains a read-only bind mount and only
the validated allowlisted regular files are writable.

The controller remains `--sandbox read-only` in its separate ephemeral policy
bundle. Post-execution scope validation remains a second layer for executor
changes. Missing Bubblewrap, namespace startup failure, unsupported path forms
and invalid scope still fail closed without a fallback.

## Verification and compatibility

Regression coverage inspects the exact nested argv and proves that the bypass
flag appears only after the outer Bubblewrap command boundary and that no inner
`--sandbox` option remains.
Active namespace tests continue to prove that allowed writes succeed while
protected and out-of-scope writes are denied by the OS. A real synthetic Codex
write probe is required before task delivery.

This is an internal adapter correction. Public CLI and Library APIs, canonical
JSON schemas, machine codes, process exit codes, token formats and stored
evidence formats are unchanged.
