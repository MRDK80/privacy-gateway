# Entity detection

[English](detection.md) | [Русский](detection.ru.md)

<!-- pgw-section: security-boundary -->
## Purpose and boundary

Detection is the diagnostic core: controlled text-input reading and deterministic
identification of potentially sensitive entities. It covers `.txt`/stdin with
encoding/size controls, regex and YAML dictionaries, metadata-only diagnostic
CLI output, synthetic unit tests and Linux/Windows support.

The `pgw detect` command has human-readable output; operational JSON mode is not
supported (ADR-150/ADR-151). Use `pgw --describe` for actual command/output-format
capabilities. Do not confuse JSON-like entity lines in a human report with a
versioned operational JSON envelope.

Other modules handle tokenization ([token format](token-format.md)), encryption/
keys/manifests, preparation and restoration. Privacy Gateway does not send text
to an external provider. Doctor's separately approved local D-Bus capability
queries are not external-provider calls; see ADR-113. See the
[pipeline architecture](ARCHITECTURE.md).

## Supported entities

### Regex detection

| Type | Pattern class | Confidence |
| --- | --- | --- |
| `EMAIL` | Email addresses | HIGH |
| `PHONE` | Russian/international telephone patterns | MEDIUM |
| `HOST` | IPv4 addresses | HIGH |
| `ENDPOINT` | HTTP/HTTPS URLs | HIGH |
| `RESOURCE` | UNC, Windows drive and POSIX paths | HIGH |
| `DATE` | YYYY-MM-DD, DD.MM.YYYY, DD/MM/YYYY | HIGH |
| `AMOUNT` | Amounts with a currency symbol/word | MEDIUM |

`HOST` detection is IPv4, not a promise to tokenize every IPv6 address. The
independent output validator has its own IPv6/entropy checks and may block
residual data the detector did not replace. Detection and validation scopes are
not interchangeable.

### Internal secret findings

Secret values are not exposed in the public entity model:

| secret_kind | Pattern examples |
| --- | --- |
| `PASSWORD` | password/passwd assignment |
| `API_TOKEN` | api_token/api_key assignment |
| `PRIVATE_KEY` | PEM private-key header |
| `CONNECTION_STRING` | Server/Database connection-string pattern |

These are pattern classes, not exhaustive recognition of secrets. Multiline or
unknown formats and passwords outside keyword patterns can be missed.

### YAML dictionary entities

`PERSON`, `ORG`, `SYSTEM`, `PROJECT`, `DEPARTMENT`, `ROLE`, `ENVIRONMENT`.
Dictionary values must be synthetic. Matching is exact case-insensitive occurrence,
not NER. `METRIC`, `DOCUMENT` and `DURATION` exist in EntityType but have no current
detectors.

## Cross-platform paths

RESOURCE patterns operate on text, not filesystem access. UNC `\\server\share\dir`,
Windows `C:\path\to\file` and POSIX `/opt/app/data/file` can be recognized regardless
of the host OS. CI uses Ubuntu/Windows; recognition of POSIX/macOS-style text is
not proof of a macOS runtime CI matrix.

## Limits and false negatives

Regex does not verify that a domain, telephone number or other object exists.
Dictionaries are a deliberately limited MVP, not NER. False positives and false
negatives are possible; diagnostic output is not a disclosure authorization.
Encoding is not guessed. POSIX patterns require at least two segments (`/a/b`)
to reduce false matches on fractions/dates. Indirect identification from context
and combinations of fields remains possible. Pseudonymization is reversible,
not guaranteed anonymization.

## Overlap resolution

Choose lower numerical priority first, then a longer match, then smaller `start`.
Do not create duplicate findings at the same position.

| Priority | Type |
| --- | --- |
|0 | PRIVATE_KEY, CONNECTION_STRING, API_TOKEN, PASSWORD |
|1 | ENDPOINT |
|2 | EMAIL |
|3 | RESOURCE |
|4 | HOST |
|5 | PHONE |
|6 | AMOUNT |
|7 | DATE |
|8 | Dictionary entities |

## Output safety

Diagnostic stdout/stderr excludes original input/entity values. `fingerprint`
is the first 12 hex characters of SHA-256 over the UTF-8 value, not encrypted data
or proof of anonymity. Models' repr/str do not expose values. Keep any diagnostic
metadata within the documented trust boundary; the external payload is only the
intended protected text, not arbitrary diagnostics.

`pgw detect` does not create `READY` and offers no legal safety guarantee. It is
technical diagnostics, not permission to send the original document. See the
[security model](SECURITY.md) for preparation, restoration and residual risks.
