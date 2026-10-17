# ADR-358: bounded outbound owner notifications

Date: 2026-10-17. Task #358, epic #294. Owner-approved design; current-client
source admission remains BLOCKED / UNPROVEN.

## Status and source admission

This is a reviewable design, not installation, recipient approval, runtime
activation or a new mandate. No implementation or current-client support is
claimed. Current desktop chat source capability is **BLOCKED / UNPROVEN**.
The local CLI 0.157.1 protocol schema establishes typed request vocabulary,
not access to this desktop's thread or passive observation ownership. Its
managed daemon control socket was absent; this does not establish absence
of a separate desktop server. Creating a fresh server cannot satisfy that gap.

Admission requires an approved, documented current-client event interface,
exact desktop/host/thread mapping, and a local synthetic probe on each claimed
question/approval/block route. A passive observer must neither answer nor take
ownership of requests. Probe evidence must demonstrate matching event IDs,
pending/resolution, reconnect recovery, and the unchanged original desktop
question. Retain metadata only. Missing endpoint identity, event coverage or
pending recovery keeps readiness BLOCKED. Freeform commentary/final questions
are not automatically typed events; unsupported routes remain explicitly
unsupported. No rollout/transcript parsing, UI scraping, guessed socket or
new owned session substitutes for this proof. Installing hooks or running a
probe that changes client configuration needs separate bounded approval.

The implementation must not silently downgrade the requested current-chat
coverage to runner-only events. A different supported source, a bridge, or a
manual fallback needs a separately reviewed source-contract decision.

## Boundary and event contract

Only OWNER_INPUT_PENDING and REQUIRED_BLOCK are eligible. They originate from
the admitted source for the exact approved repository, epic and thread.
REQUIRED_BLOCK uses a closed approved reason-code list, never an exception,
log line or arbitrary model prose. Missing reason classification uses a fixed
unknown-block template without exporting the original reason. Completion,
progress and ordinary tool output do not generate notifications.

A closed private envelope 1.0 binds source proof digest, profile digest,
repository/epic, opaque thread/turn/item/request identities, kind, reason code,
source generation and original creation time. Input lengths, field types and
versions are bounded and unknown fields rejected. Question text, options,
answers, tool arguments and results are neither copied into the envelope nor
hashed for outbound use. Arbitrary upstream identifiers remain private and
are converted to bounded opaque notification IDs for the external message.
A source event is not an authorization receipt. Digests bind records; they
do not authenticate an untrusted source or owner identity.

The exact templates (ASCII placeholders below are validated metadata) are:
`Codex: требуется ответ владельца. Epic {epic}. Уведомление {id}. Чат {chat}. Ответьте в Codex.`
and
`Codex: работа заблокирована. Epic {epic}. Причина {code}. Уведомление {id}. Чат {chat}. Откройте Codex.`
Allowed REQUIRED_BLOCK codes are SOURCE_UNAVAILABLE, AUTHORITY_EXPIRED,
OWNER_DECISION_REQUIRED, CHECK_FAILED and UNKNOWN_BLOCK. Unknown upstream
codes map only to UNKNOWN_BLOCK. Positive epic IDs use canonical decimal,
notification IDs use 32 lowercase hex characters derived from the stable event
key, and chat is the approved canonical UUID or verified link. Maximum rendered
message length is 512 Unicode code points; truncation is forbidden. No task
number or other optional variable appears in these initial templates. No generated
summary, issue title, user name, raw request digest, path, prompt, diff, log,
manifest, route, credential, restored value or payload is exported. Links are
assembled from an approved origin and canonical encoded identifiers, never
accepted from event content. Until a supported link is proven, use the approved
stable identifier with clear instructions to return to Codex. The operator
must verify that the identifier maps to the intended original chat.

## Approval, identity and secrets

A closed owner-approved notification profile 1.0 pins repository/epic/thread,
source proof digest, adapter bytes, event/template allowlists, one bot numeric
identity, one private recipient numeric chat identity, secret-store reference,
validity period and finite limits. Its canonical digest is approved explicitly.
The recipient binding is not inferred from a username, inbound message or
issue text. Changes require a new approval. Setup must verify bot and recipient
identities through separately authorized fixed read-only API checks before
any pilot send. Production readiness also requires explicit pilot approval.

The bot token is supplied only through an approved local secret backend with
validated owner-only access and backend identity. It is never provided through
chat, repository files, argv, diagnostic output or a manifest. The transport
loads it only after all profile/source/control guards pass. Token-bearing URLs,
raw response bodies, headers and transport exceptions must not be logged or
returned. No generic arbitrary-URL client or shell command is exposed.

Notification permission is separate from effect authority. It cannot authorize
merge, issue closure, repairs, installation, purchases, model calls or final
main approval. It does not extend existing task/mandate deadlines or reset any
attempts. Telegram replies are not read or processed. All answers and approvals
remain in the original Codex chat, with existing exact-source checks.

## Delivery, budgets and recovery

Use one fixed Telegram HTTPS endpoint and method with certificate verification,
redirects disabled, bounded response/input sizes, fixed request timeouts and
no proxy/environment endpoint override. Send only the approved plain-text
message with previews disabled and no reply markup or callbacks. Do not add
webhooks, getUpdates polling, inbound listener or remote execution.

