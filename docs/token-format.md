# Token Format

This document specifies the token format used by Privacy Gateway for entity replacement.

## Token syntax

```
[TYPE_NNN]
```

- `TYPE` — uppercase entity type identifier (see list below)
- `NNN` — zero-padded three-digit sequence number, starting at `001`

## Defined token types

| Token | Entity category |
|-------|-----------------|
| `[PERSON_001]` | Natural person |
| `[ROLE_001]` | Job title or role |
| `[ORG_001]` | Legal entity or organisation |
| `[DEPARTMENT_001]` | Internal department or division |
| `[EMAIL_001]` | Email address |
| `[PHONE_001]` | Phone number |
| `[HOST_001]` | Hostname or server |
| `[ENDPOINT_001]` | URL, URI, or network endpoint |
| `[RESOURCE_001]` | File system path or storage resource |
| `[SYSTEM_001]` | Internal system or application |
| `[PROJECT_001]` | Internal project name or code |
| `[AMOUNT_001]` | Monetary amount or financial figure |
| `[METRIC_001]` | KPI or performance metric |
| `[DOCUMENT_001]` | Document ID, contract number, or reference |
| `[DATE_001]` | Specific date or date range |
| `[DURATION_001]` | Time period or duration |
| `[ENVIRONMENT_001]` | Deployment environment label |

## Numbering rules

- Numbering is **per case**: each new case starts at `_001` for every type.
- One real-world object per case → one stable token. The same person always maps to the same `[PERSON_NNN]` within a case.
- Different real-world objects are **never merged** automatically, even if they appear similar.
- Reverse substitution uses **exact string matching** only.

## Error detection (future)

- Distorted tokens such as `[PERSON-001]`, `[person_001]`, or `PERSON_001` (missing brackets) will be detected and flagged during `restore`.
- Detection of distorted tokens is planned for Stage E3.

## Safety constraint

Tokens **never include any fragment of the original value**. A token such as `[PERSON_Smith]` is invalid and will be rejected.
