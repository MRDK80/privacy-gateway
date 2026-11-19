# Threat model and technical security policy

[English](SECURITY.md) | [Русский](SECURITY.ru.md)

This document describes Privacy Gateway's threat model, disk artifacts, key
handling and vulnerability reporting. It reduces accidental disclosure risk;
pseudonymization is reversible and is not guaranteed anonymization.

<!-- pgw-section: security-boundary -->
## Risk reduction

Implemented regex/dictionary detection and independent validation address supported
email/telephone/address/name patterns, file/resource paths, keyword-pattern
secrets, connection strings, PEM headers, URL endpoints, amounts and dates.
The independent validator also checks IPv6 and high-entropy strings (minimum 16
characters, Shannon threshold 3.0); this does not mean the diagnostic detector
has a general IPv6 or entropy entity type. See [detection limitations](detection.md).
False negatives remain possible; no complete detection guarantee is made.

Detected values selected for tokenization are encrypted locally with Fernet
(AES-128-CBC + HMAC-SHA256) and stored encrypted in the manifest. Operational
manifest/route/report fields do not contain the source values in plaintext.
`restore` deliberately returns original values: its result is sensitive plaintext.

## Non-guarantees

The system does not prevent multiline/unsupported secrets, passwords outside
keyword patterns, indirect identification from combinations of fields, semantic
inference from context, malicious disclosure by a keyring user, compromise of
restored plaintext, coordinated replacement of route and manifest, or compromised
infrastructure/account/process. It is not a legal compliance mechanism for GDPR,
152-FZ or another law. Ordinary OS-account access and raw backend compromise are
outside the baseline trust assumptions, not mitigated by reversible tokens.

## Trust boundaries

| Component/input | Trust level | Control |
| --- | --- | --- |
| Source text | Untrusted | Detector and independent validator |
| YAML | Untrusted | safe loading and routing-schema validation, fail closed |
| External response | Untrusted | Token classification and strict mode |
| Route | Untrusted | Version/required-field checks |
| Manifest | Untrusted until checked | Pair checksum and authenticated Fernet records |
| Keyring backend | Untrusted until checked | Full-class-name allowlist |
| Restored text | Sensitive plaintext | Consumer controls destination/access |
| Reports/errors | Potentially logged | No original/restored values |

Only the intended protected text may cross the external provider boundary. Never
export an entire PreparedPayload, restoration context, routes, manifests, key
material or restored output. Protected text can still carry unsupported or
contextually identifying data; inspect the documented limitations.

## Data on disk

| Artifact | Content | Handling |
| --- | --- | --- |
| `prompt.txt` | Tokenized text, with supported selected values replaced | Residual undetected/context data may remain |
| `route.json` | Run metadata, manifest path/checksum and counts | Keep local; metadata is not a provider payload |
| `manifest.json` | token/type/fingerprint/encrypted-value records | Keep local and protect together with access to keys |
| Restore `--out` file | Restored source values | Sensitive plaintext |
| System keyring | Fernet key encoded through latin-1 | OS-protected access required |

Source values, decrypted records and keys do not belong in route/manifest reports
or repository files. A requested restore-output file is the deliberate plaintext
exception and is not safe to publish, log or commit. Manifest creation requests
owner-only permissions (`rw-------`); on Windows rely on appropriate NTFS access
controls rather than a POSIX chmod guarantee.

## Routing configuration

Routing YAML uses only `yaml.safe_load` (ADR-10). Unknown top-level/rules keys
raise `ConfigurationError`. Configuration cannot disable secret checking:
`tokenize` and `block_unconditionally` may not intersect (ADR-11). An absent
routing config uses safe defaults, not an error. This is not a blanket statement
that every configuration loader has the routing schema.

## Artifact-pair integrity

Route metadata contains `format_version`, `status`, `timestamp`, `source_ref`,
manifest path, `manifest_sha256` for 1.1 and counts, not source values, Fernet keys,
salts, decrypted fragments or the entire input. Version 1.1 hashes all bytes of the
final manifest after its atomic publication, then publishes the route atomically.
The checksum detects mismatches/corruption. It is not authentication, a MAC or a
signature: someone replacing both files can recompute it. Fernet authenticates
encrypted records separately. Legacy route 1.0 remains readable without this
checksum; 1.1 requires it. Unknown versions, missing manifest and mismatches fail
without disclosing manifest contents.

## Restore validation order

1. Read/parse route and check version.
2. Select the actual manifest path.
3. Run `verify_manifest_integrity` on that path.
4. Only after success, access keyring.
5. Load/decrypt manifest.
6. Classify response tokens.
7. Substitute known values and build the report.
8. Publish output only after successful completion.

