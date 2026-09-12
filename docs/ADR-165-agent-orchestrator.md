# ADR-165. Budgeted executor-controller orchestrator (#165)

Дата: 2026-09-11. Epic: #162.

## Контекст

Контракты ролей из #164 сами по себе не обеспечивают изоляцию сессий,
provenance управляющей policy, ограничение repair-loop и безопасное
возобновление. Issue, PR text, comments, dependency documentation, diff и
вывод tools считаются недоверенными данными. Изменённые в task head
`AGENTS.md`, hooks, role/skill definitions и agent configuration не могут
управлять review этой же ветки.

## Решение

Локальный инструмент `tools/agent_orchestrate.py` принимает issue, epic,
acceptance criteria и точные Git refs. Он проверяет текущую ветку, закрепляет
`base_sha`, ancestry и task scope, затем выполняет цикл:

```text
executor (fresh session) -> deterministic gate -> controller (fresh session)
                                      ^                    |
                                      +------ repair ------+
```

Controller получает только номер issue, acceptance criteria, diff, компактное
evidence gate, SHA и номер repair-итерации. Самооценка executor ему не
передаётся. Policy загружается командой `git show` только из проверенного
`base_sha`; отсутствие обязательного policy-файла, изменение base ref или
изменение protected instruction/configuration surface в head приводит к
`FAIL_ESCALATE` до controller.

Provider-neutral adapter использует JSON через stdin и новый subprocess для
каждого вызова роли. Timeout, недоступная команда, ненулевой exit, malformed
JSON и превышение output limit обрабатываются fail-closed. Gate failure не
вызывает controller. Разрешено не более двух repair-итераций; после этого
неустранённая находка или gate failure дают `FAIL_ESCALATE`.

Состояние запуска атомарно хранится с правами владельца в
`$XDG_STATE_HOME/privacy-gateway/agent-runs` либо в явно заданном private
каталоге. Terminal state переиспользуется при повторном запуске, поэтому
adapter-вызовы не дублируются. Raw logs и chain-of-thought не сохраняются.
Перед каждой ролью проверяется, что private artifact patterns не tracked и не
staged. Эти patterns также добавлены в `.gitignore`.

Перед controller выполняется gate #163 и отдельный `detect-secrets` hook для
публичного patch. Команды commit, push, создания PR, comment и merge инструмент
не выполняет. Для них существуют отдельные deny-by-default permission fields
в internal contract; текст issue не может их изменить. Merge всегда остаётся
за человеком.

## Последствия и границы

Инструмент не входит в публичный `pgw` или Library API, не привязан к одному
LLM-провайдеру и не требует реального API в тестах. Adapter command должен сам
обеспечить модель и разрешённый tool sandbox. Read-only проверка provenance не
доказывает доверенность содержимого base policy — base SHA закрепляется
человеком до запуска.

**Статус:** действует.

## Обновление #180: fail-closed allowlist путей в CLI

`tools.agent_orchestrate` больше не подставляет `allowed_paths=["."]`.
Причина: широкий default разрешал любой непротектированный путь и переносил
фактическую защиту scope на post-executor diff, то есть уже после физического
изменения workspace.

### Контракт CLI

- `--allowed-path PATH` — повторяемый аргумент; задаёт repository-relative
  файл или каталог.
- Реальный запуск executor без хотя бы одного `--allowed-path` останавливается
  до создания adapter с machine code `ALLOWLIST_REQUIRED`.
- `--dry-run` без allowlist разрешён как диагностика: scope не проверяется и не
  сужается, поле `scope.mode` равно `unscoped_dry_run`, `scope.enforced` равно
  `false`, запись state и memory не выполняется.
- `--dry-run` с allowlist проверяет фактические изменённые файлы против того же
  нормализованного списка и печатает `scope.mode` равным `explicit`.
- `build_contract()` по умолчанию получает пустой allowlist; `run()`
  отказывается стартовать с пустым allowlist до вызова executor.

### Grammar допустимого пути

Принимается непустая строка без ведущих и завершающих пробелов, состоящая из
segments, разделённых `/`. Завершающий `/` нормализуется. Отклоняются: пустое
значение, `.`, `..`, любой segment `.`, `..` или пустой, `\` в любой позиции,
ведущий `/` или `~`, drive-форма `X:`, NUL, значение длиннее 4096 символов.
Разделителем на Linux и Windows является только `/`.

Каталог в allowlist разрешает себя и всё поддерево; файл разрешает только сам
файл. Дубликаты удаляются, порядок сохраняется.

### Machine codes

| Code | Причина |
|---|---|
| `ALLOWLIST_REQUIRED` | Реальный запуск или `run()` без явного allowlist. |
| `ALLOWED_PATH_INVALID` | Нарушена grammar пути, включая `.`, `..` и абсолютные формы. |
| `ALLOWED_PATH_ESCAPES_ROOT` | Реальный путь выходит за repository root, в том числе через symlink. |
| `ALLOWED_PATH_PROTECTED` | Путь попадает в protected или private policy patterns. |
| `SCOPE_VIOLATION` | Фактическое изменение вне allowlist после executor. |

Symlink внутри root допускается; symlink, ведущий наружу, отклоняется по
`os.path.realpath` до запуска executor.

### Security и compatibility

Allowlist не может разрешить `PROTECTED_PATTERNS`, `PRIVATE_PATTERNS` или
`POLICY_FILES`; post-executor проверка по-прежнему возвращает
`HEAD_POLICY_CHANGED` для policy surface. Прежний bypass `"." in allowed`
в `_assert_scope` удалён, поэтому contract со значением `.` больше не
отключает проверку scope.

Изменение несовместимо намеренно: вызовы `build_contract()` и запуски CLI,
которые полагались на неявный `.`, теперь останавливаются с ненулевым exit
code вместо молчаливого получения полного доступа к репозиторию.
