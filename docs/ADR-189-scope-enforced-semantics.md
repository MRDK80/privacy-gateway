# ADR-189: Semantics of `scope.enforced` in orchestrator dry-run

## Status

Accepted for task #189 under epic #179, subject to task PR and post-merge CI.

## Context

Pilot #182 found that `tools/agent_orchestrate.py --dry-run` prints
`scope.enforced: true` whenever an explicit allowlist is given. At that time
the executor ran with Codex `workspace-write` over the repository root and the
allowlist was checked only after execution against the Git diff, so the word
`enforced` read as a stronger guarantee than the system provided. The original
finding is kept in #189 as historical context.

Since then #185 (ADR-185) added a Linux Bubblewrap writable boundary for the
Codex executor, and #225 (ADR-225) made that outer boundary the only executor
sandbox. The field itself did not change. On roadmap SHA
`3059f2ec899e30501038ec858c5835677f59b813` it is computed in
`tools/agent_orchestrate.py` as `bool(contract.allowed_paths)`.

Observed dry-run stdout on that SHA, with `--base roadmap/179-codex-adapters`,
`--head docs/189-scope-enforced-semantics`, private storage outside the
repository and no executor or controller command. Every case exited with 0,
printed `"status": "DRY_RUN"`, left `git status --short` empty and created no
state or memory directory:

| `--allowed-path`         | `scope.mode`       | `scope.enforced` |
|--------------------------|--------------------|------------------|
| `docs/codex-adapters.md` | `explicit`         | `true`           |
| `docs`                   | `explicit`         | `true`           |
| not given                | `unscoped_dry_run` | `false`          |

The `docs` case is accepted by the orchestrator, while the Codex executor
adapter rejects a directory entry with `INVALID_REQUEST` before the role call.

## Decision

Keep the field name, its position and the published stdout JSON unchanged.
`scope.enforced` is an orchestrator-level statement only.

`true` means that the contract pins a non-empty normalised allowlist and that
the orchestrator enforces it fail-closed: a real run requires it, dry-run
checks the current diff against it, and `_assert_scope` is repeated after
every executor call, including repair iterations, failing with
`SCOPE_VIOLATION` or `HEAD_POLICY_CHANGED`.

`false` means that no allowlist is pinned. Dry-run is then diagnostic only and
a real run stops with `ALLOWLIST_REQUIRED` before any adapter is created.

`scope.enforced: true` does not state that:

- the executor will run under OS-level write confinement;
- the configured `--executor-command` is the Codex adapter;
- the platform provides Bubblewrap and unprivileged mount namespaces;
- every allowlist entry has a form the adapter supports;
- reads, network egress, CPU, memory or system calls are restricted.

OS-level write confinement is a property of the Codex executor adapter
(ADR-185, ADR-225). It fails closed rather than degrading: a missing
Bubblewrap executable, or a missing allowlisted file on a non-Linux platform,
yields `SANDBOX_UNAVAILABLE`; directory, symlinked, hardlinked, protected or
otherwise unsupported entries yield `INVALID_REQUEST`; a Bubblewrap launch or
namespace failure is a non-zero role call with no direct or broadly writable
retry.

## Guarantee matrix

| Situation | Before the executor writes | After the executor call |
|---|---|---|
| Dry-run, no allowlist | none; no executor starts | not applicable |
| Dry-run, allowlist | none; no executor starts | current diff checked once |
| Real run, no allowlist | stops with `ALLOWLIST_REQUIRED` | not applicable |
| Codex adapter, Linux, Bubblewrap, file-only allowlist | read-only root, writable allowlisted files and private scratch | `_assert_scope` |
| Codex adapter, unsupported entry form | stops with `INVALID_REQUEST` | not applicable |
| Codex adapter, no Bubblewrap or not Linux | stops with `SANDBOX_UNAVAILABLE` | not applicable |
| Other `--executor-command` | none provided by the orchestrator | `_assert_scope` |

Even in the confined case the mount boundary does not restrict reads, network
access, CPU, memory or system calls, and the private scratch stays writable
during the call. Hosted CI runners may skip active-namespace tests, so a green
hosted check does not by itself prove active confinement (ADR-185).

## Rejected alternatives

1. Rename the field. This is a breaking change of the published stdout JSON
   governed by ADR-150 and adds no verifiable information.
2. Add a guarantee-level field, for example `scope.os_confinement`. A dry-run
   does not start the executor, does not know which executor command a later
   run will use and cannot know whether namespace creation will succeed at
   call time, so the new field would be another static claim. It would also
   change the published stdout that ADR-185 deliberately kept unchanged. This
   can be revisited if the orchestrator gains trusted knowledge of the
   adapter and a verified capability probe.
3. Keep the name with documented semantics. Chosen.

## Consequences

- No change to code, canonical schemas in `docs/schemas/`, stdout JSON,
  machine codes or exit codes. The existing tests in
  `tests/test_agent_orchestrator_allowed_paths.py` keep pinning the dry-run
  `scope` object.
- Consumers must not treat `scope.enforced: true` as proof of OS-level
  confinement. Evidence of active confinement comes from adapter tests on a
  platform with working namespaces and is reported separately from hosted CI.
- A directory allowlist passes dry-run but fails closed at the Codex adapter.
- Neither the post-execution scope check nor any fail-closed path is weakened.

## References

#179, #182, #185, #189, #225; ADR-150, ADR-165, ADR-185, ADR-225;
`docs/codex-adapters.md`.
