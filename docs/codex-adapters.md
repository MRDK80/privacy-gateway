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
the worktree, and the prompt is streamed on stdin with the positional
argument `-`. It is not passed as one argv entry: Linux caps a single
argument at `MAX_ARG_STRLEN` (131072 bytes) and the controller prompt,
which embeds the trusted policy bundle, exceeds that cap. `--json` is not
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

Strict structured output also demands `additionalProperties: false` on every
object and a `required` array that lists every property. The derivation
therefore closes `required` and expresses a canonically optional property as a
nullable union, for example `["object", "null"]`, instead of omitting it
(#192). `assert_generation_ready` enforces both invariants locally, so a
defective generation schema fails closed before Codex is invoked instead of
coming back as `invalid_json_schema` from the API. Because the canonical schema
still forbids `location: null`, `tools.schema_validate.prune_generation_nulls`
removes exactly those nulls the canonical schema does not require, and only
then does canonical validation run. A null the canon does require, such as
`escalation_reason`, is preserved. Canonical schemas stay unchanged.
See ADR-192.

`--output-schema` is therefore a generation hint only. Every response is
validated against the full canonical schema before it is emitted, and identity
fields plus `review_basis` are written by the adapter rather than taken from
the model.

Version assumptions, verified against `codex-cli 0.154.0`:

* `--output-schema` cannot be combined with `codex exec resume`; the earlier
  assumption that it requires a model from the `gpt-5` family was disproved
  experimentally in #186: a trivial schema is accepted on `gpt-6-astra`,
  while a schema whose properties lack `type` is rejected with
  `invalid_json_schema` on the same model;
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
| `CODEX_NOT_FOUND` | the Codex binary cannot be launched (`OSError`) |
| `VERSION_PROBE_FAILED` | `codex --version` timed out or exited non-zero |
| `MODEL_UNAVAILABLE` | non-zero exit from `codex exec` |
| `ADAPTER_TIMEOUT` | role call exceeded the timeout |
| `OUTPUT_LIMIT` | stdin or role output exceeded its limit |
| `MALFORMED_OUTPUT` | role output is not a single JSON object |
| `SCHEMA_VIOLATION` | response fails the canonical schema |
| `SCHEMA_UNSUPPORTED` | canonical schema uses an uninterpretable construct |
| `SCHEMA_DERIVE_UNSUPPORTED` | generation schema cannot be derived safely |
| `PROMPT_TOO_LARGE` | the role prompt hit an OS limit (`E2BIG`) |

The adapter exits with code `20` on every failure. Its first stderr line is the
machine code; an optional second line is `detail=`, built from a fixed
vocabulary of the Codex exit code plus whitelisted tokens such as
`invalid_json_schema`. `tools/agent_orchestrate.py` re-emits that machine code
and the filtered detail line on its own stderr, while its stdout JSON contract
stays unchanged. Raw Codex events, chain-of-thought, prompts and credentials
are never written to stdout, to stderr or to the repository. See ADR-186.

## Validator ownership

Validation is a project-owned subset in `tools/schema_validate.py` and adds no
runtime dependency. CI installs only `pip install -e ".[dev]"`, and
`.github/workflows/` is a protected path, so an additional extra could not be
installed by the pipeline. All checks raise explicit exceptions instead of
using `assert`, so the validator keeps enforcing under `python -O`.

## Review basis trust source (#194)

`tools/agent_orchestrate.py` validates the controller verdict independently of the
canonical JSON Schema. Until #194 that manual check compared `review_basis` with a
single hardcoded literal, `trust_source_kind: "base_sha"`, while the production
controller adapter truthfully reports `local_read_only_bundle`: the controller runs
read-only over a temporary policy bundle materialized from the pinned `base_sha`,
which is exactly what keeps `head_policy_applied` equal to `false`. The canonical
schema allows both values, so the orchestrator rejected a truthful verdict with
`MALFORMED_OUTPUT`.

The orchestrator now checks trust boundary invariants instead of one source string:

- `head_policy_applied` must be exactly `false`;
- `executor_self_assessment_treated_as_evidence_only` must be exactly `true`;
- `trust_source_kind` must belong to the canonical enum of
  `docs/schemas/controller-verdict.schema.json`;
- `review_basis` must contain exactly these three keys.

The canonical enum is read from the orchestrator's own trusted checkout, never from
the task head worktree, so a task branch cannot widen the accepted set. Unknown
values, missing keys and non-boolean stand-ins such as `0` or `1` remain fail-closed
with `MALFORMED_OUTPUT` and process exit code 20. If the canonical schema cannot be
read or does not expose a non-empty enum of strings, the orchestrator fails closed
with `TRUST_SCHEMA_UNAVAILABLE`.

The executor path is unchanged: `_validate_report` never inspected `review_basis`,
and a characterization test now pins that behaviour. Canonical schemas in
`docs/schemas/` are not modified. See `docs/ADR-194-review-basis-trust-source.md`.
