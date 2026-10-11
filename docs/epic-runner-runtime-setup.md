# Owner-only task runtime: deployment readiness

This is a local corrective implementation, not an activated production loop.
Do not run the old consumer-demo command against the current private state.

Process launch is not operator completion. Follow the
[supervision runbook](epic-runner-supervision.md) for observation, CI waiting,
reconnect and verified turn transitions. It grants no runtime authority and
does not establish background wakeup from a prompt or a running session ID.

## Trusted installation

Task #296 adds `python -m tools.agent_epic_install`, run only from the reviewed
policy revision. `prepare --repository-root ROOT --repository OWNER/REPO
--policy-sha SHA --directory CANDIDATE` copies pinned Git blobs to a new
owner-only directory outside worktrees, with `policy/`, `runtime.py` and
`candidate.json`. Its returned `candidate_digest` is an identity, not approval.
After the owner reviews the exact candidate, `install --repository-root ROOT
--candidate CANDIDATE --directory INSTALLATION --approved-digest DIGEST`
installs those exact bytes. The approval argument must come separately from
the owner; preparation never fills it automatically. No state/mandate is
issued and no runtime is started. An identical existing installation returns
NO_OP; conflicting or unsafe destinations are refused.

After separately preparing the existing owner-approved runtime config/state,
`readiness --repository-root ROOT --directory INSTALLATION --state-dir STATE
--github-identity LOGIN` checks the one-task profile. Add `--epic` for the
whole-epic profile; one-task adapter config `1.0` cannot pass that check.
The report contains only bounded check names and safe codes. It verifies
policy bytes, interpreter, actual namespace availability, config/mandate and
checkpoint provenance and read-only GitHub account/repository access without
importing candidate code. READY is readiness evidence, not task completion,
cross-turn wakeup, a launch mandate or production-pilot success.

Parent directories must already be owner-only outside worktrees. Temporary
staging cleanup is best-effort; separate directory publication is not crash
durability or a transaction with private runtime state. The installer never
rewrites active checkpoints or discovers/migrates stopped #131 state.
The source contract is [ADR-296](ADR-296-runtime-install-preflight.md).

First review and integrate the corrective code through the normal task PR
and exact CI process. A new owner-approved mandate must pin the resulting
policy SHA if the previous policy revision does not contain these modules.
The runtime cannot approve that mandate or silently advance its policy SHA.

Outside every worktree, install the byte-identical `tools` and `docs/schemas`
files from that policy revision under an owner-only policy directory. No
extra modules, symlinks or bytecode files are accepted. Copy that revision's
`tools/agent_epic_runtime_cli.py` to `<state>/adapter-bin/runtime.py`.
Directories are owner-only (0700), files owner-only (0600). The entry verifies
itself and every policy file against the pinned Git object before importing
runtime modules. The existing ADR-274 adapter verifies executable, entry,
state/config ownership and namespace isolation too.

The owner supplies, without committing or publishing their contents:

- `mandate.json`: separately approved ADR-249 mandate, not generated here;
- `handover.json`: approved coordinator handover for the exact task/base;
- `demo-plan.json`: reviewed ADR-255 plan for the applicable merge SHA;
- `demo-completion.json`: after an applicable demo actually succeeds, a
  closed owner-only `1.0` receipt containing `repository`, `epic`, `task`,
  `baseline_sha`, `roadmap_sha`, `plan_digest`, `owner_identity` and
  `completed_by_owner: true`. The digest uses the canonical frozen
  `agent_epic_final_runtime.digest` on the exact rebound plan. No real input
  or output is stored here. A plan alone cannot authorize task closure;
- `runtime-task.json`: closed schema `1.0` with `policy_root`,
  `approved_mandate_digest`, `approved_handover_digest`, `owner_identity`,
  `started_at`, `task_iterations`, `follow_up_issues`, `approved_order`;
- `runtime-adapter.json`: ADR-274 command configuration pointing only to the
  installed `runtime.py`, through the current trusted Python interpreter.

The entry accepts `live`, `effect` and `reconcile`. Effect/reconcile receive
the exact phase via `--phase`; every command receives `--state-dir` and
`--repository-root`. It emits one closed JSON object. Known safe phase blocks
use exit zero with a BLOCKED envelope so `CI_PENDING` is not confused with a
transport crash; uncertain writes retain reconciliation intent.

## Whole-epic continuation (explicit opt-in)

