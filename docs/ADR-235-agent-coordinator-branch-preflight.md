# ADR-235: branch preflight agent-координатора

Дата: 2026-09-27. Задача: #235. Epic: #232.

## Статус

Принято для локальной подготовки task-ветки после одобрения точного плана.

## Контекст

Discovery из ADR-234 не выполняет Git-записей. Между preview и созданием ветки
могут измениться issue relationship, default/roadmap refs, ancestry, рабочее
дерево или множество local/remote веток. Переключение текущего checkout также
может неожиданно затронуть рабочий контекст оператора или linked worktree.

## Решение

`tools/agent_coordinator_branch.py` принимает закреплённые plan identity:
repository, epic/task, default/roadmap/task refs и ожидаемые SHA. Флаг
`--approve-plan` подтверждает только эти значения и разрешает единственный
side effect — `git branch <head> <base_sha>`.

Непосредственно перед side effect инструмент повторно получает repository,
issue relationship, branches и compare status через read-only GitHub CLI,
сверяет GitHub SHA с ожидаемыми значениями и `refs/remotes/<remote>/...`, затем
проверяет Git root, clean status, refname, local ancestry и отсутствие
конфликтующей local/remote task-ветки. Roadmap обязан содержать текущий default
branch (`ahead` либо `identical`). Любая ошибка завершает preflight до Git
write.

Инструмент не выполняет fetch: недоступный GitHub не должен оставлять даже
частично обновлённые Git metadata. Remote-tracking refs обновляются оператором
до утверждения плана, а preflight доказывает их совпадение с GitHub. Ветка
создаётся без checkout и без upstream, поэтому текущая ветка и linked
worktrees не переключаются.

Повторный запуск является `NO_OP`, только если local task ref уже равен
закреплённому `base_sha`, remote task ref отсутствует, а все остальные проверки
снова успешны. Иное существующее значение блокирует запуск.

## Последствия

- Ни executor, ни controller не получают Git/GitHub write.
- GitHub CLI используется только для read-only запросов; push, PR, merge,
  commit, checkout, worktree creation и изменение upstream отсутствуют.
- Результат сообщает before/after SHA, неизменившуюся current branch и
  отсутствие upstream; свободный текст issue не становится authority.
- Это внутренний CLI и он не меняет публичные Library/`pgw`/JSON контракты.

## Не входит

Fetch, checkout/worktree creation, commit, push, PR, merge, удаление ветки,
handover, executor/controller run, version bump и release.
