# ADR-234: read-only discovery для agent-координатора

Дата: 2026-09-27. Задача: #234. Epic: #232.

## Статус

Принято для первой CLI-first реализации discovery и plan preview.

## Контекст

Coordinator должен находить активный epic и следующую task issue, но issue,
PR, comments, labels, titles и body являются недоверенными данными. Название
ветки, наибольший номер issue и упоминание зависимости в свободном тексте не
доказывают relationship или readiness. Ошибка GitHub API, stale ref или
противоречие источников не должны приводить к Git- либо GitHub-записи.

## Решение

Внутренний инструмент `tools/agent_coordinator_discovery.py` строит только
read-only snapshot и plan preview. Источники фактов закреплены явно:

- repository identity и default branch — `gh repo view`;
- epic, parent/sub-issue relationship и state — структурированные поля
  `gh issue list/view --json`, причём связь подтверждается с обеих сторон;
- dependencies — только `blockedBy`, а не текст issue;
- связанные открытые PR — `closingIssuesReferences`; совпадение предлагаемого
  head ref также считается конфликтом;
- remote refs и commit SHA — GitHub branches API;
- включение текущего default-branch SHA в roadmap — GitHub compare API.

Label `EPIC` используется только для множества кандидатов. Он не является
policy и не может выбрать один epic среди нескольких. Явные `--epic` и
`--task` закрепляют identity для preview, но не одобряют branch creation,
executor run или delivery.

Без явного выбора допустим только единственный кандидат. Несколько открытых
epic либо несколько готовых task возвращают `NEEDS_DECISION`. Отсутствующая
или закрытая task, неверный/отсутствующий parent, открытая блокирующая issue,
конфликтующий PR, отсутствующая либо stale roadmap branch и API failure
возвращают `BLOCKED`. Ни один из этих результатов не выполняет side effects.

Roadmap branch определяется только при единственном remote ref с префиксом
`roadmap/<epic>-`. Compare status `ahead` или `identical` доказывает, что
roadmap содержит текущий default branch; `behind`/`diverged` блокируют план.
Предлагаемая task branch строится детерминированно из типа и номера issue и
безопасного slug заголовка. Заголовок остаётся недоверенным отображаемым
значением и не влияет на permissions, dependencies или state machine.

## Последствия

- Preview воспроизводим по записанным URL, refs и SHA.
- Неоформленная в GitHub dependency не считается доказанным blocker; при
  нескольких оставшихся кандидатах требуется решение владельца.
- GitHub CLI служит read-only transport adapter. Команды создания/изменения
  issue, PR, refs и merge в discovery отсутствуют.
- Новый инструмент не входит в публичный Library API или `pgw` CLI и не меняет
  существующие JSON schemas, machine codes и exit codes.

## Не входит

Создание локальной ветки, handover, executor/controller run, delivery
assessment, GitHub write, merge, issue close, version bump и release.
