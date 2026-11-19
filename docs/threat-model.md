# Threat model

[English](threat-model.md) | [Русский](threat-model.ru.md)

The current threat model and security policy have one technical source:
[SECURITY.md](SECURITY.md). The former stage-one status table was removed because
it described implemented controls as planned and diverged from that source.

<!-- pgw-section: security-boundary -->
## Navigation

| Question | Section in the security model |
| --- | --- |
| Protection and exclusions | Risk reduction; Non-guarantees |
| Trust boundaries and artifacts on disk | Trust boundaries; Data on disk |
| Pair integrity and `manifest_sha256` | Artifact-pair integrity |
| Validation order and strict restore | Restore validation order; Untrusted responses |
| External response as untrusted input | Untrusted responses |
| Safe reporting and atomic writes | Restore reporting; Restore output publication |
| Key storage and rotation | Key handling and rotation; Retired-key retention |
| YAML configuration | Routing configuration |
| Fail closed | Fail-closed decisions |
| Library integration | Library integration |
| Vulnerability reporting | Reporting vulnerabilities |

See [token syntax/classification](token-format.md) and the
[pipeline architecture](ARCHITECTURE.md). The English and Russian guides preserve
the same limits; a translation does not add security guarantees.
