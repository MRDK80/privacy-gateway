# Privacy Gateway

[English](README.md) | [Русский](README.ru.md)

A local CLI and Python library for preparing corporate text before manually
sending it to an external language model. It detects supported PII and secrets,
replaces values with tokens such as `[TYPE_N]`, encrypts the mapping, validates
the protected text, and restores original values in the model's response locally.

## Status

The local detection, pseudonymization, artifact preparation, independent
validation, strict response restoration and key-management stages are implemented,
along with a stable library API. Package metadata declares
`Development Status :: 3 - Alpha`. See the [changelog](CHANGELOG.md).

This English entry point is part of an ongoing documentation migration. Linked
guides may still be in Russian. CLI text follows the approved English default;
the [language policy](docs/LANGUAGE_POLICY.md)
explains the migration scope. A link does not imply that its target is translated.

<!-- pgw-section: protection-boundary -->
## Protection boundary

Privacy Gateway performs pseudonymization, not guaranteed anonymization.

What it provides:

- Original values are encrypted locally; `manifest.json`, `route.json` and
  operational reports do not contain those values in plaintext.
- The external model's response is untrusted input: `restore` classifies tokens
  and uses strict mode by default.
- The restoration context, manifest and key material stay local. Send only the
  protected text intended for the external consumer, never the entire prepared
  payload.
- `manifest_sha256` in `route.json` binds the artifact pair and detects mismatches.

What it does not provide:

- Detection is limited to implemented regular expressions and dictionaries;
  unknown data classes may remain undetected.
- Multiline secrets and passwords outside keyword patterns are not recognized.
- Indirect identification from combinations of pseudonymized fields is not prevented.
- Unsigned SHA-256 does not protect against coordinated replacement of both artifacts.
- The result of `restore` is plaintext and needs the same protection as the source.

See the [security model](docs/SECURITY.md),
[threat-model overview](docs/threat-model.md), and
[supported entities and detection limits](docs/detection.md).

## Requirements

- Python 3.11 or newer for installation; supported and tested minor versions
  are 3.11 and 3.12 on Linux and Windows.
- Linux: `gnome-keyring` or another SecretService-compatible daemon.
- macOS: Keychain (the backend is supported; macOS is not part of the CI matrix).
- Windows: Windows Credential Vault.

The encryption key is stored in the system keyring. Headless environments need
an active keyring session (`dbus-run-session` and `gnome-keyring-daemon`);
see the [security guide](docs/SECURITY.md).

The `keyring` library selects the backend. Privacy Gateway calls
`keyring.get_keyring()` and checks the selected class against an allowlist of
system backends before accessing keys. Privacy Gateway does not support a
`PGW_KEYRING_BACKEND` override.

<!-- pgw-section: installation -->
## Installation

For the CLI, use pipx with Python 3.11 or 3.12. Until a separately approved PyPI
publication, install the verified source commit rather than a same-named package
from an index. If pipx is already installed, on Linux with `python3.12` available:

```bash
pipx install --python python3.12 "git+https://github.com/MRDK80/privacy-gateway.git@dd0f28ffb37a95f20e92f73df44047527e333843"
pgw --help
pgw doctor
```

If `pgw` is not on PATH, use the executable path shown by pipx. The
[installation guide](docs/INSTALLATION.md) provides separate Linux and Windows
PowerShell commands without shell-profile changes, pipx bootstrap, library
installation through venv/pip, updates, uninstalling and safe diagnostics.

For development and quality gates, see [CONTRIBUTING.md](CONTRIBUTING.md).
The [dependency contract](docs/DEPENDENCIES.md) describes the sole `dev` extra.
There are no product NER, proxy or provider extras.

## First run

The example below needs a separate repository checkout containing `examples`;
pipx and wheel installations do not install repository examples. Use the
[installation guide](docs/INSTALLATION.md) to choose CLI and library environments.
Run the key command with the installed `pgw`, and the Python example with the
interpreter of a venv containing the library.

```bash
pgw key create
python examples/01_quickstart.py
```

`pgw key create` creates the active key in the system keyring. Without a key,
the example fails with a key-store error: this is expected fail-closed behavior.
The example runs `prepare` → a local deterministic response → `restore` →
`discard`, uses only synthetic data and makes no network requests.

