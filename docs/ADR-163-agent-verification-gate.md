# ADR-163. Детерминированный agent verification gate (#163)

Дата: 2026-09-11. Epic: #162.

## Контекст

Проверки Git-состояния и обязательный local quality gate выполняются набором
разрозненных команд. Их повторная интерпретация агентом расходует контекст и
создаёт неодинаковые отчёты. Проверяемые branch names, commit graph, рабочее
дерево и вывод subprocess считаются недоверенным вводом и не должны попадать в
shell-команды или отчёт без структурной обработки.

## Решение

Проект предоставляет команду:

```bash
python tools/agent_verify.py PHASE --base BASE --head HEAD [--format text|json]
```

`PHASE` имеет три значения:

- `pre-commit` проверяет имена head/base, разрешение refs, ancestry и
  `git diff --check`; незакоммиченные изменения допустимы и перечисляются;
- `pre-push` дополнительно требует clean worktree и запускает ровно четыре
  обязательные команды из `CONTRIBUTING.md`;
- `post-push` повторяет проверки `pre-push` и требует совпадения `HEAD` с
  локальным remote-tracking ref `<remote>/<head>`.

Команда не выполняет `commit`, `push`, `fetch`, `rebase`, `merge` и не меняет
refs. Поэтому post-push подтверждает только состояние уже имеющегося
remote-tracking ref; его свежесть обеспечивается внешним явным `push`/`fetch`.
Quality tools являются существующими проектными командами и могут сообщить об
изменении файлов; gate после каждого запуска повторно проверяет worktree и
считает такое изменение отказом.

JSON schema `1.0` содержит `schema_version`, `status`, `phase`, `machine_code`,
`exit_code`, `base`, `head`, `head_sha`, `remote_sha`, `changed_files`, `checks`
и `log_directory`. Environment, credentials и содержимое файлов не включаются.
Text и JSON строятся из одной модели результата. Полный stdout/stderr quality
commands не печатается; при явном `--log-dir` вне корня репозитория он
записывается в отдельные локальные файлы. Путь внутри working tree отклоняется,
чтобы raw logs не попали в публичную Git-историю. По умолчанию gate не создаёт
логов.

Стабильные категории результата:

| Machine code | Exit code | Категория |
|---|---:|---|
| `OK` | 0 | все проверки фазы успешны |
| `USAGE_ERROR` | 2 | неверные аргументы |
| `GIT_ERROR` | 10 | Git недоступен или ref не разрешается |
| `WRONG_BRANCH` | 11 | текущая/head/base ветка не соответствует контракту |
| `DIRTY_WORKTREE` | 12 | worktree не clean в требующей этого фазе |
| `ANCESTRY_MISSING` | 13 | base не является предком head |
| `DIFF_ERROR` | 14 | `git diff --check` завершился неуспешно |
| `GATE_FAILED` | 15 | обязательная quality command завершилась неуспешно |
| `REMOTE_MISMATCH` | 16 | remote-tracking SHA отсутствует или не равен HEAD |
| `INTERNAL_ERROR` | 17 | непредвиденный контролируемый отказ gate |

Gate останавливается на первом structural/Git отказе. Все четыре quality
commands запускаются даже после отдельного failure, чтобы отчёт был полным;
итоговая категория при любом таком отказе — `GATE_FAILED`.

## Последствия и границы

Инструмент находится в `tools/`, не экспортируется пакетом и не меняет CLI или
runtime-семантику Privacy Gateway. Он не создаёт PR, не ждёт GitHub Actions, не
запускает LLM, не исправляет найденные проблемы и не доказывает свежесть
remote-tracking refs. Интеграция с GitHub checks относится к отдельной задаче
epic #162.

**Статус:** действует.