Before a send, reserve the event identity and attempt in the private bounded
outbox. A single canonical owner-only namespace outside worktrees is pinned
independently of profile versions. Its initial creation/preseeding is a separate
owner-approved setup operation; the sender refuses missing/corrupt state and
never recreates an empty history. A canonical regular-file OS lock with checked
owner, no symlinks and a maximum2-second nonblocking acquisition budget serializes source validation,
deduplication, reservation, network attempt and final outcome across all
processes. Hold this lock through the bounded request and outcome save. A second
writer cannot send without observing the first reservation. Store replacement
uses atomic visibility under that lock; interrupted/uncertain persistence blocks
new sends rather than claiming successful reservation or clearing uncertainty.
Pause/stop may wait for an existing bounded send and cannot recall it. The event key is SHA-256 of a versioned canonical tuple containing the fixed
logical source namespace, canonical repository/epic/thread and original
turn/item/request identifiers plus event kind. Transport connection generation,
profile/proof/template/recipient digests and notification-run ID are excluded
from this stable key. Their exact bindings are stored and validated separately;
a changed binding does not turn an already seen event into a new send. Original
IDs must have demonstrated stability across reconnect; otherwise admission is
BLOCKED. Repeated observation across reconnect, restart, profile update or run
replacement never replays SENT or UNKNOWN. Conflicting bindings block; a new
profile cannot reset the original outbox, attempts or deduplication history. Conflicting event bytes,
missing/corrupt history, capacity exhaustion or changed identity block new
sends; they do not clear the deduplication history. The profile must explicitly contain each following integer limit with units;
missing values or values exceeding these fixed hard maxima are rejected. The
initial reviewed reference values equal these maxima: 32 pending records,
128 retained event records, 2 attempts per event, 64 total transmission
reservations for the original notification run, 900 seconds event age,
10 seconds total request timeout, 2 seconds lock acquisition, 16384 response
bytes, 4096 input-envelope bytes, 4096 stored-record bytes, and 2097152 total
outbox bytes. Retry delay is a separate integer constraint: 30 through300 seconds
inclusive, reference30; it is a floor/range, not one of the reducible maxima.
Retries still obey the earlier original deadline, with no extension for lock
wait or backoff. IDs are at most128 ASCII characters and all rendered data must
meet the smaller template-specific grammar. Profile lifetime is at most86400
seconds and may not exceed the approved original notification-run deadline.
Clock rollback or unknown elapsed time blocks attempts. Age uses original event
time and preserved deadlines; source replay cannot refresh them. All maxima
are implementation constants, not owner-overridable suggestions. Lower approved
limits are allowed; raising a hard maximum needs a separate design decision.
At capacity, retain history and report BLOCKED; do not evict SENT/UNKNOWN or
initialize a new store to bypass limits. No unbounded queue, timer or background
process is implied by this ADR.

Outcome states are RESERVED, SENT, NOT_SENT and UNKNOWN. SENT requires a
validated matching success response with the expected destination and message
identity; it means API acceptance, not owner reading or approval. After a
reservation, crash, timeout, truncated/malformed response or ambiguous network
failure becomes UNKNOWN and is never automatically resent. A local failure
provably preceding transmission may become NOT_SENT and use the same bounded
retry budget; unproven non-delivery remains UNKNOWN. A negative remote response
is not by itself proof that no message was accepted unless the specifically
reviewed API contract establishes that classification. Retry must not invent
idempotency support. No exactly-once or guaranteed delivery is promised.

Recheck profile validity, source identity, original pending status and
pause/stop/revoke before every attempt. Resolved source events suppress unsent
notifications; a generic request-resolved event is not proof of an affirmative
answer. Pause blocks new sends; stop is terminal for the approved notification
run. Already in-flight delivery may complete and cannot be recalled. Preserve
pending/UNKNOWN records for read-only reconciliation; restart never refills
budgets or recreates missing state. Separate atomic file writes are not a
cross-file transaction or a crash-durability guarantee.

Delivery failure leaves the original question, owner wait, stop and effect
state unchanged. Local status distinguishes source BLOCKED, notification
NOT_SENT/UNKNOWN and API-accepted SENT. Notification completion never wakes or
continues the coding agent by itself. Any persistent delivery worker requires
separate approved installation and demonstrated supervision/readiness.

## Threats and verification

- Forged issue/model text triggers a send: accept only admitted source-bound
  events; synthetic injection must produce zero network calls.
- Sensitive content reaches Telegram: deterministic closed templates, metadata
  projection and adversarial sentinel tests, not free-text redaction guesses.
- Recipient/token replacement redirects messages: pinned identities/profile,
  owner-only secret backend and changed-binding refusal before transmission.
- Lost response or restart duplicates a message: reserve before transmission,
  UNKNOWN blocks resend, preserved counters and event keys.
- Reply masquerades as approval: no inbound API or decision handler; original
  Codex authority and final main gate unchanged.
- Source reconnect loses a pending question: current-client recovery probe is
  mandatory; absent proof is BLOCKED, never inferred from a live process.

#359 may implement the approved bounded transport and synthetic state machine
only after the source-contract blocker has an approved disposition. #360
verifies actual readiness, fault/restart/stop cases, runbook and separately
approved pilot. Synthetic transport tests do not establish desktop integration,
secret backend readiness, actual recipient approval or real delivery. Task
completion requires canonical final local gate, independent controller, exact
CI and applicable post-merge evidence. Public pgw/Library contracts, installed
runtime schemas, workflows, dependencies and product security guarantees stay
outside this documentation task.

## Sources and limits

[OpenAI Hooks](https://learn.chatgpt.com/docs/hooks) describes local lifecycle
and tool hooks with coverage limits; it does not prove this client route.
[OpenAI App Server](https://learn.chatgpt.com/docs/app-server) describes typed
requests and thread interfaces, not verified access to this desktop instance.
Local generated schema is metadata evidence only. The independent private
capability report records the remaining gap without exposing chat contents.

[Telegram Bot API](https://core.telegram.org/bots/api#sendmessage) documents
HTTPS bot requests, sendMessage success results and response parameters. The
transport design deliberately treats ambiguous outcomes conservatively; this
source is not evidence of idempotent delivery or the recipient reading a message.
