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
| Sandbox | Bubblewrap read-only root with file allowlist; inner Codex sandbox bypassed only inside that boundary | `read-only` |
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

The executor now requires Bubblewrap on Linux. The adapter validates the
effective allowlist independently of the model prompt, starts with a read-only
bind of the host root, then bind-mounts each accepted file read-write. The
role's private temporary directory is also writable so Codex can write its
schema and output. For #216, the executor also uses a fresh `CODEX_HOME`
inside that same owner-private scratch directory: Codex can create its state
database there, while an existing `auth.json` from the caller's Codex home is
mounted read-only, not copied. The original home, checkout outside the file
allowlist, protected paths and shared Git directory remain read-only. The
scratch is deleted best-effort after the call; it is not durable storage.
No executor role call starts if
Bubblewrap is missing or the allowlist is empty, unsafe or unsupported. A
Bubblewrap namespace failure returns a failing adapter result; it never falls
back to the previous direct `workspace-write` invocation.

At present the enforced allowlist accepts non-symlinked regular files with one
hard link, including a missing file whose existing parent is validated and
whose empty placeholder is created exclusively by the trusted adapter before
the role call. Directory entries and missing parent directories fail closed. This
keeps as-yet-nonexistent nested policy files such as `AGENTS.md` outside every
writable mount. `.git`, protected and private paths are read-only, including
the shared Git directory of a linked worktree. Git operations needing
`.git/config` writes or lock files may fail. `--add-dir` and Codex `--worktree`
are not used. Inside the mandatory Bubblewrap boundary, the executor invokes
Codex with `--dangerously-bypass-approvals-and-sandbox` instead of starting a
second Linux sandbox. The flag appears only after the outer `bwrap --` command
boundary; without Bubblewrap the executor fails closed. This avoids the inner
sandbox mount-registry writes that conflict with the read-only root while
leaving the outer OS allowlist boundary unchanged. The controller continues to
use its separate `--sandbox read-only` session. See ADR-225.

Codex needs access to its remote model API, so the production role cannot run
with Bubblewrap network unsharing. The mount boundary does not restrict reads,
set CPU or memory limits, or apply seccomp filtering. The private temporary
directory is writable during the call but removed afterwards. See ADR-185.
Hosted CI runners may block unprivileged mount namespaces. On such a runner,
active-namespace integration tests are skipped after a probe, while fail-closed
tests still run. The local active-namespace result is reported separately.

The post-execution `_assert_scope` check from #180 remains as defence in depth:
changed files are compared against the normalised allowlist after every
executor call, including repair iterations. A mismatch fails closed with
`SCOPE_VIOLATION` and exit code 20.

## Repair feedback (#219)

Each executor call remains a fresh ephemeral session. The first call receives
no feedback. A later call receives only the preceding failure: deterministic
gate code and bounded check identities, or at most eight redacted controller
findings. Raw gate output, controller evidence snippets, diff and executor
self-assessment are not copied into the feedback. The feedback is untrusted
task data, cannot alter the pinned contract or sandbox, and is validated before
Codex starts. A process resumed from a nonterminal repair record cannot
recover this transient private context from public state and fails closed with
`INTERNAL_ERROR`. See ADR-219.

## Executor task envelope (#221)

The executor receives the operator-pinned contract and transient runtime
evidence in separate labelled blocks. It is explicitly required to implement
the pinned `acceptance_criteria`, while every requirement string remains
untrusted content and cannot redefine capabilities. Runtime identity and repair
feedback are evidence for the same task, not a second task source. Effective
allowlist, permissions, role sandbox and iteration budget continue to come from
validated adapter/orchestrator state and OS enforcement. See ADR-221.

## Machine codes

| Code | Meaning |
|---|---|
| `INVALID_REQUEST` | stdin is not a role request with usable identity fields |
| `VERSION_MISMATCH` | Codex CLI older than 0.154.0 or unparseable version |
| `CODEX_NOT_FOUND` | the Codex binary cannot be launched (`OSError`) |
| `SANDBOX_UNAVAILABLE` | Bubblewrap executable is absent; executor never starts |
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

## Controller review payload (#207)

Controller review receives the orchestrator-pinned contract, including epic,
base/head refs, effective allowed paths, permissions, task class, time limit,
maximum repairs and remaining repair budget. The separate
`reviewed_state.snapshot_commit` equals
`gate_evidence.snapshot.snapshot_commit`; `head_sha` still identifies the
actual Git HEAD and can equal `base_sha` for an uncommitted change. The
production adapter rejects a missing or inconsistent contract or snapshot
before invoking Codex. The executor report is absent from review input.
Trusted policy still comes from the pinned base SHA, and head policy is not
applied. See `docs/ADR-207-controller-review-payload.md`.

## Patch review versus delivery gates (#211)

An operator may pass the full, verbatim acceptance criteria with repeated
`--criterion` and explicitly mark 1-based positions checked only after the
pilot with repeated `--delivery-criterion-index`. For example, if positions
8 and 9 require a task PR and exact PR CI, mark exactly 8 and 9. The list may
not be empty after partitioning, and invalid or duplicate positions fail
closed with `INVALID_CONTRACT`. No delivery classification is inferred from
issue text or model output.

