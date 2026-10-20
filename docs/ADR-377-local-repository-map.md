# ADR-377: bounded local Python navigation map

Date: 2026-10-18. Task #377, epic #294. Internal tooling only.

## Decision and boundary

The reviewed v4 design permits a local stdlib AST navigation aid. It does not
change public pgw/Library APIs, runtime schemas, authority, role inputs or gates.
It never imports or executes indexed source. Results and caches are private,
non-authoritative metadata; read current source before an action. This is not a
complete call graph, atomic filesystem snapshot or protection from a compromised
OS account, installed Git/loader, trusted administrative files or mutable mounts.
No real Git launch profile is enrolled by this change.

Windows (including Python 3.11/3.12) and POSIX hosts without directory-descriptor
and no-follow primitives refuse before filesystem access. Windows support needs
a separate reviewed handle/reparse-point design. Use rg/read as fallback.

## Data and limits

Tracked regular Python files are limited to `src/`, `tools/`, `tests/`.
Explicit untracked paths are separately supplied, never discovered. Dot/private,
environment, log, cache, installed, generated and build/artifact components are
denied even when tracked. No symlink or hard-link source/cache file is accepted.

| Bound | Hard maximum |
| --- | ---: |
| Eligible files / explicit untracked paths | 512 / 32 |
| Source file / source bytes per operation | 256 KiB / 8 MiB |
| Post-parse AST nodes / metadata entries | 1000000 / 100000 |
| Cache / query UTF-8 bytes | 16 MiB / 128 |
| Results / response bytes | 64 / 16 KiB |
| Identifier reference segments / links per result | 4 / 32 |
| Operation soft budget / membership child deadline | 30 s / 5 s |
| Membership stdout / discarded stderr | 1 MiB / 16 KiB |
| Stage records / bytes per NUL-terminated record | 8192 / 4096 |

The bounded inventory motivating these limits was 268 Python files, about
3.0 MB, 442293 AST nodes and 26072 calls. Node limits are enforced after parsing;
the byte limit does not guarantee parser CPU/memory/depth. Memory/recursion
failure refuses. Query truncation is explicit. Membership failure serves no
cached rows; extraction errors permit only explicitly INCOMPLETE partial rows.

Output contains relative file/module/qualified identifiers, kinds, line ranges,
async flags and parameter names/kinds/default-presence/annotation-presence.
Never export literal/default/annotation/decorator/docstring/body values or raw
parse/error text. Imports and direct call-target identifier chains are static
candidates; dynamic dispatch, rebinding, wildcard and ambiguity remain unresolved.
Test links state their static-import/reference or filename-candidate basis, not
coverage. Identifiers themselves are not promised secret-free; keep output local.

## Filesystem, launch and lifecycle

Validate the original absolute spelling from `/` with directory FDs and
NOFOLLOW; resolving first cannot hide a symlink. Retain root/admin descriptors,
read files relative to them, bound reads and compare identity/size/mtime/ctime
before/after and at the original path. Reject traversal, controls, backslashes,
case collisions and replacements. Cache is an explicit outside-worktree owned
0700 leaf, with owned single-link regular 0600 files. Ancestors must be current
UID/root and not writable by others, except root-owned sticky shared ancestors.

Only an externally owner-approved profile may pin one Git 2.43.0 executable,
SHA256/build provenance/installer UID, concrete ordinary or linked-worktree
administrative roots and exact reviewed config hashes or explicit absence.
The caller supplies the approved profile digest; a digest or profile boolean
cannot create owner approval. Config review must exclude includes, redirects,
promisor/partial-clone/filter/external-command configuration and unsupported
extensions. The tool does not auto-enroll profiles or parse general Git config.
Binary verification is bounded to 8 MiB; config files to 64 KiB and each of at
most three fixed linked-worktree pointers to 4096 bytes. Unknown input refuses.
Linked gitfile, commondir and backlink must match the exact approved layout.
The index is the current worktree's single regular index, never a main fallback.

Git runs with fixed argv: no pager/optional locks/replacements, explicit admin
and worktree, fsmonitor false, hooks disabled, untracked cache/preload/split-index
writing/maintenance/gc disabled, protocol never; only builtin `ls-files --stage
--abbrev=40 --sparse -z` with three literal top directory-prefix pathspecs.
No source bodies, object traversal, submodule recursion, shell or PATH lookup.
The fresh closed child environment suppresses system/global config and loader,
trace, askpass, proxy and inherited Git redirects. HOME/XDG_CONFIG_HOME are absent;
parent environment is untouched. Validated common/index bindings are explicit.
Git's internal metadata reads are trusted/unmeasured, not a resource sandbox.
Git 2.43 split-index reads may touch associated shared-index mtime: the approved
tradeoff permits only that trusted-admin freshness touch, not source, index
contents, config or authority writes. No timestamp immutability promise.

