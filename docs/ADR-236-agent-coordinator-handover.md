# ADR-236: handover agent-координатора в executor/controller workflow

Дата: 2026-09-27. Задача: #236. Epic: #232.

## Статус

Принято для versioned handover и запуска существующего workflow после
повторной локальной проверки identity.

## Контекст

Branch preflight из ADR-235 создаёт только локальный ref. Между одобрением
плана и запуском могут измениться handover, checkout, SHA или рабочее дерево.
Свободный текст задачи и task head остаются недоверенными данными, а новый
coordinator не должен дублировать либо расширять executor, gate и controller.

## Решение

`tools/agent_coordinator_handover.py` принимает один JSON handover версии
`1.0` и отдельный `--approve-plan-digest`. Digest вычисляется над каноническим
JSON всех полей handover, кроме блока `approval`, и должен одновременно
совпасть с CLI approval и закреплённым значением в handover. Любое изменение
criteria, scope, allowlist, identity, budget или gate требует нового approval.

Handover содержит repository и issue identity, provenance criteria,
`base_ref`/`base_sha`, `head_ref`/`head_sha`, causal scope, точный allowlist,
запрещённые действия, deny-by-default permissions, budgets, обязательный
`repository-full` gate версии `1`, pinned policy source и классификацию
delivery criteria. Структура закрыта: неизвестные либо отсутствующие поля,
пустые criteria/scope/allowlist, разрешённый Git/GitHub write и расширение
лимитов отклоняются до adapter-вызова.

Непосредственно перед передачей contract инструмент проверяет Git root,
repository remote, текущую task-ветку, clean worktree, точные base/head SHA,
roadmap ancestry и policy source. Затем он вызывает существующие
`build_contract()` и `run()`; production gate, свежие executor/controller
sessions, pinned base policy, snapshot evidence, repair budget и private state
остаются реализацией ADR-165/197. Coordinator не интерпретирует результат как
delivery approval.

## Последствия

- Malformed handover, неверный approval, stale SHA, dirty tree, wrong branch,
  scope expansion и incomplete identity завершаются машинным `BLOCKED` до
  executor.
- Fake workflow runner позволяет тестировать validation и передачу contract
  без live LLM; production CLI требует обе существующие adapter commands.
- Новый инструмент внутренний и не меняет публичные Library/`pgw` contracts,
  schemas, machine codes или exit codes существующего orchestrator.

## Не входит

Discovery, создание ветки, переписывание executor/controller/gate, GitHub
write, commit, push, PR, merge, version bump и release.
