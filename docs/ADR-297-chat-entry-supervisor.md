# ADR-297: pinned chat entry and explicit supervisor control

Date: 2026-10-11. Task #297, epic #294.

## Status

Owner approved this design in chat on 2026-10-11 before production implementation.
Approval of this design does not install policy, issue a mandate or start a
production process. Stopped epic #131 remains outside this task.

## Decision

Add a repo-local `pgw-epic` Codex skill and an internal
`tools.agent_epic_entry` entry with discover, prepare, start, status, pause,
stop and resume operations. The skill invokes the installed frozen `runtime.py`;
its stdlib authority and policy-byte checks precede importing the pinned entry
module. This bootstrap chain preserves the installed executable boundary;
mutable repository scripts, issue text and model output cannot supply argv,
executable or authority. The entry delegates execution to the existing
whole-epic supervisor rather than implementing task delivery itself.

Discovery is read-only. Require an explicit canonical repository and positive
epic number, validate the local remote identity, structural GitHub parent and
children/dependencies, unique roadmap ref and its ancestry. Only an explicitly
supplied owner-only run registry outside worktrees may identify private runs.
Do not scan arbitrary home directories, discover old state implicitly or infer
an epic from titles. Missing or ambiguous bindings stop before execution.

Preparation produces a closed internal launch candidate `1.0` with repository,
epic, roadmap_ref, policy_sha, installation_digest, runtime_config_digest,
mandate_digest and owner_identity. The reviewable digest binds every field.
Missing mandate/config is reported as missing prerequisites, not replaced by
a generated approval. A candidate is not authorization. The owner must first
supply the existing valid approved mandate/config and separately approve this
exact launch candidate digest. Existing approved plans remain mandatory;
automatic planning belongs to #298.

New opt-in whole-epic runtime config `2.0` extends `1.0` only with
`approved_launch_digest`. A closed owner-only `launch.json` `1.0` stores the
candidate fields. One-task config and existing mandate/checkpoint/ledger
schemas retain their formats. Legacy configuration remains usable without
entry management; presence of managed launch/control files cannot be bypassed
by invoking a legacy or direct entry. Unknown fields/versions fail closed.
The installed runtime verifies launch digest/config binding and existing
mandate lifecycle and pinned bytes before imports or effects. The config digest
covers both epic runtime config (excluding only `approved_launch_digest` to
avoid circularity) and the complete runtime adapter config, including all
argv, timeouts and output limits.

A separate closed owner-only control record `1.0` binds launch_digest and
state RUNNING, PAUSED or STOPPED with a monotonic control generation. A start
or resume reserves its observed generation before validation and cannot clear
a newer owner pause/stop. Start cannot substitute for resuming a paused run.
Its initial creation requires the exact
approved launch, and it cannot replace existing unrelated state. PAUSED blocks
further effects; resume may move PAUSED to RUNNING only after fresh lifecycle,
identity, readiness and pending-effect checks. STOPPED is terminal for that
launch and cannot be cleared by start/resume, successful CI or a new chat.
A later authorization needs a separate reviewed candidate and explicit owner
disposition of preserved pending state, outside automatic resume.

Control is checked in the supervisor and concrete runtime authority callbacks,
including direct effect entry and downgraded legacy config paths. Read-only
reconciliation remains available while effects are blocked; it never clears
STOPPED or issues new authority. Pause/stop return
control status, not task completion. An in-flight operation may already have
applied before the control request; no rollback or instantaneous cancellation
is promised. Preserve pending intents and use existing read-only reconciliation
before any authorized continuation. Never reset attempts, receipts or cursors.
A separate short control lock serializes control changes; the existing process
runner lock still prevents competing whole-epic execution. Resume refuses an
active runner rather than killing it or trusting a reusable PID. The entry
acquires the same canonical session process lock before validation,
reconciliation or control mutation, and retains it through child completion.
The bounded Bubblewrap child inherits that locked file descriptor; the session
accepts it only after checking the canonical private file identity and ownership
of the exclusive lock on that exact open-file description. Child close does not
unlock the parent-held lock. The internal descriptor marker is consumed and
the descriptor made non-inheritable before effects/model subprocesses. This
transport conveys process ownership, not effect authority.

Start/resume invoke the existing synchronous installed supervisor with fixed
argv through the existing bounded Bubblewrap adapter. Durable checkpoints
permit a later explicitly invoked resume independently of chat history.
This task supports foreground process execution and observation; no autonomous
cross-turn scheduling is claimed. Persistent waits and actual wakeup are
separate #299/#300 requirements. Unsupported requested background capability
returns a concrete blocker. No detached process, inferred quota reset or
client automation is silently created.

The frozen entry applies mandate lifecycle checks to every action, including
status, pause and stop. After expiry or revocation those entry actions return
the lifecycle blocker; they cannot install new control through an invalid
mandate. Existing read-only reconciliation remains available through its own
guards. Do not infer a recorded pause/stop from a rejected request.

Status reads private canonical facts and reports bounded identifiers, phase,
control state and safe codes with canonical issue/PR links. It omits private
paths, argv, raw logs, payloads and credentials. Lock occupancy means an active
runner, not verified progress. Exit zero alone does not mean completion.

## Verification and scope

Synthetic tests cover wrong remote/epic/roadmap, ambiguous registered runs,
missing approval, altered candidate/config, injected argv, stopped direct
entry, pause/resume with revoked/expired mandate, second runner and preserved
pending effects. Integration tests must prove invocation of the existing
supervisor through the entry, using isolated synthetic fixtures; a mocked
instruction string is insufficient. Validate skill metadata and run the full
local gate, independent controller and separate PR/post-merge exact CI.

No public Library/pgw contract, dependency metadata or release change. No
production activation, #131 migration, final main approval, automatic plan,
persistent wait or wakeup implementation is included. Individual atomic writes
do not establish cross-file transactions, crash durability or exactly-once
execution. Same-OS-account compromise remains outside the base threat model.