The executor still receives the full contract. The controller receives that
same list plus the exact `review_criteria` and
`pending_delivery_criteria` partition. The production adapter validates
the partition before Codex runs. A positive ControllerVerdict is a patch
review result only; the adapter adds a deterministic pending-gates note.
The orchestrator keeps the public result non-positive:
`FAIL_ESCALATE / EXTERNAL_GATE_PENDING`, exit code 20. The positive
controller verdict remains in private retrospective evidence; neither result
proves `TASK DONE`. PR and CI are verified separately with primary GitHub
results. See `docs/ADR-211-patch-review-delivery-gates.md`.

## Персистентность evidence прогона

Решение зафиксировано в ADR-200. Запись прогона содержит доказательную
часть, но никогда не содержит сырого вывода модели.

### Два слоя

Публичная запись прогона в каталоге состояния содержит только величины,
вычисленные инструментами:

- идентичность snapshot: `base_sha`, `snapshot_commit`, `tree_hash`,
  `diff_sha256`, `snapshot_method`, `provenance_complete`, `tree_unchanged`;
- сводку gate: `profile`, `profile_version`, `complete`, `status`,
  `machine_code`, `expected_checks`, `executed_checks`;
- по каждой проверке — `name`, `status` и `exit_code`;
- фактическую суммарную длительность прогона.

Приватная retrospective содержит полный redacted пакет: те же поля
snapshot, длительности отдельных проверок, разобранные метрики, а также
`verdict`, `review_basis`, `escalation_reason` и структурированные
blocking findings.

Публичный stdout `RunResult` не расширяется: его поля остаются `status`,
`machine_code`, `repair_iterations` и `run_id`.

### Blocking findings

Полный текст finding не сохраняется ни в одном слое. Сохраняется
структурированная выжимка: `severity`, `category`, `location` с путём
относительно корня репозитория, раздельные ограниченные `requirement`,
`evidence` и `required_fix`, унаследованный `summary`, `check_id`,
`finding_fingerprint` как SHA-256 нормализованного исходного объекта и
блок `redaction`.

`severity` сохраняется из канонического enum `critical|high|medium|low`;
унаследованный набор `blocking|major|minor|info` также принимается, а
`unknown` остаётся только для отсутствующего или неизвестного значения.
`location.path` восстанавливается из `location.file`, а `line_start` — из
`location.line`; абсолютные и платформенные пути отклоняются.

Каждое текстовое поле редактируется независимо: отказ одного не удаляет
остальные. К `evidence` применяется вдвое более жёсткий лимит длины, чем
к `requirement` и `required_fix`. Отдельный алфавит finding сохраняет `/`
в относительных путях репозитория. Абсолютный, домашний, временный,
Windows/UNC-путь и выход через `..` отклоняют всё поле до фильтрации;
секретоподобный фрагмент также отклоняет поле. Длинный текст усечён по
границе слова и отмечен в `redaction.truncated_fields`.

`redaction.version` равен `3`; формы версий `1` и `2` читаются без ошибок.
`redaction.dropped_fields` перечисляет удалённые поля,
`redaction.drop_reasons` называет причину каждого из них значением из
закрытого перечня `DROP_REASONS`. `redaction.summary_dropped` равно
`true` только когда удалены все текстовые поля; факт наличия finding
сохраняется всегда. Если объект finding вообще невозможно разобрать,
категория становится `FINDING_REDACTION_FAILED`, все текстовые поля
равны `null`, а причиной каждого становится `unparseable`.
Отпечаток считается от исходного объекта до редакции: одинаковый исходный
finding сохраняет отпечаток при смене версии редакции, но сами текстовые
выжимки между версиями не эквивалентны.

Findings всех итераций repair-цикла сохраняются в `iteration_history`
внутри verdict evidence: номер итерации, её вердикт и список находок.
Номера итераций неотрицательны и не убывают, одинаковые
`finding_fingerprint` в разных итерациях не дедуплицируются, а сумма
находок по итерациям равна счётчику `findings` прогона. Поле
`blocking_findings` верхнего уровня остаётся проекцией последней
итерации.

### Источник идентичности snapshot

Публикуемые значения берутся из `snapshot` в evidence профиля
`repository-full`, то есть из той же структуры, которую проверяет
`_assert_gate_snapshot_unchanged`. Второй источник этих значений не
создаётся, повторного вычисления дерева для записи не выполняется.

### Версии схем

Приватная запись использует `schema_version` `1.2`; записи `1.0` и `1.1`
читаются без ошибок, причём `1.0` не содержит блока `evidence`. Версия
разбирается по числовым компонентам, а не строковым сравнением. Чужой
major и неизвестный minor отклоняются fail-closed: валидаторы приватной
записи сравнивают наборы ключей строгим равенством, поэтому чтение с
игнорированием незнакомых полей не поддерживается.
`PROMOTION_SCHEMA_VERSION` остаётся `1.0`. Публичная запись имеет
собственный `schema_version` внутри блока evidence и не переиспользует
версию схемы evidence профиля gate.