A standalone single-threaded owner sets SIGCHLD default before spawning and
retains sole exact-child waitpid ownership. Nonblocking selectors drain bounded
pipes in 16 KiB reads; no communicate/unbounded capture. Complete NUL records,
both EOFs and zero reaped exit before deadline are required. On failure close
pipes, nonblocking reap first, TERM only the positive unreaped direct-child PID,
wait at most 500 ms, reap again, KILL only if still owned/unreaped, wait at most
500 ms. Never group signals, signal after reap/ECHILD, unbounded wait or later
cleanup from saved PID. Ownership uncertainty/unconfirmed reap or suspected
pipe inheritor is CHILD_STOP_UNKNOWN. Cleanup polling may add up to 1 s to the
soft budget; syscall/scheduling is not a hard real-time guarantee. Descendant
absence/termination is not proven. Original membership is discarded on failure.

## Cache and query

Each update/query hashes all eligible bytes (including dirty tracked files),
parsing only changed hashes. Delete/rename removes old identities; syntax error
removes old symbols. Membership/config/identity drift never yields absence.
JSON has a closed bounded schema and checksum, never pickle/eval. Worktree key
binds physical root identity, tool/Python format and untracked allowlist, not
branch name. Exclusive lock refuses BUSY without stealing. One same-directory
temporary and atomic replace provide visibility, not durability or a source
snapshot. Interrupted/missing/corrupt/unsafe cache requires explicit reconciliation;
no automatic history deletion or PID signaling. Cache is never authority.

## Internal runbook

After separately approved Git/config enrollment, invoke the standalone script:

```text
python tools/repository_map.py --root ABSOLUTE_WORKTREE --cache ABSOLUTE_PRIVATE_LEAF \
  --profile ABSOLUTE_PRIVATE_PROFILE --approved-profile-digest SHA256 \
  --kind symbol --query QUALIFIED_NAME
```

The private profile uses the closed schema in `Profile.load`: version 1,
Git version 2.43.0, executable/digest/installer UID/build, administrative paths,
reviewed config digests or null absence, the fixed config-review label and the
approved shared-index mtime tradeoff. Supplying these fields or their digest does
not enroll them; separate owner approval remains required.

Kinds are symbol, prefix, file, import-users, references and test-candidates.
`--include-untracked` names at most 32 explicit eligible relative files.
No enrollment/installation helper, background worker or automatic prompt hook is
provided. COMPLETE means navigation metadata read successfully, not gate PASS;
INCOMPLETE/unsupported/stale results require current source and rg fallback.
Fixed errors never include profile/config paths, raw stderr or source values.

## Verification and measurement

Synthetic test cases specify metadata non-export, unresolved links, dirty/hash cache
invalidation, delete/rename/syntax/limits, safe paths/linked worktrees/config
pinning, poisoned environment, enumeration framing/caps/deadlines, sole-child
reap/signaling faults and Windows pre-touch refusal. Test-only launch profiles
are not real owner approvals. No indexed module side effects may execute.
Actual test outcomes, platform refusals and environment blockers must be reported
separately; authored cases alone do not establish these properties.

Root measurement uses predetermined symbol/import-user/test-candidate queries
and manual ground truth on one pinned snapshot, plus a single-file update.
Results include a monotonic source-read time range; it is not an atomic snapshot.
Report correct/missing/false candidates, build/update/query wall time, source
file/byte reads and actual context bytes separately. Warm refresh still hashes
all source. Git/rg internal physical I/O is UNKNOWN; pipe bytes and logical
inputs are separate. Bytes are not tokens; claim token savings only from primary
usage evidence, with no new paid calls just for metrics. No benefit is a valid
result. Independent controller, final local gate and exact CI remain required.

### Observed synthetic example (2026-10-19)

One four-file synthetic repository was measured once. Both methods matched the
predefined file/line ground truth for all five queries, with no missing or false
candidates. Context bytes were query-result JSON for the map versus rg stdout
plus full targeted source reads, excluding client framing and separate metrics:

| Query | Map bytes | rg + read bytes | Parsed files |
| --- | ---: | ---: | ---: |
| Cold symbol | 588 | 76 | 4 |
| Warm symbol | 588 | 76 | 0 |
| Warm import users | 801 | 258 | 0 |
| Warm test candidates | 718 | 129 | 0 |
| Symbol after one-file update | 523 | 100 | 1 |

Every refresh read/hashed all four files: 186 source bytes initially and 216
after the update. Refresh including launch verification took 9.3–10.6 ms;
subsequent query took 0.041–0.065 ms. rg plus targeted reads took 2.7–4.5 ms.
There was no time or context-byte benefit in this tiny example. No tokens were
measured, and no general performance or token-saving conclusion follows.

Order was fixed (map then rg/read), with no OS-cache reset; cold means empty
map cache. The #379 full gate ran concurrently. These single-sample timings
include cache/order/concurrency bias. Git/rg physical I/O was not measured;
logical source bytes and observed pipe/context bytes are separate quantities.
Only the isolated synthetic repository and test-only profile were used: no real
repository profile enrollment, runtime activation or agent-prompt integration.
