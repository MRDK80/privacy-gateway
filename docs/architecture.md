# Architecture

## Stage E1 scope

Stage E1 establishes the project skeleton only. No data processing logic is implemented.

## Future data flow

```
input (.txt / stdin)
    │
    ▼
 prepare
    │  detects entities → replaces with tokens → writes encrypted case map
    ▼
 sanitised text / prompt
    │
    ▼
 [manual copy → external LLM — no API call made by this tool]
    │
    ▼
 model response (copy back manually)
    │
    ▼
 restore
    │  matches tokens → looks up encrypted case map → substitutes originals
    ▼
 restored text
```

## Key design constraints

- **No API call to any LLM.** The model interaction is always manual. Privacy Gateway never sends data over the network.
- **Source text is not persisted by default.** Only the encrypted token-to-value map (case map) is stored locally.
- **The case map is never sent to the model.** The model receives only the sanitised text with tokens.
- **Restore works only on exact token matches.** Distorted tokens (e.g. `[PERSON-001]`) are detected and flagged, not silently skipped.
- **All cryptography uses standard libraries** (`cryptography.Fernet`). No custom crypto.
- **Windows is the primary target OS for MVP.** Linux is supported for CI.
