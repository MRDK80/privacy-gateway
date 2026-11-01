# ADR-303: fresh config8 SourceReader policy batch

Status: owner-approved bounded design; implementation and validation separate.
Owner accepted design manifest
`b7a2e54e4772edfefa44f575d6b2612f6b1c1519299703103b65d0fdb98b73dd`
and finite scope manifest
`7e3ed6c695c8e7cf66701a57253c9df6b098513647b20f3bef74167d3af18008`
through `call_b6bd3eb71e244176babc54bbc6ca5c33`. The separately retained
owner receipt and independent design review bind that decision. No installation,
activation, runtime mandate, additional repair budget or readiness follows.

## Selection and preserved callers

Only SourceReader.guard selects verify_policy_config8_batch, for its already
validated string schema_version `8.0`, after original config equality,
bootstrap_authority and managed_launch_guard. Later learning/profile/catalog/
owner/identity guards remain effective. Every guard performs fresh proof; there
is no cache, caller flag, fallback, reduced cadence or imported-module subset.
The original verify_policy, helpers and all other callers remain exact,
including config8 CLI/session/entry/install/status/reconcile/quota routes.
SourceReader still admits only schemas7/8; earlier/unknown values retain refusal.

## Three commands and explicit restrictions

A successful selected proof performs entry `git show`, original inventory
`git ls-tree -r --name-only`, then default `git cat-file --batch`. Requests are
exact ordered `SHA:path` ASCII LF records and final LF/EOF. SHA remains lowercase
40 hex. Git environment, replacements, alternates and resolver behavior remain
unchanged and trusted; no Git minimum, upgrade or network-absence guarantee is
introduced. subprocess.run retains captured binary output, check=False,
30-second timeout and original OSError/TimeoutExpired classes. Nonzero exits
reuse POLICY_REVISION_UNAVAILABLE; no per-file fallback follows batch failure.

Entry show exit handling, immediate entry read/comparison and original read
exception precede new entry stdout8MiB/stderr64KiB limits. Mismatch remains
UNTRUSTED_RUNTIME_COMMAND even alongside oversized captured output.

Inventory must be nonempty ASCII LF records with final LF, at most1MiB/4096
paths, stderr at most64KiB. Each unique path is at most1024 bytes, under tools/
or docs/schemas/, with slash components matching [A-Za-z0-9_.-]+, excluding
empty/dot/dotdot/trailing-dot components. Quotes, backslash, spaces, controls,
non-ASCII, absolute/drive/colon names, duplicates and casefold collisions refuse.
Each component's stem before its first dot excludes CON/PRN/AUX/NUL/COM1–9/LPT1–9
case-insensitively on every host. Original runtime/codex-adapter required paths
remain required. Batch request bytes are capped at1MiB before dispatch.

Batch stdout is at most64MiB and stderr64KiB after capture. Exactly one ordered
response per path is required: ASCII LF header at most96 bytes, lowercase40hex
OID, literal blob, canonical decimal size0 or nonzero without leading zeroes,
size at most8MiB, exactly size raw bytes and one LF. OID must equal SHA1 of
`blob <actual-size>\0<contents>`. Contents remain binary; repeated OIDs still
require separate responses. Missing/ambiguous/nonblob/malformed/truncated/extra
responses or trailing bytes refuse. The stricter object predicate can refuse
replacement/nonstandard/object-format responses accepted by legacy resolution;
it does not disable provider behavior or claim universal compatibility.

## Filesystem and error ordering

After complete batch parsing, retain original ordered target/parent symlink,
regular-file/current-UID/private-mode/current-byte checks and final recursive
lstat/ownership/mode/extra-file inventory. Original OS exceptions survive when
reached. There is no new universal hardlink guarantee, lock or atomic/ABA proof.
Inventory and eager batch failures may precede earlier per-path filesystem
faults. This changed config8-only ordering and tighter bounds are explicit
parts of the approved design; original entry precedence remains exact.

Git remains the trusted ordered SHA:path resolver. Framing/cardinality/hash
consistency does not independently attest path-to-OID association or detect a
permutation of self-consistent blobs. Current installed-byte comparison can
refuse differing mapped contents; a test must not attribute that to hash-based
path attestation. Eager collection changes read timing. All output limits are
postcapture and do not bound peak allocation, CPU/disk, descendants or cleanup.
Three commands do not imply a global90-second wall-clock or deadline fit.

## Evidence boundary

The causal test first completed a genuine synthetic config8 SourceReader and
another full guard under RunnerLock on original code, then failed only policy
Git fanout119<=3. That accepted RED is not candidate PASS or performance proof.
Focused prospective tests cover genuine selection/freshness and authority,
legacy byte preservation, real Git binary/repeated-OID/alternate/replacement
behavior, parser/name/capture bounds, error priorities and installed files.
Malformed-provider unit seams prove parser behavior, not trusted admission.
POSIX fixture execution and Windows mypy do not prove native Windows admission
or Windows parser execution. Existing unsupported-owner boundary is preserved.
Full final local gate, independent controller, exact CI and final task evidence
remain separate. No collector/lifecycle/report/READY claim follows this change.

## Owner-approved addendum: fresh legacy retrieval optimization

The owner separately approved fresh Git batching for the legacy verifier after
an independent compatibility/security review. This supersedes the earlier
source-byte preservation requirement for `verify_policy` and its private retrieval
helper only. Config selection, schemas, authority, ledger behavior and guard
cadence remain unchanged; config7 still calls `verify_policy`.

The legacy verifier retains its entry proof, original inventory, required paths,
ordered local symlink/type/owner/mode/content checks and complete namespace walk.
An optional per-call fast path obtains a NUL-delimited Git tree inventory and
requires its ordered paths to match the legacy inventory. Exact blob IDs bind
ordered batch responses; type, size, framing, content hash and final EOF are
validated before any batch is used. Installed files are still read freshly.

Fast-path path grammar and capture/object limits are eligibility conditions, not
new policy admission restrictions. Unsupported, malformed, incomplete, oversized
or nonzero optimization results, replacement-object mismatches and expected
transport failures discard the entire batch. The original per-file `git show`
then supplies expected bytes at the original comparison points. Local failure
precedence remains authoritative. Interrupts are not suppressed. This fallback
belongs only to legacy retrieval; the config8 verifier above retains its original
fail-closed batch behavior without fallback.

There is no persistent cache, checked-once flag, skipped guard or shared test
producer. No schema, CLI/Library contract, deadline, budget or resource limit is
changed. Git environment and replacement behavior are preserved by legacy
fallback. Capture limits remain postcapture bounds, not peak-memory or cleanup
guarantees. The normal eligible path uses four Git processes rather than one
per policy file; fallback does not promise that process count.

Regression coverage includes fresh mutations between calls, real binary and
legacy-path parity, replacement objects, response order/type/framing/hash,
inventory and transport failures, cancellation propagation and legacy local-error
precedence. A separately accepted old/new pair of the same learning case passed
with all31 probes in fresh equal-resource offline VMs: JUnit1339.808 seconds
versus268.125 seconds. This is one-case evidence, not full-suite timing or final
local-gate/CI acceptance. The pending full gate must validate the exact integrated
candidate separately.
