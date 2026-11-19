---
name: Bug report
about: Report a reproducible issue using synthetic data only
title: ""
labels: ""
assignees: ""
---

English is preferred; Russian reports are welcome.

**Security:** do not report vulnerability details publicly. Follow
[the private-reporting policy](https://github.com/MRDK80/privacy-gateway/blob/main/SECURITY.md).
Do not attach source/restored text, manifests/routes, keys, credentials,
configuration files, environment dumps, internal hosts or sensitive tracebacks.

## Problem and expected result

Describe actual versus expected behavior without sensitive values.

## Synthetic reproduction

Provide the smallest synthetic example, commands and exit codes. Do not use real
personal or corporate data. Current syntax is available through `pgw --describe`;
do not assume a `--version` flag exists.

## Environment

- OS and Python version:
- Installed package version from distribution metadata:
- Installation method (without private index URLs or credentials):
- Optional closed `doctor` status report, without attached config/environment:

Ordinary Python imports may create bytecode caches; use the selected environment's
`python -B -m privacy_gateway doctor` to exclude those import-cache writes. This
is not a promise of zero OS/process writes or a request to access keys.

## Context

Related issue/roadmap if known, impact and any synthetic regression evidence.
An issue does not authorize implementation, security changes or a release.
