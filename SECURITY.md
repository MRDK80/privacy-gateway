# Security policy

[English](SECURITY.md) | [Русский](SECURITY.ru.md)

<!-- pgw-section: security-boundary -->
## Sensitive data

- Do not publish real input text, case maps, encryption keys or encrypted manifests
  in this repository.
- Do not put real personal data, organization names, server names, IP addresses,
  financial figures or corporate identifiers into issues, pull requests, commit
  messages or CI logs.
- Do not commit `.env` files, credentials, tokens or secrets.
- Windows Credential Manager and `keyring` are only local key stores, never Git
  data sources.

## Before pushing

Run secret scanning and resolve findings before pushing:

```bash
detect-secrets scan --all-files \
  --exclude-files '(\.git/.*|\.venv/.*|\.mypy_cache/.*|\.pytest_cache/.*|\.ruff_cache/.*|.*\.egg-info/.*)'
```

## CI logs

CI uses synthetic data only. Real data must not enter logs, artifacts or reports.

## Vulnerability reporting

Report vulnerabilities privately through [GitHub private vulnerability reporting](https://github.com/MRDK80/privacy-gateway/security/advisories/new). Do not include sensitive details in public issues.

Never open a public issue containing sensitive vulnerability details. See the
[technical security model](docs/SECURITY.md) for supported guarantees and limits.

## Local test-case cleanup

Further operational guidance for removing a local case map, encrypted manifest
and work files remains future work. This is not a secure-erasure guarantee.
