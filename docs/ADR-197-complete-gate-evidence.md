# ADR-197: полный versioned gate_evidence

**Статус:** предложено. Задача #197, epic #179. Зависит от ADR-192, ADR-194
и ADR-196.

## Контекст

Production pilot #182 показал, что `gate_evidence` не доказывает выполнение
обязательного quality gate проекта.

Фактическое состояние кода на вершине `roadmap/179-codex-adapters`:

- `run()` в `tools/agent_orchestrate.py` вызывает `gate_runner(contract)`, а
  `_default_gate()` запускает `tools/agent_verify.py` в фазе `pre-commit`;
- в `agent_verify` блок `QUALITY_COMMANDS` выполняется только в фазах
  `pre-push` и `post-push`, поэтому в `pre-commit` реально проверяются лишь
  `git diff --check` и сбор `changed_files`, к которым `_default_gate()`
  добавляет `pre-commit run detect-secrets --files <changed>`;
- `agent_verify` принимает только имена веток: `--base` обязан начинаться с
  `roadmap/`, оба значения проходят `git check-ref-format --branch`;
- `create_trusted_snapshot()` создаёт snapshot в disposable bare clone и
  удаляет временный каталог в `finally`, поэтому `snapshot_commit` после
  возврата недоступен для checkout;
- `gate_result` передаётся контроллеру целиком ключом `gate_evidence` и
  попадает в блок `review-data` prompt'а (`tools/codex_adapter.py`), а
  `Result` из `agent_verify` содержит `log_directory` с абсолютным путём;
- канонические схемы `docs/schemas/executor-report.schema.json` и
  `docs/schemas/controller-verdict.schema.json` закрыты
  `additionalProperties: false`, а `_validate_report()` и
  `_validate_verdict()` требуют точного равенства множеств ключей.

Следствие: контроллер не может отличить полный gate от усечённого, а executor
остаётся источником утверждений о проверках.

## Решение

### 1. Границы контракта