Integrity verification precedes `get_key`, manifest loading/decryption and record
processing. Integrity `ConfigurationError` gives exit 3; unavailable/missing key
gives exit 4. Explicit `--manifest` overrides the route path, and checks apply to
that actual selection. Absolute route paths are used as-is; relative paths resolve
against the route's directory, not cwd.

## Untrusted responses

A model may alter case/syntax, remove, duplicate or invent tokens. Token-looking
text is not proof of validity. Classification is case-sensitive: `[EMAIL_1]` and
`[email_1]` differ; do not normalize away an alteration.

| Candidate | Policy |
| --- | --- |
| Known | Substitute the value |
| Unknown | Strict rejection; unchanged in lenient mode |
| Malformed | Strict rejection; unchanged in lenient mode |
| Missing | Report missing; not an error by itself |
| Known duplicate | Substitute each occurrence; record the anomaly in the internal/CLI report |

A missing value is not inserted elsewhere: legitimate summarization may omit it.
Strict mode is default and unknown/malformed tokens cause exit 5 with no result
file (ADR-21, ADR-47). `--lenient` explicitly accepts partial restoration with
warnings; it never makes a response trusted. Public RestoredPayload exposes counts,
not all internal candidate/anomaly lists; see the [library contract](LIBRARY_API.md).

## Restore reporting

Operational reports can contain expected/found counts, token names, missing/
unknown/malformed/duplicate candidates, mode and warnings. They must not contain
source/restored values, keys, decrypted manifest contents, complete plaintext or
sensitive exception values. Restored text is separate (stdout or `--out`), while
the CLI human report goes to stderr. JSON mode follows ADR-150 and requires an
output file rather than putting sensitive restored text in an envelope.

## Restore output publication

