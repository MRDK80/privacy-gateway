# ADR-300: quota observation and bounded persistent supervisor wakeup

Status: accepted. Independent design review PASS; owner approved implementation
on 2026-10-11. Installation, activation and main merge remain separate decisions.
Task #300, epic #294. No installation, activation or mandate is granted here.

## Decision

Opt-in epic config 5.0 retains config4 planning/wait bindings and adds
approved_quota_digest. Config1/2/3/4 keep their existing closed shapes. A closed
owner-approved quota profile1.0 binds repository/epic/roadmap/policy/mandate,
owner, exact existing wait profile digest, a metered limit_id, a pinned absolute
Codex executable and finite polling/lifecycle limits. A profile cannot create
an action grant or change repair/task budgets.

The quota source uses a bounded no-shell stdio App Server transport with fixed
initialize/initialized and account/rateLimits/read messages only. It cannot
call inference, login/logout, refresh, purchases/reset or owner notification.
It parses capped structured responses in memory; no raw output/account/credit
fields are persisted. Prefer rateLimitsByLimitId for the pinned bucket, with
strict compatible legacy selection; missing or ambiguous selected quota is
UNKNOWN, never free. A nullable secondary window is accepted only alongside a
valid identified primary window. Validate both finite usedPercent windows and
reset timestamps; unknown reset schedules a bounded fresh read. Elapsed reset
time is no proof of quota recovery. Only structured rateLimitExceeded or
usageLimitExceeded interruptions can enter quota pause; context/output/task
or repair budgets and unclassified errors retain their separate outcomes.

A new closed private quota-wait record1.0 retains original identity, stage,
start/deadline/last-seen, selected exhausted windows/reset hints, poll attempts,
next_check, and interrupted operation identity. It uses owner-only bounded
storage outside worktrees and reserve-before-read. Reuse original phase budget;
restart/config drift never extends the deadline or refunds an attempt. Frame,
mandate expiry/revocation, launch/control and active checkpoint guards apply.
Unknown clocks, unsupported source, malformed quota and exhausted finite limits
stop fail closed. Existing checkpoint and ledger schemas remain unchanged;
existing PAUSED_RATE_LIMIT pause/resume functions are integrated rather than
claimed as sufficient on their own.

Persistent wakeup is a separately installed POSIX host adapter, initially
systemd user service/timer with fixed trusted supervisor entry and state,
no shell, bounded deadlines and no model/issue-derived command. A separately
owner-approved installation candidate binds exact service bytes and closed timer template, policy/config,
state and a finite original lifecycle. Units contain no credentials/payload.
Installation is an explicit owner action; design approval and normal task
merge do not authorize it. No daemon/bootstrap/host settings change occurs as
part of normal readiness or resume. Unsupported hosts or an unverified waker
return readiness BLOCKED. No cron or chat prompt is substituted as proof.

On wake the fixed trusted entry reacquires existing nonblocking single-runner
ownership, rereads quota and validates original authority/live identities.
Pending executor/delivery outcomes reconcile read-only first. UNKNOWN stays
blocked; APPLIED avoids replay; NOT_APPLIED permits only an already-authorized
bounded attempt. Controller/final interruptions use the same lifecycle and
retain their phase and consumed budgets. Stop/revoke/expiry cancel future
work; leftover scheduled wake cannot create authority. A new launch requires
its own separately approved configuration. Windows remains unsupported.

Readiness requires exact source and waker bindings, existing installed unit
verification and a separately observed synthetic end-to-end probe that survives
the originating process/turn and a supervisor restart, with exactly one runner
and preserved budget. Running foreground sleep, future timestamps or successful
unit creation are insufficient. Synthetic fixture/probe results are recorded
as synthetic and never promoted to real owner receipts. Real installation and
activation remain separate owner decisions.

## Validation and boundaries

Regression-first for unknown quota, both exhausted windows and unknown reset;
quota interruptions in RUN_TASK/controller/final; bounded polling, restart,
stop/revoke/expiry, drift, competing/duplicate wake and pending effect handling.
Test installed frozen entry with real child boundaries and isolated synthetic
persistent host probes where available; otherwise verify honest readiness
BLOCKED. Full gate, independent controller, exact PR/post-merge CI and exact
merge-tree demo remain separate. No public product contracts, dependency or
release changes, #131 resume, main merge, budget reset or wider authority.

Official source: https://learn.chatgpt.com/docs/app-server
(account/rateLimits/read and multi-bucket/window fields). Installed CLI protocol
schema currently exposes quota reads but no scheduling RPC; systemd support and
actual durable wake must be probed, not inferred from binary availability.

## Closed design contracts

All records reject unknown fields, booleans in numeric fields, malformed SHA256,
unsafe paths, symlink/hardlink/private-mode violations and foreign ownership.
Repository/ref/owner identity reuse the existing validators. UTC values are
finite integer Unix seconds. Schema versions are literal strings.

Quota profile1.0 fields are exactly schema_version, repository, epic,
roadmap_ref, policy_sha, mandate_digest, owner_identity, wait_profile_digest,
limit_id, source, limits, wakeup.
- source has exactly executable, executable_sha256, version_digest,
  request_timeout_seconds (1..120). executable is an absolute owner-approved
  regular Codex CLI path; no PATH resolution or commands from source data.
  version_digest pins capped structured version identity rather than raw text.
- limit_id is 1..128 ASCII letters/digits/underscore/hyphen; no guessed bucket.
- limits has exactly poll_seconds (1..300), max_polls (1..512),
  max_wait_seconds (1..86400), max_wakeups (1..512); max_wakeups >= max_polls.
- wakeup has exactly backend (literal systemd_user), supervisor_sha256 and
  host_probe_digest (SHA256 or null). Null/unverified probe cannot yield READY.