Runtime-adapter config `2.0` retains the phase/live/reconciliation argv fields
and adds `epic_command`: the installed `runtime.py epic_resume` command with
the same state/root arguments and trusted Python. Config `1.0` remains a
one-task runtime; it is not silently upgraded. Use the frozen supervisor from
the installed policy, not a mutable task checkout:

```bash
/TRUSTED/python /PRIVATE/policy/tools/agent_epic_loop.py resume \
  --state-dir /PRIVATE/epic-248 \
  --repository-root /PATH/privacy-gateway \
  --runtime-config /PRIVATE/epic-248/runtime-adapter.json
```

For whole-epic mode the owner also supplies these private files:

- `epic-runtime.json`: closed `1.0`, with `policy_root`,
  `approved_mandate_digest`, `approved_plans_digest`, `approved_final_digest`,
  `owner_identity`, `started_at`, `task_iterations`, `follow_up_issues`,
  `approved_order`. `approved_final_digest` may be `null` until the owner
  confirms the final demo at the actual final SHA. `task_iterations` is the
  number already consumed under this mandate before the seed task; the cursor
  adds each newly completed task, rather than reusing that initial counter;
- `approved-plans.json`: closed `1.0`, `initial_handover_digest` and `tasks`.
  Each numeric task key has exactly a `plan` (`2.0`) and reviewed `demo_plan`.
  Only plan `base_sha`/`default_sha` and demo `roadmap_sha` are rebound by the
  trusted factories to fresh verified refs/merge receipt. Scope, criteria,
  policy, paths, budgets and argv remain owner-reviewed recipe data;
- `final-approval.json`, supplied at the actual final SHA: closed `1.0`,
  `repository`, `epic`, `roadmap_ref`, `roadmap_sha`, `main_sha`, `policy_sha`,
  `owner_identity`, `demo_confirmed_by_owner`, `lesson_digest`;
- `verified-lesson.json`: the redacted verified lesson record, validated by
  the existing lesson validator, matching the approved lesson digest.

`approved_plans_digest`, `approved_final_digest` and `lesson_digest` use the
canonical `digest` function in the frozen `agent_epic_final_runtime` module.
Computing a digest does not approve a file. The owner supplies the approval
values separately; the installer/runtime does not generate final confirmation.
If final approval is missing, FINAL_GATE stops with FINAL_DEMO_UNCONFIRMED.

The seed is an explicitly prepared checkpoint `3.0` and handover `2.0` under
an owner-approved mandate `3.0`. Successors live under `tasks/<number>` with
their own checkpoint, handover, evidence and ledger. `session-control/cursor.json`
and its separate lock track the current task and pending preparation. Existing
seed/old checkpoints are not overwritten or reset. Unknown preparation stops
for reconciliation. Protected main/roadmap branches are never deleted.

## Verified local components and remaining boundaries

RUN_TASK uses handover validation, the existing bounded executor/full gate/
independent controller and a private artifact receipt. Commit publishes the
exact reviewed snapshot; push/PR/merge/close/epic writes use the scoped
delivery ledger, fresh authority and narrow concrete argv transport. No
success is inferred from checkpoint status, executor self-assessment or PR CI
when post-merge CI is required. Demo preparation never reads real input or
invents task completion receipts or final owner confirmation.

Crash after a complete bootstrap reconciles the real PR binding. A partial
bootstrap with ref/index changes but no proven complete PR binding remains
fail-closed for owner reconciliation; it is not blindly repeated.

Whole-epic mode recalculates the structural queue after TASK_DONE, prepares a
separate successor under the scoped mandate, and enters the separate final
driver when there are no open children. The final driver executes a full
local gate and negative lesson tests on the actual aggregate tree, independent
cumulative-diff review, exact roadmap PR CI, merge, separate main post-merge
CI, epic closure and epic update. Every final write requires its explicit
allowlist entry; task write authority does not transfer to final delivery.
The final real-input demo and its SHA-bound owner confirmation are never
fabricated. Synthetic E2E, process namespace tests and local gates do not
establish successful production GitHub writes or a real-input demo.

Legacy ADR-251 planning and ADR-252 delivery require task
`base_sha == mandate.policy_sha`. After a
task merge the roadmap base advances, while the approved policy revision
must not silently change. A synthetic request with the new base and the same
mandate fails `MANDATE_IDENTITY_MISMATCH`. Decoupling immutable trusted policy
provenance from moving delivery base identity is specified in the owner-approved
[policy-binding amendment](ADR-274-amendment-policy-binding.md). Its pure
versioned binding and migration primitive does not activate moving-base
authority. The new consumers require mandate `3.0`, plan/handover `2.0`,
checkpoint/delivery request `3.0` and policy-bound review receipt `2.0`.
Legacy consumers retain the equality check. Pure migration invalidates the
old mandate approval and refuses checkpoint migration with a pending intent;
it does not persist or activate owner files. Fresh owner approval and a
reviewed policy deployment are required before using the new versions.