`gate_evidence` — часть request payload контроллера, а не канонических схем
ролей. Поэтому `docs/schemas/*.schema.json`, `_validate_report()`,
`_validate_verdict()`, публичный stdout `RunResult` и process exit codes 0 и
20 не изменяются. Инварианты derived generation schema (#192) и проверка
`review_basis` (#194) сохраняются без изменений.

`head_sha` не перегружается идентичностью snapshot: он по-прежнему сверяется
с фактическим HEAD и участвует в `UNAUTHORIZED_HEAD_CHANGE`. Идентичность
snapshot живёт только внутри `gate_evidence`.

Публичный CLI `tools/agent_verify.py` не меняется: `PHASES`, аргументы,
`EXIT_CODES` и JSON-контракт остаются прежними. Новый полный профиль не
переиспользует имена `pre-commit`, `pre-push` и `post-push`.

### 2. Жизненный цикл snapshot

Вводится context-managed сессия `trusted_snapshot_session(contract, root=...)`
в `tools/agent_orchestrate.py`:

```text
post-execution scope validation
  -> snapshot session (disposable bare mirror + snapshot checkout)
       -> repository-full gate внутри snapshot checkout
       -> сборка gate_evidence
  -> assert_tree_unchanged для исходного дерева
  -> controller
```

Правила сессии:

1. Scope validation выполняется до создания сессии.
2. Mirror создаётся как `git clone --bare --no-hardlinks` во временном
   каталоге вне репозитория; индекс задаётся через `GIT_INDEX_FILE`.
3. `write-tree` даёт `tree_hash`, `commit-tree -p <base_sha>` даёт
   `snapshot_commit` с синтетической идентичностью и фиксированными датами
   из ADR-196.
4. Внутри disposable mirror создаётся ref на `snapshot_commit`, чтобы из него
   можно было получить рабочее дерево. Refs пользовательского репозитория и
   любого remote не перемещаются.
5. Snapshot checkout материализуется отдельным clone из mirror, поэтому все
   разрешённые ранее untracked-файлы в checkout известны Git.
6. Checkout и mirror живут до завершения gate и сборки evidence, затем
   удаляются.
7. После gate проверяется неизменность исходного дерева; расхождение даёт
   `TREE_MUTATED_AFTER_SNAPSHOT` до вызова контроллера.

Повторная реконструкция snapshot вместо сохранения сессии отклонена: она не
доказывает равенство `tree_hash` и открывает TOCTOU между проверяемым
деревом, gate и контроллером.

### 3. Идентичность окружения gate

Команды профиля исполняются в snapshot checkout. Editable-установка пакета
может подменить `privacy_gateway` исходниками исходного репозитория, поэтому
перед профилем выполняется probe: интерпретатор сообщает путь импортируемого
пакета. Если путь не лежит внутри snapshot checkout, gate завершается
fail-closed с `GATE_ENVIRONMENT_UNVERIFIED`; ложно-зелёный прогон по чужому
дереву запрещён. Gate не создаёт virtual environment, не устанавливает
пакеты и не обращается к сети.

### 4. Профили

```json
{
  "profile": "repository-full",
  "profile_version": "1",
  "expected_checks": ["pytest", "ruff", "mypy", "pre-commit"]
}
```

Точные argv профиля `repository-full`:

```text
pytest -q
ruff check .
mypy .
pre-commit run --all-files
```

Дополнительно допускается явно именованный сокращённый профиль
`repository-diff-only` версии `1` с существующей фазой `pre-commit`
`agent_verify`. Он разрешён только для промежуточных repair-итераций, всегда
объявляется явно и никогда не считается полным. Перед итоговым
ControllerVerdict обязателен `repository-full`.

### 5. Контракт evidence

Уровень профиля: `schema_version`, `profile`, `profile_version`, `status`,
`complete`, `machine_code`, `expected_checks`, `executed_checks` и объект
`snapshot` с полями `base_sha`, `snapshot_method`, `snapshot_commit`,
`tree_hash`, `diff_sha256`, `provenance_complete`.

Уровень проверки: стабильный `id`, фактический `argv`, безопасная метка
рабочего каталога `<snapshot-checkout>`, `status`, `exit_code` или `null`,
`duration_seconds`, redacted `summary` и `metrics`.

Замкнутое множество статусов проверки:

```text
passed
failed
timeout
unavailable
interrupted
not_run
parse_error
```

Статус профиля: `passed`, `failed` или `incomplete`.

Правила summaries:

- pytest: `passed`, `failed`, `skipped`, `xfailed`, `xpassed`; отсутствующее
  значение остаётся `null`, а не `0`; ненулевой exit code остаётся failure
  независимо от результата разбора;
- mypy: число проверенных source files, если инструмент его вывел, иначе
  `null`;
- ruff: фактический статус и число ошибок, если доступно; `All checks passed`
  допустим только при exit code 0;
- pre-commit: статус каждого hook либо безопасная агрегированная сводка.

### 6. Fail-closed правила

- Отсутствие любой обязательной проверки даёт `complete: false` и
  `GATE_EVIDENCE_INCOMPLETE`.
- `timeout`, `unavailable`, `interrupted`, `not_run`, `parse_error` и
  ненулевой exit code не являются успехом.
- Несовпадение профиля или набора check ids даёт `GATE_PROFILE_MISMATCH`.
- Ошибка сессии snapshot даёт `SNAPSHOT_SESSION_FAILED`.
- Контроллер не вызывается при неуспешном или неполном gate.
- Неполнота evidence не маскируется `MODEL_UNAVAILABLE`, `COMMAND_FAILED`
  или общим `GATE_FAILED`.

Новые machine codes: `GATE_EVIDENCE_INCOMPLETE`, `GATE_PROFILE_MISMATCH`,
`GATE_ENVIRONMENT_UNVERIFIED`, `SNAPSHOT_SESSION_FAILED`. Существующие коды,
включая `GATE_FAILED` для честно провалившейся обязательной проверки, не
переопределяются.

### 7. Redaction

Raw stdout и stderr не сохраняются и не передаются контроллеру. `summary`
ограничен фиксированным алфавитом и длиной, не содержит абсолютных путей,
домашнего каталога, путей приватного хранилища, логов, prompts, credentials и
PII. `log_directory` и любые пути временных каталогов в `gate_evidence` не
попадают.

## Тесты

Детерминированные synthetic-тесты покрывают: полный профиль; отсутствие
обязательной проверки; `unavailable`; `timeout`; прерванный процесс;
ненулевой exit code; неоднозначную сводку; сокращённый профиль, не
принимаемый за полный; pytest без `xfailed`/`xpassed`; mypy без числа файлов;
pre-commit для файла, который до snapshot был untracked; запуск gate в
snapshot checkout, а не в изменяемом worktree; мутацию дерева после gate;
равенство snapshot-хешей в evidence и controller payload; redaction;
truncation, не превращающий failure в success; сохранение инвариантов #192; и
сценарий `PILOT SUCCESS — EXPECTED POLICY ESCALATION` с `workflow_passed:
true`, `change_approved: false` и terminal `FAIL_ESCALATE /
REVIEW_ESCALATED`.

Skip и xfail не подтверждают обязательное свойство.

## Границы и последствия

- Executor и controller не получают `commit`, `push`, `create_pr`, `comment`
  и `merge`. OS-level writable confinement остаётся задачей #185.
- Каждая сессия добавляет disposable mirror и checkout, поэтому стоимость run
  растёт.
- `pre-commit run --all-files` в snapshot checkout использует пользовательский
  кэш hook-окружений; недоступность кэша даёт `unavailable`, а не успех.
- Политика перевода строк в репозитории не определена и `.gitattributes`
  отсутствует, поэтому кросс-платформенное равенство `diff_sha256` не
  гарантируется. Это ограничение наследуется из ADR-196 и не решается здесь.
- Alternative с новым CLI-флагом `--snapshot-commit` для `agent_verify`
  отклонена: она расширяет публичный контракт инструмента без необходимости.
