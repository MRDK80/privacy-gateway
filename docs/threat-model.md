# Threat Model

Stage E1 creates a safe development skeleton and establishes security rules.
Most technical countermeasures will be implemented in later stages.

| # | Threat | Countermeasure | E1 status | Future stage |
|---|--------|---------------|-----------|-------------|
| 1 | Real data committed to Git | `.gitignore` rules; SECURITY.md policy; no real fixtures | ✅ Enforced in E1 | Maintained |
| 2 | Real data sent to LLM | No API calls; manual-only flow by design | ✅ By design in E1 | Maintained |
| 3 | Case map stored in plaintext | `cryptography.Fernet` + `keyring` planned | ⏳ Not implemented | E2 |
| 4 | Data printed to console or logs | CLI never logs input content; policy documented | ✅ Enforced in E1 | Maintained |
| 5 | Secrets in fixtures or test files | Only synthetic fixtures; `detect-secrets` in CI | ✅ Enforced in E1 | Maintained |
| 6 | Model distorts tokens on restore | Token integrity check on restore | ⏳ Not implemented | E3 |
| 7 | Wrong case map substituted (manifest swap) | Case map bound to case ID; integrity check | ⏳ Not implemented | E2 |
| 8 | Unsupported attachment passed to prepare | Input type validation | ⏳ Not implemented | E2 |

**Legend:** ✅ Active in E1 · ⏳ Planned for future stage