Existing checkpoint `1.0` remains unchanged. Explicit pure migration preserves
its real PR and identity; it cannot turn an existing task into a new bootstrap.
Bootstrap checkpoint `2.0` belongs to the legacy one-task profile. Whole-epic
bootstrap `3.0` must come from the approved planner and pinned policy contract.
Local tests do not establish production readiness, GitHub CI or real demo success.

## Explicit partial-bootstrap recovery (ADR-284)

This is a separately approved one-task recovery profile, not automatic resume
of an old stopped epic. Keep the original state available and unchanged.
Review/install the corrective policy first and obtain a new mandate pinned to
it. An old review receipt cannot be relabelled with the new policy SHA: any
explicit import requires fresh trusted review of the preserved exact snapshot
under the new policy. There is no automatic pending-checkpoint migration.

Only after owner review of the exact private recovery candidate, use
`runtime-task.json` version `2.0`: retain all `1.0` fields and add exactly
`approved_recovery_digest`, either null (no write approval) or the separately
approved canonical SHA-256 digest of `bootstrap-recovery-approval.json`.
Approval version `1.0` has exactly the fields listed in ADR-284, enforced by
the installed recovery module. These private state contracts are not model
output schemas in `docs/schemas`.
Use `agent_epic_recovery.checkpoint_digest` on the original checkpoint and
`artifact_digest` on the existing trusted artifact. The first digest excludes
only status/pending phase; the second excludes only final delivery identity.
Bind the actual mandate digest, policy SHA, owner, current stop generation,
issue identity, issued/expiry times and budgets keyed by the three exact
bootstrap operation IDs. IDs use the existing canonical BootstrapTask
identity algorithm. Budgets are integers 1–3; already confirmed operations
consume no attempt. Computing these values does not approve them.

Runtime owns `bootstrap-recovery.json` version `1.0` and its separate lock.
Absent state starts stopped at generation zero. Never reset this file, delete
attempts or edit ledger receipts. To stop explicitly, the owner/operator holds
`Control.lock()` and calls `Control.stop()`; an active write's final guards
still refresh this stop state. UNKNOWN or unfinished reservation advances the
stop generation and invalidates the old approval. A successful read-only
probe cannot reopen it. A new owner approval must bind the resulting exact
generation and evidence; neither a model nor a checkpoint can supply it.

Use the installed one-task supervisor and existing owner-only namespace
adapter. Recovery refuses missing reviewed artifacts, stale local snapshot,
noncanonical receipts, duplicate PRs, changed authority and exhausted budgets.
It preserves commit/push/PR receipts and resumes only proven absent operations.
Canonical reconciliation can confirm a complete PR binding after artifact-save
interruption, but a partial phase is never returned as whole-phase NOT_APPLIED.
After RUN_TASK completes, this profile returns for owner review before PR_CI;
it cannot merge/close tasks or invoke whole-epic continuation. Follow-on runtime
deployment/transition requires a separately reviewed state procedure; do not
clear the recovery marker to bypass the guard. No live #131 resumption is
authorized by this implementation or by the synthetic tests.

## Bounded task-runtime GitHub reads (ADR-290)

The concrete one-task runtime retries individual issue/ref/check and GitHub
reconciliation read commands only for positively classified transient transport
failures. See [ADR-290](ADR-290-bounded-github-read-retries.md) for the exact
schedule: at most eleven attempts with 58 minutes of scheduled waiting,
excluding request durations. Writes, executor/controller runs, local Git and
network `git ls-remote` retain their existing behavior; standalone discovery,
session/final factories and unsupported adapters do not opt into this runner.
An attempt means one complete read command, including its existing pagination;
this is not a bound on individual HTTP page requests inside GitHub CLI.

The existing outer adapter timeout remains authoritative. A deadline shorter
than the sequence stops it early. Even one exhausted read can take up to
80 minutes with eleven 120-second requests; a phase with several reads needs
its own bounded deadline. Do not extend mandate/recovery expiry to fit retries.
Local authority, identity, recovery generation and pinned approval checks run
during waiting and before another read. Later writes still need fresh guards.
Success cannot clear a stopped recovery state or grant write authority.

