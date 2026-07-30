# Security Policy

## Handling sensitive data

- **Do not publish** real input texts, case maps, encryption keys, or encrypted manifests in this repository.
- **Do not write** real personal data, organisation names, server names, IP addresses, financial figures, or any corporate identifiers in issues, pull requests, commit messages, or CI logs.
- **Do not commit** `.env` files or any file containing credentials, tokens, or secrets.
- Windows Credential Manager and `keyring` are used for local key storage only — they must never be used as a source of data for Git.

## Before every push

Run secret scanning to verify no accidental secrets are staged:

```bash
detect-secrets scan --all-files --exclude-files '\.git/.*'
```

If any finding is reported, resolve it before pushing.

## CI logs

CI pipelines run only on synthetic data. Real data must never appear in CI logs, artefacts, or test reports.

## Reporting vulnerabilities

Please report vulnerabilities **privately** to the repository owner. Do not open a public issue for security findings.

## Local test case cleanup

Instructions for securely removing a local test case (case map, encrypted manifest, and working files) will be added in a future stage.
