# Owner-only task runtime: deployment readiness

This is a local corrective implementation, not an activated production loop.
Do not run the old consumer-demo command against the current private state.

## Trusted installation

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
