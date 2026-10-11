# ADR-296: owner-only installation candidate and read-only readiness

Date: 2026-10-11. Task #296, epic #294. Owner approved this design in chat
on 2026-10-11 before production implementation.
No installation, mandate issuance or runtime activation is performed by this
document. ADR-295 design acceptance does not approve a particular installation.

## Decision candidate

Add an internal `agent_epic_install` tool with three separate operations:
prepare a pinned installation candidate, install the approved candidate and
read-only readiness. Existing runtime entry/mandate/config schemas and public
Library/pgw contracts remain unchanged.

Preparation reads Git objects at an explicit full policy SHA, never copies
mutable checkout files or imports candidate modules. It materializes exactly
the existing `tools` and `docs/schemas` inventory with owner-only modes in a
fresh directory outside all worktrees, and a byte-identical runtime entry.
It refuses Git symlinks, unsafe parents, existing unrelated directories and
unsupported hosts. A closed version `1.0` candidate binds repository identity,
policy SHA and an inventory digest. The digest proves identity, not approval.
The candidate contains no credentials, real input or signed mandate.

Installation requires a separately supplied explicit owner approval for the
exact candidate digest. No installer-generated approval, default approval or
model inference is accepted. Installation targets a fresh owner-only policy
directory; existing byte-identical installation returns NO_OP. An existing
different installation, symlink or active/pending state is never overwritten.
Staging is private, verified before publication and cleaned best-effort on
failure; failure reports a bounded code and leaves the old installation
unchanged. Atomic directory visibility does not promise crash durability.
Installation does not create runtime state, change approvals, start a process
or migrate old checkpoints. Runtime activation still uses the existing
separately pinned owner mandate/config and trusted entry guards.

Readiness reuses the existing owner/mode, policy-byte, bootstrap authority and
runtime-config guards without invoking effect, executor or candidate runtime
code. It checks configured one-task versus whole-epic schema compatibility,
mandate expiry/revocation/digest and identities, interpreter availability and
an actual bounded Bubblewrap namespace probe. GitHub probes are read-only,
verify account identity and repository access, and never expose raw output.
The result is a closed version `1.0` report with bounded check identifiers,
PASS/FAIL and safe codes; private paths and diagnostics are absent. Readiness
does not claim cross-turn wakeup, independent task review, successful delivery
or production pilot. Those capabilities belong to later tasks.

## Threat boundaries and verification

Issue/model/head cannot supply trusted installed bytes or owner approval.
Untrusted candidate manifests are checked against canonical Git inventory;
digest validation alone does not trust a candidate. No candidate Python code
is imported during preparation or readiness. Same-OS-account compromise
remains outside the base threat model. Unknown versions/fields fail closed.
Existing #131 state is never discovered, read or modified implicitly.

Synthetic tests must cover wrong policy SHA, altered inventory, symlinks and
ownership/modes, unsupported namespace, interruption before publication,
idempotent install, conflicting destination, revoked/expired mandate and
one-task versus whole-epic configs. A fake probe cannot establish actual host
readiness. Full local gate, independent controller and exact task/post-merge
CI remain mandatory. Consumer demo uses only an isolated synthetic repository
and temporary private installation; it is not a production activation.
