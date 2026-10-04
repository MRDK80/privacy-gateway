# ADR-290: bounded read-only GitHub retries and runtime errors

Date: 2026-10-04. Task #290; owner-approved corrective scope for epic #248.
This decision does not activate policy or resume stopped epic #131.

## Decision

Retry a single read-only GitHub subprocess request, never an entire live phase,
executor, controller, Git/GitHub write or delivery operation. Concrete task
runtime opts its issue/check/ref clients and GitHub reconciliation reads into
a shared bounded read runner. Other adapters keep their existing behavior.
Only closed argv forms used for `gh issue view`, `gh pr view/checks` and
`gh api` GET without body/method overrides qualify. Local Git commands,
including network `git ls-remote`, and every write use their existing path.

After the initial failure, delays are exactly 60, 60, 60, 60, 60, 180, 300,
600, 900, 1200 seconds, measured using monotonic time from completion of the
preceding failed request. There are at most eleven requests and 3480 seconds
(58 minutes) of scheduled waiting, excluding request duration. An attempt is
one complete read command, including its existing pagination; this does not
bound individual HTTP page requests internal to GitHub CLI. Each request
has a finite 120-second timeout. Success ends the loop immediately. Output
size, JSON parsing and structural/identity validation remain fail closed and
are not inside the retry loop.

Transient evidence is narrowly classified: a subprocess timeout or explicit
GitHub CLI Go transport diagnostics for TLS handshake timeout, dial/network
timeout, connection reset/refused or temporary network unreachability. A
generic nonzero status, arbitrary message containing "timeout", HTTP error,
authentication/permission error, malformed response or unsupported command
does not establish a transient failure. Permission/auth/identity/policy
markers take precedence even when accompanied by transport wording.

Raw stdout/stderr, exception text, argv and credentials are never included in
read failure exceptions or runtime envelopes. Failure stores only a closed
category (timeout, network, unknown). Original runtime machine codes remain
unchanged. CommandRuntimeAdapter recognizes the exact existing error envelope
{status: BLOCKED|ESCALATE, machine_code: safe code, receipt: null} for live and
effect requests. Valid BLOCKED raises PhaseBlocked; valid ESCALATE raises
LoopError with that original code. Extra fields, bad codes and inconsistent
receipts remain RUNTIME_ADAPTER_INVALID. No new public pgw/Library contract,
checkpoint/ledger/mandate schema or machine code is introduced.

During waiting, local retry guards run at least once per second and immediately
before another request. They reread mandate identity/digest/lifecycle and
checkpoint identity, detect recovery owner stop/generation change and check
the original pinned recovery approval expiry. No guard opens stopped recovery,
writes private state, renews expiry or reserves delivery attempts. Cancellation
(including process interruption) exits waiting; there is no recovery of the
wait through an automatic adapter restart. Exhaustion reaches the existing
durable stop path. Existing outer runtime-adapter deadlines remain unchanged:
a configured timeout shorter than the retry sequence can terminate it early;
supporting the whole sequence requires a separately owner-configured deadline
that also accounts for all request durations. Authority and canonical live
identities are still revalidated immediately before every later write.

## Verification and limitations

Synthetic fake-time regressions precede production edits. They verify exact
schedule/count relative to request completion, early success, exhaustion,
immediate non-transient failure, cancellation/expiry/owner stop, sanitized
errors, structured error propagation, single-attempt writes and zero later
effects when authority changes during a retry. Full local gate and primary
exact GitHub CI are reported separately. Read success proves facts only;
it never grants delivery authority or clears a durable stop latch.

This is not an exactly-once guarantee or a cross-file transaction. Existing
reconciliation and bounded operation recovery rules in ADR-249/ADR-284 remain.
Original and replay #131 artifacts, mandates, checkpoints and ledgers remain
unchanged. Installation, new mandate/recovery approval and #131 restart require
separate owner decisions.
