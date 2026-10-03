# ADR-274 amendment: concrete task-phase wiring

Дата: 2026-10-01. Epic: #248. Local corrective scope #274.

Production phase wiring uses the existing coordinator handover validation,
bounded workflow, minimal-file Codex executor boundary and independent
controller. The trusted adapter retains the actual full gate from the
controller request only after validating the controller response. It binds a
private review receipt to task, base, head ref and reviewed snapshot SHA.
Neither checkpoint nor model output supplies successful gate evidence.

Commit uses the exact snapshot transport in the bootstrap amendment. Push,
create PR, merge, close task and epic update go through ADR-252, with fresh
authority and live revalidation. Commands use argv, never a shell, force push
or admin/protection bypass. Unknown writes reconcile read-only before retry.
PR creation is limited to a same-repository task head and the pinned roadmap.
Epic updates use an operation marker and refuse duplicate/ambiguous outcomes.

PR_CI and POST_MERGE call the existing SHA-bound delivery assessment. DEMO
validates the owner-provided plan and current baseline/roadmap refs; it does
not execute real input or invent owner confirmation. TASK_DONE closes the task
and then updates the epic, rechecking post-merge gates for each write.

Runtime code must be installed from an owner-reviewed immutable policy
revision outside worktrees, and its commands must pass ADR-274 owner/mode
checks. This source module is not permission to import mutable task-head code
into private wrappers. It does not create credentials or approve a mandate.

Queue planning and final roadmap delivery remain separate machines. A task
runtime cannot overwrite its own active checkpoint with the next task while
the supervisor holds its lock. The one-task profile stops for the planner/final
driver. The opt-in whole-epic profile hands a terminal task to the separate
cursor and factories in the [continuation amendment](ADR-274-amendment-epic-continuation.md).
No next-task or final-gate success is fabricated. Owner-only deployment and
real consumer demo remain explicit requirements, separate from synthetic tests.