Quota-wait1.0 fields are exactly schema_version, identity, stage,
original_wait_digest, started_at, deadline_at, last_seen_at, attempts,
status, exhausted_windows, resets_at, next_check_at, interrupted, wakeup.
identity binds exactly repository, epic, policy_sha, mandate_digest,
quota_profile_digest, wait_profile_digest, runtime_config_digest, limit_id,
source_digest. stage is the source-defined #299 stage grammar. The original
#299 record is created/validated first: original_wait_digest pins its immutable
identity/start/deadline tuple, not a mutable full-record digest. Quota deadline
is the minimum of its original deadline, frame deadline, mandate lifecycle,
applicable recovery expiry and started_at+max_wait_seconds; restart never
increases it. Quota polls retain their separate finite count without refunding
or resetting #299 reads, CI polls, task or repair counters.
status is READY/RESERVED/WAITING/FAILED. A RESERVED quota read after interruption
is unclassified and fails closed; a read does not authorize any effect.
exhausted_windows is a duplicate-free subset of primary/secondary; resets_at
is integer or null. WAITING requires next_check_at <= original deadline.
interrupted is null or exactly checkpoint_identity_digest, phase,
operation_digest, outcome (PENDING/APPLIED/NOT_APPLIED/UNKNOWN). Binding comes
from the existing trusted checkpoint/ledger, never stdout; facts are freshly
reconciled and this record cannot substitute for their canonical receipts.
wakeup is null or exactly candidate_digest, arm_id, attempts, status,
next_check_at, unit_digest; unit_digest binds exact generated timer bytes. arm_id is a SHA256 bound to original identity and poll ordinal;
status is RESERVED/ARMED/FIRED/CANCELLED/UNKNOWN. UNKNOWN cannot auto-reinstall
or overwrite an existing binding. Last-seen rollback closes the wait.

Installation candidate1.0 fields are exactly schema_version, repository,
epic, policy_sha, state_digest, runtime_config_digest, quota_profile_digest,
source_digest, supervisor_sha256, host_probe_digest, issued_at, expires_at,
unit_prefix, service_sha256, timer_template_sha256, candidate_digest. candidate_digest
hashes the other fields; unit_prefix is fixed pgw-quota- plus the first 32 hex
characters of the full state/config binding. Full binding is retained and
collisions rejected. expires_at cannot exceed existing approved lifecycle;
service bytes and the closed timer template/fixed argv are generated from trusted pinned configuration.
Owner approval is separate and pins this exact candidate digest. Unit names,
source/entry bytes, state location and host probe cannot change under it.

## Finite one-shot wake ownership

The timer is a one-shot absolute UTC trigger, not a repeating timer. It may
be armed only before next_check_at <= original quota deadline. Its service
runs the fixed trusted wake entry once; after firing no subsequent trigger
exists. Further WAITING creates a new ordinal/arm within the same original
finite lifecycle and consumes a wake attempt before the bounded arm effect.
No wake may arm a future trigger after deadline. Stale/duplicate callbacks
validate full binding and ordinal, acquire existing runner lock and do no
phase/effect work. A timer left after expiry can at most fire its already
bound one-shot: it cannot rearm or renew authority.

Installation and each arm use the existing canonical operation ledger style:
reserve before effect, bounded exact read-only systemd reconciliation, then
APPLIED/NOT_APPLIED/UNKNOWN. Existing units/state are preserved; conflicting
bytes, foreign names/links or unknown partial install stop without overwrite,
delete or blind retry. NOT_APPLIED permits only the already-approved bounded
attempt. Installation is separate from quota polling and never inferred from
an approval to implement the design.

Owner stop/revoke cancels the owned pending one-shot through the narrow pinned
host adapter and verifies inactive/no-future-trigger state read-only; expiry
blocks new arm and cancels any still-pending owned trigger. If host access
fails, cancellation is UNKNOWN and readiness BLOCKED; do not claim permanent
inactivity. No candidate can alter unrelated units, enable linger, bootstrap
daemons or change host settings. Required host durability is verified by the
separate synthetic process/turn-exit and supervisor-restart probe. A binary,
running foreground child or retained timestamp does not satisfy the probe.

The installed service uses fixed approved entry/state argv. Installation has no
active timer. Each arm creates a uniquely named one-shot timer under the
approved prefix plus its full arm_id, using the pinned closed timer template
with only validated next_check_at and service identity substituted. Its exact
unit digest is saved before creation and verified during reconciliation;
existing conflicting arm units are never rewritten. No arbitrary property or
command substitution is allowed. The installation candidate pins service bytes
and template bytes, not a future timestamp-dependent timer hash. Finite arm
allowance belongs to the separately owner-approved quota/installation policy.

Host-probe record binds source executable/version, trusted wake entry, service
and timer-template digests, host backend identity and observed process-exit/
restart results. It does not include the enclosing quota profile/config digest,
avoiding circular hash binding. Readiness separately validates final exact
config/profile/candidate bindings and installed bytes; a generic host probe is
never an installation approval or an operation receipt.

Structured interruption must come from the pinned role backend's first-party
error event, bound to the active executor/controller/final invocation. Existing
codex_adapter redacted stderr token matching is diagnostic only and cannot
trigger automatic quota pause. Opt-in role integration must propagate a closed
redacted invocation-bound classification from the supported structured backend;
if that backend cannot expose the positive code, readiness is BLOCKED for
quota interruption rather than fabricating structured evidence. The quota-read
transport itself remains read-only; it never performs role inference. Runtime
phase/reconciliation retains existing pending-intent semantics before any
quota resume can repeat admitted work. Synthetic structured backend tests alone
do not prove the installed real backend supports the required interruption.
