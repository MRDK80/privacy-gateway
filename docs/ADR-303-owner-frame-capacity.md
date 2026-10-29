# ADR-303: owner frame byte capacity

Status: owner-approved64MiB successor on2026-10-25.
Prior owner-approved selection on2026-10-23 was16MiB, replacing8MiB.

## Approved resource decision

Set `OwnerFrameSources.MAX_TOTAL_BYTES` to64 *1024 *1024 =67108864 raw
bytes for each NEW instance. This is an actual internal production bound,
not a test-only override. It is four times the current16MiB finite ceiling.
A V6 targeted native prefix exhausted16MiB during merge-B derivation; no
whole-path headroom or runtime fit at64MiB is established.64MiB is an approved
resource allocation, not a measured complete-path bound or a performance fix.

Scans and every repeated frame read charge actual returned bytes to the SAME
instance `_bytes`; zero-byte EOF charges zero, but an exhausted counter rejects
before another read/EOF. No refund/reset, phase/gate reinitialization, exception
for small files, cached PASS or changed revalidation cadence. MAX_ENTRIES4096,
MAX_FRAME_BYTES16384, path/identity/association/credential/source checks,
deadlines, strict UNKNOWN/expiry/stop and512read/8CI budgets remain unchanged.
No current used counter, durable wait, mandate or history gains an extension.

This capacity bounds cumulative raw I/O, not a64MiB retained allocation: frame
buffers remain individually limited16KiB. Actual read count and total native
walltime are not inferred from byte capacity. Larger I/O/guard work may raise
resource and latency exposure; per-instance limit is not an aggregate process
or whole-run cap. Fresh native policy/original generation must include the
approved source from its initial freeze; no stale policy overlay, rekey or
backfill of completed records is permitted.

Existing seeded-prefix regression retains real scan/revalidate and no-reset
history while selecting64MiB. Additional synthetic read-boundary cases must
verify pre-read refusal, clipped remaining-byte request, exact-budget EOF
refusal and spare-byte EOF success without huge physical I/O. Seeded prefix
and stub I/O are explicit unit qualifications, not full64MiB/native evidence.

The separate future diagnostic checkpoint must assert the actual production
class64MiB value and compare its live counter against that value, with fresh
source hashes/policy bindings. It must not keep stale16MiB success predicates
or claim PASS using a diagnostic-only override. This ADR authorizes no runtime
installation/activation, native/fullgenuine run, external effect or merge.