Expected output:

```text
tokens_prepared=<N>
tokens_restored=<N>
tokens_missing=0
protected_leak_free=True
roundtrip_exact=True
workspace_clean=True
```

## CLI

Use `pgw --describe` as the source of current command and parameter syntax;
`pgw --help` and individual command help provide interactive guidance.
The key-management operations are `create`, `status` and `rotate` under `pgw key`.

A minimal end-to-end flow:

```bash
pgw prepare input.txt --out ./pgw_out
# Send only ./pgw_out/prompt.txt to the external model.
# Save the response as llm_reply.txt.
pgw restore llm_reply.txt --route ./pgw_out/route.json --out restored.txt
```

Strict mode is the default: an unknown or malformed token causes exit code 5,
and no result file is created. Lenient mode requires explicit `--lenient`;
it does not make the model's response trustworthy.

See the [CLI round-trip example](examples/06_cli_round_trip.md) for verified
flags, exit codes and Bash/PowerShell differences, the
[key-rotation example](examples/05_key_rotation.md), and the
[architecture guide](docs/ARCHITECTURE.md) for exit codes, restoration modes
and token classification. `pgw detect` reports detection metadata without
original values; it is not permission to send source text.

<!-- pgw-section: library -->
## Library usage

The following sketch assumes a configured keyring and an existing key. `text`
and `external_processor` are supplied by the application:

```python
from privacy_gateway import GatewayConfig, PrivacyGateway

gateway = PrivacyGateway(GatewayConfig())

prepared = gateway.prepare(text, correlation_id="req-1")
response_text = external_processor(prepared.text)
restored = gateway.restore(response_text, context=prepared.context)

gateway.discard(prepared.context)
```

`prepare` returns protected text and an opaque restoration context. `restore`
checks protected-artifact integrity before returning plaintext. The consumer
calls `discard` to remove artifacts. The [library contract](docs/LIBRARY_API.md)
defines the lifecycle and exceptions; the [token-format guide](docs/token-format.md)
defines token syntax. The sketch is not a guarantee that cleanup succeeds after
an application failure; cleanup and other limits are documented in the contract.

## Examples

The [examples index](examples/README.md) contains reproducible scenarios and
prerequisites: a minimal round trip, a full flow with several entity types,
secret blocking, an untrusted model response, key rotation and a CLI round trip.

<!-- pgw-section: limitations -->
## Known limitations

- Rotation retains one retired key (ADR-23). After a second rotation, manifests
  from the oldest generation can no longer be read.
- Tokens are not stable across runs: numbering is per document, so `[EMAIL_1]`
  can refer to different values in different runs.
- Detection diagnostics do not authorize source-text disclosure.
- `PENDING` is not a permissive `OK`; it requires manual approval.
- TOCTOU protection during overwrite needs separate test and implementation-audit
  evidence; this README makes no blanket guarantee.

## Development

[CONTRIBUTING.md](CONTRIBUTING.md) defines the mandatory local quality gate,
branching model, Python-version policy and reporting requirements.

Do not add real corporate data to the repository. Examples use synthetic values
such as `user@example.com`, `192.0.2.10`, `2001:db8::1` and `+7 900 000-00-00`.
See the [repository security policy](SECURITY.md) for vulnerability reporting
and the [technical security model](docs/SECURITY.md).

## Documentation

| Document | Contents |
| --- | --- |
| [Architecture](docs/ARCHITECTURE.md) | Pipeline, data model, artifact formats and exit codes |
| [Library API](docs/LIBRARY_API.md) | Public API, context lifecycle and exceptions |
| [Security](docs/SECURITY.md) | Threat model, trust boundaries and key handling |
| [Detection](docs/detection.md) | Supported entities and detector limitations |
| [Token format](docs/token-format.md) | Syntax, numbering and restoration behavior |
| [Decisions](docs/DECISIONS.md) | Architectural decision record |
| [Examples](examples/README.md) | Reproducible examples index |
| [Contributing](CONTRIBUTING.md) | Definition of Done, quality gate and branching model |
| [Language policy](docs/LANGUAGE_POLICY.md) | Canonical languages and migration/synchronization rules |

## License

MIT — see [LICENSE](LICENSE).