Interrupt the running process to cancel an active wait; the existing namespace
adapter terminates its child process tree on timeout. For owner stop during
recovery, cancel the running process first, then use the existing locked
`Control.stop()` procedure above: recovery holds that lock across bootstrap,
so a second operator must not bypass the lock to edit state. A stop already
recorded or a revocation prevents further retries. Valid runtime error envelopes
preserve their sanitized original machine code; malformed envelopes remain
invalid. This code is not installed or activated by a task PR, and #131 remains
stopped until separately reviewed owner deployment/recovery decisions.

## Managed chat entry (ADR-297)

The repo-local `pgw-epic` skill calls the owner-installed frozen runtime entry.
Its new internal `entry` command accepts explicit discover/prepare/start/status/
pause/stop/resume actions. Follow [ADR-297](ADR-297-chat-entry-supervisor.md);
this section does not install or activate policy. Check the launcher bytes
against the reviewed pinned Git object before invocation. Mutable checkout
code is not a launch source.

Register runs only in an explicitly selected owner-only JSON file outside
worktrees: closed registry `1.0` has `schema_version` and `runs`; each run has
exactly `repository`, positive integer `epic`, and absolute `state_directory`.
There is no automatic home-directory search. Missing/duplicate matches,
noncanonical remote or ambiguous/stale roadmap stop discovery. State and
registry paths are private and must not appear in public reports.

A new managed launch uses closed `epic-runtime.json` `2.0`: existing whole-epic
`1.0` fields plus `approved_launch_digest`. Draft preparation may leave that
field null. Existing owner-approved mandate, plans, handover, reviewed seed
checkpoint and runtime-adapter configuration remain prerequisites. Missing
mandate/config produces a prerequisite blocker rather than self-issued
approval. Recovery stays one-task-only; no pending legacy state is migrated.

`entry --action prepare` additionally takes explicit `--installation` and
writes a closed private launch candidate `1.0` without activating it. The
owner reviews its actual file and digest, then separately pins the exact
approved digest in managed config. The candidate binds repository/epic,
roadmap, policy SHA, installation manifest digest, owner, mandate digest and
both runtime configs; only the circular `approved_launch_digest` is excluded
from the runtime config digest. Every adapter argv, timeout and output limit
is bound. Existing active/control state is not overwritten by preparation.

Discover/start/resume take repository, epic and registry; every action takes
state and repository root through the frozen entry. Start is for a new launch;
resume is for the same existing run after fresh readiness/lifecycle and
read-only reconciliation. The entry lock prevents competing entry runners;
the entry also acquires the canonical session lock before validation,
reconciliation or control writes and holds it through bounded child completion.
The installed Bubblewrap supervisor inherits the locked descriptor and verifies
its canonical private inode and exact lock ownership. It consumes the internal
marker and prevents descriptor inheritance into effect/model subprocesses;
closing its duplicate does not unlock the parent-held lock. Direct invocation
uses that same lock, so no competing supervisor can enter during launch. UNKNOWN
pending state is preserved and blocks another supervisor. Execution uses the
existing bounded Bubblewrap epic adapter and its fixed approved argv.

Owner pause/stop uses a separate short control lock and closed
`launch-control.json` `1.0` with `schema_version`, `launch_digest`, `state` and
nonnegative monotonic `generation`. Resume cannot clear a newer owner request
observed during validation. PAUSED prohibits new effects; only explicit valid
resume can reopen it. STOPPED is terminal for that launch, and start/resume,
legacy config downgrade or direct child-task entry cannot clear it. Read-only
reconciliation does not renew authority or reset pending/attempts/receipts.
Already in-flight effects may have applied; no rollback, cross-file transaction,
instant cancellation or additional crash durability is promised.

The frozen entry requires a valid mandate lifecycle for every action, including
status, pause and stop. Expiry/revocation therefore blocks those requests too;
a rejected request does not establish a new control state. Use existing
read-only reconciliation under its own guards and seek owner disposition of
expired/revoked authority before continuation.

Status returns bounded phase/task/control identifiers and canonical issue/PR
links. Process-lock occupancy or exit zero alone does not establish verified
progress/completion. The mechanism is foreground execution and explicit later
resume from persisted state. `--background` is blocked: cross-turn wakeup and
persistent waits require later separately tested mechanisms (#299/#300).
The skill has no mandate-issuance or installation authority; #131 remains
outside this deployment. Model reasoning escalation is operational policy,
not automatic permission to change runtime settings or consume new attempts.