Restoration and validation complete in memory before file output begins. A temp
file in the destination directory is followed by `os.replace`. An existing target
without `--overwrite` causes refusal. Input/integrity/keyring/decryption/strict-token
failure does not create a result. Temporary plaintext cleanup is best effort on
write/flush/close/fdopen/replace failures. If unlink itself fails, plaintext may
remain (#44/#71); cleanup failure does not hide the original error or change an
existing destination. Atomic visibility avoids partially published plaintext,
not access-control obligations or crash-durability guarantees.

## Key handling and rotation

Keys reside in SecretService (Linux), macOS Keychain or Windows Credential Vault;
backend selection is checked against the allowlist. Never log keys or put them in
repr/str or repository files. OS-account permissions govern keyring access;
Privacy Gateway does not configure them. After compromise, rotate and re-encrypt
or destroy affected manifests; rotation alone does not erase old ciphertext.

`pgw key rotate` creates a new active key and carries the previous key as retired
(ADR-22/ADR-23). Restore can read the previous generation without manual
re-encryption. Only one retired key is supported. After two rotations, the oldest
manifests cannot be read through the normal API (`DecryptionError`).

Rotation is not a keyring transaction (ADR-46/#66). Ordered operations are carry
old active to history, write new active, read-back verification, prune history to
one previous key. Carry/active-write failure leaves the active generation unchanged
under the documented backend semantics. Verification happens after the new write:
a failure can leave a new active and two physical retired keys, without rollback.
Prune failure follows confirmed active change and leaves extra physical material.
Both failures report `KeystoreError`/exit 4, not silent success. A later explicit
successful rotation restores the bounded layout. There is no cross-process lock;
concurrent rotation can be last-writer-wins.

Only create/status/rotate exist in CLI (ADR-47); there is no CLI deletion command.
Internal `keystore.delete_key()` irreversibly removes active/retired keys; it is
not a public library API or CLI command and has no `--yes` requirement.

For full independence from an old key: load the old manifest, decrypt in a trusted
local environment, encrypt with the new key, atomically save the new manifest and
recompute the linked route's `manifest_sha256`.

## Retired-key retention

API retention and physical retention differ (ADR-46/#66). `get_all_keys()` returns
at most two distinct keys `[active, retired]` immediately, regardless of stored
history. Old manifests beyond that window become unreadable through the normal
API. Physical history shrinks to two keys only after an explicit successful
rotation with completed prune. Before that, legacy `fernet_key_retired` or a
partial failure may retain more material accessible to a raw-backend reader.
Verification failure can leave new active plus two physical retired keys; this
is not rollback. Prune failure reports code 4 and can leave deeper history.
The active key/current generation is retained under the documented failure model;
incomplete calls must be investigated using actual backend state.

Reads, key status, restore and imports do not delete key material. Truncating the
API result is not deletion from storage. Backend credential-entry deletion is
not forensic media erasure; no backup/export or deep-history rollback is promised.

## Fail-closed decisions

Disallowed backend gives `KeystoreError`; corrupt ciphertext gives
`DecryptionError`; residual detected PII gives `BLOCKED`; an unknown preparation
token type gives `PENDING`; unknown routing keys/unsafe YAML/integrity failure
are rejected; strict unknown/malformed restoration tokens are rejected. A false
alarm/manual check is preferable to a false OK causing disclosure or incorrect
restoration. `PENDING` is not permissive approval.

## Library integration

The public API preserves the pipeline threat model with additional surfaces:

**Workspace.** Each preparation has its own owner-only temporary subdirectory or
subdirectory of configured `workspace_dir`, holding the same sensitive artifacts.

**Context token.** `RestoreContext.to_token()` contains references, format version
and an authentication code, not plaintext/keys/ciphertext/manifest contents. It
points to protected artifacts and needs appropriate access control. HMAC-SHA256
uses an ownership secret in owner-only workspace `.pgw-owner`, removed with it.
The token is untrusted input: its fields do not themselves grant deletion rights.
Version 1 tokens from 0.4.0 are no longer accepted (ADR-34).

**Restored result.** `RestoredPayload.text` contains source values. The library
returns it without writing it to disk; the caller protects this sensitive plaintext.

**Cleanup.** Call `gateway.discard(context)` explicitly. There is no automatic
deletion because the external response may be delayed. `keep_artifacts=True`
leaves removal entirely to the consumer. A forged/modified token alone cannot
authorize recursive deletion; an unverified nonempty tree is not traversed.
A Linux descriptor or Windows handle pins the root; traversal starts from that
object and child names resolve relative to pinned parents. If pinning cannot be
proved, fail closed with `RestoreError`; a safe `.pgw-quarantine-*` residual may
remain. Cleanup does not access keyring, so rotation/deletion of keys does not
prevent resource release.

The attacker may control serialized context fields/paths/ownership flag/correlation
metadata and replace parent-directory namespace entries within OS permissions,
but may not read/modify owned 0700 workspace contents, process memory or owner
secret. A same-identity process with direct workspace/.pgw-owner access, an actor
already able to rewrite/delete targets through OS rights, compromised memory or
owner secret, and child mutation by a direct workspace writer are outside this
model. Separate OS identity/sandbox/system boundary is needed against those
actors. ADR-34 amendments #52/#54 describe platform residuals and proof limits.
An untrusted-writable workspace base remains a risk; use a dedicated workspace.

**Reporting.** Public models/errors expose counts, not token names, manifest
contents, keys or plaintext to the external consumer. The application must still
not export its prepared payload/context or restored text.

## Repository and synthetic fixtures

Never commit real data, restored files, real manifests or temporary keys. Use
synthetic values such as `user@example.com`, `192.0.2.10`, `2001:db8::1`,
`+7 900 000-00-00`. Findings for approved synthetic examples use an inline
`# pragma: allowlist secret`; do not change `.secrets.baseline` to add fixtures.

## Reporting vulnerabilities

Report vulnerabilities privately through [GitHub private vulnerability reporting](https://github.com/MRDK80/privacy-gateway/security/advisories/new). Do not include sensitive details in public issues.

## Manifest publication

Manifest publication provides atomic visibility (#45/ADR-45). A temp file in the
destination directory is published with `os.replace` when overwriting, or POSIX
`os.link` / Windows `os.rename` without fallback when not overwriting (#64/ADR-64).
Failure before successful publication preserves an existing manifest byte-for-byte
and does not expose partial ciphertext under its final name. Temp files request
owner-only permissions and are removed best effort.

This is not crash durability: file/directory fsync is not performed. There is no
staging-directory rename or transaction over the entire artifact set. Without
overwrite, collision preflight across prompt/route/manifest stops before the first
write (#48/ADR-48); this best-effort preflight is not a guarantee against concurrent
writers. The individual manifest publication path supplies its own no-clobber
semantics; do not infer an atomic multi-file transaction.

<!-- Internal documentation-contract fixture; prose remains subject to review. -->
```pgw-doc-facts
{"cli.restore_strict_exit":5,"cli.usage_exit":3,"retention.api_key_limit":2,"retention.physical_bound_requires_successful_prune":true,"rotation.transactional":false}
```
