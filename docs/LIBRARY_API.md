# Privacy Gateway library API

This is the public integration contract, independent of the CLI. Use public
imports rather than internal modules. Executable quickstart, round-trip,
secret-blocking, untrusted-response and CLI/rotation scenarios are in the
[examples index](../examples/README.md).

Per the [language policy](LANGUAGE_POLICY.md), the canonical API reference and
public docstrings are English-only; no Russian API mirror is required. Main
Russian user journeys remain available through the bilingual examples guides.

## Public imports

```python
from privacy_gateway import (
    GatewayConfig,
    PreparedPayload,
    PrivacyGateway,
    PrivacyGatewayError,
    RestoreContext,
    RestoredPayload,
)
```

The package includes `py.typed`. Names outside top-level `__all__` are internal
and may change without notice. All supported exports are the six types above,
`ConfigurationError`, `DetectionError`, `IntegrityError`, `KeyStoreError`,
`RestoreError`, `StrictTokenError` and `__version__`. `__version__` is the package
version string, consistent with installed distribution metadata; importing it
is not an installation-readiness check.

## Lifecycle

The following sketch assumes an available allowed keyring with an existing key.
`text`, `correlation_id` and `external_processor` come from the consumer:

```python
gateway = PrivacyGateway(GatewayConfig())

prepared = gateway.prepare(text, correlation_id=correlation_id)

response_text = external_processor(prepared.text)

restored = gateway.restore(response_text, context=prepared.context)

gateway.discard(prepared.context)
```

The library does not invoke an external provider. Only the intended protected
`prepared.text` crosses that boundary, never the payload/context, manifest, route,
key material or restored text. Pseudonymization is reversible, not anonymity;
[detector limitations](detection.md) still apply. Arrange explicit `discard` in
application error handling (for example `finally`); the sketch does not guarantee
cleanup after exceptions. There is no `PrivacyGateway` context-manager API.

## Immutable models

| Model | Fields | Purpose |
| --- | --- | --- |
| `GatewayConfig` | routing_config_path, entities_config_path, workspace_dir, strict, keep_artifacts | Instance configuration |
| `PreparedPayload` | text, context, correlation_id, token_count | Protected text and local restoration context |
| `RestoredPayload` | text, correlation_id, tokens_restored, tokens_missing | Sensitive restored text and counts |
| `RestoreContext` | No public data fields | Opaque reference to protected artifacts |

Their repr does not reveal text, source values, token names or protected-artifact
contents. Correlation identifiers are consumer input: do not put sensitive values
in them. No public candidate list, duplicate counter or warning list is added by
this reference.

## Restoration context

- **Opaque:** no plaintext, keys, ciphertext, manifest contents or internal
  detector/tokenizer structures are exposed as public data fields.
- **Serialization:** `context.to_token()` and `RestoreContext.from_token(token)`
  transfer references, format version and authentication code. Current format 2;
  version 1 from 0.4.0 is rejected with `RestoreError` (ADR-34). Parsing alone does
  not authenticate authority; checks precede the operation relying on the token.
- **Restart:** context works while the protected artifacts remain and the required
  key is available. Resource release does not require a key.
- **Reuse:** repeated restore does not change artifacts and is idempotent for the
  same inputs and relevant environment.
- **Explicit removal:** the consumer calls `gateway.discard(context)`; delayed
  external responses prevent automatic deletion. With `keep_artifacts=True`,
  artifact removal remains entirely the consumer's responsibility.
- **Ownership:** valid owned context can remove its workspace. Modified/foreign/
  out-of-base context raises `RestoreError` without granting recursive-deletion
  authority. A repeated call after removal is idempotent and does not raise.
- **Pinned traversal:** a descriptor/handle pins the workspace; traversal begins
  from that object and child names resolve relative to pinned parents. Unproven
  pinning fails closed with `RestoreError`; `.pgw-quarantine-*` may remain.
- **Threat assumptions:** this protects against forged serialized context and
  parent-namespace replacement within the accepted model, not a same-OS-identity
  actor with direct workspace/.pgw-owner access or OS authority to delete/rewrite
  the target. Error messages exclude owner secrets, artifact contents and paths.
- **Isolation:** each preparation has its own work subdirectory.

Protect tokens and artifacts like the source document; do not send context to the
provider. See [security](SECURITY.md) and ADR-34 amendments for residual limits.

## Workspace and threads

With `workspace_dir=None`, the library creates/owns an owner-only temporary
workspace. With an explicit base it creates a separate child for each preparation.
Protect access to that base and its artifacts. Explicit discard manages lifecycle;
there is no implicit automatic deletion after an external response.

One PrivacyGateway instance may be used by multiple threads: it retains no mutable
operation state between calls, reloads routing per call, requests keys for each
operation and uses disjoint workspaces. Frozen models can be passed between
threads. This is not a cross-process keyring-rotation transaction guarantee.

## Strict mode and fail closed

- `prepare` returns no protected payload when a secret, blocking rule or failed
  independent validation rejects the result; it raises `DetectionError` and invokes
  artifact cleanup under the documented cleanup limits.
- `restore` verifies protected-artifact integrity before returning plaintext;
  integrity failure raises `IntegrityError`.
- Default `strict=True` rejects unknown/malformed response tokens with
  `StrictTokenError`, returning no plaintext.
- Only explicit `GatewayConfig(strict=False)` enables lenient restoration,
  leaving unacceptable candidates unchanged.
- A missing response token is not an error by itself; it increments `tokens_missing`.

## Public exceptions

| Class | Meaning |
| --- | --- |
| `PrivacyGatewayError` | Base public library error category |
| `ConfigurationError` | Invalid configuration or unavailable workspace |
| `DetectionError` | Fail-closed preparation; neutral decision `status` attribute |
| `KeyStoreError` | Missing key, unavailable store or disallowed backend |
| `IntegrityError` | Protected-artifact integrity/authentication failure |
| `RestoreError` | Invalid context or restoration cannot complete |
| `StrictTokenError` | RestoreError subclass for unacceptable response tokens |

Causes use `raise ... from exc`. Public messages exclude plaintext, key material,
ciphertext, token names and manifest contents. This does not authorize exporting
an arbitrary traceback, internal exception cause or application diagnostics.

## Consumer error handling

`text` and the `handle_*` functions below are application-provided placeholders:

```python
from privacy_gateway import (
    DetectionError,
    IntegrityError,
    KeyStoreError,
    PrivacyGateway,
    PrivacyGatewayError,
    StrictTokenError,
)

gateway = PrivacyGateway()

try:
    prepared = gateway.prepare(text)
except DetectionError as exc:
    handle_blocked(exc.status)
except KeyStoreError:
    handle_key_problem()
except PrivacyGatewayError:
    handle_unexpected()
```

On restoration distinguish artifact problems (`IntegrityError`) from unacceptable
provider tokens (`StrictTokenError`). The snippet illustrates preparation errors;
if preparation succeeds, the consumer still owns the context and must release it.

## Relation to CLI

The library and CLI share internal pipelines but have independent contracts.
CLI exit codes do not apply to library calls: library failures use exceptions.
Changing documentation language does not change signatures, imports, exception
hierarchy, strictness or context formats.
