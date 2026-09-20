# ADR-185: OS writable boundary for the Codex executor

## Status

Accepted for task #185 under epic #179, subject to task PR and post-merge CI.

## Context

The executor currently runs with Codex `workspace-write` over the repository
root. Its `allowed_paths` are checked after execution against the Git diff.
That check cannot reject a write before it happens, nor detect a write followed
by deletion or every side effect outside Git. Codex `--add-dir` only expands its
writable set. A smaller `cwd` does not prevent writes to absolute paths.

## Decision

Run the executor inside a separate Bubblewrap mount namespace. Bind the host
filesystem read-only, then bind only validated effective allowlist files
read-write. Keep the Codex output and generated schema in a private temporary
mount that is destroyed after the call. The executor must fail before its role
call starts if Bubblewrap or the needed namespace operations are unavailable. There
is no fallback to the old `workspace-write` invocation and no elevation
prompt. The controller remains read-only. Keep `_assert_scope` as a second
layer after the executor call.

A missing Bubblewrap executable emits `SANDBOX_UNAVAILABLE` and adapter exit
code 20. A Bubblewrap launch or namespace failure remains a non-zero role
call, reported as `MODEL_UNAVAILABLE`; the adapter never retries directly.
The orchestrator's diagnostic whitelist includes the new code, while its
published stdout JSON remains unchanged.

The adapter validates the allowlist independently of the prompt and rejects
empty, escaping, protected/private, symlinked, hardlinked, missing, or directory
entries. Only existing regular files with one link are supported. A directory
bind could permit creation of a nested `AGENTS.md` that does not yet exist;
supporting it needs a separate design. Creation of a new allowed file also
needs a separate safe strategy, because a bind target must already exist. The
linked-worktree `.git` file and its shared Git directory remain read-only;
commands requiring `.git/config` or lock files may fail.

Codex needs its remote model connection, so the namespace cannot always
disable networking. Network isolation is a separate precondition for an
offline fake role, and the production role must be limited to the Codex API by
an independently enforced network policy if that guarantee is required.

## Verification

The negative test must execute a real Bubblewrap process. It attempts create,
modify, delete, rename, hardlink, and symlink operations across the boundary;
accepts `EACCES`, `EPERM`, or `EROFS`; and compares content and metadata plus
`git status` afterwards. A missing Bubblewrap executable or namespace support
must fail closed instead of silently using the old invocation. The test must
run on a platform where the chosen Linux mechanism is available; another
platform needs its own explicit enforcement decision. A CI runner that blocks
unprivileged mount namespaces skips the positive Bubblewrap integration tests
after a capability probe; fail-closed tests for missing or failing Bubblewrap
still run. A green CI check on that runner does not itself prove active OS
confinement. Local active-namespace test evidence is reported separately.

## Limits

Bubblewrap mount isolation does not impose CPU or memory limits and does not
install a seccomp filter. A read-only mount does not stop reads, and a writable
temporary mount is still writable during the call. `_assert_scope` is retained
to catch changes that the namespace policy or its implementation misses.
