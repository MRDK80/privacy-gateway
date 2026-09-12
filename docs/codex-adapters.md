# Production Codex adapters

Task #181 wires `tools/agent_orchestrate.py` to a locally installed Codex CLI
through `tools/codex_adapter.py`. The orchestrator is unchanged: the adapter is
supplied with the existing `--executor-command` and `--controller-command`
flags, which already accept a JSON array.

```bash
python tools/agent_orchestrate.py 181 \
  --epic 179 \
  --base roadmap/179-codex-adapters \
  --head feat/181-codex-production-adapters \
  --criterion "production adapters implemented" \
  --allowed-path tools/codex_adapter.py \
  --executor-command '["python","tools/codex_adapter.py","--role","executor"]' \
  --controller-command '["python","tools/codex_adapter.py","--role","controller"]'
```

## Role invocation

Both roles use a fresh ephemeral session per call. Common flags are
`exec --ephemeral --ignore-user-config --ignore-rules --strict-config
--color never`, the result is collected with `-o` into a temporary file outside
the worktree, and the prompt is passed as a single argument. `--json` is not
used, because the role contract requires exactly one JSON object on stdout.

| Aspect | Executor | Controller |
|---|---|---|
| Sandbox | `workspace-write` | `read-only` |
| Working root | repository root | temporary policy bundle |
| Extra flags | none | `--skip-git-repo-check` |
| Schema | `executor-report.schema.json` | `controller-verdict.schema.json` |

The controller never runs with `-C` inside the head worktree. Codex reads
`AGENTS.md` and execpolicy `.rules` from its working root, and the head version
of those files is untrusted content produced by the executor. The adapter
materialises the `trusted_policy` mapping, which the orchestrator already reads
at `base_sha`, into a temporary bundle and runs the controller there. This is
what keeps `review_basis.head_policy_applied` equal to `false`.

## Schema handling

Canonical schemas in `docs/schemas/` are the only source of truth and are never
modified. `--output-schema` receives a derived, strictly weaker generation
schema built in memory by `tools.schema_validate.derive_generation_schema`:
composition keywords (`allOf`, `if`, `then`) and constraint keywords
(`minItems`, `maxItems`, `uniqueItems`, `minLength`, `pattern`, `minimum`,
`maximum`) are removed, because strict structured output modes do not enforce
them. A keyword without a derivation rule raises `SCHEMA_DERIVE_UNSUPPORTED`
rather than silently degrading.

`--output-schema` is therefore a generation hint only. Every response is
validated against the full canonical schema before it is emitted, and identity
fields plus `review_basis` are written by the adapter rather than taken from
the model.

Version assumptions, verified against `codex-cli 0.154.0`:

* `--output-schema` requires a model from the `gpt-5` family and cannot be
  combined with `codex exec resume`;
* `--output-schema` is known to be ignored when tools or MCP servers are active
  in the trajectory, which is exactly the executor scenario.

Both limitations are the reason canonical validation is mandatory rather than
duplicated effort.

## Scope enforcement

Codex sandbox modes are an outer, coarser boundary. They do not express
`scope.allowed_paths` and are not its enforcement: `workspace-write` makes the
whole working root writable, `--add-dir` only widens the writable set, and
`allowed_paths` may name individual files. For that reason `--add-dir` is not
used to express scope and `--worktree` is not used at all; worktree placement
and cleanup stay with the orchestrator.

Exact conformance to the effective allowlist is enforced by the post-hoc check
introduced in #180: changed files are compared against the normalised allowlist
after every executor call, including repair iterations, and a mismatch fails
closed with `SCOPE_VIOLATION` and exit code 20.

## Machine codes

| Code | Meaning |
|---|---|
| `INVALID_REQUEST` | stdin is not a role request with usable identity fields |
| `VERSION_MISMATCH` | Codex CLI older than 0.154.0 or unparseable version |
| `MODEL_UNAVAILABLE` | binary missing or non-zero exit from Codex |
| `ADAPTER_TIMEOUT` | role call exceeded the timeout |
| `OUTPUT_LIMIT` | stdin or role output exceeded its limit |
| `MALFORMED_OUTPUT` | role output is not a single JSON object |
| `SCHEMA_VIOLATION` | response fails the canonical schema |
| `SCHEMA_UNSUPPORTED` | canonical schema uses an uninterpretable construct |
| `SCHEMA_DERIVE_UNSUPPORTED` | generation schema cannot be derived safely |

The adapter exits with code `20` on every failure and prints only the machine
code to stderr. Raw Codex events, chain-of-thought, prompts and credentials are
never written to stdout or to the repository.

## Validator ownership

Validation is a project-owned subset in `tools/schema_validate.py` and adds no
runtime dependency. CI installs only `pip install -e ".[dev]"`, and
`.github/workflows/` is a protected path, so an additional extra could not be
installed by the pipeline. All checks raise explicit exceptions instead of
using `assert`, so the validator keeps enforcing under `python -O`.
