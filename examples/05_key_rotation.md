<!-- pgw-section: side-effects -->
# 05_key_rotation — rotation and delayed restoration

[English](05_key_rotation.md) | [Русский](05_key_rotation.ru.md)

This is a command guide, not a script safe to run without consequences. Rotation
irreversibly changes the current user's system keyring.

## Why there is no executable script

- Key management is CLI-only: `pgw key create`, `pgw key status`, `pgw key rotate`.
  `privacy_gateway.keystore` is internal, not exported in `__all__`; see
  [Library API](../docs/LIBRARY_API.md).
- Only create/status/rotate exist (ADR-47/#47); no CLI key-deletion or undo exists.
- Rotation is destructive for deep history. Key backup/export is not implemented;
  rollback without the old key is impossible (ADR-46).
- Examples 01–04 require but do not change an active key. Do not run this scenario
  against a working keyring without a deliberate decision.

`tests/test_key_rotation.py` and `tests/test_key_retention_policy.py` test behavior
against an isolated fake backend, never the real system keyring.

## Before running

Restore unfinished prepared requests before rotation. After two rotations,
manifests encrypted by the oldest key are unreadable through the normal API.

## Prerequisites

```bash
pip install -e ".[dev]"
pgw key status
```

Status returns 0 if an active key exists,3 if absent. Create one if needed:

```bash
pgw key create
```

Repeated create returns 3 without overwriting. `--force` replaces active and removes
retired: it is a reset, not rotation; earlier generations become unreadable.

## Scenario

Prepare under K0:

```bash
pgw prepare request.txt --out ./work
```

Rotate to K1:

```bash
pgw key rotate
```

Restore the request prepared under K0:

```bash
pgw restore reply.txt --route ./work/route.json
```

No manual re-encryption is required. Restore tries `[active, retired]` and reads
the previous generation (ADR-23). Rotate to K2:

```bash
pgw key rotate
```

Restoring the same route now returns 3: neither available key decrypts K0.
Error messages do not disclose key material.

<!-- pgw-section: retention -->
## Generations

| State | Active | Retired | Readable generations |
| --- | --- | --- | --- |
| After key create | K0 | — | K0 |
| After first key rotate | K1 | K0 | K1, K0 |
| After second key rotate | K2 | K1 | K2, K1 |
| After third key rotate | K3 | K2 | K3, K2 |

One retired key is supported, not a chain of three or more (ADR-23, ADR-46).

## Exit codes

| Operation/condition | Code |
| --- | --- |
| Successful create/status/rotate |0 |
| Status without an active key |3 |
| Repeated create without force |3 |
| Restore beyond retention |3 |
| Rotate without active key |4 |
| Carry/active-write/verification/prune failure |4 |

Code 4 is a key-store failure, not successful partial rotation. An incomplete
rotation reports an error, never silent success (ADR-46, clarification #66).

<!-- pgw-section: failure-boundary -->
## API retention and physical retention

API retention is immediate: restore and normal consumers receive at most
`[active, retired]`. Physical retention changes only through explicit operations.
Before a first successful rotation, legacy storage may retain the accumulated
history; a prune failure may temporarily leave several keys in the retired entry.
The physical blast radius becomes two keys only after explicit successful rotation
with completed prune. A subsequent successful rotation restores the bounded layout.

Rotation is not a keyring transaction. Verification/prune failure is not rollback;
actual backend state determines the result. See the rotation section of the
[security guide](../docs/SECURITY.md).

## Non-guarantees

- No transactional atomicity or cross-process rotation lock: last-writer-wins
  is possible (ADR-46).
- No reads beyond `[active, retired]`, forensic key erasure, configurable retention
  depth or prune command.

## Fully separating from an earlier key

Rotation retains one previous generation. To remove dependence on it, follow the
security guide's re-encryption procedure: load the old manifest, decrypt in a
trusted local environment, re-encrypt with the new key, atomically save the
manifest and recompute `manifest_sha256` for the linked artifact pair.

<!-- Internal documentation-contract fixture; prose remains subject to review. -->
```pgw-doc-facts
{"retention.api_key_limit":2,"retention.physical_bound_requires_successful_prune":true,"retention.retired_key_limit":1,"rotation.failure_implies_rollback":false,"rotation.transactional":false}
```
